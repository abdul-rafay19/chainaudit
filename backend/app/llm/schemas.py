"""LLM-facing output schemas. All fields are required (nullable) so they work with strict
structured-output modes. These are NOT the domain schemas: verification/bbox/source ids are
added by deterministic code afterwards."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LLMField(BaseModel):
    field: str
    value: str | float | None
    unit: str | None
    page: int | None
    quote: str | None
    confidence: float = Field(ge=0, le=1)


class LLMCertification(BaseModel):
    name: LLMField
    issued_date: LLMField | None
    valid_until: LLMField | None


class ExtractionOutput(BaseModel):
    supplier_id: LLMField | None
    batch_id: LLMField | None
    document_date: LLMField | None
    test_results: list[LLMField]
    certifications: list[LLMCertification]
    other: list[LLMField]
    notes: list[str]


class FieldReextraction(BaseModel):
    field: LLMField


class ClassificationOutput(BaseModel):
    doc_type: Literal["lab_report", "certificate", "supplier_declaration", "unknown"]
    confidence: float = Field(ge=0, le=1)


class SemanticCheckResult(BaseModel):
    supported: bool
    rationale: str
    concerns: list[str]


class CorrectiveText(BaseModel):
    en: str
    roman_ur: str
