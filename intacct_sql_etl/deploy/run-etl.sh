#!/usr/bin/env bash
# Hardened run-wrapper for the Intacct -> SQL ETL on the SHARED VM (VTSAzPLW01).
#
# Why this exists: 2026-07-02 a wide-window delta sweep-upsert materialised a very large result
# set in memory on a swapless, shared box and drove it to thrash — sshd was starved and the box
# became unresponsive ("VM overload"). This wrapper makes that mechanically impossible, rootless:
#
#   1. flock overlap guard   -> two runs can NEVER stack (kills the concurrent/orphan-run overload).
#   2. cgroup MemoryMax      -> a runaway run is OOM-killed IN ITS OWN cgroup (systemd-run --user);
#                               the rest of the VM (stampli Docker stack, sshd) is never starved.
#                               Falls back to `ulimit -v` (RLIMIT_AS) when no user systemd bus.
#   3. wall-clock timeout    -> a stuck run (Sage hang, etc.) is reaped, never left orphaned.
#   4. clear OOM/timeout signal on stdout+log so cron/the notifier can alert.
#
# Usage:
#   run-etl.sh --config /data/intacct-etl/config.yaml --mode upsert [--start MM/DD/YYYY --end ...]
# Env overrides:
#   ETL_MEM_MAX      cgroup RSS ceiling            (default 3G)
#   ETL_ULIMIT_V_KB  ulimit -v cap in KB, fallback (default 3500000 = ~3.4G VSZ)
#   ETL_RUN_MAX_SEC  hard wall-clock cap in sec    (default 3600)
set -euo pipefail

APP=/data/intacct-etl
PY="${ETL_PY:-$APP/venv/bin/intacct-etl}"   # ETL_PY override is for guardrail self-tests only
LOCK="$APP/run.lock"
LOG_DIR="$APP/logs"
MEM_MAX="${ETL_MEM_MAX:-3G}"
ULIMIT_V_KB="${ETL_ULIMIT_V_KB:-3500000}"
RUN_MAX_SEC="${ETL_RUN_MAX_SEC:-3600}"

mkdir -p "$LOG_DIR"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
log="$LOG_DIR/run_${ts}.log"
now() { date -u +%FT%TZ; }

# ---- 1. flock overlap guard (non-blocking: skip this tick rather than stack) -------------------
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "$(now) SKIP: another ETL run holds $LOCK — not stacking (overlap guard)" | tee -a "$log"
    exit 0
fi

# ---- 7-day housekeeping (OQ-23): prune run logs + audit json older than 7 days -----------------
# Runs under the flock (single run at a time). Never fatal — housekeeping must not block a run.
AUDIT_DIR="$APP/audit"
find "$LOG_DIR" -maxdepth 1 -type f -mtime +7 -delete 2>/dev/null || true
[ -d "$AUDIT_DIR" ] && find "$AUDIT_DIR" -maxdepth 1 -type f -name '*.json' -mtime +7 -delete 2>/dev/null || true

# ---- load secrets/config env so the CLI's env-based secret provider resolves creds -------------
# The ETL reads Sage + SQL secrets from the process environment (EnvSecretProvider). Cron/manual
# invocations must load the .env first (systemd EnvironmentFile does this for unit-based runs).
ENV_FILE="${ETL_ENV_FILE:-$APP/intacct-etl.env}"
if [ -f "$ENV_FILE" ]; then
    set -a
    # tolerate CRLF-contaminated .env lines (strip trailing CR) without mutating the file
    . <(sed 's/\r$//' "$ENV_FILE")
    set +a
else
    echo "$(now) WARN: env file $ENV_FILE not found — SQL/Sage secrets may be missing" | tee -a "$log"
fi

# ---- pick the memory-cap mechanism (probe the user systemd bus once, up front) -----------------
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if systemd-run --user --scope --quiet --collect /bin/true >/dev/null 2>&1; then
    CAP_MODE="cgroup"
else
    CAP_MODE="ulimit"
fi

echo "$(now) START ETL: cap=$CAP_MODE mem_max=$MEM_MAX ulimit_v_kb=$ULIMIT_V_KB run_max=${RUN_MAX_SEC}s args=$*" | tee -a "$log"

set +e
if [ "$CAP_MODE" = "cgroup" ]; then
    # OOM-kills the run's own cgroup at RSS=$MEM_MAX without touching the rest of the box.
    systemd-run --user --scope --collect --quiet \
        -p MemoryMax="$MEM_MAX" -p MemorySwapMax=0 \
        -- timeout --signal=TERM --kill-after=30s "$RUN_MAX_SEC" "$PY" "$@" >>"$log" 2>&1
    rc=$?
else
    # Fallback: RLIMIT_AS caps virtual address space; a runaway hits MemoryError in-process.
    ( ulimit -v "$ULIMIT_V_KB"
      exec timeout --signal=TERM --kill-after=30s "$RUN_MAX_SEC" "$PY" "$@" ) >>"$log" 2>&1
    rc=$?
fi
set -e

echo "$(now) DONE ETL rc=$rc (log: $log)" | tee -a "$log"
case "$rc" in
    137) echo "$(now) *** MEMORY CAP HIT ($CAP_MODE $MEM_MAX): run OOM-killed in its cgroup. Window too large for one pass — reduce window / rely on chunking. ***" | tee -a "$log" ;;
    124) echo "$(now) *** TIMEOUT (${RUN_MAX_SEC}s): run reaped. Check Sage responsiveness / window size. ***" | tee -a "$log" ;;
esac
exit "$rc"
