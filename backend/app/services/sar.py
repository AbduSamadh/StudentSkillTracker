"""Right-of-access bundle (spec §8.2): everything held about one student, as a readable ZIP
(JSON per record type plus an HTML summary). Target: under 60 seconds — in practice it is
a handful of indexed queries."""

import io
import json
import zipfile
from datetime import UTC, datetime

from jinja2 import Environment, select_autoescape
from sqlalchemy import or_, select

from app.audit import _jsonable
from app.models import (
    Attendance,
    AuditEvent,
    Consent,
    ConsentRequest,
    Guardian,
    Insight,
    MediaAsset,
    MediaSubject,
    MessageDelivery,
    Result,
    ResultParticipant,
    SkillAward,
    SkillGoal,
    SquadMembership,
    Student,
    StudentFlag,
    StudentGuardian,
)
from app.security import decrypt_field

_env = Environment(autoescape=select_autoescape(["html"]))
SUMMARY = _env.from_string("""<!doctype html><html><head><meta charset="utf-8"><title>Records held — {{ s.name }}</title>
<style>body{font-family:system-ui,"Noto Sans Arabic",sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;line-height:1.5}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:4px;text-align:start}</style></head>
<body><h1>Records held about {{ s.name }}</h1><p>Generated {{ generated }} by {{ school }}. This bundle contains every
record the platform holds about this student. Machine-readable copies are in the JSON files.</p>
<table><tr><th>Record type</th><th>Count</th><th>File</th></tr>
{% for k, v in counts.items() %}<tr><td>{{ k.replace('_', ' ') }}</td><td>{{ v }}</td><td>{{ k }}.json</td></tr>{% endfor %}
</table></body></html>""")


def _rows(objs) -> list[dict]:  # noqa: ANN001
    out = []
    for o in objs:
        d = {
            c.key: getattr(o, c.key)
            for c in o.__table__.columns
            if c.key
            not in ("tenant_id", "email_enc", "phone_enc", "whatsapp_enc", "email_hash", "open_token")
        }
        out.append(_jsonable(d))
    return out


async def build_sar_bundle(ctx, s: Student) -> tuple[bytes, str]:  # noqa: ANN001
    q = ctx.session
    sections: dict[str, list] = {"student": _rows([s])}
    links = (await q.scalars(select(StudentGuardian).where(StudentGuardian.student_id == s.id))).all()
    guardians = []
    for link in links:
        g = await q.get(Guardian, link.guardian_id)
        if g:
            guardians.append(
                {
                    **_rows([g])[0],
                    "relationship": link.relationship_label,
                    "email": decrypt_field(g.email_enc),
                    "phone": decrypt_field(g.phone_enc),
                    "whatsapp": decrypt_field(g.whatsapp_enc),
                }
            )
    sections["guardians"] = guardians
    for name, model, col in (
        ("squad_memberships", SquadMembership, SquadMembership.student_id),
        ("attendance", Attendance, Attendance.student_id),
        ("skill_awards", SkillAward, SkillAward.student_id),
        ("skill_goals", SkillGoal, SkillGoal.student_id),
        ("flags", StudentFlag, StudentFlag.student_id),
        ("insights", Insight, Insight.student_id),
        ("consents", Consent, Consent.student_id),
        ("consent_requests", ConsentRequest, ConsentRequest.student_id),
    ):
        sections[name] = _rows((await q.scalars(select(model).where(col == s.id))).all())
    sections["results"] = _rows(
        (
            await q.scalars(
                select(Result)
                .join(ResultParticipant, ResultParticipant.result_id == Result.id)
                .where(ResultParticipant.student_id == s.id)
            )
        ).all()
    )
    sections["messages_received_by_family"] = _rows(
        (await q.scalars(select(MessageDelivery).where(MessageDelivery.student_ids.contains([s.id])))).all()
    )
    sections["media_tagged"] = _rows(
        (
            await q.scalars(
                select(MediaAsset)
                .join(MediaSubject, MediaSubject.media_id == MediaAsset.id)
                .where(MediaSubject.student_id == s.id)
            )
        ).all()
    )
    sections["access_log"] = _rows(
        (
            await q.scalars(
                select(AuditEvent).where(or_(AuditEvent.subject_id == s.id)).order_by(AuditEvent.created_at)
            )
        ).all()
    )

    counts = {k: len(v) for k, v in sections.items()}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in sections.items():
            z.writestr(f"{k}.json", json.dumps(v, ensure_ascii=False, indent=1))
        z.writestr(
            "summary.html",
            SUMMARY.render(
                s={"name": s.display_name},
                counts=counts,
                school=ctx.tenant.name,
                generated=datetime.now(UTC).strftime("%d %b %Y %H:%M UTC"),
            ),
        )
    return buf.getvalue(), f"subject-access-{s.external_mis_id}-{datetime.now(UTC):%Y%m%d}.zip"
