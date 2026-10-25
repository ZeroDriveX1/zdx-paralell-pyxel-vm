"""Mobile task negotiation, desktop parity vectors and signed result binding."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from zdx_android_vm import ADAPTER_ID, FEATURE, MANIFEST_SHA256
from zdx_compute import ComputeCoordinator, ComputeTask
from zdx_ed25519_signer import ZDXEd25519Signer
from zdx_parallel_vm import ParallelPyxelVM
from zdx_server import ZDXServer


def capabilities():
    return {
        "platform": "android", "cpu_count": 4, "memory_mb": 1024,
        "android_vm_adapter_protocol": 1, "vm_features": ["pyxel-vm", FEATURE],
        "android_vm_adapters": [{
            "adapter_id": ADAPTER_ID, "protocol_version": 1,
            "max_memory_mb": 512, "max_runtime_seconds": 300,
            "capabilities": [FEATURE], "manifest_sha256": MANIFEST_SHA256,
        }],
    }


def task(**kwargs):
    return ComputeTask(artifact_digest="a" * 64, memory_mb=64, max_seconds=5,
                       metadata={"android_vm_adapter_id": ADAPTER_ID}, **kwargs)


def test_mobile_only_gets_negotiated_work_and_one_lease(tmp_path):
    queue = ComputeCoordinator(str(tmp_path / "queue.json"))
    queue.register_worker("android", capabilities())
    ordinary = queue.submit(ComputeTask(artifact_digest="b" * 64, memory_mb=64))
    mobile = queue.submit(task())
    queue.submit(task())
    claimed = queue.claim("android", 512, 4)
    assert claimed.task_id == mobile.task_id
    assert ordinary.task_id in queue.status()["queued"]
    assert queue.claim("android", 512, 4) is None


@pytest.mark.parametrize("change", ["manifest", "protocol", "runtime", "spatial", "capability", "memory", "missing"])
def test_scheduler_rejects_incompatible_android_tasks(tmp_path, change):
    cap = capabilities()
    item = task()
    if change == "manifest":
        cap["android_vm_adapters"][0]["manifest_sha256"] = "bad"
    elif change == "protocol":
        cap["android_vm_adapter_protocol"] = 2
    elif change == "runtime":
        item.max_seconds = 301
    elif change == "spatial":
        item.metadata.update(execution_model="spatial-png", spatial_layout={"width": 1, "height": 1, "execution_rows": 1})
    elif change == "capability":
        item.metadata["required_android_capabilities"] = ["shell"]
    elif change == "memory":
        item.memory_mb = 32
    else:
        cap["android_vm_adapters"] = []
    queue = ComputeCoordinator(str(tmp_path / "queue.json"))
    queue.register_worker("android", cap)
    queue.submit(item)
    assert queue.claim("android", 1024, 4) is None


def test_desktop_does_not_claim_android_specific_tasks(tmp_path):
    queue = ComputeCoordinator(str(tmp_path / "queue.json"))
    queue.register_worker("linux", {"vm_features": ["pyxel-vm"]})
    queue.submit(task())
    assert queue.claim("linux", 512, 4) is None


def test_android_vectors_remain_identical_to_desktop():
    path = Path(__file__).resolve().parents[1] / "android/app/src/test/resources/pyxel_vectors.json"
    for vector in json.loads(path.read_text()):
        raw = np.array(vector["pixels"], dtype=np.uint32)
        rgb = np.stack(((raw >> 16) & 255, (raw >> 8) & 255, raw & 255), axis=1).astype(np.uint8)
        vm = ParallelPyxelVM(threads=vector["threads"], strict_mode=True)
        vm._execute_array(rgb.reshape(vector["height"], vector["width"], 3))
        assert vm.registers == vector["registers"]
        assert vm.shared == vector["shared"]
        assert vm.clock == vector["clock"]


@pytest.mark.parametrize("tamper", [None, "digest", "adapter", "manifest", "task"])
def test_attestation_binds_adapter_and_input_to_leased_task(tmp_path, tamper):
    signer = ZDXEd25519Signer("phone", key_path=str(tmp_path / "keys"))
    server = ZDXServer(trusted_peers={"phone": signer.get_public_key_pem()},
                       state_path=str(tmp_path / "state.json"), compute_state_path=str(tmp_path / "compute.json"),
                       artifact_root=str(tmp_path / "artifacts"))
    server.compute.register_worker("phone", capabilities())
    submitted = server.compute.submit(task())
    server.compute.claim("phone", 512, 4)
    inner = {"task_id": submitted.task_id, "input_artifact_sha256": "a" * 64,
             "adapter_id": ADAPTER_ID, "adapter_protocol_version": 1, "adapter_manifest_sha256": MANIFEST_SHA256}
    field = {"digest": "input_artifact_sha256", "adapter": "adapter_id", "manifest": "adapter_manifest_sha256", "task": "task_id"}.get(tamper)
    if field:
        inner[field] = "invalid"
    claim = {"attestation_version": 1, "node_id": "phone", "result": inner,
             "result_sha256": hashlib.sha256(json.dumps(inner, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    signed = {**claim, "attestation_signature": signer.sign_message(claim), "public_key": signer.get_public_key_pem()}
    if tamper:
        with pytest.raises(PermissionError):
            server._verify_android_result("phone", submitted.task_id, signed)
    else:
        server._verify_android_result("phone", submitted.task_id, signed)
