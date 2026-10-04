from __future__ import annotations

import base64

from pydantic import BaseModel

from app.llm.base import LLMError, RawStructuredProvider, with_retry


class AnthropicProvider(RawStructuredProvider):
    name = "anthropic"
    supports_vision = True

    def __init__(self, api_key: str, model: str, max_retries: int = 1) -> None:
        try:
            from anthropic import AsyncAnthropic  # optional extra
        except ImportError as e:  # pragma: no cover
            raise LLMError("anthropic SDK not installed: pip install anthropic") from e
        self._client = AsyncAnthropic(api_key=api_key, max_retries=0)  # retries handled by tenacity
        self.model = model
        self.max_retries = max_retries

    @staticmethod
    def _content(user_text: str, images: list[bytes] | None) -> list[dict[str, object]]:
        blocks: list[dict[str, object]] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(img).decode()}} for img in images or []
        ]
        blocks.append({"type": "text", "text": user_text})
        return blocks

    async def _raw_structured(self, *, system: str, user_text: str, images: list[bytes] | None, schema: type[BaseModel], timeout: float) -> object:
        tool = {"name": "submit_result", "description": f"Return the {schema.__name__} result.", "input_schema": schema.model_json_schema()}
        resp = await self._client.messages.create(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": self._content(user_text, images)}],  # type: ignore[list-item]
            tools=[tool],  # type: ignore[list-item]
            tool_choice={"type": "tool", "name": "submit_result"},
            timeout=timeout,
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return block.input
        raise LLMError("anthropic returned no tool_use block")

    async def generate_text(self, *, system: str, user_text: str, timeout: float) -> str:
        async def call() -> str:
            resp = await self._client.messages.create(model=self.model, max_tokens=2048, system=system, messages=[{"role": "user", "content": user_text}], timeout=timeout)
            return "".join(getattr(b, "text", "") for b in resp.content)

        try:
            return await with_retry(call, self.max_retries)
        except Exception as e:
            raise LLMError(f"anthropic call failed: {type(e).__name__}") from e
