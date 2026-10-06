import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScoped, enum_column
from app.models.enums import CompetitionStatus, EntryFormat, Tier


class Season(TenantScoped, Base):
    __tablename__ = "seasons"

    name: Mapped[str] = mapped_column(String(80))
    starts_on: Mapped[date]
    ends_on: Mapped[date]
    budget_envelope: Mapped[Decimal | None]
    currency: Mapped[str] = mapped_column(String(3), default="AED")


class ExamWindow(TenantScoped, Base):
    """Used by the clash detector and by competition recommendations."""

    __tablename__ = "exam_windows"

    name: Mapped[str] = mapped_column(String(120))
    starts_on: Mapped[date]
    ends_on: Mapped[date]
    # Empty means the window applies to every year group.
    year_groups: Mapped[list[int]] = mapped_column(default=list)


class Competition(TenantScoped, Base):
    """The recurring programme. Skill requirements live here and are inherited by editions."""

    __tablename__ = "competitions"

    name: Mapped[str] = mapped_column(String(200))
    name_ar: Mapped[str | None] = mapped_column(String(200))
    organiser: Mapped[str | None] = mapped_column(String(200))
    discipline: Mapped[str] = mapped_column(String(40))  # matches a skill domain where possible
    tier: Mapped[Tier] = enum_column(Tier)
    entry_format: Mapped[EntryFormat] = enum_column(EntryFormat, default=EntryFormat.TEAM)
    typical_field_size: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[CompetitionStatus] = enum_column(CompetitionStatus, default=CompetitionStatus.ACTIVE)
    proposed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    editions: Mapped[list["CompetitionEdition"]] = relationship(back_populates="competition")


class CompetitionEdition(TenantScoped, Base):
    """What a student actually enters: one season's run of a competition."""

    __tablename__ = "competition_editions"

    competition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("competitions.id", ondelete="CASCADE"), index=True
    )
    season_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("seasons.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    stage: Mapped[str | None] = mapped_column(String(80))  # e.g. "Regional qualifier", "National final"
    tier: Mapped[Tier] = enum_column(Tier)
    registration_opens: Mapped[date | None]
    registration_closes: Mapped[date | None]
    event_starts: Mapped[date]
    event_ends: Mapped[date]
    venue: Mapped[str | None] = mapped_column(String(300))
    entry_fee: Mapped[Decimal | None]
    currency: Mapped[str] = mapped_column(String(3), default="AED")
    expected_field_size: Mapped[int | None] = mapped_column(Integer)
    eligible_year_min: Mapped[int | None] = mapped_column(SmallInteger)
    eligible_year_max: Mapped[int | None] = mapped_column(SmallInteger)
    max_team_size: Mapped[int | None] = mapped_column(SmallInteger)
    external_registration_url: Mapped[str | None] = mapped_column(String(500))
    # [{key, label, max_score, skill_code, level_if_met, threshold (0..1)}]
    rubric: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    notes: Mapped[str | None] = mapped_column(Text)

    competition: Mapped[Competition] = relationship(back_populates="editions", lazy="joined")


class SkillRequirement(TenantScoped, Base):
    """'Skill required'. Rows with edition_id NULL belong to the competition and are inherited
    by every edition; an edition row overrides (or, with removed=True, drops) one skill."""

    __tablename__ = "skill_requirements"
    __table_args__ = (
        Index(
            "uq_skill_requirements_competition_skill",
            "competition_id",
            "skill_id",
            unique=True,
            postgresql_where=text("edition_id IS NULL"),
        ),
        Index(
            "uq_skill_requirements_edition_skill",
            "edition_id",
            "skill_id",
            unique=True,
            postgresql_where=text("edition_id IS NOT NULL"),
        ),
    )

    competition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("competitions.id", ondelete="CASCADE"), index=True
    )
    edition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competition_editions.id", ondelete="CASCADE"), index=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"))
    required_level: Mapped[int] = mapped_column(SmallInteger)
    weight: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("1.00"))
    is_core: Mapped[bool] = mapped_column(Boolean, default=False)
    removed: Mapped[bool] = mapped_column(Boolean, default=False)
