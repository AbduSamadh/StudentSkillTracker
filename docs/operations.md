# Operations runbook

## Production topology

The spec requires hosting in a UAE region (§8.2, §10.3):

| Component | Service |
|---|---|
| PostgreSQL 16 | Managed, with point-in-time recovery and encryption at rest. Three login roles (below). |
| Redis 7 | Managed; job queue and rate limits. No student data is stored in it beyond job arguments. |
| Object storage | S3-compatible: media and generated report files. Private bucket; downloads go through short-lived signed URLs. |
| `api` image | `uvicorn app.main:app`, behind TLS. Stateless, so scale horizontally. |
| `worker` image | The same image with `arq app.worker.WorkerSettings`. Run **one** replica, because it owns the cron schedule. |
| `web` image | nginx serving the PWA and proxying `/api` to the API |
| Migrations | A one-off job from the API image: `alembic upgrade head` as `stem_owner` |

`docker-compose.yml` is the reference wiring for all of the above.

## Configuration

All settings are environment variables prefixed `STEM_`; `backend/app/config.py` is the source of truth. In production, secrets come from the platform's secret manager, never from files in the image.

In `production` and `staging`, the API **refuses to start** if a development secret is still set. It also refuses if a messaging provider is enabled without its webhook secret.

| Variable | Notes |
|---|---|
| `STEM_ENVIRONMENT` | `production` |
| `STEM_DATABASE_URL` | `postgresql+asyncpg://stem_app:…@…/stemtrack?ssl=require`, the runtime role |
| `STEM_MIGRATION_DATABASE_URL` | Same database as `stem_owner`; needed only by the migration job and the CLI |
| `STEM_REDIS_URL`, `STEM_USE_JOB_QUEUE=true` | |
| `STEM_STORAGE_BACKEND=s3`, `STEM_S3_*` | Bucket in the same region |
| `STEM_PUBLIC_BASE_URL`, `STEM_API_BASE_URL`, `STEM_CORS_ORIGINS` | The public origin |
| `STEM_JWT_SECRET` | `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `STEM_FIELD_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` |
| `STEM_FIELD_ENCRYPTION_PREVIOUS_KEYS` | JSON list. Only set during a key rotation. |
| `STEM_BLIND_INDEX_KEY` | Random, at least 32 bytes |
| `STEM_EMAIL_PROVIDER`, `STEM_POSTMARK_TOKEN`, `STEM_POSTMARK_WEBHOOK_PASSWORD` | Configure Postmark's webhook URL as `https://postmark:<password>@<host>/api/v1/webhooks/email/postmark` |
| `STEM_SMS_PROVIDER`, `STEM_TWILIO_*` | Needs a registered UAE sender ID |
| `STEM_WHATSAPP_PROVIDER=cloud_api`, `STEM_WHATSAPP_*` | `STEM_WHATSAPP_APP_SECRET` is required, because it verifies status webhooks |
| `STEM_SENTRY_DSN`, `STEM_OTEL_ENABLED` | Optional. Keep both in-region. |

## First deployment

1. **Database roles.** Create the roles and database as the cluster admin. `backend/scripts/init_db.sql` shows the exact attributes; use strong generated passwords instead of its development ones:
   ```sql
   CREATE ROLE stem_owner LOGIN PASSWORD '…';
   CREATE ROLE stem_app LOGIN PASSWORD '…' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
   CREATE ROLE stem_backup LOGIN PASSWORD '…' NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE;
   GRANT pg_read_all_data TO stem_backup;
   CREATE DATABASE stemtrack OWNER stem_owner;
   ```
   Some managed services only let their admin role grant `BYPASSRLS`. If yours does not allow it at all, run the backup as the provider's admin user instead.
2. **Migrations.** Run them: `alembic upgrade head`, with `STEM_MIGRATION_DATABASE_URL` set.
3. **Provision the school.** This seeds the skills taxonomy, the nine message templates in English and Arabic, and the consent forms:
   ```sh
   python -m app.cli provision-tenant --slug falcon --name "Falcon Heights Academy" --name-ar "أكاديمية فالكون هايتس"
   python -m app.cli create-user --tenant falcon --email head@school.ae --name "…" --role leader --password
   ```
4. **Leader set-up.** The leader signs in, enrols MFA, and then completes Settings:
   - branding;
   - time zone and quiet hours;
   - the OIDC issuer and client, and which `amr` values count as MFA;
   - inspection strands;
   - retention.
5. **Import the roster.** Import students and guardians from the MIS export: *Admin → MIS import*, dry run first.
6. **Backup key and first drill.** Create the backup key pair, schedule backups, and run the first restore drill (below) **before** loading real data.

The development seeder (`seed-demo`) must never run against production. Staging and demos use synthetic data only.

## Releasing a new version

CI publishes the scanned API and web images, by digest, from `main`. The `production` environment's deploy step then:

1. Runs migrations with the new API image.
2. Rolls out `api`, then `worker`, then `web`.

**Migrations must be compatible with the version still running** (expand, then contract): add columns as nullable, backfill, and drop in a later release. CI checks that every migration round-trips (upgrade → downgrade → upgrade) and that the models match the schema.

## Scheduled jobs (worker)

| When (UTC) | Job |
|---|---|
| Every minute | Send due and held messages (quiet hours and the cap are re-checked), and retry outbound webhooks |
| 01:30 daily (05:30 Gulf time) | Recompute stretch, plateau and attendance flags; run the **retention purge** |
| Sundays 02:00 | Draft per-student insights for teacher review. Nothing reaches a parent until a teacher approves it. |

If the worker is down, nothing is lost: scheduled messages and webhooks wait in the database and go out when it returns. Quiet hours are re-checked then too.

## Backups and the restore drill

The spec asks for daily encrypted backups and a quarterly tested restore: "an untested backup is not a backup".

There are two layers of backup:
1. **Point-in-time recovery** from the managed database. Use it for "undo the last hour".
2. **Daily logical backups** from `backend/scripts/backup.sh`. These are portable, encrypted, and independent of the provider, and they are what the drill restores.

### Keys

```sh
age-keygen -o backup-identity.txt              # PRIVATE: store offline (password manager / safe), two custodians
age-keygen -y backup-identity.txt > backup-recipients.txt   # public: goes on the backup host
```

You can list several recipients, for example one key per custodian. Any one of them can then restore.

### Daily backup

```sh
BACKUP_DATABASE_URL='postgresql://stem_backup:…@db:5432/stemtrack?sslmode=require' \
BACKUP_AGE_RECIPIENTS=/etc/stemtrack/backup-recipients.txt \
BACKUP_DIR=/var/backups/stemtrack \
backend/scripts/backup.sh
```

- **With Docker Compose:** `docker compose --profile ops run --rm backup`, scheduled from cron, for example `15 1 * * *`.
- **Copies:** copy each `.dump.age` and its `.sha256` to a separate bucket in the same region. Use object lock, and a lifecycle rule of 35 daily, 12 monthly and 7 yearly copies.
- **Version match:** the `pg_dump` version must be at least the server's major version. The API image ships `pg_dump` 17.

### Quarterly restore drill

Run it on a **separate drill cluster**, never production. Its admin role must be a superuser there, and the roles from `init_db.sql` must exist.

```sh
DRILL_ADMIN_URL='postgresql://postgres:…@drill-db:5432/postgres' \
BACKUP_AGE_IDENTITY=/secure/backup-identity.txt \
DRILL_LOG=/var/backups/stemtrack/restore-drills.jsonl \
backend/scripts/restore_drill.sh /var/backups/stemtrack/stemtrack-20270105T011500Z.dump.age
```

The drill:
1. verifies the checksum;
2. restores into a brand-new database and times the restore against `DRILL_MAX_SECONDS` (the recovery-time target, default 900);
3. checks that every tenant table still has row-level security forced;
4. checks that the API's role sees **no** rows without a tenant, and exactly that tenant's rows with one;
5. checks that the audit log is still append-only;
6. appends a JSON evidence line, then drops the database.

Keep the evidence file; it is the audit trail that the drill happened. CI runs the same drill on every change, against a freshly seeded database.

**If a drill fails, treat it as an incident.** Backups are not proven until a drill passes again.

### Restoring for real

1. **Prefer point-in-time recovery** into a new instance, then point `STEM_DATABASE_URL` at it.
2. **Logical restore** (for example, to move provider): create the roles and an empty database owned by `stem_owner`. Then run:
   ```sh
   age --decrypt -i backup-identity.txt <file> | pg_restore --exit-on-error --single-transaction -d <new-db-url>
   ```
3. **Check the result.** Run `restore_drill.sh` with `DRILL_KEEP=1` against a copy first if time allows.

## Retention purge

It runs nightly, per school, using that school's `retention` settings; the schedule is in [security and privacy](security-and-privacy.md#retention). To run it by hand:

```sh
python -m app.cli purge --tenant falcon
```

The output is a count per record type.

## Rotating keys

| Secret | How | Effect on users |
|---|---|---|
| `STEM_FIELD_ENCRYPTION_KEY` | See the steps below | None |
| `STEM_BLIND_INDEX_KEY` | Set the new key, then run `python -m app.cli rotate-keys`. Run it straight after deploying: email look-ups (parent sign-in, imports) miss until it finishes. | Parent magic-link sign-in fails for the minutes in between |
| `STEM_JWT_SECRET` | Set the new value and redeploy | Access tokens expire at once, and the PWA silently refreshes (refresh tokens are opaque, not JWTs). **Opt-out links in messages already sent stop working.** Parents can still opt out in the portal. Rotate it only on suspicion of compromise. |
| Database passwords | `ALTER ROLE … PASSWORD`, then update the secret and redeploy | None |
| Backup age key | Add the new public key to the recipients file, then retire the old one. Keep the old **private** key until the last backup made with it has expired. | None |
| Provider tokens and webhook secrets | Rotate in the provider console, then update the secret | Check the next delivery receipts |

To rotate `STEM_FIELD_ENCRYPTION_KEY`:

1. Deploy with the new key as `STEM_FIELD_ENCRYPTION_KEY` and the old one in `STEM_FIELD_ENCRYPTION_PREVIOUS_KEYS='["old"]'`.
2. Run `python -m app.cli rotate-keys`. It re-encrypts guardian contacts, MFA secrets, webhook secrets and OIDC client secrets, one school per transaction, and stops at any value no configured key can read.
3. Remove the old key and redeploy.

## Monitoring

- **Health.** `GET /api/v1/health` returns 200 only if the database, Redis and object storage all respond, and the body shows which one failed. Use it for load-balancer checks and uptime alerts.
- **Logs.** Logs are JSON, one line per request, with a `request_id`. Alert on:
  - 5xx rate;
  - `dispatch failed` and `webhooks failed` from the worker;
  - a rising count of deliveries stuck in `pending`.
- **Errors.** Set `STEM_SENTRY_DSN` to send errors to Sentry; the integration runs with `send_default_pii` off. Keep Sentry in-region, or leave it off.

## Incident playbooks

### A wrong parent message was released

1. **Stop it.** Open the message and choose **Cancel message**, giving a reason. Any delivery not yet sent (pending, held for quiet hours, or throttled) is cancelled immediately. Already-delivered copies cannot be recalled.
2. **Find out who got it.** The delivery list shows who received it, through which channel, and whether they opened it.
3. **Follow up personally.** Send any correction by hand. Do not use an automated correction for a sensitive error.

### A user's account or device is compromised

1. In *Admin → Users*, **deactivate** the user. This takes effect on their next request, and their refresh tokens stop working.
2. Reset their MFA if needed.
3. Review their actions in the audit log, filtered by user.

### A suspected data breach

1. Preserve the audit log, and export the relevant range.
2. Involve the school's DPO and counsel straight away. The PDPL and the Child Digital Safety law have notification duties, and their timelines run from discovery.
3. Rotate the secrets that may be exposed (above).

### A second school joins

Run `python -m app.cli provision-tenant`. Row-level security already isolates it, and there is nothing else to deploy.
