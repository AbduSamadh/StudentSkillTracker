"""Personalised per-student insights, generated per half-term (spec §6.3).

The rules decide what is true; the sentence is rendered only from the computed ``facts``.
No language model is used. If one is introduced later it must receive ``facts`` alone and
must not add any claim absent from them — ``phrase()`` is the single seam for that.
Every insight is a DRAFT until a teacher approves it; only approved insights reach parents.
Sentences use the student's name rather than pronouns, and the Arabic templates use
verbless constructions so they are not gendered.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Attendance,
    Insight,
    Skill,
    SkillAward,
    Student,
    TrainingSession,
)
from app.models.enums import AttendanceStatus, AwardStatus, ClaimType, FlagKind, InsightStatus
from app.models.skills import LEVELS
from app.services.flags import current_editions, evaluate_flags
from app.services.readiness import readiness_for
from app.services.recommendations import AR_MONTHS
from app.tenancy import TenantSettings

LEVELS_AR = {1: "ناشئ", 2: "متطور", 3: "متمكّن", 4: "متقدّم"}

# Half-term start dates (month, day) within an academic year beginning in August.
HALF_TERM_STARTS = [(8, 15), (10, 21), (1, 1), (2, 16), (4, 1), (5, 21)]


def half_term(d: date) -> tuple[str, date, date]:
    year0 = d.year if d.month >= 8 else d.year - 1
    starts = []
    for i, (m, day) in enumerate(HALF_TERM_STARTS):
        y = year0 if m >= 8 else year0 + 1
        starts.append((i + 1, date(y, m, day)))
    starts.append((7, date(year0 + 1, 8, 15)))
    for (n, start), (_, nxt) in zip(starts, starts[1:], strict=False):
        if start <= d < nxt:
            return f"{year0}-{str(year0 + 1)[2:]}-HT{n}", start, nxt - timedelta(days=1)
    # Before 15 Aug: summer, counts as the last half-term of the previous year.
    return half_term(date(year0, 7, 31)) if d.month < 8 else half_term(date(d.year, 8, 15))


@dataclass
class Draft:
    rule: str
    claim_type: ClaimType
    facts: dict


def phrase(rule: str, f: dict) -> tuple[str, str]:
    """Render a sentence from facts only. Every template uses only keys present in ``f``."""
    name = f["name"]
    if rule == "level_progress":
        en = (
            f"{name} has moved from {f['from_level']} to {f['to_level']} on “{f['skill_en']}” "
            f"across {f['evidence_count']} piece{'s' if f['evidence_count'] != 1 else ''} of verified evidence this term."
        )
        ar = (
            f"{name}: ارتقاء من مستوى «{f['from_level_ar']}» إلى «{f['to_level_ar']}» في «{f['skill_ar']}»، "
            f"استنادًا إلى {f['evidence_count']} من الأدلة المعتمدة هذا الفصل."
        )
        if f.get("next_skill_en"):
            en += (
                f" {name}'s next stretch is “{f['next_skill_en']}”, required for {f['next_edition']} "
                f"in {f['next_month_en']}."
            )
            ar += f" التحدّي التالي لـ{name}: «{f['next_skill_ar']}»، وهو مطلوب في {f['next_edition']} ({f['next_month_ar']})."
        return en, ar
    if rule == "new_verified_skills":
        n = f["count"]
        en = f"{name} earned {n} new verified skill{'s' if n != 1 else ''} this half-term, including “{f['example_en']}”."
        ar = f"{name}: {n} من المهارات الجديدة المعتمدة في هذا النصف من الفصل، منها «{f['example_ar']}»."
        return en, ar
    if rule == "participation_without_progress":
        en = (
            f"{name} has entered {f['events']} event{'s' if f['events'] != 1 else ''} and attended "
            f"{f['sessions']} session{'s' if f['sessions'] != 1 else ''} but has earned no new verified skill "
            f"{f['since_en']}. Worth a conversation — participation without progression usually means the "
            "challenge level is wrong."
        )
        ar = (
            f"{name}: مشاركة في {f['events']} من الفعاليات وحضور {f['sessions']} من الجلسات التدريبية، دون أي "
            f"مهارة جديدة معتمدة {f['since_ar']}. يستحق الأمر نقاشًا — فالمشاركة دون تقدّم تعني غالبًا أن مستوى التحدّي غير مناسب."
        )
        return en, ar
    if rule == "under_challenged":
        en = f"{name} is Advanced on every skill the current programme requires and is being under-challenged."
        ar = f"{name}: المستوى المتقدّم في جميع المهارات التي يتطلبها البرنامج الحالي، ما يعني الحاجة إلى تحدٍّ أكبر."
        return en, ar
    if rule == "attendance_strong":
        en = f"{name} attended {f['attended']} of {f['total']} training sessions this half-term."
        ar = f"{name}: حضور {f['attended']} من أصل {f['total']} جلسة تدريبية في هذا النصف من الفصل."
        return en, ar
    raise ValueError(rule)


async def draft_insights(
    session: AsyncSession, settings: TenantSettings, student: Student, today: date
) -> list[Draft]:
    period, start, end = half_term(today)
    name = student.first_name
    drafts: list[Draft] = []

    awards = (
        await session.scalars(
            select(SkillAward).where(SkillAward.student_id == student.id, SkillAward.status == AwardStatus.VERIFIED)
        )
    ).all()
    before: dict[uuid.UUID, int] = defaultdict(int)
    after: dict[uuid.UUID, int] = defaultdict(int)
    in_period: dict[uuid.UUID, list[SkillAward]] = defaultdict(list)
    for a in awards:
        if a.awarded_on < start:
            before[a.skill_id] = max(before[a.skill_id], a.level)
        if a.awarded_on <= end:
            after[a.skill_id] = max(after[a.skill_id], a.level)
        if start <= a.awarded_on <= end:
            in_period[a.skill_id].append(a)
    skills = {s.id: s for s in (await session.scalars(select(Skill).where(Skill.id.in_(list(after) or [uuid.UUID(int=0)])))).all()}

    # Rule: level_progress — largest jump from an existing level.
    progress = [
        (after[sid] - before[sid], sid) for sid in in_period if before[sid] >= 1 and after[sid] > before[sid]
    ]
    if progress:
        _, sid = max(progress, key=lambda p: (p[0], max(a.awarded_on for a in in_period[p[1]])))
        sk = skills[sid]
        facts = {
            "name": name,
            "skill_code": sk.code,
            "skill_en": sk.parent_label_en,
            "skill_ar": sk.parent_label_ar,
            "from_level": LEVELS[before[sid]],
            "to_level": LEVELS[after[sid]],
            "from_level_ar": LEVELS_AR[before[sid]],
            "to_level_ar": LEVELS_AR[after[sid]],
            "evidence_count": len(in_period[sid]),
            "award_ids": [str(a.id) for a in in_period[sid]],
        }
        editions = (await current_editions(session, [student.id], today)).get(student.id, {})
        for ed in sorted(editions.values(), key=lambda e: e.event_starts):
            res = (await readiness_for(session, [student.id], ed))[student.id]
            if res.gaps:
                g = res.gaps[0].requirement
                facts.update(
                    next_skill_en=g.parent_label_en,
                    next_skill_ar=g.parent_label_ar,
                    next_skill_code=g.code,
                    next_edition=ed.name,
                    next_month_en=f"{ed.event_starts:%B}",
                    next_month_ar=AR_MONTHS[ed.event_starts.month - 1],
                )
                break
        drafts.append(Draft("level_progress", ClaimType.MEASURED, facts))

    # Rule: new_verified_skills — skills first evidenced this half-term.
    new_skills = [sid for sid in in_period if before[sid] == 0]
    if new_skills:
        ex = skills[new_skills[0]]
        drafts.append(
            Draft(
                "new_verified_skills",
                ClaimType.MEASURED,
                {"name": name, "count": len(new_skills), "example_en": ex.parent_label_en,
                 "example_ar": ex.parent_label_ar, "skill_codes": sorted(skills[s].code for s in new_skills)},
            )
        )

    # Rules from flags: plateau and stretch (inferred).
    for f in await evaluate_flags(session, settings, [student], today):
        if f.kind == FlagKind.PLATEAU:
            last = f.facts.get("last_verified_award_on")
            drafts.append(
                Draft(
                    "participation_without_progress",
                    ClaimType.INFERRED,
                    {
                        "name": name,
                        "events": f.facts["events_entered"],
                        "sessions": f.facts["sessions_attended"],
                        "since_en": f"since {last:%B}" if last else "yet",
                        "since_ar": f"منذ {AR_MONTHS[last.month - 1]}" if last else "حتى الآن",
                        "rule": f.rule,
                    },
                )
            )
        elif f.kind == FlagKind.STRETCH:
            drafts.append(Draft("under_challenged", ClaimType.INFERRED, {"name": name, **f.facts, "rule": f.rule}))

    # Rule: attendance_strong — positive only. Attendance concerns are never sent automatically.
    rows = (
        await session.execute(
            select(Attendance.status)
            .join(TrainingSession, TrainingSession.id == Attendance.session_id)
            .where(Attendance.student_id == student.id, TrainingSession.starts_at >= start,
                   TrainingSession.starts_at <= end + timedelta(days=1))
        )
    ).scalars().all()
    total = sum(1 for st in rows if st != AttendanceStatus.EXCUSED)
    attended = sum(1 for st in rows if st in (AttendanceStatus.PRESENT, AttendanceStatus.LATE))
    if total >= 4 and attended / total >= 0.9:
        drafts.append(Draft("attendance_strong", ClaimType.MEASURED, {"name": name, "attended": attended, "total": total}))

    for d in drafts:
        d.facts["period"] = period
    return drafts


async def generate_for_student(
    session: AsyncSession, settings: TenantSettings, student: Student, today: date
) -> list[Insight]:
    """Create or refresh DRAFT insights. Approved/rejected ones are left as reviewed."""
    from app.audit import _jsonable

    period, _, _ = half_term(today)
    existing = {
        i.rule: i
        for i in (
            await session.scalars(select(Insight).where(Insight.student_id == student.id, Insight.period == period))
        ).all()
    }
    out = []
    for d in await draft_insights(session, settings, student, today):
        en, ar = phrase(d.rule, d.facts)
        ins = existing.get(d.rule)
        if ins is None:
            ins = Insight(student_id=student.id, period=period, rule=d.rule, claim_type=d.claim_type,
                          text_en=en, text_ar=ar, facts=_jsonable(d.facts))
            session.add(ins)
        elif ins.status == InsightStatus.DRAFT:
            ins.text_en, ins.text_ar, ins.facts, ins.claim_type = en, ar, _jsonable(d.facts), d.claim_type
        out.append(ins)
    await session.flush()
    return out
