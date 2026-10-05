import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.enums import (
    AttendanceStatus,
    AwardSource,
    AwardStatus,
    CompetitionStatus,
    EnrolmentStatus,
    EntryFormat,
    MembershipStatus,
    Tier,
)
from app.schemas.common import ORM


class StudentOut(ORM):
    id: uuid.UUID
    external_mis_id: str
    given_name: str
    family_name: str
    preferred_name: str | None
    full_name_ar: str | None
    display_name: str
    year_group: int
    house: str | None
    gender: str | None
    enrolment_status: EnrolmentStatus


class SkillOut(ORM):
    id: uuid.UUID
    code: str
    name: str
    domain: str
    strand: str
    description: str | None
    parent_label_en: str
    parent_label_ar: str
    typical_year_group: int | None
    framework_refs: list[str]
    is_active: bool


class SkillIn(BaseModel):
    code: str = Field(pattern=r"^[A-Z]{2,6}\.[A-Z]{2,8}\.\d{2}$")
    name: str
    domain: str
    strand: str
    description: str | None = None
    parent_label_en: str
    parent_label_ar: str
    typical_year_group: int | None = Field(default=None, ge=1, le=13)
    framework_refs: list[str] = []


class SeasonIn(BaseModel):
    name: str
    starts_on: date
    ends_on: date
    budget_envelope: Decimal | None = None
    currency: str = "AED"


class SeasonOut(ORM, SeasonIn):
    id: uuid.UUID


class ExamWindowIn(BaseModel):
    name: str
    starts_on: date
    ends_on: date
    year_groups: list[int] = []


class ExamWindowOut(ORM, ExamWindowIn):
    id: uuid.UUID


class CompetitionIn(BaseModel):
    name: str
    name_ar: str | None = None
    organiser: str | None = None
    discipline: str
    tier: Tier
    entry_format: EntryFormat = EntryFormat.TEAM
    typical_field_size: int | None = Field(default=None, ge=1)
    description: str | None = None
    website_url: str | None = None


class CompetitionOut(ORM, CompetitionIn):
    id: uuid.UUID
    status: CompetitionStatus
    proposed_by_id: uuid.UUID | None
    approved_by_id: uuid.UUID | None


class RubricCriterion(BaseModel):
    key: str
    label: str
    max_score: Decimal = Decimal(4)
    skill_code: str | None = None
    level_if_met: int | None = Field(default=None, ge=1, le=4)
    threshold: Decimal = Field(default=Decimal("0.75"), ge=0, le=1)


class EditionIn(BaseModel):
    season_id: uuid.UUID | None = None
    name: str
    stage: str | None = None
    tier: Tier | None = None  # defaults to the competition's tier
    registration_opens: date | None = None
    registration_closes: date | None = None
    event_starts: date
    event_ends: date
    venue: str | None = None
    entry_fee: Decimal | None = None
    currency: str = "AED"
    expected_field_size: int | None = Field(default=None, ge=1)
    eligible_year_min: int | None = Field(default=None, ge=1, le=13)
    eligible_year_max: int | None = Field(default=None, ge=1, le=13)
    max_team_size: int | None = None
    external_registration_url: str | None = None
    rubric: list[RubricCriterion] = []
    notes: str | None = None

    @field_validator("event_ends")
    @classmethod
    def _ends_after_start(cls, v: date, info) -> date:  # noqa: ANN001
        start = info.data.get("event_starts")
        if start and v < start:
            raise ValueError("event_ends must be on or after event_starts")
        return v


class EditionOut(ORM):
    id: uuid.UUID
    competition_id: uuid.UUID
    competition_name: str | None = None
    season_id: uuid.UUID | None
    name: str
    stage: str | None
    tier: Tier
    registration_opens: date | None
    registration_closes: date | None
    event_starts: date
    event_ends: date
    venue: str | None
    entry_fee: Decimal | None
    currency: str
    expected_field_size: int | None
    eligible_year_min: int | None
    eligible_year_max: int | None
    max_team_size: int | None
    external_registration_url: str | None
    rubric: list[dict[str, Any]]
    notes: str | None


class RequirementIn(BaseModel):
    skill_id: uuid.UUID | None = None
    skill_code: str | None = None
    required_level: int = Field(ge=1, le=4)
    weight: Decimal = Field(default=Decimal("1"), gt=0, le=10)
    is_core: bool = False
    removed: bool = False


class SquadIn(BaseModel):
    name: str
    season_id: uuid.UUID | None = None
    discipline: str | None = None
    lead_coach_user_id: uuid.UUID | None = None
    description: str | None = None


class SquadOut(ORM):
    id: uuid.UUID
    name: str
    season_id: uuid.UUID | None
    discipline: str | None
    lead_coach_user_id: uuid.UUID | None
    description: str | None
    is_active: bool


class MembershipIn(BaseModel):
    student_id: uuid.UUID
    role: str | None = None
    is_reserve: bool = False
    joined_on: date | None = None


class MembershipPatch(BaseModel):
    role: str | None = None
    is_reserve: bool | None = None
    withdraw: bool = False
    withdrawal_reason: str | None = None


class MembershipOut(ORM):
    id: uuid.UUID
    squad_id: uuid.UUID
    student_id: uuid.UUID
    role: str | None
    is_reserve: bool
    status: MembershipStatus
    joined_on: date | None
    withdrawn_on: date | None
    withdrawal_reason: str | None


class SessionIn(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=80)
    id: uuid.UUID | None = None  # client-generated ID for offline creation
    squad_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime | None = None
    location: str | None = None
    notes: str | None = None
    skill_ids: list[uuid.UUID] = []
    client_modified_at: datetime | None = None


class SessionOut(ORM):
    id: uuid.UUID
    squad_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime | None
    location: str | None
    notes: str | None
    skill_ids: list[uuid.UUID]
    idempotency_key: str | None


class AttendanceRecord(BaseModel):
    student_id: uuid.UUID
    status: AttendanceStatus
    note: str | None = None
    client_modified_at: datetime | None = None


class AttendanceIn(BaseModel):
    idempotency_key: str | None = None
    records: list[AttendanceRecord]


class AttendanceOut(ORM):
    id: uuid.UUID
    session_id: uuid.UUID
    student_id: uuid.UUID
    status: AttendanceStatus
    note: str | None
    client_modified_at: datetime | None


class ResultIn(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=80)
    id: uuid.UUID | None = None
    edition_id: uuid.UUID
    squad_id: uuid.UUID | None = None
    entry_name: str | None = None
    participant_ids: list[uuid.UUID] = Field(min_length=1)
    placement: int | None = Field(default=None, ge=1)
    field_size: int | None = Field(default=None, ge=1)
    score: Decimal | None = None
    max_score: Decimal | None = None
    rubric_scores: dict[str, Decimal] = {}
    award_title: str | None = None
    judge_feedback: str | None = None
    client_modified_at: datetime | None = None


class ResultPatch(BaseModel):
    placement: int | None = Field(default=None, ge=1)
    field_size: int | None = Field(default=None, ge=1)
    score: Decimal | None = None
    max_score: Decimal | None = None
    rubric_scores: dict[str, Decimal] | None = None
    award_title: str | None = None
    judge_feedback: str | None = None
    client_modified_at: datetime | None = None
    admin_override: bool = False
    override_reason: str | None = None


class ResultOut(ORM):
    id: uuid.UUID
    edition_id: uuid.UUID
    squad_id: uuid.UUID | None
    entry_name: str | None
    participant_ids: list[uuid.UUID] = []
    placement: int | None
    field_size: int | None
    score: Decimal | None
    max_score: Decimal | None
    rubric_scores: dict[str, Any]
    award_title: str | None
    judge_feedback: str | None
    performance_index: Decimal | None
    data_quality_flags: list[str]
    idempotency_key: str | None
    client_modified_at: datetime | None
    released_at: datetime | None
    proposed_award_count: int = 0


class AwardIn(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=80)
    student_id: uuid.UUID
    skill_id: uuid.UUID
    level: int = Field(ge=1, le=4)
    source: AwardSource = AwardSource.TEACHER
    awarded_on: date | None = None
    evidence_note: str | None = None
    artefact_media_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None


class QuickTagIn(BaseModel):
    """Tag one skill for several students in one tap (target: <30s per student per session)."""

    idempotency_key: str | None = None
    session_id: uuid.UUID | None = None
    skill_id: uuid.UUID
    level: int = Field(ge=1, le=4)
    student_ids: list[uuid.UUID] = Field(min_length=1)
    evidence_note: str | None = None


class BulkConfirmIn(BaseModel):
    confirm: list[uuid.UUID] = []
    reject: list[uuid.UUID] = []
    level_overrides: dict[uuid.UUID, int] = {}


class RevokeIn(BaseModel):
    reason: str = Field(min_length=3)


class AwardOut(ORM):
    id: uuid.UUID
    student_id: uuid.UUID
    skill_id: uuid.UUID
    skill_code: str | None = None
    skill_name: str | None = None
    level: int
    awarded_on: date
    source: AwardSource
    confidence: str | None = None
    status: AwardStatus
    evidence_note: str | None
    result_id: uuid.UUID | None
    session_id: uuid.UUID | None
    rubric_criterion: str | None
    verified_by_id: uuid.UUID | None
    verified_at: datetime | None
    countersigned_by_id: uuid.UUID | None
    revoked_at: datetime | None
    revoke_reason: str | None
    claim_type: str = "measured"


class GoalIn(BaseModel):
    skill_id: uuid.UUID
    target_level: int = Field(ge=1, le=4)
    target_date: date | None = None
    edition_id: uuid.UUID | None = None


class GoalOut(ORM):
    id: uuid.UUID
    student_id: uuid.UUID
    skill_id: uuid.UUID
    target_level: int
    target_date: date | None
    origin: str
    status: str
    edition_id: uuid.UUID | None
