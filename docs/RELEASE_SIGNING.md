# Production release signing

Release signing is operator-controlled. No Android keystore, Ed25519 release
private key, CI secret, or signing password belongs in this repository or CI
logs.

## Authoritative inventory and checksum/manifest chain

`release/RELEASE_FILES.txt` is the authoritative source/runtime package
inventory. `packaging/sign_release.sh` refuses missing inventory files, refuses
a private key located inside the release root, regenerates `release/SHA256SUMS`,
checks every checksum, creates the Ed25519 manifest, verifies it, and checks
that manifest/checksum coverage exactly matches the inventory. It never embeds
the private signing key.

On a trusted release host:

```sh
export ZDX_RELEASE_VERSION=1.0.0
export ZDX_RELEASE_SIGNING_KEY=/secure/zdx-release-ed25519.pem
export ZDX_RELEASE_MANIFEST=$PWD/release/release-manifest.json
./packaging/sign_release.sh
```

If an APK has been built and signed, include it in the same chain:

```sh
export ZDX_ANDROID_APK=$PWD/android/app/build/outputs/apk/release/app-release.apk
./packaging/sign_release.sh
```

The APK must already be signed with the operator-controlled Android keystore.
The script hashes it and adds it to the signed manifest inventory. Verify from
a separate release-consumer host:

```sh
sha256sum -c release/SHA256SUMS
python3 packaging/release_manifest.py verify release/release-manifest.json
```

Trust the manifest public key out of band; a valid signature from an untrusted
key is not a trusted release. Do not place the manifest or private key on an
automated download server; distribute only the approved public manifest,
checksums, signatures, and release artifacts.

## Android APK signing

Use a separate operator-controlled Android keystore:

```sh
cd android
export ZDX_ANDROID_KEYSTORE=/secure/zdx-release.jks
export ZDX_ANDROID_KEYSTORE_PASSWORD='read-from-secret-store'
export ZDX_ANDROID_KEY_ALIAS=zdx
export ZDX_ANDROID_KEY_PASSWORD='read-from-secret-store'
./gradlew clean assembleRelease
```

Do not commit these values or store them in Gradle properties. Verify the APK
with the organization’s Android signing toolchain and use the same key for
updates.

## Key handling

- keep release and Android private keys on an approved signing host or secret
  manager;
- use separate keys for source manifests and Android APKs;
- restrict key file permissions and destroy temporary copies;
- disable shell tracing and redact CI output;
- publish only public keys, checksums, signatures, and signed artifacts;
- rotate/revoke keys through release governance.
