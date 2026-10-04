from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")


class LLMError(Exception):
    """Provider call failed (after retries)."""


class LLMOutputError(LLMError):
    """Provider returned output that does not satisfy the schema, even after one corrective retry."""


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def supports_vision(self) -> bool: ...

    async def generate_structured(self, *, system: str, user_text: str, images: list[bytes] | None, schema: type[T], timeout: float) -> T: ...

    async def generate_text(self, *, system: str, user_text: str, timeout: float) -> str: ...


def is_transient(exc: BaseException) -> bool:
    """Timeouts, 429 and 5xx are retried; everything else is not."""
    if isinstance(exc, asyncio.TimeoutError | TimeoutError):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    return type(exc).__name__ in {"APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError", "ReadTimeout", "ConnectTimeout"}


async def with_retry(fn: Callable[[], Awaitable[R]], max_retries: int) -> R:
    async for attempt in AsyncRetrying(
        retry=retry_if_exception(is_transient),
        stop=stop_after_attempt(max_retries + 1),
        wait=wait_random_exponential(multiplier=0.5, max=8),
        reraise=True,
    ):
        with attempt:
            return await fn()
    raise AssertionError("unreachable")  # pragma: no cover


class RawStructuredProvider:
    """Shared validation + corrective-retry logic for real providers.

    Subclasses implement `_raw_structured` returning a parsed JSON object; this class
    ALWAYS validates with the Pydantic schema and retries once with the validation error.
    """

    name: str
    model: str
    supports_vision = True
    max_retries: int = 1

    async def _raw_structured(self, *, system: str, user_text: str, images: list[bytes] | None, schema: type[BaseModel], timeout: float) -> object:
        raise NotImplementedError

    async def generate_structured(self, *, system: str, user_text: str, images: list[bytes] | None, schema: type[T], timeout: float) -> T:
        text = user_text
        last: ValidationError | None = None
        for _ in range(2):
            try:
                raw = await with_retry(lambda t=text: self._raw_structured(system=system, user_text=t, images=images, schema=schema, timeout=timeout), self.max_retries)  # type: ignore[misc]
            except LLMError:
                raise
            except Exception as e:
                raise LLMError(f"{self.name} call failed: {type(e).__name__}") from e
            try:
                return schema.model_validate(raw)
            except ValidationError as e:
                last = e
                text = f"{user_text}\n\nYour previous output failed schema validation:\n{e}\nReturn corrected output that satisfies the schema exactly."
        raise LLMOutputError(f"{self.name} output failed schema validation: {last}")
