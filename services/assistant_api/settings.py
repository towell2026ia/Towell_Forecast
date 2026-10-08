"""Single validated configuration boundary for the Python service."""

from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from services.forecast_engine.selection_policy import SELECTION_POLICY

ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENTS = {"development", "test", "staging", "production"}


def _boolean(value: str, name: str) -> bool:
    if value.casefold() not in {"true", "false"}:
        raise ValueError(f"invalid_boolean:{name}")
    return value.casefold() == "true"


def _integer(value: str, name: str, *, minimum: int = 1, maximum: int = 65535) -> int:
    try:
        result = int(value)
    except ValueError:
        raise ValueError(f"invalid_integer:{name}") from None
    if not minimum <= result <= maximum:
        raise ValueError(f"invalid_integer:{name}")
    return result


def _float(value: str, name: str) -> float:
    try:
        result = float(value)
    except ValueError:
        raise ValueError(f"invalid_float:{name}") from None
    if not math.isfinite(result):
        raise ValueError(f"invalid_float:{name}")
    return result


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    frontend_url: str = "http://localhost:3000"
    data_dir: Path = ROOT / "app" / "data"
    state_dir: Path = ROOT / "services" / "assistant_api" / "state"
    sqlite_path: Path | None = None
    assistant_provider: str = "local"
    data_provider: str = "normalized"
    persistence_provider: str = "sqlite"
    persistence_mode: str = "local"
    research_provider: str = "local"
    ai_assistant_ui_enabled: bool = True
    ai_assistant_api_enabled: bool = True
    historical_runner_enabled: bool = True
    legacy_pilot_enabled: bool = False
    monthly_runner_enabled: bool = True
    local_research_enabled: bool = True
    local_intent_router_enabled: bool = True
    openai_enabled: bool = False
    openai_api_key: str = field(default="", repr=False)
    supabase_enabled: bool = False
    operational_preview_enabled: bool = False
    vintage_persistence_enabled: bool = False
    official_publication_enabled: bool = False
    champion_publication_enabled: bool = False
    forecast_calculation_history_enabled: bool = False
    forecast_selection_enabled: bool = False
    capture_center_enabled: bool = False
    live_learning_enabled: bool = False
    service_target_fill_rate: float = 95.0
    quality_policy_version: str = "E3-GATES-1.0.0"
    minimum_history_months: int = 18
    warning_continuity_rate: float = 0.85
    ready_continuity_rate: float = 0.95
    minimum_improvement_points: float = 0.0
    maximum_bias_deterioration: float = 5.0
    critical_horizon_degradation: float = 10.0
    supabase_url: str = ""
    supabase_publishable_key: str = field(default="", repr=False)
    supabase_service_role_key: str = field(default="", repr=False)
    voice_enabled: bool = False
    deep_research_enabled: bool = False
    assistant_token: str = field(default="", repr=False)
    manager_ids: frozenset[str] = frozenset()
    editor_ids: frozenset[str] = frozenset()
    reader_ids: frozenset[str] = frozenset()
    max_forecast_runs: int = 2
    max_historical_runs: int = 1
    assistant_rate_per_minute: int = 60
    forecast_rate_per_minute: int = 10
    historical_rate_per_minute: int = 4
    external_timeout_seconds: int = 10
    retry_max_attempts: int = 3
    git_sha: str = "unknown"
    build_time: str = "unknown"
    app_version: str = "0.11.0-prd09"
    railway_runtime: bool = False

    @property
    def database_path(self) -> Path:
        return self.sqlite_path or self.state_dir / "historical" / "historical.sqlite3"

    @property
    def strict_auth(self) -> bool:
        return self.app_env in {"staging", "production"}

    @property
    def auth_issuer(self) -> str:
        return f"forecast-towell-frontend:{self.app_env}"

    def validate(self) -> "Settings":
        if self.app_env not in ENVIRONMENTS:
            raise ValueError("invalid_app_env")
        if not 1 <= self.api_port <= 65535:
            raise ValueError("invalid_integer:API_PORT")
        if self.assistant_provider != "local" or self.data_provider not in {"normalized", "supabase"} or \
                self.persistence_provider != "sqlite" or self.research_provider != "local":
            raise ValueError("provider_disabled")
        if self.persistence_mode not in {"local", "hosted-volume"}:
            raise ValueError("invalid_persistence_mode")
        if self.voice_enabled:
            raise ValueError("future_provider_disabled")
        if self.openai_enabled != self.deep_research_enabled:
            raise ValueError("future_provider_disabled")
        if self.deep_research_enabled and (not self.operational_preview_enabled or
                                           not self.openai_api_key.startswith("sk-")):
            raise ValueError("cold_start_research_configuration_missing")
        if self.official_publication_enabled and not self.vintage_persistence_enabled:
            raise ValueError("official_requires_vintage_persistence")
        if self.champion_publication_enabled and not self.vintage_persistence_enabled:
            raise ValueError("champion_requires_vintage_persistence")
        if self.vintage_persistence_enabled and not self.operational_preview_enabled:
            raise ValueError("vintage_requires_operational_preview")
        if any((self.forecast_calculation_history_enabled, self.forecast_selection_enabled,
                self.capture_center_enabled, self.live_learning_enabled)):
            if not self.operational_preview_enabled or not self.supabase_service_role_key:
                raise ValueError("e4_requires_supabase_write_repository")
            if self.forecast_selection_enabled and not self.forecast_calculation_history_enabled:
                raise ValueError("selection_requires_calculation_history")
            if self.live_learning_enabled and not (self.forecast_calculation_history_enabled and self.capture_center_enabled):
                raise ValueError("live_requires_history_and_capture")
        if not 0 < self.service_target_fill_rate <= 100:
            raise ValueError("invalid_service_target_fill_rate")
        if self.quality_policy_version not in {"E3-GATES-1.0.0", "E3-GATES-2.0.0"} or self.minimum_history_months < 1 or not (
                0 < self.warning_continuity_rate <= self.ready_continuity_rate <= 1) or any(
                value < 0 or not math.isfinite(value) for value in (self.minimum_improvement_points,
                self.maximum_bias_deterioration, self.critical_horizon_degradation)):
            raise ValueError("invalid_quality_policy")
        if self.vintage_persistence_enabled and self.quality_policy_version == "E3-GATES-1.0.0" and (
                self.minimum_history_months, self.warning_continuity_rate,
                self.ready_continuity_rate, self.minimum_improvement_points,
                self.maximum_bias_deterioration, self.critical_horizon_degradation,
                self.service_target_fill_rate) != (18, 0.85, 0.95, 0.0, 5.0, 10.0, 95.0):
            raise ValueError("policy_version_change_requires_review")
        if self.quality_policy_version == "E3-GATES-2.0.0" and (
                self.minimum_history_months, self.warning_continuity_rate,
                self.ready_continuity_rate, self.minimum_improvement_points,
                self.maximum_bias_deterioration, self.critical_horizon_degradation) != (
                SELECTION_POLICY.minimum_history_months, SELECTION_POLICY.warning_continuity,
                SELECTION_POLICY.ready_continuity, SELECTION_POLICY.minimum_improvement_points,
                SELECTION_POLICY.maximum_bias_deterioration,
                SELECTION_POLICY.maximum_critical_deterioration):
            raise ValueError("policy_version_change_requires_review")
        if self.data_provider == "supabase" or self.supabase_enabled or self.operational_preview_enabled:
            if not (self.data_provider == "supabase" and self.supabase_enabled and self.operational_preview_enabled):
                raise ValueError("operational_preview_configuration_missing")
            if self.legacy_pilot_enabled or self.ai_assistant_api_enabled:
                raise ValueError("preview_only_configuration_required")
            supabase = urlparse(self.supabase_url)
            if supabase.scheme != "https" or not supabase.hostname or not supabase.hostname.endswith(".supabase.co") \
                    or supabase.username or supabase.password or supabase.path not in {"", "/"} \
                    or supabase.query or supabase.fragment:
                raise ValueError("invalid_supabase_url")
            if not self.supabase_publishable_key.startswith("sb_publishable_") or len(self.supabase_publishable_key) < 24:
                raise ValueError("supabase_publishable_key_required")
            if self.max_forecast_runs > 2:
                raise ValueError("operational_concurrency_maximum_two")
        if not self.local_research_enabled or not self.local_intent_router_enabled:
            raise ValueError("local_provider_disabled")
        parsed = urlparse(self.frontend_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or "*" in self.frontend_url:
            raise ValueError("invalid_frontend_url")
        if self.strict_auth:
            if parsed.scheme != "https" or (not self.operational_preview_enabled and
                    (not self.assistant_token or not self.manager_ids)):
                raise ValueError("strict_auth_configuration_missing")
            if not self.operational_preview_enabled and len(self.assistant_token) < 32:
                raise ValueError("weak_auth_secret")
            if self.api_host != "0.0.0.0":
                raise ValueError("host_must_bind_all_interfaces")
            if self.persistence_mode != "hosted-volume" or not self.database_path.is_relative_to(self.state_dir):
                raise ValueError("persistent_volume_configuration_missing")
        for path in (self.data_dir, self.state_dir, self.database_path):
            if not path.is_absolute():
                raise ValueError("runtime_path_must_be_absolute")
        if self.max_forecast_runs < 1 or self.max_historical_runs < 1:
            raise ValueError("invalid_concurrency_limit")
        return self

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        def flag(name: str, default: bool) -> bool:
            return _boolean(env.get(name, str(default).lower()), name)
        def number(name: str, default: int, maximum: int = 65535,
                   fallback_name: str | None = None) -> int:
            if name in env:
                return _integer(env[name], name, maximum=maximum)
            if fallback_name and fallback_name in env:
                return _integer(env[fallback_name], fallback_name, maximum=maximum)
            return _integer(str(default), name, maximum=maximum)
        def path(name: str, default: Path) -> Path:
            value = env.get(name)
            return Path(value).expanduser().resolve() if value else default
        def ids(name: str) -> frozenset[str]:
            return frozenset(item.strip() for item in env.get(name, "").split(",") if item.strip())
        environment = env.get("APP_ENV", "development")
        settings = cls(
            app_env=environment,
            api_host=env.get("API_HOST", "127.0.0.1" if environment in {"development", "test"} else "0.0.0.0"),
            api_port=number("API_PORT", 8000, fallback_name="PORT"),
            frontend_url=env.get("FRONTEND_URL", "http://localhost:3000"),
            data_dir=path("DATA_DIR", ROOT / "app" / "data"),
            state_dir=path("STATE_DIR", ROOT / "services" / "assistant_api" / "state"),
            sqlite_path=path("SQLITE_PATH", ROOT / "services" / "assistant_api" / "state" / "historical" / "historical.sqlite3")
            if env.get("SQLITE_PATH") else None,
            assistant_provider=env.get("ASSISTANT_PROVIDER", "local"),
            data_provider=env.get("DATA_PROVIDER", "normalized"),
            persistence_provider=env.get("PERSISTENCE_PROVIDER", "sqlite"),
            persistence_mode=env.get("PERSISTENCE_MODE", "local"),
            research_provider=env.get("RESEARCH_PROVIDER", "local"),
            ai_assistant_ui_enabled=flag("AI_ASSISTANT_UI_ENABLED", True),
            ai_assistant_api_enabled=flag("AI_ASSISTANT_API_ENABLED", True),
            historical_runner_enabled=flag("HISTORICAL_RUNNER_ENABLED", True),
            legacy_pilot_enabled=flag("LEGACY_PILOT_ENABLED", False),
            monthly_runner_enabled=flag("MONTHLY_RUNNER_ENABLED", True),
            local_research_enabled=flag("LOCAL_RESEARCH_ENABLED", True),
            local_intent_router_enabled=flag("LOCAL_INTENT_ROUTER_ENABLED", True),
            openai_enabled=flag("OPENAI_ENABLED", False),
            openai_api_key=env.get("OPENAI_API_KEY", ""),
            supabase_enabled=flag("SUPABASE_ENABLED", False),
            operational_preview_enabled=flag("OPERATIONAL_PREVIEW_ENABLED", False),
            vintage_persistence_enabled=flag("VINTAGE_PERSISTENCE_ENABLED", False),
            official_publication_enabled=flag("OFFICIAL_PUBLICATION_ENABLED", False),
            champion_publication_enabled=flag("CHAMPION_PUBLICATION_ENABLED", False),
            forecast_calculation_history_enabled=flag("FORECAST_CALCULATION_HISTORY_ENABLED", False),
            forecast_selection_enabled=flag("FORECAST_SELECTION_ENABLED", False),
            capture_center_enabled=flag("CAPTURE_CENTER_ENABLED", False),
            live_learning_enabled=flag("LIVE_LEARNING_ENABLED", False),
            service_target_fill_rate=_float(env.get("SERVICE_TARGET_FILL_RATE", "95.0"), "SERVICE_TARGET_FILL_RATE"),
            quality_policy_version=env.get("QUALITY_POLICY_VERSION", "E3-GATES-1.0.0"),
            minimum_history_months=number("MINIMUM_HISTORY_MONTHS", 18, 120),
            warning_continuity_rate=_float(env.get("WARNING_CONTINUITY_RATE", "0.85"), "WARNING_CONTINUITY_RATE"),
            ready_continuity_rate=_float(env.get("READY_CONTINUITY_RATE", "0.95"), "READY_CONTINUITY_RATE"),
            minimum_improvement_points=_float(env.get("MINIMUM_IMPROVEMENT_POINTS", "0"), "MINIMUM_IMPROVEMENT_POINTS"),
            maximum_bias_deterioration=_float(env.get("MAXIMUM_BIAS_DETERIORATION", "5"), "MAXIMUM_BIAS_DETERIORATION"),
            critical_horizon_degradation=_float(env.get("CRITICAL_HORIZON_DEGRADATION", "10"), "CRITICAL_HORIZON_DEGRADATION"),
            supabase_url=env.get("SUPABASE_URL", ""),
            supabase_publishable_key=env.get("SUPABASE_PUBLISHABLE_KEY", ""),
            supabase_service_role_key=env.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            voice_enabled=flag("VOICE_ENABLED", False),
            deep_research_enabled=flag("DEEP_RESEARCH_ENABLED", False),
            assistant_token=env.get("ASSISTANT_API_TOKEN", ""),
            manager_ids=ids("ASSISTANT_MANAGER_IDS"),
            editor_ids=ids("ASSISTANT_EDITOR_IDS"),
            reader_ids=ids("ASSISTANT_READER_IDS"),
            max_forecast_runs=number("MAX_FORECAST_RUNS", 2, 100),
            max_historical_runs=number("MAX_HISTORICAL_RUNS", 1, 100),
            assistant_rate_per_minute=number("ASSISTANT_RATE_PER_MINUTE", 60, 100000),
            forecast_rate_per_minute=number("FORECAST_RATE_PER_MINUTE", 10, 100000),
            historical_rate_per_minute=number("HISTORICAL_RATE_PER_MINUTE", 4, 100000),
            external_timeout_seconds=number("EXTERNAL_TIMEOUT_SECONDS", 10, 300),
            retry_max_attempts=number("RETRY_MAX_ATTEMPTS", 3, 10),
            git_sha=env.get("GIT_SHA") or env.get("RAILWAY_GIT_COMMIT_SHA") or "unknown",
            build_time=env.get("BUILD_TIME", "unknown"),
            app_version=env.get("APP_VERSION", "0.11.0-prd09"),
            railway_runtime=bool(env.get("RAILWAY_ENVIRONMENT_ID") or env.get("RAILWAY_SERVICE_ID")),
        )
        return settings.validate()
