# ZeroDriveX Operations Guide

## Health and recovery

Monitor process uptime, active sessions/nodes, commit failures and latency, recovery operations, migrations, lock contention, scheduler latency, memory, CPU, and file descriptors. `zdx_validation.py` exports bounded JSON metrics and benchmarks; it does not replace fleet monitoring.

Workers reconnect with bounded exponential backoff and perform fresh mutual TLS and application authentication. Coordinator restart invalidates live sessions. Stale registry entries are removed by authenticated lifecycle cleanup. There is no automatic coordinator discovery or failover.

## Certificate rotation

Issue the replacement certificate with the same verified hostname and intended EKU. Update certificate/key files atomically, then call the server reload facility or restart the service; existing TLS connections retain their old context and new connections use the replacement. Refresh the CRL and restart/reload before relying on a revocation. Always test the new path from a worker before retiring the old certificate.

## Backup and restore

Stop writers or take a filesystem snapshot that preserves each state file with its `.bak` generation. Copy keys separately with restrictive permissions. Never copy transient `.tmp` files as authoritative state. To restore, stop the service, preserve the damaged directory for forensics, restore the complete snapshot, verify owner/mode and checksums by loading state, then start the coordinator and require every worker to authenticate again. Sessions are audit metadata only and are never resumed after restart.

Corrupt primary state is quarantined and a verified backup is restored when available. If neither generation verifies, keep the service stopped and restore a known-good backup; do not edit checksums manually.

## Incident checklist

Capture logs and JSON metrics, isolate the affected endpoint, preserve quarantined files, revoke exposed certificates and application keys, restore verified state, and re-enroll peers explicitly. Treat repeated authentication failures, nonce/sequence rejection, checksum failures, and recovery loops as security events.

## Ollama mission operations

Ollama defaults to `http://localhost:11434` with `qwen2.5:1.5b`. Configure only through `ZDX_OLLAMA_ENDPOINT`, `ZDX_OLLAMA_MODEL`, `ZDX_OLLAMA_TIMEOUT_SECONDS`, `ZDX_OLLAMA_MAX_RETRIES`, `ZDX_OLLAMA_BACKOFF_SECONDS`, `ZDX_OLLAMA_JITTER_SECONDS`, and `ZDX_OLLAMA_POOL_SIZE`, `ZDX_OLLAMA_NUM_PREDICT`, `ZDX_OLLAMA_TEMPERATURE`, and `ZDX_OLLAMA_MAX_RESPONSE_BYTES`. Mission controls use `ZDX_MISSION_HISTORY`, `ZDX_REFLECTION_ENABLED`, `ZDX_REFLECTION_MAX_CYCLES` (0–2), and `ZDX_LOG_LEVEL`.

Keep Ollama bound to loopback where possible. If a remote endpoint is necessary, use HTTPS and network access controls; the provider uses standard platform certificate verification but does not add application authentication to Ollama. Never place API tokens, private keys, credentials, or unredacted sensitive inputs in missions because prompts and final responses are deliberately persisted. Metrics contain counts and timings, not prompts or responses.

Monitor `provider_latency`, `prompt_latency`, `generation_latency`, `total_mission_latency`, `mission_duration`, `model_failures`, `model_retries`, `reflection_count`, `successful_missions`, and `failed_missions`. Repeated retry or failure growth usually indicates an unavailable service, model eviction/loading pressure, or timeout below observed generation latency. Reflection can multiply provider calls by up to five per mission: generation plus two critique/revision pairs.

### Termux runtime note

An Ollama model appearing in `ollama list` does not prove the native runner is installed. Validate with `ollama run qwen2.5:1.5b "Reply with ready"` before enabling missions. On the Pass 15 ARM64 Termux host, Ollama 0.32.3 reported `llama-server binary not found`; application requests consequently returned HTTP 500 and were retried and persisted as structured failures. Install a Termux-compatible CPU `llama-server` build before expecting live inference. The generic Linux bundle includes large platform accelerator assets and was not accepted as a validated Termux solution.
