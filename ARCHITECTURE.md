# ZDX Parallel Pyxel VM Architecture

## Overview

ZDX Parallel Pyxel VM is a deterministic pixel-based virtual machine with a distributed node coordination layer.

The architecture separates:

- VM execution
- networking
- node identity
- capability discovery
- scheduling
- synchronization

## Current Components

### VM Layer

Responsible for deterministic execution of Pyxel VM programs.

### Protocol Layer

Provides:

- message envelopes
- protocol versions
- compatibility checks
- timestamps

### Identity Layer

Provides persistent node identity across restarts.

### Capability Layer

Reports available resources:

- operating system
- architecture
- CPU capacity
- GPU/NPU capability flags
- supported features

### Discovery Layer

Tracks:

- node announcements
- heartbeats
- stale nodes

### Scheduler Layer

Provides capability-aware node selection.

## Design Principles

- deterministic execution
- explicit capability reporting
- local execution boundaries
- verifiable synchronization
- modular expansion

## Roadmap

- secure enrollment
- Android node transport
- distributed testing harness
- workload manifests
- production scheduling
