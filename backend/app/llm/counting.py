"""Transparent wrapper that counts provider calls (used to prove rule re-runs make 0 LLM calls)."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from app.llm.base import LLMProvider

T = TypeVar("T", bound=BaseModel)


class CountingProvider:
    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner
        self.calls = 0

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def supports_vision(self) -> bool:
        return self.inner.supports_vision

    async def generate_structured(self, *, system: str, user_text: str, images: list[bytes] | None, schema: type[T], timeout: float) -> T:
        self.calls += 1
        return await self.inner.generate_structured(system=system, user_text=user_text, images=images, schema=schema, timeout=timeout)

    async def generate_text(self, *, system: str, user_text: str, timeout: float) -> str:
        self.calls += 1
        return await self.inner.generate_text(system=system, user_text=user_text, timeout=timeout)
