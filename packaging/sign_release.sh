#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=${ZDX_RELEASE_VERSION:-1.0.0}
KEY=${ZDX_RELEASE_SIGNING_KEY:-}
OUTPUT=${ZDX_RELEASE_MANIFEST:-$ROOT/release/release-manifest.json}
APK=${ZDX_ANDROID_APK:-}
INVENTORY="$ROOT/release/RELEASE_FILES.txt"

[[ -n "$KEY" && -f "$KEY" ]] || { echo "Set ZDX_RELEASE_SIGNING_KEY to an operator-controlled Ed25519 private key" >&2; exit 2; }
KEY_REAL=$(readlink -f "$KEY")
case "$KEY_REAL" in "$ROOT"/*) echo "release signing key must not be inside the release root" >&2; exit 2 ;; esac
[[ -f "$INVENTORY" ]] || { echo "missing release inventory: $INVENTORY" >&2; exit 2; }

mapfile -t INVENTORY_FILES < <(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$INVENTORY")
FILES=()
for relative in "${INVENTORY_FILES[@]}"; do
  [[ "$relative" != /* && "$relative" != *".."* ]] || { echo "invalid inventory path: $relative" >&2; exit 2; }
  [[ -f "$ROOT/$relative" ]] || { echo "inventory file is missing: $relative" >&2; exit 2; }
  FILES+=("$relative")
done

if [[ -n "$APK" ]]; then
  APK_ABS=$(readlink -f "$APK")
  [[ -f "$APK_ABS" ]] || { echo "ZDX_ANDROID_APK does not point to a file" >&2; exit 2; }
  case "$APK_ABS" in "$ROOT"/*) ;; *) echo "ZDX_ANDROID_APK must be inside the release root" >&2; exit 2 ;; esac
  APK_REL=${APK_ABS#$ROOT/}
  FILES+=("$APK_REL")
else
  APK_REL=""
fi

MANIFEST_FILES=(release/RELEASE_FILES.txt release/SHA256SUMS "${INVENTORY_FILES[@]}")
if [[ -n "$APK_REL" ]]; then MANIFEST_FILES+=("$APK_REL"); fi

(
  cd "$ROOT"
  sha256sum "${FILES[@]}" > release/SHA256SUMS
  sha256sum -c release/SHA256SUMS >/dev/null
)

ARGS=(create --root "$ROOT" --version "$VERSION" --signing-key "$KEY" --output "$OUTPUT")
for file in "${MANIFEST_FILES[@]}"; do ARGS+=(--file "$file"); done
python3 "$ROOT/packaging/release_manifest.py" "${ARGS[@]}"
python3 "$ROOT/packaging/release_manifest.py" verify "$OUTPUT" --root "$ROOT"

python3 - "$OUTPUT" "$ROOT" "$APK_REL" <<'PY'
import json
import sys
from pathlib import Path

manifest_path, root, apk = sys.argv[1:]
manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
listed = {item["path"] for item in manifest["files"]}
inventory = []
for raw in (Path(root) / "release/RELEASE_FILES.txt").read_text(encoding="utf-8").splitlines():
    value = raw.split("#", 1)[0].strip()
    if value:
        inventory.append(value)
expected = set(inventory) | {"release/SHA256SUMS"}
if apk:
    expected.add(apk)
if listed != expected:
    raise SystemExit(f"manifest coverage mismatch: missing={sorted(expected-listed)} extra={sorted(listed-expected)}")
checksum_paths = set()
for line in (Path(root) / "release/SHA256SUMS").read_text(encoding="utf-8").splitlines():
    if line.strip():
        checksum_paths.add(line.split(None, 1)[1].lstrip(" *"))
if checksum_paths != (set(inventory) | ({apk} if apk else set())):
    raise SystemExit("checksum coverage does not match release inventory")
PY

chmod 0644 "$OUTPUT" "$ROOT/release/SHA256SUMS"
echo "signed release manifest created at $OUTPUT"
