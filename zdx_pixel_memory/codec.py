"""
codec.py — Encode/decode arbitrary Python objects as PNG pixel data.

Format:
  Bytes 0-3  : uint32 big-endian — length of JSON payload
  Bytes 4-N  : UTF-8 encoded JSON
  Remaining  : zero-padding to fill last pixel triplet

Image is always WIDTH=64 pixels wide; height grows with data size.
"""

import json
import math

import numpy as np
from PIL import Image

_WIDTH = 64  # pixels per row


def encode(obj) -> Image.Image:
    """Serialize a JSON-serializable Python object into an RGB PNG image."""
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    length_header = len(payload).to_bytes(4, "big")
    raw = length_header + payload

    # Pad to a multiple of 3 (one pixel = 3 channels)
    pad = (-len(raw)) % 3
    raw = raw + b"\x00" * pad

    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)

    height = math.ceil(len(pixels) / _WIDTH)
    pad_rows = _WIDTH * height - len(pixels)
    if pad_rows:
        pixels = np.vstack([pixels, np.zeros((pad_rows, 3), dtype=np.uint8)])

    arr = pixels.reshape(height, _WIDTH, 3).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def decode(img: Image.Image):
    """Deserialize an RGB PNG image back into the original Python object."""
    arr = np.array(img.convert("RGB"), dtype=np.uint8)
    raw = arr.reshape(-1, 3).tobytes()

    if len(raw) < 4:
        raise ValueError("codec.decode: image too small to contain a length header")

    length = int.from_bytes(raw[:4], "big")

    if len(raw) < 4 + length:
        raise ValueError(
            f"codec.decode: declared payload length {length} "
            f"exceeds available data ({len(raw) - 4} bytes)"
        )

    payload = raw[4 : 4 + length]
    try:
        return json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"codec.decode: corrupt payload — {exc}") from exc
