import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, MetaData, Numeric, String, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        uuid.UUID: UUID(as_uuid=True),
        dict[str, Any]: JSONB(),
        list[str]: ARRAY(String),
        list[uuid.UUID]: ARRAY(UUID(as_uuid=True)),
        list[int]: ARRAY(Integer),
        datetime: DateTime(timezone=True),
        date: Date,
        Decimal: Numeric(12, 2),
    }


def enum_column(enum_cls: type[StrEnum], **kw: Any):  # noqa: ANN201
    """Store enums as VARCHAR, validated on the Python side (migration-friendly)."""
    return mapped_column(
        SAEnum(
            enum_cls,
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            length=40,
            values_callable=lambda e: [m.value for m in e],
            name=f"{enum_cls.__name__.lower()}",
        ),
        **kw,
    )


class TenantScoped:
    """Every tenant table: UUID primary key, tenant_id under RLS, timestamps."""

    __rls__ = True

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(
            ForeignKey("tenants.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
            server_default=text("(current_setting('app.tenant_id', true))::uuid"),
        )

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow, onupdate=utcnow)
