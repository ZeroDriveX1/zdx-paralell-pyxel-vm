#!/usr/bin/env bash
# build.sh — Compile open-pyxel to native Cython extensions.
#
# Usage:   bash build.sh
# Output:  dist/   (ready-to-import, .so + pure-Python + .pyi)

set -euo pipefail
PYTHON="${PYTHON:-python3}"

CYTHON_C_FILES=(
    "zdx_parallel_vm.c"
    "zdx_pixel_memory/codec.c"
    "zdx_pixel_memory/store.c"
    "zdx_pixel_memory/agent_memory.c"
)

TOP_LEVEL_MODS=("zdx_parallel_vm")
PKG_MODS=("codec" "store" "agent_memory")

find_binary() {
    local name="$1" dir="$2"
    find "$dir" -maxdepth 1 \( \
        -name "${name}.so"          -o \
        -name "${name}.pyd"         -o \
        -name "${name}.cpython*.so" -o \
        -name "${name}.cpython*.pyd" \
    \) 2>/dev/null | head -1
}

echo "▶ Compiling Cython extensions..."
"$PYTHON" setup.py build_ext --inplace

echo "▶ Assembling dist/..."
rm -rf dist
mkdir -p dist/zdx_pixel_memory

for mod in "${TOP_LEVEL_MODS[@]}"; do
    bin=$(find_binary "$mod" ".")
    [ -n "$bin" ] || { echo "ERROR: binary for '${mod}' not found" >&2; exit 1; }
    cp "$bin" "dist/"
done

for mod in "${PKG_MODS[@]}"; do
    bin=$(find_binary "$mod" "zdx_pixel_memory")
    [ -n "$bin" ] || { echo "ERROR: binary for 'zdx_pixel_memory.${mod}' not found" >&2; exit 1; }
    cp "$bin" "dist/zdx_pixel_memory/"
done

cp pyxel_registry.py                dist/
cp zdx_agent_runtime.py             dist/
cp zdx_pixel_memory/__init__.py     dist/zdx_pixel_memory/
cp zdx_parallel_vm.pyi              dist/
cp zdx_pixel_memory/codec.pyi       dist/zdx_pixel_memory/
cp zdx_pixel_memory/store.pyi       dist/zdx_pixel_memory/
cp zdx_pixel_memory/agent_memory.pyi dist/zdx_pixel_memory/

for f in "${CYTHON_C_FILES[@]}"; do
    [ -f "$f" ] && rm "$f"
done

echo "▶ Build complete. dist/ layout:"
find dist -type f | sort | sed 's/^/  /'
