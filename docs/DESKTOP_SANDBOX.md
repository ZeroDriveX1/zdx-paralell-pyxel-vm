# Disposable desktop workload executor (Linux)

The network worker holds enrollment keys and MUST NOT execute remote PNG
workloads in-process. The dedicated `zdx_executor_sandbox` runner is the only
supported path for production desktop worker compute. It **fails closed**:
without Linux, nonroot Podman, an immutable locally available image, or
enforceable per-container limits it does not poll or claim new tasks.

## Provisioning (not deployed by this repository)

Build the reduced source-only execution image under a nonroot worker identity:

```sh
podman build --pull=always -f packaging/Dockerfile.worker-sandbox -t localhost/zdx-pyxel-executor:local .
podman image inspect localhost/zdx-pyxel-executor:local --format '{{.Id}}'
```

Set `ZDX_SANDBOX_IMAGE=sha256:<full local image ID>` in the protected
`/etc/zdx-worker/worker.env` file, not a mutable tag. Worker restarts and
runtime operations use `--pull=never`. For CI or air-gapped images,
build and transfer a reviewed OCI artifact and verify its digest separately.
Use cgroup v2 rootless Podman and enable the memory, PID, and CPU controllers
for the `zdx-worker` service account. Failure to apply a limit aborts the
container, never falls back to in-process VM execution.

The executor sees only two read-only bind mounts: one size/hash-verified
input PNG and one bounded metadata JSON file. It has no host network,
host path, coordinator state, enrollment keys, device access, ambient
capabilities, writable image root, or user-provided Python/shell code.
It returns one bounded JSON object. The coordinator still independently
authenticates result envelopes and lease tokens.

Per task: enforce `--memory`, `--memory-swap`, `--pids-limit`, CPU
quota, RLIMIT_CPU, RLIMIT_NOFILE, and host-side monotonic deadline;
on timeout or shutdown force removal of the generated Podman container.
Host stdout and stderr use monitored temporary files instead of unbounded
in-memory pipes. Results exceeding the protocol limit fail closed.

**Pre-mortem / integration risks:**
- Rootless user namespaces/cgroup delegation unavailable: no tasks run.
  Confirm with a real memory-limit enforcement smoke test on target host.
- 8 MiB compressed PNG may expand significantly: enforce RAM cgroups and
  Pillow decompression limits in the disposable worker; OS OOM kills may
  yield failed/retriable tasks depending on the coordinator's retry policy.
- Pulling/updating a mutable image can change executed code: pin a reviewed
  local image ID and do not auto-pull at runtime.
- The worker runs at most one task synchronously, but very short lease
  durations can expire before startup/execution completes. Late results are
  rejected; renewal would require a separately authenticated bounded
  protocol extension, not relaxed expiry checks.
- Rootless OCI isolation is not a defense against kernel vulnerabilities;
  patch kernel/Podman/runc or crun and perform independent adversarial review.

No existing deployment is assumed. This image is not a generic AI code
execution environment, and is not a replacement for production container
host hardening.
