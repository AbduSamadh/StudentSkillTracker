-- Creates the two database roles the platform uses. Run once per cluster as a superuser.
--   stem_owner: owns the schema, runs migrations.
--   stem_app:   the runtime role. NOBYPASSRLS, so row-level security always applies.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'stem_owner') THEN
    CREATE ROLE stem_owner LOGIN PASSWORD 'stem_owner';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'stem_app') THEN
    CREATE ROLE stem_app LOGIN PASSWORD 'stem_app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;
