"""Rule-based, explainable recommendations (spec §4.5). No machine learning.

Every recommendation carries the reason in one sentence a teacher can repeat to a parent,
plus the structured checks that produced it. A recommendation is an *inferred* claim and
is labelled as such wherever it is rendered.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BudgetLine,
    Competition,
    CompetitionEdition,
    ExamWindow,
    Season,
    Squad,
    SquadMembership,
    SquadTargetEdition,
    Student,
)
from app.models.enums import ApprovalStatus, CompetitionStatus, MembershipStatus
from app.services.clashes import exam_clash_for_year
from app.services.readiness import ReadinessResult, readiness_for, shared_gaps
from app.tenancy import TenantSettings

AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
ALMOST_READY_MARGIN = Decimal("0.15")


def fmt_date_en(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def fmt_date_ar(d: date) -> str:
    return f"{d.day} {AR_MONTHS[d.month - 1]} {d.year}"


@dataclass
class Check:
    key: str
    passed: bool
    detail_en: str
    detail_ar: str

    def as_dict(self) -> dict:
        return {"key": self.key, "passed": self.passed, "detail_en": self.detail_en, "detail_ar": self.detail_ar}


async def season_budget_remaining(session: AsyncSession) -> dict[uuid.UUID, Decimal | None]:
    """Envelope minus approved planned spend, per season. None = no envelope set."""
    seasons = (await session.scalars(select(Season))).all()
    committed = dict(
        (
            await session.execute(
                select(BudgetLine.season_id, func.coalesce(func.sum(BudgetLine.planned_amount), 0))
                .where(BudgetLine.status == ApprovalStatus.APPROVED)
                .group_by(BudgetLine.season_id)
            )
        ).all()
    )
    return {
        s.id: None if s.budget_envelope is None else Decimal(s.budget_envelope) - Decimal(committed.get(s.id, 0))
        for s in seasons
    }


def _year_range(ed: CompetitionEdition) -> str:
    lo, hi = ed.eligible_year_min, ed.eligible_year_max
    if lo and hi:
        return f"{lo}–{hi}"
    if lo:
        return f"{lo}+"
    if hi:
        return f"up to {hi}"
    return "all"


async def competition_recommendations(
    session: AsyncSession, settings: TenantSettings, student: Student, today: date
) -> dict:
    editions = (
        await session.scalars(
            select(CompetitionEdition)
            .join(Competition, Competition.id == CompetitionEdition.competition_id)
            .where(CompetitionEdition.event_starts >= today, Competition.status == CompetitionStatus.ACTIVE)
            .order_by(CompetitionEdition.event_starts)
        )
    ).all()
    windows = list((await session.scalars(select(ExamWindow))).all())
    budgets = await season_budget_remaining(session)
    threshold = settings.readiness_threshold
    recommended, almost, other = [], [], []

    for ed in editions:
        checks: list[Check] = []
        lo, hi = ed.eligible_year_min, ed.eligible_year_max
        eligible = (lo is None or student.year_group >= lo) and (hi is None or student.year_group <= hi)
        checks.append(
            Check(
                "eligible",
                eligible,
                f"Year {student.year_group} {'is' if eligible else 'is not'} eligible (Years {_year_range(ed)})",
                f"الصف {student.year_group} {'مؤهل' if eligible else 'غير مؤهل'} (الصفوف {_year_range(ed)})",
            )
        )
        if not eligible:
            continue  # ineligible editions are not shown at all

        res: ReadinessResult = (await readiness_for(session, [student.id], ed))[student.id]
        if res.score is None:
            checks.append(
                Check("readiness", False, "skill requirements are not catalogued yet",
                      "متطلبات المهارات لم تُحدَّد بعد")
            )
        else:
            ok = res.score >= threshold
            checks.append(
                Check(
                    "readiness",
                    ok,
                    f"readiness is {res.percent}% against the {int(threshold * 100)}% threshold",
                    f"نسبة الجاهزية {res.percent}% مقابل الحد المطلوب {int(threshold * 100)}%",
                )
            )

        opens, closes = ed.registration_opens, ed.registration_closes
        reg_open = (opens is None or opens <= today) and (closes is None or today <= closes)
        if reg_open:
            en = f"registration is open until {fmt_date_en(closes)}" if closes else "registration is open"
            ar = f"التسجيل مفتوح حتى {fmt_date_ar(closes)}" if closes else "التسجيل مفتوح"
        elif opens and opens > today:
            en, ar = f"registration opens {fmt_date_en(opens)}", f"يفتح التسجيل في {fmt_date_ar(opens)}"
        else:
            en, ar = "registration has closed", "أُغلق التسجيل"
        checks.append(Check("registration_open", reg_open, en, ar))

        fee = Decimal(ed.entry_fee) if ed.entry_fee is not None else None
        remaining = budgets.get(ed.season_id) if ed.season_id else None
        if fee is None or fee == 0:
            checks.append(Check("budget", True, "there is no entry fee", "لا توجد رسوم مشاركة"))
        elif remaining is None:
            checks.append(
                Check("budget", True, f"the {ed.currency} {fee:.0f} entry fee has no season envelope to check against",
                      f"رسوم المشاركة {fee:.0f} {ed.currency} دون ميزانية موسمية للمقارنة")
            )
        else:
            within = fee <= remaining
            checks.append(
                Check(
                    "budget",
                    within,
                    f"the {ed.currency} {fee:.0f} entry fee {'fits' if within else 'exceeds'} the remaining season budget",
                    f"رسوم المشاركة {fee:.0f} {ed.currency} {'ضمن' if within else 'تتجاوز'} الميزانية المتبقية للموسم",
                )
            )

        clash = await exam_clash_for_year(session, ed, student.year_group, windows)
        checks.append(
            Check(
                "no_exam_clash",
                clash is None,
                "no exam window clashes" if clash is None else f"it clashes with {clash.name}",
                "لا يوجد تعارض مع فترة الامتحانات" if clash is None else f"يتعارض مع {clash.name}",
            )
        )

        entry = {
            "edition_id": ed.id,
            "edition_name": ed.name,
            "competition_name": ed.competition.name,
            "tier": ed.tier.value,
            "event_starts": ed.event_starts,
            "readiness_percent": res.percent,
            "gap_count": len(res.gaps),
            "gaps": [line.as_dict() for line in res.gaps[:5]],
            "checks": [c.as_dict() for c in checks],
            "claim_type": "inferred",
        }
        failed = [c for c in checks if not c.passed]
        if not failed:
            entry["reason_en"] = "Recommended: " + _join_en([c.detail_en for c in checks]) + "."
            entry["reason_ar"] = "مُوصى به: " + "، و".join(c.detail_ar for c in checks) + "."
            recommended.append(entry)
        elif (
            [c.key for c in failed] == ["readiness"]
            and res.score is not None
            and res.score >= threshold - ALMOST_READY_MARGIN
        ):
            n = len(res.gaps)
            entry["reason_en"] = (
                f"Not yet: readiness is {res.percent}%, {n} skill{'s' if n != 1 else ''} away from the "
                f"{int(threshold * 100)}% threshold."
            )
            entry["reason_ar"] = (
                f"ليس بعد: نسبة الجاهزية {res.percent}%، وتفصله {n} مهارة عن الحد المطلوب {int(threshold * 100)}%."
            )
            almost.append(entry)
        else:
            entry["reason_en"] = "Not recommended: " + _join_en([c.detail_en for c in failed]) + "."
            entry["reason_ar"] = "غير مُوصى به: " + "، و".join(c.detail_ar for c in failed) + "."
            other.append(entry)

    return {
        "student_id": student.id,
        "threshold_percent": int(threshold * 100),
        "recommended": recommended,
        "almost_ready": almost,
        "not_recommended": other,
        "claim_type": "inferred",
    }


def _join_en(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


async def squad_readiness(
    session: AsyncSession, settings: TenantSettings, squad: Squad, today: date, edition_id: uuid.UUID | None = None
) -> dict:
    """Readiness per member for each upcoming target edition, the shared gap list, and a
    suggested focus for the next session."""
    members = (
        await session.execute(
            select(SquadMembership, Student)
            .join(Student, Student.id == SquadMembership.student_id)
            .where(SquadMembership.squad_id == squad.id, SquadMembership.status == MembershipStatus.ACTIVE)
        )
    ).all()
    student_ids = [s.id for _, s in members]
    names = {s.id: s.display_name for _, s in members}
    stmt = (
        select(CompetitionEdition)
        .join(SquadTargetEdition, SquadTargetEdition.edition_id == CompetitionEdition.id)
        .where(SquadTargetEdition.squad_id == squad.id)
        .order_by(CompetitionEdition.event_starts)
    )
    if edition_id:
        stmt = stmt.where(CompetitionEdition.id == edition_id)
    else:
        stmt = stmt.where(CompetitionEdition.event_ends >= today)
    editions = (await session.scalars(stmt)).all()
    threshold = settings.readiness_threshold

    from app.services.readiness import readiness_clusters

    per_edition = []
    focus_pool: dict[uuid.UUID, dict] = {}
    for ed in editions:
        results = await readiness_for(session, student_ids, ed)
        rs = list(results.values())
        gaps = shared_gaps(rs, threshold)
        for g in gaps:
            for s in g["students"]:
                s["name"] = names.get(s["student_id"])
            pool = focus_pool.setdefault(
                g["skill_id"], {**{k: g[k] for k in ("skill_id", "code", "name", "domain")}, "students": set(),
                                "unblocks": 0, "editions": []},
            )
            pool["students"].update(s["student_id"] for s in g["students"])
            pool["unblocks"] += g["unblocks"]
            pool["editions"].append(ed.name)
        clusters = readiness_clusters(rs, threshold)
        for c in clusters:
            c["students"] = [{"student_id": sid, "name": names.get(sid)} for sid in c.pop("student_ids")]
        per_edition.append(
            {
                "edition_id": ed.id,
                "edition_name": ed.name,
                "event_starts": ed.event_starts,
                "ready_count": sum(1 for r in rs if r.score is not None and r.score >= threshold),
                "member_count": len(rs),
                "students": sorted(
                    (
                        {"student_id": r.student_id, "name": names.get(r.student_id), "percent": r.percent,
                         "gap_count": len(r.gaps), "gap_codes": [g.requirement.code for g in r.gaps]}
                        for r in rs
                    ),
                    key=lambda x: -(x["percent"] or 0),
                ),
                "shared_gaps": gaps,
                "clusters": clusters,
            }
        )

    focus = sorted(focus_pool.values(), key=lambda f: (-f["unblocks"], -len(f["students"]), f["code"]))[:3]
    suggestions = []
    for f in focus:
        n = len(f["students"])
        suggestions.append(
            {
                "skill_id": f["skill_id"],
                "code": f["code"],
                "name": f["name"],
                "student_count": n,
                "unblocks": f["unblocks"],
                "reason_en": (
                    f"Cover {f['code']} ({f['name']}): {n} of {len(student_ids)} students have it as a gap"
                    + (f", and closing it would make {f['unblocks']} ready" if f["unblocks"] else "")
                    + f" for {', '.join(dict.fromkeys(f['editions']))}."
                ),
                "reason_ar": (
                    f"ركّز على {f['code']}: {n} من أصل {len(student_ids)} طلاب لديهم فجوة فيها"
                    + (f"، وسدّها سيجعل {f['unblocks']} منهم جاهزين" if f["unblocks"] else "")
                    + "."
                ),
                "claim_type": "inferred",
            }
        )
    return {
        "squad_id": squad.id,
        "squad_name": squad.name,
        "threshold_percent": int(threshold * 100),
        "editions": per_edition,
        "suggested_session_focus": suggestions,
    }
