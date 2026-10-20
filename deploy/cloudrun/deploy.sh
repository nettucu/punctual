#!/bin/sh
# One-shot Cloud Run deploy with Litestream backups to GCS.
# Usage (from the repo root): PROJECT=my-project ADMIN_EMAIL=you@example.com [ACCOUNT=you@example.com] [TIMEZONE=Europe/Bucharest] [UI_LOCALE=ro] deploy/cloudrun/deploy.sh
set -eu
: "${PROJECT:?set PROJECT}" "${ADMIN_EMAIL:?set ADMIN_EMAIL}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-punctual}"
BUCKET="${BUCKET:-$PROJECT-punctual-backup}"
SA="punctual-run@$PROJECT.iam.gserviceaccount.com"

# Scoped to this process via env vars: the user's global gcloud configuration is left untouched.
export CLOUDSDK_CORE_PROJECT=""
[ -z "" ] || export CLOUDSDK_CORE_ACCOUNT=""  # default: the active gcloud account
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
  --set-env-vars "LITESTREAM_REPLICA_URL=gs://$BUCKET/db-v5,PUNCTUAL_BOOTSTRAP_ADMIN_EMAIL=,PUNCTUAL_TIMEZONE=" \
  --set-secrets "PUNCTUAL_BOOTSTRAP_ADMIN_PASSWORD=punctual-bootstrap-password:latest"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "PUNCTUAL_PUBLIC_URL=$URL"
echo "Deployed: $URL  (admin: $ADMIN_EMAIL)"
