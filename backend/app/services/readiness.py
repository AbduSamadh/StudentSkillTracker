"""Readiness and gap analysis (spec §4.4).

    Readiness = Σ_{s∈R} w_s · min(L_earned_s / L_required_s, 1)  /  Σ_{s∈R} w_s

R is the edition's effective requirement set (competition rows overlaid by edition rows),
w the requirement weight, and levels are ordinal 1–4. Only VERIFIED, non-revoked awards
count — entering is not demonstrating.

The output is deliberately traceable: every line names the requirement and the exact
award that produced the earned level, so the UI can show why a score is what it is.
The gap list, not the score, is the valuable artefact.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CompetitionEdition, Skill, SkillAward, SkillRequirement
from app.models.enums import AWARD_SOURCE_CONFIDENCE, AwardStatus
from app.models.skills import LEVELS


@dataclass(frozen=True)
class Requirement:
    skill_id: uuid.UUID
    code: str
    name: str
    parent_label_en: str
    parent_label_ar: str
    domain: str
    required_level: int
    weight: Decimal
    is_core: bool
    requirement_id: uuid.UUID
    inherited: bool  # True when it comes from the competition, not the edition


@dataclass(frozen=True)
class EarnedEvidence:
    level: int
    award_id: uuid.UUID
    source: str
    confidence: str
    awarded_on: date
    verified_by_id: uuid.UUID | None
    evidence_note: str | None


@dataclass
class ReadinessLine:
    requirement: Requirement
    earned_level: int
    evidence: EarnedEvidence | None
    ratio: Decimal
    contribution: Decimal  # w · min(ratio, 1)

    @property
    def is_gap(self) -> bool:
        return self.earned_level < self.requirement.required_level

    @property
    def levels_short(self) -> int:
        return max(self.requirement.required_level - self.earned_level, 0)

    def as_dict(self) -> dict:
        r = self.requirement
        return {
            "skill_id": r.skill_id,
            "code": r.code,
            "name": r.name,
            "parent_label_en": r.parent_label_en,
            "parent_label_ar": r.parent_label_ar,
            "domain": r.domain,
            "required_level": r.required_level,
            "required_level_name": LEVELS[r.required_level],
            "weight": float(r.weight),
            "is_core": r.is_core,
            "inherited": r.inherited,
            "requirement_id": r.requirement_id,
            "earned_level": self.earned_level,
            "earned_level_name": LEVELS.get(self.earned_level, "Not yet evidenced"),
            "ratio": float(self.ratio),
            "contribution": float(self.contribution),
            "is_gap": self.is_gap,
            "levels_short": self.levels_short,
            "evidence": None
            if self.evidence is None
            else {
                "award_id": self.evidence.award_id,
                "level": self.evidence.level,
                "source": self.evidence.source,
                "confidence": self.evidence.confidence,
                "awarded_on": self.evidence.awarded_on,
                "verified_by_id": self.evidence.verified_by_id,
                "evidence_note": self.evidence.evidence_note,
            },
        }


@dataclass
class ReadinessResult:
    student_id: uuid.UUID
    edition_id: uuid.UUID
    score: Decimal | None  # None when the edition has no requirements catalogued
    lines: list[ReadinessLine] = field(default_factory=list)

    @property
    def gaps(self) -> list[ReadinessLine]:
        return sorted(
            (line for line in self.lines if line.is_gap),
            key=lambda line: (-line.requirement.weight, -line.levels_short, line.requirement.code),
        )

    @property
    def percent(self) -> int | None:
        if self.score is None:
            return None
        return int((self.score * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def is_ready(self, threshold: Decimal) -> bool:
        return self.score is not None and self.score >= threshold

    def as_dict(self) -> dict:
        return {
            "student_id": self.student_id,
            "edition_id": self.edition_id,
            "score": None if self.score is None else float(self.score),
            "percent": self.percent,
            "claim_type": "inferred",
            "formula": "Σ w·min(L_earned/L_required, 1) / Σ w",
            "total_weight": float(sum((line.requirement.weight for line in self.lines), Decimal(0))),
            "lines": [line.as_dict() for line in self.lines],
            "gaps": [line.as_dict() for line in self.gaps],
            "gap_count": len(self.gaps),
        }


def compute_readiness(
    student_id: uuid.UUID,
    edition_id: uuid.UUID,
    requirements: list[Requirement],
    earned: dict[uuid.UUID, EarnedEvidence],
) -> ReadinessResult:
    """Pure function — no I/O — so the formula is directly unit-testable."""
    result = ReadinessResult(student_id=student_id, edition_id=edition_id, score=None)
    total_w = Decimal(0)
    total = Decimal(0)
    for req in requirements:
        ev = earned.get(req.skill_id)
        level = ev.level if ev else 0
        ratio = Decimal(level) / Decimal(req.required_level)
        contribution = req.weight * min(ratio, Decimal(1))
        result.lines.append(
            ReadinessLine(
                requirement=req, earned_level=level, evidence=ev, ratio=ratio, contribution=contribution
            )
        )
        total_w += req.weight
        total += contribution
    if total_w > 0:
        result.score = (total / total_w).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    result.lines.sort(key=lambda line: (not line.is_gap, -line.requirement.weight, line.requirement.code))
    return result


# ---------------- Data loading ----------------
async def effective_requirements(session: AsyncSession, edition: CompetitionEdition) -> list[Requirement]:
    """Competition-level rows are inherited; edition rows override or remove them."""
    rows = (
        await session.execute(
            select(SkillRequirement, Skill)
            .join(Skill, Skill.id == SkillRequirement.skill_id)
            .where(
                SkillRequirement.competition_id == edition.competition_id,
                (SkillRequirement.edition_id.is_(None)) | (SkillRequirement.edition_id == edition.id),
            )
        )
    ).all()
    by_skill: dict[uuid.UUID, tuple[SkillRequirement, Skill]] = {}
    for req, skill in sorted(rows, key=lambda rs: rs[0].edition_id is not None):  # competition rows first
        by_skill[skill.id] = (req, skill)
    out = []
    for req, skill in by_skill.values():
        if req.removed:
            continue
        out.append(
            Requirement(
                skill_id=skill.id,
                code=skill.code,
                name=skill.name,
                parent_label_en=skill.parent_label_en,
                parent_label_ar=skill.parent_label_ar,
                domain=skill.domain,
                required_level=req.required_level,
                weight=Decimal(req.weight),
                is_core=req.is_core,
                requirement_id=req.id,
                inherited=req.edition_id is None,
            )
        )
    return sorted(out, key=lambda r: r.code)


async def earned_evidence(
    session: AsyncSession, student_ids: list[uuid.UUID], skill_ids: list[uuid.UUID] | None = None
) -> dict[uuid.UUID, dict[uuid.UUID, EarnedEvidence]]:
    """Highest verified level per (student, skill) and the award that evidences it."""
    if not student_ids:
        return {}
    stmt = select(SkillAward).where(
        SkillAward.student_id.in_(student_ids), SkillAward.status == AwardStatus.VERIFIED
    )
    if skill_ids is not None:
        if not skill_ids:
            return {}
        stmt = stmt.where(SkillAward.skill_id.in_(skill_ids))
    out: dict[uuid.UUID, dict[uuid.UUID, EarnedEvidence]] = defaultdict(dict)
    for a in (await session.scalars(stmt)).all():
        current = out[a.student_id].get(a.skill_id)
        candidate = EarnedEvidence(
            level=a.level,
            award_id=a.id,
            source=a.source.value,
            confidence=AWARD_SOURCE_CONFIDENCE[a.source],
            awarded_on=a.awarded_on,
            verified_by_id=a.verified_by_id,
            evidence_note=a.evidence_note,
        )
        if current is None or (candidate.level, candidate.awarded_on) > (current.level, current.awarded_on):
            out[a.student_id][a.skill_id] = candidate
    return out


async def readiness_for(
    session: AsyncSession, student_ids: list[uuid.UUID], edition: CompetitionEdition
) -> dict[uuid.UUID, ReadinessResult]:
    reqs = await effective_requirements(session, edition)
    earned = await earned_evidence(session, student_ids, [r.skill_id for r in reqs])
    return {sid: compute_readiness(sid, edition.id, reqs, earned.get(sid, {})) for sid in student_ids}


# ---------------- Squad view: the shared gap list ----------------
def shared_gaps(results: list[ReadinessResult], threshold: Decimal) -> list[dict]:
    """Skills that appear in the most gap lists, ranked by how many students closing the
    gap would push over the readiness threshold ("unblock")."""
    by_skill: dict[uuid.UUID, dict] = {}
    for res in results:
        if res.score is None:
            continue
        total_w = sum((line.requirement.weight for line in res.lines), Decimal(0))
        for line in res.gaps:
            req = line.requirement
            entry = by_skill.setdefault(
                req.skill_id,
                {
                    "skill_id": req.skill_id,
                    "code": req.code,
                    "name": req.name,
                    "domain": req.domain,
                    "required_level": req.required_level,
                    "weight": float(req.weight),
                    "students": [],
                    "unblocks": 0,
                },
            )
            gain = (req.weight - line.contribution) / total_w if total_w else Decimal(0)
            unblocks = res.score < threshold <= res.score + gain
            entry["students"].append(
                {
                    "student_id": res.student_id,
                    "earned_level": line.earned_level,
                    "levels_short": line.levels_short,
                    "would_unblock": unblocks,
                }
            )
            if unblocks:
                entry["unblocks"] += 1
    ranked = sorted(
        by_skill.values(),
        key=lambda e: (-e["unblocks"], -len(e["students"]), -e["weight"], e["code"]),
    )
    for e in ranked:
        e["student_count"] = len(e["students"])
    return ranked


def readiness_clusters(results: list[ReadinessResult], threshold: Decimal) -> list[dict]:
    """Groups not-yet-ready students by their exact gap set, so a coach can read
    'these four students are 2 skills away, and all four are missing the same skill'."""
    clusters: dict[tuple, dict] = {}
    for res in results:
        if res.score is None or res.score >= threshold:
            continue
        key = tuple(sorted(line.requirement.code for line in res.gaps))
        c = clusters.setdefault(key, {"missing_codes": list(key), "skills_away": len(key), "student_ids": []})
        c["student_ids"].append(res.student_id)
    return sorted(clusters.values(), key=lambda c: (c["skills_away"], -len(c["student_ids"])))
