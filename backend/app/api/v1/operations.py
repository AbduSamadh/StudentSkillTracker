import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from app.deps import CtxDep
from app.models import (
    Asset,
    AssetLoan,
    AssetRequest,
    AuditEvent,
    BudgetLine,
    CompetitionEdition,
    ImportBatch,
    MediaAsset,
    MediaSubject,
    Season,
)
from app.models.enums import (
    ApprovalStatus,
    AssetCondition,
    AssetRequestStatus,
    BudgetCategory,
    ImportStatus,
)
from app.permissions import Cap, ensure_student_in_scope, not_found
from app.schemas.common import ORM
from app.services import imports as import_service
from app.services import storage
from app.services.consent import media_consented_ids

router = APIRouter(tags=["operations"])
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_MEDIA_BYTES = 50 * 1024 * 1024
MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "video/mp4", "video/quicktime", "application/pdf"}


# ---------------- MIS import ----------------
def _batch_out(b: ImportBatch, include_rows: bool = False) -> dict:
    return {"id": b.id, "source": b.source, "filename": b.filename, "status": b.status.value, "summary": b.summary,
            "diff": {k: v for k, v in b.diff.items() if k != "signature"}, "mapping": b.mapping,
            "created_at": b.created_at, "committed_at": b.committed_at,
            **({"rows": b.rows.get("rows", [])[:50]} if include_rows else {})}


@router.post("/imports/mis/dry-run", status_code=201)
async def mis_dry_run(
    ctx: CtxDep, file: UploadFile = File(...), mapping: str = Form("{}"), mark_missing_as_left: bool = Form(False),
) -> dict:
    """Upload + map + validate + preview diff. Nothing is written to the roster."""
    ctx.require(Cap.MANAGE_IMPORTS)
    import json

    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (10 MB max)")
    try:
        mapping_d = json.loads(mapping or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "mapping must be JSON") from exc
    batch = await import_service.dry_run(ctx.session, content=content, filename=file.filename, mapping=mapping_d,
                                         source="csv", mark_missing_as_left=mark_missing_as_left, user_id=ctx.user_id)
    ctx.audit("import.dry_run", "import_batch", batch.id, context=batch.summary)
    return _batch_out(batch, include_rows=True)


class CommitIn(BaseModel):
    batch_id: uuid.UUID


@router.post("/imports/mis/commit")
async def mis_commit(body: CommitIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_IMPORTS)
    batch = await ctx.session.get(ImportBatch, body.batch_id)
    if batch is None:
        raise not_found("Import not found")
    try:
        summary = await import_service.commit(ctx.session, batch, ctx.user_id)
    except import_service.StaleImport as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    ctx.audit("import.commit", "import_batch", batch.id, context=summary)
    return _batch_out(batch)


@router.get("/imports")
async def list_imports(ctx: CtxDep) -> list[dict]:
    ctx.require(Cap.MANAGE_IMPORTS)
    rows = (await ctx.session.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(50))).all()
    return [_batch_out(b) for b in rows]


@router.get("/imports/fields")
async def import_fields(ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_IMPORTS)
    return {"targets": import_service.ALL_TARGETS, "required": import_service.REQUIRED}


@router.get("/imports/{batch_id}")
async def get_import(batch_id: uuid.UUID, ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_IMPORTS)
    b = await ctx.session.get(ImportBatch, batch_id)
    if b is None:
        raise not_found()
    return _batch_out(b, include_rows=b.status == ImportStatus.PREVIEWED)


# ---------------- Inventory ----------------
class AssetIn(BaseModel):
    tag: str
    name: str
    category: str
    condition: AssetCondition = AssetCondition.GOOD
    is_consumable: bool = False
    quantity_on_hand: int = Field(default=1, ge=0)
    reorder_threshold: int | None = None
    unit_cost: Decimal | None = None
    assigned_squad_id: uuid.UUID | None = None
    assigned_student_id: uuid.UUID | None = None
    purchased_on: date | None = None
    service_due_on: date | None = None
    calibration_due_on: date | None = None
    notes: str | None = None


class AssetOut(ORM, AssetIn):
    id: uuid.UUID
    on_loan: int = 0
    needs_reorder: bool = False
    service_overdue: bool = False


async def _asset_out(ctx, a: Asset) -> AssetOut:  # noqa: ANN001
    out = AssetOut.model_validate(a)
    out.on_loan = await ctx.session.scalar(select(func.coalesce(func.sum(AssetLoan.quantity), 0)).where(
        AssetLoan.asset_id == a.id, AssetLoan.returned_at.is_(None))) or 0
    out.needs_reorder = a.is_consumable and a.reorder_threshold is not None and a.quantity_on_hand <= a.reorder_threshold
    today = datetime.now(UTC).date()
    out.service_overdue = any(d is not None and d < today for d in (a.service_due_on, a.calibration_due_on))
    return out


@router.get("/inventory", response_model=list[AssetOut])
async def list_inventory(ctx: CtxDep, category: str | None = None, q: str | None = None) -> list[AssetOut]:
    ctx.require(Cap.VIEW_INVENTORY)
    stmt = select(Asset).order_by(Asset.category, Asset.tag)
    if category:
        stmt = stmt.where(Asset.category == category)
    if q:
        stmt = stmt.where(func.lower(Asset.name).like(f"%{q.lower()}%") | (Asset.tag == q))
    return [await _asset_out(ctx, a) for a in (await ctx.session.scalars(stmt)).all()]


@router.post("/inventory", response_model=AssetOut, status_code=201)
async def create_asset(body: AssetIn, ctx: CtxDep) -> AssetOut:
    ctx.require(Cap.MANAGE_INVENTORY)
    if await ctx.session.scalar(select(Asset).where(Asset.tag == body.tag)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Asset tag already in use")
    a = Asset(**body.model_dump())
    ctx.session.add(a)
    await ctx.session.flush()
    ctx.audit("asset.create", "asset", a.id)
    return await _asset_out(ctx, a)


@router.patch("/inventory/{asset_id}", response_model=AssetOut)
async def update_asset(asset_id: uuid.UUID, body: AssetIn, ctx: CtxDep) -> AssetOut:
    ctx.require(Cap.MANAGE_INVENTORY)
    a = await ctx.session.get(Asset, asset_id)
    if a is None:
        raise not_found()
    for k, v in body.model_dump().items():
        setattr(a, k, v)
    ctx.audit("asset.update", "asset", a.id)
    return await _asset_out(ctx, a)


class LoanIn(BaseModel):
    asset_id: uuid.UUID
    edition_id: uuid.UUID | None = None
    squad_id: uuid.UUID | None = None
    student_id: uuid.UUID | None = None
    quantity: int = Field(default=1, ge=1)
    due_back_on: date | None = None
    notes: str | None = None


class ReturnIn(BaseModel):
    returned_quantity: int | None = None
    return_condition: AssetCondition = AssetCondition.GOOD
    notes: str | None = None


def _loan_dict(loan: AssetLoan) -> dict:
    return {"id": loan.id, "asset_id": loan.asset_id, "edition_id": loan.edition_id, "squad_id": loan.squad_id,
            "student_id": loan.student_id, "quantity": loan.quantity, "issued_at": loan.issued_at,
            "due_back_on": loan.due_back_on, "returned_at": loan.returned_at, "returned_quantity": loan.returned_quantity,
            "return_condition": loan.return_condition.value if loan.return_condition else None, "notes": loan.notes}


@router.post("/inventory/loans", status_code=201)
async def issue_loan(body: LoanIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_INVENTORY)
    a = await ctx.session.get(Asset, body.asset_id)
    if a is None:
        raise not_found("Asset not found")
    out_now = await ctx.session.scalar(select(func.coalesce(func.sum(AssetLoan.quantity), 0)).where(
        AssetLoan.asset_id == a.id, AssetLoan.returned_at.is_(None))) or 0
    if out_now + body.quantity > a.quantity_on_hand:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only {a.quantity_on_hand - out_now} available")
    loan = AssetLoan(**body.model_dump(), issued_at=datetime.now(UTC), issued_by_id=ctx.user_id)
    ctx.session.add(loan)
    await ctx.session.flush()
    ctx.audit("asset.loan", "asset", a.id, context={"loan_id": loan.id, "edition_id": body.edition_id})
    return _loan_dict(loan)


@router.post("/inventory/loans/{loan_id}/return")
async def return_loan(loan_id: uuid.UUID, body: ReturnIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_INVENTORY)
    loan = await ctx.session.get(AssetLoan, loan_id)
    if loan is None:
        raise not_found()
    if loan.returned_at is not None:
        return _loan_dict(loan)
    a = await ctx.session.get(Asset, loan.asset_id)
    assert a is not None
    returned = loan.quantity if body.returned_quantity is None else body.returned_quantity
    loan.returned_at, loan.returned_quantity, loan.return_condition = datetime.now(UTC), returned, body.return_condition
    loan.returned_to_id, loan.notes = ctx.user_id, body.notes or loan.notes
    missing = loan.quantity - returned  # consumed (consumables) or lost (equipment)
    if missing > 0:
        a.quantity_on_hand = max(0, a.quantity_on_hand - missing)
        if not a.is_consumable and a.quantity_on_hand == 0:
            a.condition = AssetCondition.LOST
    if body.return_condition in (AssetCondition.NEEDS_REPAIR, AssetCondition.LOST):
        a.condition = body.return_condition
    ctx.audit("asset.return", "asset", a.id, context={"loan_id": loan.id, "returned": returned,
                                                     "condition": body.return_condition.value})
    return _loan_dict(loan)


@router.get("/inventory/loans")
async def list_loans(ctx: CtxDep, edition_id: uuid.UUID | None = None, open_only: bool = False) -> list[dict]:
    ctx.require(Cap.VIEW_INVENTORY)
    stmt = select(AssetLoan).order_by(AssetLoan.issued_at.desc())
    if edition_id:
        stmt = stmt.where(AssetLoan.edition_id == edition_id)
    if open_only:
        stmt = stmt.where(AssetLoan.returned_at.is_(None))
    return [_loan_dict(x) for x in (await ctx.session.scalars(stmt.limit(500))).all()]


@router.get("/inventory/editions/{edition_id}/manifest")
async def edition_manifest(edition_id: uuid.UUID, ctx: CtxDep) -> dict:
    """What leaves the building on event day, and what has come back."""
    ctx.require(Cap.VIEW_INVENTORY)
    ed = await ctx.session.get(CompetitionEdition, edition_id)
    if ed is None:
        raise not_found()
    rows = (await ctx.session.execute(select(AssetLoan, Asset).join(Asset, Asset.id == AssetLoan.asset_id)
                                      .where(AssetLoan.edition_id == edition_id))).all()
    items = [{**_loan_dict(loan), "tag": a.tag, "name": a.name, "category": a.category} for loan, a in rows]
    return {"edition_id": ed.id, "edition_name": ed.name, "items": items,
            "out": sum(1 for i in items if i["returned_at"] is None),
            "returned": sum(1 for i in items if i["returned_at"] is not None)}


class KitRequestIn(BaseModel):
    description: str
    quantity: int = Field(default=1, ge=1)
    squad_id: uuid.UUID | None = None
    edition_id: uuid.UUID | None = None
    needed_by: date | None = None


@router.post("/inventory/requests", status_code=201)
async def request_kit(body: KitRequestIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.REQUEST_KIT)
    r = AssetRequest(**body.model_dump(), requested_by_id=ctx.user_id)
    ctx.session.add(r)
    await ctx.session.flush()
    ctx.audit("asset.request", "asset_request", r.id)
    return {"id": r.id, "status": r.status.value}


@router.get("/inventory/requests")
async def list_kit_requests(ctx: CtxDep) -> list[dict]:
    ctx.require(Cap.REQUEST_KIT)
    stmt = select(AssetRequest).order_by(AssetRequest.created_at.desc())
    if not ctx.principal.can(Cap.MANAGE_INVENTORY):
        stmt = stmt.where(AssetRequest.requested_by_id == ctx.user_id)
    return [{"id": r.id, "description": r.description, "quantity": r.quantity, "squad_id": r.squad_id,
             "edition_id": r.edition_id, "needed_by": r.needed_by, "status": r.status.value,
             "created_at": r.created_at} for r in (await ctx.session.scalars(stmt)).all()]


class DecisionIn(BaseModel):
    status: AssetRequestStatus


@router.post("/inventory/requests/{request_id}/decide")
async def decide_kit_request(request_id: uuid.UUID, body: DecisionIn, ctx: CtxDep) -> dict:
    ctx.require(Cap.MANAGE_INVENTORY)
    r = await ctx.session.get(AssetRequest, request_id)
    if r is None:
        raise not_found()
    r.status, r.decided_by_id, r.decided_at = body.status, ctx.user_id, datetime.now(UTC)
    ctx.audit("asset.request_decide", "asset_request", r.id, context={"status": body.status.value})
    return {"id": r.id, "status": r.status.value}


# ---------------- Budget ----------------
class BudgetIn(BaseModel):
    season_id: uuid.UUID | None = None
    edition_id: uuid.UUID | None = None
    category: BudgetCategory
    description: str
    planned_amount: Decimal = Field(ge=0)
    actual_amount: Decimal | None = Field(default=None, ge=0)


class BudgetOut(ORM, BudgetIn):
    id: uuid.UUID
    status: ApprovalStatus
    approved_by_id: uuid.UUID | None
    approved_at: datetime | None
    decision_note: str | None


@router.get("/budget")
async def budget_overview(ctx: CtxDep, season_id: uuid.UUID | None = None) -> dict:
    """Per edition: planned vs actual; rolled up to the season."""
    ctx.require(Cap.VIEW_BUDGET)
    season = await ctx.session.get(Season, season_id) if season_id else await ctx.session.scalar(
        select(Season).order_by(Season.starts_on.desc()).limit(1))
    stmt = select(BudgetLine).order_by(BudgetLine.created_at)
    if season:
        stmt = stmt.where(BudgetLine.season_id == season.id)
    lines = list((await ctx.session.scalars(stmt)).all())
    editions = {e.id: e.name for e in (await ctx.session.scalars(select(CompetitionEdition))).all()}
    per_edition: dict = {}
    for b in lines:
        key = str(b.edition_id) if b.edition_id else "season"
        e = per_edition.setdefault(key, {"edition_id": b.edition_id, "edition_name": editions.get(b.edition_id, "Season-wide"),
                                         "planned": Decimal(0), "actual": Decimal(0), "lines": []})
        if b.status == ApprovalStatus.APPROVED:
            e["planned"] += Decimal(b.planned_amount)
            e["actual"] += Decimal(b.actual_amount or 0)
        e["lines"].append(BudgetOut.model_validate(b))
    approved = [b for b in lines if b.status == ApprovalStatus.APPROVED]
    return {
        "season": {"id": season.id, "name": season.name, "envelope": season.budget_envelope,
                   "currency": season.currency} if season else None,
        "planned_total": sum((Decimal(b.planned_amount) for b in approved), Decimal(0)),
        "actual_total": sum((Decimal(b.actual_amount or 0) for b in approved), Decimal(0)),
        "awaiting_approval": sum(1 for b in lines if b.status == ApprovalStatus.PROPOSED),
        "by_edition": list(per_edition.values()),
    }


@router.post("/budget/lines", response_model=BudgetOut, status_code=201)
async def create_budget_line(body: BudgetIn, ctx: CtxDep) -> BudgetOut:
    ctx.require(Cap.MANAGE_BUDGET)
    if body.season_id is None and body.edition_id:
        ed = await ctx.session.get(CompetitionEdition, body.edition_id)
        body.season_id = ed.season_id if ed else None
    b = BudgetLine(**body.model_dump(), requested_by_id=ctx.user_id)
    ctx.session.add(b)
    await ctx.session.flush()
    ctx.audit("budget.line_create", "budget_line", b.id, context={"planned": body.planned_amount})
    return BudgetOut.model_validate(b)


class ActualIn(BaseModel):
    actual_amount: Decimal = Field(ge=0)


@router.patch("/budget/lines/{line_id}", response_model=BudgetOut)
async def record_actual(line_id: uuid.UUID, body: ActualIn, ctx: CtxDep) -> BudgetOut:
    ctx.require(Cap.MANAGE_BUDGET)
    b = await ctx.session.get(BudgetLine, line_id)
    if b is None:
        raise not_found()
    b.actual_amount = body.actual_amount
    ctx.audit("budget.actual", "budget_line", b.id, context={"actual": body.actual_amount})
    return BudgetOut.model_validate(b)


class ApproveIn(BaseModel):
    decision: ApprovalStatus
    note: str | None = None


@router.post("/budget/lines/{line_id}/decide", response_model=BudgetOut)
async def decide_budget_line(line_id: uuid.UUID, body: ApproveIn, ctx: CtxDep) -> BudgetOut:
    """School leaders approve spend."""
    ctx.require(Cap.APPROVE_SPEND)
    b = await ctx.session.get(BudgetLine, line_id)
    if b is None:
        raise not_found()
    if body.decision == ApprovalStatus.PROPOSED:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Approve or reject")
    b.status, b.approved_by_id, b.approved_at, b.decision_note = body.decision, ctx.user_id, datetime.now(UTC), body.note
    ctx.audit("budget.decide", "budget_line", b.id, reason=body.note, context={"decision": body.decision.value})
    return BudgetOut.model_validate(b)


# ---------------- Media (consent enforced at upload and at every render) ----------------
@router.post("/media", status_code=201)
async def upload_media(
    ctx: CtxDep, file: UploadFile = File(...), edition_id: uuid.UUID | None = Form(None),
    student_ids: str = Form(""), caption: str | None = Form(None), is_artefact: bool = Form(False),
) -> dict:
    ctx.require(Cap.UPLOAD_MEDIA)
    if file.content_type not in MEDIA_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Unsupported type {file.content_type}")
    ids = [uuid.UUID(x) for x in student_ids.split(",") if x.strip()]
    for sid in ids:
        await ensure_student_in_scope(ctx.session, ctx.principal, sid)
    if not is_artefact:
        consented = await media_consented_ids(ctx.session, ids)
        refused = [str(sid) for sid in ids if sid not in consented]
        if refused:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                {"message": "These students do not have media consent and cannot be tagged in event media.",
                                 "student_ids": refused})
    data = await file.read(MAX_MEDIA_BYTES + 1)
    if len(data) > MAX_MEDIA_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (50 MB max)")
    mid = uuid.uuid4()
    key = f"{ctx.tenant.id}/media/{mid}"
    await storage.put(key, data, file.content_type or "application/octet-stream")
    m = MediaAsset(id=mid, edition_id=edition_id, storage_key=key, content_type=file.content_type or "",
                   size_bytes=len(data), caption=caption, is_artefact=is_artefact, uploaded_by_id=ctx.user_id)
    ctx.session.add(m)
    await ctx.session.flush()
    for sid in ids:
        ctx.session.add(MediaSubject(media_id=m.id, student_id=sid))
    ctx.audit("media.upload", "media", m.id, context={"students": len(ids), "edition_id": edition_id})
    return {"id": m.id, "storage_key": key}


@router.get("/media")
async def list_media(ctx: CtxDep, edition_id: uuid.UUID | None = None) -> list[dict]:
    """Reads ONLY from the media_renderable view: media showing any student without media
    consent is excluded by the database, whatever the caller asks for."""
    ctx.require(Cap.VIEW_ROSTER)
    sql = "SELECT id, edition_id, storage_key, content_type, caption, created_at FROM media_renderable"
    params: dict = {}
    if edition_id:
        sql += " WHERE edition_id = :eid"
        params["eid"] = edition_id
    rows = (await ctx.session.execute(text(sql + " ORDER BY created_at DESC LIMIT 200"), params)).all()
    return [{"id": r.id, "edition_id": r.edition_id, "content_type": r.content_type, "caption": r.caption,
             "url": storage.signed_url(ctx.tenant.id, r.storage_key, f"{r.id}", r.content_type),
             "created_at": r.created_at} for r in rows]


# ---------------- Audit log ----------------
@router.get("/audit")
async def audit_log(
    ctx: CtxDep, actor_user_id: uuid.UUID | None = None, subject_id: uuid.UUID | None = None,
    action: str | None = None, limit: int = Query(100, le=1000), offset: int = 0,
) -> dict:
    """Leaders see all events; programme admins see their own actions."""
    ctx.require(Cap.VIEW_AUDIT_OWN)
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc())
    if not ctx.principal.can(Cap.VIEW_AUDIT_ALL):
        stmt = stmt.where(AuditEvent.actor_user_id == ctx.user_id)
    elif actor_user_id:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_user_id)
    if subject_id:
        stmt = stmt.where(AuditEvent.subject_id == subject_id)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    total = await ctx.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (await ctx.session.scalars(stmt.limit(limit).offset(offset))).all()
    return {"total": total, "items": [
        {"id": e.id, "at": e.created_at, "actor_user_id": e.actor_user_id, "actor_roles": e.actor_roles,
         "action": e.action, "subject_type": e.subject_type, "subject_id": e.subject_id, "reason": e.reason,
         "ip": e.ip, "context": e.context} for e in rows]}

