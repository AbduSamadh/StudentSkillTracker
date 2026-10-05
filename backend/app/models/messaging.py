import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, enum_column
from app.models.enums import (
    Channel,
    ConsentDecision,
    ConsentPurpose,
    ConsentRequestStatus,
    DeliveryStatus,
    Language,
    MessageStatus,
    MessageType,
    TemplateStatus,
    WhatsAppTemplateStatus,
)


class ConsentForm(TenantScoped, Base):
    """The text a guardian agrees to. Consent rows reference a specific version."""

    __tablename__ = "consent_forms"
    __table_args__ = (UniqueConstraint("tenant_id", "purpose", "version"),)

    purpose: Mapped[ConsentPurpose] = enum_column(ConsentPurpose)
    version: Mapped[int] = mapped_column(Integer)
    title_en: Mapped[str] = mapped_column(String(200))
    body_en: Mapped[str] = mapped_column(Text)
    title_ar: Mapped[str] = mapped_column(String(200))
    body_ar: Mapped[str] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class Consent(TenantScoped, Base):
    """A record, not a boolean: purpose, version, who decided, when, and withdrawal."""

    __tablename__ = "consents"
    __table_args__ = (Index("ix_consents_student_purpose", "student_id", "purpose"),)

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    guardian_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("guardians.id", ondelete="SET NULL"))
    purpose: Mapped[ConsentPurpose] = enum_column(ConsentPurpose)
    consent_form_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("consent_forms.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Travel / fee consent can be specific to one edition.
    edition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competition_editions.id", ondelete="SET NULL")
    )
    decision: Mapped[ConsentDecision] = enum_column(ConsentDecision)
    decided_at: Mapped[datetime]
    withdrawn_at: Mapped[datetime | None]
    method: Mapped[str] = mapped_column(String(40))  # portal_verified, paper, email, mis_import
    recorded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)


class ConsentRequest(TenantScoped, Base):
    __tablename__ = "consent_requests"

    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    guardian_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guardians.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[ConsentPurpose] = enum_column(ConsentPurpose)
    edition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competition_editions.id", ondelete="SET NULL")
    )
    status: Mapped[ConsentRequestStatus] = enum_column(ConsentRequestStatus, default=ConsentRequestStatus.PENDING)
    responded_at: Mapped[datetime | None]
    consent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("consents.id", ondelete="SET NULL"))


class MessageTemplate(TenantScoped, Base):
    """Versioned templates with named variables. WhatsApp business-initiated sends need a
    Meta-approved template, tracked in whatsapp_status."""

    __tablename__ = "message_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "key", "version"),)

    key: Mapped[str] = mapped_column(String(80))
    message_type: Mapped[MessageType] = enum_column(MessageType)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[TemplateStatus] = enum_column(TemplateStatus, default=TemplateStatus.DRAFT)
    subject_en: Mapped[str] = mapped_column(String(300))
    body_en: Mapped[str] = mapped_column(Text)
    subject_ar: Mapped[str] = mapped_column(String(300))
    body_ar: Mapped[str] = mapped_column(Text)
    variables: Mapped[list[str]] = mapped_column(default=list)
    whatsapp_template_name: Mapped[str | None] = mapped_column(String(120))
    whatsapp_status: Mapped[WhatsAppTemplateStatus] = enum_column(
        WhatsAppTemplateStatus, default=WhatsAppTemplateStatus.NOT_SUBMITTED
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None]


class Message(TenantScoped, Base):
    """A message to a set of families. Nothing dispatches until a named human releases it."""

    __tablename__ = "messages"

    message_type: Mapped[MessageType] = enum_column(MessageType)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("message_templates.id", ondelete="RESTRICT")
    )
    status: Mapped[MessageStatus] = enum_column(MessageStatus, default=MessageStatus.DRAFT)
    title: Mapped[str] = mapped_column(String(200))
    student_ids: Mapped[list[uuid.UUID]] = mapped_column(default=list)
    edition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competition_editions.id", ondelete="SET NULL")
    )
    squad_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("squads.id", ondelete="SET NULL"))
    variables: Mapped[dict[str, Any]] = mapped_column(default=dict)  # extra named variables
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    previewed_at: Mapped[datetime | None]
    released_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    released_at: Mapped[datetime | None]
    scheduled_for: Mapped[datetime | None]
    is_emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_reason: Mapped[str | None] = mapped_column(Text)


class MessageDelivery(TenantScoped, Base):
    """One family's copy of a message: exactly what they received, through which channel."""

    __tablename__ = "message_deliveries"
    __table_args__ = (
        UniqueConstraint("message_id", "guardian_id"),
        Index("ix_message_deliveries_guardian_sent", "guardian_id", "sent_at"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    guardian_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guardians.id", ondelete="CASCADE"))
    student_ids: Mapped[list[uuid.UUID]] = mapped_column(default=list)
    language: Mapped[Language] = enum_column(Language)
    channel_planned: Mapped[Channel] = enum_column(Channel)
    channel_used: Mapped[Channel | None] = enum_column(Channel, nullable=True)
    channels_attempted: Mapped[list[str]] = mapped_column(default=list)
    status: Mapped[DeliveryStatus] = enum_column(DeliveryStatus, default=DeliveryStatus.PENDING)
    template_version: Mapped[int | None] = mapped_column(Integer)
    rendered_subject: Mapped[str | None] = mapped_column(String(400))
    rendered_body: Mapped[str | None] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    hold_until: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    opened_at: Mapped[datetime | None]
    open_token: Mapped[str | None] = mapped_column(String(64), unique=True)


class CommunicationOptOut(TenantScoped, Base):
    """One-tap opt-out per message category, honoured immediately."""

    __tablename__ = "communication_opt_outs"
    __table_args__ = (
        Index(
            "uq_opt_out_active",
            "guardian_id",
            "category",
            unique=True,
            postgresql_where=text("opted_back_in_at IS NULL"),
        ),
    )

    guardian_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guardians.id", ondelete="CASCADE"))
    category: Mapped[MessageType] = enum_column(MessageType)
    opted_out_at: Mapped[datetime]
    opted_back_in_at: Mapped[datetime | None]
    source: Mapped[str] = mapped_column(String(20))  # portal, link, admin


class PortalNotification(TenantScoped, Base):
    """In-app channel and the guaranteed last fallback."""

    __tablename__ = "portal_notifications"

    guardian_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guardians.id", ondelete="CASCADE"), index=True)
    delivery_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("message_deliveries.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(400))
    body: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None]


class OutboxEntry(TenantScoped, Base):
    """Record of what a channel adapter would have sent, used when no provider is configured
    (development, staging with synthetic data) so nothing ever reaches a real family."""

    __tablename__ = "outbox"

    channel: Mapped[Channel] = enum_column(Channel)
    recipient: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(400))
    body: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
