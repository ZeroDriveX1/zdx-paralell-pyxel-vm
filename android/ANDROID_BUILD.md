# Android build and release

The Gradle app uses package `com.zerodrivex.zdxnode`, min SDK 26, target SDK
35, and a foreground data-sync service. Build a signed release only on a
trusted Android build host:

```sh
cd android
export ZDX_ANDROID_KEYSTORE=/secure/zdx-release.jks
export ZDX_ANDROID_KEYSTORE_PASSWORD='from-secret-store'
export ZDX_ANDROID_KEY_ALIAS=zdx
export ZDX_ANDROID_KEY_PASSWORD='from-secret-store'
./gradlew clean assembleRelease
adb install -r app/build/outputs/apk/release/app-release.apk
```

The service is `START_STICKY` only after an explicit user start. A persisted
stop request makes any system restart fail closed, and no connection or task
admission occurs unless mesh settings enable it. The UI supports enable/disable,
screen-inactive idle-only operation, charging requirements, compute-memory
limit, minimum free-memory reserve, and service start/stop. Admission is checked
before polling, continuously during artifact transfer/execution, and immediately
before adapter execution. Android low-memory pressure also rejects work.

The app persists a bounded 250-event work console and compact current/last task
state. Events include incoming, queued, transfer progress, running, outgoing,
completed, released, failed, and transport/error transitions. The service
rechecks admission every 15 seconds while connected and exponentially backs off
failed connections up to 15 minutes.

No release keystore, wrapper binary, APK, or private key is checked in. A
build host must supply the SDK, Gradle wrapper, keystore, installation device,
and runtime validation.
