"""E3 server-only, deterministic preview-to-vintage transaction boundary."""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx

from services.forecast_engine.engine import add_month
from .persistence import content_hash
from .quality_gates import QualityPolicy, data_quality, forecast_quality, observed_service
from .supabase_access import PreviewError


def _amount(value: Any) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        raise PreviewError("LINEAGE_MISMATCH", 409) from None
    if not number.is_finite() or number < 0:
        raise PreviewError("LINEAGE_MISMATCH", 409)
    return str(number.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def _uuid(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise PreviewError("LINEAGE_MISMATCH", 409) from None


def candidate_payload(preview: dict[str, Any], sales: list[dict[str, Any]],
                      service_rows: list[dict[str, Any]], *, actor_id: str,
                      git_sha: str, policy: QualityPolicy = QualityPolicy(),
                      preview_id: str) -> dict[str, Any]:
    if preview.get("status") != "PREVIEW" or preview.get("objective") != "Venta" or not preview.get("products"):
        raise PreviewError("PREVIEW_NOT_READY", 409)
    if any(row.get("chain_id") != preview.get("chain_id") or row.get("period", "")[:7] > preview["issue_period"]
           for row in sales):
        raise PreviewError("LINEAGE_MISMATCH", 409)
    from .operational_preview import snapshot_hash
    if snapshot_hash(sales) != preview.get("dataset_hash"):
        raise PreviewError("HASH_MISMATCH", 409)
    dq = data_quality(sales, preview, policy)
    fq = forecast_quality(preview, policy)
    sl = observed_service(service_rows, policy)
    if dq["status"] in {"BLOCKED", "IDENTITY_CONFLICT"}:
        raise PreviewError("DATA_QUALITY_BLOCKED", 409)
    horizons: list[dict[str, Any]] = []
    for product in preview["products"]:
        source = product.get("horizons") or []
        if not source:
            continue
        if len(source) != 12 or {row.get("horizon") for row in source} != set(range(1, 13)):
            raise PreviewError("VINTAGE_NOT_FREEZABLE", 409)
        for row in source:
            if row.get("target_period") != add_month(preview["issue_period"], row["horizon"]):
                raise PreviewError("LINEAGE_MISMATCH", 409)
            band = [row.get(name) for name in ("p10", "p50", "p90", "p95")]
            complete_band = all(value is not None for value in band)
            if any(value is not None for value in band) and not complete_band and any(
                    row.get(name) is not None for name in ("p10", "p90", "p95")):
                raise PreviewError("LINEAGE_MISMATCH", 409)
            entry = {"product_id": _uuid(product["product_id"]),
                "category_id": _uuid(product["category_id"]) if product.get("category_id") else None,
                "horizon": row["horizon"], "target_period": row["target_period"] + "-01",
                "statistical_value": _amount(row.get("statistical_value")),
                "ml_value": _amount(row.get("ml_value")),
                "ensemble_value": _amount(row.get("ensemble_value")),
                "forecast_towell": _amount(row["forecast_towell"]),
                "model_strategy": row.get("model_strategy") or "ENSEMBLE",
                "confidence": row.get("confidence"),
                "band_basis": row.get("band_basis") if complete_band else None,
                "band_observations": row.get("band_observations") if complete_band else None,
                "forecast_status": product.get("forecast_status") or "ACTIVE"}
            for name in ("p10", "p50", "p90", "p95"):
                entry[name] = _amount(row[name]) if complete_band else None
            if complete_band and (not entry["band_basis"] or not entry["band_observations"] or
                                  [Decimal(entry[name]) for name in ("p10", "p50", "p90", "p95")] !=
                                  sorted(Decimal(entry[name]) for name in ("p10", "p50", "p90", "p95"))):
                raise PreviewError("LINEAGE_MISMATCH", 409)
            horizons.append(entry)
    if not horizons:
        raise PreviewError("PREVIEW_NOT_READY", 409)
    horizons.sort(key=lambda row: (row["product_id"], row["horizon"]))
    totals: dict[tuple[str, str | None, int], Decimal] = defaultdict(Decimal)
    for row in horizons:
        totals[("CHAIN", None, row["horizon"])] += Decimal(row["forecast_towell"])
        if row["category_id"]:
            totals[("CATEGORY", row["category_id"], row["horizon"])] += Decimal(row["forecast_towell"])
    aggregates = [{"level": level, "category_id": category, "horizon": horizon,
        "target_period": add_month(preview["issue_period"], horizon) + "-01",
        "forecast_towell": str(total), "p10": None, "p50": None, "p90": None, "p95": None}
        for (level, category, horizon), total in sorted(totals.items(), key=lambda pair: (pair[0][0], pair[0][1] or "", pair[0][2]))]
    inputs = sorted({(_uuid(row.get("source_id")), None) for row in sales})
    if not inputs or len(inputs) != len(sales):
        raise PreviewError("LINEAGE_MISMATCH", 409)
    model_rows: dict[tuple[str, str], dict[str, Any]] = {}
    source_horizons = {(product["product_id"], row["horizon"]): row
        for product in preview["products"] for row in product.get("horizons") or []}
    for family, block in (("STATISTICAL", preview.get("statistical") or {}), ("ML", preview.get("ml") or {})):
        candidates = block.get("scope_candidates") if family == "STATISTICAL" else block.get("candidates")
        for candidate in candidates or []:
            algorithm = candidate.get("model")
            if isinstance(algorithm, str) and algorithm:
                model_rows[(family, algorithm)] = {"family": family, "algorithm": algorithm,
                    "validation_wape": candidate.get("retrospective_validation_wape",
                        candidate.get("validation_wape")), "parameters": {}}
    for row in horizons:
        source = source_horizons.get((row["product_id"], row["horizon"]))
        if source:
            for family, field in (("STATISTICAL", "statistical_model"), ("ML", "ml_model")):
                algorithm = source.get(field)
                if algorithm:
                    model_rows.setdefault((family, algorithm), {"family": family, "algorithm": algorithm,
                        "validation_wape": None, "parameters": {}})
    strategy = ((preview.get("selection") or {}).get("preview_leader") or {}).get("strategy")
    if strategy:
        model_rows[("ENSEMBLE", strategy)] = {"family": "ENSEMBLE", "algorithm": strategy,
            "validation_wape": fq["candidate_wape"], "parameters": {}}
    metrics = []
    leader = (preview.get("selection") or {}).get("preview_leader") or {}
    for name, keys in (("WAPE", ("retrospective_wape", "validation_wape")),
                       ("BIAS", ("retrospective_bias", "validation_bias")),
                       ("MAE", ("retrospective_mae", "validation_mae")),
                       ("RMSE", ("retrospective_rmse", "validation_rmse"))):
        value = next((leader[key] for key in keys if leader.get(key) is not None), None)
        if value is not None:
            metrics.append({"metric": name, "value": value})
    gates = [{"gate_type": item["gate_type"], "status": item["status"],
        "score": None, "target": item.get("target_fill_rate"),
        "observed": item.get("observed_fill_rate"), "details": item}
        for item in (dq, fq, sl)]
    content = {"chain_id": preview["chain_id"], "objective": preview["objective"],
        "issue_period": preview["issue_period"], "cutoff": preview["cutoff"],
        "horizons": horizons, "engine_version": preview["engine_version"],
        "data_snapshot_hash": preview["dataset_hash"], "policy_version": policy.version}
    digest = content_hash(content)
    return {"chain_id": _uuid(preview["chain_id"]), "objective": preview["objective"],
        "issue_period": preview["issue_period"] + "-01",
        "cutoff_at": preview["cutoff"] + "T23:59:59Z", "actor_id": _uuid(actor_id),
        "engine_version": preview["engine_version"], "git_sha": git_sha,
        "data_snapshot_hash": preview["dataset_hash"], "policy_version": policy.version,
        "content_hash": digest, "preview_id": preview_id,
        "certification_status": "PROVISIONAL", "evidence_mode": preview["mode"],
        "horizons": horizons, "aggregates": aggregates,
        "inputs": [{"observation_id": observation_id, "source_evidence_id": evidence_id}
            for observation_id, evidence_id in inputs],
        "models": [model_rows[key] for key in sorted(model_rows)], "metrics": metrics, "gates": gates}


class SupabaseForecastWriteRepository:
    """Service-role REST RPC only. Credentials are never exposed or logged."""

    def __init__(self, url: str, service_key: str, *, timeout: int = 30,
                 transport: httpx.BaseTransport | None = None):
        self.url, self.__key, self.timeout, self.transport = url.rstrip("/"), service_key, timeout, transport

    def __repr__(self) -> str:
        return "SupabaseForecastWriteRepository(credentials redacted)"

    def rpc(self, name: str, payload: dict[str, Any]) -> str | dict[str, Any]:
        if not self.__key:
            raise PreviewError("REQUIRED_SECRET_MISSING", 503)
        if not re.fullmatch(r"e3_(create_vintage_candidate|freeze_vintage|publish_official|promote_champion|close_target_period)", name):
            raise PreviewError("REQUEST_001", 400)
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport, follow_redirects=False) as client:
                response = client.post(f"{self.url}/rest/v1/rpc/{name}", json=payload,
                    headers={"apikey": self.__key, "Authorization": f"Bearer {self.__key}"})
            if response.status_code not in (200, 201):
                raise PreviewError("VINTAGE_WRITE_FAILED", 503)
            result = response.json()
            if name == "e3_close_target_period":
                if (not isinstance(result, dict) or result.get("status") not in {"CLOSED", "ALREADY_CLOSED"}
                        or result.get("vintage_id") != payload.get("p_vintage_id")
                        or result.get("period") != payload.get("p_target_period")):
                    raise PreviewError("VINTAGE_WRITE_FAILED", 503)
                return result
            return _uuid(result)
        except (httpx.HTTPError, ValueError):
            raise PreviewError("VINTAGE_WRITE_FAILED", 503) from None
