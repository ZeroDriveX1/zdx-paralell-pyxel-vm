# Android build and reproducibility evidence

Captured: 2026-09-08 UTC

## Provenance

- HEAD commit: `1774487bebdeb8a19963350671f3950fc615fa26`
- Worktree: **NOT CLEAN**. After removing generated Android cache/build
  directories, 11 modified/untracked entries remained. No reset, stash, or
  commit was performed because those changes belong to the user.
- Toolchain details: [ANDROID_TOOLCHAIN.txt](ANDROID_TOOLCHAIN.txt)

## Debug build

Two clean builds were run after clearing the project Android Gradle/Kotlin
state and using a fresh `GRADLE_USER_HOME`.

```text
build A SHA-256: d17edaff92415e6769eb87c6681f511ed1386d05232f2a314dd3a645bc8189a1
build B SHA-256: 567982606e2e046a18a7720e384f22e0482b6d913433c35bb13bdaaea045a59c
```

After extraction, normalizing ZIP metadata and sorting entry names, exactly
one entry differed: `classes4.dex`. `AndroidManifest.xml`, `resources.arsc`,
Gradle metadata, `classes.dex`, `classes2.dex`, and `classes3.dex` were
byte-identical. `classes4.dex` contains Android adapter classes and Kotlin
stdlib classes.

`dexdump -d` structural output for both `classes4.dex` files was identical
after removing only the input filename header. Class definitions, methods,
instructions, constants, and resources therefore compare semantically equal
at the available DEX-tooling level. The remaining difference is a
non-semantic DEX byte/layout/debug-metadata variation; the APKs are not
byte-for-byte reproducible.

Final debug evidence artifact:

```text
release/zdx-node-1.0.0-debug.apk
SHA-256: 9112e8f273187a7e23d1dc16fc20664da944f79706a5eda20567cc1461fa6151
```

APK inspection confirmed package `com.zerodrivex.zdxnode`, min SDK 26,
target/compile SDK 35, the declared foreground-service/data-sync/notification
permissions, no native libraries or assets, and packaged Android mesh and
integrity-probe adapter classes. The project does not contain a Pyxel VM
adapter; its source and documentation intentionally fail closed for ordinary
Pyxel tasks.

## Lease and retry safety

The supplied six-check lease probe passed: unique lease IDs are issued at
claim time, stale leases are rejected after requeue/reclaim, explicit release
and lease expiry retire poison tasks at `max_attempts`, and legacy running
records without lease IDs remain compatible. The default attempt cap is 5;
tests override it where needed. Lease IDs are propagated through Python and
Android completion, release, and failure messages. The replicated cluster ledger also rejects stale terminal events. Permanent
coverage is in `test_compute_leases.py`.

## UI and background status

The control surface was rebuilt with live capability and service status refresh, explicit idle-only and charging policy controls, mesh distribution settings, and a “Dismiss and run in background” action. The foreground notification is ongoing, expandable, and tappable to reopen settings. When the endpoint is incomplete it reports `mesh not configured`; connection failures are reported as `mesh unavailable: ...` (with the socket error detail).

## Python regression

The isolated startup test passed on rerun, followed by the full suite:

```text
127 passed in 4.53s
```

## Release status

The user explicitly authorized signing the current dirty source snapshot.
Signed release APK:

```text
release/zdx-node-1.0.0-release.apk
SHA-256: bb532556403cbcc69d9f55be6e0d2134d13b468d42daa327bff365ba2d91a799
copied to: /home/zdxadmin/zdx-node-1.0.0-release.apk
```

`zipalign -c -v 4` passed. `apksigner verify --verbose --print-certs`
passed using APK Signature Scheme v2. Certificate SHA-256:
`7675d71be0d2f694ae9ef43381792bc237ec8a40287f68cbe9452b3241910ca6`.

The protected production keystore remains outside the repository at
`/home/zdx/signing/zdx-release.jks`; no password or private key is recorded in
this repository.
