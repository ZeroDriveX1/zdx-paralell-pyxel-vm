# ZDX Android node

The APK is a private, opt-in mesh node. It persists a Keystore-backed node
identity, discovers device capabilities, registers over authenticated TLS,
polls for leases, receives task artifacts in resumable chunks, verifies the
final SHA-256, and reports bounded probe results or an explicit fail-closed
unsupported-execution result.

Compute is disabled by default. Idle-only now means the device is not actively
interactive (screen/user activity), rather than Android Doze mode. Charging,
configured compute-memory limits, free-memory reserve, low-memory pressure,
foreground-service controls, and immediate transfer/execution yield protect
the device. Android device workloads always win.

The app includes a persistent work console. It records connection/poll activity
and task lifecycle states including incoming, queued, receiving/download
progress, running, outgoing result submission, completed, released, failed, and
transport errors. The live status area also shows the current task, last task,
last completed-work summary, policy eligibility reason, charging/idle state,
free RAM, compute memory limit, and preserved free-memory reserve.

When compute is disabled or temporarily blocked by policy, the node remains a
useful authenticated mesh participant. It enters light/sync support mode,
refreshes capabilities and policy state, sends authenticated heartbeats, and
receives queue/cluster routing status without polling for or executing compute
leases. Support mode is intentionally non-authoritative: it helps the scheduler
with fresh availability/routing information but cannot bypass local compute
policy or become a scheduler authority. Support nodes can also submit signed,
advisory rectification requests for suspicious or stale-trust enrolled peers.
Those requests only enter the trust-review queue; they cannot directly change
karma, quarantine, suspend, or revoke another node. The Android status panel
shows the aggregate pending trust-review count.

If the server marks this Android identity stale for authentication freshness,
the ordinary support heartbeat can carry a short-lived re-attestation
challenge. The app answers it automatically using the existing Android
Keystore Ed25519 private key. This is permitted in sync/light support mode
because it performs no Pyxel workload execution, artifact transfer, or compute
lease. The console records the challenge and successful proof and the status
panel shows the last lightweight re-auth time.

## Build a signed release APK

The repository does not include the Gradle wrapper, Android SDK, release
keystore, or signing private key. On a trusted build host with SDK 35 and
Gradle 8.7+:

```sh
cd android
export ZDX_ANDROID_KEYSTORE=/secure/zdx-release.jks
export ZDX_ANDROID_KEYSTORE_PASSWORD='read-from-secret-store'
export ZDX_ANDROID_KEY_ALIAS=zdx
export ZDX_ANDROID_KEY_PASSWORD='read-from-secret-store'
./gradlew clean assembleRelease
```

Do not commit those values. Validate the generated APK signature using the
organization’s Android release process.

## Install, update, and uninstall

```sh
adb devices
adb install -r app/build/outputs/apk/release/app-release.apk
adb shell am start -n com.zerodrivex.zdxnode/.MainActivity
```

An update must use the same Android signing identity. Clean uninstall:

```sh
adb shell am force-stop com.zerodrivex.zdxnode
adb uninstall com.zerodrivex.zdxnode
```

## Configure the node

In **ZDX Node**:

1. Review the stable node ID and capability display.
2. Paste the private CA certificate PEM and enter the enrolled ZDX server host
   and TLS port.
3. Enable **Join configured ZDX mesh**, save, and confirm the device public key
   is enrolled on the cluster server.
4. Configure the compute memory limit and minimum free-RAM reserve, then keep
   **Enable compute**, **Only run while device is idle**, and **Require
   charging** enabled until the device has been reviewed.
5. Grant notification permission and press **Start background service**.
6. Use **Live service status** and **Work console** to inspect current policy
   eligibility, incoming/queued/running work, transfer progress, outgoing
   results, completion/release/failure records, and the last completed task.

The service does not open ports or weaken Android permissions. It uses an
app-private checkpoint directory and Android Keystore for the Ed25519 private
key. It reconnects with a fresh task grant after a socket interruption; an
expired old grant is never reused. Corrupt or incomplete transfers are deleted
after verification failure.

## Execution boundary

The checked-in APK does not embed the Python/Pyxel VM. It therefore rejects
ordinary Pyxel tasks rather than pretending to execute them. It can run the
explicitly marked bounded artifact-integrity probe and return its verified
result. Linux/VPS workers remain the Pyxel execution path until a separately
signed Android VM adapter is added and validated on devices.
