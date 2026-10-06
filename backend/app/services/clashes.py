"""Clash detector (spec §5.1): an edition clashes when its event dates fall inside an exam
window, or overlap another edition that the same squad is entered in."""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CompetitionEdition, ExamWindow, Squad, SquadTargetEdition


def _overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start <= b_end and b_start <= a_end


def _years(edition: CompetitionEdition) -> set[int] | None:
    if edition.eligible_year_min is None and edition.eligible_year_max is None:
        return None
    lo = edition.eligible_year_min or 1
    hi = edition.eligible_year_max or 13
    return set(range(lo, hi + 1))


async def clashes_for_edition(session: AsyncSession, edition: CompetitionEdition) -> list[dict]:
    out: list[dict] = []
    years = _years(edition)
    for w in (await session.scalars(select(ExamWindow))).all():
        if not _overlaps(edition.event_starts, edition.event_ends, w.starts_on, w.ends_on):
            continue
        affected = None
        if w.year_groups:
            affected = sorted(set(w.year_groups) & years) if years is not None else sorted(w.year_groups)
            if not affected:
                continue
        out.append(
            {
                "kind": "exam_window",
                "exam_window_id": w.id,
                "name": w.name,
                "starts_on": w.starts_on,
                "ends_on": w.ends_on,
                "year_groups": affected,
                "reason": f"Event dates overlap the exam window '{w.name}' ({w.starts_on:%d %b}–{w.ends_on:%d %b}).",
            }
        )

    squad_ids = list(
        await session.scalars(
            select(SquadTargetEdition.squad_id).where(SquadTargetEdition.edition_id == edition.id)
        )
    )
    if squad_ids:
        rows = (
            await session.execute(
                select(SquadTargetEdition.squad_id, Squad.name, CompetitionEdition)
                .join(CompetitionEdition, CompetitionEdition.id == SquadTargetEdition.edition_id)
                .join(Squad, Squad.id == SquadTargetEdition.squad_id)
                .where(
                    SquadTargetEdition.squad_id.in_(squad_ids), SquadTargetEdition.edition_id != edition.id
                )
            )
        ).all()
        for squad_id, squad_name, other in rows:
            if _overlaps(edition.event_starts, edition.event_ends, other.event_starts, other.event_ends):
                out.append(
                    {
                        "kind": "squad_double_booking",
                        "squad_id": squad_id,
                        "squad_name": squad_name,
                        "other_edition_id": other.id,
                        "other_edition_name": other.name,
                        "reason": f"Squad '{squad_name}' is also entered in '{other.name}' on overlapping dates.",
                    }
                )
    return out


async def exam_clash_for_year(
    session: AsyncSession,
    edition: CompetitionEdition,
    year_group: int,
    windows: list[ExamWindow] | None = None,
) -> ExamWindow | None:
    if windows is None:
        windows = list((await session.scalars(select(ExamWindow))).all())
    for w in windows:
        if _overlaps(edition.event_starts, edition.event_ends, w.starts_on, w.ends_on) and (
            not w.year_groups or year_group in w.year_groups
        ):
            return w
    return None


async def editions_for_squads(session: AsyncSession, squad_ids: list[uuid.UUID]) -> dict[uuid.UUID, list]:
    if not squad_ids:
        return {}
    rows = (
        await session.execute(
            select(SquadTargetEdition.squad_id, CompetitionEdition)
            .join(CompetitionEdition, CompetitionEdition.id == SquadTargetEdition.edition_id)
            .where(SquadTargetEdition.squad_id.in_(squad_ids))
        )
    ).all()
    out: dict[uuid.UUID, list] = {}
    for sid, ed in rows:
        out.setdefault(sid, []).append(ed)
    return out
