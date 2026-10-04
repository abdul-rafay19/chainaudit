from __future__ import annotations

import base64
import json

from pydantic import BaseModel

from app.llm.base import LLMError, RawStructuredProvider, with_retry


class OpenAIProvider(RawStructuredProvider):
    name = "openai"
    supports_vision = True

    def __init__(self, api_key: str, model: str, max_retries: int = 1, base_url: str | None = None, name: str = "openai") -> None:
        try:
            from openai import AsyncOpenAI  # optional extra
        except ImportError as e:  # pragma: no cover
            raise LLMError("openai SDK not installed: pip install openai") from e
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)  # retries handled by tenacity
        self.model = model
        self.name = name
        self._json_mode = base_url is not None  # non-OpenAI hosts: json_object + schema in prompt
        self.max_retries = max_retries

    async def _raw_structured(self, *, system: str, user_text: str, images: list[bytes] | None, schema: type[BaseModel], timeout: float) -> object:
        parts: list[dict[str, object]] = [{"type": "text", "text": user_text}]
        for img in images or []:
            parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(img).decode()}})
        if self._json_mode:
            system += "\n\nReturn ONLY one JSON object matching this JSON Schema:\n" + json.dumps(schema.model_json_schema())
            rf: dict[str, object] = {"type": "json_object"}
        else:
            rf = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": False}}
        resp = await self._client.chat.completions.create(  # type: ignore[call-overload]
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": parts if images else user_text}],  # type: ignore[list-item]
            response_format=rf,
            timeout=timeout,
        )
        content = resp.choices[0].message.content or ""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"_invalid_json": content[:200]}  # fails schema validation -> corrective retry

    async def generate_text(self, *, system: str, user_text: str, timeout: float) -> str:
        async def call() -> str:
            resp = await self._client.chat.completions.create(
                model=self.model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user_text}], timeout=timeout
            )
            return resp.choices[0].message.content or ""

        try:
            return await with_retry(call, self.max_retries)
        except Exception as e:
            raise LLMError(f"openai call failed: {type(e).__name__}") from e
