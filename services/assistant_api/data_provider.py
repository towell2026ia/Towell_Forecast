"""Read published, normalized platform results without consulting source Excel files."""

from __future__ import annotations

import csv
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .persistence import PersistenceProvider

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


class PersistedObservationProvider(DataProvider):
    """Keep legacy dashboard reads separate from the active forecast input."""

    def __init__(self, display_provider: DataProvider, persistence: PersistenceProvider):
        self.display_provider = display_provider
        self.persistence = persistence

    @property
    def name(self) -> str:
        return "normalized_observations"

    def health(self) -> dict[str, str]:
        return self.persistence.health()

    def load(self, name: str) -> dict[str, Any]:
        return self.display_provider.load(name)

    def records(self) -> list[dict[str, Any]]:
        return self.persistence.list("normalized_observations")

    def decisions(self) -> list[dict[str, Any]]:
        return self.display_provider.decisions()

    def vintages(self) -> list[dict[str, Any]]:
        return self.display_provider.vintages()


class SupabaseDataProvider(DataProvider):
    """E2 GET-only input adapter; every read carries the caller's verified JWT."""

    def __init__(self, client=None):
        self.client = client
        self._catalogs = {}

    def bind(self, token: str):
        return SupabaseDataProvider(self.client.bind(token))

    def product_catalog(self, chain_id: str):
        from .supabase_access import uuid_value
        chain_id = uuid_value(chain_id)
        if chain_id not in self._catalogs:
            self._catalogs[chain_id] = self.client.rows("products", {
                "select": "id,chain_id,product_code,variant_code,description,category_id,first_seen_period",
                "chain_id": f"eq.{chain_id}", "order": "id.asc"})
            from .supabase_access import PreviewError
            if any(row.get("chain_id") != chain_id for row in self._catalogs[chain_id]):
                raise PreviewError("DATA_READ_FAILED", 503)
        return self._catalogs[chain_id]

    @property
    def name(self) -> str:
        return "supabase"

    def health(self) -> dict[str, str]:
        return {"status": "healthy" if self.client else "disabled", "provider": self.name}

    def load(self, name: str) -> dict[str, Any]:
        raise NotImplementedError("preview_only_no_legacy_snapshots")

    def records(self, *, chain_id: str, product_id: str | None = None, objective: str = "Venta",
                issue_period: str | None = None, mode: str = "RETROSPECTIVE_TRAINING") -> list[dict[str, Any]]:
        from .supabase_access import PreviewError, uuid_value
        from services.forecast_engine.engine import month_end
        if not self.client:
            raise PreviewError("AUTH_REQUIRED", 401)
        chain_id = uuid_value(chain_id)
        if objective != "Venta" or mode not in {"RETROSPECTIVE_TRAINING", "POINT_IN_TIME"}:
            raise PreviewError("REQUEST_001")
        params = {"select": "*", "chain_id": f"eq.{chain_id}", "metric_code": "eq.SALES",
                  "order": "product_id.asc,period.asc"}
        if product_id:
            params["product_id"] = f"eq.{uuid_value(product_id)}"
        if issue_period:
            params["period"] = f"lte.{issue_period}-01"
        if mode == "POINT_IN_TIME":
            params["available_at"] = "not.is.null"
            if issue_period:
                params["and"] = f"(available_at.lte.{month_end(issue_period)}T23:59:59Z)"
        table = "portal_monthly_observations_current" if mode == "RETROSPECTIVE_TRAINING" else "monthly_observations_current"
        categories = {row["id"]: row["name"] for row in self.client.rows("categories",
            {"select": "id,name", "chain_id": f"eq.{chain_id}", "order": "id.asc"})}
        products = {row["id"]: row for row in self.product_catalog(chain_id)}
        records, seen = [], set()
        for row in self.client.rows(table, params):
            if row.get("chain_id") != chain_id or row.get("metric_code") != "SALES" or row.get("product_id") not in products \
                    or product_id and row.get("product_id") != product_id:
                raise PreviewError("DATA_READ_FAILED", 503)
            identity = (chain_id, row["product_id"], row["period"])
            if identity in seen:
                raise PreviewError("DUPLICATE_OBSERVATION_CONFLICT", 503)
            seen.add(identity)
            product = products[row["product_id"]]
            category = product.get("category_id")
            if category is not None and category not in categories:
                raise PreviewError("DATA_READ_FAILED", 503)
            records.append({"chain_id": chain_id, "product_id": row["product_id"],
                "chain_name": row.get("chain_name", chain_id),
                "product_code": product["product_code"], "description": product["description"],
                "variant": product.get("variant_code") or "", "category_id": category,
                "category": category or "UNCLASSIFIED", "category_name": categories.get(category, "Sin categoría"),
                "period": row["period"][:7], "objective": "Venta", "value": row["value"],
                "available_at": row.get("available_at"), "availability_source": row.get("availability_source"),
                "source_id": row.get("observation_id") or row.get("id"), "version_no": row.get("version_no"),
                "source_batch_id": row.get("source_batch_id"), "evidence_mode": mode})
        return records

    def decisions(self) -> list[dict[str, Any]]:
        return []

    def vintages(self) -> list[dict[str, Any]]:
        return []
