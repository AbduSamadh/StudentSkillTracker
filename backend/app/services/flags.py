"""Stretch, plateau and attendance flags (spec §4.5). Each flag records the rule that
triggered it and the facts behind it, so the report can show *why*.

These are educational assessments of demonstrated skills and participation. The engine
never infers personality, temperament or character (spec §8.1).
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Attendance,
    CompetitionEdition,
    Result,
    ResultParticipant,
    SkillAward,
    SquadMembership,
    SquadTargetEdition,
    Student,
    StudentFlag,
    TrainingSession,
)
from app.models.enums import AttendanceStatus, AwardStatus, EnrolmentStatus, FlagKind, MembershipStatus
from app.services.readiness import earned_evidence, effective_requirements
from app.tenancy import TenantSettings

ATTENDANCE_LOOKBACK_SESSIONS = 6


@dataclass
class FlagFinding:
    student_id: uuid.UUID
    kind: FlagKind
    rule: str
    explanation: str
    facts: dict


async def current_editions(session: AsyncSession, student_ids: list[uuid.UUID], today: date) -> dict:
    rows = (
        await session.execute(
            select(SquadMembership.student_id, CompetitionEdition)
            .join(SquadTargetEdition, SquadTargetEdition.squad_id == SquadMembership.squad_id)
            .join(CompetitionEdition, CompetitionEdition.id == SquadTargetEdition.edition_id)
            .where(
                SquadMembership.student_id.in_(student_ids),
                SquadMembership.status == MembershipStatus.ACTIVE,
                CompetitionEdition.event_ends >= today,
            )
        )
    ).all()
    out: dict[uuid.UUID, dict[uuid.UUID, CompetitionEdition]] = defaultdict(dict)
    for sid, ed in rows:
        out[sid][ed.id] = ed
    return out


async def evaluate_flags(
    session: AsyncSession, settings: TenantSettings, students: list[Student], today: date
) -> list[FlagFinding]:
    findings: list[FlagFinding] = []
    active = [s for s in students if s.enrolment_status == EnrolmentStatus.ACTIVE]
    if not active:
        return findings
    ids = [s.id for s in active]
    names = {s.id: s.display_name for s in active}

    # ---- Stretch: Advanced on every required skill for all current competitions ----
    editions_by_student = await current_editions(session, ids, today)
    req_cache: dict[uuid.UUID, list] = {}
    for ed_map in editions_by_student.values():
        for ed in ed_map.values():
            if ed.id not in req_cache:
                req_cache[ed.id] = await effective_requirements(session, ed)
    all_skill_ids = list({r.skill_id for reqs in req_cache.values() for r in reqs})
    earned = await earned_evidence(session, list(editions_by_student), all_skill_ids)
    for sid, ed_map in editions_by_student.items():
        reqs = {r.skill_id: r for ed_id in ed_map for r in req_cache[ed_id]}
        if not reqs:
            continue
        ev = earned.get(sid, {})
        if all(ev.get(skill_id) is not None and ev[skill_id].level >= 4 for skill_id in reqs):
            findings.append(
                FlagFinding(
                    student_id=sid,
                    kind=FlagKind.STRETCH,
                    rule="Advanced (level 4) on every required skill for all current competitions",
                    explanation=(
                        f"{names[sid]} is Advanced on all {len(reqs)} skills required by "
                        f"{len(ed_map)} current competition{'s' if len(ed_map) != 1 else ''} — "
                        "consider a higher-tier competition."
                    ),
                    facts={
                        "editions": [{"id": e.id, "name": e.name} for e in ed_map.values()],
                        "required_skill_codes": sorted(r.code for r in reqs.values()),
                    },
                )
            )

    # ---- Plateau: no new verified award in the window despite continued participation ----
    window_days = settings.plateau_window_days
    window_start = today - timedelta(days=window_days)
    ws_dt = datetime.combine(window_start, datetime.min.time(), tzinfo=UTC)
    last_award = dict(
        (
            await session.execute(
                select(SkillAward.student_id, func.max(SkillAward.awarded_on))
                .where(SkillAward.student_id.in_(ids), SkillAward.status == AwardStatus.VERIFIED)
                .group_by(SkillAward.student_id)
            )
        ).all()
    )
    earliest_join = dict(
        (
            await session.execute(
                select(SquadMembership.student_id, func.min(func.coalesce(SquadMembership.joined_on, date(1970, 1, 1))))
                .where(SquadMembership.student_id.in_(ids), SquadMembership.status == MembershipStatus.ACTIVE)
                .group_by(SquadMembership.student_id)
            )
        ).all()
    )
    attended = dict(
        (
            await session.execute(
                select(Attendance.student_id, func.count())
                .join(TrainingSession, TrainingSession.id == Attendance.session_id)
                .where(
                    Attendance.student_id.in_(ids),
                    Attendance.status.in_([AttendanceStatus.PRESENT, AttendanceStatus.LATE]),
                    TrainingSession.starts_at >= ws_dt,
                )
                .group_by(Attendance.student_id)
            )
        ).all()
    )
    results_in_window = dict(
        (
            await session.execute(
                select(ResultParticipant.student_id, func.count(func.distinct(Result.id)))
                .join(Result, Result.id == ResultParticipant.result_id)
                .join(CompetitionEdition, CompetitionEdition.id == Result.edition_id)
                .where(ResultParticipant.student_id.in_(ids), CompetitionEdition.event_starts >= window_start)
                .group_by(ResultParticipant.student_id)
            )
        ).all()
    )
    for s in active:
        joined = earliest_join.get(s.id)
        if joined is None or joined > window_start:
            continue  # not enrolled in the programme for a full window yet
        if s.enrolled_on and s.enrolled_on > window_start:
            continue
        sessions_n = attended.get(s.id, 0)
        events_n = results_in_window.get(s.id, 0)
        if sessions_n == 0 and events_n == 0:
            continue  # not participating — that is a different conversation
        last = last_award.get(s.id)
        if last is not None and last >= window_start:
            continue
        since = f"since {last:%B %Y}" if last else "yet"
        findings.append(
            FlagFinding(
                student_id=s.id,
                kind=FlagKind.PLATEAU,
                rule=f"No new verified skill award in {window_days} days of active enrolment despite continued participation",
                explanation=(
                    f"{s.display_name} has attended {sessions_n} session{'s' if sessions_n != 1 else ''} and entered "
                    f"{events_n} event{'s' if events_n != 1 else ''} in the last {window_days} days but has earned no "
                    f"new verified skill {since}."
                ),
                facts={
                    "window_days": window_days,
                    "sessions_attended": sessions_n,
                    "events_entered": events_n,
                    "last_verified_award_on": last,
                },
            )
        )

    # ---- Attendance concern (staff only; messages about it are always manual) ----
    threshold = settings.attendance_concern_threshold
    rows = (
        await session.execute(
            select(Attendance.student_id, Attendance.status, TrainingSession.starts_at)
            .join(TrainingSession, TrainingSession.id == Attendance.session_id)
            .where(Attendance.student_id.in_(ids))
            .order_by(Attendance.student_id, TrainingSession.starts_at.desc())
        )
    ).all()
    recent: dict[uuid.UUID, list[AttendanceStatus]] = defaultdict(list)
    for sid, st, _ in rows:
        if len(recent[sid]) < ATTENDANCE_LOOKBACK_SESSIONS:
            recent[sid].append(st)
    for sid, statuses in recent.items():
        if len(statuses) < ATTENDANCE_LOOKBACK_SESSIONS:
            continue
        counted = [st for st in statuses if st != AttendanceStatus.EXCUSED]
        if not counted:
            continue
        rate = Decimal(sum(1 for st in counted if st in (AttendanceStatus.PRESENT, AttendanceStatus.LATE))) / len(counted)
        if rate < threshold:
            findings.append(
                FlagFinding(
                    student_id=sid,
                    kind=FlagKind.ATTENDANCE,
                    rule=f"Attendance below {int(threshold * 100)}% across the last {ATTENDANCE_LOOKBACK_SESSIONS} recorded sessions (excused absences excluded)",
                    explanation=f"{names[sid]} attended {int(rate * 100)}% of the last {len(counted)} unexcused sessions.",
                    facts={"rate_percent": int(rate * 100), "sessions_counted": len(counted)},
                )
            )
    return findings


async def sync_flags(
    session: AsyncSession, settings: TenantSettings, students: list[Student], today: date
) -> list[StudentFlag]:
    """Persist findings: raise new flags, refresh existing ones, resolve those no longer true.
    Returns newly raised flags (for the student.flagged webhook)."""
    findings = await evaluate_flags(session, settings, students, today)
    ids = [s.id for s in students]
    open_flags = {
        (f.student_id, f.kind): f
        for f in (
            await session.scalars(
                select(StudentFlag).where(StudentFlag.student_id.in_(ids), StudentFlag.resolved_at.is_(None))
            )
        ).all()
    }
    now = datetime.now(UTC)
    raised = []
    seen = set()
    for f in findings:
        key = (f.student_id, f.kind)
        seen.add(key)
        existing = open_flags.get(key)
        if existing:
            existing.rule, existing.explanation, existing.facts = f.rule, f.explanation, _jsonable(f.facts)
        else:
            flag = StudentFlag(
                student_id=f.student_id, kind=f.kind, rule=f.rule, explanation=f.explanation,
                facts=_jsonable(f.facts), raised_at=now,
            )
            session.add(flag)
            raised.append(flag)
    for key, flag in open_flags.items():
        if key not in seen:
            flag.resolved_at = now
    await session.flush()
    return raised


def _jsonable(v):  # noqa: ANN001, ANN202
    from app.audit import _jsonable as j

    return j(v)
