"""Persistence of derived historical artifacts, separate from operational data reads."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from abc import ABC, abstractmethod
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

ENTITIES = (
    "source_evidence", "availability_rules", "availability_audits",
    "research_snapshots", "data_snapshots", "historical_runs", "monthly_runs",
    "model_versions", "champion_history", "forecast_vintages",
    "forecast_horizons", "forecast_bands", "actual_evaluations",
    "performance_metrics", "run_logs", "vintage_registry", "forecast_jobs", "champion_registry",
    "import_batches", "normalized_observations",
)
IMMUTABLE = set(ENTITIES) - {"historical_runs", "monthly_runs", "actual_evaluations", "performance_metrics", "run_logs", "vintage_registry", "forecast_jobs", "champion_registry"}


def content_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PersistenceProvider(ABC):
    def health(self) -> dict[str, str]:
        return {"status": "healthy", "provider": type(self).__name__}

    @abstractmethod
    def put(self, entity: str, key: str, payload: dict[str, Any]) -> None: ...

    @abstractmethod
    def get(self, entity: str, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def list(self, entity: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def items(self, entity: str) -> list[dict[str, Any]]: ...


class LocalPersistenceProvider(PersistenceProvider):
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            for entity in ENTITIES:
                connection.execute(f'''CREATE TABLE IF NOT EXISTS {entity}
                    (id TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                     inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _check(entity: str, key: str) -> None:
        if entity not in ENTITIES or not key:
            raise ValueError("invalid_persistence_entity_or_key")

    def put(self, entity: str, key: str, payload: dict[str, Any]) -> None:
        self._check(entity, key)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            existing = connection.execute(f"SELECT sha256 FROM {entity} WHERE id=?", (key,)).fetchone()
            if existing and entity in IMMUTABLE and existing["sha256"] != digest:
                raise ValueError("immutable_persistence_conflict")
            if existing and entity in IMMUTABLE:
                return
            connection.execute(f'''INSERT INTO {entity}(id,payload,sha256) VALUES(?,?,?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, sha256=excluded.sha256''',
                (key, encoded, digest))

    def get(self, entity: str, key: str) -> dict[str, Any] | None:
        self._check(entity, key)
        with self._connect() as connection:
            row = connection.execute(f"SELECT payload,sha256 FROM {entity} WHERE id=?", (key,)).fetchone()
        if row is None:
            return None
        if hashlib.sha256(row["payload"].encode("utf-8")).hexdigest() != row["sha256"]:
            raise ValueError("persistence_integrity_failed")
        return json.loads(row["payload"])

    def list(self, entity: str) -> list[dict[str, Any]]:
        self._check(entity, "list")
        with self._connect() as connection:
            keys = [row["id"] for row in connection.execute(f"SELECT id FROM {entity} ORDER BY id")]
        return [value for key in keys if (value := self.get(entity, key)) is not None]

    def items(self, entity: str) -> list[dict[str, Any]]:
        self._check(entity, "list")
        with self._connect() as connection:
            keys = [row["id"] for row in connection.execute(f"SELECT id FROM {entity} ORDER BY id")]
        return [{"key": key, "payload": self.get(entity, key)} for key in keys]

    def health(self) -> dict[str, str]:
        try:
            with self._connect() as connection:
                result = connection.execute("PRAGMA quick_check").fetchone()[0]
            return {"status": "healthy" if result == "ok" else "not_ready", "provider": "sqlite"}
        except (sqlite3.Error, OSError):
            return {"status": "not_ready", "provider": "sqlite", "error_code": "PERSISTENCE_001"}

    def backup(self, destination: Path) -> dict[str, Any]:
        target = Path(destination).resolve()
        if target == self.path.resolve() or target.exists():
            raise ValueError("backup_target_must_be_new")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as source:
            with closing(sqlite3.connect(target)) as copy:
                with copy:
                    source.backup(copy)
                    if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("backup_integrity_failed")
        return {"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "size": target.stat().st_size}

    def restore(self, source: Path, *, confirm: bool = False) -> dict[str, Any]:
        """Explicit restore with preflight and a recoverable safety backup."""
        if not confirm:
            raise ValueError("restore_requires_confirmation")
        candidate = Path(source).resolve()
        if not candidate.is_file() or candidate == self.path.resolve():
            raise ValueError("invalid_restore_source")
        with closing(sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("restore_source_corrupt")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not set(ENTITIES).issubset(tables):
                raise ValueError("restore_schema_mismatch")
        safety = self.path.with_suffix(self.path.suffix + ".pre-restore.bak")
        if safety.exists():
            raise ValueError("safety_backup_already_exists")
        self.backup(safety)
        with closing(sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True)) as origin:
            with self._connect() as destination:
                origin.backup(destination)
        if self.health()["status"] != "healthy":
            raise ValueError("restored_database_not_ready")
        return {"restored_from_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                "safety_backup": str(safety)}


class SupabasePersistenceProvider(PersistenceProvider):
    """Deliberately unconnected adapter boundary; no credentials or silent fallback."""

    def put(self, entity: str, key: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError("supabase_persistence_not_connected")

    def get(self, entity: str, key: str) -> dict[str, Any] | None:
        raise NotImplementedError("supabase_persistence_not_connected")

    def list(self, entity: str) -> list[dict[str, Any]]:
        raise NotImplementedError("supabase_persistence_not_connected")

    def items(self, entity: str) -> list[dict[str, Any]]:
        raise NotImplementedError("supabase_persistence_not_connected")

    def health(self) -> dict[str, str]:
        return {"status": "disabled", "provider": "supabase"}
