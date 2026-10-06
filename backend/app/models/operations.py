import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, enum_column
from app.models.enums import (
    ApprovalStatus,
    AssetCondition,
    AssetRequestStatus,
    BudgetCategory,
    ExportFormat,
    ImportStatus,
    JobStatus,
    ReportType,
)


class Asset(TenantScoped, Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("tenant_id", "tag"),)

    tag: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(60))
    condition: Mapped[AssetCondition] = enum_column(AssetCondition, default=AssetCondition.GOOD)
    is_consumable: Mapped[bool] = mapped_column(Boolean, default=False)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=1)
    reorder_threshold: Mapped[int | None] = mapped_column(Integer)
    unit_cost: Mapped[Decimal | None]
    assigned_squad_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("squads.id", ondelete="SET NULL"))
    assigned_student_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL")
    )
    purchased_on: Mapped[date | None]
    service_due_on: Mapped[date | None]
    calibration_due_on: Mapped[date | None]
    notes: Mapped[str | None] = mapped_column(Text)


class AssetLoan(TenantScoped, Base):
    """Kit leaving the building — linked to an edition so event-day kit is reconcilable."""

    __tablename__ = "asset_loans"

    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    edition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competition_editions.id", ondelete="SET NULL"), index=True
    )
    squad_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("squads.id", ondelete="SET NULL"))
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    issued_at: Mapped[datetime]
    due_back_on: Mapped[date | None]
    returned_at: Mapped[datetime | None]
    returned_quantity: Mapped[int | None] = mapped_column(Integer)
    return_condition: Mapped[AssetCondition | None] = enum_column(AssetCondition, nullable=True)
    issued_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    returned_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)


class AssetRequest(TenantScoped, Base):
    """Teachers request kit; admins decide."""

    __tablename__ = "asset_requests"

    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    squad_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("squads.id", ondelete="SET NULL"))
    edition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competition_editions.id", ondelete="SET NULL")
    )
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    needed_by: Mapped[date | None]
    status: Mapped[AssetRequestStatus] = enum_column(AssetRequestStatus, default=AssetRequestStatus.OPEN)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None]


class BudgetLine(TenantScoped, Base):
    __tablename__ = "budget_lines"

    season_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("seasons.id", ondelete="SET NULL"), index=True
    )
    edition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competition_editions.id", ondelete="SET NULL"), index=True
    )
    category: Mapped[BudgetCategory] = enum_column(BudgetCategory)
    description: Mapped[str] = mapped_column(String(300))
    planned_amount: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    actual_amount: Mapped[Decimal | None]
    status: Mapped[ApprovalStatus] = enum_column(ApprovalStatus, default=ApprovalStatus.PROPOSED)
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(Text)


class MediaAsset(TenantScoped, Base):
    """Photos/video/artefacts. Never read this table directly to render media — use the
    ``media_renderable`` view, which drops anything showing a student without media consent."""

    __tablename__ = "media_assets"

    edition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competition_editions.id", ondelete="SET NULL"), index=True
    )
    storage_key: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    caption: Mapped[str | None] = mapped_column(String(500))
    taken_on: Mapped[date | None]
    is_artefact: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class MediaSubject(TenantScoped, Base):
    __tablename__ = "media_subjects"
    __table_args__ = (UniqueConstraint("media_id", "student_id"),)

    media_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)


class ReportJob(TenantScoped, Base):
    __tablename__ = "report_jobs"

    report_type: Mapped[ReportType] = enum_column(ReportType)
    format: Mapped[ExportFormat] = enum_column(ExportFormat)
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[JobStatus] = enum_column(JobStatus, default=JobStatus.QUEUED)
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    storage_key: Mapped[str | None] = mapped_column(String(500))
    filename: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]


class ImportBatch(TenantScoped, Base):
    """Upload -> map -> validate -> preview diff -> commit. Never commit on upload."""

    __tablename__ = "import_batches"

    source: Mapped[str] = mapped_column(String(40))  # csv, isams, engage, powerschool, veracross
    filename: Mapped[str | None] = mapped_column(String(300))
    content_hash: Mapped[str] = mapped_column(String(64))
    mapping: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[ImportStatus] = enum_column(ImportStatus, default=ImportStatus.PREVIEWED)
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    diff: Mapped[dict[str, Any]] = mapped_column(default=dict)
    rows: Mapped[dict[str, Any]] = mapped_column(default=dict)  # normalised rows awaiting commit
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    committed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    committed_at: Mapped[datetime | None]


class WebhookSubscription(TenantScoped, Base):
    __tablename__ = "webhook_subscriptions"

    url: Mapped[str] = mapped_column(String(500))
    events: Mapped[list[str]] = mapped_column(default=list)
    secret_enc: Mapped[str] = mapped_column(String(1024))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class WebhookDelivery(TenantScoped, Base):
    __tablename__ = "webhook_deliveries"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, delivered, failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_response_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
