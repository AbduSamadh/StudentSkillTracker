"""The seven standard reports (spec §6.2) and the leadership dashboard (§6.4).

Every number is built through ``Figure`` (denominators, suppression, measured/inferred).
Students without media consent never appear identifiably in a generated report: they are
pseudonymised ("Student A") but still counted in aggregates. The one exception is the
student profile, which is the subject's own record and contains no media.
"""

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import median

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Asset,
    AssetLoan,
    BudgetLine,
    CompetitionEdition,
    Result,
    ResultParticipant,
    Season,
    Skill,
    SkillAward,
    Squad,
    SquadMembership,
    Student,
    StudentFlag,
    User,
)
from app.models.enums import (
    AWARD_SOURCE_CONFIDENCE,
    ApprovalStatus,
    AssetCondition,
    AwardStatus,
    EnrolmentStatus,
    MembershipStatus,
    ReportType,
)
from app.models.skills import LEVELS
from app.permissions import Principal, staff_student_scope
from app.services.consent import media_consented_ids
from app.services.recommendations import competition_recommendations, squad_readiness
from app.services.reporting.suppression import Figure, ReportBody
from app.tenancy import TenantSettings


@dataclass
class ReportCtx:
    session: AsyncSession
    settings: TenantSettings
    principal: Principal
    tenant_name: str
    today: date


class Namer:
    """Pseudonymises students without media consent, consistently within one report."""

    def __init__(self, consented: set[uuid.UUID]):
        self.consented = consented
        self.aliases: dict[uuid.UUID, str] = {}

    def __call__(self, student_id: uuid.UUID, real: str) -> str:
        if student_id in self.consented:
            return real
        if student_id not in self.aliases:
            n = len(self.aliases)
            letter = chr(ord("A") + n % 26) + (str(n // 26) if n >= 26 else "")
            self.aliases[student_id] = f"Student {letter} (no media consent)"
        return self.aliases[student_id]


async def _namer(rc: ReportCtx, ids: list[uuid.UUID]) -> Namer:
    return Namer(await media_consented_ids(rc.session, ids))


async def current_season(session: AsyncSession, today: date) -> Season | None:
    s = await session.scalar(select(Season).where(Season.starts_on <= today, Season.ends_on >= today))
    if s is None:
        s = await session.scalar(select(Season).where(Season.starts_on <= today).order_by(Season.starts_on.desc()))
    return s


async def previous_season(session: AsyncSession, season: Season) -> Season | None:
    return await session.scalar(
        select(Season).where(Season.ends_on < season.starts_on).order_by(Season.starts_on.desc()).limit(1)
    )


async def _require_scope_all(rc: ReportCtx) -> None:
    if await staff_student_scope(rc.session, rc.principal) is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This report covers the whole school")


async def _active_roll(session: AsyncSession) -> list[Student]:
    return list((await session.scalars(select(Student).where(Student.enrolment_status == EnrolmentStatus.ACTIVE))).all())


async def _participants(session: AsyncSession, season: Season | None) -> set[uuid.UUID]:
    stmt = select(SquadMembership.student_id).join(Squad, Squad.id == SquadMembership.squad_id).where(
        SquadMembership.status == MembershipStatus.ACTIVE
    )
    if season is not None:
        stmt = stmt.where(Squad.season_id == season.id)
    return set(await session.scalars(stmt))


# ---------------- Student profile ----------------
async def student_profile(rc: ReportCtx, student_id: uuid.UUID) -> ReportBody:
    scope = await staff_student_scope(rc.session, rc.principal)
    if scope is not None and student_id not in scope:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
    s = await rc.session.get(Student, student_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
    body = ReportBody(title=f"Student profile — {s.display_name}",
                      subtitle=f"Year {s.year_group} · private to the student's family and staff · no media included")
    awards = (await rc.session.scalars(select(SkillAward).where(
        SkillAward.student_id == s.id, SkillAward.status == AwardStatus.VERIFIED).order_by(SkillAward.awarded_on))).all()
    verifiers = {u.id: u.display_name for u in (await rc.session.scalars(
        select(User).where(User.id.in_({a.verified_by_id for a in awards if a.verified_by_id} or {uuid.UUID(int=0)})))).all()}
    best: dict[uuid.UUID, SkillAward] = {}
    for a in awards:
        if a.skill_id not in best or a.level >= best[a.skill_id].level:
            best[a.skill_id] = a
    body.sections.append({
        "heading": "Skills earned (verified evidence)", "kind": "table", "claim_type": "measured",
        "columns": ["Domain", "Skill", "Level", "Evidence", "Confidence", "Verified by", "Date"],
        "rows": [[a.skill.domain, f"{a.skill.code} — {a.skill.name}", LEVELS[a.level], a.evidence_note or a.source.value,
                  AWARD_SOURCE_CONFIDENCE[a.source], verifiers.get(a.verified_by_id, "—"), a.awarded_on.isoformat()]
                 for a in sorted(best.values(), key=lambda a: (a.skill.domain, a.skill.code))],
        "note": f"{len(best)} skills evidenced from {len(awards)} verified awards. Self-assessments count only once countersigned.",
    })
    rows = (await rc.session.execute(
        select(Result, CompetitionEdition).join(ResultParticipant, ResultParticipant.result_id == Result.id)
        .join(CompetitionEdition, CompetitionEdition.id == Result.edition_id)
        .where(ResultParticipant.student_id == s.id).order_by(CompetitionEdition.event_starts))).all()
    body.sections.append({
        "heading": "Participation history", "kind": "table", "claim_type": "measured",
        "columns": ["Date", "Competition", "Tier", "Placement", "Performance index", "Recognition"],
        "rows": [[ed.event_starts.isoformat(), ed.name, ed.tier.value.replace("_", "-"),
                  f"{r.placement} of {r.field_size}" if r.placement and r.field_size else "field size not recorded",
                  f"{r.performance_index:.1f}" if r.performance_index is not None else "—", r.award_title or ""]
                 for r, ed in rows],
    })
    by_term: Counter = Counter()
    for a in awards:
        by_term[f"{a.awarded_on:%Y}-T{(a.awarded_on.month - 1) // 4 + 1}"] += 1
    body.sections.append({"heading": "Progression", "kind": "table", "claim_type": "measured",
                          "columns": ["Period", "Verified awards"], "rows": [[k, v] for k, v in sorted(by_term.items())]})
    recs = await competition_recommendations(rc.session, rc.settings, s, rc.today)
    body.sections.append({
        "heading": "Recommended next steps", "kind": "list", "claim_type": "inferred",
        "items": [r["reason_en"].replace("Recommended: ", f"{r['edition_name']}: ") for r in recs["recommended"][:3]]
        + [f"{r['edition_name']}: {r['reason_en']}" for r in recs["almost_ready"][:3]],
        "note": "Recommendations are inferred by rules from verified skills; they are not results.",
    })
    return body


# ---------------- Squad readiness ----------------
async def squad_readiness_report(rc: ReportCtx, squad_id: uuid.UUID, edition_id: uuid.UUID | None) -> ReportBody:
    if not rc.principal.whole_school and squad_id not in rc.principal.coached_squad_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Squad not found")
    squad = await rc.session.get(Squad, squad_id)
    if squad is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Squad not found")
    data = await squad_readiness(rc.session, rc.settings, squad, rc.today, edition_id)
    ids = [st["student_id"] for e in data["editions"] for st in e["students"]]
    name = await _namer(rc, ids)
    body = ReportBody(title=f"Squad readiness — {squad.name}",
                      subtitle=f"Readiness threshold {data['threshold_percent']}% · readiness is inferred from verified skills")
    for e in data["editions"]:
        body.sections.append({
            "heading": f"{e['edition_name']} ({e['event_starts']})", "kind": "figures",
            "figures": [body.figure(Figure.count(e["ready_count"], rc.settings, label="Students at or above threshold",
                                                 denominator=e["member_count"], denominator_label="squad members",
                                                 apply_cell_rule=False, claim_type="inferred"))],
        })
        body.sections.append({
            "heading": "Readiness per student", "kind": "table", "claim_type": "inferred",
            "columns": ["Student", "Readiness", "Gaps"],
            "rows": [[name(st["student_id"], st["name"]), f"{st['percent']}%" if st["percent"] is not None else "—",
                      ", ".join(st["gap_codes"]) or "none"] for st in e["students"]],
        })
        body.sections.append({
            "heading": "Shared gap list", "kind": "table", "claim_type": "inferred",
            "columns": ["Skill", "Required level", "Students with this gap", "Would become ready if closed"],
            "rows": [[f"{g['code']} — {g['name']}", LEVELS[g["required_level"]],
                      f"{g['student_count']} of {e['member_count']}", g["unblocks"]] for g in e["shared_gaps"]],
        })
    body.sections.append({
        "heading": "Suggested session focus", "kind": "list", "claim_type": "inferred",
        "items": [f["reason_en"] for f in data["suggested_session_focus"]] or ["No shared gaps — every member is ready."],
    })
    return body


# ---------------- Season review ----------------
async def _season_metrics(rc: ReportCtx, season: Season) -> dict:
    s = rc.session
    roll = await _active_roll(s)
    participants = await _participants(s, season)
    editions = list((await s.scalars(select(CompetitionEdition).where(CompetitionEdition.season_id == season.id))).all())
    ed_ids = [e.id for e in editions]
    results = list((await s.scalars(select(Result).where(Result.edition_id.in_(ed_ids or [uuid.UUID(int=0)])))).all())
    entrants = set(await s.scalars(select(ResultParticipant.student_id).where(
        ResultParticipant.result_id.in_([r.id for r in results] or [uuid.UUID(int=0)]))))
    awards = list((await s.scalars(select(SkillAward).where(
        SkillAward.status == AwardStatus.VERIFIED, SkillAward.awarded_on >= season.starts_on,
        SkillAward.awarded_on <= season.ends_on))).all())
    lines = list((await s.scalars(select(BudgetLine).where(BudgetLine.season_id == season.id))).all())
    planned = sum((Decimal(b.planned_amount) for b in lines if b.status == ApprovalStatus.APPROVED), Decimal(0))
    actual = sum((Decimal(b.actual_amount or 0) for b in lines if b.status == ApprovalStatus.APPROVED), Decimal(0))
    indexes = [float(r.performance_index) for r in results if r.performance_index is not None]
    return {
        "roll": len(roll), "participants": len(participants & {st.id for st in roll}), "editions": len(editions),
        "results": len(results), "entrants": entrants, "awards": awards,
        "students_with_new_award": len({a.student_id for a in awards} & participants),
        "planned": planned, "actual": actual, "envelope": season.budget_envelope,
        "median_index": median(indexes) if indexes else None, "indexed_results": len(indexes),
        "podiums": sum(1 for r in results if r.placement and r.placement <= 3),
        "missing_field_size": sum(1 for r in results if "missing_field_size" in (r.data_quality_flags or [])),
    }


async def season_review(rc: ReportCtx, season_id: uuid.UUID | None) -> ReportBody:
    await _require_scope_all(rc)
    season = await rc.session.get(Season, season_id) if season_id else await current_season(rc.session, rc.today)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No season found")
    m = await _season_metrics(rc, season)
    st = rc.settings
    body = ReportBody(title=f"Season review — {season.name}", subtitle=f"{season.starts_on} to {season.ends_on}")
    body.sections.append({"heading": "Participation", "kind": "figures", "figures": [
        body.figure(Figure.percent(m["participants"], m["roll"], st, label="Students in a programme squad",
                                   denominator_label="students on roll")),
        body.figure(Figure.count(m["editions"], st, label="Competition editions entered", apply_cell_rule=False)),
        body.figure(Figure.count(len(m["entrants"]), st, label="Students who competed", denominator=m["roll"],
                                 denominator_label="students on roll")),
    ]})
    body.sections.append({"heading": "Results", "kind": "figures", "figures": [
        body.figure(Figure.count(m["results"], st, label="Results recorded", apply_cell_rule=False)),
        body.figure(Figure.count(m["podiums"], st, label="Podium finishes (top 3)", denominator=m["results"],
                                 denominator_label="results", apply_cell_rule=False)),
        body.figure(Figure.number(m["median_index"], label="Median weighted performance index",
                                  denominator=m["indexed_results"], denominator_label="results with a field size",
                                  settings=st)),
        body.figure(Figure.count(m["missing_field_size"], st, label="Results missing field size (data quality)",
                                 denominator=m["results"], denominator_label="results", apply_cell_rule=False)),
    ]})
    body.sections.append({"heading": "Skills growth", "kind": "figures", "figures": [
        body.figure(Figure.count(len(m["awards"]), st, label="Verified skill awards", apply_cell_rule=False)),
        body.figure(Figure.percent(m["students_with_new_award"], m["participants"], st,
                                   label="Participants with at least one new verified skill",
                                   denominator_label="programme participants")),
    ]})
    body.sections.append({"heading": "Spend against budget", "kind": "figures", "figures": [
        body.figure(Figure.money(m["planned"], label="Approved planned spend", denominator=m["envelope"],
                                 denominator_label="season envelope")),
        body.figure(Figure.money(m["actual"], label="Actual spend", denominator=m["planned"],
                                 denominator_label="approved plan")),
    ]})
    prev = await previous_season(rc.session, season)
    if prev is None:
        body.sections.append({"heading": "Year-on-year", "kind": "text",
                              "text": "Not available: this is the first season on record. Trend reporting begins "
                                      "once a second season exists."})
    else:
        pm = await _season_metrics(rc, prev)
        body.sections.append({
            "heading": f"Year-on-year (vs {prev.name})", "kind": "table", "claim_type": "measured",
            "columns": ["Measure", prev.name, season.name],
            "rows": [
                ["Students in a programme squad", Figure.percent(pm["participants"], pm["roll"], st, label="prev").display,
                 Figure.percent(m["participants"], m["roll"], st, label="cur").display],
                ["Verified skill awards", len(pm["awards"]), len(m["awards"])],
                ["Results recorded", pm["results"], m["results"]],
                ["Podium finishes", pm["podiums"], m["podiums"]],
            ],
        })
    return body


# ---------------- Cohort coverage ----------------
async def cohort_coverage(rc: ReportCtx, season_id: uuid.UUID | None) -> ReportBody:
    await _require_scope_all(rc)
    season = await rc.session.get(Season, season_id) if season_id else await current_season(rc.session, rc.today)
    roll = await _active_roll(rc.session)
    participants = await _participants(rc.session, season)
    st = rc.settings
    body = ReportBody(title="Cohort coverage", subtitle=f"Season: {season.name if season else 'all'} · who takes part, "
                                                        "and where participation is absent")

    def breakdown(key, label: str) -> dict:  # noqa: ANN001
        groups: dict[str, list[Student]] = defaultdict(list)
        for s in roll:
            groups[str(key(s) or "Not recorded")].append(s)
        rows, absent = [], []
        for g, members in sorted(groups.items(), key=lambda kv: (len(kv[0]), kv[0])):
            n_part = sum(1 for s in members if s.id in participants)
            if n_part == 0:
                absent.append(g)
            rows.append([g, body.figure(Figure.count(len(members), st, label=f"{label} {g} roll"))["display"],
                         body.figure(Figure.count(n_part, st, label=f"{label} {g} participants"))["display"],
                         body.figure(Figure.percent(n_part, len(members), st, label=f"{label} {g} participation rate",
                                                    denominator_label=f"students in {label.lower()} {g}"))["display"]])
        return {"heading": f"Participation by {label.lower()}", "kind": "table", "claim_type": "measured",
                "columns": [label, "On roll", "Participating", "Rate"], "rows": rows,
                "note": ("No participation in: " + ", ".join(absent)) if absent else "Participation in every group."}

    body.sections.append(breakdown(lambda s: f"Year {s.year_group}", "Year group"))
    body.sections.append(breakdown(lambda s: s.gender, "Gender"))
    rows = (await rc.session.execute(
        select(Squad.discipline, func.count(func.distinct(SquadMembership.student_id)))
        .join(SquadMembership, SquadMembership.squad_id == Squad.id)
        .where(SquadMembership.status == MembershipStatus.ACTIVE, *([Squad.season_id == season.id] if season else []))
        .group_by(Squad.discipline))).all()
    present = {d for d, _ in rows}
    all_disciplines = set(await rc.session.scalars(select(Skill.domain).distinct()))
    body.sections.append({
        "heading": "Participation by discipline", "kind": "table", "claim_type": "measured",
        "columns": ["Discipline", "Participating students"],
        "rows": [[d or "Unspecified", body.figure(Figure.count(n, st, label=f"Discipline {d}"))["display"]] for d, n in rows],
        "note": ("No squad in: " + ", ".join(sorted(all_disciplines - present))) if all_disciplines - present else "",
    })
    return body


# ---------------- Inspection evidence ----------------
async def inspection_evidence(rc: ReportCtx, since: date | None) -> ReportBody:
    await _require_scope_all(rc)
    since = since or rc.today - timedelta(days=365)
    st = rc.settings
    body = ReportBody(title="Inspection evidence — innovation and learning skills",
                      subtitle=f"Verified evidence since {since}. Named examples include only students with media consent.")
    awards = (await rc.session.execute(
        select(SkillAward, Skill, Student).join(Skill, Skill.id == SkillAward.skill_id)
        .join(Student, Student.id == SkillAward.student_id)
        .where(SkillAward.status == AwardStatus.VERIFIED, SkillAward.awarded_on >= since,
               Student.enrolment_status == EnrolmentStatus.ACTIVE))).all()
    consented = await media_consented_ids(rc.session, list({s.id for _, _, s in awards}))
    verifiers = {u.id: u.display_name for u in (await rc.session.scalars(select(User).where(
        User.id.in_({a.verified_by_id for a, _, _ in awards if a.verified_by_id} or {uuid.UUID(int=0)})))).all()}
    roll = len(await _active_roll(rc.session))
    for strand, domains in st.inspection_strands.items():
        rows = [(a, sk, s) for a, sk, s in awards if sk.domain in domains]
        students = {s.id for _, _, s in rows}
        top = Counter(f"{sk.code} — {sk.name}" for _, sk, _ in rows).most_common(5)
        examples = sorted((r for r in rows if r[2].id in consented and r[0].level >= 3),
                          key=lambda r: (-r[0].level, r[0].awarded_on), reverse=False)[:5]
        body.sections.append({"heading": strand, "kind": "figures", "figures": [
            body.figure(Figure.count(len(rows), st, label=f"{strand}: verified awards", apply_cell_rule=False)),
            body.figure(Figure.percent(len(students), roll, st, label=f"{strand}: students with evidence",
                                       denominator_label="students on roll")),
        ]})
        body.sections.append({"heading": f"{strand}: most-evidenced skills", "kind": "table", "claim_type": "measured",
                              "columns": ["Skill", "Verified awards"], "rows": [[k, v] for k, v in top]})
        body.sections.append({
            "heading": f"{strand}: named examples", "kind": "list", "claim_type": "measured",
            "items": [f"{s.display_name} (Year {s.year_group}) — {LEVELS[a.level]} in {sk.code} ({sk.name}), "
                      f"verified {a.awarded_on:%d %b %Y} by {verifiers.get(a.verified_by_id, 'a teacher')}"
                      + (f": {a.evidence_note}" if a.evidence_note else "") for a, sk, s in examples]
            or ["No examples with media consent in this period."],
        })
    return body


# ---------------- Plateau and stretch ----------------
async def plateau_stretch(rc: ReportCtx) -> ReportBody:
    scope = await staff_student_scope(rc.session, rc.principal)
    stmt = select(StudentFlag, Student).join(Student, Student.id == StudentFlag.student_id).where(
        StudentFlag.resolved_at.is_(None))
    if scope is not None:
        stmt = stmt.where(Student.id.in_(scope or {uuid.UUID(int=0)}))
    rows = (await rc.session.execute(stmt.order_by(StudentFlag.kind, Student.family_name))).all()
    name = await _namer(rc, [s.id for _, s in rows])
    body = ReportBody(title="Plateau and stretch", subtitle="Students flagged by rule — each flag shows the rule that "
                                                            "triggered it. Flags are inferred, not measured.")
    for kind in ("stretch", "plateau", "attendance"):
        sel = [(f, s) for f, s in rows if f.kind.value == kind]
        body.sections.append({
            "heading": {"stretch": "Under-challenged (stretch)", "plateau": "Stalled (plateau)",
                        "attendance": "Attendance concern (staff only; never messaged automatically)"}[kind],
            "kind": "table", "claim_type": "inferred",
            "columns": ["Student", "Year", "Rule", "Detail", "Raised"],
            "rows": [[name(s.id, s.display_name), s.year_group, f.rule, f.explanation.replace(s.display_name, name(s.id, s.display_name)),
                      f.raised_at.date().isoformat()] for f, s in sel],
            "note": f"{len(sel)} student{'s' if len(sel) != 1 else ''} flagged.",
        })
    return body


# ---------------- Kit utilisation ----------------
async def kit_utilisation(rc: ReportCtx) -> ReportBody:
    await _require_scope_all(rc)
    assets = list((await rc.session.scalars(select(Asset))).all())
    loans = list((await rc.session.scalars(select(AssetLoan))).all())
    since = datetime.now(UTC) - timedelta(days=90)
    by_asset = Counter(loan.asset_id for loan in loans)
    recent = {loan.asset_id for loan in loans if loan.issued_at >= since}
    body = ReportBody(title="Kit utilisation", subtitle="Equipment usage, idle assets, and loss or damage by edition")
    cat_rows = defaultdict(lambda: [0, 0])
    for a in assets:
        cat_rows[a.category][0] += 1
        cat_rows[a.category][1] += by_asset.get(a.id, 0)
    body.sections.append({"heading": "Usage by category", "kind": "table", "claim_type": "measured",
                          "columns": ["Category", "Assets", "Loans"],
                          "rows": [[c, v[0], v[1]] for c, v in sorted(cat_rows.items())]})
    idle = [a for a in assets if a.id not in recent and a.condition not in (AssetCondition.RETIRED, AssetCondition.LOST)]
    body.sections.append({"heading": "Idle assets (no loan in 90 days)", "kind": "table", "claim_type": "measured",
                          "columns": ["Tag", "Name", "Category", "Condition"],
                          "rows": [[a.tag, a.name, a.category, a.condition.value] for a in idle],
                          "note": f"{len(idle)} of {len(assets)} assets idle."})
    editions = {e.id: e.name for e in (await rc.session.scalars(select(CompetitionEdition))).all()}
    names = {a.id: f"{a.tag} {a.name}" for a in assets}
    problems = [loan for loan in loans if loan.returned_at and (
        loan.return_condition in (AssetCondition.NEEDS_REPAIR, AssetCondition.LOST)
        or (loan.returned_quantity is not None and loan.returned_quantity < loan.quantity))]
    outstanding = [loan for loan in loans if loan.returned_at is None and loan.due_back_on and loan.due_back_on < rc.today]
    body.sections.append({
        "heading": "Loss and damage by edition", "kind": "table", "claim_type": "measured",
        "columns": ["Edition", "Asset", "Out", "Returned", "Condition"],
        "rows": [[editions.get(loan.edition_id, "—"), names.get(loan.asset_id, "?"), loan.quantity,
                  loan.returned_quantity if loan.returned_quantity is not None else loan.quantity,
                  loan.return_condition.value if loan.return_condition else "—"] for loan in problems],
    })
    body.sections.append({
        "heading": "Overdue returns", "kind": "table", "claim_type": "measured",
        "columns": ["Asset", "Edition", "Due back"],
        "rows": [[names.get(loan.asset_id, "?"), editions.get(loan.edition_id, "—"), loan.due_back_on.isoformat()]
                 for loan in outstanding if loan.due_back_on],
    })
    return body


async def build(rc: ReportCtx, report_type: ReportType, params: dict) -> ReportBody:
    def uid(k: str) -> uuid.UUID | None:
        return uuid.UUID(params[k]) if params.get(k) else None

    if report_type == ReportType.STUDENT_PROFILE:
        sid = uid("student_id")
        if sid is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "student_id is required")
        return await student_profile(rc, sid)
    if report_type == ReportType.SQUAD_READINESS:
        sq = uid("squad_id")
        if sq is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "squad_id is required")
        return await squad_readiness_report(rc, sq, uid("edition_id"))
    if report_type == ReportType.SEASON_REVIEW:
        return await season_review(rc, uid("season_id"))
    if report_type == ReportType.COHORT_COVERAGE:
        return await cohort_coverage(rc, uid("season_id"))
    if report_type == ReportType.INSPECTION_EVIDENCE:
        return await inspection_evidence(rc, date.fromisoformat(params["since"]) if params.get("since") else None)
    if report_type == ReportType.PLATEAU_STRETCH:
        return await plateau_stretch(rc)
    if report_type == ReportType.KIT_UTILISATION:
        return await kit_utilisation(rc)
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown report")


# ---------------- Leadership dashboard: six numbers ----------------
async def leader_dashboard(rc: ReportCtx) -> dict:
    s, st = rc.session, rc.settings
    season = await current_season(s, rc.today)
    roll = await _active_roll(s)
    roll_ids = {x.id for x in roll}
    participants = await _participants(s, season) & roll_ids
    skills_total = await s.scalar(select(func.count()).select_from(Skill).where(Skill.is_active.is_(True))) or 0
    evidenced = await s.scalar(
        select(func.count(func.distinct(SkillAward.skill_id))).join(Student, Student.id == SkillAward.student_id)
        .where(SkillAward.status == AwardStatus.VERIFIED, Student.enrolment_status == EnrolmentStatus.ACTIVE)) or 0
    lines = list((await s.scalars(select(BudgetLine).where(BudgetLine.season_id == season.id))).all()) if season else []
    actual = sum((Decimal(b.actual_amount or 0) for b in lines if b.status == ApprovalStatus.APPROVED), Decimal(0))
    pending_approval = sum(1 for b in lines if b.status == ApprovalStatus.PROPOSED)
    upcoming = list((await s.scalars(select(CompetitionEdition).where(
        CompetitionEdition.event_starts >= rc.today, CompetitionEdition.event_starts <= rc.today + timedelta(days=30))
        .order_by(CompetitionEdition.event_starts))).all())
    flags = (await s.execute(select(StudentFlag.kind, func.count()).where(StudentFlag.resolved_at.is_(None))
                             .group_by(StudentFlag.kind))).all()
    flagged_total = sum(n for _, n in flags)
    trend: dict
    prev = await previous_season(s, season) if season else None
    if season and prev:
        prev_n = len(await _participants(s, prev))
        trend = {"label": f"Programme participants: {prev.name} → {season.name}", "available": True,
                 "previous": Figure.count(prev_n, st, label=prev.name, apply_cell_rule=True).as_dict(),
                 "current": Figure.count(len(participants), st, label=season.name).as_dict()}
    else:
        trend = {"label": "Year-on-year participation", "available": False,
                 "reason": "Available once a second season exists."}
    return {
        "season": {"id": season.id, "name": season.name} if season else None,
        "participation": Figure.percent(len(participants), len(roll), st, label="Participation against school roll",
                                        denominator_label="students on roll").as_dict(),
        "skills_coverage": Figure.percent(evidenced, skills_total, st, label="Taxonomy skills with verified evidence",
                                          denominator_label="skills in the taxonomy", apply_base_rule=False).as_dict(),
        "spend": {**Figure.money(actual, label="Actual spend against season budget",
                                 denominator=season.budget_envelope if season else None,
                                 denominator_label="season envelope").as_dict(),
                  "lines_awaiting_approval": pending_approval},
        "upcoming_events": {"count": len(upcoming), "next": [
            {"edition_id": e.id, "name": e.name, "starts": e.event_starts, "tier": e.tier.value} for e in upcoming[:5]]},
        "flagged_students": {**Figure.count(flagged_total, st, label="Open flags needing attention").as_dict(),
                             "by_kind": {k.value: Figure.count(n, st, label=k.value).as_dict() for k, n in flags},
                             "claim_type": "inferred"},
        "trend": trend,
    }


__all__ = ["ReportCtx", "build", "leader_dashboard"]
