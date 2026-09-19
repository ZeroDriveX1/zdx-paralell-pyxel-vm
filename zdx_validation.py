"""Bounded soak, resource validation, and repeatable microbenchmarks."""

from __future__ import annotations

import argparse
import io
import json
import math
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from security.identity import NodeIdentity
from zdx_metrics import METRICS, resource_snapshot
from zdx_network import MessageAuthenticator, SessionRegistry, TrustedKeyStore
from zdx_node_registry import ZDXNodeRegistry
from zdx_parallel_vm import ParallelPyxelVM, SimpleCompiler
from zdx_pixel_memory import codec
from zdx_pixel_memory.store import PixelStore
from zdx_scheduler import ZDXScheduler
from zdx_session import NodeCredentials, SessionClient, SessionCoordinator
from zdx_state import ZDXState
from zdx_storage import Migration, MigrationRegistry, StateStore


def _percentile(values, percentage):
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * percentage) - 1)
    return ordered[max(0, index)]


def _bench(name, function, iterations):
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        function()
        samples.append(time.perf_counter() - started)
    total = sum(samples)
    return {
        "name": name,
        "iterations": iterations,
        "total_seconds": total,
        "operations_per_second": iterations / total if total else None,
        "mean_seconds": statistics.fmean(samples),
        "p50_seconds": statistics.median(samples),
        "p95_seconds": _percentile(samples, 0.95),
        "max_seconds": max(samples),
    }


def run_benchmarks(iterations=25):
    with tempfile.TemporaryDirectory(prefix="zdx-bench-") as directory:
        root = Path(directory)
        program = root / "program.png"
        SimpleCompiler().compile(
            [["SET_A 7", "SET_B 5", "ADD", "COPY_OUT", "HALT"]],
            str(program),
        )
        vm = ParallelPyxelVM(threads=1)
        state = StateStore(root / "state.json", "benchmark")
        scheduler = ZDXScheduler()
        registry = ZDXNodeRegistry()
        for number in range(100):
            capabilities = {"cpu_count": number % 16, "gpu": number % 9 == 0}
            scheduler.register_node(f"node-{number:03}", capabilities)
            registry.register(f"node-{number:03}", capabilities)
        private = Ed25519PrivateKey.generate()
        public = private.public_key()
        payload = b"zdx-benchmark-payload"
        signature = private.sign(payload)
        pixel_value = {"values": list(range(128))}
        migration_registry = MigrationRegistry()
        migration_registry.register(Migration(
            "bench-migration", 1, 2, lambda data: {**data, "version": 2}
        ))
        migration_counter = [0]

        def migrate():
            migration_counter[0] += 1
            path = root / f"migration-{migration_counter[0]}.json"
            StateStore(path, "bench-migration", version=1).save({"version": 1})
            StateStore(
                path, "bench-migration", version=2,
                migrations=migration_registry,
            ).load()

        signing = [b""]

        def serialize_png():
            buffer = io.BytesIO()
            codec.encode(pixel_value).save(buffer, format="PNG")
            return buffer.tell()

        benchmarks = [
            _bench("vm_execution", lambda: vm.execute_texture(str(program)), iterations),
            _bench("persistence_commit", lambda: state.save({"value": time.time_ns()}), iterations),
            _bench("scheduler_selection", scheduler.select_node, iterations * 10),
            _bench("registry_lookup", lambda: registry.get("node-050"), iterations * 20),
            _bench("ed25519_sign", lambda: signing.__setitem__(0, private.sign(payload)), iterations * 10),
            _bench("ed25519_verify", lambda: public.verify(signature, payload), iterations * 10),
            _bench("png_serialization", serialize_png, iterations),
            _bench("state_migration", migrate, max(3, iterations // 5)),
        ]

        # Authenticated validation is the coordinator dispatch security boundary.
        coordinator = NodeCredentials(NodeIdentity(Ed25519PrivateKey.generate()))
        node = NodeCredentials(NodeIdentity(Ed25519PrivateKey.generate()))
        server_trust = TrustedKeyStore()
        server_trust.enroll(node.node_id, node.public_key_bytes)
        client_trust = TrustedKeyStore()
        client_trust.enroll(coordinator.node_id, coordinator.public_key_bytes)
        sessions = SessionRegistry()
        server = SessionCoordinator(coordinator, server_trust, sessions)
        client = SessionClient(node, client_trust)
        challenge = server.accept_hello(client.hello())
        ack = server.accept_confirmation(
            client.confirm(challenge, coordinator.node_id)
        )
        client.accept_ack(ack)
        sequence = [0]

        def dispatch():
            sequence[0] += 1
            packet = node.message(
                "heartbeat", {}, session_id=client.session_id,
                sequence=sequence[0],
            )
            MessageAuthenticator(server_trust, sessions).validate(packet)

        benchmarks.append(_bench(
            "coordinator_authenticated_dispatch", dispatch, iterations
        ))
        return {
            "schema_version": 1,
            "generated_at_epoch": time.time(),
            "benchmarks": benchmarks,
            "metrics": METRICS.snapshot(),
        }


def run_soak(iterations=100, duration_seconds=None):
    """Run bounded deterministic soak; pass 86400 seconds for a 24-hour run."""
    with tempfile.TemporaryDirectory(prefix="zdx-soak-") as directory:
        root = Path(directory)
        program = root / "program.png"
        SimpleCompiler().compile(
            [["SET_A 2", "SET_B 3", "ADD", "COPY_OUT", "STORE_MEM 0", "HALT"]],
            str(program),
        )
        vm = ParallelPyxelVM(threads=1)
        state = StateStore(root / "commits.json", "soak")
        pixels = PixelStore(str(root / "pixels"))
        registry = ZDXNodeRegistry(path=root / "registry.json")
        sessions = SessionRegistry(path=root / "sessions.json")
        scheduler = ZDXScheduler()
        coordinator = ZDXState(root / "coordinator.json")
        migrations = MigrationRegistry()
        migrations.register(Migration(
            "soak-migration", 1, 2, lambda data: {**data, "migrated": True}
        ))
        for number in range(16):
            scheduler.register_node(
                f"sched-{number}", {"cpu_count": number + 1}
            )

        tracemalloc.start()
        memory_before = tracemalloc.get_traced_memory()[0]
        resources_before = resource_snapshot()
        started = time.monotonic()
        completed = 0
        warm_lock_count = None
        warm_backup_count = None
        selections = {}
        while completed < iterations or (
            duration_seconds is not None
            and time.monotonic() - started < duration_seconds
        ):
            vm.execute_texture(str(program))
            state.save({"iteration": completed})
            pixels.write(f"slot-{completed % 8}", {"iteration": completed})
            node_id = f"node-{completed % 32}"
            session = sessions.establish(node_id)
            registry.register(node_id, {"cpu_count": completed % 8}, session.session_id)
            registry.heartbeat(node_id, session.session_id)
            registry.disconnect(session.session_id)
            sessions.close(session.session_id)
            if completed % 4 == 0:
                registry.remove_stale(now=time.time() + 1000)
            selected = scheduler.select_node()
            selections[selected[0]] = selections.get(selected[0], 0) + 1
            coordinator.record_heartbeat()
            migration_path = root / f"migration-{completed % 3}.json"
            StateStore(
                migration_path, "soak-migration", version=1
            ).save({"iteration": completed})
            StateStore(
                migration_path, "soak-migration", version=2,
                migrations=migrations,
            ).load()
            completed += 1
            if completed == max(1, min(16, iterations)):
                warm_lock_count = len(list(root.rglob("*.lock")))
                warm_backup_count = len(list(root.rglob("*.bak")))
            if duration_seconds is None and completed >= iterations:
                break

        memory_after, memory_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        resources_after = resource_snapshot()
        stale_transactions = list(root.rglob("*.tmp"))
        elapsed = time.monotonic() - started
        result = {
            "schema_version": 1,
            "mode": "bounded" if duration_seconds is None else "duration",
            "requested_iterations": iterations,
            "requested_duration_seconds": duration_seconds,
            "completed_iterations": completed,
            "elapsed_seconds": elapsed,
            "iterations_per_second": completed / elapsed,
            "resource_before": resources_before,
            "resource_after": resources_after,
            "memory_growth_bytes": memory_after - memory_before,
            "memory_peak_bytes": memory_peak,
            "thread_growth": (
                resources_after["active_threads"] - resources_before["active_threads"]
            ),
            "descriptor_growth": (
                None if resources_before["open_file_descriptors"] is None
                else resources_after["open_file_descriptors"]
                - resources_before["open_file_descriptors"]
            ),
            "stale_transactions": len(stale_transactions),
            "lock_files": len(list(root.rglob("*.lock"))),
            "backup_files": len(list(root.rglob("*.bak"))),
            "lock_file_growth_after_warmup": (
                len(list(root.rglob("*.lock"))) - (warm_lock_count or 0)
            ),
            "backup_growth_after_warmup": (
                len(list(root.rglob("*.bak"))) - (warm_backup_count or 0)
            ),
            "scheduler_selections": selections,
            "metrics": METRICS.snapshot(),
        }
        result["passed"] = (
            result["thread_growth"] == 0
            and result["descriptor_growth"] in (0, None)
            and result["stale_transactions"] == 0
            and result["lock_file_growth_after_warmup"] == 0
            and result["backup_growth_after_warmup"] == 0
            and result["memory_growth_bytes"] < 4 * 1024 * 1024
            and sum(selections.values()) == completed
            and StateStore(root / "commits.json", "soak").load()["iteration"]
            == completed - 1
        )
        return result


def run_persistence_stress(commits=1000):
    """Thousands-of-commits integrity stress with bounded backup growth."""
    with tempfile.TemporaryDirectory(prefix="zdx-stress-") as directory:
        root = Path(directory)
        store = StateStore(root / "state.json", "stress")
        started = time.monotonic()
        for generation in range(commits):
            store.save({"generation": generation, "parity": generation % 2})
            if generation % 100 == 0:
                assert store.load()["generation"] == generation
        final = store.load()
        document = json.loads((root / "state.json").read_text())
        elapsed = time.monotonic() - started
        return {
            "schema_version": 1,
            "requested_commits": commits,
            "completed_commits": commits,
            "elapsed_seconds": elapsed,
            "commits_per_second": commits / elapsed,
            "final_generation": final["generation"],
            "checksum_present": bool(document["metadata"]["checksum"]),
            "backup_files": len(list(root.rglob("*.bak"))),
            "temporary_files": len(list(root.rglob("*.tmp"))),
            "passed": (
                final["generation"] == commits - 1
                and len(list(root.rglob("*.bak"))) == 1
                and not list(root.rglob("*.tmp"))
            ),
            "metrics": METRICS.snapshot(),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("soak", "stress", "benchmarks", "all"), default="all")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {}
    if args.mode in ("soak", "all"):
        result["soak"] = run_soak(args.iterations, args.duration)
    if args.mode in ("stress", "all"):
        result["stress"] = run_persistence_stress(args.iterations)
    if args.mode in ("benchmarks", "all"):
        result["benchmarks"] = run_benchmarks(max(5, args.iterations // 4))
    encoded = json.dumps(result, sort_keys=True, indent=2)
    if args.output:
        from zdx_storage import atomic_write_bytes
        atomic_write_bytes(args.output, encoded.encode())
    print(encoded)


if __name__ == "__main__":
    main()
