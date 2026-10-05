import os
import struct
import tempfile
import zlib

import pytest
from PIL import Image

from pyxel_registry import PyxelRegistry
from zdx_agent_runtime import ZDXAgentRuntime
from zdx_pixel_memory import ZDXAgentMemory
from zdx_pixel_memory.spatial_store import SpatialPixelStore
from zdx_spatial_frame import (
    SpatialCompiler,
    SpatialFrame,
    SpatialLayout,
    SpatialPyxelVM,
    SpatialRegion,
)


def _png_chunks(path):
    """Parse the PNG container and validate each chunk CRC and exact EOF."""
    with open(path, "rb") as fh:
        data = fh.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    chunks = []
    while True:
        assert pos + 12 <= len(data), "truncated PNG chunk"
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        start = pos + 8
        end = start + length
        assert end + 4 <= len(data), "declared PNG chunk exceeds file"
        payload = data[start:end]
        expected_crc = struct.unpack(">I", data[end : end + 4])[0]
        actual_crc = zlib.crc32(ctype)
        actual_crc = zlib.crc32(payload, actual_crc) & 0xFFFFFFFF
        assert actual_crc == expected_crc, f"CRC mismatch for {ctype!r}"
        chunks.append(ctype)
        pos = end + 4
        if ctype == b"IEND":
            break
    assert pos == len(data), "data exists after IEND"
    return chunks


def test_spatial_png_is_strict_standards_valid_container():
    layout = SpatialLayout(
        width=64,
        height=64,
        execution_rows=4,
        regions=(SpatialRegion("payload", 0, 4, 64, 60),),
    )
    program = [["SET_A 3", "SET_B 4", "ADD", "COPY_OUT", "HALT"]]
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "frame.png")
        SpatialCompiler(layout).compile(program, path, payloads={"payload": {"ok": True}})
        chunks = _png_chunks(path)
        assert chunks[0] == b"IHDR"
        assert b"IDAT" in chunks
        assert chunks[-1] == b"IEND"
        with Image.open(path) as image:
            assert image.size == (64, 64)
            assert image.mode == "RGB"
            image.verify()


def test_full_xy_address_space_and_u24_machine_cells():
    layout = SpatialLayout(width=257, height=131, execution_rows=3)
    frame = SpatialFrame.blank(layout)
    points = [
        (0, 0, 0x000001),
        (256, 0, 0x102030),
        (0, 130, 0xA1B2C3),
        (256, 130, 0xFFFFFF),
        (128, 65, 0x55AA11),
    ]
    for x, y, value in points:
        frame.write_u24(x, y, value)
        assert layout.coordinates(layout.address(x, y)) == (x, y)
        assert frame.read_u24(x, y) == value
    assert layout.total_cells == 257 * 131
    assert layout.raw_capacity_bytes == 257 * 131 * 3


def test_storage_pixels_that_equal_live_opcodes_never_execute():
    layout = SpatialLayout(width=32, height=12, execution_rows=2)
    frame = SpatialFrame.blank(layout)
    # Executable T0 computes 5+7 and stores 12 in M0. T1 halts immediately.
    compiler = SpatialCompiler(layout)
    program = [
        ["SET_A 5", "SET_B 7", "ADD", "COPY_OUT", "STORE_MEM 0", "HALT"],
        ["HALT"],
    ]
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "opcodes-in-storage.png")
        compiler.compile(program, path)
        frame = SpatialFrame.open(path, layout)
        # Fill storage with values that would be destructive if interpreted as code.
        for y in range(layout.execution_rows, layout.height):
            for x in range(layout.width):
                frame.write_cell(x, y, (255, 255, 255) if x % 2 == 0 else (70, 0, 0))
        frame.save(path)

        vm = SpatialPyxelVM(layout=layout)
        vm.execute_spatial(path)
        assert vm.shared["M0"] == 12
        assert vm.registers["T0"]["OUT"] == 12
        assert set(vm.registers) == {"T0", "T1"}


def test_multi_thread_spatial_execution_keeps_existing_column_semantics():
    layout = SpatialLayout(width=16, height=8, execution_rows=2)
    # Column 0: T0 writes M0=9 before T1 loads M0 in the same column.
    program = [
        ["SET_A 9", "STORE_MEM 0", "HALT"],
        ["LOAD_MEM 0", "SET_B 1", "ADD", "HALT"],
    ]
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "parallel.png")
        SpatialCompiler(layout).compile(program, path)
        vm = SpatialPyxelVM(layout=layout)
        vm.execute_spatial(path)
        # T1 LOAD_MEM occurs at column 0 after T0 SET_A, not STORE_MEM, so M0 is
        # still zero there. This confirms exact inherited column-major timing.
        assert vm.registers["T1"]["A"] == 0
        assert vm.shared["M0"] == 9
        assert vm.registers["T1"]["OUT"] == 1


def test_named_regions_are_exact_and_isolated():
    layout = SpatialLayout(
        width=40,
        height=20,
        execution_rows=2,
        regions=(
            SpatialRegion("facts", 0, 2, 20, 9),
            SpatialRegion("state", 20, 2, 20, 9),
            SpatialRegion("cache", 0, 11, 40, 9),
        ),
    )
    frame = SpatialFrame.blank(layout)
    frame.write_json({"facts": [1, 2, 3]}, region="facts")
    frame.write_json({"state": "ready"}, region="state")
    frame.write_bytes(b"cache-bytes", region="cache")
    assert frame.read_json(region="facts") == {"facts": [1, 2, 3]}
    assert frame.read_json(region="state") == {"state": "ready"}
    assert frame.read_bytes(len(b"cache-bytes"), region="cache") == b"cache-bytes"


def test_region_overlap_and_execution_overlap_are_rejected():
    with pytest.raises(ValueError):
        SpatialLayout(
            width=16,
            height=16,
            execution_rows=2,
            regions=(SpatialRegion("bad", 0, 1, 4, 4),),
        )
    with pytest.raises(ValueError):
        SpatialLayout(
            width=16,
            height=16,
            execution_rows=2,
            regions=(
                SpatialRegion("a", 0, 2, 8, 8),
                SpatialRegion("b", 4, 4, 8, 8),
            ),
        )


def test_region_capacity_is_enforced_without_spill():
    layout = SpatialLayout(
        width=8,
        height=8,
        execution_rows=1,
        regions=(SpatialRegion("tiny", 0, 1, 2, 2),),
    )
    frame = SpatialFrame.blank(layout)
    assert layout.region("tiny").capacity_bytes == 12
    frame.write_bytes(b"123456789012", region="tiny")
    assert frame.read_bytes(12, region="tiny") == b"123456789012"
    with pytest.raises(ValueError):
        frame.write_bytes(b"1234567890123", region="tiny")


def test_spatial_store_and_agent_memory_use_single_png_without_sidecar():
    with tempfile.TemporaryDirectory() as td:
        store_path = os.path.join(td, "packed.png")
        store = SpatialPixelStore(store_path, width=96, height=96)
        for i in range(25):
            store.write(f"k{i:02d}", {"value": i, "square": i * i})
        assert len(store.keys()) == 25
        assert store.read("k17") == {"value": 17, "square": 289}
        assert os.path.isfile(store_path)
        assert not os.path.exists(os.path.join(td, "keys.json"))
        _png_chunks(store_path)

        base_dir = os.path.join(td, "agents") + "/"
        mem = ZDXAgentMemory(
            agent_id="spatial_agent",
            base_dir=base_dir,
            spatial=True,
            spatial_width=96,
            spatial_height=96,
        )
        mem.remember("name", "zdx")
        mem.append("history", "first")
        mem.append("history", "second")
        assert mem.recall("name") == "zdx"
        assert mem.recall_list("history") == ["first", "second"]
        assert mem.capacity_bytes == 96 * 96 * 3
        agent_dir = os.path.join(base_dir, "spatial_agent")
        assert sorted(os.listdir(agent_dir)) == ["memory.spatial.png"]


def test_agent_runtime_spatial_execution_persists_geometry_and_state():
    layout = SpatialLayout(width=32, height=16, execution_rows=1)
    program = [["SET_A 8", "SET_B 9", "ADD", "COPY_OUT", "STORE_MEM 2", "HALT"]]
    with tempfile.TemporaryDirectory() as td:
        program_path = os.path.join(td, "program.png")
        SpatialCompiler(layout).compile(program, program_path)

        registry = PyxelRegistry()
        vm = SpatialPyxelVM(layout=layout)
        mem = ZDXAgentMemory(
            agent_id="runtime",
            base_dir=os.path.join(td, "memory") + "/",
            spatial=True,
            spatial_width=128,
            spatial_height=128,
        )
        registry.register("vm", vm)
        registry.register("memory", mem)
        result = ZDXAgentRuntime(registry).run_spatial(program_path)

        assert result["T0"]["OUT"] == 17
        assert mem.recall("shared_state")["M2"] == 17
        assert mem.recall("register_state")["T0"]["A"] == 17
        persisted_layout = mem.recall("spatial_layout")
        assert persisted_layout["width"] == 32
        assert persisted_layout["height"] == 16
        assert persisted_layout["execution_rows"] == 1


def test_capacity_scales_with_area_not_only_program_length():
    narrow = SpatialLayout(width=256, height=1, execution_rows=1)
    spatial = SpatialLayout(width=256, height=256, execution_rows=1)
    assert narrow.total_cells == 256
    assert spatial.total_cells == 65536
    assert spatial.total_cells == narrow.total_cells * 256
    assert spatial.raw_capacity_bytes == 196608
    assert spatial.storage_capacity_bytes == 255 * 256 * 3
