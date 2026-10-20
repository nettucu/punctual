#!/bin/sh
# Cloud Run deploy with Litestream backups to GCS.
# First deploy:   PROJECT=my-project ADMIN_EMAIL=you@example.com [TIMEZONE=Europe/Bucharest] [UI_LOCALE=ro] deploy/cloudrun/deploy.sh
# Update:         PROJECT=my-project deploy/cloudrun/deploy.sh   (code only: env vars, secrets and data are kept)
# Optional: REGION, SERVICE, BUCKET, ACCOUNT (default: the active gcloud account). Run from the repo root.
set -eu
: "${PROJECT:?set PROJECT}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-punctual}"
BUCKET="${BUCKET:-$PROJECT-punctual-backup}"
SA="punctual-run@$PROJECT.iam.gserviceaccount.com"
REPLICA="gs://$BUCKET/db-v5"

# Scoped to this process via env vars: the user's global gcloud configuration is left untouched.
export CLOUDSDK_CORE_PROJECT="$PROJECT"
[ -z "${ACCOUNT:-}" ] || export CLOUDSDK_CORE_ACCOUNT="$ACCOUNT"

ask() { printf '%s [y/N] ' "$1"; read -r a; [ "$a" = y ] || [ "$a" = Y ]; }

if gcloud run services describe "$SERVICE" --region "$REGION" >/dev/null 2>&1; then
  echo "Service '$SERVICE' already exists in $PROJECT/$REGION:"
  gcloud run services describe "$SERVICE" --region "$REGION" \
    --format='value(status.latestReadyRevisionName,status.url)'
  echo "Env vars:  $(gcloud run services describe "$SERVICE" --region "$REGION" \
    --format='value(spec.template.spec.containers[0].env[].name)' | tr ';' ' ')"
  if command -v litestream >/dev/null && command -v sqlite3 >/dev/null; then
    echo "Backup check (restores the latest replica into a temp file):"
    LITESTREAM_REPLICA_URL="$REPLICA" sh "$(dirname "$0")/verify-backup.sh" || { echo "Backup check FAILED" >&2; ask "Deploy anyway?" || exit 1; }
  else
    echo "litestream/sqlite3 not installed: backup NOT checked."
  fi
  ask "Deploy the current code? Env vars, secrets and the database are left as they are." || exit 0
  gcloud run deploy "$SERVICE" --source . --region "$REGION"
  [ -z "${TIMEZONE:-}${UI_LOCALE:-}" ] || gcloud run services update "$SERVICE" --region "$REGION" \
    --update-env-vars "PUNCTUAL_TIMEZONE=${TIMEZONE:-Europe/Bucharest},PUNCTUAL_LOCALE=${UI_LOCALE:-en}"
  if gcloud run services describe "$SERVICE" --region "$REGION" --format=yaml | grep -q PUNCTUAL_BOOTSTRAP_ADMIN_PASSWORD; then
    ask "The bootstrap admin password is still attached. Remove it (needed only for the first login)?" &&
      gcloud run services update "$SERVICE" --region "$REGION" --remove-secrets PUNCTUAL_BOOTSTRAP_ADMIN_PASSWORD
  fi
  exit 0
fi

: "${ADMIN_EMAIL:?first deploy: set ADMIN_EMAIL}"
ask "No service '$SERVICE' yet. Create bucket, service account, secret and service in $PROJECT/$REGION?" || exit 0

NUM="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com storage.googleapis.com

# New projects: the default build identity needs build permissions or `--source` deploys fail.
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$NUM-compute@developer.gserviceaccount.com" \
  --role=roles/cloudbuild.builds.builder --condition=None >/dev/null

# Backup bucket: private, versioned, same region as the service (data stays in the chosen region).
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || {
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
  gcloud storage buckets update "gs://$BUCKET" --versioning
}

# Least-privilege identity: it can touch only this bucket.
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || gcloud iam service-accounts create punctual-run --display-name="Punctual runtime"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" --role=roles/storage.objectAdmin >/dev/null

# First-admin password lives in Secret Manager, never in the command line or repo.
if ! gcloud secrets describe punctual-bootstrap-password >/dev/null 2>&1; then
  printf 'Bootstrap admin password (min 12 chars): '; stty -echo; read -r PW; stty echo; echo
  printf '%s' "$PW" | gcloud secrets create punctual-bootstrap-password --data-file=-
fi
gcloud secrets add-iam-policy-binding punctual-bootstrap-password --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor >/dev/null

# max-instances=1: SQLite has a single writer. no-cpu-throttling: Litestream must keep replicating between requests.
gcloud run deploy "$SERVICE" --source . --region "$REGION" --service-account "$SA" \
  --allow-unauthenticated --max-instances=1 --min-instances=0 --cpu=1 --memory=512Mi --no-cpu-throttling \
  --set-env-vars "LITESTREAM_REPLICA_URL=$REPLICA,PUNCTUAL_BOOTSTRAP_ADMIN_EMAIL=$ADMIN_EMAIL,PUNCTUAL_TIMEZONE=${TIMEZONE:-Europe/Bucharest},PUNCTUAL_LOCALE=${UI_LOCALE:-en}" \
  --set-secrets "PUNCTUAL_BOOTSTRAP_ADMIN_PASSWORD=punctual-bootstrap-password:latest"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "PUNCTUAL_PUBLIC_URL=$URL"
echo "Deployed: $URL  (admin: $ADMIN_EMAIL). After the first login remove the bootstrap password: rerun this script."
