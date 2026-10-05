import os
import tempfile

from PIL import Image

from zdx_spatial_frame import (
    SpatialCompiler,
    SpatialFrame,
    SpatialLayout,
    SpatialPyxelVM,
    SpatialRegion,
)
from zdx_pixel_memory.spatial_store import SpatialPixelStore


def test_spatial_coordinate_is_exact_machine_address():
    layout = SpatialLayout(width=256, height=256, execution_rows=8)
    assert layout.address(0, 0) == 0
    assert layout.address(255, 255) == 65535
    assert layout.coordinates(65535) == (255, 255)
    assert layout.raw_capacity_bytes == 256 * 256 * 3
    assert layout.storage_capacity_bytes == 248 * 256 * 3


def test_spatial_u24_cell_round_trip():
    layout = SpatialLayout(width=16, height=16, execution_rows=1)
    frame = SpatialFrame.blank(layout)
    frame.write_u24(7, 9, 0xA1B2C3)
    assert frame.read_u24(7, 9) == 0xA1B2C3
    assert frame.read_cell(7, 9) == (0xA1, 0xB2, 0xC3)


def test_spatial_program_executes_while_storage_rows_remain_data():
    layout = SpatialLayout(
        width=32,
        height=16,
        execution_rows=2,
        regions=(SpatialRegion("payload", 0, 2, 32, 14),),
    )
    program = [
        ["SET_A 5", "SET_B 7", "ADD", "COPY_OUT", "STORE_MEM 0", "HALT"],
        ["HALT"],
    ]
    payload = {
        "message": "storage pixels are not executable",
        "values": [10, 20, 30, 40, 50, 60, 70, 255],
    }

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "spatial_program.png")
        SpatialCompiler(layout).compile(program, path, payloads={"payload": payload})

        with open(path, "rb") as fh:
            assert fh.read(8) == b"\x89PNG\r\n\x1a\n"
        with Image.open(path) as image:
            assert image.size == (32, 16)
            assert image.mode == "RGB"
            image.verify()

        vm = SpatialPyxelVM(layout=layout)
        vm.execute_spatial(path)
        assert vm.shared["M0"] == 12

        frame = SpatialFrame.open(path, layout)
        assert frame.read_json(region="payload") == payload


def test_spatial_store_packs_multiple_keys_into_one_png():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "agent.spatial.png")
        store = SpatialPixelStore(path, width=64, height=64)
        store.write("alpha", {"n": 1})
        store.write("beta", [1, 2, 3, 4])

        assert store.read("alpha") == {"n": 1}
        assert store.read("beta") == [1, 2, 3, 4]
        assert store.keys() == ["alpha", "beta"]
        assert store.capacity_bytes == 64 * 64 * 3

        # One raster holds both keys; there is no keys.json sidecar.
        assert os.path.exists(path)
        assert not os.path.exists(os.path.join(td, "keys.json"))


def test_spatial_vm_rejects_thread_plane_overrun():
    layout = SpatialLayout(width=8, height=8, execution_rows=2)
    try:
        SpatialPyxelVM(layout=layout, threads=3)
    except ValueError as exc:
        assert "storage rows non-executable" in str(exc)
    else:
        raise AssertionError("thread count beyond execution plane must be rejected")
