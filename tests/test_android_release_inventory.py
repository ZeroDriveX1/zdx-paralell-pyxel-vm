"""The actual signed source-release path must cover every new runtime source."""

import argparse
import importlib.util
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

ROOT = Path(__file__).resolve().parents[1]
KOTLIN = "android/app/src/main/java/com/zerodrivex/zdxnode/"
NEW_RUNTIME = ["zdx_android_vm.py"] + [KOTLIN + name for name in (
    "AndroidPyxelAdapter.kt", "CanonicalJson.kt", "PyxelEngine.kt", "PyxelIsolatedService.kt",
)]


def inventory():
    return {line.split("#", 1)[0].strip() for line in (ROOT / "release/RELEASE_FILES.txt").read_text().splitlines()} - {""}


def test_android_runtime_and_dependency_policy_are_in_signed_inventory():
    assert set(NEW_RUNTIME + ["requirements.txt", "requirements-security.txt", "pyproject.toml", "Dockerfile"]) <= inventory()
    for name in inventory():
        assert (ROOT / name).is_file(), name


@pytest.mark.parametrize("changed", NEW_RUNTIME)
def test_signed_manifest_detects_android_runtime_tampering(tmp_path, changed):
    spec = importlib.util.spec_from_file_location("release_manifest_test", ROOT / "packaging/release_manifest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    release_root = tmp_path / "release"
    for name in NEW_RUNTIME:
        target = release_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    key_path = tmp_path / "test-key.pem"
    key = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(key)
    manifest = tmp_path / "manifest.json"
    module.create(argparse.Namespace(root=release_root, signing_key=key_path, output=manifest, version="test", file=NEW_RUNTIME))
    args = argparse.Namespace(root=release_root, manifest=manifest)
    module.verify(args)
    target = release_root / changed
    target.write_bytes(target.read_bytes() + b"\n// altered runtime\n")
    with pytest.raises(ValueError, match="failed verification"):
        module.verify(args)
