"""Read-only, twelve-month ORDER revisions over a frozen authorized corpus.

Targets and comparables are separate. The caller owns authorization, immutable
storage and explicit Champion acceptance; this module performs none of them.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import copy
import uuid

from services.assistant_api.cold_start import ColdStartPolicy, launch_forecast
from services.forecast_engine.engine import (
    ENGINE_VERSION,
    ForecastPolicy,
    RetrospectiveProductSeries,
    add_month,
    _metrics,
    month_end,
    normalize_retrospective_dataset,
)
from services.forecast_engine.retrospective import forecast_chain
from .planning_engine import (
    FAMILY_KEYS,
    GROUP_FIELDS,
    _candidate,
    aggregate_models,
    canonical,
    digest,
)

OPERATION_VERSION = "ORDER-ROLLING-12M-1.0.0"


class OperationalOrderSeries(RetrospectiveProductSeries):
    """A numeric zero remains evidence; statistical status is not delisting."""

    catalog_status = "UNKNOWN"

    def history(self, issue_period):
        result = []
        period = issue_period
        while (
            point := self.observations.get(period)
        ) is not None and point.value is not None:
            result.append(point.value)
            period = add_month(period, -1)
        return list(reversed(result))

    def lifecycle(self, cutoff, policy):
        return self.catalog_status


def _series(rows, cutoff, metadata):
    result = normalize_retrospective_dataset(rows, cutoff, objective="Pedido")
    for item in result:
        item.__class__ = OperationalOrderSeries
        item.catalog_status = metadata[item.product_id].get("status") or "UNKNOWN"
    return result


def _validated(
    rows, metadata, context, model_cutoff, observed_cutoff, observed_records
):
    if (
        context.get("objective") != "Pedido"
        or context.get("target_metric") != "ORDER"
        or not context.get("source_hash")
        or not context.get("generation")
        or not context.get("target_file_sha256")
        or context.get("forecast_available") is not True
        or context.get("forecast_block_code")
    ):
        raise ValueError("ROLLING_ORDER_SOURCE_UNACCREDITED")
    month_end(model_cutoff)
    month_end(observed_cutoff)
    if observed_cutoff > model_cutoff:
        raise ValueError("ROLLING_OBSERVED_CUTOFF_AFTER_MODEL_CUTOFF")
    values = list(metadata.values()) if isinstance(metadata, dict) else list(metadata)
    by_id = {item["product_id"]: dict(item) for item in values}
    if len(by_id) != len(values) or any(not item.get("chain_id") for item in values):
        raise ValueError("ROLLING_METADATA_IDENTITY_INVALID")
    authoritative = {}
    originals = (
        observed_records
        if observed_records is not None
        else [row for row in rows if row.get("basis", "OBSERVED") == "OBSERVED"]
    )
    for row in originals:
        if row["period"] > observed_cutoff:
            continue
        key = row["chain_id"], row["product_id"], row["period"]
        if key in authoritative:
            raise ValueError("ROLLING_DUPLICATE_OBSERVATION")
        if row.get("basis", "OBSERVED") != "OBSERVED":
            raise ValueError("ROLLING_OBSERVED_DTO_INVALID")
        if (
            row.get("objective") != "Pedido"
            or row.get("metric_code") != "ORDER"
            or row.get("source_file_sha256") != context["target_file_sha256"]
            or not row.get("source_reference")
        ):
            raise ValueError("ROLLING_OBSERVED_DTO_INVALID")
        authoritative[key] = row
    selected, keys, represented = [], set(), set()
    for original in rows:
        row = dict(original)
        if row["period"] > model_cutoff:
            continue
        basis = row.get("basis", "OBSERVED")
        key = row["chain_id"], row["product_id"], row["period"]
        meta = by_id.get(row["product_id"])
        amount = Decimal(str(row["value"])) if row.get("value") is not None else None
        if (
            meta is None
            or row["chain_id"] != meta["chain_id"]
            or row.get("objective") != "Pedido"
            or row.get("metric_code") != "ORDER"
            or row.get("source_file_sha256") != context["target_file_sha256"]
            or not row.get("source_reference")
            or basis not in {"OBSERVED", "COMMERCIAL_CLOSE"}
            or amount is not None
            and (not amount.is_finite() or amount < 0)
        ):
            raise ValueError("ROLLING_ORDER_INPUT_UNACCREDITED")
        month_end(row["period"])
        if key in keys:
            raise ValueError("ROLLING_DUPLICATE_OBSERVATION")
        if basis == "OBSERVED":
            if row["period"] > observed_cutoff:
                # A partial current month stays in historical consultation,
                # never in fit/evaluation or silently promoted to a closure.
                continue
            source = authoritative.get(key)
            if (
                source is None
                or source.get("value") != row.get("value")
                or source.get("source_reference") != row.get("source_reference")
            ):
                raise ValueError("ROLLING_OBSERVED_LINK_INVALID")
            represented.add(key)
        elif not context.get("scenario_hash") or key in authoritative:
            raise ValueError("ROLLING_COMMERCIAL_CANNOT_REPLACE_OBSERVED")
        keys.add(key)
        selected.append({**row, "basis": basis})
    if represented != set(authoritative):
        raise ValueError("ROLLING_OBSERVED_MEMBERSHIP_CHANGED")
    selected.sort(key=lambda row: (row["chain_id"], row["product_id"], row["period"]))
    observed = [row for row in selected if row["basis"] == "OBSERVED"]
    return selected, observed, by_id


def _same_peer_scope(target, peer):
    if target["chain_id"] != peer["chain_id"]:
        return False
    # Exact accredited metadata only; descriptions never create membership.
    for field in ("model", "presentation", "format", "account"):
        left = " ".join(str(target.get(field) or "").split()).casefold()
        right = " ".join(str(peer.get(field) or "").split()).casefold()
        if target.get(field) is not None and left != right:
            return False
    return bool(target.get("model") and target.get("presentation"))


def _empty_cold_start(cutoff, reason):
    return {
        "family": "COLD_START",
        "model": None,
        "origin": "INTERNAL_PEER_ORDER",
        "eligible": False,
        "calculable": False,
        "status": "SIN_COBERTURA",
        "reason": reason,
        "metrics": {"wape": None, "bias": None},
        "target_wape": None,
        "evaluation_target_basis": "OBSERVED_ONLY",
        "admission": {"accepted": False},
        "horizons": [
            {
                "horizon": h,
                "planning_horizon": h,
                "forecast_distance": h,
                "target_period": add_month(cutoff, h),
                "value": None,
                "status": "SIN_COBERTURA",
            }
            for h in range(1, 13)
        ],
    }


def _scope_comparison(products, paired):
    """Three methods, identical actual keys; never an average of percentages."""
    expected = {product["product_id"] for product in products}
    by_id = {product["product_id"]: product for product in products}
    common = []
    for row in paired:
        pid, horizon = row["product_id"], row["horizon"]
        product = by_id[pid]
        stat = row["statistical"].get(row.get("selected_statistical_model"))
        ml = row["ml"].get(row.get("selected_ml_model"))
        candidate = product["candidates"]["ENSEMBLE"]
        point = next((p for p in candidate["horizons"] if p["horizon"] == horizon), {})
        weight = point.get("statistical_weight")
        component = row["ml"].get(point.get("ensemble_ml_model"))
        ensemble = (
            stat
            if weight == 1
            else stat * weight + component * (1 - weight)
            if weight is not None and stat is not None and component is not None
            else None
        )
        if stat is None or ml is None or ensemble is None:
            continue
        common.append(
            {
                "product_id": pid,
                "chain_id": row.get("chain_id"),
                "origin": row["origin"],
                "target_period": row["target_period"],
                "horizon": horizon,
                "actual": row["actual"],
                "STATISTICAL": stat,
                "ML": ml,
                "ENSEMBLE": ensemble,
            }
        )
    covered = {row["product_id"] for row in common}
    metrics = {
        family: {
            **_metrics([(row["actual"], row[family]) for row in common]),
            "observations": len(common),
            "windows": len({row["origin"] for row in common}),
        }
        for family in FAMILY_KEYS.values()
    }
    complete = covered == expected and bool(expected)
    eligible = [
        family for family, values in metrics.items() if values["wape"] is not None
    ]
    leader = (
        min(
            eligible,
            key=lambda family: (
                metrics[family]["wape"],
                abs(metrics[family]["bias"]),
                family,
            ),
        )
        if eligible
        else None
    )
    leader = leader if complete and len(eligible) == 3 else None
    return {
        "basis": "IDENTICAL_OBSERVED_PRODUCT_ORIGIN_TARGET_HORIZON_PAIRS",
        "bias_convention": "OBSERVED_MINUS_FORECAST",
        "complete_scope": complete,
        "expected_product_ids": sorted(expected),
        "evaluated_product_ids": sorted(covered),
        "missing_product_ids": sorted(expected - covered),
        "metrics": metrics,
        "scope_leader": leader,
        "automatic_adoption": False,
        "pairs": common,
    }


def calculate_rolling_revision(
    rows,
    metadata,
    source_context,
    *,
    requested_product_ids=None,
    observed_records=None,
    policy=None,
    cold_start_policy=None,
    model_cutoff=None,
    observed_cutoff=None,
    run_id=None,
    emitted_at=None,
    on_stage=None,
):
    """Calculate once; never load G6 as the result or recalculate on filtering.

    ``rows`` and ``metadata`` are the full authorized comparable universe.
    ``requested_product_ids`` limits output only. The source pin is unchanged;
    the new run carries its own calendar and observed/commercial separation.
    No network, persistence, Champion selection or inventory input is used.
    """
    rows = list(rows)
    context = copy.deepcopy(source_context)
    cutoff = (
        model_cutoff or context.get("model_cutoff") or context.get("training_cutoff")
    )
    real_cutoff = observed_cutoff or context.get("observed_cutoff") or cutoff
    if not cutoff or not real_cutoff:
        raise ValueError("ROLLING_CLOSURE_UNACCREDITED")
    fit, observed, by_id = _validated(
        rows, metadata, context, cutoff, real_cutoff, observed_records
    )
    requested = (
        set(requested_product_ids) if requested_product_ids is not None else set(by_id)
    )
    if not requested or not requested.issubset(by_id):
        raise ValueError("ROLLING_REQUESTED_PRODUCT_UNAUTHORIZED")
    policy = policy or ForecastPolicy()
    cold_start_policy = cold_start_policy or ColdStartPolicy()
    current = _series(fit, cutoff, by_id)
    real = _series(observed, real_cutoff, by_id)
    current_chains, real_chains = defaultdict(list), defaultdict(list)
    for item in current:
        current_chains[item.chain_id].append(item)
    for item in real:
        real_chains[item.chain_id].append(item)
    selected_chains = {by_id[pid]["chain_id"] for pid in requested}
    products, paired, audits = [], [], []
    empty = {"model_audit": {"product_candidates": []}}
    for chain in sorted(selected_chains):
        scope = current_chains[chain]
        raw = (
            forecast_chain(
                scope,
                cutoff,
                policy,
                None,
                horizons=tuple(range(1, 13)),
                evaluation_horizons=tuple(range(1, 13)),
                evaluation_products=real_chains[chain],
                observed_cutoff=real_cutoff,
                retain_pairs=True,
                on_stage=on_stage,
            )
            if scope
            else empty
        )
        pairs = raw.get("paired_evidence", [])
        if any(point["target_period"] > real_cutoff for point in pairs):
            raise ValueError("ROLLING_COMMERCIAL_ACTUAL_LEAKAGE")
        paired.extend(point for point in pairs if point["product_id"] in requested)
        audits.append(
            {
                "chain_id": chain,
                "comparable_product_ids": sorted(item.product_id for item in scope),
                "model_audit": raw.get("model_audit", {}),
                "selection": raw.get("selection", {}),
            }
        )
        chain_rows = [row for row in fit if row["chain_id"] == chain]
        chain_observed = [row for row in observed if row["chain_id"] == chain]
        # Cold Start evaluation must not reinterpret commercial rows as actuals.
        cold_rows = [{**row, "basis": row["basis"]} for row in chain_rows]
        for pid in sorted(
            requested & {p for p, m in by_id.items() if m["chain_id"] == chain}
        ):
            meta = by_id[pid]
            forecasts = [
                point
                for point in raw.get("forecast_towell", [])
                if point["product_id"] == pid
            ]
            candidates = {
                FAMILY_KEYS[family]: _candidate(
                    raw,
                    family,
                    pid,
                    forecasts,
                    pairs,
                    distances=tuple(range(1, 13)),
                    cutoff=cutoff,
                )
                for family in FAMILY_KEYS
            }
            # Individual ML exists only with its own validated saved candidate,
            # never by relabelling the component used by a global Ensemble.
            own_ml = any(
                c["product_id"] == pid and c["family"] == "ml"
                for c in raw.get("model_audit", {}).get("product_candidates", [])
            )
            if not own_ml:
                candidates["ML"].update(
                    model=None,
                    eligible=False,
                    calculable=False,
                    status="SIN_COBERTURA",
                    reason="NO_VALIDATED_INDIVIDUAL_ML_CANDIDATE",
                )
                for point in candidates["ML"]["horizons"]:
                    point.update(
                        value=None,
                        status="SIN_COBERTURA",
                        reason="NO_VALIDATED_INDIVIDUAL_ML_CANDIDATE",
                    )
            for candidate in candidates.values():
                candidate.update(
                    evaluation_target_basis="OBSERVED_ONLY",
                    observed_cutoff=real_cutoff,
                    calculable=all(
                        point["value"] is not None for point in candidate["horizons"]
                    ),
                )
            cold = _empty_cold_start(cutoff, "REGULAR_CURVE_AVAILABLE")
            if not candidates["STATISTICAL"]["calculable"]:
                peers = {
                    other
                    for other, peer in by_id.items()
                    if other != pid and _same_peer_scope(meta, peer)
                }
                category = next(
                    (row["category"] for row in chain_rows if row["product_id"] == pid),
                    meta.get("engine_category")
                    or meta.get("product_type")
                    or meta.get("category_id"),
                )
                target = {
                    **meta,
                    "id": pid,
                    "category_id": category,
                    "description": meta.get("description")
                    or " ".join(
                        str(meta.get(k) or "")
                        for k in ("model", "presentation", "color")
                    ),
                    "objective": "Pedido",
                }
                proposal = launch_forecast(
                    cold_rows,
                    cutoff,
                    target,
                    cold_start_policy,
                    objective="Pedido",
                    observed_cutoff=real_cutoff,
                    comparable_product_ids=peers,
                )
                cold = _empty_cold_start(cutoff, "INSUFFICIENT_ACCREDITED_COMPARABLES")
                if proposal is not None:
                    proposal["comparables"] = [
                        {
                            **peer,
                            **{
                                key: by_id[peer["product_id"]].get(key)
                                for key in (
                                    "upc",
                                    "account",
                                    "format",
                                    "model",
                                    "presentation",
                                    "color",
                                )
                            },
                        }
                        for peer in proposal["comparables"]
                    ]
                    cold.update(
                        proposal,
                        family="COLD_START",
                        origin="INTERNAL_PEER_ORDER",
                        calculable=True,
                        eligible=False,
                        reason="EXPLICIT_COLD_START_ACCEPTANCE_REQUIRED",
                        metrics={"wape": None, "bias": None},
                    )
                    for point in cold["horizons"]:
                        point.update(
                            value=str(point["value"]),
                            planning_horizon=point["horizon"],
                            forecast_distance=point["horizon"],
                            status="PROVISIONAL_COLD_START",
                        )
                    cold["admission"].update(
                        policy_version="EXPLICIT_COLD_START-1",
                        manual_selection_allowed=True,
                        required_acknowledgements=[
                            "COLD_START_METHOD",
                            "NO_TARGET_WAPE",
                            "UNDETERMINED_INCREMENTAL_EFFECT",
                        ],
                    )
            candidates["COLD_START"] = cold
            source_rows = [row for row in chain_rows if row["product_id"] == pid]
            real_rows = [row for row in chain_observed if row["product_id"] == pid]
            products.append(
                {
                    **meta,
                    "product_id": pid,
                    "chain_id": chain,
                    "model_group_key": canonical(
                        [meta.get(key) for key in GROUP_FIELDS]
                    ),
                    "candidates": candidates,
                    "bridge_horizons": [],
                    "commercial_status": meta.get("status") or "UNKNOWN",
                    "input_snapshot": {
                        "source_context": copy.deepcopy(context),
                        "objective": "Pedido",
                        "training_cutoff": cutoff,
                        "observed_cutoff": real_cutoff,
                        "history": [
                            {
                                key: row.get(key)
                                for key in (
                                    "period",
                                    "value",
                                    "basis",
                                    "source_reference",
                                    "source_id",
                                )
                            }
                            for row in source_rows
                        ],
                        "observed_numeric_months": sum(
                            row.get("value") is not None for row in real_rows
                        ),
                        "target_product_ids": sorted(requested),
                        "ml_target_basis": "OBSERVED_ONLY",
                    },
                }
            )
    models = aggregate_models(products, paired)
    result = {
        "schema": "unified-planning-run-1",
        "run_id": run_id or str(uuid.uuid4()),
        "source_context": context,
        "objective": "Pedido",
        "cutoff": cutoff,
        "model_cutoff": cutoff,
        "observed_cutoff": real_cutoff,
        "planning_start": add_month(cutoff, 1),
        "planning_end": add_month(cutoff, 12),
        "window_start": add_month(cutoff, 1),
        "window_end": add_month(cutoff, 12),
        "planning_basis": "OBSERVED_AND_COMMERCIAL_CLOSE"
        if any(row["basis"] == "COMMERCIAL_CLOSE" for row in fit)
        else "OBSERVED_ONLY",
        "emitted_at": emitted_at or datetime.now(timezone.utc).isoformat(),
        "engine_version": ENGINE_VERSION,
        "operation_version": OPERATION_VERSION,
        "method_selection_version": "CURRENT_CONTEXT_ELIGIBILITY_V1",
        "training_fingerprint": digest(fit),
        "observed_training_fingerprint": digest(observed),
        "forecast_distances": list(range(1, 13)),
        "planning_distances": list(range(1, 13)),
        "evaluation_mode": "RETROSPECTIVE_TRAINING",
        "champion_published": False,
        "bias_convention": "OBSERVED_MINUS_FORECAST",
        "automatic_adoption": False,
        "products": products,
        "models": models,
        "paired_evidence": paired,
        "audits": audits,
        "scope_comparison": _scope_comparison(products, paired),
        "coverage": {
            "products": len(products),
            "scopes": len(selected_chains),
            **{
                family: sum(
                    all(
                        point["value"] is not None
                        for point in p["candidates"][family]["horizons"]
                    )
                    for p in products
                )
                for family in (*FAMILY_KEYS.values(), "COLD_START")
            },
        },
        "restrictions": [
            "NOT_WAPE_LIVE",
            "NO_AUTOMATIC_CHAMPION",
            "OBSERVED_ONLY_BACKTEST_TARGETS",
            "NO_COLD_START_IN_REGULAR_TOTALS",
            "COLD_START_INCREMENTAL_EFFECT_UNDETERMINED",
        ],
    }
    result["content_digest"] = digest(result)
    return result
