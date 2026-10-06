import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScoped, enum_column
from app.models.enums import Channel, EnrolmentStatus, Language


class Student(TenantScoped, Base):
    """Identity comes from the MIS (matched on external_mis_id), never typed twice."""

    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("tenant_id", "external_mis_id"),)

    external_mis_id: Mapped[str] = mapped_column(String(64))
    given_name: Mapped[str] = mapped_column(String(120))
    family_name: Mapped[str] = mapped_column(String(120))
    preferred_name: Mapped[str | None] = mapped_column(String(120))
    full_name_ar: Mapped[str | None] = mapped_column(String(240))
    date_of_birth: Mapped[date | None]
    gender: Mapped[str | None] = mapped_column(String(16))
    year_group: Mapped[int] = mapped_column(Integer, index=True)
    house: Mapped[str | None] = mapped_column(String(60))
    enrolment_status: Mapped[EnrolmentStatus] = enum_column(EnrolmentStatus, default=EnrolmentStatus.ACTIVE)
    enrolled_on: Mapped[date | None]
    left_on: Mapped[date | None]
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    mis_synced_at: Mapped[datetime | None]
    anonymised_at: Mapped[datetime | None]

    guardian_links: Mapped[list["StudentGuardian"]] = relationship(back_populates="student", lazy="selectin")

    @property
    def display_name(self) -> str:
        return f"{self.preferred_name or self.given_name} {self.family_name}"

    @property
    def first_name(self) -> str:
        return self.preferred_name or self.given_name


class Guardian(TenantScoped, Base):
    """Contact details are encrypted at field level; email_hash is a keyed blind index."""

    __tablename__ = "guardians"
    __table_args__ = (UniqueConstraint("tenant_id", "external_mis_id"),)

    external_mis_id: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(240))
    email_enc: Mapped[str | None] = mapped_column(String(1024))
    email_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    phone_enc: Mapped[str | None] = mapped_column(String(1024))
    whatsapp_enc: Mapped[str | None] = mapped_column(String(1024))
    preferred_channel: Mapped[Channel] = enum_column(Channel, default=Channel.EMAIL)
    language: Mapped[Language] = enum_column(Language, default=Language.EN)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    student_links: Mapped[list["StudentGuardian"]] = relationship(back_populates="guardian", lazy="selectin")


class StudentGuardian(TenantScoped, Base):
    """Many-to-many: siblings share guardians."""

    __tablename__ = "student_guardians"
    __table_args__ = (UniqueConstraint("student_id", "guardian_id"),)

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    guardian_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("guardians.id", ondelete="CASCADE"), index=True)
    relationship_label: Mapped[str] = mapped_column(String(40), default="parent")
    is_primary_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    receives_communications: Mapped[bool] = mapped_column(Boolean, default=True)

    student: Mapped[Student] = relationship(back_populates="guardian_links")
    guardian: Mapped[Guardian] = relationship(back_populates="student_links")
