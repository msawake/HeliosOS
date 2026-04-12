# Runbook — Database Recovery

**Alert**: `ForgeOSDatabaseDown` / `db.connection_lost`
**Severity**: SEV1
**Owner**: Platform on-call

## Symptoms

- API `/api/health` returns `"database": false`
- Audit log action `db.connection_lost` fires
- Client writes fail with 503 or the UI silently loses newly-created rows
- Grafana "Agents running" gauge drops to 0 even though pods are up
- pg connection pool errors in API pod logs

## Immediate triage

1. **Confirm the outage is real**
   ```bash
   kubectl exec -n forgeos deploy/forgeos-api -- \
     curl -sf http://localhost:5000/api/health | jq .components.database
   ```
   If this returns `false`, the DB is unreachable from the API pods.

2. **Check the DB pod / instance**
   ```bash
   # In-cluster Postgres:
   kubectl get pod -n forgeos -l app=postgres
   kubectl logs -n forgeos -l app=postgres --tail=100

   # Cloud SQL:
   gcloud sql instances describe forgeos-prod --format='value(state)'
   # Expect: RUNNABLE

   # RDS:
   aws rds describe-db-instances --db-instance-identifier forgeos-prod \
     --query 'DBInstances[0].DBInstanceStatus'
   # Expect: available
   ```

3. **Check connection count**
   ```bash
   psql "$DATABASE_URL" -c "SELECT count(*) FROM pg_stat_activity;"
   ```
   If near `max_connections` (200 by default), kill idle connections:
   ```sql
   SELECT pg_terminate_backend(pid) FROM pg_stat_activity
   WHERE state = 'idle' AND state_change < now() - interval '10 minutes';
   ```

## Recovery paths

### Path A: Transient network blip (most common)

If the DB instance is healthy but the API can't reach it:
1. Check the K8s NetworkPolicy:
   ```bash
   kubectl get networkpolicy -n forgeos
   kubectl describe networkpolicy api-egress -n forgeos
   ```
2. Verify egress to the DB host/IP is allowed.
3. Restart the API pods to drop stale connection pools:
   ```bash
   kubectl rollout restart deploy/forgeos-api -n forgeos
   ```
4. Watch `/api/health` come back green.

### Path B: DB instance crash

If the DB instance is itself down:
1. **Cloud SQL / RDS**: wait for auto-failover (Cloud SQL HA: ~2 min,
   RDS Multi-AZ: ~60 sec). Monitor the instance state.
2. **In-cluster Postgres** (StatefulSet): check the pod, `kubectl logs`,
   usually the PVC is corrupted or disk is full. In that case, see Path C.
3. Once the DB is back, restart API pods to drop stale pools.

### Path C: Data corruption / disk full (rare)

If the DB won't start or there's obvious corruption:

1. **Put ForgeOS in maintenance mode**:
   ```bash
   kubectl scale deploy/forgeos-api --replicas=0 -n forgeos
   kubectl scale deploy/forgeos-web --replicas=0 -n forgeos
   ```

2. **Restore from the most recent backup** (see `db-restore.sh`):
   ```bash
   # 1. Create a fresh empty database
   gcloud sql databases create forgeos_restored --instance=forgeos-prod

   # 2. Run restore
   BACKUP_BUCKET=gs://forgeos-backups \
   TARGET_DB_URL=postgres://.../forgeos_restored \
     bash infrastructure/scripts/db-restore.sh

   # 3. Run migrations on restored DB
   DATABASE_URL=postgres://.../forgeos_restored forgeos-migrate
   ```

3. **Flip the app to the restored DB**:
   ```bash
   kubectl set env deploy/forgeos-api -n forgeos \
     DATABASE_URL=postgres://.../forgeos_restored
   kubectl rollout restart deploy/forgeos-api -n forgeos
   ```

4. **Bring traffic back**:
   ```bash
   kubectl scale deploy/forgeos-api --replicas=2 -n forgeos
   kubectl scale deploy/forgeos-web --replicas=2 -n forgeos
   ```

5. Verify `/api/health` and smoke-test the UI.

## Data loss estimation

`db-backup.sh` runs daily at 03:00 UTC. Worst case = 24 hours of data loss.
Check the backup bucket for the latest snapshot timestamp:

```bash
gsutil ls -l gs://forgeos-backups/daily/ | tail -5
```

## Post-incident

- Write an incident report using `docs/runbooks/incident-response.md`.
- Open a ticket to shorten the backup interval if 24h RPO is unacceptable
  (enable Cloud SQL PITR or RDS automated backups for < 5 min RPO).
- Audit the alert fired vs. time-to-detection; if > 5 min, tune the
  PrometheusRule `for:` clause.

## Related runbooks

- `incident-response.md` — general incident handling
- `scheduler.md` — if scheduled jobs missed runs during the outage
- `cost-spike.md` — if recovery caused a cost spike from retries
