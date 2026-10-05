# ZDX Spatial PNG Machine Model

## Core rule

A spatial frame remains a standards-valid RGB PNG. The raster is the machine representation.

- **X/Y** are implicit location, address, and execution topology.
- **R** is the opcode dispatch byte inside executable rows.
- **G/B** are operand/immediate fields.
- Rows below the executable plane are addressable storage cells.

There is no PNG-to-Python or PNG-to-secondary-bytecode translation step. PNG decompression reconstructs the raster; PyxelVM then consumes pixel cells directly.

## ISA compatibility

Spatial execution does **not** add or renumber opcodes. The existing 16-opcode PyxelVM ISA remains unchanged.

The spatial layer changes geometry, capacity, and addressing rather than the instruction set.

## Default 256x256 layout

With 8 executable rows:

- total cells: `256 * 256 = 65,536`
- raw raster capacity: `65,536 * 3 = 196,608 bytes`
- executable cells: `256 * 8 = 2,048`
- storage cells: `256 * 248 = 63,488`
- storage bytes: `63,488 * 3 = 190,464 bytes`

PNG compression may reduce the on-disk representation substantially when the spatial raster contains repeated or low-entropy structure. Logical capacity and compressed file size are intentionally separate concepts.

## Execution safety boundary

`SpatialPyxelVM` requires `threads == execution_rows`.

This is important: arbitrary storage pixels may contain R values that happen to equal valid opcodes. They remain data because rows outside the executable plane are never scheduled as VM threads.

## Exact addressing

Every pixel has a deterministic row-major cell address:

`address = y * width + x`

And the inverse:

- `x = address % width`
- `y = address // width`

A cell can therefore be identified by either exact `(x, y)` coordinates or one absolute spatial address without storing additional location metadata in the raster.

## Spatial compiler

`SpatialCompiler` emits one PNG containing both:

1. executable thread rows at the top of the raster; and
2. optional named storage regions below those rows.

The compiler writes the same RGB opcode cells used by `SimpleCompiler`; it does not create an intermediate runtime format.

## Spatial memory

`SpatialPixelStore` packs an agent key/value dictionary into one PNG storage plane instead of creating one PNG per key. It implements the same high-level read/write/delete/keys/all interface as `PixelStore`.

`ZDXAgentMemory(..., spatial=True)` enables the backend while preserving the normal agent-memory API.

## Combined execution + storage

A single frame can carry executable rows and persistent data rows simultaneously. The VM executes only the configured execution plane; platform components can read and write the storage plane by precise spatial coordinates or named regions.

This preserves the defining PyxelVM invariant:

**PNG is the executable container. Raster is machine code/state. Pixels are cells. Coordinates are structure.**
