#!/usr/bin/env bash
# Idempotent installer for the Intacct -> SQL ETL on a fresh Ubuntu VM.
# Run from the unpacked bundle directory:  sudo bash deploy/install.sh
# It does NOT write secrets or config.yaml — you fill those in afterwards (see DEPLOY.md).
set -euo pipefail

APP_USER=intacct
APP_DIR=/opt/intacct-etl
CFG_DIR=/etc/intacct-etl
DATA_DIR=/var/lib/intacct-etl
LOG_DIR=/var/log/intacct-etl
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo ">> Creating service user + directories"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR" "$DATA_DIR/audit" "$LOG_DIR"
install -d -o root -g "$APP_USER" -m 750 "$CFG_DIR"

echo ">> Copying application into $APP_DIR"
cp -r "$SRC_DIR"/. "$APP_DIR"/
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo ">> Creating virtualenv + installing the package"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip wheel >/dev/null
"$APP_DIR/venv/bin/pip" install "$APP_DIR"

echo ">> Installing systemd units"
install -m 644 "$SRC_DIR/deploy/intacct-etl.service" /etc/systemd/system/intacct-etl.service
install -m 644 "$SRC_DIR/deploy/intacct-etl.timer"   /etc/systemd/system/intacct-etl.timer
systemctl daemon-reload

echo ">> Seeding config + secret templates (only if absent)"
[ -f "$CFG_DIR/config.yaml" ] || install -o root -g "$APP_USER" -m 640 "$SRC_DIR/config/config.example.yaml" "$CFG_DIR/config.yaml"
[ -f "$CFG_DIR/intacct-etl.env" ] || install -o root -g "$APP_USER" -m 640 "$SRC_DIR/deploy/intacct-etl.env.example" "$CFG_DIR/intacct-etl.env"

cat <<'NEXT'

>> Install complete. NEXT STEPS (see DEPLOY.md):
   1. Edit /etc/intacct-etl/config.yaml         (endpoints, table/proc names, run params)
   2. Edit /etc/intacct-etl/intacct-etl.env     (Sage + SQL secrets); chmod 600
   3. Point log.dir=/var/log/intacct-etl and audit.dir=/var/lib/intacct-etl/audit in config.yaml
   4. Verify:   sudo -u intacct /opt/intacct-etl/venv/bin/intacct-etl --config /etc/intacct-etl/config.yaml --help
   5. First run is test_mode=true (parallel to Make, test_ tables). Enable the timer only after parity sign-off.
NEXT
