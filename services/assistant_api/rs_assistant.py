"""Operational assistant for a single authorized RS scope."""
from __future__ import annotations

import re
import sqlite3
from decimal import Decimal
from threading import Lock
from typing import Literal

from fastapi import Header, Request
from pydantic import BaseModel, ConfigDict, Field

from services.forecast_engine.engine import ENGINE_VERSION, ForecastPolicy, forecast_dataset, normalize_retrospective_dataset

from .auth import AuthFailure, EXECUTE, READ, authorize
from .orchestrator import ForecastOrchestrator
from .forecast_execution import execute_forecast
from .persistence import content_hash
from .rs_assistant_data import load_rs_scope
from .supabase_access import PreviewError, uuid_value
from .tools import fold

PUBLIC_CONTEXT = re.compile(
    r"\b(?:investig\w*|mercado|hallazgos?|tendencias?|estacional\w*|"
    r"riesgos?\s+comerciales?|desabasto|sobreinventario)\b"
)


class RSAssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    chain_id: str
    purchase_type: Literal["RS"]
    format: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=80)
    presentation: str | None = Field(default=None, min_length=1, max_length=80)
    period: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    message: str = Field(default="", max_length=2000)
    action: Literal["answer", "forecast", "research", "research_status"] = "answer"


def summarize_forecast(result, variants_expected):
    chain = result["chains"][0]
    audit = chain["model_audit"]
    rows = chain["forecast_towell"]
    by_period = {}
    for row in rows:
        by_period.setdefault(row["target_period"], []).append(row)
    periods = []
    for period, members in sorted(by_period.items()):
        values = {}
        for engine in ("statistical", "ml", "ensemble_value"):
            numbers = [Decimal(str(r[engine])) for r in members if r.get(engine) is not None]
            values[engine] = {"pieces": str(sum(numbers)) if numbers else None,
                              "covered": len(numbers), "eligible": variants_expected}
        periods.append({"period": period, **values})
    metrics = {}
    for name in ("statistical", "ml"):
        measured = audit.get(f"retrospective_{name}_metrics") or {}
        metrics[name] = {"wape": measured.get("wape"), "bias": measured.get("bias")}
    selected = audit.get("selected_candidate") or {}
    if selected.get("strategy") == "ensemble":
        metrics["ensemble"] = {"wape": selected.get("validation_wape"),
                               "bias": selected.get("validation_bias")}
    return {"status": "PROVISIONAL", "engine": audit["engine_version"],
            "periods": periods, "retrospective": metrics, "live": None}


class RSAssistantWorkflow:
    def __init__(self, storage, research=None, *, compute=forecast_dataset):
        self.storage, self.research, self.compute = storage, research, compute
        self.compute_slot = Lock()

    def answer(self, body, scoped, *, may_start_research=False):
        scope, rows = scoped["scope"], scoped["records"]
        key = content_hash({"engine": ENGINE_VERSION, "scope": scope, "records": rows})
        forecast = self.storage.get("rs_assistant_forecasts", key)
        if body.action == "forecast" and forecast is None:
            variants = {r["product_id"] for r in rows}
            if not variants or len(variants) > 50:
                raise PreviewError("RS_FORECAST_SCOPE_UNAVAILABLE", 409)
            series = normalize_retrospective_dataset(rows, scope["period"])
            eligible = {item.product_id for item in series
                        if len(item.history(scope["period"])) >= ForecastPolicy().min_train_observations}
            usable = [r for r in rows if r["product_id"] in eligible]
            if not usable:
                raise PreviewError("RS_FORECAST_HISTORY_INSUFFICIENT", 409)
            if not self.compute_slot.acquire(blocking=False):
                raise PreviewError("RS_FORECAST_BUSY", 429)
            try:
                forecast = self.storage.get("rs_assistant_forecasts", key)
                if forecast is None:
                    result, exclusions = execute_forecast(usable, scope["period"], compute=self.compute, objective="Venta",
                                                         evidence_mode="RETROSPECTIVE_TRAINING")
                    if not result.get("chains"):
                        raise PreviewError("RS_FORECAST_SCOPE_UNAVAILABLE", 409)
                    forecast = summarize_forecast(result, scoped.get("eligible_variants", len(variants)))
                    forecast["execution_exclusions"] = exclusions
                    self.storage.put("rs_assistant_forecasts", key, forecast)
            finally:
                self.compute_slot.release()
        report = None
        research_scope = {key: scope[key] for key in (
            "chain_id", "format", "model", "period", "source_hash", "purchase_type", "public_product_type"
        ) if key in scope}
        context_requested = body.action == "answer" and PUBLIC_CONTEXT.search(fold(body.message))
        try:
            if self.research is None:
                if body.action in {"research", "research_status"}:
                    report = {"status": "DISABLED"}
            elif body.action == "research_status":
                report = self.research.status(research_scope)
            elif may_start_research and (body.action in {"forecast", "research"} or context_requested):
                report = self.research.start(research_scope)
            elif context_requested:
                report = self.research.cached(research_scope)
        except sqlite3.Error:
            report = {"status": "UNAVAILABLE", "reason": "RESEARCH_STORAGE_UNAVAILABLE"}
        return ForecastOrchestrator.operational_answer(body.message, scoped, forecast=forecast, research=report)


def mount_rs_assistant(app, config, identity, client, limiter, workflow):
    @app.post("/api/forecast/rs-assistant")
    def answer(body: RSAssistantRequest, request: Request, authorization: str | None = Header(default=None)):
        if not config.ai_assistant_api_enabled:
            raise PreviewError("RS_ASSISTANT_DISABLED", 503)
        if not authorization or not authorization.startswith("Bearer "):
            raise PreviewError("AUTH_REQUIRED", 401)
        token = authorization[7:]
        executes = body.action in {"research", "forecast"}
        try:
            principal = identity.authenticate(token=token, actor_id=None, client_host=None)
            authorize(principal, EXECUTE if executes else READ)
        except AuthFailure as error:
            raise PreviewError("AUTH_REQUIRED" if error.status_code == 401 else "SCOPE_FORBIDDEN",
                               error.status_code) from None
        request.state.user_id = principal.user_id
        chain = uuid_value(body.chain_id)
        if chain not in (principal.execute_chain_ids if executes else principal.chain_ids):
            raise PreviewError("SCOPE_FORBIDDEN", 403)
        if not limiter.allow("rs-assistant:" + principal.user_id, 20):
            raise PreviewError("RATE_001", 429)
        scoped = load_rs_scope(client.bind(token), principal, chain_id=chain, purchase_type=body.purchase_type,
                               format=body.format, model=body.model, period=body.period, presentation=body.presentation)
        try:
            return workflow.answer(body, scoped, may_start_research=(
                EXECUTE in principal.permissions and chain in principal.execute_chain_ids
            ))
        except (ValueError, ArithmeticError, IndexError, KeyError):
            raise PreviewError("RS_ASSISTANT_UNAVAILABLE", 503) from None
