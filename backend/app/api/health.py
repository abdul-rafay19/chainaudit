from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.container import Container
from app.schemas.domain import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(c: Container = Depends(get_container)) -> HealthResponse:
    return HealthResponse(
        app_env=c.settings.app_env,
        provider=c.provider.name,
        model=c.provider.model,
        mock=c.provider.name == "mock",
        fault_injection=sorted(c.settings.fault_set) if c.provider.name == "mock" else [],
        rule_set=c.default_rule_set_id,
    )
