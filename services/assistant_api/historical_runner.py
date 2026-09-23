"""Temporal, offline forecast replay. Never publishes or replaces the live Champion."""

from __future__ import annotations

import asyncio
import calendar
import csv
import hashlib
import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from services.ensemble_engine.engine import EnsembleConfig

from .data_provider import DataProvider
from .availability import GATE_VERSION, TemporalAvailabilityAuditor, _identity
from .evidence_registry import EvidenceRegistry, VintageRegistry
from .persistence import LocalPersistenceProvider, PersistenceProvider
from .runner import EnginePipeline, ExistingEnginePipeline, LocalResearchProvider, ResearchProvider, _atomic_json, _next_period, _utc, LOGGER


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _month_end(period: str) -> str:
    year, month = map(int, period.split("-"))
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def _valid_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _immutable_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic create-if-absent. A changed snapshot gets a different hash/path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     suffix=".tmp", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != payload:
                raise ValueError("frozen_snapshot_conflict")
    finally:
        temporary.unlink(missing_ok=True)


def _period_range(start: str, end: str) -> list[str]:
    _month_end(start)
    _month_end(end)
    if start > end:
        raise ValueError("invalid_period_range")
    result = []
    current = start
    while current <= end:
        result.append(current)
        current = _next_period(current)
    return result


@dataclass(frozen=True)
class HistoricalRunConfig:
    start: str
    end: str
    chain: str = "Walmart"
    pilot_scope: str = "FENDI BD"
    forecast_horizon: int = 12
    allow_future_data: bool = False
    stop_on_error: bool = True
    random_seed: int = 20260919
    missing_availability: str = "exclude"

    def __post_init__(self) -> None:
        _period_range(self.start, self.end)
        if (self.forecast_horizon != 12 or self.allow_future_data or self.missing_availability != "exclude"
                or self.random_seed != 20260919):
            raise ValueError("unsafe_historical_configuration")


class HistoricalForecastRunner:
    """Runs one month or a resumable parent range from point-in-time evidence.

    The current normalized pilot CSV lacks `available_at`; those rows cannot be
    replayed at earlier cutoffs and are intentionally excluded until evidenced.
    """

    # The statistical engine's rolling backtest begins at origin=6 and needs
    # one subsequent actual: seven consecutive observed months after launch.
    MODEL_REQUIREMENTS = {
        "statistical": {"minimum_history_required": 7, "basis": "rolling origin 6 + one actual"},
        "ml": {"minimum_training_samples": 18, "eligibility": "engine temporal backtest"},
        "ensemble": {"minimum_observations": EnsembleConfig().minimum_observations,
                     "minimum_windows": EnsembleConfig().minimum_windows},
    }

    def __init__(self, provider: DataProvider, research: ResearchProvider | None = None,
                 pipeline: EnginePipeline | None = None, state_dir: Path | None = None,
                 persistence: PersistenceProvider | None = None):
        self.provider = provider
        self.research = research or LocalResearchProvider()
        self.pipeline = pipeline or ExistingEnginePipeline()
        self.state_dir = Path(state_dir) if state_dir else Path(__file__).resolve().parent / "state" / "historical"
        self.availability = TemporalAvailabilityAuditor(provider, self.state_dir)
        self.persistence = persistence or LocalPersistenceProvider(self.state_dir / "historical.sqlite3")
        self.evidence = EvidenceRegistry(self.persistence, self.availability)
        self.vintage_registry = VintageRegistry(self.persistence, self.state_dir)
        self._cancel = threading.Event()

    def harden_evidence(self, source_dir: Path) -> dict[str, Any]:
        """Verify source bytes/cells and register a frozen vintage without changing its JSON."""
        manifests = [self.evidence.register_local_source(source_dir, rule_id)
                     for rule_id, rule in self.availability.registry.rules.items()
                     if rule.get("enabled") and rule.get("source_file")]
        imported = []
        for run_path in sorted((self.state_dir / "runs").glob("*.json")):
            run = json.loads(run_path.read_text(encoding="utf-8"))
            if run.get("status") != "COMPLETED" or not run.get("vintage_id"):
                continue
            snapshot_path = self.state_dir / "data" / f"{run['data_snapshot_id']}.json"
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            source_hashes = {row.get("source_sha256", "").lower() for row in snapshot["rows"]}
            matching = [item for item in manifests if item["sha256"] in source_hashes]
            if len(matching) != 1:
                continue
            imported.append(self.vintage_registry.import_frozen(run["vintage_id"],
                          matching[0]["evidence_manifest_id"]))
            self.persistence.put("research_snapshots", run["research_snapshot_id"],
                                 json.loads((self.state_dir / "research" / f"{run['research_snapshot_id']}.json").read_text(encoding="utf-8")))
            self.persistence.put("data_snapshots", run["data_snapshot_id"], snapshot)
            self.persistence.put("model_versions", run["run_id"], run.get("versions", {}))
            self.persistence.put("champion_history", run["run_id"],
                                 {"run_id": run["run_id"], "champion": run.get("champion"),
                                  "cutoff_date": run.get("cutoff_date")})
            evaluation_path = self.state_dir / "evaluations" / f"{run['vintage_id']}.json"
            if evaluation_path.exists():
                self.persistence.put("actual_evaluations", run["vintage_id"],
                                     json.loads(evaluation_path.read_text(encoding="utf-8")))
        report_path = self.state_dir / "first_vintage.json"
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            entry = next((entry for entry in imported if entry["vintage_id"] == report.get("vintage_id")), None)
            if entry:
                report.update(status=entry["validation_status"], registry_status=entry["status"],
                              evidence_level=entry["evidence_level"],
                              evidence_manifest_id=entry["evidence_manifest_id"])
                _atomic_json(report_path, report)
        return {"evidence_manifests": manifests, "registered_vintages": imported}

    def expand_vintages(self, source_dir: Path, start: str = "2023-01", end: str = "2026-08",
                        *, max_new: int = 3) -> dict[str, Any]:
        """Only run evidenced, temporally ready cutoffs; never manufacture dates."""
        if not 1 <= max_new <= 12:
            raise ValueError("invalid_expansion_limit")
        evidence = self.persistence.list("source_evidence")
        existing = {item.get("cutoff_date", "")[:7] for item in self.vintage_registry.all()}
        accepted, blocked = [], []
        for period in _period_range(start, end):
            if period in existing:
                continue
            dates = sorted({item["evidence_date"] for item in evidence
                            if item.get("evidence_level") in {"E1", "E2", "E3"} and
                            item.get("evidence_date", "")[:7] == period})
            if not dates:
                blocked.append({"period": period, "reason": "no_registered_temporal_evidence"})
                continue
            candidates = [self.availability.validate_temporal_readiness(period, cutoff=day) for day in dates]
            candidate = next((item for item in candidates if item["ready"]), None)
            if candidate is None:
                blocked.append({"period": period, "reason": candidates[-1]["status"]})
                continue
            if len(accepted) >= max_new:
                break
            run = self.run_month(period, cutoff_date=candidate["cutoff"])
            if run.get("status") == "COMPLETED":
                registered = self.harden_evidence(source_dir)["registered_vintages"]
                if any(item["vintage_id"] == run["vintage_id"] for item in registered):
                    accepted.append(run["vintage_id"])
                else:
                    blocked.append({"period": period, "reason": "completed_but_unregistered"})
            else:
                blocked.append({"period": period, "reason": run.get("status")})
        return {"new_vintage_ids": accepted, "blocked": blocked,
                "registered_vintages": len(self.vintage_registry.all())}

    def _log(self, run_id: str, period: str | None, step: str, status: str,
             duration: float | None = None, **extra: Any) -> None:
        LOGGER.info(json.dumps({"timestamp": _utc(), "run_id": run_id, "period": period,
                                "step": step, "status": status, "duration": duration, **extra},
                               ensure_ascii=False))

    def _persist_monthly_run(self, run: dict[str, Any]) -> None:
        self.persistence.put("monthly_runs", run["run_id"], run)
        for number, event in enumerate(run.get("events", []), 1):
            self.persistence.put("run_logs", f"{run['run_id']}-{number:03d}",
                                 {"run_id": run["run_id"], **event})
        evaluation = run.get("evaluation")
        if evaluation and run.get("vintage_id"):
            self.persistence.put("actual_evaluations", run["vintage_id"], evaluation)
            self.persistence.put("performance_metrics", run["vintage_id"],
                                 {"vintage_id": run["vintage_id"],
                                  "metrics": evaluation.get("metrics", {})})

    def _scope_rows(self, config: HistoricalRunConfig) -> list[dict[str, str]]:
        return [row for row in self.provider.records()
                if row.get("chain") == config.chain and config.pilot_scope.casefold() in
                row.get("pilot_scope", "").casefold()]

    def _engine_versions(self) -> dict[str, str]:
        if isinstance(self.pipeline, ExistingEnginePipeline):
            from services.statistical_engine import engine as statistical
            from services.ml_engine import engine as ml
            from services.ensemble_engine import engine as ensemble
            return {"statistical": statistical.ENGINE_VERSION,
                    "ml": ml.ENGINE_VERSION, "ensemble": ensemble.ENGINE_VERSION}
        return {"pipeline": str(getattr(self.pipeline, "version", type(self.pipeline).__name__))}

    def _inputs_fingerprint(self, config: HistoricalRunConfig) -> str | None:
        if not isinstance(self.research, LocalResearchProvider):
            return None  # A dynamic provider cannot prove that its evidence is unchanged.
        research_files = []
        for period in _period_range(config.start, config.end):
            path = self.research.source_dir / f"{period}.json" if self.research.source_dir else None
            if path and path.exists():
                research_files.append({"period": period, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        return _digest({"rows": self._scope_rows(config),
                        "availability_assignments": self.availability._assignments(),
                        "availability_catalog_sha256": self.availability.registry.catalog_sha256,
                        "availability_gate_version": GATE_VERSION,
                        "research_files": research_files,
                        "engine_versions": self._engine_versions()})

    def _research_snapshot(self, cutoff: str, config: HistoricalRunConfig) -> dict[str, Any]:
        context = {"period": cutoff[:7], "pilot_scope": config.pilot_scope, "baseline": True}
        payload = self.research.run(cutoff, config.chain, historical_context=context)
        if not isinstance(payload, dict) or payload.get("cutoff_date") != cutoff:
            raise ValueError("research_cutoff_mismatch")
        if payload.get("status", "completed") != "completed":
            raise ValueError("research_failed")
        filtered = dict(payload)
        excluded = 0
        for field in ("sources", "signals"):
            values = payload.get(field, [])
            if not isinstance(values, list):
                raise ValueError("invalid_research_evidence")
            allowed = []
            for value in values:
                if not isinstance(value, dict):
                    raise ValueError("invalid_research_evidence")
                published = _valid_date(value.get("published_at"))
                if published is None or published > cutoff:
                    excluded += 1
                    continue
                allowed.append(value)
            filtered[field] = allowed
        filtered.update({"provider": payload.get("provider", type(self.research).__name__),
                         "status": "completed", "frozen": True,
                         "excluded_after_cutoff": int(payload.get("excluded_after_cutoff", 0)) + excluded,
                         "signal_count": len(filtered["signals"]),
                         "source_count": len(filtered["sources"])})
        content = {key: value for key, value in filtered.items()
                   if key not in {"created_at", "generated_at", "content_hash", "snapshot_id"}}
        filtered["hash"] = _digest(content)
        filtered["snapshot_id"] = f"RS-FENDI-{cutoff[:7]}-{filtered['hash'][:12]}"
        filtered.pop("content_hash", None)
        path = self.state_dir / "research" / f"{filtered['snapshot_id']}.json"
        # Time of first freeze is not part of the content hash.
        if path.exists():
            stored = json.loads(path.read_text(encoding="utf-8"))
            stored_content = {key: value for key, value in stored.items()
                              if key not in {"created_at", "generated_at", "content_hash", "snapshot_id", "hash"}}
            if stored.get("hash") != _digest(stored_content) or stored.get("hash") != filtered["hash"] or not stored.get("frozen"):
                raise ValueError("research_snapshot_integrity_failed")
            self.persistence.put("research_snapshots", stored["snapshot_id"], stored)
            return stored
        filtered["created_at"] = _utc()
        filtered.pop("generated_at", None)
        _immutable_json(path, filtered)
        self.persistence.put("research_snapshots", filtered["snapshot_id"], filtered)
        return filtered

    def _data_snapshot(self, period: str, cutoff: str, config: HistoricalRunConfig) -> dict[str, Any]:
        gated = self.availability.gate(period, cutoff, chain=config.chain,
                                       pilot_scope=config.pilot_scope)
        manifest = {"period": period, "cutoff": cutoff,
                    "availability_gate_version": GATE_VERSION,
                    "availability_catalog_sha256": self.availability.registry.catalog_sha256,
                    "included_records": len(gated["rows"]) + len(gated["known_future"]),
                    "excluded_records": len(gated["excluded_records"]),
                    "included_record_ids": [_identity(row) for row in gated["rows"]],
                    "known_future_record_ids": [_identity(row) for row in gated["known_future"]],
                    "excluded": gated["excluded_records"],
                    "exclusion_reasons": gated["exclusion_reasons"]}
        manifest["hash"] = _digest(manifest)
        manifest["manifest_id"] = f"IM-FENDI-{period}-{manifest['hash'][:12]}"
        _immutable_json(self.state_dir / "manifests" / f"{manifest['manifest_id']}.json", manifest)
        content = {"period": period, "cutoff_date": cutoff,
                   "availability_gate_version": GATE_VERSION,
                   "availability_catalog_sha256": self.availability.registry.catalog_sha256,
                   "rows": gated["rows"],
                   "known_future": gated["known_future"],
                   "excluded": gated["exclusion_reasons"],
                   "input_manifest_id": manifest["manifest_id"],
                   "input_manifest_hash": manifest["hash"]}
        digest = _digest(content)
        snapshot = {**content, "snapshot_id": f"DS-FENDI-{period}-{digest[:12]}",
                    "hash": digest, "frozen": True}
        _immutable_json(self.state_dir / "data" / f"{snapshot['snapshot_id']}.json", snapshot)
        self.persistence.put("availability_audits", manifest["manifest_id"], manifest)
        self.persistence.put("data_snapshots", snapshot["snapshot_id"], snapshot)
        return snapshot

    def _history(self, snapshot: dict[str, Any], period: str) -> dict[str, Any]:
        months: dict[str, float] = {}
        for row in snapshot["rows"]:
            if row.get("objective", "").casefold() != "venta" or row.get("is_missing", "").casefold() == "true":
                continue
            try:
                months[row["period"]] = months.get(row["period"], 0.0) + float(row["value"])
            except (ValueError, KeyError):
                continue
        observed = sorted(months)
        first_active = next((month for month in observed if months[month] > 0), None)
        active = []
        current = first_active
        while current and current <= period and current in months:
            active.append(current)
            current = _next_period(current)
        latest = active[-1] if active else None
        eligible = bool(active and len(active) >= self.MODEL_REQUIREMENTS["statistical"]["minimum_history_required"]
                        and (latest == period or _next_period(latest) == period))
        return {"earliest_available_period": observed[0] if observed else None,
                "first_active_period": first_active,
                "latest_observed_period": latest,
                "consecutive_active_periods": len(active), "eligible": eligible,
                "minimum_history_required": self.MODEL_REQUIREMENTS["statistical"]["minimum_history_required"]}

    def _existing_runs(self, period: str) -> list[dict[str, Any]]:
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((self.state_dir / "runs").glob(f"RUN-FENDI-{period}-*.json"))]

    def _next_child_id(self, period: str) -> str:
        runs = self._existing_runs(period)
        number = max((int(run["run_id"].rsplit("-", 1)[-1]) for run in runs), default=0) + 1
        return f"RUN-FENDI-{period}-{number:03d}"

    def _check_cancel(self, parent_run_id: str | None) -> None:
        if self._cancel.is_set() or (parent_run_id and (self.state_dir / "control" / f"{parent_run_id}.cancel").exists()):
            raise InterruptedError("cancel_requested")

    def cancel(self, parent_run_id: str | None = None) -> None:
        self._cancel.set()
        if parent_run_id:
            marker = self.state_dir / "control" / f"{parent_run_id}.cancel"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch(exist_ok=True)

    def _statistical_fallback(self, statistical: dict[str, Any], period: str, version_number: int) -> dict[str, Any]:
        from services.ensemble_engine.engine import _bands

        total = next((row for row in statistical.get("series", [])
                      if row.get("series_id") == "total-fendi-bd" and row.get("target") == "Venta"
                      and row.get("status", "").startswith("completed")), None)
        if not total or len(total.get("forecast", [])) != 12:
            raise ValueError("statistical_forecast_unavailable")
        residuals = [float(row["actual"]) - float(row["forecast"])
                     for row in total.get("history", []) if row.get("forecast") is not None]
        forecast = []
        for horizon, row in enumerate(total["forecast"], 1):
            value = max(0.0, float(row["forecast"]))
            forecast.append({"series_id": "total-fendi-bd", "label": "Total FENDI BD",
                             "period": row["period"], "horizon": horizon, "value": value,
                             "statistical": value, "ml": None, "statistical_weight": 1.0,
                             "ml_weight": 0.0, "probability": _bands(value, residuals, horizon),
                             "confidence": "limited" if len(residuals) < 5 else "historical"})
        return {"version": f"FT-FENDI-{period.replace('-', '')}-V{version_number:02d}",
                "cutoff": period, "engine_version": "historical-statistical-fallback-1",
                "selection": {"status": "champion", "official": {"strategy": "statistical",
                              "wape": total.get("wape")}, "challenger": None,
                              "decision": "insufficient_evidence_or_ml_failure"},
                "forecast_towell": forecast, "alerts": [], "historical_only": True}

    def _evaluate(self, final: dict[str, Any], config: HistoricalRunConfig) -> dict[str, Any]:
        """Evaluate after freezing the vintage; never feed these actuals into models."""
        today = date.today().isoformat()
        by_month: dict[str, list[dict[str, str]]] = {}
        for raw in self._scope_rows(config):
            row = self.availability.resolve(raw)
            if row.get("objective", "").casefold() != "venta" or row.get("is_missing", "").casefold() == "true":
                continue
            available = _valid_date(row.get("available_at"))
            status = row.get("close_status", "").casefold()
            verified = row.get("availability_confidence") in {"verified", "documented"}
            closed = row.get("closure_status") == "CLOSED" or any(word in status for word in ("closed", "cerrado", "validado"))
            if available is None or available > today or not verified or not closed:
                continue
            by_month.setdefault(row.get("period", ""), []).append(row)
        evaluations = []
        for forecast in final["forecast_towell"]:
            target = forecast["period"]
            actual_rows = by_month.get(target, [])
            if not actual_rows or any(not row.get("value", "").strip() for row in actual_rows):
                evaluations.append({"period": target, "horizon": forecast["horizon"],
                                    "evaluation_status": "pending", "actual": None})
                continue
            actual = sum(float(row["value"]) for row in actual_rows)
            prediction = float(forecast["value"])
            bands = forecast.get("probability") or {}
            coverage = None
            if all(key in bands for key in ("p10", "p50", "p90", "p95")):
                coverage = ("below_p10" if actual < bands["p10"] else
                            "p10_p50" if actual < bands["p50"] else
                            "p50_p90" if actual < bands["p90"] else
                            "p90_p95" if actual < bands["p95"] else "above_p95")
            evaluations.append({"period": target, "horizon": forecast["horizon"],
                                "evaluation_status": "evaluated", "actual": round(actual, 4),
                                "forecast": prediction, "absolute_error": round(abs(actual - prediction), 4),
                                "signed_error": round(actual - prediction, 4), "coverage": coverage})
        return {"evaluated_at": _utc(), "rows": evaluations,
                "evaluated_count": sum(row["evaluation_status"] == "evaluated" for row in evaluations)}

    def _write_engine_input(self, snapshot: dict[str, Any], path: Path) -> None:
        rows = snapshot["rows"]
        fields = list(dict.fromkeys(field for row in rows for field in row))
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def run_month(self, period: str, *, parent_run_id: str | None = None,
                  force_rerun: bool = False, actor: str = "local-process",
                  config: HistoricalRunConfig | None = None,
                  cutoff_date: str | None = None) -> dict[str, Any]:
        config = config or HistoricalRunConfig(period, period)
        if period < config.start or period > config.end:
            raise ValueError("period_outside_config")
        cutoff = cutoff_date or _month_end(period)
        if _valid_date(cutoff) != cutoff or not f"{period}-01" <= cutoff <= _month_end(period):
            raise ValueError("invalid_historical_cutoff")
        if cutoff > date.today().isoformat():
            raise ValueError("future_historical_cutoff")
        self._check_cancel(parent_run_id)
        research_start = time.monotonic()
        try:
            research = self._research_snapshot(cutoff, config)
        except Exception as exc:
            # No data/model step is reached after a research failure.
            return self._failed_before_data(period, cutoff, parent_run_id, actor, "RESEARCH_FAILED", exc)
        research_duration = round(time.monotonic() - research_start, 4)
        self._check_cancel(parent_run_id)
        data_start = time.monotonic()
        snapshot = self._data_snapshot(period, cutoff, config)
        data_duration = round(time.monotonic() - data_start, 4)
        month_config = {"chain": config.chain, "pilot_scope": config.pilot_scope,
                        "forecast_horizon": config.forecast_horizon,
                        "allow_future_data": config.allow_future_data,
                        "random_seed": config.random_seed,
                        "missing_availability": config.missing_availability}
        fingerprint = _digest({"period": period, "cutoff": cutoff, "data_hash": snapshot["hash"],
                               "research_hash": research["hash"], "config": month_config,
                               "pipeline": type(self.pipeline).__name__,
                               "research_provider": type(self.research).__name__,
                               "engine_versions": self._engine_versions()})
        existing = self._existing_runs(period)
        if not force_rerun:
            completed = next((run for run in existing if run.get("fingerprint") == fingerprint and
                              run.get("status") in {"COMPLETED", "SKIPPED_INSUFFICIENT_HISTORY"}), None)
            if completed:
                return completed
            if existing and not all(run.get("status") == "SKIPPED_INSUFFICIENT_HISTORY"
                                    for run in existing):
                raise ValueError("existing_run_requires_force_rerun")
        run_id = self._next_child_id(period)
        path = self.state_dir / "runs" / f"{run_id}.json"
        run: dict[str, Any] = {"run_id": run_id, "parent_run_id": parent_run_id,
                               "period": period, "cutoff_date": cutoff, "actor": actor,
                               "status": "PENDING", "step": "PENDING", "events": [],
                               "fingerprint": fingerprint, "research_snapshot_id": research["snapshot_id"],
                               "research_hash": research["hash"], "data_snapshot_id": snapshot["snapshot_id"],
                               "data_hash": snapshot["hash"], "data_provider": self.provider.name,
                               "input_manifest_id": snapshot["input_manifest_id"],
                               "input_manifest_hash": snapshot["input_manifest_hash"],
                               "research_provider": research["provider"], "warnings": [], "errors": [],
                               "durations": {"research": research_duration, "data_prep": data_duration},
                               "model_requirements": self.MODEL_REQUIREMENTS,
                               "availability_gate_version": GATE_VERSION,
                                "training": {"random_seed": config.random_seed,
                                             "trained_until": None},
                               "versions": {}, "started_at": _utc()}
        started = time.monotonic()

        def stage(name: str) -> None:
            self._check_cancel(parent_run_id)
            run["step"] = name
            run["status"] = name
            run["events"].append({"step": name, "timestamp": _utc()})
            _atomic_json(path, run)
            self._log(run_id, period, name, name)

        stage("RESEARCH")
        stage("DATA_PREP")
        history = self._history(snapshot, period)
        run["history"] = history
        run["training"]["trained_until"] = history["latest_observed_period"]
        if not history["eligible"]:
            run["status"] = "SKIPPED_INSUFFICIENT_HISTORY"
            run["step"] = "SKIPPED_INSUFFICIENT_HISTORY"
            run["warnings"].append("no_verified_consecutive_history")
            run["finished_at"] = _utc()
            run["durations"]["total"] = round(time.monotonic() - started + research_duration + data_duration, 4)
            _atomic_json(path, run)
            self._persist_monthly_run(run)
            self._log(run_id, period, "finished", run["status"], run["durations"]["total"])
            return run
        try:
            with tempfile.TemporaryDirectory(prefix="fendi-historical-") as directory:
                normalized_csv = Path(directory) / "snapshot.csv"
                self._write_engine_input(snapshot, normalized_csv)
                stage("STATISTICAL")
                step_start = time.monotonic()
                statistical = self.pipeline.statistical(normalized_csv)
                run["durations"]["statistical"] = round(time.monotonic() - step_start, 4)
                if statistical.get("run", {}).get("cutoff", "") > period:
                    raise ValueError("future_statistical_model")
                statistical.get("run", {})["version"] = f"STAT-FENDI-{period.replace('-', '')}-{snapshot['hash'][:8]}"
                run["versions"]["statistical"] = statistical.get("run", {}).get("engine_version")
                stage("ML")
                step_start = time.monotonic()
                try:
                    ml = self.pipeline.ml(normalized_csv)
                    trained_until = ml.get("training", {}).get("trained_until") or ml.get("cutoff", "")
                    if str(trained_until)[:7] > period:
                        raise ValueError("future_ml_model")
                    ml.get("champion", {})["version"] = f"ML-FENDI-{period.replace('-', '')}-{snapshot['hash'][:8]}"
                    run["versions"]["ml"] = ml.get("engine_version")
                except Exception as exc:
                    ml = None
                    code = str(exc) if isinstance(exc, ValueError) and str(exc) == "future_ml_model" else type(exc).__name__
                    run["warnings"].append(f"ml_fallback:{code}")
                run["durations"]["ml"] = round(time.monotonic() - step_start, 4)
                stage("ENSEMBLE")
                step_start = time.monotonic()
                try:
                    method = getattr(self.pipeline, "ensemble_historical", self.pipeline.ensemble)
                    final = method(normalized_csv, statistical, ml)
                    if final.get("cutoff", "") > period or len(final.get("forecast_towell", [])) != 12:
                        raise ValueError("historical_ensemble_unavailable")
                    run["versions"]["ensemble"] = final.get("engine_version")
                except Exception as exc:
                    run["warnings"].append(f"ensemble_statistical_fallback:{type(exc).__name__}")
                    final = self._statistical_fallback(statistical, period,
                                                       int(run_id.rsplit("-", 1)[-1]))
                    run["versions"]["ensemble"] = final["engine_version"]
                run["durations"]["ensemble"] = round(time.monotonic() - step_start, 4)
            self._check_cancel(parent_run_id)
            stage("SAVE")
            final = dict(final)
            final["version"] = f"FT-FENDI-{period.replace('-', '')}-V{int(run_id.rsplit('-', 1)[-1]):02d}"
            final["historical_only"] = True
            final["research_snapshot_id"] = research["snapshot_id"]
            final["data_snapshot_id"] = snapshot["snapshot_id"]
            final["input_manifest_id"] = snapshot["input_manifest_id"]
            final["trained_until"] = history["latest_observed_period"]
            vintage = {"vintage_id": f"V-{run_id}", "issue_period": period,
                       "parent_run_id": parent_run_id, "forecast_version": final["version"],
                       "research_snapshot_id": research["snapshot_id"],
                       "data_snapshot_id": snapshot["snapshot_id"],
                       "input_manifest_id": snapshot["input_manifest_id"],
                       "input_manifest_hash": snapshot["input_manifest_hash"],
                       "cutoff_date": cutoff, "trained_until": history["latest_observed_period"],
                       "forecasts": final["forecast_towell"], "frozen": True}
            vintage["hash"] = _digest(vintage)
            _immutable_json(self.state_dir / "vintages" / f"{vintage['vintage_id']}.json", vintage)
            system = {"period": period, "cutoff_date": cutoff,
                      "champion": final.get("selection", {}).get("official"),
                      "challenger": final.get("selection", {}).get("challenger"),
                      "statistical_wape": next((row.get("wape") for row in statistical.get("series", [])
                                                if row.get("series_id") == "total-fendi-bd" and row.get("target") == "Venta"), None),
                      "ml_wape": ml.get("champion", {}).get("wape") if ml else None,
                      "bias": next((row.get("bias") for row in statistical.get("series", [])
                                    if row.get("series_id") == "total-fendi-bd" and row.get("target") == "Venta"), None),
                      "drift": ml.get("drift") if ml else None,
                      "versions": run["versions"], "configuration": asdict(config),
                      "research_snapshot_id": research["snapshot_id"]}
            system["hash"] = _digest(system)
            _immutable_json(self.state_dir / "system" / f"{run_id}.json", system)
            run["champion"] = system["champion"]
            run["forecast_version"] = final["version"]
            run["horizons"] = 12
            run["vintage_id"] = vintage["vintage_id"]
            run["vintage_hash"] = vintage["hash"]
            run["system_snapshot_hash"] = system["hash"]
            stage("EVALUATE")
            evaluation = self._evaluate(final, config)
            _atomic_json(self.state_dir / "evaluations" / f"{vintage['vintage_id']}.json", evaluation)
            run["evaluation"] = evaluation
            run["status"] = "COMPLETED"
            run["step"] = "COMPLETED"
        except InterruptedError:
            run["status"] = "CANCELLED"
            run["step"] = "CANCELLED"
        except Exception as exc:
            run["status"] = "FAILED"
            run["step"] = "FAILED"
            run["errors"].append({"step": run["events"][-1]["step"] if run["events"] else "PENDING",
                                  "code": str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
        run["finished_at"] = _utc()
        run["durations"]["total"] = round(time.monotonic() - started + research_duration + data_duration, 4)
        _atomic_json(path, run)
        self._persist_monthly_run(run)
        if run.get("status") == "COMPLETED":
            self.persistence.put("forecast_vintages", vintage["vintage_id"], vintage)
            self.persistence.put("model_versions", run_id, run.get("versions", {}))
            self.persistence.put("champion_history", run_id,
                                 {"run_id": run_id, "champion": run.get("champion"), "cutoff_date": cutoff})
            for horizon, row in enumerate(vintage["forecasts"], 1):
                self.persistence.put("forecast_horizons", f"{vintage['vintage_id']}-{horizon:02d}",
                                     {"vintage_id": vintage["vintage_id"], "horizon": horizon, "forecast": row})
                self.persistence.put("forecast_bands", f"{vintage['vintage_id']}-{horizon:02d}",
                                     {"vintage_id": vintage["vintage_id"], "horizon": horizon,
                                      "bands": row.get("probability", {})})
        self._log(run_id, period, "finished", run["status"], run["durations"]["total"],
                  warnings=run["warnings"], error=run["errors"][-1] if run["errors"] else None)
        return run

    def _failed_before_data(self, period: str, cutoff: str, parent_run_id: str | None,
                            actor: str, status: str, exc: Exception) -> dict[str, Any]:
        run_id = self._next_child_id(period)
        run = {"run_id": run_id, "parent_run_id": parent_run_id, "period": period,
               "cutoff_date": cutoff, "actor": actor, "status": status, "step": "RESEARCH",
               "errors": [{"step": "RESEARCH", "code": type(exc).__name__}],
               "started_at": _utc(), "finished_at": _utc()}
        _atomic_json(self.state_dir / "runs" / f"{run_id}.json", run)
        self._persist_monthly_run(run)
        self._log(run_id, period, "RESEARCH", status, error=type(exc).__name__)
        return run

    def evaluate_available_actuals(self, run_id: str) -> dict[str, Any]:
        """Refresh separate evaluation records without touching a frozen vintage."""
        run_path = self.state_dir / "runs" / f"{run_id}.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        if run.get("status") != "COMPLETED":
            raise ValueError("run_not_completed")
        vintage = json.loads((self.state_dir / "vintages" / f"{run['vintage_id']}.json").read_text(encoding="utf-8"))
        evaluation = self._evaluate({"forecast_towell": vintage["forecasts"]},
                                    HistoricalRunConfig(run["period"], run["period"]))
        _atomic_json(self.state_dir / "evaluations" / f"{run['vintage_id']}.json", evaluation)
        self.persistence.put("actual_evaluations", run["vintage_id"], evaluation)
        return evaluation

    def first_vintage(self, *, start: str = "2023-01", end: str = "2026-08",
                      actor: str = "local-process", force_rerun: bool = False) -> dict[str, Any]:
        """Run exactly one historically eligible vintage, not a full replay."""
        candidate = self.availability.find_first_temporally_valid_period(start, end)
        if candidate is None:
            report = {"first_real_vintage_validated": False, "status": "BLOCKED_AVAILABILITY",
                      "reason": "no_temporally_valid_period"}
            _atomic_json(self.state_dir / "first_vintage.json", report)
            return report
        self._log("FIRST-VINTAGE", candidate["period"], "first_valid_period_found", "READY",
                  cutoff=candidate["cutoff"])
        self._log("FIRST-VINTAGE", candidate["period"], "first_vintage_started", "RUNNING")
        run = self.run_month(candidate["period"], cutoff_date=candidate["cutoff"],
                             actor=actor, force_rerun=force_rerun)
        if run.get("status") != "COMPLETED":
            report = {"first_real_vintage_validated": False, "status": run.get("status"),
                      "run_id": run.get("run_id"), "errors": run.get("errors", [])}
            _atomic_json(self.state_dir / "first_vintage.json", report)
            return report
        data = json.loads((self.state_dir / "data" / f"{run['data_snapshot_id']}.json").read_text(encoding="utf-8"))
        research = json.loads((self.state_dir / "research" / f"{run['research_snapshot_id']}.json").read_text(encoding="utf-8"))
        vintage = json.loads((self.state_dir / "vintages" / f"{run['vintage_id']}.json").read_text(encoding="utf-8"))
        cutoff = candidate["cutoff"]
        data_leakage = sum((_valid_date(row.get("available_at")) or "9999-12-31") > cutoff
                           for row in data["rows"] + data["known_future"])
        research_leakage = sum((_valid_date(row.get("published_at")) or "9999-12-31") > cutoff
                               for row in research["sources"] + research["signals"])
        forecasts = vintage.get("forecasts", [])
        bands_ready = len(forecasts) == 12 and all(
            all(key in (forecast.get("probability") or {}) for key in ("p10", "p50", "p90", "p95"))
            for forecast in forecasts)
        valid = (data_leakage == research_leakage == 0 and bands_ready and vintage.get("frozen") is True
                 and bool(vintage.get("input_manifest_id")))
        report = {"first_real_vintage_validated": valid, "status": "PASS" if valid else "FAIL",
                  "period": candidate["period"], "cutoff": cutoff, "run_id": run["run_id"],
                  "vintage_id": run["vintage_id"], "vintage_hash": run["vintage_hash"],
                  "input_manifest_id": run["input_manifest_id"],
                  "research_snapshot_id": run["research_snapshot_id"],
                  "data_snapshot_id": run["data_snapshot_id"],
                  "data_leakage": data_leakage, "research_leakage": research_leakage,
                  "forecast_horizons": len(forecasts), "probability_bands_ready": bands_ready}
        registered = self.vintage_registry.get(run["vintage_id"])
        if valid and registered and registered.get("status") == "FROZEN":
            report.update(status=registered["validation_status"], registry_status="FROZEN",
                          evidence_level=registered["evidence_level"],
                          evidence_manifest_id=registered["evidence_manifest_id"])
        _atomic_json(self.state_dir / "first_vintage.json", report)
        self._log("FIRST-VINTAGE", candidate["period"], "first_vintage_completed", report["status"])
        return report

    def _next_parent_id(self) -> str:
        jobs = (self.state_dir / "jobs").glob("HRUN-FENDI-*.json")
        number = max((int(path.stem.rsplit("-", 1)[-1]) for path in jobs), default=0) + 1
        return f"HRUN-FENDI-{number:03d}"

    def _summary(self, job: dict[str, Any]) -> dict[str, Any]:
        child_runs = [json.loads((self.state_dir / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
                      for run_id in job["children"].values()]
        completed = [run for run in child_runs if run["status"] == "COMPLETED"]
        skipped = [run for run in child_runs if run["status"] == "SKIPPED_INSUFFICIENT_HISTORY"]
        failed = [run for run in child_runs if run["status"] in {"FAILED", "RESEARCH_FAILED"}]
        evaluated = []
        for run in completed:
            path = self.state_dir / "evaluations" / f"{run['vintage_id']}.json"
            if path.exists():
                evaluated.extend(row for row in json.loads(path.read_text(encoding="utf-8"))["rows"]
                                 if row["evaluation_status"] == "evaluated")

        def metrics(rows: list[dict[str, Any]]) -> dict[str, float | int | None]:
            denominator = sum(float(row["actual"]) for row in rows)
            return {"observations": len(rows),
                    "wape": round(100 * sum(float(row["absolute_error"]) for row in rows) / denominator, 4)
                    if denominator else None,
                    "bias": round(100 * sum(float(row["signed_error"]) for row in rows) / denominator, 4)
                    if denominator else None}

        raw_rows = [row for row in self._scope_rows(HistoricalRunConfig(**job["config"]))
                    if row.get("objective", "").casefold() == "venta" and row.get("is_missing", "").casefold() != "true"]
        raw_periods = sorted({row.get("period", "") for row in raw_rows})
        raw_totals: dict[str, float] = {}
        for row in raw_rows:
            try:
                raw_totals[row["period"]] = raw_totals.get(row["period"], 0.0) + float(row["value"])
            except (ValueError, KeyError):
                continue
        first_nonzero = next((period for period in sorted(raw_totals) if raw_totals[period] > 0), None)
        theoretical = None
        if first_nonzero:
            candidate = first_nonzero
            for _ in range(self.MODEL_REQUIREMENTS["statistical"]["minimum_history_required"] - 1):
                candidate = _next_period(candidate)
            if all(period in raw_totals for period in _period_range(first_nonzero, candidate)):
                theoretical = candidate
        return {"periods_requested": len(_period_range(job["config"]["start"], job["config"]["end"])),
                "periods_executed": len(completed), "periods_skipped": len(skipped),
                "periods_failed": len(failed), "earliest_source_period": raw_periods[0] if raw_periods else None,
                "first_nonzero_source_period": first_nonzero,
                "earliest_theoretical_period_unverified": theoretical,
                "earliest_forecastable_period": completed[0]["period"] if completed else None,
                "first_forecast": completed[0]["period"] if completed else None,
                "last_forecast": completed[-1]["period"] if completed else None,
                "vintage_count": len(completed), "horizon_count": sum(run.get("horizons", 0) for run in completed),
                "champion_by_period": {run["period"]: run.get("champion", {}).get("strategy") for run in completed},
                "aggregate": metrics(evaluated),
                "by_horizon": {str(horizon): metrics([row for row in evaluated if row["horizon"] == horizon])
                               for horizon in range(1, 13)},
                "coverage": {name: sum(row.get("coverage") == name for row in evaluated)
                             for name in ("below_p10", "p10_p50", "p50_p90", "p90_p95", "above_p95")},
                "errors": [error for run in failed for error in run.get("errors", [])]}

    def _execute_job(self, job: dict[str, Any], *, force_rerun: bool = False,
                     resume_from: str | None = None) -> dict[str, Any]:
        parent_id = job["run_id"]
        path = self.state_dir / "jobs" / f"{parent_id}.json"
        config = HistoricalRunConfig(**job["config"])
        periods = _period_range(config.start, config.end)
        self._cancel.clear()
        job["status"] = "RUNNING"
        job["started_at"] = job.get("started_at") or _utc()
        _atomic_json(path, job)
        self.persistence.put("historical_runs", parent_id, job)
        self._log(parent_id, None, "job", "RUNNING")
        for period in periods:
            if resume_from and period < resume_from:
                continue
            if (self.state_dir / "control" / f"{parent_id}.pause").exists():
                job["status"] = "PAUSED"
                break
            if self._cancel.is_set() or (self.state_dir / "control" / f"{parent_id}.cancel").exists():
                job["status"] = "CANCELLED"
                break
            previous_id = job["children"].get(period)
            if previous_id:
                previous = json.loads((self.state_dir / "runs" / f"{previous_id}.json").read_text(encoding="utf-8"))
                if previous["status"] in {"COMPLETED", "SKIPPED_INSUFFICIENT_HISTORY"}:
                    continue
            try:
                child = self.run_month(period, parent_run_id=parent_id,
                                       force_rerun=force_rerun or bool(previous_id), config=config,
                                       actor=job["actor"])
            except InterruptedError:
                job["status"] = "CANCELLED"
                break
            except Exception as exc:
                job["status"] = "FAILED"
                job["errors"].append({"period": period, "code": str(exc) if isinstance(exc, ValueError)
                                      else type(exc).__name__})
                break
            job["children"][period] = child["run_id"]
            if child["run_id"] not in job["child_run_ids"]:
                job["child_run_ids"].append(child["run_id"])
            job["progress"] = {"periods_completed": len([value for value in job["children"].values()
                                                            if value]), "periods_total": len(periods)}
            _atomic_json(path, job)
            self.persistence.put("historical_runs", parent_id, job)
            if child["status"] == "CANCELLED":
                job["status"] = "CANCELLED"
                break
            if child["status"] in {"FAILED", "RESEARCH_FAILED"} and config.stop_on_error:
                job["status"] = "FAILED"
                break
        else:
            job["status"] = "COMPLETED"
        job["summary"] = self._summary(job)
        if job["status"] == "COMPLETED" and (job["summary"]["periods_skipped"] or
                                                job["summary"]["periods_failed"]):
            job["status"] = "COMPLETED_WITH_WARNINGS"
        job["finished_at"] = _utc()
        _atomic_json(path, job)
        self.persistence.put("historical_runs", parent_id, job)
        self._log(parent_id, None, "job", job["status"], summary=job["summary"])
        return job

    def run_range(self, start_period: str, end_period: str, *, stop_on_error: bool = True,
                  force_rerun: bool = False, actor: str = "local-process") -> dict[str, Any]:
        config = HistoricalRunConfig(start_period, end_period, stop_on_error=stop_on_error)
        input_fingerprint = self._inputs_fingerprint(config)
        signature = _digest({"config": asdict(config), "provider": self.provider.name,
                             "pipeline": type(self.pipeline).__name__,
                             "research": type(self.research).__name__})
        for path in sorted((self.state_dir / "jobs").glob("HRUN-FENDI-*.json")):
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("config_hash") == signature and not force_rerun:
                if existing["status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}:
                    if input_fingerprint is not None and existing.get("input_fingerprint") == input_fingerprint:
                        return existing
                    raise ValueError("historical_inputs_changed_requires_force_rerun")
                raise ValueError("existing_job_requires_resume")
        parent_id = self._next_parent_id()
        job = {"run_id": parent_id, "status": "QUEUED", "actor": actor,
               "config": asdict(config), "config_hash": signature,
               "input_fingerprint": input_fingerprint, "engine_versions": self._engine_versions(),
               "provider": self.provider.name, "research_provider": type(self.research).__name__,
               "pipeline": type(self.pipeline).__name__, "children": {}, "child_run_ids": [],
               "progress": {"periods_completed": 0, "periods_total": len(_period_range(start_period, end_period))},
               "errors": [], "queued_at": _utc()}
        _atomic_json(self.state_dir / "jobs" / f"{parent_id}.json", job)
        self._log(parent_id, None, "job", "QUEUED")
        job["status"] = "INITIALIZING"
        _atomic_json(self.state_dir / "jobs" / f"{parent_id}.json", job)
        return self._execute_job(job, force_rerun=force_rerun)

    async def run_range_async(self, start_period: str, end_period: str, **kwargs: Any) -> dict[str, Any]:
        return await asyncio.to_thread(self.run_range, start_period, end_period, **kwargs)

    def resume(self, parent_run_id: str, *, resume_from: str | None = None) -> dict[str, Any]:
        path = self.state_dir / "jobs" / f"{parent_run_id}.json"
        job = json.loads(path.read_text(encoding="utf-8"))
        if resume_from and resume_from not in _period_range(job["config"]["start"], job["config"]["end"]):
            raise ValueError("resume_period_outside_range")
        if job["status"] not in {"PAUSED", "FAILED", "CANCELLED", "COMPLETED_WITH_WARNINGS"}:
            raise ValueError("job_not_resumable")
        for suffix in ("pause", "cancel"):
            (self.state_dir / "control" / f"{parent_run_id}.{suffix}").unlink(missing_ok=True)
        self._cancel.clear()
        job["errors"] = []
        return self._execute_job(job, force_rerun=False, resume_from=resume_from)

    def pause(self, parent_run_id: str) -> None:
        marker = self.state_dir / "control" / f"{parent_run_id}.pause"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
