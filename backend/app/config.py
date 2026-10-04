from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    database_url: str = "sqlite:///./chainaudit.db"
    upload_dir: Path = Path("./data/uploads")
    render_dir: Path = Path("./data/renders")
    cors_origins: str = "http://localhost:3000"
    llm_provider: str = "mock"
    llm_model: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""
    nvidia_api_key: str = ""
    gemini_api_key: str = ""
    llm_timeout_seconds: float = 60
    llm_max_retries: int = 1
    extraction_concurrency: int = 4
    max_upload_mb: int = 25
    max_files_per_upload: int = 20
    max_reextractions_per_field: int = 2
    workflow_timeout_seconds: float = 600
    sse_idle_seconds: float = 3600
    mock_faults: str = ""
    replay_step_seconds: float = 0.35
    replay_dir: Path = Path(__file__).resolve().parents[2] / "demo_data" / "replays"

    @field_validator("llm_provider")
    @classmethod
    def _provider_ok(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"mock", "anthropic", "openai", "groq", "nvidia", "gemini"}:
            raise ValueError("LLM_PROVIDER must be one of: mock, anthropic, openai, groq, nvidia, gemini")
        return v

    @model_validator(mode="after")
    def _key_present(self) -> Settings:
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY to be set")
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("LLM_PROVIDER=openai requires OPENAI_API_KEY to be set")
        if self.llm_provider in {"groq", "nvidia", "gemini"} and not getattr(self, f"{self.llm_provider}_api_key"):
            raise ValueError(f"LLM_PROVIDER={self.llm_provider} requires {self.llm_provider.upper()}_API_KEY to be set")
        if self.llm_provider != "mock" and not self.llm_model:
            raise ValueError(f"LLM_PROVIDER={self.llm_provider} requires LLM_MODEL to be set")
        return self

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def fault_set(self) -> set[str]:
        return {f.strip() for f in self.mock_faults.split(",") if f.strip()}

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
