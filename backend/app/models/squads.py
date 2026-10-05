import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScoped, enum_column
from app.models.enums import AttendanceStatus, MembershipStatus


class Squad(TenantScoped, Base):
    """A coached group. Coaches are teacher role assignments scoped to the squad."""

    __tablename__ = "squads"

    name: Mapped[str] = mapped_column(String(160))
    season_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("seasons.id", ondelete="SET NULL"))
    discipline: Mapped[str | None] = mapped_column(String(40))
    lead_coach_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    ics_token: Mapped[str | None] = mapped_column(String(128), unique=True)

    memberships: Mapped[list["SquadMembership"]] = relationship(back_populates="squad")


class SquadTargetEdition(TenantScoped, Base):
    __tablename__ = "squad_target_editions"
    __table_args__ = (UniqueConstraint("squad_id", "edition_id"),)

    squad_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), index=True)
    edition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("competition_editions.id", ondelete="CASCADE"), index=True
    )


class SquadMembership(TenantScoped, Base):
    """Role predicts which skills get demonstrated (driver, programmer, presenter...)."""

    __tablename__ = "squad_memberships"
    __table_args__ = (UniqueConstraint("squad_id", "student_id"),)

    squad_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    role: Mapped[str | None] = mapped_column(String(60))
    is_reserve: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[MembershipStatus] = enum_column(MembershipStatus, default=MembershipStatus.ACTIVE)
    joined_on: Mapped[date | None]
    withdrawn_on: Mapped[date | None]
    withdrawal_reason: Mapped[str | None] = mapped_column(Text)

    squad: Mapped[Squad] = relationship(back_populates="memberships")


class TrainingSession(TenantScoped, Base):
    __tablename__ = "training_sessions"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    squad_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), index=True)
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime | None]
    location: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    skill_ids: Mapped[list[uuid.UUID]] = mapped_column(default=list)  # skills covered
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    client_modified_at: Mapped[datetime | None]


class Attendance(TenantScoped, Base):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("session_id", "student_id"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("training_sessions.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    status: Mapped[AttendanceStatus] = enum_column(AttendanceStatus)
    note: Mapped[str | None] = mapped_column(Text)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    client_modified_at: Mapped[datetime | None]


class Result(TenantScoped, Base):
    """One entry's outcome at one edition. Field size is mandatory for comparability; a missing
    field size is recorded as a data-quality flag, never silently defaulted."""

    __tablename__ = "results"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    edition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("competition_editions.id", ondelete="CASCADE"), index=True
    )
    squad_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("squads.id", ondelete="SET NULL"), index=True
    )
    entry_name: Mapped[str | None] = mapped_column(String(160))
    placement: Mapped[int | None] = mapped_column(Integer)
    field_size: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    max_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    rubric_scores: Mapped[dict[str, Any]] = mapped_column(default=dict)
    award_title: Mapped[str | None] = mapped_column(String(200))
    judge_feedback: Mapped[str | None] = mapped_column(Text)
    performance_index: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    data_quality_flags: Mapped[list[str]] = mapped_column(default=list)
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    client_modified_at: Mapped[datetime | None]
    released_at: Mapped[datetime | None]  # once released to parents, edits need admin override
    released_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    participants: Mapped[list["ResultParticipant"]] = relationship(
        back_populates="result", lazy="selectin", cascade="all, delete-orphan"
    )


class ResultParticipant(TenantScoped, Base):
    __tablename__ = "result_participants"
    __table_args__ = (UniqueConstraint("result_id", "student_id"),)

    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("results.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("squad_memberships.id", ondelete="SET NULL")
    )

    result: Mapped[Result] = relationship(back_populates="participants")
