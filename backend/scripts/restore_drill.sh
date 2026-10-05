#!/usr/bin/env bash
# Timed restore drill (spec §13.1: "A restore from backup into a clean environment succeeds in a
# timed drill"). Run quarterly (§10.3), and after any change to backup.sh or the schema.
#
# Restores an encrypted backup into a brand-new database, proves the result is usable and still
# enforces tenant isolation, appends a JSON record of the drill as evidence, then drops the database.
#
# Usage:  restore_drill.sh <stemtrack-...dump.age>
#
# Environment:
#   DRILL_ADMIN_URL      libpq URL, including a database name, for the admin role of a drill or
#                        staging cluster (never production): it creates and drops the drill database
#                        and impersonates stem_app for the isolation check, so it must be a superuser
#                        there. The cluster needs the roles from scripts/init_db.sql.
#   BACKUP_AGE_IDENTITY  the age private key file. It is kept offline and brought to the drill.
#   DRILL_MAX_SECONDS    recovery-time target; the drill fails if restoring takes longer (default 900)
#   DRILL_LOG            JSON-lines evidence file (default: ./backups/restore-drills.jsonl)
#   DRILL_KEEP=1         keep the restored database for inspection instead of dropping it
set -euo pipefail

backup="${1:?usage: restore_drill.sh <backup.dump.age>}"
: "${DRILL_ADMIN_URL:?set DRILL_ADMIN_URL (admin of a drill cluster, with a database name)}"
: "${BACKUP_AGE_IDENTITY:?set BACKUP_AGE_IDENTITY (the age private key file)}"
max_seconds="${DRILL_MAX_SECONDS:-900}"
log="${DRILL_LOG:-./backups/restore-drills.jsonl}"

for tool in psql pg_restore age sha256sum; do
  command -v "$tool" >/dev/null || { echo "drill: $tool is not installed" >&2; exit 2; }
done
[ -f "$backup" ] || { echo "drill: no such backup: $backup" >&2; exit 2; }

# The same server with a different database, keeping any ?sslmode=... options.
url_for_db() {
  local base="${DRILL_ADMIN_URL%%\?*}" query=""
  [[ "$DRILL_ADMIN_URL" == *\?* ]] && query="?${DRILL_ADMIN_URL#*\?}"
  printf '%s/%s%s' "${base%/*}" "$1" "$query"
}
db="stemtrack_drill_$(date -u +%Y%m%d%H%M%S)"
admin() { psql -X -q -At -v ON_ERROR_STOP=1 --dbname="$DRILL_ADMIN_URL" "$@"; }
drill() { psql -X -q -At -v ON_ERROR_STOP=1 --dbname="$(url_for_db "$db")" "$@"; }

failures=()
check() { # check <description> <actual> <expected>
  if [ "$2" == "$3" ]; then echo "  ok    $1"; else echo "  FAIL  $1 (got '$2', expected '$3')"; failures+=("$1"); fi
}

started=$(date +%s)
echo "drill: $backup -> $db"

sha="$(sha256sum "$backup" | cut -d' ' -f1)"
if [ -f "$backup.sha256" ]; then
  check "checksum matches $backup.sha256" "$sha" "$(cut -d' ' -f1 < "$backup.sha256")"
else
  echo "  warn  no $backup.sha256 to verify against"
fi

roles="$(admin -c "SELECT count(*) FROM pg_roles WHERE rolname IN ('stem_owner', 'stem_app')")"
[ "$roles" == 2 ] || { echo "drill: run scripts/init_db.sql on the drill cluster first" >&2; exit 2; }

admin -c "CREATE DATABASE \"$db\" OWNER stem_owner"
cleanup() {
  if [ "${DRILL_KEEP:-0}" == 1 ]; then echo "drill: kept database $db"; else admin -c "DROP DATABASE IF EXISTS \"$db\" WITH (FORCE)"; fi
}
trap cleanup EXIT

restore_started=$(date +%s)
age --decrypt --identity "$BACKUP_AGE_IDENTITY" "$backup" \
  | pg_restore --exit-on-error --single-transaction --dbname="$(url_for_db "$db")"
restore_seconds=$(( $(date +%s) - restore_started ))
echo "drill: restored in ${restore_seconds}s"

revision="$(drill -c "SELECT version_num FROM alembic_version")"
tenants="$(drill -c "SELECT count(*) FROM tenants")"
counts="$(drill -c "SELECT json_build_object(
  'tenants', (SELECT count(*) FROM tenants), 'users', (SELECT count(*) FROM users),
  'students', (SELECT count(*) FROM students), 'skill_awards', (SELECT count(*) FROM skill_awards),
  'results', (SELECT count(*) FROM results), 'message_deliveries', (SELECT count(*) FROM message_deliveries),
  'consents', (SELECT count(*) FROM consents), 'audit_events', (SELECT count(*) FROM audit_events))")"
echo "drill: schema revision $revision, rows $counts"

echo "checks:"
check "at least one tenant restored" "$([ "$tenants" -ge 1 ] && echo yes || echo no)" yes
check "every tenant table has row-level security enabled and forced" "$(drill -c "
  SELECT coalesce(string_agg(c.relname, ','), '') FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
  WHERE c.relkind = 'r' AND NOT (c.relrowsecurity AND c.relforcerowsecurity)")" ""
check "runtime role cannot update or delete the audit log" "$(drill -c "
  SELECT has_table_privilege('stem_app', 'audit_events', 'UPDATE')
      OR has_table_privilege('stem_app', 'audit_events', 'DELETE')")" f

# The restored database must still isolate tenants for the role the API runs as.
tenant="$(drill -c "SELECT id FROM tenants ORDER BY created_at LIMIT 1")"
expected="$(drill -c "SELECT count(*) FROM students WHERE tenant_id = '$tenant'")"
isolation="$(drill <<SQL
BEGIN;
SET LOCAL ROLE stem_app;
SELECT count(*) FROM students;
SELECT set_config('app.tenant_id', '$tenant', true) IS NOT NULL;
SELECT count(*) FROM students;
ROLLBACK;
SQL
)"
check "runtime role sees no students without a tenant" "$(sed -n 1p <<< "$isolation")" 0
check "runtime role sees exactly its tenant's students" "$(sed -n 3p <<< "$isolation")" "$expected"
check "restore finished within ${max_seconds}s" "$([ "$restore_seconds" -le "$max_seconds" ] && echo yes || echo no)" yes

total_seconds=$(( $(date +%s) - started ))
result=$([ ${#failures[@]} -eq 0 ] && echo pass || echo fail)
mkdir -p "$(dirname "$log")"
printf '{"drilled_at":"%s","backup":"%s","sha256":"%s","database":"%s","schema_revision":"%s","restore_seconds":%d,"total_seconds":%d,"max_seconds":%d,"rows":%s,"failures":%d,"result":"%s"}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$backup")" "$sha" "$db" "$revision" \
  "$restore_seconds" "$total_seconds" "$max_seconds" "$counts" "${#failures[@]}" "$result" >> "$log"

echo "drill: $result in ${total_seconds}s (restore ${restore_seconds}s); recorded in $log"
[ "$result" == pass ]
