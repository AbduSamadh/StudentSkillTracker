"""Automatically *drafted* messages (spec §7.2). Drafting is automated; sending never is.

| Type                 | Trigger                              | Approval                           |
|----------------------|--------------------------------------|------------------------------------|
| Selection notice     | student added to a squad             | draft; admin releases              |
| Result notification  | results recorded for an edition      | admin reviews batch, then releases |
| Progress report      | scheduled, half-termly               | teacher reviews insights; admin releases |
| Attendance concern   | attendance rule                      | always manual — personal hand-off  |
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message, MessageTemplate, Result, ResultParticipant, Squad, Student
from app.models.enums import MessageStatus, MessageType, TemplateStatus


async def latest_approved_template(
    session: AsyncSession, message_type: MessageType
) -> MessageTemplate | None:
    return await session.scalar(
        select(MessageTemplate)
        .where(
            MessageTemplate.message_type == message_type, MessageTemplate.status == TemplateStatus.APPROVED
        )
        .order_by(MessageTemplate.version.desc())
        .limit(1)
    )


async def _open_draft(session: AsyncSession, message_type: MessageType, **where: object) -> Message | None:
    stmt = select(Message).where(Message.message_type == message_type, Message.status == MessageStatus.DRAFT)
    for k, v in where.items():
        stmt = stmt.where(getattr(Message, k) == v)
    return await session.scalar(stmt.order_by(Message.created_at.desc()).limit(1))


async def queue_selection_notice(
    session: AsyncSession, squad: Squad, student: Student, user_id: uuid.UUID
) -> Message | None:
    template = await latest_approved_template(session, MessageType.SELECTION_NOTICE)
    if template is None:
        return None
    msg = await _open_draft(session, MessageType.SELECTION_NOTICE, squad_id=squad.id)
    if msg is None:
        msg = Message(
            message_type=MessageType.SELECTION_NOTICE,
            template_id=template.id,
            squad_id=squad.id,
            title=f"Selection: {squad.name}",
            student_ids=[],
            created_by_id=user_id,
        )
        session.add(msg)
    if student.id not in msg.student_ids:
        msg.student_ids = [*msg.student_ids, student.id]
        msg.previewed_at = None  # audience changed: preview again before release
    await session.flush()
    return msg


async def draft_result_batch(
    session: AsyncSession, edition_id: uuid.UUID, user_id: uuid.UUID
) -> Message | None:
    template = await latest_approved_template(session, MessageType.RESULT_NOTIFICATION)
    if template is None:
        return None
    student_ids = list(
        dict.fromkeys(
            await session.scalars(
                select(ResultParticipant.student_id)
                .join(Result, Result.id == ResultParticipant.result_id)
                .where(Result.edition_id == edition_id)
            )
        )
    )
    if not student_ids:
        return None
    msg = await _open_draft(session, MessageType.RESULT_NOTIFICATION, edition_id=edition_id)
    if msg is None:
        msg = Message(
            message_type=MessageType.RESULT_NOTIFICATION,
            template_id=template.id,
            edition_id=edition_id,
            title="Result notification",
            created_by_id=user_id,
        )
        session.add(msg)
    msg.student_ids, msg.previewed_at = student_ids, None
    await session.flush()
    return msg


async def draft_progress_reports(
    session: AsyncSession, student_ids: list[uuid.UUID], period: str, user_id: uuid.UUID | None
) -> Message | None:
    template = await latest_approved_template(session, MessageType.PROGRESS_REPORT)
    if template is None or not student_ids:
        return None
    msg = Message(
        message_type=MessageType.PROGRESS_REPORT,
        template_id=template.id,
        title=f"Half-term progress report {period}",
        student_ids=student_ids,
        variables={"period": period},
        created_by_id=user_id,
    )
    session.add(msg)
    await session.flush()
    return msg


async def draft_attendance_concern(
    session: AsyncSession, student_id: uuid.UUID, user_id: uuid.UUID | None
) -> Message | None:
    """Rule-triggered, but always manual: the draft can only be prepared for personal sending."""
    template = await latest_approved_template(session, MessageType.ATTENDANCE_CONCERN)
    if template is None:
        return None
    existing = await _open_draft(session, MessageType.ATTENDANCE_CONCERN)
    if existing is not None and student_id in existing.student_ids:
        return existing
    msg = Message(
        message_type=MessageType.ATTENDANCE_CONCERN,
        template_id=template.id,
        title="Attendance concern (send personally)",
        student_ids=[student_id],
        created_by_id=user_id,
    )
    session.add(msg)
    await session.flush()
    return msg
