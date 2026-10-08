"""E2 interactive routes. The same JWT authorizes every GET in Supabase."""
from __future__ import annotations

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from .auth import ADMIN, EXECUTE, AuthFailure, authorize
from .supabase_access import PreviewError, uuid_value
from .quality_gates import QualityPolicy, data_quality, forecast_quality, observed_service
from .operational_preview import snapshot_hash
from .vintage_service import candidate_payload


class PreviewRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_id: str | None = None
    product_id: str | None = None
    objective: str = "Venta"
    issue_period: str | None = Field(default=None, pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    mode: str = "RETROSPECTIVE_TRAINING"


class CandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_id: str
    preview_id: str


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool
    comment: str = Field(min_length=5, max_length=1000)


class PromoteRequest(ApprovalRequest):
    vintage_id: str
    model_version_id: str


class ClosePeriodRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_period: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])-01$")
    confirm: bool


def mount_preview_routes(app, config, identity, data, runner, limiter, write_repository=None):
    router = APIRouter(prefix="/api/forecast")

    def quality_policy():
        if config.quality_policy_version == "E3-GATES-2.0.0":
            return QualityPolicy.selection_v2()
        return QualityPolicy(version=config.quality_policy_version,
            minimum_history_months=config.minimum_history_months,
            warning_continuity_rate=config.warning_continuity_rate,
            ready_continuity_rate=config.ready_continuity_rate,
            minimum_improvement_points=config.minimum_improvement_points,
            maximum_bias_deterioration=config.maximum_bias_deterioration,
            critical_horizon_degradation=config.critical_horizon_degradation,
            target_fill_rate=config.service_target_fill_rate)

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

    @router.get("/preview-runs/{job_id}/research")
    def research(job_id: str, request: Request, authorization: str | None = Header(default=None)):
        principal, _ = actor(request, authorization)
        if not limiter.allow("research-poll:" + principal.user_id, config.forecast_rate_per_minute):
            raise PreviewError("RATE_001", 429)
        return runner.poll_research(job_id, principal)

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

    @router.get("/quality-gates")
    def quality_gates(request: Request, chain_id: str, authorization: str | None = Header(default=None)):
        principal, token = actor(request, authorization)
        chain_id = uuid_value(chain_id)
        if chain_id not in principal.chain_ids:
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        latest_job = runner.latest(principal, chain_id, None)
        if latest_job.get("status") != "READY_PREVIEW":
            raise PreviewError("PREVIEW_NOT_READY", 409)
        saved_job = runner.persistence.get("forecast_jobs", latest_job["job_id"])
        reference = next((scope for scope in (saved_job or {}).get("scopes", []) if scope.get("chain_id") == chain_id), None)
        if not reference or not reference.get("preview_id"):
            raise PreviewError("PREVIEW_NOT_READY", 409)
        preview = runner.persistence.get("forecast_previews", reference["preview_id"])
        if not preview:
            raise PreviewError("PREVIEW_NOT_READY", 409)
        source = data.bind(token)
        sales = source.records(chain_id=chain_id, issue_period=preview["issue_period"], mode=preview["mode"])
        if snapshot_hash(sales) != preview["dataset_hash"]:
            raise PreviewError("PREVIEW_NOT_READY", 409)
        policy = quality_policy()
        raw = source.client.rows("portal_monthly_observations_current", {
            "select": "product_id,period,metric_code,value", "chain_id": f"eq.{chain_id}",
            "metric_code": "in.(ORDER,DELIVERY)", "period": f"eq.{preview['issue_period']}-01",
            "order": "product_id.asc,period.asc"})
        return {"chain_id": chain_id, "preview_id": reference["preview_id"], "dataset_hash": preview["dataset_hash"],
            "policy_version": policy.version, "data_quality": data_quality(sales, preview, policy),
            "forecast_quality": forecast_quality(preview, policy),
            "service_level": observed_service(raw, policy), "publication": "PROVISIONAL"}

    @router.get("/vintages")
    def vintages(request: Request, chain_id: str, authorization: str | None = Header(default=None)):
        principal, token = actor(request, authorization)
        chain_id = uuid_value(chain_id)
        if chain_id not in principal.chain_ids:
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        rows = data.bind(token).client.rows("forecast_vintages", {"select": "id,run_id,chain_id,objective,issue_period,cutoff_at,forecast_version,certification_status,frozen_at,created_at",
            "chain_id": f"eq.{chain_id}", "order": "created_at.desc,id.desc"})
        if any(row.get("chain_id") != chain_id for row in rows):
            raise PreviewError("DATA_READ_FAILED", 503)
        return {"chain_id": chain_id, "vintages": rows}

    @router.get("/vintages/{vintage_id}")
    def vintage_detail(vintage_id: str, request: Request, authorization: str | None = Header(default=None)):
        principal, token = actor(request, authorization)
        vintage_id = uuid_value(vintage_id)
        client = data.bind(token).client
        found = client.rows("forecast_vintages", {"select": "*", "id": f"eq.{vintage_id}", "order": "id.asc"})
        if len(found) != 1 or found[0].get("chain_id") not in principal.chain_ids:
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        vintage = found[0]
        chain_id = vintage["chain_id"]
        tables = {"horizons": ("forecast_horizons", "vintage_id"),
                  "aggregates": ("forecast_aggregates", "vintage_id"),
                  "metrics": ("performance_metrics", "vintage_id"),
                  "models": ("model_versions", "run_id"),
                  "inputs": ("forecast_run_inputs", "run_id"),
                  "gates": ("forecast_quality_gates", "vintage_id")}
        detail = {"vintage": vintage}
        runs = client.rows("forecast_runs", {"select": "id,chain_id,objective,issue_period,cutoff_at,status,data_snapshot_hash,actor_id,engine_version,git_sha",
            "id": f"eq.{vintage['run_id']}", "chain_id": f"eq.{chain_id}", "order": "id.asc"})
        if len(runs) != 1 or runs[0].get("chain_id") != chain_id:
            raise PreviewError("DATA_READ_FAILED", 503)
        detail["run"] = runs[0]
        for label, (table, column) in tables.items():
            key = vintage["run_id"] if column == "run_id" else vintage_id
            rows = client.rows(table, {"select": "*", column: f"eq.{key}", "chain_id": f"eq.{chain_id}", "order": "id.asc"})
            if any(row.get("chain_id") != chain_id for row in rows):
                raise PreviewError("DATA_READ_FAILED", 503)
            detail[label] = rows
        return detail

    def manager(request: Request, authorization: str | None):
        principal, token = actor(request, authorization)
        try:
            authorize(principal, ADMIN)
        except AuthFailure:
            raise PreviewError("MANAGER_REQUIRED", 403) from None
        return principal, token

    def writer():
        if not config.supabase_service_role_key or write_repository is None:
            raise PreviewError("REQUIRED_SECRET_MISSING", 503)
        return write_repository

    def idempotency(value: str | None):
        if not value or len(value) > 128 or any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise PreviewError("REQUEST_001", 422)

    @router.post("/vintages/candidates")
    def create_candidate(request: Request, body: CandidateRequest | None = None, authorization: str | None = Header(default=None),
                         idempotency_key: str | None = Header(default=None)):
        principal, token = manager(request, authorization)
        if not config.vintage_persistence_enabled:
            raise PreviewError("VINTAGE_PERSISTENCE_DISABLED", 503)
        if body is None:
            raise PreviewError("REQUEST_001", 422)
        idempotency(idempotency_key)
        write = writer()
        chain_id = uuid_value(body.chain_id)
        if chain_id not in principal.execute_chain_ids:
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        latest_job = runner.latest(principal, chain_id, None)
        saved_job = runner.persistence.get("forecast_jobs", latest_job["job_id"])
        reference = next((scope for scope in (saved_job or {}).get("scopes", []) if scope.get("chain_id") == chain_id), None)
        if latest_job.get("status") != "READY_PREVIEW" or not reference or reference.get("status") != "READY_PREVIEW" or reference.get("preview_id") != body.preview_id:
            raise PreviewError("PREVIEW_NOT_READY", 409)
        preview = runner.persistence.get("forecast_previews", body.preview_id)
        if not preview or preview.get("chain_id") != chain_id:
            raise PreviewError("LINEAGE_MISMATCH", 409)
        source = data.bind(token)
        sales = source.records(chain_id=chain_id, issue_period=preview["issue_period"], mode=preview["mode"])
        service_rows = source.client.rows("portal_monthly_observations_current", {
            "select": "product_id,period,metric_code,value", "chain_id": f"eq.{chain_id}",
            "metric_code": "in.(ORDER,DELIVERY)", "period": f"eq.{preview['issue_period']}-01",
            "order": "product_id.asc,period.asc"})
        payload = candidate_payload(preview, sales, service_rows, actor_id=principal.user_id,
            git_sha=config.git_sha, policy=quality_policy(),
            preview_id=body.preview_id)
        vintage_id = write.rpc("e3_create_vintage_candidate", {"p": payload})
        return {"vintage_id": vintage_id, "content_hash": payload["content_hash"],
            "certification_status": "PROVISIONAL", "official_publication": False}

    @router.post("/vintages/{vintage_id}/freeze")
    def freeze(vintage_id: str, request: Request, authorization: str | None = Header(default=None),
               idempotency_key: str | None = Header(default=None)):
        principal, token = manager(request, authorization)
        uuid_value(vintage_id)
        if not config.vintage_persistence_enabled:
            raise PreviewError("VINTAGE_PERSISTENCE_DISABLED", 503)
        idempotency(idempotency_key)
        detail = vintage_detail(vintage_id, request, authorization)
        if detail["vintage"]["chain_id"] not in principal.execute_chain_ids:
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        frozen_id = writer().rpc("e3_freeze_vintage", {"p_vintage_id": vintage_id, "p_actor_id": principal.user_id})
        return {"vintage_id": frozen_id, "status": "FROZEN"}

    @router.post("/vintages/{vintage_id}/publish")
    def publish(vintage_id: str, request: Request, body: ApprovalRequest | None = None, authorization: str | None = Header(default=None),
                idempotency_key: str | None = Header(default=None)):
        principal, token = manager(request, authorization)
        uuid_value(vintage_id)
        if not config.official_publication_enabled:
            raise PreviewError("OFFICIAL_PUBLICATION_DISABLED", 503)
        if body is None:
            raise PreviewError("REQUEST_001", 422)
        idempotency(idempotency_key)
        if not body.confirm:
            raise PreviewError("REQUEST_001", 422)
        detail = vintage_detail(vintage_id, request, authorization)
        if detail["vintage"]["chain_id"] not in principal.execute_chain_ids:
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        published_id = writer().rpc("e3_publish_official", {"p_vintage_id": vintage_id,
            "p_actor_id": principal.user_id, "p_comment": body.comment})
        return {"vintage_id": published_id, "status": "OFFICIAL"}

    @router.post("/vintages/{vintage_id}/close")
    def close_period(vintage_id: str, request: Request, body: ClosePeriodRequest,
                     authorization: str | None = Header(default=None),
                     idempotency_key: str | None = Header(default=None)):
        principal, _ = manager(request, authorization)
        vintage_id = uuid_value(vintage_id)
        if not config.vintage_persistence_enabled:
            raise PreviewError("VINTAGE_PERSISTENCE_DISABLED", 503)
        idempotency(idempotency_key)
        if not body.confirm:
            raise PreviewError("REQUEST_001", 422)
        detail = vintage_detail(vintage_id, request, authorization)
        if detail["vintage"]["chain_id"] not in principal.execute_chain_ids:
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        return writer().rpc("e3_close_target_period", {"p_vintage_id": vintage_id,
            "p_target_period": body.target_period, "p_actor_id": principal.user_id})

    @router.post("/champion/promote")
    def promote(request: Request, body: PromoteRequest | None = None, authorization: str | None = Header(default=None),
                idempotency_key: str | None = Header(default=None)):
        principal, token = manager(request, authorization)
        if not config.champion_publication_enabled:
            raise PreviewError("CHAMPION_PUBLICATION_DISABLED", 503)
        if body is None:
            raise PreviewError("REQUEST_001", 422)
        idempotency(idempotency_key)
        if not body.confirm:
            raise PreviewError("REQUEST_001", 422)
        detail = vintage_detail(body.vintage_id, request, authorization)
        if detail["vintage"]["chain_id"] not in principal.execute_chain_ids:
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        champion_id = writer().rpc("e3_promote_champion", {"p_vintage_id": uuid_value(body.vintage_id),
            "p_model_version_id": uuid_value(body.model_version_id), "p_actor_id": principal.user_id,
            "p_comment": body.comment})
        return {"champion_id": champion_id, "status": "ACTIVE"}

    app.include_router(router)
