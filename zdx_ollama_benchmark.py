"""Repeatable mission-pipeline benchmarks with optional live Ollama execution."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from zdx_inference import InferenceProvider, InferenceResult, OllamaProvider
from zdx_metrics import MetricsRegistry, resource_snapshot
from zdx_mission import MissionAgent, MissionExecutor, MissionHistory
from zdx_scheduler import ZDXScheduler
from zdx_storage import atomic_write_bytes


class BenchmarkProvider(InferenceProvider):
    @property
    def model(self): return "deterministic:benchmark"
    def generate(self, prompt, *, system=None):
        started = time.perf_counter()
        text = "revised verified result" if "Revise" in prompt else "verified result"
        return InferenceResult(text, self.model, time.perf_counter() - started)


def summary(samples):
    total = sum(samples)
    return {
        "iterations": len(samples), "total_seconds": total,
        "average_seconds": statistics.mean(samples),
        "p50_seconds": statistics.median(samples), "max_seconds": max(samples),
        "operations_per_second": len(samples) / total if total else 0.0,
    }


def run(iterations=20, live=False):
    metrics = MetricsRegistry()
    provider = OllamaProvider(metrics=metrics) if live else BenchmarkProvider()
    with tempfile.TemporaryDirectory(prefix="zdx-mission-bench-") as directory:
        history = MissionHistory(Path(directory) / "missions.json")
        scheduler = ZDXScheduler()
        agent = MissionAgent(provider, history, metrics=metrics)
        executor = MissionExecutor(scheduler, agent)
        mission_samples = []
        provider_samples = []
        scheduler_samples = []
        persistence_samples = []
        for index in range(iterations):
            started = time.perf_counter(); provider.generate(f"provider probe {index}"); provider_samples.append(time.perf_counter() - started)
            class Noop:
                def execute(self, mission): return mission
            started = time.perf_counter(); scheduler.execute_mission(Noop(), "probe"); scheduler_samples.append(time.perf_counter() - started)
            started = time.perf_counter(); history.append({"mission_id": f"probe-{index}", "verification_status": True}); persistence_samples.append(time.perf_counter() - started)
            started = time.perf_counter(); executor.execute(f"Return the word ready. Mission {index}"); mission_samples.append(time.perf_counter() - started)
        reflection_provider = BenchmarkProvider() if not live else provider
        checks = iter([(False, "benchmark forced critique"), (True, "accepted")])
        reflected = MissionAgent(reflection_provider, history, metrics=metrics,
                                 reflection_enabled=True, max_reflection_cycles=1,
                                 verifier=lambda *_: next(checks))
        started = time.perf_counter()
        MissionExecutor(scheduler, reflected).execute("Reflection overhead benchmark")
        reflection_seconds = time.perf_counter() - started
        result = {
            "schema_version": 1, "mode": "live_ollama" if live else "deterministic_provider",
            "model": provider.model, "missions_per_minute": 60 / statistics.mean(mission_samples),
            "mission_latency": summary(mission_samples), "provider_latency": summary(provider_samples),
            "scheduler_overhead": summary(scheduler_samples), "persistence_overhead": summary(persistence_samples),
            "reflection_one_cycle_seconds": reflection_seconds,
            "metrics": metrics.snapshot(), "resources": resource_snapshot(),
        }
    provider.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--output", required=True)
    parser.add_argument("--live", action="store_true", help="use configured Ollama instead of deterministic provider")
    args = parser.parse_args()
    if args.iterations < 1: parser.error("iterations must be positive")
    result = run(args.iterations, args.live)
    atomic_write_bytes(args.output, json.dumps(result, indent=2, sort_keys=True).encode())
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__": main()
