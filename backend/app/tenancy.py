"""Per-school configuration stored on the tenant row.

Everything the spec says must be configurable by the school (tier weights, readiness
threshold, plateau window, quiet hours, message cap, retention...) lives here.
Reporting suppression thresholds can be raised by a school but never lowered below
the spec's floor (20 for percentages, 5 for cells).
"""

from datetime import time
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import Tier

MIN_BASE_FOR_PERCENT = 20
MIN_BASE_FOR_CELL = 5


class QuietHours(BaseModel):
    start: time = time(20, 0)
    end: time = time(7, 0)


class Branding(BaseModel):
    primary: str = "#0f766e"
    accent: str = "#f59e0b"
    logo_url: str | None = None


class Retention(BaseModel):
    """Months to keep each record type. Decided explicitly, purged by a scheduled job."""

    audit_events_months: int = 84  # 7 years
    message_deliveries_months: int = 36
    outbox_months: int = 3
    report_exports_months: int = 3
    import_batches_months: int = 12
    media_after_leaving_months: int = 12
    student_after_leaving_months: int = 36  # then anonymised, not deleted, to keep aggregates


class OidcConfig(BaseModel):
    issuer: str | None = None
    client_id: str | None = None
    client_secret_enc: str | None = None
    # amr values from the IdP that count as MFA (Entra: "mfa"; Google: "mfa"/"otp").
    mfa_amr_values: list[str] = Field(default_factory=lambda: ["mfa", "otp", "hwk", "swk"])


class TenantSettings(BaseModel):
    timezone: str = "Asia/Dubai"
    tier_weights: dict[Tier, Decimal] = Field(
        default_factory=lambda: {
            Tier.SCHOOL: Decimal("0.62"),
            Tier.INTER_SCHOOL: Decimal("0.75"),
            Tier.EMIRATE: Decimal("0.84"),
            Tier.NATIONAL: Decimal("1.00"),
            Tier.INTERNATIONAL: Decimal("1.18"),
        }
    )
    readiness_threshold: Decimal = Decimal("0.65")
    plateau_window_days: int = 90
    attendance_concern_threshold: Decimal = Decimal("0.60")  # below this rate over 6 sessions
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    weekly_message_cap: int = 3
    min_base_for_percent: int = MIN_BASE_FOR_PERCENT
    min_base_for_cell: int = MIN_BASE_FOR_CELL
    student_portal_min_year: int = 9
    branding: Branding = Field(default_factory=Branding)
    retention: Retention = Field(default_factory=Retention)
    oidc: OidcConfig = Field(default_factory=OidcConfig)
    # Inspection strands the evidence export maps skill domains onto.
    inspection_strands: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "Innovation, enterprise and entrepreneurship": ["Design", "Engineering", "AI"],
            "Learning skills — critical thinking and problem solving": ["Coding", "Robotics", "Science"],
            "Learning skills — collaboration and communication": ["Collaboration"],
        }
    )

    @field_validator("min_base_for_percent")
    @classmethod
    def _floor_percent(cls, v: int) -> int:
        return max(v, MIN_BASE_FOR_PERCENT)

    @field_validator("min_base_for_cell")
    @classmethod
    def _floor_cell(cls, v: int) -> int:
        return max(v, MIN_BASE_FOR_CELL)

    @field_validator("readiness_threshold")
    @classmethod
    def _threshold_range(cls, v: Decimal) -> Decimal:
        if not Decimal("0") < v <= Decimal("1"):
            raise ValueError("readiness_threshold must be in (0, 1]")
        return v

    def tier_weight(self, tier: Tier) -> Decimal:
        return self.tier_weights.get(tier, Decimal("1.00"))


def load_settings(raw: dict | None) -> TenantSettings:
    return TenantSettings.model_validate(raw or {})
