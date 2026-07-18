# ZDX Android Node

Android is a supported ZDX network node target.

## Goals

- connect mobile devices to the ZDX node network
- report device capabilities
- maintain heartbeat
- exchange verified frame manifests
- provide mobile node controls

## Planned Stack

- Kotlin
- Android SDK
- Jetpack Compose UI

## Node Modes

### Light Node

- heartbeat
- routing
- validation

### Compute Node

- approved VM workload execution
- charging/WiFi aware operation

### Sync Node

- frame download
- checksum verification

## Protocol

The Android client follows the same transport model as desktop nodes:

```
zdx_network protocol
        |
 Kotlin Android adapter
        |
 Android node app
```

## Security

Required before production:

- signed node identity
- encrypted transport
- capability limits
- verified frame execution

Developed by ZeroDriveX LLC.
