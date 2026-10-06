from enum import StrEnum


class Role(StrEnum):
    TEACHER = "teacher"
    PROGRAMME_ADMIN = "programme_admin"
    LEADER = "leader"
    PARENT = "parent"
    STUDENT = "student"


class ScopeType(StrEnum):
    SCHOOL = "school"
    SQUAD = "squad"  # a teacher coaching one squad
    GUARDIAN = "guardian"  # a parent account bound to one guardian record
    STUDENT = "student"  # a student account bound to one student record


class Tier(StrEnum):
    SCHOOL = "school"
    INTER_SCHOOL = "inter_school"
    EMIRATE = "emirate"
    NATIONAL = "national"
    INTERNATIONAL = "international"


class EntryFormat(StrEnum):
    TEAM = "team"
    INDIVIDUAL = "individual"


class CompetitionStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    ARCHIVED = "archived"


class EnrolmentStatus(StrEnum):
    ACTIVE = "active"
    LEFT = "left"


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    SMS = "sms"
    IN_APP = "in_app"


class Language(StrEnum):
    EN = "en"
    AR = "ar"


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class AttendanceStatus(StrEnum):
    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"
    EXCUSED = "excused"


class AwardSource(StrEnum):
    RUBRIC = "rubric"  # criterion scored at a competition — high confidence
    TEACHER = "teacher"  # teacher verification at training — high
    ARTEFACT = "artefact"  # project artefact reviewed — medium
    SELF = "self"  # self-assessment — low, never counts without countersign


AWARD_SOURCE_CONFIDENCE = {
    AwardSource.RUBRIC: "high",
    AwardSource.TEACHER: "high",
    AwardSource.ARTEFACT: "medium",
    AwardSource.SELF: "low",
}


class AwardStatus(StrEnum):
    PROPOSED = "proposed"  # e.g. from a rubric import or self-assessment; does NOT count yet
    VERIFIED = "verified"  # confirmed by a named teacher; counts toward readiness
    REJECTED = "rejected"
    REVOKED = "revoked"


class GoalOrigin(StrEnum):
    COACH = "coach"
    GAP_SUGGESTED = "gap_suggested"


class GoalStatus(StrEnum):
    OPEN = "open"
    ACHIEVED = "achieved"
    DROPPED = "dropped"


class ConsentPurpose(StrEnum):
    MEDIA = "media"
    TRAVEL = "travel"
    FEE_AUTHORISATION = "fee_authorisation"
    COMMUNICATIONS = "communications"
    DATA_PROCESSING = "data_processing"


class ConsentDecision(StrEnum):
    GRANTED = "granted"
    DECLINED = "declined"


class ConsentRequestStatus(StrEnum):
    PENDING = "pending"
    GRANTED = "granted"
    DECLINED = "declined"
    EXPIRED = "expired"


class MessageType(StrEnum):
    SELECTION_NOTICE = "selection_notice"
    LOGISTICS = "logistics"
    CONSENT_REQUEST = "consent_request"
    RESULT_NOTIFICATION = "result_notification"
    PROGRESS_REPORT = "progress_report"
    ATTENDANCE_CONCERN = "attendance_concern"
    CELEBRATION = "celebration"
    NON_SELECTION = "non_selection"
    BEHAVIOUR_NOTE = "behaviour_note"
    EMERGENCY = "emergency"


# Negative news is never sent by the platform. These types can only be drafted and
# handed to a member of staff to send personally. This is deliberately a hard-coded
# constant and not tenant configuration (spec §7.3).
NEGATIVE_MESSAGE_TYPES: frozenset[MessageType] = frozenset(
    {MessageType.ATTENDANCE_CONCERN, MessageType.NON_SELECTION, MessageType.BEHAVIOUR_NOTE}
)

# Logistics-style messages are about an event, not a child — siblings collapse to one send.
FAMILY_DEDUPLICATED_TYPES: frozenset[MessageType] = frozenset(
    {MessageType.LOGISTICS, MessageType.SELECTION_NOTICE, MessageType.CONSENT_REQUEST, MessageType.EMERGENCY}
)


class TemplateStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    RETIRED = "retired"


class WhatsAppTemplateStatus(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class MessageStatus(StrEnum):
    DRAFT = "draft"
    RELEASED = "released"  # a named human approved it; dispatch may proceed
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    MANUAL_HANDOFF = "manual_handoff"  # negative news: staff send personally


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    HELD_QUIET_HOURS = "held_quiet_hours"
    THROTTLED = "throttled"
    BLOCKED_CONSENT = "blocked_consent"
    BLOCKED_OPT_OUT = "blocked_opt_out"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AssetCondition(StrEnum):
    NEW = "new"
    GOOD = "good"
    FAIR = "fair"
    NEEDS_REPAIR = "needs_repair"
    LOST = "lost"
    RETIRED = "retired"


class AssetRequestStatus(StrEnum):
    OPEN = "open"
    APPROVED = "approved"
    DECLINED = "declined"
    FULFILLED = "fulfilled"


class BudgetCategory(StrEnum):
    ENTRY_FEE = "entry_fee"
    TRANSPORT = "transport"
    KIT = "kit"
    COVER = "cover"
    ACCOMMODATION = "accommodation"
    OTHER = "other"


class ApprovalStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReportType(StrEnum):
    STUDENT_PROFILE = "student_profile"
    SQUAD_READINESS = "squad_readiness"
    SEASON_REVIEW = "season_review"
    COHORT_COVERAGE = "cohort_coverage"
    INSPECTION_EVIDENCE = "inspection_evidence"
    PLATEAU_STRETCH = "plateau_stretch"
    KIT_UTILISATION = "kit_utilisation"


class ExportFormat(StrEnum):
    PDF = "pdf"
    XLSX = "xlsx"
    CSV = "csv"
    HTML = "html"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FlagKind(StrEnum):
    PLATEAU = "plateau"
    STRETCH = "stretch"
    ATTENDANCE = "attendance"


class InsightStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


class ClaimType(StrEnum):
    MEASURED = "measured"  # a verified award, a recorded result
    INFERRED = "inferred"  # a recommendation, a flag, a readiness projection


class ImportStatus(StrEnum):
    PREVIEWED = "previewed"
    COMMITTED = "committed"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class WebhookEvent(StrEnum):
    RESULT_RECORDED = "result.recorded"
    SKILL_VERIFIED = "skill.verified"
    STUDENT_FLAGGED = "student.flagged"
