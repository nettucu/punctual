#!/bin/sh
# Restore the DB from the replica if the container is fresh, then run the app under Litestream replication.
set -eu
APP="uvicorn punctual.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips=*"
if [ -n "${LITESTREAM_REPLICA_URL:-}" ]; then
  litestream restore -config /etc/litestream.yml -if-db-not-exists -if-replica-exists "$PUNCTUAL_DB_PATH"
  exec litestream replicate -config /etc/litestream.yml -exec "$APP"
fi
echo "WARNING: LITESTREAM_REPLICA_URL not set, running WITHOUT backups" >&2
exec $APP
