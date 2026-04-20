"""
codec.pyi — Public API stubs for the pixel codec.

encode: Python object → RGB PNG image
decode: RGB PNG image → Python object
"""

from typing import Any

# PIL.Image is a runtime dependency; use a forward reference to avoid
# importing PIL at type-check time if it is not installed.
try:
    from PIL.Image import Image
except ImportError:
    from typing import Any as Image  # type: ignore[assignment]

def encode(obj: Any) -> Image: ...
def decode(img: Image) -> Any: ...
