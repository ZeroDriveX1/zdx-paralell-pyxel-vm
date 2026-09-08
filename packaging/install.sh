#!/usr/bin/env bash
set -euo pipefail
PREFIX=${ZDX_PREFIX:-/opt/zdx}; STATE=${ZDX_STATE_DIR:-/var/lib/zdx-worker}; CONFIG=${ZDX_CONFIG_DIR:-/etc/zdx-worker}
NODE_ID=${ZDX_NODE_ID:-$(hostname -s)-zdx}; COORDINATOR_HOST=${ZDX_COORDINATOR_HOST:-127.0.0.1}; COORDINATOR_PORT=${ZDX_COORDINATOR_PORT:-8765}; CA_FILE=${ZDX_CA_FILE:-}
ROOT=$(cd "$(dirname "$0")/.." && pwd); DROPIN=/etc/systemd/system/zdx-worker.service.d
[[ ${EUID} -eq 0 ]] || { echo "Run as root: sudo $0" >&2; exit 1; }
[[ -f "$CA_FILE" ]] || { echo "Set ZDX_CA_FILE to the trusted CA certificate before install" >&2; exit 1; }
[[ -f "$ROOT/zdx_worker.py" && -d "$ROOT/zdx_node" ]] || { echo "installer must be run from a ZDX release tree" >&2; exit 1; }
install -d -m 0755 "$PREFIX" "$CONFIG"; install -d -m 0700 "$STATE" "$STATE/.zdx"
install -m 0644 "$CA_FILE" "$CONFIG/ca.cert.pem"; CA_FILE="$CONFIG/ca.cert.pem"
for file in "$ROOT"/zdx_*.py; do install -m 0644 "$file" "$PREFIX/$(basename "$file")"; done
install -d -m 0755 "$PREFIX/zdx_node"; for file in "$ROOT"/zdx_node/*.py; do install -m 0644 "$file" "$PREFIX/zdx_node/"; done
install -m 0755 "$ROOT/packaging/zdx-server" /usr/local/bin/zdx-server
install -m 0644 "$ROOT/packaging/systemd/zdx-worker.service" /etc/systemd/system/zdx-worker.service
id zdx-worker >/dev/null 2>&1 || useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin zdx-worker
chown -R zdx-worker:zdx-worker "$STATE"; chmod 0700 "$STATE"
cat > "$CONFIG/worker.env" <<EOF
ZDX_NODE_ID=$NODE_ID
ZDX_COORDINATOR_HOST=$COORDINATOR_HOST
ZDX_COORDINATOR_PORT=$COORDINATOR_PORT
ZDX_CA_FILE=$CA_FILE
EOF
chmod 0600 "$CONFIG/worker.env"
if [[ ! -f "$STATE/resource_policy.json" ]]; then
  /usr/bin/python3 "$ROOT/zdx_capabilities.py" --node-id "$NODE_ID" --output "$STATE/capability_profile.json" >/dev/null
  /usr/bin/python3 - "$STATE/capability_profile.json" "$STATE/resource_policy.json" <<'PY'
import json, sys
profile = json.load(open(sys.argv[1], encoding="utf-8"))
policy = {"enabled": False, "idle_only": True, "idle_cpu_percent": 20.0,
          "memory_limit_mb": profile["recommended_memory_mb"],
          "min_free_memory_mb": profile["recommended_min_free_memory_mb"],
          "max_concurrent_tasks": profile["recommended_max_concurrent_tasks"],
          "require_charging": False, "production_guard_file": "/var/lib/zdx-worker/production_busy",
          "protected_processes": ["uvicorn", "gunicorn", "nginx"], "nice_level": 10,
          "poll_interval_seconds": 10.0}
with open(sys.argv[2], "w", encoding="utf-8") as stream:
    json.dump(policy, stream, indent=2, sort_keys=True); stream.write("\n")
PY
  chown zdx-worker:zdx-worker "$STATE/resource_policy.json" "$STATE/capability_profile.json"; chmod 0600 "$STATE/resource_policy.json" "$STATE/capability_profile.json"
elif [[ ! -f "$STATE/capability_profile.json" ]]; then
  /usr/bin/python3 "$ROOT/zdx_capabilities.py" --node-id "$NODE_ID" --output "$STATE/capability_profile.json" >/dev/null
  chown zdx-worker:zdx-worker "$STATE/capability_profile.json"; chmod 0600 "$STATE/capability_profile.json"
fi
read -r PROFILE_MEMORY PROFILE_CPUS PROFILE_CONCURRENCY < <(/usr/bin/python3 - "$STATE/capability_profile.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
print(int(p["recommended_memory_mb"]), max(1, int(p["cpu_count"])), max(1, int(p["recommended_max_concurrent_tasks"])))
PY
)
CPU_QUOTA=$((PROFILE_CPUS * 25)); (( CPU_QUOTA < 25 )) && CPU_QUOTA=25; (( CPU_QUOTA > 100 )) && CPU_QUOTA=100
MEMORY_MAX=$((PROFILE_MEMORY * 125 / 100)); (( MEMORY_MAX < PROFILE_MEMORY + 128 )) && MEMORY_MAX=$((PROFILE_MEMORY + 128))
install -d -m 0755 "$DROPIN"
cat > "$DROPIN/resources.conf" <<EOF
[Service]
# Generated from capability_profile.json; local application workloads retain priority.
CPUQuota=${CPU_QUOTA}%
CPUWeight=1
IOWeight=1
MemoryHigh=${PROFILE_MEMORY}M
MemoryMax=${MEMORY_MAX}M
TasksMax=$((PROFILE_CONCURRENCY * 4))
OOMPolicy=stop
OOMScoreAdjust=500
KillMode=mixed
EOF
chmod 0644 "$DROPIN/resources.conf"
install -m 0755 "$ROOT/packaging/zdxctl" /usr/local/bin/zdxctl
systemctl daemon-reload; systemctl enable zdx-worker.service >/dev/null
echo "ZDX installed at $PREFIX; profile and cgroup drop-in generated."
echo "Worker is disabled by default. Use: zdxctl status, then zdxctl policy enable and zdxctl start"
