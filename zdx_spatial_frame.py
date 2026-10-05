"""Spatial PNG execution/storage primitives for ZDX PyxelVM.

A spatial frame is still a standards-valid RGB PNG.  The decoded raster is the
machine representation:

    X/Y  -> implicit location/address/topology
    R    -> opcode (inside executable rows)
    G/B  -> operands / immediate fields

Rows below the executable plane are storage cells.  They are not translated into
Python or another bytecode format and are not executed by the VM because the VM
thread count is intentionally bounded to ``execution_rows``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping, Optional

import numpy as np
from PIL import Image

from zdx_parallel_vm import ParallelPyxelVM, SimpleCompiler


_NOP = (0, 0, 0)


@dataclass(frozen=True)
class SpatialRegion:
    """A rectangular region addressed directly by PNG coordinates."""

    name: str
    x: int
    y: int
    width: int
    height: int
    kind: str = "data"

    @property
    def cells(self) -> int:
        return self.width * self.height

    @property
    def capacity_bytes(self) -> int:
        return self.cells * 3

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


@dataclass(frozen=True)
class SpatialLayout:
    """Deterministic geometry contract for a spatial PNG.

    ``execution_rows`` are the rows the VM may execute.  All remaining rows are
    addressable storage.  Optional named regions subdivide that storage plane.
    """

    width: int = 256
    height: int = 256
    execution_rows: int = 8
    regions: tuple[SpatialRegion, ...] = ()

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("SpatialLayout width/height must be >= 1")
        if not 0 <= self.execution_rows <= self.height:
            raise ValueError("execution_rows must be between 0 and image height")

        names: set[str] = set()
        regions = list(self.regions)
        for region in regions:
            if not region.name:
                raise ValueError("SpatialRegion name must not be empty")
            if region.name in names:
                raise ValueError(f"duplicate spatial region name: {region.name}")
            names.add(region.name)
            if region.width < 1 or region.height < 1:
                raise ValueError(f"region {region.name!r} must have positive dimensions")
            if region.x < 0 or region.y < 0:
                raise ValueError(f"region {region.name!r} has a negative origin")
            if region.x + region.width > self.width or region.y + region.height > self.height:
                raise ValueError(f"region {region.name!r} exceeds frame bounds")
            if region.kind != "execution" and region.y < self.execution_rows:
                raise ValueError(
                    f"data region {region.name!r} overlaps executable rows 0..{self.execution_rows - 1}"
                )

        for i, left in enumerate(regions):
            for right in regions[i + 1 :]:
                if _regions_overlap(left, right):
                    raise ValueError(f"spatial regions overlap: {left.name!r} and {right.name!r}")

    @property
    def execution_region(self) -> SpatialRegion:
        return SpatialRegion("execution", 0, 0, self.width, self.execution_rows, "execution")

    @property
    def storage_region(self) -> SpatialRegion:
        return SpatialRegion(
            "storage",
            0,
            self.execution_rows,
            self.width,
            self.height - self.execution_rows,
            "data",
        )

    @property
    def total_cells(self) -> int:
        return self.width * self.height

    @property
    def raw_capacity_bytes(self) -> int:
        return self.total_cells * 3

    @property
    def storage_capacity_bytes(self) -> int:
        return self.storage_region.capacity_bytes

    def region(self, name: Optional[str] = None) -> SpatialRegion:
        if name is None or name == "storage":
            region = self.storage_region
            if region.height == 0:
                raise ValueError("layout has no storage rows")
            return region
        if name == "execution":
            return self.execution_region
        for region in self.regions:
            if region.name == name:
                return region
        raise KeyError(f"unknown spatial region: {name}")

    def address(self, x: int, y: int) -> int:
        """Return the absolute row-major cell address for exact coordinate x/y."""
        self._check_coordinate(x, y)
        return y * self.width + x

    def coordinates(self, address: int) -> tuple[int, int]:
        if not 0 <= address < self.total_cells:
            raise ValueError(f"spatial address out of range: {address}")
        return address % self.width, address // self.width

    def _check_coordinate(self, x: int, y: int) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError(f"coordinate ({x},{y}) outside {self.width}x{self.height} frame")

    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "execution_rows": self.execution_rows,
            "raw_capacity_bytes": self.raw_capacity_bytes,
            "storage_capacity_bytes": self.storage_capacity_bytes,
            "regions": [
                {
                    "name": r.name,
                    "x": r.x,
                    "y": r.y,
                    "width": r.width,
                    "height": r.height,
                    "kind": r.kind,
                    "capacity_bytes": r.capacity_bytes,
                }
                for r in self.regions
            ],
        }


def _regions_overlap(a: SpatialRegion, b: SpatialRegion) -> bool:
    return not (
        a.x + a.width <= b.x
        or b.x + b.width <= a.x
        or a.y + a.height <= b.y
        or b.y + b.height <= a.y
    )


class SpatialFrame:
    """Mutable view over an RGB PNG using direct coordinate/cell addressing."""

    def __init__(self, image: Image.Image, layout: SpatialLayout):
        rgb = image.convert("RGB")
        if rgb.size != (layout.width, layout.height):
            raise ValueError(
                f"frame size {rgb.size} does not match spatial layout "
                f"{layout.width}x{layout.height}"
            )
        self.image = rgb
        self.layout = layout

    @classmethod
    def blank(cls, layout: SpatialLayout) -> "SpatialFrame":
        return cls(Image.new("RGB", (layout.width, layout.height), _NOP), layout)

    @classmethod
    def open(cls, path: str, layout: SpatialLayout) -> "SpatialFrame":
        with Image.open(path) as image:
            image.load()
            return cls(image.copy(), layout)

    def read_cell(self, x: int, y: int) -> tuple[int, int, int]:
        self.layout._check_coordinate(x, y)
        r, g, b = self.image.getpixel((x, y))
        return int(r), int(g), int(b)

    def write_cell(self, x: int, y: int, rgb: tuple[int, int, int]) -> None:
        self.layout._check_coordinate(x, y)
        if len(rgb) != 3 or any(not 0 <= int(v) <= 255 for v in rgb):
            raise ValueError("RGB cell values must each be in range 0..255")
        self.image.putpixel((x, y), tuple(int(v) for v in rgb))

    def read_u24(self, x: int, y: int) -> int:
        r, g, b = self.read_cell(x, y)
        return (r << 16) | (g << 8) | b

    def write_u24(self, x: int, y: int, value: int) -> None:
        if not 0 <= value <= 0xFFFFFF:
            raise ValueError("24-bit cell value must be in range 0..0xFFFFFF")
        self.write_cell(x, y, ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF))

    def read_bytes(self, length: int, *, region: Optional[str] = None, offset: int = 0) -> bytes:
        target = self.layout.region(region)
        if length < 0 or offset < 0 or offset + length > target.capacity_bytes:
            raise ValueError("spatial byte read exceeds region capacity")
        block = np.array(self.image, dtype=np.uint8)[
            target.y : target.y + target.height,
            target.x : target.x + target.width,
            :,
        ].reshape(-1)
        return block[offset : offset + length].tobytes()

    def write_bytes(self, data: bytes, *, region: Optional[str] = None, offset: int = 0) -> None:
        target = self.layout.region(region)
        raw = bytes(data)
        if offset < 0 or offset + len(raw) > target.capacity_bytes:
            raise ValueError(
                f"payload of {len(raw)} bytes at offset {offset} exceeds "
                f"region {target.name!r} capacity {target.capacity_bytes}"
            )
        arr = np.array(self.image, dtype=np.uint8)
        block = arr[
            target.y : target.y + target.height,
            target.x : target.x + target.width,
            :,
        ].copy()
        flat = block.reshape(-1)
        flat[offset : offset + len(raw)] = np.frombuffer(raw, dtype=np.uint8)
        arr[
            target.y : target.y + target.height,
            target.x : target.x + target.width,
            :,
        ] = flat.reshape(target.height, target.width, 3)
        self.image = Image.fromarray(arr, "RGB")

    def write_json(self, obj, *, region: Optional[str] = None) -> None:
        payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        raw = len(payload).to_bytes(4, "big") + payload
        self.write_bytes(raw, region=region, offset=0)

    def read_json(self, *, region: Optional[str] = None):
        target = self.layout.region(region)
        if target.capacity_bytes < 4:
            raise ValueError("spatial region is too small for a JSON length header")
        length = int.from_bytes(self.read_bytes(4, region=region), "big")
        if length == 0:
            return None
        if length > target.capacity_bytes - 4:
            raise ValueError(
                f"declared spatial JSON payload length {length} exceeds "
                f"region capacity {target.capacity_bytes - 4}"
            )
        payload = self.read_bytes(length, region=region, offset=4)
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"corrupt spatial JSON payload: {exc}") from exc

    def save(self, path: str) -> str:
        self.image.save(path, format="PNG")
        return path


class SpatialCompiler:
    """Compile executable rows and storage payloads into one spatial PNG."""

    def __init__(self, layout: SpatialLayout, compiler: Optional[SimpleCompiler] = None):
        self.layout = layout
        self.compiler = compiler if compiler is not None else SimpleCompiler()

    def compile(
        self,
        program: list,
        out_path: str,
        *,
        payloads: Optional[Mapping[str, object]] = None,
        debug: bool = False,
    ) -> str:
        program = self.compiler._expand(program)
        self.compiler._validate(program, debug)
        if len(program) > self.layout.execution_rows:
            raise ValueError(
                f"program has {len(program)} threads but layout allows "
                f"{self.layout.execution_rows} executable rows"
            )
        max_cols = max((len(row) for row in program), default=0)
        if max_cols > self.layout.width:
            raise ValueError(
                f"program width {max_cols} exceeds spatial frame width {self.layout.width}"
            )

        frame = SpatialFrame.blank(self.layout)
        for y, row in enumerate(program):
            for x, instruction in enumerate(row):
                frame.write_cell(x, y, self.compiler._parse_one(instruction, debug=debug))

        for name, value in (payloads or {}).items():
            if isinstance(value, (bytes, bytearray, memoryview)):
                frame.write_bytes(bytes(value), region=name)
            else:
                frame.write_json(value, region=name)

        frame.save(out_path)
        if debug:
            print(
                f"[SpatialCompiler] {self.layout.width}x{self.layout.height} PNG: "
                f"exec_rows={self.layout.execution_rows}, "
                f"storage={self.layout.storage_capacity_bytes} bytes"
            )
        return out_path

    def capacity_report(self) -> dict:
        return self.layout.to_dict()


class SpatialPyxelVM(ParallelPyxelVM):
    """ParallelPyxelVM with an explicit spatial-frame geometry contract.

    The inherited 16-opcode ISA is unchanged.  Execution still reads RGB cells
    directly.  Storage rows are outside the configured thread plane and therefore
    remain data, not instructions.
    """

    def __init__(self, layout: Optional[SpatialLayout] = None, **kwargs):
        self.layout = layout if layout is not None else SpatialLayout()
        requested_threads = kwargs.get("threads", self.layout.execution_rows)
        if requested_threads != self.layout.execution_rows:
            raise ValueError(
                "SpatialPyxelVM threads must equal layout.execution_rows to keep "
                "storage rows non-executable"
            )
        kwargs["threads"] = requested_threads
        super().__init__(**kwargs)

    def execute_spatial(self, image_path: str) -> dict:
        # Opening validates that the PNG dimensions match the geometry contract.
        SpatialFrame.open(image_path, self.layout)
        return self.execute_texture(image_path)

    def open_spatial_frame(self, image_path: str) -> SpatialFrame:
        return SpatialFrame.open(image_path, self.layout)

    def spatial_address(self, x: int, y: int) -> int:
        return self.layout.address(x, y)

    def storage_read_bytes(
        self, image_path: str, length: int, *, region: Optional[str] = None, offset: int = 0
    ) -> bytes:
        return self.open_spatial_frame(image_path).read_bytes(length, region=region, offset=offset)

    def storage_write_bytes(
        self,
        image_path: str,
        data: bytes,
        *,
        region: Optional[str] = None,
        offset: int = 0,
    ) -> str:
        frame = self.open_spatial_frame(image_path)
        frame.write_bytes(data, region=region, offset=offset)
        return frame.save(image_path)
