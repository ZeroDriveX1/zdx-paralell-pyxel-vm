"""Verify a real emulator's signed VM output using the coordinator's verifier."""

import base64
import hashlib
import json
from pathlib import Path
import sys

from zdx_android_vm import ADAPTER_ID, MANIFEST_SHA256
from zdx_ed25519_signer import verify_ed25519_signature


def main():
    lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
    claims = [line.removeprefix("CLAIM=") for line in lines if line.startswith("CLAIM=")]
    if not claims:
        raise ValueError("emulator did not export a signed test claim")
    claim = json.loads(base64.b64decode(claims[-1], validate=True).decode("utf-8"))
    signed = {key: claim[key] for key in ("attestation_version", "node_id", "result", "result_sha256")}
    if not verify_ed25519_signature(claim["public_key"], signed, claim["attestation_signature"]):
        raise ValueError("coordinator rejected real Android signature")
    result = signed["result"]
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != signed["result_sha256"]:
        raise ValueError("Android result digest does not match Python canonical JSON")
    png = Path(__file__).resolve().parents[1] / "android/app/src/androidTest/assets/program.png"
    assert result["input_artifact_sha256"] == hashlib.sha256(png.read_bytes()).hexdigest()
    assert result["adapter_id"] == ADAPTER_ID
    assert result["adapter_manifest_sha256"] == MANIFEST_SHA256
    assert result["shared"]["M0"] == 12
    assert result["registers"]["T0"] == {"A": 12, "B": 7, "OUT": 12, "T": 5}
    assert result["interop"] == "é😀" and signed["node_id"]
    print("Real Android isolated VM result verified by Python coordinator signature code")


if __name__ == "__main__":
    main()
