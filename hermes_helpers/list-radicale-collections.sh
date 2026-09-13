#!/usr/bin/env bash
# Lists Radicale collections under satori/ with their display names + types.
# Written by Hermes; run with sudo.

set -euo pipefail

ROOT="/var/lib/radicale/collections/collection-root/satori"

for d in "$ROOT"/*/; do
  echo "=== $d ==="
  cat "$d.Radicale.props" 2>/dev/null || echo "(no props file)"
  echo
done
