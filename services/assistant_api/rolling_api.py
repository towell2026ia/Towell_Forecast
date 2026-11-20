"""Explicit rolling execution and scoped read/choice routes, disabled by default."""
from __future__ import annotations

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthFailure
from .supabase_access import PreviewError


class PlanningScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_context: dict
    chain_ids: list[str] = Field(min_length=1, max_length=100)
    product_ids: list[str] = Field(min_length=1, max_length=2000)


class PlanningCreateRequest(PlanningScopeRequest):
    objective: str = "Pedido"
    issue_period: str | None = Field(default=None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")


class PlanningChampionReadRequest(PlanningScopeRequest):
    window_start: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    window_end: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")


class PlanningChampionRequest(PlanningChampionReadRequest):
    candidate_family: str
    confirm: bool
    comment: str = Field(min_length=5, max_length=1000)
    accept_cold_start: bool = False
    acknowledgements: list[str] = Field(default_factory=list, max_length=20)


def mount_rolling_routes(app, config, identity, data, service, limiter):
    router = APIRouter(prefix="/api/forecast/planning-runs")

    def actor(request, authorization):
        if service is None or not getattr(config, "rolling_planning_enabled", False):
            raise PreviewError("ROLLING_PLANNING_DISABLED", 503)
        if not authorization or not authorization.startswith("Bearer "):
            raise PreviewError("AUTH_REQUIRED", 401)
        try:
            principal = identity.authenticate(token=authorization[7:], actor_id=None, client_host=None)
        except AuthFailure as error:
            raise PreviewError("AUTH_REQUIRED" if error.status_code == 401 else "SCOPE_FORBIDDEN", error.status_code) from None
        request.state.user_id = principal.user_id
        return principal, data.bind(authorization[7:])

    @router.post("", status_code=202)
    def create(body: PlanningCreateRequest, request: Request, authorization: str | None = Header(default=None),
               idempotency_key: str | None = Header(default=None)):
        principal, provider = actor(request, authorization)
        if not limiter.allow("rolling-create:" + principal.user_id, config.forecast_rate_per_minute):
            raise PreviewError("RATE_001", 429)
        return service.create(body.model_dump(), principal, provider, idempotency_key=idempotency_key)

    @router.get("")
    def runs(request: Request, authorization: str | None = Header(default=None)):
        principal, provider = actor(request, authorization)
        return service.list(principal, provider)

    @router.get("/{run_id}")
    def status(run_id: str, request: Request, authorization: str | None = Header(default=None)):
        principal, provider = actor(request, authorization)
        return service.summary(run_id, principal, provider)

    @router.post("/{run_id}/report")
    def report(run_id: str, body: PlanningScopeRequest, request: Request, authorization: str | None = Header(default=None)):
        principal, provider = actor(request, authorization)
        return service.report(run_id, principal, provider, body.model_dump())

    @router.post("/{run_id}/champion")
    def current(run_id: str, body: PlanningChampionReadRequest, request: Request, authorization: str | None = Header(default=None)):
        principal, provider = actor(request, authorization)
        return service.current_champion(run_id, principal, provider, body.model_dump())

    @router.post("/{run_id}/champion/promote")
    def promote(run_id: str, body: PlanningChampionRequest, request: Request, authorization: str | None = Header(default=None),
                idempotency_key: str | None = Header(default=None)):
        principal, provider = actor(request, authorization)
        event = service.promote(run_id, principal, provider, body.model_dump(), idempotency_key=idempotency_key)
        return {"champion_id": event["id"], "status": event["status"], "run_id": run_id,
                "window_start": event["window_start"], "window_end": event["window_end"], "source_context": event["source_context"]}

    app.include_router(router)
