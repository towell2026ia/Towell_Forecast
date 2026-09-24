"""FastAPI boundary with local providers and fail-closed remote modes."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth import ADMIN, EXECUTE, READ, AuthFailure, AuthProvider, LocalAuthProvider, Principal, SignedAuthProvider, authorize
from .data_provider import DataProvider, NormalizedDataProvider
from .historical_runner import HistoricalForecastRunner
from .orchestrator import AssistantProvider, ForecastOrchestrator
from .persistence import LocalPersistenceProvider, PersistenceProvider
from .runner import EnginePipeline, LocalResearchProvider, MonthlyForecastRunner
from .runtime import LocalTelemetryProvider, SlidingWindowRateLimiter, TelemetryProvider
from .settings import Settings

API_VERSION = "0.11.0-prd09"
ERROR_MESSAGES = {
    "AUTH_001": "Se requiere autenticación válida.",
    "AUTH_002": "No tienes permiso para esta operación.",
    "DATA_001": "No fue posible consultar la información solicitada.",
    "MODEL_001": "La corrida del modelo no pudo completarse.",
    "RUNNER_001": "No fue posible iniciar la corrida histórica.",
    "PERSISTENCE_001": "El almacenamiento no está disponible.",
    "ASSISTANT_001": "Asistente temporalmente no disponible.",
    "RATE_001": "Demasiadas solicitudes. Inténtalo más tarde.",
    "REQUEST_001": "Solicitud inválida.",
    "INTERNAL_001": "Ocurrió un error interno.",
}


class AssistantMessage(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    context: dict[str, Any] = Field(default_factory=dict)


class ForecastRunRequest(BaseModel):
    period: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")


class ErrorResponse(BaseModel):
    status: str = "error"
    error_code: str
    message: str
    request_id: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


def _error(code: str, status: int, request_id: str | None = None) -> JSONResponse:
    payload = ErrorResponse(error_code=code, message=ERROR_MESSAGES.get(code, ERROR_MESSAGES["INTERNAL_001"]),
                            request_id=request_id)
    return JSONResponse(status_code=status, content=payload.model_dump())


def create_app(provider: DataProvider | None = None, historical_state_dir: Path | None = None,
               *, settings: Settings | None = None, persistence: PersistenceProvider | None = None,
               assistant_provider: AssistantProvider | None = None,
               forecast_pipeline: EnginePipeline | None = None,
               auth_provider: AuthProvider | None = None,
               telemetry: TelemetryProvider | None = None) -> FastAPI:
    config = (settings or Settings.from_env()).validate()
    data = provider or NormalizedDataProvider(data_dir=config.data_dir, state_dir=config.state_dir)
    state_dir = (Path(historical_state_dir) if historical_state_dir else
                 Path(getattr(data, "state_dir", config.state_dir)) / "historical")
    storage = persistence or LocalPersistenceProvider(config.database_path if not historical_state_dir else
                                                       state_dir / "historical.sqlite3")
    identity = auth_provider or (SignedAuthProvider(config) if config.strict_auth else LocalAuthProvider(config))
    metrics = telemetry or LocalTelemetryProvider()
    rate_limiter = SlidingWindowRateLimiter()
    historical_slots = threading.BoundedSemaphore(config.max_historical_runs)
    forecast_slots = threading.BoundedSemaphore(config.max_forecast_runs)
    command_lock = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=config.max_historical_runs, thread_name_prefix="historical")
    forecast_executor = ThreadPoolExecutor(max_workers=config.max_forecast_runs, thread_name_prefix="forecast")
    app = FastAPI(title="FORECAST Towell API", version=config.app_version,
                  docs_url=None if config.app_env == "production" else "/docs",
                  redoc_url=None if config.app_env == "production" else "/redoc",
                  openapi_url=None if config.app_env == "production" else "/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=[config.frontend_url], allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type",
                                                                       "Idempotency-Key", "X-Assistant-Token",
                                                                       "X-Actor-Id", "X-Request-ID"])
    orchestrator = ForecastOrchestrator(data, assistant_provider=assistant_provider)
    historical = HistoricalForecastRunner(data, research=LocalResearchProvider(),
                                          state_dir=state_dir, persistence=storage)
    forecast_runner = MonthlyForecastRunner(data, research=LocalResearchProvider(), pipeline=forecast_pipeline,
                                            state_dir=config.state_dir, persistence=storage)
    app.state.settings = config
    app.state.orchestrator = orchestrator
    app.state.historical = historical
    app.state.forecast_runner = forecast_runner
    app.state.persistence = storage
    app.state.telemetry = metrics
    app.state.auth = identity

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        candidate = request.headers.get("x-request-id", "")
        try:
            request_id = str(uuid.UUID(candidate))
        except ValueError:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.user_id = None
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            metrics.emit(environment=config.app_env, component="api", event="unhandled_exception",
                         status="error", request_id=request_id, error_code="INTERNAL_001",
                         duration=round(time.monotonic() - started, 4))
            response = _error("INTERNAL_001", 500, request_id)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        metrics.emit(environment=config.app_env, component="api", event="request",
                     status="ok" if response.status_code < 400 else "error",
                     request_id=request_id, user_id=request.state.user_id,
                     duration=round(time.monotonic() - started, 4),
                     error_code=None if response.status_code < 400 else f"HTTP_{response.status_code}",
                     method=request.method, route=request.url.path)
        return response

    @app.exception_handler(AuthFailure)
    async def auth_exception(request: Request, exc: AuthFailure):
        return _error(exc.code, exc.status_code, getattr(request.state, "request_id", None))

    @app.exception_handler(HTTPException)
    async def http_exception(request: Request, exc: HTTPException):
        code = exc.detail if isinstance(exc.detail, str) and exc.detail in ERROR_MESSAGES else "REQUEST_001"
        return _error(code, exc.status_code, getattr(request.state, "request_id", None))

    @app.exception_handler(RequestValidationError)
    async def validation_exception(request: Request, exc: RequestValidationError):
        return _error("REQUEST_001", 422, getattr(request.state, "request_id", None))

    def require(permission: str):
        def dependency(request: Request, authorization: str | None = Header(default=None),
                       x_assistant_token: str | None = Header(default=None),
                       x_actor_id: str | None = Header(default=None)) -> Principal:
            token = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") \
                else x_assistant_token
            try:
                principal = identity.authenticate(token=token, actor_id=x_actor_id,
                                                  client_host=request.client.host if request.client else None)
            except AuthFailure as exc:
                metrics.emit(environment=config.app_env, component="auth", event=exc.reason,
                             status="error", request_id=request.state.request_id, error_code=exc.code)
                raise
            request.state.user_id = principal.user_id
            try:
                authorize(principal, permission)
            except AuthFailure as exc:
                metrics.emit(environment=config.app_env, component="auth", event="authorization_denied",
                             status="error", request_id=request.state.request_id, user_id=principal.user_id,
                             error_code=exc.code, session_id=principal.session_id)
                raise
            metrics.emit(environment=config.app_env, component="auth", event="auth_success",
                         status="ok", request_id=request.state.request_id, user_id=principal.user_id,
                         session_id=principal.session_id)
            if request.url.path.endswith("/assistant/message"):
                limit = config.assistant_rate_per_minute
            elif "/historical/" in request.url.path:
                limit = config.historical_rate_per_minute
            else:
                limit = config.forecast_rate_per_minute
            if not rate_limiter.allow(f"{principal.user_id}:{request.url.path}", limit):
                raise HTTPException(status_code=429, detail="RATE_001")
            return principal
        return dependency

    read_access = require(READ)
    execute_access = require(EXECUTE)
    admin_access = require(ADMIN)

    @app.get("/api/live", response_model=HealthResponse)
    @app.get("/api/v1/live", response_model=HealthResponse)
    def live() -> dict[str, str]:
        return {"status": "ok", "version": config.app_version, "environment": config.app_env}

    @app.get("/api/health", response_model=HealthResponse)
    @app.get("/api/v1/health", response_model=HealthResponse)
    def health() -> dict[str, str]:
        return live()

    @app.get("/api/version")
    @app.get("/api/v1/version")
    def version() -> dict[str, str]:
        return {"version": config.app_version, "commit": config.git_sha,
                "build": config.build_time, "environment": config.app_env}

    @app.get("/api/ready")
    @app.get("/api/v1/ready")
    def ready():
        providers = {"data": data.health(), "persistence": storage.health(),
                     "assistant": orchestrator.assistant_provider.health(),
                     "research": historical.research.health(), "auth": identity.health()}
        try:
            for name in ("statistical", "ml", "ensemble"):
                payload = data.load(name)
                if not isinstance(payload, dict) or not payload:
                    raise ValueError("empty_model_snapshot")
            model_status = "healthy"
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            model_status = "not_ready"
        providers["models"] = {"status": model_status}
        if any(providers[key]["status"] != "healthy" for key in ("data", "persistence", "auth", "models")):
            status, code = "not_ready", 503
        elif any(providers[key]["status"] != "healthy" for key in ("assistant", "research")):
            status, code = "degraded", 200
        else:
            status, code = "ok", 200
        return JSONResponse(status_code=code, content={"status": status, "version": config.app_version,
                             "environment": config.app_env, "providers": providers})

    def safe_query(intent: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return orchestrator.query(intent, params)
        except Exception:
            raise HTTPException(status_code=503, detail="DATA_001") from None

    def execute_forecast(job_id: str, period: str, actor: str) -> None:
        try:
            job = storage.get("forecast_jobs", job_id) or {"run_id": job_id}
            job.update(status="RUNNING", started_at=time.time())
            storage.put("forecast_jobs", job_id, job)
            metrics.emit(environment=config.app_env, component="forecast", event="run_started",
                         status="ok", run_id=job_id, user_id=actor)
            result = forecast_runner.run_month(period, actor=actor)
            job.update(status="COMPLETED" if result.get("state") == "Completed" else "FAILED",
                       model_run_id=result.get("run_id"), finished_at=time.time(),
                       error_code=None if result.get("state") == "Completed" else "MODEL_001")
            storage.put("forecast_jobs", job_id, job)
            metrics.emit(environment=config.app_env, component="forecast", event="run_completed",
                         status="ok" if job["status"] == "COMPLETED" else "error",
                         run_id=job_id, user_id=actor, duration=result.get("duration_seconds"),
                         error_code=job["error_code"])
        except Exception:
            storage.put("forecast_jobs", job_id, {"run_id": job_id, "status": "FAILED",
                                                 "error_code": "MODEL_001", "finished_at": time.time()})
            metrics.emit(environment=config.app_env, component="forecast", event="run_completed",
                         status="error", run_id=job_id, user_id=actor, error_code="MODEL_001")
        finally:
            forecast_slots.release()

    @app.post("/api/forecast/run", status_code=202)
    @app.post("/api/v1/forecast/run", status_code=202)
    def forecast_run(body: ForecastRunRequest, request: Request,
                     principal: Principal = Depends(execute_access),
                     idempotency_key: str | None = Header(default=None)):
        if not config.monthly_runner_enabled:
            raise HTTPException(status_code=503, detail="RUNNER_001")
        if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
            raise HTTPException(status_code=400, detail="REQUEST_001")
        digest = hashlib.sha256(f"forecast:{principal.user_id}:{idempotency_key}".encode()).hexdigest()
        with command_lock:
            old = storage.get("run_logs", f"idempotency-{digest}")
            if old:
                if old.get("period") != body.period:
                    raise HTTPException(status_code=409, detail="REQUEST_001")
                storage.put("run_logs", f"duplicate-{uuid.uuid4().hex}",
                            {"job_id": old["job_id"], "period": body.period, "user_id": principal.user_id,
                             "session_id": principal.session_id, "request_id": request.state.request_id,
                             "event": "duplicate_prevented", "result": "EXISTING", "timestamp": time.time()})
                return storage.get("forecast_jobs", old["job_id"])
            if not forecast_slots.acquire(blocking=False):
                raise HTTPException(status_code=429, detail="RATE_001")
            job_id = f"API-FRUN-{uuid.uuid4().hex[:16]}"
            job = {"run_id": job_id, "period": body.period, "status": "QUEUED",
                   "actor": principal.user_id, "session_id": principal.session_id,
                   "request_id": request.state.request_id, "created_at": time.time()}
            try:
                storage.put("forecast_jobs", job_id, job)
                storage.put("run_logs", f"idempotency-{digest}",
                            {"job_id": job_id, "period": body.period, "user_id": principal.user_id,
                             "session_id": principal.session_id, "request_id": request.state.request_id,
                             "event": "command_accepted", "result": "QUEUED", "timestamp": time.time()})
                forecast_executor.submit(execute_forecast, job_id, body.period, principal.user_id)
            except Exception:
                forecast_slots.release()
                raise HTTPException(status_code=503, detail="PERSISTENCE_001") from None
        return job

    @app.get("/api/forecast/jobs/{job_id}")
    @app.get("/api/v1/forecast/jobs/{job_id}")
    def forecast_job(job_id: str, principal: Principal = Depends(read_access)):
        job = storage.get("forecast_jobs", job_id)
        if not job or (job.get("actor") != principal.user_id and ADMIN not in principal.permissions):
            raise HTTPException(status_code=404, detail="REQUEST_001")
        return job

    @app.get("/api/historical/availability/audit")
    @app.get("/api/v1/historical/availability/audit")
    def historical_availability_audit(chain: str = "Walmart", start_period: str = "2023-01",
                                      end_period: str = "2026-08", principal: Principal = Depends(read_access)):
        try:
            return historical.availability.audit_availability(start_period, end_period, chain=chain)
        except ValueError:
            raise HTTPException(status_code=400, detail="REQUEST_001") from None

    @app.get("/api/historical/readiness/{period}")
    @app.get("/api/v1/historical/readiness/{period}")
    def historical_readiness(period: str, chain: str = "Walmart", cutoff: str | None = None,
                             principal: Principal = Depends(read_access)):
        try:
            return historical.availability.validate_temporal_readiness(period, chain=chain, cutoff=cutoff)
        except ValueError:
            raise HTTPException(status_code=400, detail="REQUEST_001") from None

    @app.get("/api/historical/first-valid-period")
    @app.get("/api/v1/historical/first-valid-period")
    def historical_first_valid(chain: str = "Walmart", start_period: str = "2023-01",
                               end_period: str = "2026-08", principal: Principal = Depends(read_access)):
        try:
            return {"first_valid_period": historical.availability.find_first_temporally_valid_period(
                start_period, end_period, chain=chain)}
        except ValueError:
            raise HTTPException(status_code=400, detail="REQUEST_001") from None

    def execute_first_vintage(job_id: str, actor: str) -> None:
        try:
            job = storage.get("historical_runs", job_id) or {"run_id": job_id}
            job.update(status="RUNNING", started_at=time.time())
            storage.put("historical_runs", job_id, job)
            metrics.emit(environment=config.app_env, component="historical", event="run_started",
                         status="ok", run_id=job_id, user_id=actor)
            result = historical.first_vintage(actor=actor)
            job.update(status="COMPLETED" if result.get("first_real_vintage_validated") else "FAILED",
                       result=result, finished_at=time.time())
            storage.put("historical_runs", job_id, job)
            metrics.emit(environment=config.app_env, component="historical", event="run_completed",
                         status="ok" if job["status"] == "COMPLETED" else "error",
                         run_id=job_id, user_id=actor)
        except Exception:
            storage.put("historical_runs", job_id, {"run_id": job_id, "status": "FAILED",
                                                    "error_code": "RUNNER_001", "finished_at": time.time()})
            metrics.emit(environment=config.app_env, component="historical", event="run_completed",
                         status="error", run_id=job_id, user_id=actor, error_code="RUNNER_001")
        finally:
            historical_slots.release()

    @app.post("/api/historical/first-vintage", status_code=202)
    @app.post("/api/v1/historical/first-vintage", status_code=202)
    def historical_first_vintage(request: Request, principal: Principal = Depends(admin_access),
                                 idempotency_key: str | None = Header(default=None)):
        if not config.historical_runner_enabled:
            raise HTTPException(status_code=503, detail="RUNNER_001")
        if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
            raise HTTPException(status_code=400, detail="REQUEST_001")
        digest = hashlib.sha256(f"{principal.user_id}:{idempotency_key}".encode()).hexdigest()
        with command_lock:
            old = storage.get("run_logs", f"idempotency-{digest}")
            if old:
                storage.put("run_logs", f"duplicate-{uuid.uuid4().hex}",
                            {"job_id": old["job_id"], "user_id": principal.user_id,
                             "session_id": principal.session_id, "request_id": request.state.request_id,
                             "event": "duplicate_prevented", "result": "EXISTING", "timestamp": time.time()})
                return storage.get("historical_runs", old["job_id"])
            if not historical_slots.acquire(blocking=False):
                raise HTTPException(status_code=429, detail="RATE_001")
            job_id = f"API-HRUN-{uuid.uuid4().hex[:16]}"
            job = {"run_id": job_id, "status": "QUEUED", "actor": principal.user_id,
                   "session_id": principal.session_id, "command": "first_vintage",
                   "request_id": request.state.request_id, "created_at": time.time()}
            try:
                storage.put("historical_runs", job_id, job)
                storage.put("run_logs", f"idempotency-{digest}",
                            {"job_id": job_id, "user_id": principal.user_id,
                             "session_id": principal.session_id, "request_id": request.state.request_id,
                             "event": "command_accepted", "result": "QUEUED", "timestamp": time.time()})
                executor.submit(execute_first_vintage, job_id, principal.user_id)
            except Exception:
                historical_slots.release()
                raise HTTPException(status_code=503, detail="PERSISTENCE_001") from None
        return job

    @app.get("/api/historical/jobs/{job_id}")
    @app.get("/api/v1/historical/jobs/{job_id}")
    def historical_job(job_id: str, principal: Principal = Depends(read_access)):
        job = storage.get("historical_runs", job_id)
        if not job or (job.get("actor") != principal.user_id and ADMIN not in principal.permissions):
            raise HTTPException(status_code=404, detail="REQUEST_001")
        return job

    @app.post("/api/assistant/message")
    @app.post("/api/v1/assistant/message")
    def assistant_message(body: AssistantMessage, request: Request,
                          principal: Principal = Depends(read_access)):
        if not config.ai_assistant_api_enabled or not config.local_intent_router_enabled:
            raise HTTPException(status_code=503, detail="ASSISTANT_001")
        start = time.monotonic()
        try:
            result = orchestrator.answer(body.message, body.context)
            result["metadata"]["actor"] = principal.user_id
            metrics.emit(environment=config.app_env, component="assistant", event="assistant_call",
                         status="ok", request_id=request.state.request_id, user_id=principal.user_id,
                         duration=round(time.monotonic() - start, 4), intent=result.get("intent"),
                         provider="local", prompt_version=None, tool_calls=0, token_usage=None, cost=None)
            return result
        except Exception:
            metrics.emit(environment=config.app_env, component="assistant", event="assistant_call",
                         status="error", request_id=request.state.request_id, user_id=principal.user_id,
                         duration=round(time.monotonic() - start, 4), error_code="ASSISTANT_001")
            raise HTTPException(status_code=503, detail="ASSISTANT_001") from None

    routes = {
        "/api/forecast/current": "current_forecast", "/api/forecast/12m": "forecast_12m",
        "/api/performance/wape": "wape_summary", "/api/performance/bias": "bias_summary",
        "/api/performance/fill-rate": "fill_rate", "/api/performance/product": "product_performance",
        "/api/performance/highest-error": "highest_error_series", "/api/models/champion": "champion_status",
        "/api/models/challenger": "challenger_status", "/api/models/drift": "drift_status",
        "/api/forecast/vintages": "forecast_vintage", "/api/forecast/bands": "probability_bands",
        "/api/forecast/comparison": "forecast_comparison", "/api/decisions/history": "decision_history",
        "/api/decisions/fva": "fva_summary", "/api/periods/status": "period_status",
    }

    def make_endpoint(intent: str):
        def endpoint(product: str | None = None, category: str | None = None, color: str | None = None,
                     period: str | None = None, issue_period: str | None = None,
                     target_period: str | None = None, principal: Principal = Depends(read_access)):
            return safe_query(intent, {"product": product, "category": category, "color": color,
                                       "period": period, "issue_period": issue_period,
                                       "target_period": target_period})
        return endpoint

    for path, intent in routes.items():
        app.get(path, name=intent)(make_endpoint(intent))
        app.get(path.replace("/api/", "/api/v1/"), name=f"v1_{intent}")(make_endpoint(intent))

    @app.on_event("shutdown")
    def shutdown() -> None:
        executor.shutdown(wait=True, cancel_futures=False)
        forecast_executor.shutdown(wait=True, cancel_futures=False)

    return app


app = create_app()
