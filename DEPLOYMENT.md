# ZeroDriveX Deployment Guide

## RC2 validation status

Pass 14 produced TLS-required coordinator and worker entrypoints plus packaging examples. The available environment contained one ARM64 Android/Termux physical host, zero virtual machines, and no Docker, systemd, Windows host, privileged network emulator, or remote peer. The executed topology was one coordinator and three isolated worker OS processes communicating over loopback TCP with mutual TLS and application-layer Ed25519 authentication. This is deployment rehearsal, not multi-machine acceptance. RC2 acceptance remains blocked pending the field run below.

## Prerequisites

Use Python 3.10 or newer, `cryptography`, a private CA, distinct coordinator and worker certificates, and Ed25519 application identities. Certificates need the correct server/client extended-key usages and coordinator DNS/IP subject alternative names. Keep private keys readable only by the service account.

## Install

```bash
python -m pip install dist/open_pyxel-1.0.0rc2.tar.gz
```

Start the coordinator:

```bash
zdx serve --port 8765 --coordinator-key /var/lib/zdx/coordinator.pem \
  --tls-ca /etc/zdx/tls/ca.pem --tls-cert /etc/zdx/tls/coordinator.pem \
  --tls-key /etc/zdx/tls/coordinator-key.pem \
  --trust WORKER_ID=/etc/zdx/trust/worker.raw
```

Start each enrolled worker:

```bash
zdx-worker --host coordinator.example --port 8765 --node-key /var/lib/zdx/worker.pem \
  --coordinator-id COORDINATOR_ID --coordinator-public-key /etc/zdx/trust/coordinator.raw \
  --tls-ca /etc/zdx/tls/ca.pem --tls-cert /etc/zdx/tls/worker.pem \
  --tls-key /etc/zdx/tls/worker-key.pem --server-hostname coordinator.example
```

TLS is fail-closed by default. Application trust is explicit key pinning; network discovery and dynamic enrollment are not implemented. `docker-compose.example.yml` and `packaging/systemd/` are templates, not validated artifacts on this host. The Windows script prints the service command because no service-wrapper dependency is bundled.

## Required field acceptance run

Use at least three machines or VMs on separate network namespaces: one coordinator and two workers. Record host/OS/filesystem versions. Run 24 hours while continuously scheduling, committing state, reconnecting workers, and collecting JSON metrics. Apply latency, loss, jitter, duplication, reordering, partitions, and clock skew using the platform network emulator. Hard-power or hypervisor-reset each role during commits and migrations. Test certificate renewal, expired and revoked certificates, coordinator/node restart, backup restore, and capacity in increasing steps until the first breached SLO. Do not promote RC2 until these results are attached to the release record.
