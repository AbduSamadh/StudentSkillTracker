"""Parent messaging safeguards (spec §7.3).

* Nothing sends without a human release. ``Message.released_by_id`` is required for any
  delivery to leave the building; there is no configuration that bypasses it.
* Preview renders against three real recipients before release.
* Quiet hours and a weekly per-family cap, both re-checked at the moment of sending.
* Negative news is never sent by automation (NEGATIVE_MESSAGE_TYPES is a constant).
* Siblings collapse into one delivery per guardian.
* Every delivery records template version, channel, approver and status.
* One-tap opt-out per category; consent withdrawal blocks the next send.
* Emergency broadcast bypasses quiet hours and the cap only — Leader role, with a reason.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    CommunicationOptOut,
    CompetitionEdition,
    Guardian,
    Insight,
    Message,
    MessageDelivery,
    MessageTemplate,
    Result,
    ResultParticipant,
    Squad,
    Student,
    StudentGuardian,
    Tenant,
)
from app.models.enums import (
    Channel,
    DeliveryStatus,
    EnrolmentStatus,
    InsightStatus,
    Language,
    MessageType,
    WhatsAppTemplateStatus,
)
from app.security import create_scoped_token, decrypt_field
from app.services.consent import communications_blocked, media_consented_ids
from app.services.messaging.render import render
from app.services.recommendations import fmt_date_ar, fmt_date_en
from app.tenancy import TenantSettings

SENT_STATUSES = (DeliveryStatus.SENT, DeliveryStatus.DELIVERED, DeliveryStatus.READ)


@dataclass
class Family:
    guardian: Guardian
    students: list[Student] = field(default_factory=list)


@dataclass
class Verdict:
    status: DeliveryStatus  # PENDING means "clear to send now"
    reason: str | None = None
    hold_until: datetime | None = None
    sent_this_week: int = 0


async def resolve_families(session: AsyncSession, message: Message) -> list[Family]:
    """One entry per guardian, however many of their children the message concerns."""
    if not message.student_ids:
        return []
    rows = (
        await session.execute(
            select(Guardian, Student)
            .join(StudentGuardian, StudentGuardian.guardian_id == Guardian.id)
            .join(Student, Student.id == StudentGuardian.student_id)
            .where(
                Student.id.in_(message.student_ids),
                StudentGuardian.receives_communications.is_(True),
                Student.enrolment_status == EnrolmentStatus.ACTIVE,
            )
            .order_by(Guardian.full_name, Student.given_name)
        )
    ).all()
    families: dict[uuid.UUID, Family] = {}
    for g, s in rows:
        families.setdefault(g.id, Family(guardian=g)).students.append(s)
    return list(families.values())


def _names(names: list[str], lang: Language) -> str:
    if len(names) <= 1:
        return "".join(names)
    joiner = " و" if lang == Language.AR else " and "
    return (
        ", ".join(names[:-1]) + joiner + names[-1]
        if lang == Language.EN
        else "، ".join(names[:-1]) + joiner + names[-1]
    )


async def build_context(
    session: AsyncSession, tenant: Tenant, message: Message, family: Family, lang: Language
) -> dict:
    s = get_settings()
    first_names = [st.first_name for st in family.students]
    ctx: dict = {
        "guardian_name": family.guardian.full_name,
        "child_name": _names(first_names, lang),
        "child_names": _names(first_names, lang),
        "school_name": tenant.name_ar if lang == Language.AR and tenant.name_ar else tenant.name,
        "portal_link": f"{s.public_base_url}/portal",
        "opt_out_link": f"{s.public_base_url}/portal/opt-out?token="
        + create_scoped_token(
            "opt_out",
            {"gid": str(family.guardian.id), "tid": str(tenant.id), "cat": message.message_type.value},
            60 * 24 * 60,
        ),
    }
    if message.edition_id:
        ed = await session.get(CompetitionEdition, message.edition_id)
        if ed:
            ctx.update(
                edition_name=ed.name,
                competition_name=ed.competition.name if ed.competition else ed.name,
                event_date=fmt_date_ar(ed.event_starts)
                if lang == Language.AR
                else fmt_date_en(ed.event_starts),
                venue=ed.venue or "",
            )
    if message.squad_id:
        sq = await session.get(Squad, message.squad_id)
        if sq:
            ctx["squad_name"] = sq.name
    if (
        message.message_type in (MessageType.RESULT_NOTIFICATION, MessageType.CELEBRATION)
        and message.edition_id
    ):
        ctx.update(await _result_context(session, message, family, lang))
    if message.message_type == MessageType.PROGRESS_REPORT:
        ctx["progress_summary"] = await _progress_context(session, family, lang)
    ctx.update({k: str(v) for k, v in (message.variables or {}).items()})
    return ctx


async def _result_context(session: AsyncSession, message: Message, family: Family, lang: Language) -> dict:
    """Result lines for this family's children. Team-mates from other families are named only
    if they have media consent (data-layer view); others are counted, not named."""
    child_ids = [s.id for s in family.students]
    rows = (
        await session.execute(
            select(Result, ResultParticipant.student_id)
            .join(ResultParticipant, ResultParticipant.result_id == Result.id)
            .where(Result.edition_id == message.edition_id, ResultParticipant.student_id.in_(child_ids))
        )
    ).all()
    lines, mates_named, mates_hidden = [], set(), 0
    for r, _sid in rows:
        if r.placement and r.field_size:
            lines.append(
                f"المركز {r.placement} من أصل {r.field_size}"
                if lang == Language.AR
                else f"placed {r.placement} of {r.field_size}"
            )
        if r.award_title:
            lines.append(r.award_title)
        others = [p.student_id for p in r.participants if p.student_id not in child_ids]
        ok = await media_consented_ids(session, others)
        for st in (await session.scalars(select(Student).where(Student.id.in_(ok)))).all() if ok else []:
            mates_named.add(st.first_name)
        mates_hidden += len(set(others) - ok)
    team = sorted(mates_named)
    if mates_hidden:
        team.append(
            f"{mates_hidden} من زملاء الفريق"
            if lang == Language.AR
            else f"{mates_hidden} other{'s' if mates_hidden != 1 else ''}"
        )
    return {
        "result_summary": "؛ ".join(dict.fromkeys(lines))
        if lang == Language.AR
        else "; ".join(dict.fromkeys(lines)),
        "team_members": _names(team, lang),
    }


async def _progress_context(session: AsyncSession, family: Family, lang: Language) -> str:
    """Only teacher-approved insights reach a parent."""
    parts = []
    for st in family.students:
        rows = (
            await session.scalars(
                select(Insight)
                .where(Insight.student_id == st.id, Insight.status == InsightStatus.APPROVED)
                .order_by(Insight.created_at.desc())
                .limit(4)
            )
        ).all()
        for i in rows:
            if lang == Language.AR:
                parts.append(i.edited_text_ar or i.text_ar)
            else:
                parts.append(i.edited_text_en or i.text_en)
    return "\n".join(f"• {p}" for p in parts)


def render_for(template: MessageTemplate, lang: Language, context: dict) -> tuple[str, str]:
    if lang == Language.AR:
        return render(template.subject_ar, context), render(template.body_ar, context)
    return render(template.subject_en, context), render(template.body_en, context)


def channel_order(guardian: Guardian, template: MessageTemplate | None) -> list[Channel]:
    """Preferred channel, then email, then in-app (the guaranteed fallback)."""
    order = [guardian.preferred_channel, Channel.EMAIL, Channel.IN_APP]
    out: list[Channel] = []
    for ch in order:
        if ch in out:
            continue
        if ch == Channel.WHATSAPP and not (
            guardian.whatsapp_enc
            and template
            and template.whatsapp_template_name
            and template.whatsapp_status == WhatsAppTemplateStatus.APPROVED
        ):
            continue
        if ch == Channel.EMAIL and not guardian.email_enc:
            continue
        if ch == Channel.SMS and not guardian.phone_enc:
            continue
        out.append(ch)
    return out


def address_for(guardian: Guardian, channel: Channel) -> str | None:
    if channel == Channel.EMAIL:
        return decrypt_field(guardian.email_enc)
    if channel == Channel.WHATSAPP:
        return decrypt_field(guardian.whatsapp_enc)
    if channel == Channel.SMS:
        return decrypt_field(guardian.phone_enc)
    return str(guardian.id)


# ---------------- Safeguards ----------------
def quiet_hours_hold(settings: TenantSettings, now: datetime) -> datetime | None:
    """If ``now`` falls in quiet hours (school's local time), return when they end."""
    tz = ZoneInfo(settings.timezone)
    local = now.astimezone(tz)
    start, end = settings.quiet_hours.start, settings.quiet_hours.end
    t = local.time()
    in_quiet = (t >= start or t < end) if start > end else (start <= t < end)
    if not in_quiet:
        return None
    release = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if release <= local:
        release += timedelta(days=1)
    return release.astimezone(UTC)


async def sent_in_last_week(session: AsyncSession, guardian_id: uuid.UUID, now: datetime) -> list[datetime]:
    rows = await session.scalars(
        select(MessageDelivery.sent_at)
        .join(Message, Message.id == MessageDelivery.message_id)
        .where(
            MessageDelivery.guardian_id == guardian_id,
            MessageDelivery.status.in_(SENT_STATUSES),
            MessageDelivery.sent_at >= now - timedelta(days=7),
            Message.is_emergency.is_(False),
        )
        .order_by(MessageDelivery.sent_at)
    )
    return [r for r in rows if r is not None]


async def opted_out(session: AsyncSession, guardian_id: uuid.UUID, category: MessageType) -> bool:
    if category == MessageType.EMERGENCY:
        return False
    return (
        await session.scalar(
            select(func.count())
            .select_from(CommunicationOptOut)
            .where(
                CommunicationOptOut.guardian_id == guardian_id,
                CommunicationOptOut.category == category,
                CommunicationOptOut.opted_back_in_at.is_(None),
            )
        )
        or 0
    ) > 0


async def evaluate(
    session: AsyncSession,
    settings: TenantSettings,
    message: Message,
    guardian_id: uuid.UUID,
    student_ids: list[uuid.UUID],
    now: datetime,
) -> Verdict:
    for sid in student_ids:
        if await communications_blocked(session, sid, guardian_id):
            return Verdict(DeliveryStatus.BLOCKED_CONSENT, "Guardian has withdrawn communications consent")
    if await opted_out(session, guardian_id, message.message_type):
        return Verdict(
            DeliveryStatus.BLOCKED_OPT_OUT, f"Guardian opted out of {message.message_type.value} messages"
        )
    sent = await sent_in_last_week(session, guardian_id, now)
    if message.is_emergency:
        return Verdict(DeliveryStatus.PENDING, sent_this_week=len(sent))
    hold = quiet_hours_hold(settings, now)
    if hold is not None:
        return Verdict(
            DeliveryStatus.HELD_QUIET_HOURS, "Quiet hours", hold_until=hold, sent_this_week=len(sent)
        )
    if len(sent) >= settings.weekly_message_cap:
        return Verdict(
            DeliveryStatus.THROTTLED,
            f"Weekly cap of {settings.weekly_message_cap} reached",
            hold_until=sent[0] + timedelta(days=7),
            sent_this_week=len(sent),
        )
    return Verdict(DeliveryStatus.PENDING, sent_this_week=len(sent))


async def opted_out_count(session: AsyncSession, guardian_ids: list[uuid.UUID], category: MessageType) -> int:
    if not guardian_ids or category == MessageType.EMERGENCY:
        return 0
    return (
        await session.scalar(
            select(func.count(func.distinct(CommunicationOptOut.guardian_id))).where(
                CommunicationOptOut.guardian_id.in_(guardian_ids),
                CommunicationOptOut.category == category,
                CommunicationOptOut.opted_back_in_at.is_(None),
            )
        )
        or 0
    )


async def media_ok(session: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    return await media_consented_ids(session, ids)


async def tenant_of(session: AsyncSession) -> Tenant:
    tid = (await session.execute(text("SELECT app_current_tenant()"))).scalar_one()
    t = await session.get(Tenant, tid)
    assert t is not None
    return t
