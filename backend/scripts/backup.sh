#!/usr/bin/env bash
# Daily encrypted logical backup (spec §10: daily encrypted backups, quarterly tested restore).
#
# Writes BACKUP_DIR/stemtrack-<UTC time>.dump.age and a .sha256 beside it. The dump is pg_dump's
# custom format, encrypted with age to one or more PUBLIC keys: the host that takes backups never
# holds the private key, so a compromised backup host cannot read old backups.
#
# This complements, not replaces, the managed database's own point-in-time recovery: it is the
# portable copy that can be restored anywhere, and the one the restore drill exercises.
#
# Environment:
#   BACKUP_DATABASE_URL    libpq URL for the stem_backup role (read-only, BYPASSRLS), e.g.
#                          postgresql://stem_backup:...@db:5432/stemtrack?sslmode=require
#   BACKUP_AGE_RECIPIENTS  file of age public keys, one per line (age-keygen -y identity.txt)
#   BACKUP_DIR             output directory (default: ./backups)
#   BACKUP_KEEP_DAYS       local copies older than this are deleted (default: 35)
#
# Copy the output to object storage in the same UAE region with a lifecycle rule that matches the
# retention schedule in docs/operations.md.
set -euo pipefail

: "${BACKUP_DATABASE_URL:?set BACKUP_DATABASE_URL (the stem_backup role)}"
: "${BACKUP_AGE_RECIPIENTS:?set BACKUP_AGE_RECIPIENTS (a file of age public keys)}"
dir="${BACKUP_DIR:-./backups}"
keep_days="${BACKUP_KEEP_DAYS:-35}"

for tool in pg_dump age sha256sum; do
  command -v "$tool" >/dev/null || { echo "backup: $tool is not installed" >&2; exit 2; }
done
[ -s "$BACKUP_AGE_RECIPIENTS" ] || { echo "backup: $BACKUP_AGE_RECIPIENTS is empty or missing" >&2; exit 2; }

umask 077
mkdir -p "$dir"
name="stemtrack-$(date -u +%Y%m%dT%H%M%SZ).dump.age"
partial="$dir/.$name.partial"
trap 'rm -f "$partial"' EXIT

started=$(date +%s)
# Ownership and grants are kept (no --no-owner / --no-privileges): stem_app's narrow rights and
# the row-level security policies are part of the security model and must come back on restore.
pg_dump --format=custom --compress=6 --dbname="$BACKUP_DATABASE_URL" \
  | age --encrypt --recipients-file "$BACKUP_AGE_RECIPIENTS" --output "$partial"
mv "$partial" "$dir/$name"
(cd "$dir" && sha256sum "$name" > "$name.sha256")

find "$dir" -maxdepth 1 -name 'stemtrack-*.dump.age*' -mtime +"$keep_days" -delete

size=$(wc -c < "$dir/$name")
echo "backup: wrote $dir/$name (${size} bytes) in $(( $(date +%s) - started ))s"
