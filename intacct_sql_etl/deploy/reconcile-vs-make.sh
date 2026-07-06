#!/usr/bin/env bash
# Daily parallel-run reconcile (gate #12): compare our test_ output vs Make's prod, log drift.
# Sources the .env (secrets) then runs the reconcile; appends to logs/reconcile_<date>.txt.
# Exit 2 on drift so cron/the notifier can alert (once #8 is wired).
set -uo pipefail
APP=/data/intacct-etl
ENV_FILE="${ETL_ENV_FILE:-$APP/intacct-etl.env}"
[ -f "$ENV_FILE" ] && { set -a; . <(sed 's/\r$//' "$ENV_FILE"); set +a; }
cd "$APP/intacct_sql_etl-0.1.1" 2>/dev/null || cd "$APP"
"$APP/venv/bin/python" "$APP/reconcile_vs_make.py" "$@"
rc=$?
[ "$rc" = "2" ] && echo "$(date -u +%FT%TZ) *** RECONCILE DRIFT — see logs/reconcile_*.txt ***"
exit "$rc"
