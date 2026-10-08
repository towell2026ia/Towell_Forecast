"""Flag-gated E4 API. Browser JWTs read through RLS; writes use audited RPCs."""
from __future__ import annotations

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from .auth import ADMIN, EXECUTE, AuthFailure, authorize
from .calculation_history import snapshot_from_preview, live_metrics, validate_selection
from .operational_preview import snapshot_hash
from .supabase_access import PreviewError, uuid_value


class CalculationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_id: str
    product_id: str
    preview_id: str
    recalculation_reason: str | None = None


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_candidate: str
    decision_reason: str | None = None
    comment: str | None = None


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_id: str
    product_id: str
    period: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])-01$")
    order_value: float = Field(ge=0)
    sale_value: float = Field(ge=0)
    delivery_value: float = Field(ge=0)
    notes: str | None = Field(default=None, max_length=2000)
    correction_reason: str | None = Field(default=None, max_length=1000)


class LiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_id: str
    product_id: str
    target_period: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])-01$")


def mount_e4_routes(app, config, identity, data, runner, writer):
    router = APIRouter(prefix="/api/forecast")

    def actor(request, authorization, permission=EXECUTE):
        if not authorization or not authorization.startswith("Bearer "):
            raise PreviewError("AUTH_REQUIRED", 401)
        token = authorization[7:]
        try:
            principal = identity.authenticate(token=token, actor_id=None, client_host=None)
            if permission:
                authorize(principal, permission)
        except AuthFailure as error:
            raise PreviewError("AUTH_REQUIRED" if error.status_code == 401 else "SCOPE_FORBIDDEN", error.status_code) from None
        request.state.user_id = principal.user_id
        return principal, data.bind(token)

    def enabled(flag):
        if not getattr(config, flag):
            raise PreviewError("E4_DISABLED", 503)

    def key(value):
        if not value or len(value) > 128 or any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise PreviewError("IDEMPOTENCY_KEY_REQUIRED", 422)
        return value

    def allowed(principal, chain_id, *, execute=False):
        chain_id = uuid_value(chain_id)
        if chain_id not in (principal.execute_chain_ids if execute else principal.chain_ids):
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        return chain_id

    def write(name, payload):
        if writer is None:
            raise PreviewError("REQUIRED_SECRET_MISSING", 503)
        return writer.rpc(name, payload)

    def calculation(client, calculation_id):
        rows = client.rows("forecast_calculations", {"select": "*", "id": f"eq.{uuid_value(calculation_id)}"})
        if len(rows) != 1:
            raise PreviewError("CALCULATION_NOT_FOUND", 404)
        return rows[0]

    def detail(client, header):
        calc_id = header["id"]
        return {**header,
            "horizons": client.rows("forecast_calculation_horizons", {
                "select": "*", "calculation_id": f"eq.{calc_id}", "order": "horizon.asc"}),
            "selection_events": client.rows("forecast_selection_events", {
                "select": "*", "calculation_id": f"eq.{calc_id}", "order": "selected_at.asc,id.asc"}),
            "live_evaluations": client.rows("forecast_live_evaluations", {
                "select": "*", "calculation_id": f"eq.{calc_id}", "order": "horizon.asc,candidate.asc"})}

    @router.post("/calculations")
    def create(request: Request, body: CalculationRequest, authorization: str | None = Header(default=None),
               idempotency_key: str | None = Header(default=None)):
        enabled("forecast_calculation_history_enabled")
        principal, source = actor(request, authorization)
        chain, product = allowed(principal, body.chain_id, execute=True), uuid_value(body.product_id)
        key(idempotency_key)
        if body.recalculation_reason and len(body.recalculation_reason) > 1000:
            raise PreviewError("REQUEST_001", 422)
        catalog = source.product_catalog(chain)
        if not any(row["id"] == product for row in catalog):
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        preview = runner.persistence.get("forecast_previews", body.preview_id)
        if not preview or preview.get("chain_id") != chain or preview.get("objective") != "Venta":
            raise PreviewError("PREVIEW_NOT_READY", 409)
        rows = source.records(chain_id=chain, issue_period=preview["issue_period"],
            mode=preview.get("mode") or preview.get("evidence_mode") or "RETROSPECTIVE_TRAINING")
        if snapshot_hash(rows) != preview.get("dataset_hash"):
            raise PreviewError("HASH_MISMATCH", 409)
        payload = snapshot_from_preview(preview, product_id=product, preview_id=body.preview_id,
            actor_id=principal.user_id, git_sha=config.git_sha,
            input_observation_ids=[row["source_id"] for row in rows
                if row.get("product_id") == product and row.get("source_id")],
            research_snapshot=preview.get("research") if isinstance(preview.get("research"), dict) else None)
        payload["recalculation_reason"] = body.recalculation_reason
        payload["idempotency_key"] = idempotency_key
        calc_id = write("e4_create_calculation", {"p": payload})
        return {"calculation_id": calc_id, "status": "READY_FOR_DECISION"}

    @router.get("/calculations")
    def list_calculations(request: Request, chain_id: str, product_id: str,
                          authorization: str | None = Header(default=None)):
        enabled("forecast_calculation_history_enabled")
        principal, source = actor(request, authorization, None)
        chain, product = allowed(principal, chain_id), uuid_value(product_id)
        return {"calculations": source.client.rows("forecast_calculations", {
            "select": "id,chain_id,product_id,calculation_no,calculation_code,issue_period,status,created_at,created_by,suggested_reference,recalculation_reason",
            "chain_id": f"eq.{chain}", "product_id": f"eq.{product}", "order": "calculation_no.desc"})}

    @router.get("/calculations/{calculation_id}")
    def get_calculation(calculation_id: str, request: Request,
                        authorization: str | None = Header(default=None)):
        enabled("forecast_calculation_history_enabled")
        principal, source = actor(request, authorization, None)
        header = calculation(source.client, calculation_id)
        allowed(principal, header["chain_id"])
        return detail(source.client, header)

    @router.post("/calculations/{calculation_id}/select")
    def select(calculation_id: str, request: Request, body: SelectionRequest,
               authorization: str | None = Header(default=None), idempotency_key: str | None = Header(default=None)):
        enabled("forecast_selection_enabled")
        principal, source = actor(request, authorization, ADMIN)
        key(idempotency_key)
        header = calculation(source.client, calculation_id)
        allowed(principal, header["chain_id"])
        full = detail(source.client, header)
        validate_selection(full, body.selected_candidate, body.decision_reason, body.comment)
        event_id = write("e4_select_forecast", {"p": {"calculation_id": calculation_id,
            "actor_id": principal.user_id, "selected_candidate": body.selected_candidate,
            "decision_reason": body.decision_reason, "comment": body.comment,
            "idempotency_key": idempotency_key}})
        return {"selection_event_id": event_id, "status": "DECIDED"}

    @router.get("/current-selection")
    def current(request: Request, chain_id: str, product_id: str,
                authorization: str | None = Header(default=None)):
        enabled("forecast_selection_enabled")
        principal, source = actor(request, authorization, None)
        chain, product = allowed(principal, chain_id), uuid_value(product_id)
        rows = source.client.rows("current_forecast_selection", {"select": "*", "chain_id": f"eq.{chain}",
            "product_id": f"eq.{product}", "objective": "eq.Venta"})
        return {"selection": rows[0] if rows else None}

    @router.post("/observations/capture")
    def capture(request: Request, body: CaptureRequest, authorization: str | None = Header(default=None),
                idempotency_key: str | None = Header(default=None)):
        enabled("capture_center_enabled")
        principal, _ = actor(request, authorization)
        chain, product = allowed(principal, body.chain_id), uuid_value(body.product_id)
        key(idempotency_key)
        session = write("e4_save_capture", {"p": {**body.model_dump(), "chain_id": chain,
            "product_id": product, "actor_id": principal.user_id, "idempotency_key": idempotency_key}})
        return {"session_id": session, "status": "DRAFT"}

    @router.get("/observations/capture")
    def capture_history(request: Request, chain_id: str, product_id: str, period: str,
                        authorization: str | None = Header(default=None)):
        enabled("capture_center_enabled")
        principal, source = actor(request, authorization, None)
        chain, product = allowed(principal, chain_id), uuid_value(product_id)
        if len(period) != 7 or period[4] != "-" or period[5:] not in {f"{n:02d}" for n in range(1, 13)}:
            raise PreviewError("REQUEST_001", 422)
        return {"sessions": source.client.rows("forecast_capture_sessions", {"select": "*",
            "chain_id": f"eq.{chain}", "product_id": f"eq.{product}", "period": f"eq.{period}-01",
            "order": "created_at.desc"}),
            "observations": source.client.rows("monthly_observations", {"select": "id,metric_code,value,version_no,available_at,source_batch_id",
                "chain_id": f"eq.{chain}", "product_id": f"eq.{product}", "period": f"eq.{period}-01",
                "order": "metric_code.asc,version_no.desc"})}

    @router.post("/observations/capture/{session_id}/confirm")
    def confirm(session_id: str, request: Request, authorization: str | None = Header(default=None),
                idempotency_key: str | None = Header(default=None)):
        enabled("capture_center_enabled")
        principal, source = actor(request, authorization)
        key(idempotency_key)
        session = source.client.rows("forecast_capture_sessions", {"select": "id,chain_id,product_id,status",
            "id": f"eq.{uuid_value(session_id)}"})
        if len(session) != 1:
            raise PreviewError("CAPTURE_NOT_FOUND", 404)
        allowed(principal, session[0]["chain_id"])
        batch = write("e4_confirm_capture", {"p_session": session_id, "p_actor": principal.user_id,
            "p_key": idempotency_key})
        return {"session_id": session_id, "source_batch_id": batch, "status": "CONFIRMED"}

    @router.post("/live-evaluation")
    def close_live(request: Request, body: LiveRequest, authorization: str | None = Header(default=None),
                   idempotency_key: str | None = Header(default=None)):
        enabled("live_learning_enabled")
        principal, _ = actor(request, authorization, ADMIN)
        chain, product = allowed(principal, body.chain_id), uuid_value(body.product_id)
        key(idempotency_key)
        return write("e4_close_live", {"p_chain": chain, "p_product": product,
            "p_period": body.target_period, "p_actor": principal.user_id, "p_key": idempotency_key})

    @router.get("/live-metrics")
    def metrics(request: Request, chain_id: str, product_id: str,
                authorization: str | None = Header(default=None)):
        enabled("live_learning_enabled")
        principal, source = actor(request, authorization, None)
        chain, product = allowed(principal, chain_id), uuid_value(product_id)
        rows = source.client.rows("forecast_live_evaluations", {"select": "*", "chain_id": f"eq.{chain}",
            "product_id": f"eq.{product}", "order": "target_period.asc"})
        return {"metrics": live_metrics(rows), "evaluations": len(rows)}

    @router.get("/learning")
    def learning(request: Request, chain_id: str, product_id: str,
                 authorization: str | None = Header(default=None)):
        enabled("live_learning_enabled")
        principal, source = actor(request, authorization, None)
        chain, product = allowed(principal, chain_id), uuid_value(product_id)
        return {"events": source.client.rows("forecast_learning_events", {"select": "*",
            "chain_id": f"eq.{chain}", "product_id": f"eq.{product}", "order": "created_at.desc"})}

    app.include_router(router)
