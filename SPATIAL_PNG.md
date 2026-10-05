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

`SpatialPixelStore` packs an agent key/value map into one PNG storage plane instead of creating one PNG per key. Values use a deterministic typed binary encoding with an in-raster header, generation counter, SHA-256 payload checksum, and canonical key ordering; JSON text is not the active spatial-memory representation. Writes are process-locked, atomically committed, and recoverable from the previous valid PNG generation.

`ZDXAgentMemory(..., spatial=True)` enables the backend while preserving the normal agent-memory API. `spatial_path`, `spatial_layout`, and `spatial_region` can bind agent memory to a named non-executable region of the same PNG that carries the program.

## Combined execution + storage

A single frame can carry executable rows and persistent data rows simultaneously. The VM executes only the configured execution plane; platform components can read and write the storage plane by precise spatial coordinates or named regions.

This preserves the defining PyxelVM invariant:

**PNG is the executable container. Raster is machine code/state. Pixels are cells. Coordinates are structure.**


## Resident agent transaction path

When ZDX Agent Memory is bound to a named storage region in the same executable frame, the runtime uses one resident transaction:

    lock frame
      -> decode PNG once
      -> validate RGB and geometry
      -> execute resident raster
      -> update non-executable memory region
      -> encode/checkpoint PNG once
    unlock

If execution or memory update raises, no checkpoint is committed. The previous PNG generation remains authoritative.

Automatic backup recovery is constrained for executable spatial frames: a backup may replace a corrupt primary only when its executable plane is byte-equivalent to the current frame. Memory recovery is never allowed to roll program rows back to older code.

Because frame identity is SHA-256 over the exact PNG bytes, every intentional same-frame state checkpoint produces a new frame hash.

## Canonical spatial v1 validation

Spatial v1 rejects implicit image conversion and ambiguous geometry:

- input container must be PNG;
- image mode must be RGB;
- width, height, execution rows, coordinates, and region dimensions must be actual integers, not booleans;
- derived raw/storage/region capacities must match declared geometry when present;
- named regions are data regions only; the execution plane is implicit;
- total raster size is bounded before execution;
- distributed tasks must declare threads equal to execution_rows;
- spatial compute admission includes a conservative decoded-raster working-set check.

## Operational boundary

Spatial mode removes per-key PNG files, JSON serialization in the active memory format, and repeated state-file commits for batch runtime updates. The current persistent backend still decodes and re-encodes the PNG at commit boundaries. Hot-loop agent IPC should therefore operate on a resident decoded raster and checkpoint to PNG transactionally; persistent PNG compression is not itself treated as the acceleration mechanism.

## Checkpoint cadence and lineage

The resident execution path does not rewrite the PNG after every VM execution. The default checkpoint interval is 10 completed executions. Generations between durable checkpoints remain volatile and may be lost after a hard crash.

Every completed execution receives a deterministic VM checkpoint marker containing generation, clock, and a state hash. When a generation is selected for durability, the checkpoint worker freezes the resident frame/state, recomputes the same state hash, and refuses the checkpoint if the hashes differ.

Non-barrier checkpoint requests coalesce to the newest pending generation. Barrier checkpoints are exact and are used before/after externally consequential operations, on explicit durability requests, and for final shutdown/mission completion.

A committed checkpoint also receives the SHA-256 of the exact PNG artifact. VM-state identity and PNG-artifact identity are deliberately separate so state lineage remains verifiable independently of PNG compression bytes.



## Agent ABI v1 and native mailbox regions

A spatial agent frame may bind named non-executable regions to semantic Agent ABI v1 roles. The ABI is stored with persistent agent state and includes a SHA-256 of the canonical `SpatialLayout`, preventing silent region rebinding across restart or migration.

Current semantic roles are:

- persistent memory;
- working memory;
- inbound mailbox;
- outbound mailbox;
- capabilities;
- provenance.

Inbound/outbound mailboxes use a bounded binary ring format inside their declared regions. Each message is sequence-bound and integrity-checked; queue overflow is explicit rather than spilling into adjacent raster state. Mailbox regions must be distinct from the persistent-memory region.

`SpatialAgentSession` owns the resident frame containing those regions. A clean flush/close forces dirty resident state durable even if the normal 10-execution checkpoint interval has not been reached. `SpatialFrame.dirty_rectangles` records resident cell/byte mutation regions as bounded half-open rectangles; this is tracking metadata only and does not imply in-place PNG IDAT patching.
