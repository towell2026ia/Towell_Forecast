"""Present saved Pedido ML winners without relabeling Ensemble components.

This is a pure read projection. It neither executes a model nor modifies the
cached preview, candidate curves, applied Ensemble, or persisted calculations.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
import math
import re

from services.forecast_engine.engine import add_month


def _amount(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def _candidate(candidate, issue, mode):
    rows = candidate.get("horizons")
    if (not isinstance(candidate.get("model"), str) or not candidate["model"].strip()
            or candidate.get("evidence_mode") != mode or not isinstance(rows, list)
            or len(rows) != 12 or any(not isinstance(row, dict) for row in rows)
            or any(type(row.get("horizon")) is not int for row in rows)
            or {row.get("horizon") for row in rows} != set(range(1, 13))):
        return None
    if any(type(row.get("horizon")) is not int or row.get("target_period") != add_month(issue, row["horizon"])
           or not _amount(row.get("value")) for row in rows):
        return None
    return {row["horizon"]: row for row in rows}


def project_pedido_individual_ml(preview, *, project_aggregates=True):
    if preview.get("objective") != "Pedido":
        return preview
    output = deepcopy(preview)
    issue = output.get("issue_period")
    if not isinstance(issue, str) or not re.fullmatch(r"20\d\d-(?:0[1-9]|1[0-2])", issue):
        return output
    mode = output.get("evidence_mode") or output.get("mode")
    candidates = defaultdict(list)
    for row in (output.get("selection") or {}).get("product_candidates", []):
        if isinstance(row, dict) and row.get("family") == "ml":
            candidates[row.get("product_id")].append(row)
    valid = {}
    for product in output.get("products", []):
        own = candidates.get(product["product_id"], [])
        curve = _candidate(own[0], issue, mode) if len(own) == 1 else None
        displayed = product.get("horizons", [])
        if (len(displayed) != 12 or any(not isinstance(row, dict) for row in displayed)
                or any(type(row.get("horizon")) is not int for row in displayed)
                or {row.get("horizon") for row in displayed} != set(range(1, 13))):
            curve = None
        if curve and any(row.get("target_period") != curve[row["horizon"]]["target_period"] for row in displayed):
            curve = None
        if curve:
            valid[product["product_id"]] = own[0]
        product["ml_model"] = own[0]["model"] if curve else None
        product["ml_unavailable_reason"] = None if curve else "NO_VALIDATED_PRODUCT_ML_CANDIDATE"
        for row in product.get("horizons", []):
            if not isinstance(row, dict):
                continue
            horizon = row.get("horizon")
            original_ml = row.get("ml_value")
            row.setdefault("scope_ml_value", original_ml)
            row.setdefault("scope_ml_model", row.get("ml_model"))
            individual = curve.get(horizon) if curve else None
            if individual and row.get("target_period") != individual["target_period"]:
                individual = None
            row["ml_value"] = individual["value"] if individual else None
            row["ml_model"] = own[0]["model"] if individual else None
            # ensemble_ml_model/component and both weights remain untouched.
    available = {product["product_id"] for product in output.get("products", [])
                 if product["product_id"] in valid and len(product.get("horizons", [])) == 12
                 and all(_amount(row.get("ml_value")) for row in product["horizons"])}
    distribution = Counter(valid[pid]["model"] for pid in sorted(available))
    if output.get("eligibility") is not None:
        output["eligibility"]["ml_eligible"] = len(available)
    ml = output.setdefault("ml", {})
    for name in ("leader", "retrospective_wape", "retrospective_bias"):
        ml.setdefault("scope_" + name, ml.get(name))
    ml.update(status="COMPLETED" if available else "NOT_ELIGIBLE", models=dict(distribution),
              leader=next(iter(distribution)) if len(distribution) == 1 else None)
    own = valid[next(iter(available))] if len(output.get("products", [])) == 1 and len(available) == 1 else None
    ml["retrospective_wape"] = own.get("wape") if own else None
    ml["retrospective_bias"] = own.get("bias") if own else None
    ml["individual_candidates"] = len(available)
    output["ml_representation"] = "VALIDATED_PRODUCT_CANDIDATE_V1"
    if not project_aggregates:
        return output
    products = output.get("products", [])
    for aggregate in output.get("aggregates", []):
        level, group = aggregate.get("level"), aggregate.get("key")
        members, expected, unknown = [], 0, 0
        for product in products:
            row = next((r for r in product.get("horizons", []) if r.get("horizon") == aggregate.get("horizon")
                        and r.get("target_period") == aggregate.get("target_period")), None)
            category = (row or {}).get("category") or product.get("engine_category") or product.get("category_id")
            belongs = level == "chain" and group == output.get("chain_id") or level == "category" and group == category
            if belongs:
                expected += 1
                if row is not None:
                    members.append(row)
            elif level == "category" and row is None and (category is None or re.fullmatch(r"[0-9a-fA-F-]{36}", str(category))):
                # An old cache can contain a legacy category UUID for a product
                # without a curve. Do not infer its commercial category by name.
                unknown += 1
        aggregate.setdefault("scope_ml_value", aggregate.get("ml_value"))
        values = [Decimal(str(row["ml_value"])) for row in members if _amount(row.get("ml_value"))]
        aggregate["ml_value"] = float(sum(values, Decimal(0))) if values else None
        aggregate["ml_covered_products"] = len(values)
        aggregate["ml_expected_products"] = expected
        aggregate["ml_unknown_category_products"] = unknown
        aggregate["ml_partial"] = len(values) < expected or bool(unknown)
        components = [Decimal(str(row["ensemble_ml_component"])) for row in members if _amount(row.get("ensemble_ml_component"))]
        aggregate["ensemble_ml_component"] = float(sum(components, Decimal(0))) if components else None
        aggregate["ensemble_ml_component_products"] = len(components)
    return output
