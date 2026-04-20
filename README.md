# open-pyxel

A pixel-native parallel virtual machine. Programs are PNG images; the VM
executes their pixels. Rows are threads, columns are time steps, and each
pixel is a single instruction with up to two operand bytes.

This is the open-source VM. It ships with the default opcode MAP.

> For the per-customer opcode-remapping + AES-GCM encrypted MAP distribution,
> see the commercial fork.

---

## Install

```bash
pip install pillow numpy
git clone https://github.com/zerodrivex/open-pyxel
cd open-pyxel
```

Run tests:

```bash
python -m pytest test_vm.py test_registry.py -v
```

Build native extensions (optional — pure Python works fine too):

```bash
pip install cython
python setup.py build_ext --inplace
```

---

## 60-second example

```python
from zdx_parallel_vm import SimpleCompiler, ParallelPyxelVM

program = [
    ["SET_A 10", "SET_B 5", "ADD", "COPY_OUT", "STORE_MEM 0", "HALT"],
]

SimpleCompiler().compile(program, "demo.png")

vm = ParallelPyxelVM(threads=1)
vm.execute_texture("demo.png")
vm.dump_state()
# T0: {'A': 15, 'B': 5, 'OUT': 15, 'T': 5}
# M0: 15
```

---

## Instruction set (16 opcodes)

| Instruction   | What it does                                    |
| ------------- | ----------------------------------------------- |
| `SET_A n`     | `A = n`                                         |
| `SET_B n`     | `B = n`                                         |
| `ADD`         | `OUT = (A + B) & 0xFF`                          |
| `SUB`         | `A = (A - B) & 0xFF`                            |
| `MUL`         | `A = (A * B) & 0xFF`                            |
| `DIV`         | `A = A // B` (0 if `B == 0`)                    |
| `NOT`         | `A = (~A) & 0xFF`                               |
| `CMP`         | `OUT = 1 if A == B else 0`                      |
| `COPY_OUT`    | `A = OUT` (lift `ADD` / `CMP` result into A)    |
| `IF_A_EQ n`   | Skip next instruction if `A != n`               |
| `JMP n`       | Jump to column `n`                              |
| `LOAD_MEM n`  | `A = shared[Mn]`                                |
| `STORE_MEM n` | `shared[Mn] = A` (not OUT — always A)           |
| `SAVE_STATE i`| Chain to frame registered at index `i`          |
| `VERIFY_FREQ` | Anti-tamper pixel-mirror check                  |
| `HALT`        | Stop this thread                                |

---

## CLI

```bash
# Compile JSON program to PNG
python zdx_parallel_vm.py --mode compile_program \
    --program '[["SET_A 10","SET_B 5","ADD","HALT"]]' \
    --output prog.png

# Execute
python zdx_parallel_vm.py --mode execute --input prog.png --threads 1

# Debug (per-instruction register diff)
python zdx_parallel_vm.py --mode execute --input prog.png --threads 1 --debug
```

---

## Agent memory

Store Python values as PNGs — no plain text on disk:

```python
from zdx_pixel_memory import ZDXAgentMemory

mem = ZDXAgentMemory(agent_id="my_agent")
mem.remember("config", {"model": "zdx-v1", "temp": 0.7})
print(mem.recall("config"))
```

Files live in `zdx_memory/my_agent/*.px.png`.

---

## License

MIT — see [LICENSE](LICENSE).
