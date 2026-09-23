"""Single validated configuration boundary for the Python service."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

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
    research_provider: str = "local"
    ai_assistant_ui_enabled: bool = True
    ai_assistant_api_enabled: bool = True
    historical_runner_enabled: bool = True
    monthly_runner_enabled: bool = True
    local_research_enabled: bool = True
    local_intent_router_enabled: bool = True
    openai_enabled: bool = False
    supabase_enabled: bool = False
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

    @property
    def database_path(self) -> Path:
        return self.sqlite_path or self.state_dir / "historical" / "historical.sqlite3"

    @property
    def strict_auth(self) -> bool:
        return self.app_env in {"staging", "production"}

    def validate(self) -> "Settings":
        if self.app_env not in ENVIRONMENTS:
            raise ValueError("invalid_app_env")
        if self.assistant_provider != "local" or self.data_provider != "normalized" or \
                self.persistence_provider != "sqlite" or self.research_provider != "local":
            raise ValueError("provider_disabled")
        if any((self.openai_enabled, self.supabase_enabled, self.voice_enabled, self.deep_research_enabled)):
            raise ValueError("future_provider_disabled")
        if not self.local_research_enabled or not self.local_intent_router_enabled:
            raise ValueError("local_provider_disabled")
        parsed = urlparse(self.frontend_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or "*" in self.frontend_url:
            raise ValueError("invalid_frontend_url")
        if self.strict_auth:
            if parsed.scheme != "https" or not self.assistant_token or not self.manager_ids:
                raise ValueError("strict_auth_configuration_missing")
            if len(self.assistant_token) < 32:
                raise ValueError("weak_auth_secret")
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
        def number(name: str, default: int, maximum: int = 65535) -> int:
            return _integer(env.get(name, str(default)), name, maximum=maximum)
        def path(name: str, default: Path) -> Path:
            value = env.get(name)
            return Path(value).expanduser().resolve() if value else default
        def ids(name: str) -> frozenset[str]:
            return frozenset(item.strip() for item in env.get(name, "").split(",") if item.strip())
        environment = env.get("APP_ENV", "development")
        settings = cls(
            app_env=environment,
            api_host=env.get("API_HOST", "127.0.0.1" if environment in {"development", "test"} else "0.0.0.0"),
            api_port=number("API_PORT", 8000),
            frontend_url=env.get("FRONTEND_URL", "http://localhost:3000"),
            data_dir=path("DATA_DIR", ROOT / "app" / "data"),
            state_dir=path("STATE_DIR", ROOT / "services" / "assistant_api" / "state"),
            sqlite_path=path("SQLITE_PATH", ROOT / "services" / "assistant_api" / "state" / "historical" / "historical.sqlite3")
            if env.get("SQLITE_PATH") else None,
            assistant_provider=env.get("ASSISTANT_PROVIDER", "local"),
            data_provider=env.get("DATA_PROVIDER", "normalized"),
            persistence_provider=env.get("PERSISTENCE_PROVIDER", "sqlite"),
            research_provider=env.get("RESEARCH_PROVIDER", "local"),
            ai_assistant_ui_enabled=flag("AI_ASSISTANT_UI_ENABLED", True),
            ai_assistant_api_enabled=flag("AI_ASSISTANT_API_ENABLED", True),
            historical_runner_enabled=flag("HISTORICAL_RUNNER_ENABLED", True),
            monthly_runner_enabled=flag("MONTHLY_RUNNER_ENABLED", True),
            local_research_enabled=flag("LOCAL_RESEARCH_ENABLED", True),
            local_intent_router_enabled=flag("LOCAL_INTENT_ROUTER_ENABLED", True),
            openai_enabled=flag("OPENAI_ENABLED", False),
            supabase_enabled=flag("SUPABASE_ENABLED", False),
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
            git_sha=env.get("GIT_SHA", "unknown"),
            build_time=env.get("BUILD_TIME", "unknown"),
            app_version=env.get("APP_VERSION", "0.11.0-prd09"),
        )
        return settings.validate()
