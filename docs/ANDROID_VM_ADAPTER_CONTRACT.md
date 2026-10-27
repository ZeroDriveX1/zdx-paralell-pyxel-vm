# Android Pyxel execution contract

APK 1.1.1 (versionCode 3) includes `zdx.pyxel.android`, protocol 1. It executes
real static RGB PNG programs with the desktop VM's default instruction mapping.
It is a Kotlin interpreter, not a Python runtime or a Linux/Windows emulator.
The existing integrity probe remains available for explicitly requested probes.

## Negotiated workload

A task must be artifact-backed and explicitly include metadata:

```json
{
  "android_vm_adapter_id": "zdx.pyxel.android",
  "artifact_type": "pyxel-frame-v1",
  "execution_model": "pixel-frame",
  "required_vm_features": ["pyxel-static-rgb-v1"],
  "required_android_capabilities": ["pyxel-static-rgb-v1", "isolated-process"]
}
```

The coordinator matches platform, adapter protocol and compiled manifest digest,
requested capabilities, memory and runtime. It gives a phone at most one lease.
Ordinary desktop jobs are not implicitly mobile-compatible. An explicitly
Android-targeted job is not silently redirected to a desktop worker.
The APK's local registry independently checks the request. The task's existing
thread, memory and runtime fields still apply; use memory_mb >= 64.

Supported instructions: NOP, SET_A/SET_REG, SET_B, NOT, ADD, SUB, MUL, DIV,
COPY_OUT, IF_A_EQ, CMP, LOAD_MEM, STORE_MEM, VERIFY_FREQ, JMP and HALT.
Registers and eight shared slots use the same byte arithmetic, column-major
row ordering, skip consumption and first-JMP semantics as the desktop VM.
The interpreter executes rows sequentially as the desktop reference does;
this does not advertise GPU/NPU acceleration.

Unsupported: SAVE_STATE/frame chaining, custom opcode maps, spatial-png resident
state, persistent VM checkpoints, host tools, arbitrary Python/shell programs,
and network/filesystem instructions. Unsupported opcodes fail even in
unreachable columns. Policy interruption releases the lease for a restart from
the beginning; it does not claim checkpoint/resume support.

## PNG and resource limits

- At most 8 MiB of compressed input, 1,048,576 pixels, 4096 pixels per dimension,
  32 executable rows, 4096 column steps and 300 seconds.
- Only non-interlaced RGB8 PNG with IHDR, contiguous IDAT and IEND is accepted.
  All CRCs and lengths are checked before native decoding. APNG, palettes,
  profiles, EXIF, text, unknown chunks and trailing data are rejected.
- The decoder is Android's OS-provided BitmapFactory/Skia stack, not Pillow.
  Keep the device's Android security updates current. APK upgrades do not
  replace the OS decoder.
- A conservative compressed/raster working-set estimate must fit the task's
  reservation, itself capped by operator policy and the adapter's 512 MiB cap.
  This estimate is not a hard per-process OS memory quota; Android may still
  kill the isolated process on memory pressure. Such a death fails the task.
- Charging, screen-inactive/idle policy, free-memory reserve and service-stop
  admission remain enabled. The host monitors cancellation every 100 ms while
  waiting. Parsing and instructions also check a monotonic deadline.

## Isolation, artifact and adapter trust

The host opens a read-only descriptor to a verified app-private artifact. The
bound service runs with `isolatedProcess=true`, is not exported, has no app
permissions and cannot read host identity keys or open network connections.
It independently bounds reads, hashes the exact bytes, checks the full PNG
container, decodes and executes. Only a bounded result crosses back to the host.
Unbinding after completion, cancellation or timeout kills that dedicated
process, including native decode. The host retains all transport and signing.

This adapter is compiled into the operator-installed APK: trust comes from APK
signing and a fixed manifest descriptor digest shared with the coordinator.
The advertised signature label `apk-builtin` is **not** a detached signature or
remote-code trust mechanism. There is no dynamic adapter installation path.
Future separately distributed adapters require their own release-key verifier.
The integrity probe retains its existing platform-builtin identity.

## Result acceptance

The host node signs the result claim with its enrolled Ed25519 identity and
signs the enclosing protocol message. The server checks both existing admission
and claim signatures, task/worker lease ownership, requested adapter/protocol,
compiled manifest digest and input artifact digest. The inner signed result
must contain `lease_id`, equal to the running coordinator lease; the outer
completion/release/failure message must also provide the current lease token.
Registration capability changes cannot downgrade an already-leased task's
attestation requirement. Legacy state records lacking lease IDs have a
separate compatibility path and are not eligible for Android result attestation.
A signature proves attribution, not that a
worker computed an honest answer; independent result verification/redundancy
remains a coordinator policy concern.

The result includes task and artifact identity, adapter identity, registers,
shared memory, clock/column steps, elapsed milliseconds and completed work.
A phone does not self-enroll: operators must configure TLS and enroll its key.
The existing Keystore Ed25519 provider must be supported by the device;
unsupported providers fail closed rather than falling back to an unsigned node.

## Validation and rollout

Run JVM tests, Android lint/build and connected instrumentation:

```sh
gradle -p android :app:testDebugUnitTest :app:assembleDebug :app:lintDebug
gradle -p android :app:connectedDebugAndroidTest
pytest -q tests/test_android_compute.py
```

Shared golden vectors compare the Kotlin interpreter with the current Python
reference. Tests cover PNG admission, loop/cancellation limits, task negotiation
and signed result binding. Instrumentation decodes a desktop-compiled PNG and
executes it through the isolated service, checking result bytes and isolated UID.
Release validation runs this on an API 35 emulator. Physical-device charging,
screen transitions, thermal/memory pressure, native decode interruption, signing
provider support and a real enrolled-coordinator round trip still require device
validation before fleet rollout. Install the new APK explicitly; merging source
cannot update an already-installed app.
