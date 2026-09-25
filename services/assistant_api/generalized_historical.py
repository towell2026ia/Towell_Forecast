"""Generic point-in-time replay; the frozen pilot runner is not used here."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from services.forecast_engine.engine import add_month, month_end

from .data_provider import DataProvider
from .ingestion import resolve_availability, utc_timestamp
from .persistence import PersistenceProvider
from .runner import GeneralizedMonthlyForecastRunner, ResearchProvider


class _SnapshotProvider(DataProvider):
    def __init__(self, delegate: DataProvider, rows: list[dict[str, Any]]):
        self.delegate, self.rows = delegate, rows

    @property
    def name(self) -> str:
        return "point_in_time"

    def load(self, name: str) -> dict[str, Any]:
        return self.delegate.load(name)

    def records(self) -> list[dict[str, Any]]:
        return self.rows

    def decisions(self) -> list[dict[str, Any]]:
        return self.delegate.decisions()

    def vintages(self) -> list[dict[str, Any]]:
        return self.delegate.vintages()


class GeneralizedHistoricalForecastRunner:
    """Replay every requested month with only information known at its cutoff."""

    def __init__(self, provider: DataProvider, persistence: PersistenceProvider,
                 research: ResearchProvider | None = None, state_dir: Path | None = None,
                 legacy_auditor: Any | None = None):
        self.provider = provider
        self.persistence = persistence
        self.research = research
        self.state_dir = Path(state_dir) if state_dir else Path(__file__).resolve().parent / "state" / "generic_historical"
        self.legacy_auditor = legacy_auditor
        self.availability = self

    def _eligible(self, period: str, chain_id: str | None, objective: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        cutoff = month_end(period)
        rows, blocked = [], []
        for source in self.provider.records():
            if str(source.get("objective", "")).casefold() != objective.casefold():
                continue
            chain = str(source.get("chain_id") or source.get("chain_code") or source.get("chain") or "")
            if chain_id is not None and chain != chain_id:
                continue
            observation_period = str(source.get("period", ""))
            month_end(observation_period)
            if observation_period > period:
                continue
            resolved = resolve_availability(source, self.persistence, legacy_auditor=self.legacy_auditor)
            if resolved is None:
                blocked.append({"period": observation_period, "reason": "UNKNOWN_AVAILABILITY"})
                continue
            try:
                available = utc_timestamp(str(resolved["available_at"]))
            except (ValueError, KeyError):
                blocked.append({"period": observation_period, "reason": "INVALID_AVAILABILITY"})
                continue
            if available > f"{cutoff}T23:59:59.999999Z":
                blocked.append({"period": observation_period, "reason": "AFTER_CUTOFF"})
                continue
            rows.append(resolved)
        return rows, blocked

    def validate_temporal_readiness(self, period: str, chain: str | None = None,
                                    cutoff: str | None = None, objective: str = "Venta") -> dict[str, Any]:
        if cutoff is not None and cutoff != month_end(period):
            raise ValueError("unsupported_execution_cutoff")
        rows, blocked = self._eligible(period, chain, objective)
        return {"period": period, "cutoff": month_end(period), "chain_id": chain,
                "objective": objective, "ready": bool(rows) and not blocked,
                "status": "READY" if rows and not blocked else "BLOCKED_AVAILABILITY",
                "eligible_rows": len(rows), "blocked": blocked,
                "blocking_fields": sorted({item["reason"] for item in blocked})}

    def audit_availability(self, start_period: str, end_period: str,
                           chain: str | None = None, objective: str = "Venta") -> dict[str, Any]:
        if start_period > end_period:
            raise ValueError("invalid_period_range")
        periods = []
        current = start_period
        while current <= end_period:
            periods.append(self.validate_temporal_readiness(current, chain, objective=objective))
            current = add_month(current, 1)
        return {"periods": periods, "periods_audited": len(periods),
                "ready_count": sum(item["ready"] for item in periods)}

    def find_first_temporally_valid_period(self, start_period: str, end_period: str,
                                           chain: str | None = None, objective: str = "Venta") -> dict[str, Any] | None:
        return next((item for item in self.audit_availability(start_period, end_period, chain, objective)["periods"]
                     if item["ready"]), None)

    def run(self, chain_id: str | None, start_period: str, end_period: str,
            objective: str = "Venta", actor: str = "local-process") -> dict[str, Any]:
        if start_period > end_period:
            raise ValueError("invalid_period_range")
        if objective not in {"Venta", "Pedido", "Entrega"}:
            raise ValueError("unsupported_forecast_objective")
        identifier = f"GH-{uuid.uuid4().hex}"
        runs = []
        current = start_period
        while current <= end_period:
            rows, blocked = self._eligible(current, chain_id, objective)
            if blocked or not rows:
                runs.append({"period": current, "status": "BLOCKED_AVAILABILITY",
                             "blocked": blocked or [{"reason": "NO_ELIGIBLE_OBSERVATIONS"}]})
            else:
                monthly = GeneralizedMonthlyForecastRunner(
                    _SnapshotProvider(self.provider, rows), research=self.research,
                    state_dir=self.state_dir, persistence=self.persistence)
                result = monthly.run_month(current, chain_id=chain_id, actor=actor, objective=objective)
                runs.append({"period": current, "status": result["state"].upper(),
                             "run_id": result["run_id"], "result": result})
            current = add_month(current, 1)
        payload = {"run_id": identifier, "chain_id": chain_id, "objective": objective,
                   "start_period": start_period, "end_period": end_period, "actor": actor,
                   "status": "COMPLETED" if all(item["status"] == "COMPLETED" for item in runs)
                   else "BLOCKED_AVAILABILITY" if any(item["status"] == "BLOCKED_AVAILABILITY" for item in runs)
                   else "FAILED", "runs": runs}
        self.persistence.put("historical_runs", identifier, payload)
        return payload
