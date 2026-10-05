#!/bin/sh
# First-boot initialisation for the compose Postgres: the roles and the database.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<SQL
CREATE ROLE stem_owner LOGIN PASSWORD '${STEM_OWNER_PASSWORD}';
CREATE ROLE stem_app LOGIN PASSWORD '${STEM_APP_PASSWORD}' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
-- Read-only, for scripts/backup.sh: pg_dump has to see every tenant's rows.
CREATE ROLE stem_backup LOGIN PASSWORD '${STEM_BACKUP_PASSWORD}' NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT pg_read_all_data TO stem_backup;
CREATE DATABASE stemtrack OWNER stem_owner;
SQL
