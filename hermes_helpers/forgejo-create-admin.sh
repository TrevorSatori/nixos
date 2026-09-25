#!/usr/bin/env bash
# Creates the Forgejo admin user. Run AFTER `sudo nixos-rebuild switch`:
#   sudo /etc/nixos/hermes_helpers/forgejo-create-admin.sh [username] [email]
set -euo pipefail
USER_NAME="${1:-satori}"
EMAIL="${2:-trevorsatori@gmail.com}"
OUT=/var/src/secrets/forgejo-admin.txt

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
systemctl is-active --quiet forgejo || { echo "forgejo not running - rebuild first"; exit 1; }

# Pull the exact forgejo binary the service uses
BIN=$(systemctl cat forgejo | grep -o '/nix/store/[^ ]*/bin/\(forgejo\|gitea\)' | head -1)
[ -x "$BIN" ] || { echo "couldn't find forgejo binary"; exit 1; }

RESULT=$(sudo -S -p '' -u forgejo "$BIN" --work-path /var/lib/forgejo \
  --config /var/lib/forgejo/custom/conf/app.ini \
  admin user create --admin --username "$USER_NAME" --email "$EMAIL" \
  --random-password --must-change-password=false 2>&1)

PASS=$(echo "$RESULT" | grep -o "generated random password is '[^']*'" | sed "s/.*is '\(.*\)'/\1/")
if [ -z "$PASS" ]; then echo "$RESULT"; exit 1; fi

umask 077
printf 'url=https://git.lo-pan.com\nusername=%s\npassword=%s\n' "$USER_NAME" "$PASS" > "$OUT"
chmod 600 "$OUT"
echo "Admin '$USER_NAME' created. Creds in $OUT" | tee /etc/nixos/hermes_helpers/forgejo-create-admin.log
