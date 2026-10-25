"""Exercise the executable PNG boundary against CVE-2026-112399."""

import struct
import zlib

import pytest
from PIL import Image

from zdx_parallel_vm import ParallelPyxelVM
from zdx_spatial_frame import SpatialFrame, SpatialLayout


def _chunk(kind, payload):
    return (
        struct.pack(">I", len(payload)) + kind + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _oversized_apng(disposal):
    # A tiny first frame must not cause eager allocation of the huge canvas.
    return b"\x89PNG\r\n\x1a\n" + b"".join([
        _chunk(b"IHDR", struct.pack(">IIBBBBB", 2**31 - 1, 1, 8, 6, 0, 0, 0)),
        _chunk(b"acTL", struct.pack(">II", 2, 0)),
        _chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 1, 1, 0, 0, 1, 10, disposal, 0)),
        _chunk(b"IDAT", zlib.compress(b"\x00" * 5)),
        _chunk(b"IEND", b""),
    ])


@pytest.mark.parametrize("disposal", [0, 1, 2])
@pytest.mark.parametrize("loader", ["vm", "spatial"])
def test_oversized_apng_rejected_before_canvas_allocation_or_execution(
    tmp_path, monkeypatch, disposal, loader
):
    # Trap the vulnerable native allocation *before* it can consume memory.
    # This makes the test safe even when deliberately run against Pillow 12.3.
    original_fill = Image.core.fill

    def bounded_fill(mode, size, *args, **kwargs):
        assert size[0] * size[1] <= 1_000_000, "pre-check APNG canvas allocation"
        return original_fill(mode, size, *args, **kwargs)

    monkeypatch.setattr(Image.core, "fill", bounded_fill)
    path = tmp_path / "oversized.png"
    path.write_bytes(_oversized_apng(disposal))

    def no_execution(*args, **kwargs):
        pytest.fail("untrusted oversized PNG reached VM instruction execution")

    monkeypatch.setattr(ParallelPyxelVM, "_execute_array", no_execution)
    with pytest.raises(Image.DecompressionBombError):
        if loader == "vm":
            ParallelPyxelVM().execute_texture(str(path))
        else:
            SpatialFrame.open(str(path), SpatialLayout(width=1, height=1, execution_rows=1))
