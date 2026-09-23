"""Read published, normalized platform results without consulting source Excel files."""

from __future__ import annotations

import csv
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "app" / "data"


class DataProvider(ABC):
    def health(self) -> dict[str, str]:
        return {"status": "healthy", "provider": self.name}

    @abstractmethod
    def load(self, name: str) -> dict[str, Any]: ...

    @abstractmethod
    def records(self) -> list[dict[str, str]]: ...

    @abstractmethod
    def decisions(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def vintages(self) -> list[dict[str, Any]]: ...

    @property
    @abstractmethod
    def name(self) -> str: ...


class NormalizedDataProvider(DataProvider):
    """The committed platform snapshots are authoritative until a live store exists.

    Optional operational decisions/vintages are read from a separate local state
    directory. Test fixtures are deliberately excluded from production queries.
    """

    FILES = {
        "dashboard": "fendi-dashboard.json",
        "statistical": "forecast-demo.json",
        "ml": "ml-demo.json",
        "ml_pedido": "ml-pedido-demo.json",
        "ensemble": "ensemble-demo.json",
    }

    def __init__(self, data_dir: Path = DATA, state_dir: Path | None = None):
        self.data_dir = Path(data_dir)
        self.state_dir = Path(state_dir) if state_dir else ROOT / "services" / "assistant_api" / "state"

    @property
    def name(self) -> str:
        return "normalized"

    def health(self) -> dict[str, str]:
        required = [self.data_dir / "fendi-engine-series.csv",
                    *(self.data_dir / name for name in self.FILES.values())]
        if not all(path.is_file() and path.stat().st_size > 0 for path in required):
            return {"status": "not_ready", "provider": self.name, "error_code": "DATA_001"}
        return {"status": "healthy", "provider": self.name}

    def load(self, name: str) -> dict[str, Any]:
        if name not in self.FILES:
            raise ValueError("unknown_platform_dataset")
        return json.loads((self.data_dir / self.FILES[name]).read_text(encoding="utf-8"))

    def records(self) -> list[dict[str, str]]:
        with (self.data_dir / "fendi-engine-series.csv").open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _operational_list(self, filename: str) -> list[dict[str, Any]]:
        path = self.state_dir / filename
        if not path.exists():
            return []
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, list):
            raise ValueError("invalid_operational_store")
        return result

    def decisions(self) -> list[dict[str, Any]]:
        return self._operational_list("decisions.json")

    def vintages(self) -> list[dict[str, Any]]:
        return self._operational_list("vintages.json")


class SupabaseDataProvider(DataProvider):
    """Future adapter contract. No network or Supabase use in PRD 08B."""

    @property
    def name(self) -> str:
        return "supabase"

    def health(self) -> dict[str, str]:
        return {"status": "disabled", "provider": self.name}

    def load(self, name: str) -> dict[str, Any]:
        raise NotImplementedError("supabase_disabled")

    def records(self) -> list[dict[str, str]]:
        raise NotImplementedError("supabase_disabled")

    def decisions(self) -> list[dict[str, Any]]:
        raise NotImplementedError("supabase_disabled")

    def vintages(self) -> list[dict[str, Any]]:
        raise NotImplementedError("supabase_disabled")
