"""Reference data every new tenant starts with: the skills taxonomy, message templates
(English + Arabic, named variables) and versioned consent forms.

Arabic copy avoids gendered verbs and pronouns about the child, so the same template is
correct for every family.
"""

import csv
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentForm, MessageTemplate, Skill
from app.models.enums import ConsentPurpose, MessageType, TemplateStatus, WhatsAppTemplateStatus
from app.services.messaging.render import variables_in

TAXONOMY_CSV = Path(__file__).parent / "data" / "skills_taxonomy.csv"


async def load_taxonomy(session: AsyncSession) -> int:
    """Idempotent upsert by code. Codes are stable; names and labels may be revised. A skill a
    school has retired stays retired: only skills created here start active."""
    existing = {s.code: s for s in (await session.scalars(select(Skill))).all()}
    n = 0
    with TAXONOMY_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            s = existing.get(row["code"])
            if s is None:
                s = Skill(code=row["code"], is_active=True)
                session.add(s)
                n += 1
            s.domain, s.strand, s.name = row["domain"], row["strand"], row["name"]
            s.parent_label_en, s.parent_label_ar = row["parent_label_en"], row["parent_label_ar"]
            s.typical_year_group = int(row["typical_year_group"]) if row["typical_year_group"] else None
            s.framework_refs = [r for r in row["framework_refs"].split(";") if r]
    await session.flush()
    return n


TEMPLATES: list[dict] = [
    {
        "key": "selection_notice",
        "type": MessageType.SELECTION_NOTICE,
        "wa": "squad_selection_v1",
        "subject_en": "{{ child_name }} has been selected for {{ squad_name }}",
        "body_en": "Dear {{ guardian_name }},\n\nWe are delighted to let you know that {{ child_name }} has been "
        "selected for {{ squad_name }}. Training times and upcoming events are in the parent portal: "
        "{{ portal_link }}\n\n{{ school_name }}",
        "subject_ar": "تم اختيار {{ child_name }} في {{ squad_name }}",
        "body_ar": "{{ guardian_name }} المحترم/ة،\n\nيسعدنا إبلاغكم باختيار {{ child_name }} للانضمام إلى "
        "{{ squad_name }}. مواعيد التدريب والفعاليات القادمة متاحة في بوابة أولياء الأمور: {{ portal_link }}"
        "\n\n{{ school_name }}",
    },
    {
        "key": "event_logistics",
        "type": MessageType.LOGISTICS,
        "wa": "event_logistics_v1",
        "subject_en": "{{ edition_name }}: arrangements for {{ event_date }}",
        "body_en": "Dear {{ guardian_name }},\n\n{{ edition_name }} takes place on {{ event_date }} at {{ venue }}.\n"
        "Meeting time: {{ meet_time }}\nPick-up: {{ pickup_time }}\nPlease bring: {{ kit_list }}\n\n"
        "This message covers {{ child_names }}.\n\n{{ school_name }}",
        "subject_ar": "{{ edition_name }}: الترتيبات ليوم {{ event_date }}",
        "body_ar": "{{ guardian_name }} المحترم/ة،\n\nتُقام {{ edition_name }} يوم {{ event_date }} في {{ venue }}.\n"
        "موعد التجمّع: {{ meet_time }}\nموعد الاستلام: {{ pickup_time }}\nيُرجى إحضار: {{ kit_list }}\n\n"
        "تشمل هذه الرسالة: {{ child_names }}.\n\n{{ school_name }}",
    },
    {
        "key": "consent_request",
        "type": MessageType.CONSENT_REQUEST,
        "wa": None,
        "subject_en": "Your response is needed: {{ edition_name }}",
        "body_en": "Dear {{ guardian_name }},\n\nPlease review and respond to the {{ consent_label }} request for "
        "{{ child_names }} for {{ edition_name }} on {{ event_date }}. You can respond in the parent "
        "portal: {{ portal_link }}\n\n{{ school_name }}",
        "subject_ar": "يُرجى الرد: {{ edition_name }}",
        "body_ar": "{{ guardian_name }} المحترم/ة،\n\nيُرجى مراجعة طلب {{ consent_label_ar }} الخاص بـ{{ child_names }} "
        "للمشاركة في {{ edition_name }} يوم {{ event_date }}، والرد عبر بوابة أولياء الأمور: {{ portal_link }}"
        "\n\n{{ school_name }}",
    },
    {
        "key": "result_notification",
        "type": MessageType.RESULT_NOTIFICATION,
        "wa": "result_notification_v1",
        "subject_en": "{{ edition_name }}: result for {{ child_name }}",
        "body_en": "Dear {{ guardian_name }},\n\nAt {{ edition_name }}, {{ child_name }}'s entry {{ result_summary }}."
        "{% if team_members %} Team-mates: {{ team_members }}.{% endif %}\n\nThe full history is in the "
        "parent portal: {{ portal_link }}\n\n{{ school_name }}",
        "subject_ar": "{{ edition_name }}: نتيجة {{ child_name }}",
        "body_ar": "{{ guardian_name }} المحترم/ة،\n\nفي {{ edition_name }}، حصلت مشاركة {{ child_name }} على: "
        "{{ result_summary }}.{% if team_members %} أعضاء الفريق: {{ team_members }}.{% endif %}\n\n"
        "السجل الكامل متاح في بوابة أولياء الأمور: {{ portal_link }}\n\n{{ school_name }}",
    },
    {
        "key": "progress_report",
        "type": MessageType.PROGRESS_REPORT,
        "wa": None,
        "subject_en": "Half-term progress: {{ child_names }}",
        "body_en": "Dear {{ guardian_name }},\n\nHere is this half-term's update from the STEM programme, reviewed by "
        "{{ child_names }}'s coach:\n\n{{ progress_summary }}\n\nSee the full skills profile in the parent "
        "portal: {{ portal_link }}\n\n{{ school_name }}",
        "subject_ar": "التقدّم في هذا النصف من الفصل: {{ child_names }}",
        "body_ar": "{{ guardian_name }} المحترم/ة،\n\nإليكم مستجدات هذا النصف من الفصل من برنامج العلوم والتكنولوجيا، "
        "بعد مراجعتها من المدرّب:\n\n{{ progress_summary }}\n\nملف المهارات الكامل متاح في بوابة أولياء "
        "الأمور: {{ portal_link }}\n\n{{ school_name }}",
    },
    {
        "key": "celebration",
        "type": MessageType.CELEBRATION,
        "wa": "celebration_v1",
        "subject_en": "Congratulations, {{ child_name }}!",
        "body_en": "Dear {{ guardian_name }},\n\n{{ celebration_note }}\n\nWell done from everyone at {{ school_name }}.",
        "subject_ar": "تهانينا لـ{{ child_name }}!",
        "body_ar": "{{ guardian_name }} المحترم/ة،\n\n{{ celebration_note_ar }}\n\nتهانينا من أسرة {{ school_name }}.",
    },
    {
        "key": "attendance_concern",
        "type": MessageType.ATTENDANCE_CONCERN,
        "wa": None,
        "subject_en": "{{ squad_name }}: a quick conversation about attendance",
        "body_en": "Dear {{ guardian_name }},\n\nI wanted to get in touch personally about {{ child_name }}'s recent "
        "attendance at {{ squad_name }} training. Could we find a time to talk this week?\n\n{{ school_name }}",
        "subject_ar": "{{ squad_name }}: حديث قصير حول الحضور",
        "body_ar": "{{ guardian_name }} المحترم/ة،\n\nأودّ التواصل معكم شخصيًا بشأن حضور {{ child_name }} مؤخرًا لتدريبات "
        "{{ squad_name }}. هل يمكننا تحديد موعد للحديث هذا الأسبوع؟\n\n{{ school_name }}",
    },
    {
        "key": "non_selection",
        "type": MessageType.NON_SELECTION,
        "wa": None,
        "subject_en": "{{ squad_name }} selection",
        "body_en": "Dear {{ guardian_name }},\n\nI wanted to let you know personally about this round of selection for "
        "{{ squad_name }}, and talk about next steps for {{ child_name }}.\n\n{{ school_name }}",
        "subject_ar": "الاختيار لـ{{ squad_name }}",
        "body_ar": "{{ guardian_name }} المحترم/ة،\n\nأودّ إبلاغكم شخصيًا بنتائج جولة الاختيار لـ{{ squad_name }}، "
        "والحديث عن الخطوات القادمة لـ{{ child_name }}.\n\n{{ school_name }}",
    },
    {
        "key": "emergency_notice",
        "type": MessageType.EMERGENCY,
        "wa": "emergency_notice_v1",
        "subject_en": "URGENT — {{ school_name }}",
        "body_en": "{{ notice_en }}\n\n{{ school_name }}",
        "subject_ar": "عاجل — {{ school_name }}",
        "body_ar": "{{ notice_ar }}\n\n{{ school_name }}",
    },
]

CONSENT_FORMS = {
    ConsentPurpose.MEDIA: (
        "Photographs and video",
        "I agree that the school may take photographs and video of my child at competitions and training, store them "
        "securely, and use them in school reports, newsletters and messages to other families in the programme. "
        "I can withdraw at any time in the parent portal, and my child will then not appear in any new material.",
        "الصور ومقاطع الفيديو",
        "أوافق على أن تلتقط المدرسة صورًا ومقاطع فيديو لطفلي خلال المسابقات والتدريبات، وأن تحفظها بشكل آمن، وأن تستخدمها في "
        "تقارير المدرسة ونشراتها والرسائل الموجّهة إلى الأسر الأخرى في البرنامج. يمكنني سحب الموافقة في أي وقت عبر بوابة "
        "أولياء الأمور، ولن يظهر طفلي بعدها في أي مواد جديدة.",
    ),
    ConsentPurpose.TRAVEL: (
        "Travel to an event",
        "I agree that my child may travel with school staff to and from the named event, using transport arranged "
        "by the school.",
        "السفر إلى فعالية",
        "أوافق على سفر طفلي برفقة طاقم المدرسة من وإلى الفعالية المحددة، باستخدام وسيلة نقل ترتّبها المدرسة.",
    ),
    ConsentPurpose.FEE_AUTHORISATION: (
        "Entry fee authorisation",
        "I authorise the school to pay the entry fee for the named event and add it to my account.",
        "تفويض رسوم المشاركة",
        "أفوّض المدرسة بدفع رسوم المشاركة في الفعالية المحددة وإضافتها إلى حسابي.",
    ),
    ConsentPurpose.COMMUNICATIONS: (
        "Programme messages",
        "I agree to receive messages about my child's STEM programme by my chosen channel. I can opt out of any "
        "category, or withdraw entirely, at any time.",
        "رسائل البرنامج",
        "أوافق على تلقي رسائل حول برنامج العلوم والتكنولوجيا الخاص بطفلي عبر القناة التي أختارها. يمكنني إلغاء الاشتراك في أي "
        "فئة، أو سحب الموافقة بالكامل، في أي وقت.",
    ),
    ConsentPurpose.DATA_PROCESSING: (
        "Recording skills and results",
        "I agree that the school may record my child's competition entries, results and demonstrated skills to plan "
        "their learning. The school does not profile personality or behaviour and never uses this data for "
        "advertising.",
        "تسجيل المهارات والنتائج",
        "أوافق على أن تسجّل المدرسة مشاركات طفلي في المسابقات ونتائجه والمهارات التي أظهرها لتخطيط تعلّمه. لا تقوم المدرسة "
        "بتحليل الشخصية أو السلوك، ولا تستخدم هذه البيانات لأي أغراض إعلانية.",
    ),
}


async def load_templates(session: AsyncSession, approver_id=None) -> int:  # noqa: ANN001
    existing = set(await session.scalars(select(MessageTemplate.key)))
    n = 0
    now = datetime.now(UTC)
    for t in TEMPLATES:
        if t["key"] in existing:
            continue
        session.add(
            MessageTemplate(
                key=t["key"],
                message_type=t["type"],
                version=1,
                status=TemplateStatus.APPROVED,
                subject_en=t["subject_en"],
                body_en=t["body_en"],
                subject_ar=t["subject_ar"],
                body_ar=t["body_ar"],
                variables=variables_in(t["body_en"], t["body_ar"], t["subject_en"], t["subject_ar"]),
                whatsapp_template_name=t["wa"],
                whatsapp_status=WhatsAppTemplateStatus.APPROVED
                if t["wa"]
                else WhatsAppTemplateStatus.NOT_SUBMITTED,
                approved_by_id=approver_id,
                approved_at=now,
            )
        )
        n += 1
    await session.flush()
    return n


async def load_consent_forms(session: AsyncSession) -> int:
    existing = set(await session.scalars(select(ConsentForm.purpose)))
    n = 0
    for purpose, (te, be, ta, ba) in CONSENT_FORMS.items():
        if purpose in existing:
            continue
        session.add(ConsentForm(purpose=purpose, version=1, title_en=te, body_en=be, title_ar=ta, body_ar=ba))
        n += 1
    await session.flush()
    return n


async def load_reference_data(session: AsyncSession) -> dict:
    return {
        "skills": await load_taxonomy(session),
        "templates": await load_templates(session),
        "consent_forms": await load_consent_forms(session),
    }
