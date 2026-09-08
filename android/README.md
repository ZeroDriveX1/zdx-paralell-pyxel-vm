# ZDX Android node

The APK is a private, opt-in mesh node. It persists a Keystore-backed node
identity, discovers device capabilities, registers over authenticated TLS,
polls for leases, receives task artifacts in resumable chunks, verifies the
final SHA-256, and reports bounded probe results or an explicit fail-closed
unsupported-execution result.

Compute is disabled by default. Idle-only, charging-required, free-memory
admission, foreground-service controls, and immediate transfer yield protect
the device. Android device workloads always win.

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
4. Keep **Enable compute**, **Only run while device is idle**, and **Require
   charging** enabled until the device has been reviewed.
5. Grant notification permission and press **Start background service**.
6. Use the persistent notification and status text to confirm connection or
   diagnose an explicit policy/TLS/enrollment error.

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
