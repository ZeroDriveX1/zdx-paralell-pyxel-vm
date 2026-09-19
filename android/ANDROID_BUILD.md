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
stop request makes any system restart fail closed, and no connection or task admission
occurs unless mesh settings enable it. The UI supports enable/disable, idle-only, charging, and
service start/stop; capability status includes node identity, CPU, total/free
RAM, and charging state. Android OS restrictions may prevent exact idle/screen
signals on some versions, so the service rechecks admission every 15 seconds while connected and exponentially backs
off failed connections up to 15 minutes.

No release keystore, wrapper binary, APK, or private key is checked in. A
build host must supply the SDK, Gradle wrapper, keystore, installation device,
and runtime validation.
