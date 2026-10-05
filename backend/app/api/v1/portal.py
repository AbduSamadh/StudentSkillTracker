"""Parent portal (mobile-first, no install) and student portal (spec §7.4, §2).

A parent sees only their own children: scope comes from the parent's role assignment to a
guardian record, never from IDs supplied by the client.
"""

import uuid
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.db import tenant_session
from app.deps import CtxDep
from app.models import (
    CommunicationOptOut,
    CompetitionEdition,
    Consent,
    ConsentForm,
    ConsentRequest,
    Guardian,
    Message,
    MessageDelivery,
    PortalNotification,
    Result,
    ResultParticipant,
    SkillAward,
    SquadMembership,
    SquadTargetEdition,
    Student,
)
from app.models.enums import (
    NEGATIVE_MESSAGE_TYPES,
    AwardStatus,
    Channel,
    ConsentDecision,
    ConsentPurpose,
    ConsentRequestStatus,
    DeliveryStatus,
    Language,
    MembershipStatus,
    MessageType,
)
from app.models.skills import LEVELS
from app.permissions import Cap, family_student_ids, not_found
from app.security import decode_scoped_token
from app.services.consent import has_consent, latest
from app.services.insights import LEVELS_AR
from app.services.recommendations import competition_recommendations

router = APIRouter(prefix="/portal", tags=["portal"])

PARENT_LEVEL_EN = {1: "just starting", 2: "building up", 3: "confident", 4: "excelling"}
PARENT_LEVEL_AR = {1: "في البداية", 2: "في طور التطوّر", 3: "متمكّن", 4: "متميّز"}
OPTABLE = [MessageType.SELECTION_NOTICE, MessageType.LOGISTICS, MessageType.CONSENT_REQUEST,
           MessageType.RESULT_NOTIFICATION, MessageType.PROGRESS_REPORT, MessageType.CELEBRATION]


async def _child(ctx, student_id: uuid.UUID) -> Student:  # noqa: ANN001
    if not (ctx.principal.can(Cap.PORTAL_PARENT) or ctx.principal.can(Cap.PORTAL_STUDENT)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Portal access only")
    if student_id not in await family_student_ids(ctx.session, ctx.principal):
        raise not_found("Student not found")
    s = await ctx.session.get(Student, student_id)
    if s is None:
        raise not_found("Student not found")
    return s


def _guardian_ids(ctx) -> set[uuid.UUID]:  # noqa: ANN001
    ctx.require(Cap.PORTAL_PARENT)
    return ctx.principal.guardian_ids


async def plain_language_profile(ctx, s: Student) -> list[dict]:  # noqa: ANN001
    awards = (await ctx.session.scalars(select(SkillAward).where(
        SkillAward.student_id == s.id, SkillAward.status == AwardStatus.VERIFIED))).all()
    best: dict[uuid.UUID, SkillAward] = {}
    for a in awards:
        if a.skill_id not in best or a.level > best[a.skill_id].level:
            best[a.skill_id] = a
    return [
        {
            "skill_id": a.skill_id,
            "domain": a.skill.domain,
            "label_en": a.skill.parent_label_en,
            "label_ar": a.skill.parent_label_ar,
            "level": a.level,
            "level_en": PARENT_LEVEL_EN[a.level],
            "level_ar": PARENT_LEVEL_AR[a.level],
            "since": a.awarded_on,
            "claim_type": "measured",
        }
        for a in sorted(best.values(), key=lambda a: (a.skill.domain, -a.level))
    ]


@router.get("/children/{student_id}")
async def child_overview(student_id: uuid.UUID, ctx: CtxDep) -> dict:
    s = await _child(ctx, student_id)
    today = datetime.now(UTC).date()
    upcoming = (
        await ctx.session.scalars(
            select(CompetitionEdition)
            .join(SquadTargetEdition, SquadTargetEdition.edition_id == CompetitionEdition.id)
            .join(SquadMembership, SquadMembership.squad_id == SquadTargetEdition.squad_id)
            .where(SquadMembership.student_id == s.id, SquadMembership.status == MembershipStatus.ACTIVE,
                   CompetitionEdition.event_ends >= today)
            .order_by(CompetitionEdition.event_starts)
        )
    ).unique().all()
    results = (
        await ctx.session.execute(
            select(Result, CompetitionEdition)
            .join(ResultParticipant, ResultParticipant.result_id == Result.id)
            .join(CompetitionEdition, CompetitionEdition.id == Result.edition_id)
            .where(ResultParticipant.student_id == s.id, Result.released_at.is_not(None))
            .order_by(CompetitionEdition.event_starts.desc())
        )
    ).all()
    consents = []
    for purpose in ConsentPurpose:
        c = await latest(ctx.session, s.id, purpose)
        consents.append({
            "purpose": purpose.value,
            "active": await has_consent(ctx.session, s, purpose),
            "decision": c.decision.value if c else None,
            "decided_at": c.decided_at if c else None,
            "withdrawn_at": c.withdrawn_at if c else None,
            "version": c.version if c else None,
            "can_withdraw": c is not None and c.decision == ConsentDecision.GRANTED and c.withdrawn_at is None,
        })
    pending = (await ctx.session.scalars(select(ConsentRequest).where(
        ConsentRequest.student_id == s.id, ConsentRequest.status == ConsentRequestStatus.PENDING))).all()
    ctx.audit("portal.child_read", "student", s.id)
    return {
        "student": {"id": s.id, "name": s.display_name, "year_group": s.year_group},
        "upcoming_events": [
            {"edition_id": e.id, "name": e.name, "competition": e.competition.name, "starts": e.event_starts,
             "ends": e.event_ends, "venue": e.venue, "entry_fee": e.entry_fee, "currency": e.currency}
            for e in upcoming
        ],
        "skills": await plain_language_profile(ctx, s),
        "results": [
            {"edition": e.name, "competition": e.competition.name, "date": e.event_starts, "placement": r.placement,
             "field_size": r.field_size, "award": r.award_title}
            for r, e in results
        ],
        "consents": consents,
        "pending_consent_requests": [
            {"id": r.id, "purpose": r.purpose.value, "edition_id": r.edition_id, "created_at": r.created_at}
            for r in pending
        ],
    }


@router.get("/messages")
async def my_messages(ctx: CtxDep) -> list[dict]:
    gids = _guardian_ids(ctx)
    rows = (
        await ctx.session.execute(
            select(MessageDelivery, Message)
            .join(Message, Message.id == MessageDelivery.message_id)
            .where(MessageDelivery.guardian_id.in_(gids or {uuid.UUID(int=0)}),
                   MessageDelivery.status.in_([DeliveryStatus.SENT, DeliveryStatus.DELIVERED, DeliveryStatus.READ]),
                   Message.message_type.not_in(list(NEGATIVE_MESSAGE_TYPES)))
            .order_by(MessageDelivery.sent_at.desc())
            .limit(100)
        )
    ).all()
    return [
        {"delivery_id": d.id, "type": m.message_type.value, "subject": d.rendered_subject, "body": d.rendered_body,
         "sent_at": d.sent_at, "channel": d.channel_used.value if d.channel_used else None, "opened_at": d.opened_at,
         "is_emergency": m.is_emergency}
        for d, m in rows
    ]


@router.post("/messages/{delivery_id}/read")
async def mark_read(delivery_id: uuid.UUID, ctx: CtxDep) -> dict:
    gids = _guardian_ids(ctx)
    d = await ctx.session.get(MessageDelivery, delivery_id)
    if d is None or d.guardian_id not in gids:
        raise not_found()
    now = datetime.now(UTC)
    if d.opened_at is None:
        d.opened_at = now
        if d.status in (DeliveryStatus.SENT, DeliveryStatus.DELIVERED):
            d.status = DeliveryStatus.READ
    for n in (await ctx.session.scalars(select(PortalNotification).where(PortalNotification.delivery_id == d.id))).all():
        n.read_at = n.read_at or now
    return {"ok": True}


class ConsentIn(BaseModel):
    student_id: uuid.UUID
    purpose: ConsentPurpose
    decision: ConsentDecision
    edition_id: uuid.UUID | None = None
    consent_request_id: uuid.UUID | None = None


async def _current_form(ctx, purpose: ConsentPurpose) -> ConsentForm | None:  # noqa: ANN001
    return await ctx.session.scalar(select(ConsentForm).where(
        ConsentForm.purpose == purpose, ConsentForm.is_current.is_(True)).order_by(ConsentForm.version.desc()).limit(1))


@router.get("/consent-forms/{purpose}")
async def consent_form(purpose: ConsentPurpose, ctx: CtxDep) -> dict:
    f = await _current_form(ctx, purpose)
    if f is None:
        raise not_found()
    return {"purpose": f.purpose.value, "version": f.version, "title_en": f.title_en, "body_en": f.body_en,
            "title_ar": f.title_ar, "body_ar": f.body_ar}


@router.post("/consents", status_code=201)
async def give_consent(body: ConsentIn, ctx: CtxDep) -> dict:
    """Verifiable parental consent: the parent is authenticated by a single-use link sent to
    the contact address the school holds, and the exact form version is recorded."""
    gids = _guardian_ids(ctx)
    s = await _child(ctx, body.student_id)
    link_guardian = next((link.guardian_id for link in s.guardian_links if link.guardian_id in gids), None)
    if link_guardian is None:
        raise not_found("Student not found")
    form = await _current_form(ctx, body.purpose)
    now = datetime.now(UTC)
    c = Consent(student_id=s.id, guardian_id=link_guardian, purpose=body.purpose, edition_id=body.edition_id,
                consent_form_id=form.id if form else None, version=form.version if form else 1,
                decision=body.decision, decided_at=now, method="portal_verified", recorded_by_id=ctx.user_id)
    ctx.session.add(c)
    await ctx.session.flush()
    if body.consent_request_id:
        req = await ctx.session.get(ConsentRequest, body.consent_request_id)
        if req and req.student_id == s.id and req.guardian_id == link_guardian:
            req.status = (ConsentRequestStatus.GRANTED if body.decision == ConsentDecision.GRANTED
                          else ConsentRequestStatus.DECLINED)
            req.responded_at, req.consent_id = now, c.id
    ctx.audit("consent.decide", "student", s.id, context={"purpose": body.purpose.value, "decision": body.decision.value,
                                                         "version": c.version, "edition_id": body.edition_id})
    return {"id": c.id, "decision": c.decision.value, "version": c.version}


class WithdrawIn(BaseModel):
    student_id: uuid.UUID
    purpose: ConsentPurpose
    edition_id: uuid.UUID | None = None


@router.post("/consents/withdraw")
async def withdraw_consent(body: WithdrawIn, ctx: CtxDep) -> dict:
    """Withdraw without emailing anyone. Takes effect immediately: the next send is blocked."""
    gids = _guardian_ids(ctx)
    s = await _child(ctx, body.student_id)
    c = await latest(ctx.session, s.id, body.purpose, body.edition_id)
    now = datetime.now(UTC)
    if c is None or c.withdrawn_at is not None or c.decision != ConsentDecision.GRANTED:
        # Record an explicit decline so a default (e.g. communications) is overridden too.
        guardian_id = next((link.guardian_id for link in s.guardian_links if link.guardian_id in gids), None)
        c = Consent(student_id=s.id, guardian_id=guardian_id, purpose=body.purpose, edition_id=body.edition_id,
                    decision=ConsentDecision.DECLINED, decided_at=now, method="portal_verified",
                    recorded_by_id=ctx.user_id)
        ctx.session.add(c)
    else:
        c.withdrawn_at = now
    ctx.audit("consent.withdraw", "student", s.id, context={"purpose": body.purpose.value})
    return {"ok": True, "withdrawn_at": now}


class PrefsIn(BaseModel):
    preferred_channel: Channel | None = None
    language: Language | None = None


@router.get("/preferences")
async def get_preferences(ctx: CtxDep) -> dict:
    gids = _guardian_ids(ctx)
    out = []
    for gid in gids:
        g = await ctx.session.get(Guardian, gid)
        if g is None:
            continue
        opts = {o.category.value for o in (await ctx.session.scalars(select(CommunicationOptOut).where(
            CommunicationOptOut.guardian_id == g.id, CommunicationOptOut.opted_back_in_at.is_(None)))).all()}
        out.append({
            "guardian_id": g.id, "preferred_channel": g.preferred_channel.value, "language": g.language.value,
            "has_whatsapp": bool(g.whatsapp_enc), "has_phone": bool(g.phone_enc), "has_email": bool(g.email_enc),
            "categories": [{"category": c.value, "opted_out": c.value in opts} for c in OPTABLE],
        })
    return {"guardians": out}


@router.patch("/preferences")
async def set_preferences(body: PrefsIn, ctx: CtxDep) -> dict:
    gids = _guardian_ids(ctx)
    for gid in gids:
        g = await ctx.session.get(Guardian, gid)
        if g is None:
            continue
        if body.preferred_channel:
            g.preferred_channel = body.preferred_channel
        if body.language:
            g.language = body.language
    ctx.audit("portal.preferences", "user", ctx.user_id, context=body.model_dump(mode="json"))
    return await get_preferences(ctx)


class OptOutIn(BaseModel):
    category: MessageType
    opted_out: bool


async def _set_opt_out(session, guardian_id: uuid.UUID, category: MessageType, opted_out: bool, source: str) -> None:  # noqa: ANN001
    if category not in OPTABLE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "This category cannot be opted out of")
    active = await session.scalar(select(CommunicationOptOut).where(
        CommunicationOptOut.guardian_id == guardian_id, CommunicationOptOut.category == category,
        CommunicationOptOut.opted_back_in_at.is_(None)))
    now = datetime.now(UTC)
    if opted_out and active is None:
        session.add(CommunicationOptOut(guardian_id=guardian_id, category=category, opted_out_at=now, source=source))
    elif not opted_out and active is not None:
        active.opted_back_in_at = now


@router.post("/opt-outs")
async def set_opt_out(body: OptOutIn, ctx: CtxDep) -> dict:
    for gid in _guardian_ids(ctx):
        await _set_opt_out(ctx.session, gid, body.category, body.opted_out, "portal")
    ctx.audit("portal.opt_out", "user", ctx.user_id, context=body.model_dump(mode="json"))
    return {"ok": True}


class TokenIn(BaseModel):
    token: str


@router.post("/opt-out-link")
async def opt_out_by_link(body: TokenIn) -> dict:
    """One tap from the message itself; no sign-in needed. Honoured immediately."""
    try:
        data = decode_scoped_token(body.token, "opt_out")
        gid, tid, cat = uuid.UUID(data["gid"]), uuid.UUID(data["tid"]), MessageType(data["cat"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link is invalid or has expired") from exc
    async with tenant_session(tid) as session:
        if await session.get(Guardian, gid) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link is invalid or has expired")
        await _set_opt_out(session, gid, cat, True, "link")
        from app import audit

        audit.record(session, actor_user_id=None, actor_roles=["parent"], action="portal.opt_out_link",
                     subject_type="guardian", subject_id=gid, context={"category": cat.value})
        await session.commit()
    return {"ok": True, "category": cat.value}


# ---------------- Student portal ----------------
@router.get("/student")
async def student_home(ctx: CtxDep) -> dict:
    """Read-only skills profile and recommended next competitions (Year 9+ by default)."""
    ctx.require(Cap.PORTAL_STUDENT)
    sid = next(iter(ctx.principal.own_student_ids), None)
    if sid is None:
        raise not_found()
    s = await ctx.session.get(Student, sid)
    if s is None:
        raise not_found()
    if s.year_group < ctx.settings.student_portal_min_year:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The student portal opens in Year "
                            f"{ctx.settings.student_portal_min_year}")
    recs = await competition_recommendations(ctx.session, ctx.settings, s, datetime.now(UTC).date())
    ctx.audit("portal.student_read", "student", s.id)
    return {
        "student": {"id": s.id, "name": s.display_name, "year_group": s.year_group},
        "skills": await plain_language_profile(ctx, s),
        "recommended": recs["recommended"],
        "almost_ready": recs["almost_ready"],
        "levels": {"en": LEVELS, "ar": LEVELS_AR},
    }
