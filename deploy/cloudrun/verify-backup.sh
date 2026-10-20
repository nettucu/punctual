#!/bin/sh
# Backup drill: restore the latest replica into a temp file and check it against expectations.
# Usage: LITESTREAM_REPLICA_URL=gs://bucket/db-v5 deploy/cloudrun/verify-backup.sh
set -eu
: "${LITESTREAM_REPLICA_URL:?set LITESTREAM_REPLICA_URL}"
OUT="$(mktemp -d)/restored.db"
litestream restore -o "$OUT" "$LITESTREAM_REPLICA_URL"
echo "integrity: $(sqlite3 "$OUT" 'PRAGMA integrity_check;')"
for t in employees sites punches punch_voids; do
  echo "$t: $(sqlite3 "$OUT" "SELECT COUNT(*) FROM $t;")"
done
echo "latest punch: $(sqlite3 "$OUT" 'SELECT MAX(ts_utc) FROM punches;')"
echo "restored copy at $OUT"
