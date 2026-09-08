# ZDX Pyxel distributed deployment

Use [USER_GUIDE.md](USER_GUIDE.md) as the complete installation and operating
manual. Use [CLEAN_LINUX_INTEGRATION_TEST.md](CLEAN_LINUX_INTEGRATION_TEST.md)
for the external two-clean-machine release gate.

## Linux/VPS worker

```sh
sudo env \
  ZDX_CA_FILE=/etc/zdx-worker/ca.pem \
  ZDX_COORDINATOR_HOST=vps-01.example \
  ZDX_COORDINATOR_PORT=8765 \
  ZDX_NODE_ID=vps-worker-02 \
  ./packaging/install.sh
sudo zdxctl status
sudo zdxctl capabilities
sudo zdxctl policy show
```

The worker service is installed and boot-enabled but compute-disabled. Enable
only after public-key enrollment:

```sh
sudo zdxctl policy enable
sudo zdxctl start
sudo zdxctl logs 100
```

## Election-aware server and gossip

Run one server per enrolled server VPS; no address is a fixed coordinator:

```sh
sudo /usr/local/bin/zdx-server \
  --node-id vps-01 --cluster-id private-prod \
  --host 0.0.0.0 --port 8765 \
  --cert-file /etc/zdx-worker/server.crt \
  --key-file /etc/zdx-worker/server.key \
  --ca-file /etc/zdx-worker/ca.pem \
  --trusted-peer vps-02=/etc/zdx-worker/peers/vps-02.pub.pem \
  --gossip-peer vps-02.example:8765
```

Gossip exchanges signed durable queue, lease, result, failure, and
contribution operations. Partition/rejoin converges by operation hash and
deterministic ordering. Karmic/gravitational election and the 100-node cap
remain unchanged.

## Android

Build/sign/install from a trusted Android host:

```sh
cd android
export ZDX_ANDROID_KEYSTORE=/secure/zdx-release.jks
export ZDX_ANDROID_KEYSTORE_PASSWORD='read-from-secret-store'
export ZDX_ANDROID_KEY_ALIAS=zdx
export ZDX_ANDROID_KEY_PASSWORD='read-from-secret-store'
./gradlew clean assembleRelease
adb install -r app/build/outputs/apk/release/app-release.apk
adb shell am start -n com.zerodrivex.zdxnode/.MainActivity
```

The APK joins, registers dynamic capabilities, polls, receives/resumes/
verifies artifacts, and returns bounded probe results. Ordinary Pyxel tasks
remain disabled until the signed adapter contract is implemented and device
validated; see [ANDROID_VM_ADAPTER_CONTRACT.md](ANDROID_VM_ADAPTER_CONTRACT.md).

## Release verification and signing

```sh
sha256sum -c release/SHA256SUMS
python3 packaging/release_manifest.py verify release/release-manifest.json
```

To create the operator-controlled chain, see [RELEASE_SIGNING.md](RELEASE_SIGNING.md).
