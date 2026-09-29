"""E2 interactive routes. The same JWT authorizes every GET in Supabase."""
from __future__ import annotations

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from .auth import EXECUTE, AuthFailure, authorize
from .supabase_access import PreviewError, uuid_value


class PreviewRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_id: str | None = None
    product_id: str | None = None
    objective: str = "Venta"
    issue_period: str | None = Field(default=None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    mode: str = "RETROSPECTIVE_TRAINING"


def mount_preview_routes(app, config, identity, data, runner, limiter):
    router = APIRouter(prefix="/api/forecast")

    def actor(request, authorization):
        if not config.operational_preview_enabled:
            raise PreviewError("PREVIEW_DISABLED", 503)
        if not authorization or not authorization.startswith("Bearer "):
            raise PreviewError("AUTH_REQUIRED", 401)
        token = authorization[7:]
        try:
            principal = identity.authenticate(token=token, actor_id=None, client_host=None)
        except AuthFailure as error:
            raise PreviewError("AUTH_REQUIRED" if error.status_code == 401 else "SCOPE_FORBIDDEN", error.status_code) from None
        request.state.user_id = principal.user_id
        return principal, token

    @router.post("/preview-runs", status_code=202)
    def create(body: PreviewRunRequest, request: Request, authorization: str | None = Header(default=None)):
        principal, token = actor(request, authorization)
        try:
            authorize(principal, EXECUTE)
        except AuthFailure:
            raise PreviewError("SCOPE_FORBIDDEN", 403) from None
        if body.objective != "Venta" or body.mode not in {"RETROSPECTIVE_TRAINING", "POINT_IN_TIME"}:
            raise PreviewError("REQUEST_001")
        scopes = [uuid_value(body.chain_id)] if body.chain_id else sorted(principal.execute_chain_ids)
        if not scopes or not set(scopes).issubset(principal.execute_chain_ids):
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        if body.product_id:
            uuid_value(body.product_id)
            if not body.chain_id:
                raise PreviewError("REQUEST_001")
            products = data.bind(token).client.rows("products", {"select": "id", "id": f"eq.{body.product_id}",
                "chain_id": f"eq.{body.chain_id}", "order": "id.asc"})
            if len(products) != 1 or products[0].get("id") != body.product_id:
                raise PreviewError("SCOPE_FORBIDDEN", 403)
        if not limiter.allow("preview-create:" + principal.user_id, config.forecast_rate_per_minute):
            raise PreviewError("RATE_001", 429)
        return runner.submit(data.bind(token), principal, chain_ids=scopes, product_id=body.product_id,
                             mode=body.mode, issue_period=body.issue_period, all_scopes=body.chain_id is None)

    @router.get("/preview-runs/{job_id}")
    def status(job_id: str, request: Request, authorization: str | None = Header(default=None)):
        principal, _ = actor(request, authorization)
        return runner.get_job(job_id, principal)

    @router.get("/preview-runs/{job_id}/result")
    def result(job_id: str, request: Request, product_id: str | None = None, authorization: str | None = Header(default=None)):
        principal, _ = actor(request, authorization)
        if product_id:
            uuid_value(product_id)
        return runner.result(job_id, principal, product_id)

    @router.get("/preview-latest")
    def latest(request: Request, chain_id: str | None = None, product_id: str | None = None,
               mode: str = "RETROSPECTIVE_TRAINING", authorization: str | None = Header(default=None)):
        principal, _ = actor(request, authorization)
        if mode not in {"RETROSPECTIVE_TRAINING", "POINT_IN_TIME"}:
            raise PreviewError("REQUEST_001")
        if chain_id:
            uuid_value(chain_id)
        if product_id:
            uuid_value(product_id)
        return runner.latest(principal, chain_id, product_id, mode)

    app.include_router(router)
