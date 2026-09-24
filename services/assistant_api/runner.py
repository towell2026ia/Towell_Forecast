"""Local monthly runner. Commands are deliberately not exposed to chat queries."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import logging
import tempfile
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .data_provider import DataProvider
from .persistence import PersistenceProvider

LOGGER = logging.getLogger("forecast_towell.runner")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _next_period(period: str) -> str:
    year, month = map(int, period.split("-"))
    return f"{year + (month == 12):04d}-{1 if month == 12 else month + 1:02d}"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    temporary.replace(path)


class ResearchProvider(ABC):
    def health(self) -> dict[str, str]:
        return {"status": "healthy", "provider": type(self).__name__}

    @abstractmethod
    def run(self, cutoff_date: str, chain: str, category: str | None = None, product: str | None = None,
            historical_context: dict[str, Any] | None = None) -> dict[str, Any]: ...


class LocalResearchProvider(ResearchProvider):
    def __init__(self, source_dir: Path | None = None):
        self.source_dir = Path(source_dir) if source_dir else None

    def health(self) -> dict[str, str]:
        if self.source_dir and not self.source_dir.is_dir():
            return {"status": "degraded", "provider": "local", "error_code": "RESEARCH_001"}
        return {"status": "healthy", "provider": "local"}

    def run(self, cutoff_date: str, chain: str, category: str | None = None, product: str | None = None,
            historical_context: dict[str, Any] | None = None) -> dict[str, Any]:
        from services.forecast_engine.identifiers import research_id
        period = cutoff_date[:7]
        candidate = self.source_dir / f"{period}.json" if self.source_dir else None
        source = json.loads(candidate.read_text(encoding="utf-8")) if candidate and candidate.exists() else {}
        if not isinstance(source, dict):
            raise ValueError("invalid_research_source")
        excluded = 0
        selected: dict[str, list[dict[str, Any]]] = {}
        for key in ("signals", "sources"):
            items = source.get(key, [])
            if not isinstance(items, list):
                raise ValueError("invalid_research_source")
            selected[key] = []
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("invalid_research_source")
                published = item.get("published_at")
                if not published or str(published)[:10] > cutoff_date:
                    excluded += 1
                    continue
                selected[key].append(item)
        payload = {
            "snapshot_id": research_id(chain, cutoff_date[:7]),
            "cutoff_date": cutoff_date, "chain": chain, "category": category, "product": product,
            "signals": selected["signals"], "sources": selected["sources"], "provider": "local",
            "status": "completed", "created_at": _utc(), "frozen": True,
            "excluded_after_cutoff": excluded,
            "historical_context": historical_context or {},
            "historical_replay": cutoff_date < date.today().isoformat(),
        }
        payload["content_hash"] = hashlib.sha256(json.dumps(
            {key: value for key, value in payload.items() if key != "created_at"},
            sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        return payload


class EnginePipeline(ABC):
    @abstractmethod
    def statistical(self, normalized_csv: Path) -> dict[str, Any]: ...

    @abstractmethod
    def ml(self, normalized_csv: Path) -> dict[str, Any]: ...

    @abstractmethod
    def ensemble(self, normalized_csv: Path, statistical: dict[str, Any], ml: dict[str, Any] | None) -> dict[str, Any]: ...


class ExistingEnginePipeline(EnginePipeline):
    def statistical(self, normalized_csv: Path) -> dict[str, Any]:
        from services.statistical_engine import engine
        return engine.build_payload(normalized_csv)

    def ml(self, normalized_csv: Path) -> dict[str, Any]:
        from services.ml_engine import engine
        return engine.build_payload(normalized_csv, "Venta")

    def ensemble(self, normalized_csv: Path, statistical: dict[str, Any], ml: dict[str, Any] | None) -> dict[str, Any]:
        return self._ensemble(normalized_csv, statistical, ml, historical=False)

    def ensemble_historical(self, normalized_csv: Path, statistical: dict[str, Any], ml: dict[str, Any] | None) -> dict[str, Any]:
        """Offline counterfactual promotion; never changes the published Champion."""
        return self._ensemble(normalized_csv, statistical, ml, historical=True)

    def _ensemble(self, normalized_csv: Path, statistical: dict[str, Any], ml: dict[str, Any] | None,
                  historical: bool) -> dict[str, Any]:
        from services.ensemble_engine import engine as ensemble
        from services.ml_engine import engine as ml_engine
        with tempfile.TemporaryDirectory(prefix="fendi-ensemble-") as directory:
            stat_file = Path(directory) / "statistical.json"
            ml_file = Path(directory) / "ml.json"
            stat_file.write_text(json.dumps(statistical, ensure_ascii=False), encoding="utf-8")
            if ml:
                ml_file.write_text(json.dumps(ml, ensure_ascii=False), encoding="utf-8")
            future, models, stat_version, ml_version, cutoff, stat_wape, ml_wape = ensemble.load_future(stat_file, ml_file if ml else None)
        series = ensemble.load_normalized_series(normalized_csv)
        factory = ml_engine.MODEL_FACTORIES.get(ml["champion"]["model"]) if ml else None
        records = ensemble.build_common_backtest_records(series, models, factory)
        records = [row for row in records if row.series_id == "total-fendi-bd"]
        future = [row for row in future if row.series_id == "total-fendi-bd"]
        config = ensemble.EnsembleConfig(reference_champion_wape=stat_wape)
        return ensemble.run_ensemble(records, future, cutoff, stat_version, ml_version, config,
                                     "Venta", stat_wape, ml_wape, promote_challenger=historical)


class MonthlyForecastRunner:
    STATES = ("Queued", "Preparing", "Research", "Running Statistical", "Running ML",
              "Running Ensemble", "Saving", "Completed", "Failed")

    def __init__(self, provider: DataProvider, research: ResearchProvider | None = None,
                 pipeline: EnginePipeline | None = None, state_dir: Path | None = None,
                 research_failure_policy: str = "fail_closed",
                 persistence: PersistenceProvider | None = None):
        self.provider = provider
        self.research = research or LocalResearchProvider()
        self.pipeline = pipeline or ExistingEnginePipeline()
        self.state_dir = Path(state_dir) if state_dir else Path(__file__).resolve().parent / "state"
        self.persistence = persistence
        if research_failure_policy not in {"fail_closed", "continue_without_signals"}:
            raise ValueError("invalid_research_failure_policy")
        self.research_failure_policy = research_failure_policy

    def _log(self, run_id: str, component: str, event: str, status: str, **extra: Any) -> None:
        LOGGER.info(json.dumps({"timestamp": _utc(), "run_id": run_id, "component": component,
                                "event": event, "status": status, **extra}, ensure_ascii=False))

    def run_month(self, period: str, actor: str = "local-process") -> dict[str, Any]:
        date.fromisoformat(f"{period}-01")
        cutoff = f"{period}-{__import__('calendar').monthrange(*map(int, period.split('-')))[1]:02d}"
        index = 1
        while (self.state_dir / "runs" / f"RUN-FENDI-{period}-{index:03d}.json").exists():
            index += 1
        run_id = f"RUN-FENDI-{period}-{index:03d}"
        path = self.state_dir / "runs" / f"{run_id}.json"
        started = time.monotonic()
        self._log(run_id, "runner", "run_started", "Queued")
        run: dict[str, Any] = {
            "run_id": run_id, "period": period, "cutoff_date": cutoff, "actor": actor,
            "data_provider": self.provider.name, "research_provider": type(self.research).__name__,
            "state": "Queued", "events": [], "versions": {}, "errors": [],
            "research_snapshot_id": None, "results": {}, "started_at": _utc(),
        }

        def advance(state: str) -> None:
            run["state"] = state
            run["events"].append({"timestamp": _utc(), "state": state})
            _atomic_json(path, run)
            self._log(run_id, "runner", "state_change", state)

        advance("Queued")
        try:
            from .settings import Settings
            if not Settings.from_env().monthly_runner_enabled:
                raise RuntimeError("monthly_runner_disabled")
            advance("Preparing")
            rows = [row for row in self.provider.records() if row["period"] <= period]
            if not rows or max(row["period"] for row in rows) < period:
                raise ValueError("cutoff_data_missing")
            if any(row["period"] > period for row in rows):
                raise ValueError("data_leakage_detected")
            if period < self.provider.load("ensemble")["cutoff"] and any(not row.get("available_at") for row in rows):
                raise ValueError("availability_metadata_missing")
            if any(row.get("available_at", "")[:10] > cutoff for row in rows):
                raise ValueError("data_leakage_detected")
            advance("Research")
            try:
                research = self.research.run(cutoff, "Walmart", "FENDI BD")
                evidence = [*research.get("sources", []), *research.get("signals", [])]
                if research.get("cutoff_date") != cutoff or any(
                    source.get("published_at", "")[:10] > cutoff for source in evidence
                    if isinstance(source, dict)
                ):
                    raise ValueError("research_leakage_detected")
            except Exception as exc:
                run["errors"].append({"stage": "Research", "error": type(exc).__name__})
                if self.research_failure_policy == "fail_closed":
                    raise
                research = LocalResearchProvider().run(cutoff, "Walmart", "FENDI BD")
                research["fallback_reason"] = "research_provider_failed"
            research["snapshot_id"] = f"{research['snapshot_id']}-{index:03d}"
            snapshot_path = self.state_dir / "research" / f"{run_id}.json"
            _atomic_json(snapshot_path, research)
            run["research_snapshot_id"] = research["snapshot_id"]
            run["research_snapshot_path"] = str(snapshot_path)
            if self.persistence:
                self.persistence.put("research_snapshots", research["snapshot_id"], research)
            with tempfile.TemporaryDirectory(prefix="fendi-cutoff-") as directory:
                normalized_csv = Path(directory) / "normalized.csv"
                with normalized_csv.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
                advance("Running Statistical")
                self._log(run_id, "statistical", "model_started", "Running")
                statistical = self.pipeline.statistical(normalized_csv)
                self._log(run_id, "statistical", "model_completed", "Completed")
                run["versions"]["statistical"] = statistical["run"]["version"]
                advance("Running ML")
                self._log(run_id, "ml", "model_started", "Running")
                try:
                    ml = self.pipeline.ml(normalized_csv)
                    self._log(run_id, "ml", "model_completed", "Completed")
                    run["versions"]["ml"] = ml["champion"]["version"]
                except Exception as exc:
                    ml = None
                    run["errors"].append({"stage": "Running ML", "error": type(exc).__name__,
                                          "fallback": "statistical"})
                    self._log(run_id, "ml", "fallback", "statistical")
                advance("Running Ensemble")
                self._log(run_id, "ensemble", "model_started", "Running")
                try:
                    final = self.pipeline.ensemble(normalized_csv, statistical, ml)
                    self._log(run_id, "ensemble", "model_completed", "Completed")
                except Exception as exc:
                    run["errors"].append({"stage": "Running Ensemble", "error": type(exc).__name__,
                                          "fallback": "retained_published_champion"})
                    current = self.provider.load("ensemble")
                    if current.get("cutoff") != period:
                        raise ValueError("no_valid_champion_for_cutoff") from exc
                    final = current
                run["versions"]["ensemble"] = final["version"]
                self._log(run_id, "ensemble", "champion_selected", "Completed",
                          strategy=(final.get("selection", {}).get("official") or {}).get("strategy"))
                advance("Saving")
                vintage = {"vintage_id": f"V-{run_id}", "issue_period": period,
                           "forecast_version": final["version"], "research_snapshot_id": research["snapshot_id"],
                           "forecast_towell": final["forecast_towell"]}
                run["results"] = {"statistical": statistical, "ml": ml, "ensemble": final,
                                  "bands": [row.get("probability") for row in final["forecast_towell"]],
                                  "vintage": vintage, "metrics": final.get("selection", {})}
                vintage_path = self.state_dir / "vintages.json"
                existing = json.loads(vintage_path.read_text(encoding="utf-8")) if vintage_path.exists() else []
                entries = [
                    {"vintage_id": f"{vintage['vintage_id']}-H{row['horizon']:02d}",
                     "issue_period": period, "target_period": row["period"],
                     "series_id": row["series_id"], "forecast_towell": row["value"],
                     "forecast_version": final["version"],
                     "research_snapshot_id": research["snapshot_id"]}
                    for row in final["forecast_towell"]
                ]
                _atomic_json(vintage_path, existing + entries)
                if self.persistence:
                    self.persistence.put("forecast_vintages", vintage["vintage_id"], vintage)
                    self.persistence.put("champion_history", run_id,
                                         {"run_id": run_id, "selection": final.get("selection", {})})
                    for row in entries:
                        self.persistence.put("forecast_horizons", row["vintage_id"], row)
                    for row in final["forecast_towell"]:
                        self.persistence.put("forecast_bands", f"{vintage['vintage_id']}-H{row['horizon']:02d}",
                                             {"vintage_id": vintage["vintage_id"], "horizon": row["horizon"],
                                              "bands": row.get("probability", {})})
                self._log(run_id, "forecast", "forecast_saved", "Completed")
            advance("Completed")
        except Exception as exc:
            safe_code = str(exc) if isinstance(exc, ValueError) and str(exc) in {
                "cutoff_data_missing", "availability_metadata_missing",
                "data_leakage_detected", "research_leakage_detected",
                "no_valid_champion_for_cutoff"
            } else type(exc).__name__
            run["errors"].append({"stage": run["state"], "error": safe_code})
            advance("Failed")
        run["duration_seconds"] = round(time.monotonic() - started, 3)
        run["finished_at"] = _utc()
        _atomic_json(path, run)
        if self.persistence:
            self.persistence.put("monthly_runs", run_id, run)
            self.persistence.put("model_versions", run_id, run.get("versions", {}))
            for number, event in enumerate(run["events"], 1):
                self.persistence.put("run_logs", f"{run_id}-{number:03d}",
                                     {"run_id": run_id, **event})
        self._log(run_id, "runner", "finished", run["state"], duration=run["duration_seconds"],
                  error=run["errors"][-1]["error"] if run["errors"] else None)
        self._log(run_id, "runner", "run_completed", run["state"], duration=run["duration_seconds"])
        return run

    async def run_month_async(self, period: str, actor: str = "local-process") -> dict[str, Any]:
        return await asyncio.to_thread(self.run_month, period, actor)

    def run_range(self, start_period: str, end_period: str, actor: str = "local-process") -> list[dict[str, Any]]:
        if start_period > end_period:
            raise ValueError("invalid_period_range")
        runs = []
        current = start_period
        while current <= end_period:
            runs.append(self.run_month(current, actor))
            current = _next_period(current)
        return runs


class GeneralizedMonthlyForecastRunner:
    """Official generic runner. Legacy pilot replays remain in HistoricalForecastRunner.

    Every new run requires explicit point-in-time availability. The committed pilot
    CSV has no such field, so it cannot silently become certified evidence.
    """

    def __init__(self, provider: DataProvider, research: ResearchProvider | None = None,
                 state_dir: Path | None = None, persistence: PersistenceProvider | None = None,
                 policy: Any | None = None):
        self.provider = provider
        self.research = research or LocalResearchProvider()
        self.state_dir = Path(state_dir) if state_dir else Path(__file__).resolve().parent / "state"
        self.persistence = persistence
        self.policy = policy

    def run_month(self, period: str, chain_id: str | None = None,
                  product_id: str | None = None, actor: str | None = None,
                  objective: str = "Venta") -> dict[str, Any]:
        from services.forecast_engine import ChampionRegistry, forecast_dataset
        from services.forecast_engine.engine import month_end
        from services.forecast_engine.identifiers import research_id, run_id as make_run_id, vintage_id
        from .settings import Settings

        cutoff = month_end(period)
        sequence = 1
        while (self.state_dir / "runs" / f"{make_run_id(chain_id, period, sequence)}.json").exists():
            sequence += 1
        identifier = make_run_id(chain_id, period, sequence)
        path = self.state_dir / "runs" / f"{identifier}.json"
        started = time.monotonic()
        run: dict[str, Any] = {"run_id": identifier, "period": period,
                               "chain_id": chain_id, "product_id": product_id,
                               "objective": objective, "cutoff_date": cutoff,
                               "actor": actor or "local-process", "state": "Queued",
                               "errors": [], "results": {}, "started_at": _utc()}
        _atomic_json(path, run)
        try:
            if not Settings.from_env().monthly_runner_enabled:
                raise RuntimeError("monthly_runner_disabled")
            rows = [row for row in self.provider.records()
                    if str(row.get("objective", "")).casefold() == objective.casefold()
                    and (chain_id is None or str(row.get("chain_id") or row.get("chain_code") or row.get("chain")) == chain_id)
                    and (product_id is None or str(row.get("product_id") or row.get("canonical_product_id")) == product_id)]
            if not rows:
                raise ValueError("no_eligible_products")
            chains = sorted({str(row.get("chain_id") or row.get("chain_code") or row.get("chain") or "") for row in rows})
            if "" in chains:
                raise ValueError("identity_missing")
            registry = ChampionRegistry(self.persistence) if self.persistence else None
            results = []
            for chain in chains:
                research = self.research.run(cutoff, chain)
                if research.get("cutoff_date") != cutoff:
                    raise ValueError("research_leakage_detected")
                research["snapshot_id"] = f"{research_id(chain, period, research.get('content_hash', ''))}-{sequence:03d}"
                current = registry.current(chain, objective, "chain") if registry else None
                try:
                    result = forecast_dataset(rows, period, chain_id=chain,
                                              product_id=product_id, objective=objective,
                                              policy=self.policy, incumbent=current, research=research,
                                              version_sequence=sequence)["chains"][0]
                except (RuntimeError, ArithmeticError) as exc:
                    previous = [] if not (self.persistence and current) else self.persistence.list("forecast_vintages")
                    retained = next(((vintage["vintage_id"], entry) for vintage in reversed(previous)
                                     for entry in vintage.get("chains", [])
                                     if entry.get("chain_id") == chain and
                                     entry.get("version") == current.get("version")), None)
                    if retained is None:
                        raise ValueError("no_valid_published_forecast_fallback") from exc
                    result = {**retained[1], "fallback": "retained_published_champion",
                              "source_vintage_id": retained[0],
                              "fallback_reason": type(exc).__name__}
                results.append(result)
                if self.persistence:
                    self.persistence.put("research_snapshots", research["snapshot_id"], research)
            vintage = {"vintage_id": vintage_id(identifier), "issue_period": period,
                       "objective": objective, "chains": results, "published_champion_changed": False}
            fallback_used = any("fallback" in chain for chain in results)
            if self.persistence:
                if not fallback_used:
                    self.persistence.put("forecast_vintages", vintage["vintage_id"], vintage)
                for chain in results:
                    if "fallback" not in chain and not fallback_used:
                        self.persistence.put("model_versions", f"{identifier}|{chain['chain_id']}", chain["model_audit"])
                        for row in chain["forecast_towell"]:
                            key = f"{vintage['vintage_id']}|{row['chain_id']}|{row['product_id']}|{row['horizon']:02d}"
                            self.persistence.put("forecast_horizons", key, row)
                            self.persistence.put("forecast_bands", key, {"probability": row["probability"]})
            if fallback_used:
                vintage = {"vintage_id": None, "retained_vintages":
                           [chain["source_vintage_id"] for chain in results if "fallback" in chain]}
            run["results"] = {"chains": results, "vintage": vintage}
            run["state"] = "Completed"
        except Exception as exc:
            run["state"] = "Failed"
            run["errors"].append({"stage": "forecast", "error": str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
        run["duration_seconds"] = round(time.monotonic() - started, 3)
        run["finished_at"] = _utc()
        _atomic_json(path, run)
        if self.persistence:
            self.persistence.put("monthly_runs", identifier, run)
        return run

    async def run_month_async(self, period: str, chain_id: str | None = None,
                              product_id: str | None = None, actor: str | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self.run_month, period, chain_id, product_id, actor)
