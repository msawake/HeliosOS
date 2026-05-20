#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# ForgeOS database restore script.
#
# Downloads a backup from S3/GCS and restores it into a target Postgres.
# ALWAYS restores into a FRESH database — never onto the running production
# DB. Use for DR drills, PITR-style recovery, or environment cloning.
#
# Environment:
#   BACKUP_BUCKET    Source bucket (gs:// or s3://)
#   TARGET_DB_URL    Destination Postgres URL (MUST be empty/fresh)
#
# Usage:
#   BACKUP_BUCKET=gs://forgeos-backups \
#   TARGET_DB_URL=postgres://user:pass@host/forgeos_restored \
#     bash infrastructure/scripts/db-restore.sh
#
#   # Restore a specific backup:
#   BACKUP_KEY=daily/forgeos-20260411T020000Z.sql.gz ./db-restore.sh
#
#   # List available backups:
#   BACKUP_BUCKET=gs://forgeos-backups ./db-restore.sh --list
# ----------------------------------------------------------------------------
set -euo pipefail

: "${BACKUP_BUCKET:?BACKUP_BUCKET not set}"

if [[ "${1:-}" == "--list" ]]; then
  case "$BACKUP_BUCKET" in
    gs://*) gsutil ls -l "$BACKUP_BUCKET/daily/" 2>/dev/null ;;
    s3://*) aws s3 ls "$BACKUP_BUCKET/daily/" ;;
  esac
  exit 0
fi

: "${TARGET_DB_URL:?TARGET_DB_URL not set}"

# Safety check: refuse to restore into a non-empty DB
EXISTING=$(psql "$TARGET_DB_URL" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null || echo "0")
if [[ "$EXISTING" != "0" ]]; then
  echo "ERROR: Target DB has $EXISTING existing tables. Refusing to overwrite."
  echo "Create a fresh database first:"
  echo "  createdb forgeos_restored"
  exit 2
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Pick the most recent daily backup if no key specified
BACKUP_KEY="${BACKUP_KEY:-}"
if [[ -z "$BACKUP_KEY" ]]; then
  echo "[$(date -u +%H:%M:%S)] Finding most recent backup..."
  case "$BACKUP_BUCKET" in
    gs://*)
      BACKUP_KEY=$(gsutil ls "$BACKUP_BUCKET/daily/*.sql.gz" 2>/dev/null | sort | tail -1 \
                   | sed "s|$BACKUP_BUCKET/||")
      ;;
    s3://*)
      BACKUP_KEY=$(aws s3 ls "$BACKUP_BUCKET/daily/" | sort | tail -1 | awk '{print "daily/"$4}')
      ;;
  esac
  echo "  Selected: $BACKUP_KEY"
fi

LOCAL_FILE="$TMPDIR/$(basename "$BACKUP_KEY")"

# Download
echo "[$(date -u +%H:%M:%S)] Downloading $BACKUP_BUCKET/$BACKUP_KEY"
case "$BACKUP_BUCKET" in
  gs://*) gsutil cp "$BACKUP_BUCKET/$BACKUP_KEY" "$LOCAL_FILE" ;;
  s3://*) aws s3 cp "$BACKUP_BUCKET/$BACKUP_KEY" "$LOCAL_FILE" ;;
esac

SIZE=$(du -h "$LOCAL_FILE" | cut -f1)
echo "[$(date -u +%H:%M:%S)] Downloaded $SIZE"

# Restore
echo "[$(date -u +%H:%M:%S)] Restoring to target DB"
gunzip -c "$LOCAL_FILE" | psql "$TARGET_DB_URL"

# Verify
TABLES=$(psql "$TARGET_DB_URL" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
echo "[$(date -u +%H:%M:%S)] Restore complete — $TABLES tables created"

# Quick sanity check: can we query a few core tables?
for tbl in tenants platform_agents audit_log; do
  count=$(psql "$TARGET_DB_URL" -tAc "SELECT count(*) FROM $tbl;" 2>/dev/null || echo "MISSING")
  echo "  $tbl: $count rows"
done

echo ""
echo "Next steps:"
echo "  1. Run migrations: DATABASE_URL=$TARGET_DB_URL forgeos-migrate"
echo "  2. Point app at restored DB"
echo "  3. Verify functionality in staging before cutover"
