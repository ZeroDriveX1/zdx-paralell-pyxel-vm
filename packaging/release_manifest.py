#!/usr/bin/env python3
"""Create and verify Ed25519-signed ZDX release manifests."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _files(root: Path, names: list[str]) -> list[dict]:
    output = []
    for name in sorted(names):
        path = (root / name).resolve()
        if root.resolve() not in path.parents:
            raise ValueError(f"release file escapes root: {name}")
        data = path.read_bytes()
        output.append({"path": str(path.relative_to(root.resolve())), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return output


def create(args) -> None:
    root = Path(args.root).resolve()
    private_key = serialization.load_pem_private_key(Path(args.signing_key).read_bytes(), password=None)
    public_key = private_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
    manifest = {"format": 1, "product": "zdx-pyxel", "version": args.version, "files": _files(root, args.file)}
    manifest["public_key_pem"] = public_key
    manifest["signature_ed25519"] = base64.b64encode(private_key.sign(_canonical(manifest))).decode("ascii")
    Path(args.output).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify(args) -> None:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    signature = base64.b64decode(manifest.pop("signature_ed25519"), validate=True)
    public_key = serialization.load_pem_public_key(manifest["public_key_pem"].encode("ascii"))
    public_key.verify(signature, _canonical(manifest))
    root = Path(args.root).resolve()
    for item in manifest["files"]:
        path = root / item["path"]
        data = path.read_bytes()
        if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"release file failed verification: {item['path']}")
    print(f"verified {manifest['product']} {manifest['version']} ({len(manifest['files'])} files)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("create")
    make.add_argument("--version", required=True); make.add_argument("--signing-key", required=True); make.add_argument("--root", default="."); make.add_argument("--output", required=True); make.add_argument("--file", action="append", required=True)
    check = sub.add_parser("verify")
    check.add_argument("manifest"); check.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    (create if args.command == "create" else verify)(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
