"""Validated rule-set models. Unknown rule types / missing fields fail at load time."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.enums import DocType


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NumericMaxRule(_Strict):
    id: str
    title: str
    type: Literal["numeric_max"]
    field: str
    limit: float
    unit: str


class RequiredCertificationRule(_Strict):
    id: str
    title: str
    type: Literal["required_certification"]
    value: str


class DateWindowRule(_Strict):
    id: str
    title: str
    type: Literal["date_window"]
    field: Literal["document_date"]
    document_types: list[DocType]
    max_age_days: int = Field(ge=0)
    relative_to: Literal["reference_date"] = "reference_date"


class ConsistencyRule(_Strict):
    id: str
    title: str
    type: Literal["consistency"]
    fields: list[Literal["supplier_id", "batch_id"]] = Field(min_length=1)


Rule = Annotated[
    NumericMaxRule | RequiredCertificationRule | DateWindowRule | ConsistencyRule,
    Field(discriminator="type"),
]


class RuleSet(_Strict):
    framework: str
    version: str
    label: str
    last_reviewed: str
    reference_date: date
    min_extraction_confidence: float = Field(ge=0, le=1)
    rules: list[Rule] = Field(min_length=1)

    @field_validator("version", mode="before")
    @classmethod
    def _v(cls, v: object) -> str:
        return str(v)

    @field_validator("last_reviewed", mode="before")
    @classmethod
    def _lr(cls, v: object) -> str:
        return str(v)

    @model_validator(mode="after")
    def _unique_ids(self) -> RuleSet:
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids must be unique")
        return self

    @property
    def tag(self) -> str:
        return f"{self.framework} v{self.version}"
