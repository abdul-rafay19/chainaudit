"""SQLAlchemy tables. `workflow_events` is insert-only (DB triggers + ORM guards)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Base(DeclarativeBase):
    pass


class WorkflowRow(Base):
    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    rule_set_id: Mapped[str] = mapped_column(String(120))
    evaluation_number: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    replay: Mapped[bool] = mapped_column(Boolean, default=False)
    replay_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class DocumentRow(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(String(600))
    sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    doc_type: Mapped[str] = mapped_column(String(32), default="unknown")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="received")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class ExtractionRow(Base):
    __tablename__ = "extractions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True, unique=True)
    extractor_version: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    result_json: Mapped[str] = mapped_column(Text)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    reextraction_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class ExtractionCacheRow(Base):
    __tablename__ = "extraction_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    result_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class SemanticCacheRow(Base):
    __tablename__ = "semantic_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    result_json: Mapped[str] = mapped_column(Text)


class RuleSetRow(Base):
    __tablename__ = "rule_sets"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(60))
    content_json: Mapped[str] = mapped_column(Text)
    derived_from: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class FindingRow(Base):
    __tablename__ = "findings"
    finding_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    evaluation_number: Mapped[int] = mapped_column(Integer)
    rule_id: Mapped[str] = mapped_column(String(80))
    compliance_status: Mapped[str] = mapped_column(String(40), index=True)
    audit_status: Mapped[str] = mapped_column(String(20))
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)
    data_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class ReviewRow(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.finding_id"), index=True)
    workflow_id: Mapped[str] = mapped_column(String(40), index=True)
    decision: Mapped[str] = mapped_column(String(20))
    edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class CorrectiveActionRow(Base):
    __tablename__ = "corrective_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.finding_id"), index=True)
    workflow_id: Mapped[str] = mapped_column(String(40), index=True)
    en: Mapped[str] = mapped_column(Text)
    roman_ur: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    label: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class OutboxRow(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.finding_id"), index=True)
    workflow_id: Mapped[str] = mapped_column(String(40), index=True)
    channel: Mapped[str] = mapped_column(String(30))
    language: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class WorkflowEventRow(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (UniqueConstraint("workflow_id", "seq", name="uq_event_seq"), Index("ix_event_wf_seq", "workflow_id", "seq"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(String(40))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(40))
    agent: Mapped[str] = mapped_column(String(40))
    timestamp: Mapped[str] = mapped_column(String(40))
    finding_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    replay: Mapped[bool] = mapped_column(Boolean, default=False)


class AppendOnlyError(RuntimeError):
    pass


@event.listens_for(WorkflowEventRow, "before_update")
def _no_event_update(*_a, **_k):  # type: ignore[no-untyped-def]
    raise AppendOnlyError("workflow_events is append-only")


@event.listens_for(WorkflowEventRow, "before_delete")
def _no_event_delete(*_a, **_k):  # type: ignore[no-untyped-def]
    raise AppendOnlyError("workflow_events is append-only")


@event.listens_for(RuleSetRow, "before_update")
def _no_rs_update(*_a, **_k):  # type: ignore[no-untyped-def]
    raise AppendOnlyError("rule_sets rows are immutable")
