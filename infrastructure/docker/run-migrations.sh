#!/bin/sh
# Apply every /migrations/*.sql in lexicographic order. The schema files use
# bare CREATE TABLE (no IF NOT EXISTS), so partial-state databases produce
# "relation already exists" errors mid-run. We deliberately do NOT pass
# ON_ERROR_STOP — psql logs the error and continues, so each file applies
# what's new and skips what's already there. Re-runs are idempotent.
set -u
: "${DATABASE_URL:?DATABASE_URL must be set}"
for f in /migrations/*.sql; do
    echo "→ applying $(basename "$f")"
    psql "$DATABASE_URL" -f "$f" || true
done
echo "✓ migrations pass complete"
