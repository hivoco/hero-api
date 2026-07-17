#!/bin/bash
# Download the free DB-IP "City Lite" database used for offline IP -> city
# lookup (app/core/geoip.py). CC-BY-4.0, direct download, NO signup.
#
# NOTE: you normally do NOT need to run this. The app downloads the file itself
# on startup when it's missing (see ensure_db_async() in app/core/geoip.py).
# This script is just a manual/offline escape hatch, and a way to REFRESH the
# data — DB-IP republishes monthly, and the app only downloads when the file is
# absent, so delete the .mmdb and re-run this (or restart) to pick up a newer one.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p app/data
OUT="app/data/dbip-city-lite.mmdb"

for offset in 0 1 2; do
  MONTH=$(date -v-"${offset}"m +%Y-%m 2>/dev/null || date -d "-${offset} month" +%Y-%m)
  URL="https://download.db-ip.com/free/dbip-city-lite-${MONTH}.mmdb.gz"
  echo "Trying ${MONTH} ..."
  if curl -fsSL "$URL" -o /tmp/dbip.mmdb.gz; then
    gunzip -f /tmp/dbip.mmdb.gz
    mv -f /tmp/dbip.mmdb "$OUT"
    echo "Saved $OUT ($(du -h "$OUT" | cut -f1)) from ${MONTH}"
    exit 0
  fi
done

echo "Failed to download DB-IP City Lite (tried the last 3 months)." >&2
exit 1
