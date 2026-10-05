-- Creates the database roles the platform uses. Run once per cluster as a superuser.
--   stem_owner:  owns the schema, runs migrations. Tables FORCE row-level security, so even
--                the owner only sees rows for the tenant set in app.tenant_id.
--   stem_app:    the runtime role. NOBYPASSRLS, so row-level security always applies.
--   stem_backup: read-only, used only by scripts/backup.sh. pg_dump must see every tenant's
--                rows, so this is the one role with BYPASSRLS; it cannot write anything.
-- The passwords here are for local development and CI only.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'stem_owner') THEN
    CREATE ROLE stem_owner LOGIN PASSWORD 'stem_owner';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'stem_app') THEN
    CREATE ROLE stem_app LOGIN PASSWORD 'stem_app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'stem_backup') THEN
    CREATE ROLE stem_backup LOGIN PASSWORD 'stem_backup' NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;
GRANT pg_read_all_data TO stem_backup;
