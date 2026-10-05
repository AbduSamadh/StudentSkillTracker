"""MIS roster import: upload -> map fields -> validate -> preview diff -> commit (spec §9).

* Never commits on upload.
* Matched on the stable external MIS ID, never on name.
* Idempotent and re-runnable: committing the same file twice changes nothing the second time.
* A commit is refused if the database changed since the preview was produced.
"""

import csv
import hashlib
import io
import json
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Guardian, ImportBatch, Student, StudentGuardian
from app.models.enums import Channel, EnrolmentStatus, ImportStatus, Language
from app.security import blind_index, decrypt_field, encrypt_field

STUDENT_FIELDS = [
    "external_mis_id",
    "given_name",
    "family_name",
    "preferred_name",
    "full_name_ar",
    "date_of_birth",
    "gender",
    "year_group",
    "house",
    "enrolled_on",
]
GUARDIAN_FIELDS = ["external_id", "name", "email", "phone", "whatsapp", "relationship", "language", "channel"]
REQUIRED = ["external_mis_id", "given_name", "family_name", "year_group"]

# Header synonyms used by common MIS exports (iSAMS, Engage, PowerSchool, Veracross).
SYNONYMS = {
    "external_mis_id": [
        "external_mis_id",
        "mis id",
        "pupil id",
        "student id",
        "school id",
        "upn",
        "id",
        "student number",
    ],
    "given_name": ["given_name", "forename", "first name", "firstname", "legal first name"],
    "family_name": ["family_name", "surname", "last name", "lastname"],
    "preferred_name": ["preferred_name", "preferred name", "known as", "nickname"],
    "full_name_ar": ["full_name_ar", "arabic name", "name (arabic)", "الاسم"],
    "date_of_birth": ["date_of_birth", "dob", "date of birth", "birth date"],
    "gender": ["gender", "sex"],
    "year_group": ["year_group", "year", "nc year", "year group", "grade"],
    "house": ["house", "tutor house"],
    "enrolled_on": ["enrolled_on", "enrolment date", "admission date", "date of admission"],
}
for i in (1, 2):
    for f, syn in {
        "external_id": ["contact id", "parent id", "guardian id"],
        "name": ["name", "full name"],
        "email": ["email"],
        "phone": ["phone", "mobile", "telephone"],
        "whatsapp": ["whatsapp"],
        "relationship": ["relationship"],
        "language": ["language"],
        "channel": ["channel", "preferred channel"],
    }.items():
        SYNONYMS[f"guardian{i}_{f}"] = (
            [f"guardian{i}_{f}"] + [f"contact {i} {s}" for s in syn] + [f"parent {i} {s}" for s in syn]
        )

ALL_TARGETS = STUDENT_FIELDS + [f"guardian{i}_{f}" for i in (1, 2) for f in GUARDIAN_FIELDS]


def _norm(h: str) -> str:
    return re.sub(r"[\s_]+", " ", h.strip().lower())


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    by_norm = {_norm(h): h for h in headers}
    out = {}
    for target, syns in SYNONYMS.items():
        for s in syns:
            if _norm(s) in by_norm:
                out[target] = by_norm[_norm(s)]
                break
    return out


def _parse_date(v: str) -> date:
    v = v.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(v, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{v}' (use YYYY-MM-DD or DD/MM/YYYY)")


def _year(v: str) -> int:
    m = re.search(r"\d+", v or "")
    if not m:
        raise ValueError(f"year group '{v}' is not a number")
    y = int(m.group())
    if not 1 <= y <= 13:
        raise ValueError(f"year group {y} is outside 1–13")
    return y


def parse(content: bytes, mapping: dict[str, str]) -> tuple[list[dict], list[dict], list[str]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    mapping = {**suggest_mapping(headers), **{k: v for k, v in mapping.items() if v}}
    missing = [f for f in REQUIRED if f not in mapping]
    if missing:
        return (
            [],
            [{"row": 0, "errors": [f"No column mapped for required field '{f}'" for f in missing]}],
            headers,
        )
    rows, errors, seen = [], [], set()
    for n, raw in enumerate(reader, start=2):
        rec: dict[str, Any] = {t: (raw.get(col) or "").strip() for t, col in mapping.items()}
        errs = []
        for f in REQUIRED:
            if not rec.get(f):
                errs.append(f"{f} is empty")
        try:
            if rec.get("year_group"):
                rec["year_group"] = _year(rec["year_group"])
            for f in ("date_of_birth", "enrolled_on"):
                if rec.get(f):
                    rec[f] = _parse_date(rec[f]).isoformat()
        except ValueError as exc:
            errs.append(str(exc))
        for i in (1, 2):
            e = rec.get(f"guardian{i}_email")
            if e:
                try:
                    rec[f"guardian{i}_email"] = validate_email(
                        e, check_deliverability=False
                    ).normalized.lower()
                except EmailNotValidError:
                    errs.append(f"guardian{i}_email '{e}' is not a valid email")
            lang = (rec.get(f"guardian{i}_language") or "").lower()
            rec[f"guardian{i}_language"] = "ar" if lang.startswith(("ar", "عر")) else "en"
            ch = (rec.get(f"guardian{i}_channel") or "").lower().replace(" ", "_")
            rec[f"guardian{i}_channel"] = ch if ch in {c.value for c in Channel} else None
        mid = rec.get("external_mis_id")
        if mid in seen:
            errs.append(f"duplicate external_mis_id {mid} in file")
        seen.add(mid)
        if errs:
            errors.append({"row": n, "external_mis_id": mid, "errors": errs})
        else:
            rows.append({k: v for k, v in rec.items() if v not in ("", None)})
    return rows, errors, headers


async def _guardian_for(session: AsyncSession, rec: dict, i: int, cache: dict) -> Guardian | None:
    ext, email = rec.get(f"guardian{i}_external_id"), rec.get(f"guardian{i}_email")
    key = ext or email
    if not key or not rec.get(f"guardian{i}_name"):
        return None
    if key in cache:
        return cache[key]
    g = None
    if ext:
        g = await session.scalar(select(Guardian).where(Guardian.external_mis_id == ext))
    if g is None and email:
        g = await session.scalar(select(Guardian).where(Guardian.email_hash == blind_index(email)))
    cache[key] = g
    return g


async def compute_diff(session: AsyncSession, rows: list[dict], mark_missing_as_left: bool) -> dict:
    existing = {s.external_mis_id: s for s in (await session.scalars(select(Student))).all()}
    creates, updates, unchanged, guardian_changes = [], [], 0, 0
    cache: dict = {}
    for rec in rows:
        s = existing.get(rec["external_mis_id"])
        if s is None:
            creates.append(
                {
                    "external_mis_id": rec["external_mis_id"],
                    "name": f"{rec.get('given_name')} {rec.get('family_name')}",
                    "year_group": rec.get("year_group"),
                }
            )
        else:
            changes = {}
            for f in STUDENT_FIELDS[1:]:
                if f not in rec:
                    continue
                cur = getattr(s, f)
                cur = cur.isoformat() if isinstance(cur, date) else cur
                if cur != rec[f]:
                    changes[f] = {"from": cur, "to": rec[f]}
            if s.enrolment_status == EnrolmentStatus.LEFT:
                changes["enrolment_status"] = {"from": "left", "to": "active"}
            if changes:
                updates.append(
                    {"external_mis_id": s.external_mis_id, "student_id": str(s.id), "changes": changes}
                )
            else:
                unchanged += 1
        for i in (1, 2):
            if rec.get(f"guardian{i}_name"):
                g = await _guardian_for(session, rec, i, cache)
                if (
                    g is None
                    or g.full_name != rec[f"guardian{i}_name"]
                    or (
                        rec.get(f"guardian{i}_email")
                        and decrypt_field(g.email_enc) != rec[f"guardian{i}_email"]
                    )
                ):
                    guardian_changes += 1
    in_file = {r["external_mis_id"] for r in rows}
    leavers = (
        [
            {"external_mis_id": s.external_mis_id, "student_id": str(s.id), "name": s.display_name}
            for s in existing.values()
            if s.enrolment_status == EnrolmentStatus.ACTIVE and s.external_mis_id not in in_file
        ]
        if mark_missing_as_left
        else []
    )
    return {
        "creates": creates,
        "updates": updates,
        "unchanged": unchanged,
        "leavers": leavers,
        "guardian_changes": guardian_changes,
    }


def content_hash(content: bytes, mapping: dict, options: dict) -> str:
    h = hashlib.sha256(content)
    h.update(json.dumps({"m": mapping, "o": options}, sort_keys=True).encode())
    return h.hexdigest()


def _diff_signature(diff: dict) -> str:
    sig = {
        "c": sorted(c["external_mis_id"] for c in diff["creates"]),
        "u": sorted(
            (u["external_mis_id"], json.dumps(u["changes"], sort_keys=True)) for u in diff["updates"]
        ),
        "l": sorted(x["external_mis_id"] for x in diff["leavers"]),
    }
    return hashlib.sha256(json.dumps(sig, sort_keys=True).encode()).hexdigest()


async def dry_run(
    session: AsyncSession,
    *,
    content: bytes,
    filename: str | None,
    mapping: dict,
    source: str,
    mark_missing_as_left: bool,
    user_id: uuid.UUID | None,
) -> ImportBatch:
    options = {"mark_missing_as_left": mark_missing_as_left}
    rows, errors, headers = parse(content, mapping)
    diff = await compute_diff(session, rows, mark_missing_as_left)
    batch = ImportBatch(
        source=source,
        filename=filename,
        content_hash=content_hash(content, mapping, options),
        mapping={"columns": {**suggest_mapping(headers), **mapping}, "headers": headers, "options": options},
        status=ImportStatus.PREVIEWED,
        summary={
            "rows": len(rows) + len(errors),
            "valid": len(rows),
            "errors": len(errors),
            "creates": len(diff["creates"]),
            "updates": len(diff["updates"]),
            "unchanged": diff["unchanged"],
            "leavers": len(diff["leavers"]),
            "guardian_changes": diff["guardian_changes"],
        },
        diff={**diff, "errors": errors, "signature": _diff_signature(diff)},
        rows={"rows": rows},
        created_by_id=user_id,
    )
    session.add(batch)
    await session.flush()
    return batch


class StaleImport(Exception):
    pass


async def commit(session: AsyncSession, batch: ImportBatch, user_id: uuid.UUID | None) -> dict:
    if batch.status == ImportStatus.COMMITTED:
        return batch.summary  # idempotent: committing twice is a no-op
    if batch.diff.get("errors"):
        raise ValueError("Fix the rows with errors and run the dry-run again before committing")
    rows = batch.rows["rows"]
    mark_left = batch.mapping.get("options", {}).get("mark_missing_as_left", False)
    current = await compute_diff(session, rows, mark_left)
    if _diff_signature(current) != batch.diff.get("signature"):
        raise StaleImport("The data changed since this preview was produced. Run the dry-run again.")
    now = datetime.now(UTC)
    existing = {s.external_mis_id: s for s in (await session.scalars(select(Student))).all()}
    cache: dict = {}
    for rec in rows:
        s = existing.get(rec["external_mis_id"])
        if s is None:
            s = Student(
                external_mis_id=rec["external_mis_id"],
                given_name=rec["given_name"],
                family_name=rec["family_name"],
                year_group=rec["year_group"],
            )
            session.add(s)
            existing[s.external_mis_id] = s
        for f in STUDENT_FIELDS[1:]:
            if f in rec:
                setattr(s, f, date.fromisoformat(rec[f]) if f in ("date_of_birth", "enrolled_on") else rec[f])
        s.enrolment_status, s.left_on, s.mis_synced_at = EnrolmentStatus.ACTIVE, None, now
        await session.flush()
        links = {
            link.guardian_id
            for link in (
                await session.scalars(select(StudentGuardian).where(StudentGuardian.student_id == s.id))
            ).all()
        }
        for i in (1, 2):
            if not rec.get(f"guardian{i}_name"):
                continue
            g = await _guardian_for(session, rec, i, cache)
            if g is None:
                g = Guardian(
                    full_name=rec[f"guardian{i}_name"], external_mis_id=rec.get(f"guardian{i}_external_id")
                )
                session.add(g)
                cache[rec.get(f"guardian{i}_external_id") or rec.get(f"guardian{i}_email")] = g
            g.full_name = rec[f"guardian{i}_name"]
            if rec.get(f"guardian{i}_email"):
                g.email_enc, g.email_hash = (
                    encrypt_field(rec[f"guardian{i}_email"]),
                    blind_index(rec[f"guardian{i}_email"]),
                )
            if rec.get(f"guardian{i}_phone"):
                g.phone_enc = encrypt_field(rec[f"guardian{i}_phone"])
            if rec.get(f"guardian{i}_whatsapp"):
                g.whatsapp_enc = encrypt_field(rec[f"guardian{i}_whatsapp"])
            g.language = Language(rec.get(f"guardian{i}_language", "en"))
            if rec.get(f"guardian{i}_channel"):
                g.preferred_channel = Channel(rec[f"guardian{i}_channel"])
            await session.flush()
            if g.id not in links:
                session.add(
                    StudentGuardian(
                        student_id=s.id,
                        guardian_id=g.id,
                        relationship_label=rec.get(f"guardian{i}_relationship") or "parent",
                        is_primary_contact=i == 1,
                    )
                )
                links.add(g.id)
    for leaver in current["leavers"]:
        s = existing.get(leaver["external_mis_id"])
        if s is not None:
            s.enrolment_status, s.left_on = EnrolmentStatus.LEFT, now.date()
    batch.status, batch.committed_by_id, batch.committed_at = ImportStatus.COMMITTED, user_id, now
    await session.flush()
    return batch.summary
