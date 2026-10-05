import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScoped, enum_column
from app.models.enums import (
    AwardSource,
    AwardStatus,
    ClaimType,
    FlagKind,
    GoalOrigin,
    GoalStatus,
    InsightStatus,
)

LEVELS = {1: "Emerging", 2: "Developing", 3: "Secure", 4: "Advanced"}
LEVEL_CODES = {1: "emerging", 2: "developing", 3: "secure", 4: "advanced"}


class Skill(TenantScoped, Base):
    """The taxonomy. Seeded per tenant from app/seed/data/skills_taxonomy.csv."""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(300))
    domain: Mapped[str] = mapped_column(String(40), index=True)
    strand: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    # Parent-facing language is written separately from the internal taxonomy (spec §7.4).
    parent_label_en: Mapped[str] = mapped_column(String(300))
    parent_label_ar: Mapped[str] = mapped_column(String(300))
    typical_year_group: Mapped[int | None] = mapped_column(SmallInteger)
    framework_refs: Mapped[list[str]] = mapped_column(default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SkillAward(TenantScoped, Base):
    """'Skill earned' — evidenced, never inferred from participation alone.

    Only VERIFIED awards count toward readiness. A rubric import or a self-assessment
    creates a PROPOSED award; a named teacher confirms it.
    """

    __tablename__ = "skill_awards"
    __table_args__ = (
        Index("ix_skill_awards_student_skill", "student_id", "skill_id"),
        UniqueConstraint("tenant_id", "idempotency_key"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"))
    level: Mapped[int] = mapped_column(SmallInteger)
    awarded_on: Mapped[date]
    source: Mapped[AwardSource] = enum_column(AwardSource)
    status: Mapped[AwardStatus] = enum_column(AwardStatus, default=AwardStatus.PROPOSED)
    evidence_note: Mapped[str | None] = mapped_column(Text)
    artefact_media_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("results.id", ondelete="SET NULL"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("training_sessions.id", ondelete="SET NULL")
    )
    rubric_criterion: Mapped[str | None] = mapped_column(String(80))
    proposed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime | None]
    # Self-assessments require a teacher countersign to count.
    countersigned_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None]
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(80))

    skill: Mapped["Skill"] = relationship(lazy="joined")


class SkillGoal(TenantScoped, Base):
    """'Skill targeted' — coach-set or suggested by gap analysis."""

    __tablename__ = "skill_goals"

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"))
    target_level: Mapped[int] = mapped_column(SmallInteger)
    target_date: Mapped[date | None]
    origin: Mapped[GoalOrigin] = enum_column(GoalOrigin, default=GoalOrigin.COACH)
    status: Mapped[GoalStatus] = enum_column(GoalStatus, default=GoalStatus.OPEN)
    edition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competition_editions.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    skill: Mapped["Skill"] = relationship(lazy="joined")


class StudentFlag(TenantScoped, Base):
    """Plateau / stretch / attendance flags raised by rules, with the triggering rule shown."""

    __tablename__ = "student_flags"
    __table_args__ = (
        Index(
            "uq_student_flags_open",
            "student_id",
            "kind",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    kind: Mapped[FlagKind] = enum_column(FlagKind)
    rule: Mapped[str] = mapped_column(String(200))
    explanation: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict[str, Any]] = mapped_column(default=dict)
    raised_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Insight(TenantScoped, Base):
    """A per-student, per-half-term sentence plus the facts that produced it.

    The rule decides what is true; text is generated from ``facts`` only. A teacher reviews
    every insight before it can reach a parent.
    """

    __tablename__ = "insights"
    __table_args__ = (UniqueConstraint("student_id", "period", "rule"),)

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    period: Mapped[str] = mapped_column(String(20))  # e.g. "2026-27-HT1"
    rule: Mapped[str] = mapped_column(String(60))
    claim_type: Mapped[ClaimType] = enum_column(ClaimType)
    text_en: Mapped[str] = mapped_column(Text)
    text_ar: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[InsightStatus] = enum_column(InsightStatus, default=InsightStatus.DRAFT)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None]
    edited_text_en: Mapped[str | None] = mapped_column(Text)
    edited_text_ar: Mapped[str | None] = mapped_column(Text)
