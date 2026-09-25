#!/usr/bin/env bash
# Writes the Forgejo runner token (from the UI) to its secret file.
# Prompts silently, no newline, root:root 600, then restarts the runner.
#   sudo /etc/nixos/hermes_helpers/forgejo-runner-secret.sh
set -euo pipefail
F=/var/src/secrets/forgejo-runner.token
LOG=/etc/nixos/hermes_helpers/forgejo-runner-secret.log
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

read -rsp "Paste runner token: " TOKEN; echo
TOKEN=$(printf '%s' "$TOKEN" | tr -d '[:space:]')
[ -n "$TOKEN" ] || { echo "empty token, bailing"; exit 1; }

install -m 600 -o root -g root /dev/null "$F"
printf '%s' "$TOKEN" > "$F"
unset TOKEN
rm -f /var/src/secrets/forgejo-runner.env   # old legacy-flow file

systemctl restart gitea-runner-lopan || true
sleep 5
{
  ls -l "$F"
  echo "token len=$(wc -c < "$F")"
  systemctl is-active gitea-runner-lopan || true
  journalctl -u gitea-runner-lopan -n 25 --no-pager
} > "$LOG" 2>&1
chmod 644 "$LOG"
echo "done, see $LOG"
