# Deploy

Punctual is one container: the app plus [Litestream](https://litestream.io) for continuous SQLite backup. Pick a path.

| Path | Use when |
|------|----------|
| [A. Docker Compose](#a-docker-compose-any-vps) | You have a VPS or any Docker host. |
| [B. Google Cloud Run](#b-google-cloud-run) | You want a managed, scale-to-zero deploy on GCP. |
| [C. Other container platforms](#c-other-platforms) | Fly.io, Railway, Kubernetes, ... |

Constraints that apply everywhere:
- **One instance only.** SQLite has a single writer.
- **HTTPS is required.** Browsers expose GPS only on secure origins. Compose handles it with Caddy.
- Set `PUNCTUAL_PUBLIC_URL` so printed QR codes point at the real address.
- Set `PUNCTUAL_TIMEZONE` (IANA name, default `UTC`). All displayed and typed times use it.
- Set `PUNCTUAL_LOCALE` (`en` default, or `ro`) for the interface and message language.
- Remove `PUNCTUAL_BOOTSTRAP_ADMIN_PASSWORD` after the first login and change that password.

## A. Docker Compose (any VPS)

```bash
cp .env.example .env        # fill in domain, admin email/password, public URL, timezone
docker compose -f deploy/docker-compose.yml --env-file .env up -d --build
```

Point your domain's DNS at the host first; Caddy obtains the certificate on its own. Data lives in the `data` volume.

**Backups.** Set `LITESTREAM_REPLICA_URL` in `.env`; on boot a fresh container restores the DB from the replica, then replicates with a 1 s sync. Without it the entrypoint prints a warning and runs **without backups**. Credentials depend on the target:

- **S3, MinIO, Backblaze B2:** `s3://bucket/punctual` plus `LITESTREAM_ACCESS_KEY_ID` and `LITESTREAM_SECRET_ACCESS_KEY` (these two are for S3-compatible storage only).
- **Google Cloud Storage:** `gs://bucket/db-v5` plus a service-account key file with access to that bucket. Save it as `gcs-key.json` next to `docker-compose.yml`, uncomment the `gcs.json` volume line in the compose file and set `GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcs.json` in `.env`. (On Cloud Run no key is needed, see B.)
- **Local drill:** `file:///replica` with a volume mounted at `/replica`.

## B. Google Cloud Run

```bash
PROJECT=my-project ADMIN_EMAIL=you@example.com TIMEZONE=Europe/Bucharest UI_LOCALE=ro deploy/cloudrun/deploy.sh
```

Optional: `UI_LOCALE` (`en` default, or `ro`), `REGION` (default `europe-west1`), `SERVICE`, `BUCKET`, `ACCOUNT` (default: your active gcloud account).

The script creates a private versioned GCS bucket, a least-privilege service account limited to that bucket, a Secret Manager secret for the first-admin password, and deploys with `--max-instances=1 --no-cpu-throttling` (Litestream must keep replicating between requests). It prompts for the admin password only the first time, when the secret does not exist yet. Re-runs reuse it.

It changes nothing in your global gcloud configuration. Replica location: `gs://<bucket>/db-v5`.

## C. Other platforms

Build the root `Dockerfile`, mount a persistent volume at `/data`, run a single instance, and pass the variables from `.env.example`. The container listens on `$PORT` (default 8080); `/healthz` is the health endpoint.

## Backup drill

`deploy/cloudrun/verify-backup.sh` restores the latest replica into a temp file and prints the integrity check and row counts (needs `litestream` and `sqlite3` locally). It works with any replica URL. Run it regularly.

Local drill without cloud: run the image with `-e LITESTREAM_REPLICA_URL=file:///replica -v somedir:/replica`, write data, kill the container, start a new one, and confirm the data is back. Measured locally: a graceful stop (SIGTERM) loses nothing; a hard kill loses at most the last ~1 s of writes.
