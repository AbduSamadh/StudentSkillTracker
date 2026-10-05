"""Row-level security, runtime grants, append-only audit, data-layer media consent.

Revision ID: 0002
Revises: 0001
"""

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = os.environ.get("STEM_APP_DB_ROLE", "stem_app")

# Frozen list: a new tenant table must be added in its own migration, and
# tests/test_rls.py fails if any table with a tenant_id column lacks a policy.
TENANT_TABLES = [
    "asset_loans", "asset_requests", "assets", "attendance", "audit_events", "budget_lines",
    "communication_opt_outs", "competition_editions", "competitions", "consent_forms",
    "consent_requests", "consents", "exam_windows", "guardians", "import_batches", "insights",
    "magic_links", "media_assets", "media_subjects", "message_deliveries", "message_templates",
    "messages", "outbox", "portal_notifications", "refresh_tokens", "report_jobs",
    "result_participants", "results", "role_assignments", "seasons", "skill_awards", "skill_goals",
    "skill_requirements", "skills", "squad_memberships", "squad_target_editions", "squads",
    "student_flags", "student_guardians", "students", "training_sessions", "users",
    "webhook_deliveries", "webhook_subscriptions",
]  # fmt: skip


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid
        LANGUAGE sql STABLE AS $$
          SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
        $$;
        """
    )

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE so that even the table owner is subject to the policy.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING (tenant_id = app_current_tenant())
              WITH CHECK (tenant_id = app_current_tenant())
            """
        )

    # Tenants: anyone may resolve a slug (needed before login); only the current tenant
    # may update its own settings; the runtime role can never create or delete tenants.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_lookup ON tenants FOR SELECT USING (true)")
    op.execute(
        "CREATE POLICY tenant_self_update ON tenants FOR UPDATE "
        "USING (id = app_current_tenant()) WITH CHECK (id = app_current_tenant())"
    )
    # The owner role provisions tenants from the CLI; the runtime role has no INSERT grant.
    op.execute("CREATE POLICY tenant_provision ON tenants FOR INSERT WITH CHECK (true)")

    # Runtime grants.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"REVOKE INSERT, DELETE ON tenants FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON alembic_version FROM {APP_ROLE}")
    # Audit log is append-only for the application.
    op.execute(f"REVOKE UPDATE, DELETE ON audit_events FROM {APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION app_current_tenant() TO {APP_ROLE}")

    # Retention purge of audit events goes through a narrow definer function with a floor,
    # so a compromised app credential cannot erase recent history.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION purge_audit_events(older_than timestamptz) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE n integer;
        BEGIN
          IF older_than > now() - interval '365 days' THEN
            RAISE EXCEPTION 'audit events younger than one year cannot be purged';
          END IF;
          DELETE FROM audit_events WHERE tenant_id = app_current_tenant() AND created_at < older_than;
          GET DIAGNOSTICS n = ROW_COUNT;
          RETURN n;
        END $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION purge_audit_events(timestamptz) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION purge_audit_events(timestamptz) TO {APP_ROLE}")

    # ---- Media consent in the data layer (spec §5.7, §8.2) ----
    # security_invoker makes the views evaluate RLS as the querying role.
    op.execute(
        """
        CREATE VIEW student_media_consent WITH (security_invoker = true) AS
        SELECT s.id AS student_id,
               s.tenant_id,
               COALESCE((
                 SELECT c.decision = 'granted' AND c.withdrawn_at IS NULL
                 FROM consents c
                 WHERE c.student_id = s.id AND c.purpose = 'media' AND c.edition_id IS NULL
                 ORDER BY c.decided_at DESC
                 LIMIT 1
               ), false) AS has_media_consent
        FROM students s
        """
    )
    op.execute(
        """
        CREATE VIEW media_renderable WITH (security_invoker = true) AS
        SELECT m.*
        FROM media_assets m
        WHERE NOT EXISTS (
          SELECT 1 FROM media_subjects ms
          JOIN student_media_consent smc ON smc.student_id = ms.student_id
          WHERE ms.media_id = m.id AND NOT smc.has_media_consent
        )
        """
    )
    # Students who may be named or pictured in anything shown beyond their own family:
    # generated reports, newsletter exports, and messages to other families.
    op.execute(
        """
        CREATE VIEW publishable_students WITH (security_invoker = true) AS
        SELECT s.*
        FROM students s
        JOIN student_media_consent smc ON smc.student_id = s.id
        WHERE smc.has_media_consent AND s.anonymised_at IS NULL
        """
    )
    for view in ("student_media_consent", "media_renderable", "publishable_students"):
        op.execute(f"GRANT SELECT ON {view} TO {APP_ROLE}")

    # Future tables created by the owner are granted automatically.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )


def downgrade() -> None:
    for view in ("publishable_students", "media_renderable", "student_media_consent"):
        op.execute(f"DROP VIEW IF EXISTS {view}")
    op.execute("DROP FUNCTION IF EXISTS purge_audit_events(timestamptz)")
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for policy in ("tenant_lookup", "tenant_self_update", "tenant_provision"):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON tenants")
    op.execute("ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS app_current_tenant()")
