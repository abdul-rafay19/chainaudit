from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response

from app.api.builders import build_audit, build_detail, rule_set_info
from app.api.deps import get_container
from app.container import Container
from app.core import pdf as pdfmod
from app.core.errors import AppError, ErrorResponse, bad_request, not_found
from app.core.storage import read_limited, validate_upload
from app.enums import ComplianceStatus, EventType
from app.replay import list_replays, start_replay
from app.schemas.domain import (
    AuditRecord,
    DispatchRequest,
    DispatchResponse,
    Finding,
    FindingDetail,
    ReplayInfo,
    ReplayStartResponse,
    RerunRequest,
    RerunResponse,
    ReviewRequest,
    ReviewResponse,
    RuleSetInfo,
    UploadResponse,
    WorkflowDetail,
    WorkflowSummary,
)

ERR: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    415: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
router = APIRouter(prefix="/api", responses=ERR)


async def _validated(c: Container, files: list[UploadFile]):  # type: ignore[no-untyped-def]
    if not files:
        raise bad_request("NO_FILES", "At least one file is required")
    if len(files) > c.settings.max_files_per_upload:
        raise AppError(413, "TOO_MANY_FILES", f"At most {c.settings.max_files_per_upload} files per upload")
    out = []
    for f in files:
        data = await read_limited(f, c.settings.max_upload_bytes)
        out.append(validate_upload(f.filename, data))
    return out  # every file validated BEFORE anything is created: no half-made workflows


# ---------------------------------------------------------------- evidence / workflows
@router.post("/evidence/upload", response_model=UploadResponse, tags=["workflows"])
async def upload(files: list[UploadFile] = File(...), rule_set_id: str | None = Form(default=None), c: Container = Depends(get_container)) -> UploadResponse:
    uploads = await _validated(c, files)
    wid, ids = await c.orchestrator.create_from_uploads(uploads, rule_set_id)
    return UploadResponse(workflow_id=wid, document_ids=ids)


@router.post("/workflows/{workflow_id}/evidence", response_model=UploadResponse, tags=["workflows"])
async def add_evidence(workflow_id: str, files: list[UploadFile] = File(...), c: Container = Depends(get_container)) -> UploadResponse:
    await asyncio.to_thread(c.repo.require_workflow, workflow_id)
    uploads = await _validated(c, files)
    ids = await c.orchestrator.add_evidence(workflow_id, uploads)
    return UploadResponse(workflow_id=workflow_id, document_ids=ids)


@router.get("/workflows", response_model=list[WorkflowSummary], tags=["workflows"])
def list_workflows(c: Container = Depends(get_container)) -> list[WorkflowSummary]:
    return c.repo.list_workflows()


@router.get("/workflows/{workflow_id}", response_model=WorkflowDetail, tags=["workflows"])
def get_workflow(workflow_id: str, c: Container = Depends(get_container)) -> WorkflowDetail:
    return build_detail(c, workflow_id)


@router.post("/workflows/{workflow_id}/rerun-rules", response_model=RerunResponse, tags=["rules"])
async def rerun_rules(workflow_id: str, body: RerunRequest, c: Container = Depends(get_container)) -> RerunResponse:
    return await c.orchestrator.rerun_rules(workflow_id, body.rule_set_id, body.overrides)


# ---------------------------------------------------------------- findings
@router.get("/findings", response_model=list[Finding], tags=["findings"])
def list_findings(workflow_id: str | None = None, status: ComplianceStatus | None = None, needs_review: bool | None = None, c: Container = Depends(get_container)) -> list[Finding]:
    if workflow_id:
        c.repo.require_workflow(workflow_id)
    return c.repo.findings(workflow_id, status=status.value if status else None, needs_review=needs_review, include_superseded=False)


@router.get("/findings/{finding_id}", response_model=FindingDetail, tags=["findings"])
def get_finding(finding_id: str, c: Container = Depends(get_container)) -> FindingDetail:
    f = c.repo.require_finding(finding_id)
    events = c.bus.history_sync(f.workflow_id)
    docs = {e.document_id for e in f.evidence}
    fields = {(e.document_id, e.field) for e in f.evidence}
    mine = [
        ev
        for ev in events
        if ev.finding_id == finding_id
        or (ev.document_id in docs and ev.type in {EventType.EXTRACTION_COMPLETED, EventType.REEXTRACTION_REQUESTED, EventType.REEXTRACTION_COMPLETED})
    ]
    checks = [
        ev
        for ev in events
        if ev.type in {EventType.AUDIT_CHECK_RESULT, EventType.AUDIT_CONFLICT_FOUND}
        and (ev.finding_id == finding_id or (ev.document_id and (ev.document_id, str(ev.data.get("field"))) in fields) or finding_id in (ev.data.get("finding_ids") or []))
    ]
    return FindingDetail(finding=f, events=mine, audit_checks=checks)


@router.get("/findings/{finding_id}/evidence-image", tags=["findings"], responses={200: {"content": {"image/png": {}}}})
async def evidence_image(finding_id: str, evidence_index: int = Query(default=0, ge=0), c: Container = Depends(get_container)) -> Response:
    f = c.repo.require_finding(finding_id)
    if evidence_index >= len(f.evidence):
        raise not_found("Evidence", str(evidence_index))
    e = f.evidence[evidence_index]
    row = c.repo.get_document(e.document_id) if e.document_id else None
    if row is None or not isinstance(e.page, int):
        raise not_found("Evidence page", f"{finding_id}#{evidence_index}")
    path = Path(row.stored_path)
    tag = hashlib.sha256(f"{row.sha256}|{e.page}|{e.bbox}|150".encode()).hexdigest()[:24]
    cache = c.settings.render_dir / f"{tag}.png"

    def render() -> bytes:
        if cache.exists():
            return cache.read_bytes()
        png = pdfmod.render_highlighted(path, e.page, e.bbox, 150)  # type: ignore[arg-type]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(png)
        return png

    try:
        png = await asyncio.to_thread(render)
    except (pdfmod.PdfError, IndexError) as err:
        raise not_found("Evidence page", f"{finding_id}#{evidence_index}") from err
    return Response(content=png, media_type="image/png", headers={"X-Highlight": "bbox" if e.bbox else "none", "Cache-Control": "private, max-age=300"})


@router.post("/findings/{finding_id}/dispatch", response_model=DispatchResponse, tags=["findings"])
async def dispatch(finding_id: str, body: DispatchRequest | None = None, c: Container = Depends(get_container)) -> DispatchResponse:
    return await c.orchestrator.dispatch(finding_id, body or DispatchRequest())


@router.post("/reviews/{finding_id}", response_model=ReviewResponse, tags=["reviews"])
async def review(finding_id: str, body: ReviewRequest, c: Container = Depends(get_container)) -> ReviewResponse:
    return await c.orchestrator.submit_review(finding_id, body)


# ---------------------------------------------------------------- rules / audit / replays
@router.get("/rules", response_model=RuleSetInfo, tags=["rules"])
def active_rules(c: Container = Depends(get_container)) -> RuleSetInfo:
    return rule_set_info(c, c.default_rule_set_id)


@router.get("/rule-sets/{rule_set_id:path}", response_model=RuleSetInfo, tags=["rules"])
def get_rule_set(rule_set_id: str, c: Container = Depends(get_container)) -> RuleSetInfo:
    return rule_set_info(c, rule_set_id)


@router.get("/audit/{workflow_id}", response_model=AuditRecord, tags=["audit"])
def audit(workflow_id: str, c: Container = Depends(get_container)) -> AuditRecord:
    return build_audit(c, workflow_id)


@router.get("/replays", response_model=list[ReplayInfo], tags=["replays"])
def replays(c: Container = Depends(get_container)) -> list[ReplayInfo]:
    return list_replays(c.settings.replay_dir)


@router.post("/replays/{name}/start", response_model=ReplayStartResponse, tags=["replays"])
async def replay_start(name: str, c: Container = Depends(get_container)) -> ReplayStartResponse:
    wid = await start_replay(c, name)
    return ReplayStartResponse(workflow_id=wid, name=name)
