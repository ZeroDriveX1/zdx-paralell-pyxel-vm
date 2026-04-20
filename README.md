🔷 README / Open Source Guide (Top Description Included)
Markdown
# open-pyxel

A pixel-native parallel virtual machine where programs are PNG images.

In open-pyxel, computation is encoded visually:
- rows = threads
- columns = time steps
- each pixel = one instruction

The VM executes images deterministically using a fixed opcode mapping.

This repository contains the **fully open-source core VM**, including:
- compiler (text → PNG)
- execution engine (PNG → state)
- shared memory model
- pixel-based agent memory system

---

## What This Is

open-pyxel is:

- a **deterministic parallel VM**
- a **visual computation model**
- a **portable execution format (PNG)**
- a **minimal instruction system (16 opcodes)**

It is designed for:
- experimentation with alternative compute models
- deterministic agent execution
- parallel logic simulation
- novel program encoding systems

---

## What This Is NOT

This repository does **not include**:

- encrypted opcode maps
- per-customer opcode remapping
- extended frame chaining limits
- commercial distribution tooling

Those exist in the ZerodriveX commercial implementation.

---

## Install

```bash
pip install pillow numpy
git clone https://github.com/zerodrivex/open-pyxel
cd open-pyxel
Optional (native speedups):
Bash
pip install cython
python setup.py build_ext --inplace
Quick Example (60 seconds)
Python
from zdx_parallel_vm import SimpleCompiler, ParallelPyxelVM

program = [
    ["SET_A 10", "SET_B 5", "ADD", "COPY_OUT", "STORE_MEM 0", "HALT"],
]

SimpleCompiler().compile(program, "demo.png")

vm = ParallelPyxelVM(threads=1)
vm.execute_texture("demo.png")
vm.dump_state()
Expected output:
Plain text
T0: {'A': 15, 'B': 5, 'OUT': 15, 'T': 5}
M0: 15
Execution Model
Execution happens column-first:
Plain text
for each column:
    for each thread:
        execute instruction
Thread order is deterministic:
Plain text
T0 → T1 → T2 → ...
Meaning:
earlier threads can write state
later threads see it immediately in the same step
Instruction Set (16 Opcodes)
Instruction
Description
SET_A n
A = n
SET_B n
B = n
ADD
OUT = A + B
SUB
A = A - B
MUL
A = A * B
DIV
A = A // B
NOT
bitwise NOT
CMP
OUT = (A == B)
COPY_OUT
A = OUT
IF_A_EQ n
skip next instruction if A != n
JMP n
jump to column
LOAD_MEM n
read shared memory
STORE_MEM n
write shared memory
VERIFY_FREQ
pixel mirror check
SAVE_STATE
frame chaining (limited)
HALT
stop thread
Critical Rules
1. STORE_MEM writes A, not OUT
Plain text
ADD
COPY_OUT
STORE_MEM 0
2. IF_A_EQ skips on mismatch
Plain text
IF_A_EQ 5
Means:
if A == 5 → continue
else → skip next instruction
3. CMP compares A to B
Shared Memory
Global slots:
Plain text
M0 – M7
all threads can read/write
values are integers only
CLI Usage
Compile:
Bash
python zdx_parallel_vm.py --mode compile_program \
  --program '[["SET_A 10","SET_B 5","ADD","HALT"]]' \
  --output prog.png
Execute:
Bash
python zdx_parallel_vm.py --mode execute --input prog.png --threads 1
Debug:
Bash
python zdx_parallel_vm.py --mode execute --input prog.png --threads 1 --debug
Agent Memory (PNG-based storage)
Python
from zdx_pixel_memory import ZDXAgentMemory

mem = ZDXAgentMemory(agent_id="test")

mem.remember("config", {"model": "px", "temp": 0.7})
print(mem.recall("config"))
No plaintext files — everything is stored as PNGs.
Limits (Open Version)
Feature
Limit
frame chaining
limited (~10)
opcode mapping
fixed
encryption
not included
VM tuning
static
Tests
Bash
python -m pytest test_vm.py test_registry.py -v
Why PNG?
portable across systems
inspectable
deterministic binary structure
allows visual + computational duality
License
MIT
