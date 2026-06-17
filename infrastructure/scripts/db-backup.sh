#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# Helios OS database backup script.
#
# Runs pg_dump against the configured DATABASE_URL, compresses the output
# with gzip, and uploads to S3 or GCS (whichever is configured). Creates
# daily + monthly retention snapshots.
#
# Environment:
#   DATABASE_URL         Source Postgres URL
#   BACKUP_BUCKET        S3 or GCS bucket (gs:// or s3:// prefix)
#   BACKUP_RETENTION_DAYS  Number of daily backups to keep (default 30)
#
# Usage:
#   # One-off manual backup:
#   bash infrastructure/scripts/db-backup.sh
#
#   # Scheduled via a CronJob in K8s:
#   kubectl create -f deploy/k8s/base/cronjob-backup.yaml
# ----------------------------------------------------------------------------
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL not set}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET not set (e.g. gs://forgeos-backups)}"

BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")
HOSTNAME=$(hostname)
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

BACKUP_FILE="$TMPDIR/forgeos-${TIMESTAMP}.sql.gz"
DAILY_KEY="daily/forgeos-${TIMESTAMP}.sql.gz"
MONTHLY_KEY="monthly/forgeos-$(date -u +%Y%m).sql.gz"

echo "[$(date -u +%H:%M:%S)] Running pg_dump → $BACKUP_FILE"
pg_dump \
  --no-owner \
  --no-acl \
  --format=plain \
  --dbname="$DATABASE_URL" \
  | gzip -9 > "$BACKUP_FILE"

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date -u +%H:%M:%S)] Dump complete — $SIZE"

# -- Upload --------------------------------------------------------------
case "$BACKUP_BUCKET" in
  gs://*)
    if ! command -v gsutil >/dev/null 2>&1; then
      echo "ERROR: gsutil not found (install google-cloud-sdk)"
      exit 1
    fi
    echo "[$(date -u +%H:%M:%S)] Uploading to $BACKUP_BUCKET/$DAILY_KEY"
    gsutil cp "$BACKUP_FILE" "$BACKUP_BUCKET/$DAILY_KEY"
    # Monthly: only upload on the 1st of the month
    if [[ "$(date -u +%d)" == "01" ]]; then
      echo "[$(date -u +%H:%M:%S)] Uploading monthly snapshot"
      gsutil cp "$BACKUP_FILE" "$BACKUP_BUCKET/$MONTHLY_KEY"
    fi
    ;;
  s3://*)
    if ! command -v aws >/dev/null 2>&1; then
      echo "ERROR: aws cli not found"
      exit 1
    fi
    echo "[$(date -u +%H:%M:%S)] Uploading to $BACKUP_BUCKET/$DAILY_KEY"
    aws s3 cp "$BACKUP_FILE" "$BACKUP_BUCKET/$DAILY_KEY"
    if [[ "$(date -u +%d)" == "01" ]]; then
      aws s3 cp "$BACKUP_FILE" "$BACKUP_BUCKET/$MONTHLY_KEY"
    fi
    ;;
  *)
    echo "ERROR: Unsupported BACKUP_BUCKET prefix (expected gs:// or s3://)"
    exit 1
    ;;
esac

# -- Retention: delete daily backups older than N days -----------------
echo "[$(date -u +%H:%M:%S)] Pruning backups older than $BACKUP_RETENTION_DAYS days"
CUTOFF_DATE=$(date -u -d "$BACKUP_RETENTION_DAYS days ago" +%Y%m%dT 2>/dev/null || \
              date -u -v "-${BACKUP_RETENTION_DAYS}d" +%Y%m%dT)

case "$BACKUP_BUCKET" in
  gs://*)
    gsutil ls "$BACKUP_BUCKET/daily/" 2>/dev/null | while read -r obj; do
      obj_date=$(basename "$obj" | sed -n 's/forgeos-\([0-9]\{8\}\)T.*/\1T/p')
      if [[ -n "$obj_date" && "$obj_date" < "$CUTOFF_DATE" ]]; then
        echo "  deleting $obj"
        gsutil rm "$obj" || true
      fi
    done
    ;;
  s3://*)
    aws s3 ls "$BACKUP_BUCKET/daily/" 2>/dev/null | while read -r _ _ _ name; do
      [[ -z "$name" ]] && continue
      obj_date=$(echo "$name" | sed -n 's/forgeos-\([0-9]\{8\}\)T.*/\1T/p')
      if [[ -n "$obj_date" && "$obj_date" < "$CUTOFF_DATE" ]]; then
        echo "  deleting $name"
        aws s3 rm "$BACKUP_BUCKET/daily/$name" || true
      fi
    done
    ;;
esac

echo "[$(date -u +%H:%M:%S)] Backup complete."
