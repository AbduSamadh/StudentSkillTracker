"""Skills engine maths: readiness, gap lists, shared gaps, performance index, suppression."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.services.performance import FLAG_MISSING_FIELD_SIZE, FLAG_PLACEMENT_EXCEEDS_FIELD, performance_index
from app.services.readiness import (
    EarnedEvidence,
    Requirement,
    compute_readiness,
    readiness_clusters,
    shared_gaps,
)
from app.services.reporting.suppression import Figure, ReportBody
from app.tenancy import TenantSettings


def req(code: str, level: int, weight: float = 1, core: bool = False) -> Requirement:
    return Requirement(
        skill_id=uuid.uuid5(uuid.NAMESPACE_DNS, code),
        code=code,
        name=code,
        parent_label_en=code,
        parent_label_ar=code,
        domain="Coding",
        required_level=level,
        weight=Decimal(str(weight)),
        is_core=core,
        requirement_id=uuid.uuid4(),
        inherited=True,
    )


def ev(level: int) -> EarnedEvidence:
    return EarnedEvidence(
        level=level,
        award_id=uuid.uuid4(),
        source="teacher",
        confidence="high",
        awarded_on=date(2026, 9, 1),
        verified_by_id=uuid.uuid4(),
        evidence_note="seen",
    )


R = [req("A", 3, 2), req("B", 2, 1), req("C", 4, 1)]


def test_readiness_formula_matches_spec() -> None:
    sid, eid = uuid.uuid4(), uuid.uuid4()
    earned = {R[0].skill_id: ev(3), R[1].skill_id: ev(1)}  # A met, B half, C missing
    res = compute_readiness(sid, eid, R, earned)
    # (2·1 + 1·0.5 + 1·0) / 4 = 0.625
    assert res.score == Decimal("0.6250")
    assert res.percent == 63
    assert [g.requirement.code for g in res.gaps] == ["B", "C"] or [g.requirement.code for g in res.gaps] == [
        "C",
        "B",
    ]


def test_exceeding_a_requirement_is_capped_at_one() -> None:
    res = compute_readiness(uuid.uuid4(), uuid.uuid4(), [req("A", 2)], {req("A", 2).skill_id: ev(4)})
    assert res.score == Decimal("1.0000") and res.gaps == []


def test_no_requirements_means_no_score_not_zero() -> None:
    res = compute_readiness(uuid.uuid4(), uuid.uuid4(), [], {})
    assert res.score is None and res.percent is None


def test_every_line_is_traceable_to_the_award_and_requirement() -> None:
    e = ev(3)
    res = compute_readiness(uuid.uuid4(), uuid.uuid4(), R, {R[0].skill_id: e})
    line = next(x for x in res.as_dict()["lines"] if x["code"] == "A")
    assert line["evidence"]["award_id"] == e.award_id
    assert line["requirement_id"] == R[0].requirement_id
    assert line["contribution"] == 2.0 and line["weight"] == 2.0


def test_gap_list_reports_levels_short() -> None:
    res = compute_readiness(uuid.uuid4(), uuid.uuid4(), R, {R[2].skill_id: ev(2)})
    gaps = {g.requirement.code: g.levels_short for g in res.gaps}
    assert gaps == {"A": 3, "B": 2, "C": 2}


def test_shared_gaps_rank_by_students_unblocked() -> None:
    threshold = Decimal("0.65")
    reqs = [req("CORE", 3, 3, True), req("X", 2, 1), req("Y", 2, 1)]
    full = {r.skill_id: ev(4) for r in reqs}
    students = []
    for _ in range(4):  # missing only CORE -> 2/5 = 40%, closing CORE -> 100%
        students.append(
            compute_readiness(
                uuid.uuid4(), uuid.uuid4(), reqs, {k: v for k, v in full.items() if k != reqs[0].skill_id}
            )
        )
    # one student missing only X -> 80%, already ready
    students.append(
        compute_readiness(
            uuid.uuid4(), uuid.uuid4(), reqs, {k: v for k, v in full.items() if k != reqs[1].skill_id}
        )
    )
    gaps = shared_gaps(students, threshold)
    assert gaps[0]["code"] == "CORE" and gaps[0]["student_count"] == 4 and gaps[0]["unblocks"] == 4
    clusters = readiness_clusters(students, threshold)
    assert clusters == [
        {"missing_codes": ["CORE"], "skills_away": 1, "student_ids": [s.student_id for s in students[:4]]}
    ]


@pytest.mark.parametrize(
    ("p", "f", "tier", "expected"),
    [
        (1, 300, "1.00", Decimal("100.00")),  # winning the largest reference field at national tier
        (1, 10, "1.00", Decimal("83.30")),  # 100·(0.72 + 0.28·log10(10)/log10(300))
        (10, 10, "1.00", Decimal("11.30")),  # last place still earns field-size credit
        (1, 1, "0.62", Decimal("44.64")),  # a field of one: placement score 1, no field credit
        (2, 28, "0.84", Decimal("71.98")),
    ],
)
def test_performance_index_formula(p: int, f: int, tier: str, expected: Decimal) -> None:
    assert performance_index(p, f, Decimal(tier)).value == expected


def test_missing_field_size_is_a_flag_not_a_default() -> None:
    res = performance_index(3, None, Decimal("1"))
    assert res.value is None and res.flags == [FLAG_MISSING_FIELD_SIZE]
    assert performance_index(12, 10, Decimal("1")).flags == [FLAG_PLACEMENT_EXCEEDS_FIELD]


def test_tier_weights_are_configurable() -> None:
    st = TenantSettings.model_validate({"tier_weights": {"national": "1.5"}})
    assert performance_index(1, 300, st.tier_weight("national")).value == Decimal("150.00")


# ---- Reporting principles ----
def test_percentage_on_base_below_twenty_is_withheld_with_reason() -> None:
    f = Figure.percent(13, 19, None, label="Improved", denominator_label="students with two or more results")
    assert f.withheld and f.value is None
    assert "below the minimum of 20" in f.withheld_reason
    assert f.as_dict()["denominator"] == 19


def test_percentage_carries_its_denominator() -> None:
    f = Figure.percent(15, 22, None, label="Improved", denominator_label="students with two or more results")
    assert f.value == 68
    assert f.display == "68% (15 of 22 students with two or more results)"


def test_small_cells_are_withheld() -> None:
    assert Figure.count(4, None, label="x").withheld
    assert not Figure.count(5, None, label="x").withheld
    assert not Figure.count(0, None, label="x").withheld
    assert Figure.percent(3, 40, None, label="x").withheld  # 3 students is an identifiable cell


def test_schools_can_raise_but_not_lower_thresholds() -> None:
    lowered = TenantSettings.model_validate({"min_base_for_percent": 5, "min_base_for_cell": 1})
    assert lowered.min_base_for_percent == 20 and lowered.min_base_for_cell == 5
    raised = TenantSettings.model_validate({"min_base_for_percent": 30})
    assert Figure.percent(20, 25, raised, label="x").withheld


def test_withheld_figures_are_listed_never_dropped() -> None:
    body = ReportBody(title="t")
    body.figure(Figure.percent(3, 10, None, label="Small group"))
    body.figure(Figure.percent(30, 40, None, label="Fine"))
    assert body.withheld == [{"label": "Small group", "reason": body.withheld[0]["reason"]}]


def test_measured_and_inferred_are_distinct() -> None:
    assert Figure.count(10, None, label="x", claim_type="inferred").as_dict()["claim_type"] == "inferred"
    assert Figure.count(10, None, label="x").as_dict()["claim_type"] == "measured"
