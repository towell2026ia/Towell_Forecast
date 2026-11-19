"""Reproducible commercial December close, separate from observed ORDER facts.

No reference is promoted to an observed order. The returned final-fit records
carry an explicit basis; observed_records are the only evaluation population.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json

from .unified_source import MASTER_SHA256, CONTRACT
from .unified_runtime import normalized_records, uid

PLANNING_BASIS = "OBSERVED_AND_COMMERCIAL_CLOSE"
RULES = {"version": "UNIFIED-COMMERCIAL-CLOSE-1", "observed_cutoff": "2026-09",
         "model_cutoff": "2026-12", "reference_year": "2026",
         "priority": ["OBSERVED_COMPLETE_NUMERIC", "DEFINITIVE_INCLUDING_ZERO", "ORDER_PROPOSAL", "ABSENT"],
         "partial_is_observed": False, "reference_is_real_for_evaluation": False,
         "evaluation_population": "OBSERVED_ONLY", "source_unit": "PIEZAS"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _number(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("SCENARIO_BOOLEAN_QUANTITY")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("SCENARIO_INVALID_QUANTITY") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("SCENARIO_INVALID_QUANTITY")
    return str(value)


def _pick(row):
    period = row["periodo"]
    observed = _number(row.get("pedido_real_piezas"))
    if (period <= RULES["observed_cutoff"] and row.get("apto_entrenamiento_pedido") is True
            and observed is not None):
        return "OBSERVED", "pedido_real_piezas", observed, None
    if period.startswith(RULES["reference_year"] + "-"):
        for field in ("definitiva_piezas", "propuesta_pedidos_piezas"):
            if row.get(field) is not None:
                try:
                    value = _number(row[field])
                except ValueError:
                    return None, field, None, "INVALID_COMMERCIAL_REFERENCE"
                if value is not None:
                    return "COMMERCIAL_CLOSE", field, value, None
    return None, None, None, "NO_COMPLETE_ORDER_OR_COMMERCIAL_REFERENCE"


def validate_scenario(scenario):
    semantic = {key: scenario[key] for key in ("schema", "master_sha256", "planning_basis", "rules", "records")}
    if (semantic["schema"] != "unified-commercial-close-scenario-1" or semantic["master_sha256"] != MASTER_SHA256
            or semantic["planning_basis"] != PLANNING_BASIS or semantic["rules"] != RULES
            or fingerprint(semantic) != scenario.get("scenario_hash")):
        raise ValueError("SCENARIO_SEAL_MISMATCH")
    return semantic


def build_close_runtime(document, bindings, *, generation=6):
    if (document.get("contract") != CONTRACT or document["source"].get("sha256") != MASTER_SHA256
            or document.get("closure", {}).get("last_closed_period") != RULES["observed_cutoff"]
            or type(generation) is not int or generation < 6):
        raise ValueError("SCENARIO_SOURCE_UNACCREDITED")
    runtime = normalized_records(document, bindings, generation=generation)
    identities = {item["identity_key"]: item for item in bindings["identities"]
                  if item["state"] in {"EXISTING_UUID", "NEW_LOCAL_SOURCE_IDENTITY"}}
    audit, seen = [], set()
    for row in document["rows"]:
        if not "2024-01" <= row["periodo"] <= RULES["model_cutoff"]:
            raise ValueError("SCENARIO_PERIOD_OUTSIDE_CONTRACT")
        key = row["clave"], row["periodo"]
        if key in seen:
            raise ValueError("SCENARIO_DUPLICATE_PRODUCT_MONTH")
        seen.add(key)
        identity = identities.get(row["clave"])
        if identity is None:
            raise ValueError("SCENARIO_IDENTITY_NOT_BOUND")
        basis, field, value, reason = _pick(row)
        source = row.get("lineage", {}).get(field, {}) if field else {}
        if value is not None and (not isinstance(source.get("cell"), str)
                                  or not source["cell"].startswith("Base_2024_2026!")):
            raise ValueError("SCENARIO_SOURCE_CELL_MISSING")
        audit.append({"identity_key": row["clave"], "chain_id": identity["chain_id"],
            "product_id": identity["product_id"], "period": row["periodo"], "basis": basis,
            "value": value, "source_field": field, "source_reference": source.get("cell"),
            "source_value_literal": source.get("raw"), "source_formula": source.get("formula"),
            "source_row": row["source_row"], "observed_value": row.get("pedido_real_piezas"),
            "observed_complete": row["periodo"] <= RULES["observed_cutoff"] and row.get("apto_entrenamiento_pedido") is True,
            "observed_partial": row["periodo"] > RULES["observed_cutoff"] and row.get("pedido_real_piezas") is not None,
            "original_order_reference": row.get("pedido_celda"),
            "commercial_origin": row.get("fuente_referencia"),
            "commercial_original_reference": row.get("celda_referencia"),
            "commercial_type": "DEFINITIVE" if field == "definitiva_piezas" else "ORDER_PROPOSAL" if basis == "COMMERCIAL_CLOSE" else None,
            "definitive_value": row.get("definitiva_piezas"), "proposal_value": row.get("propuesta_pedidos_piezas"),
            "eligible_final_fit": value is not None, "eligible_evaluation": basis == "OBSERVED", "reason": reason})
    audit.sort(key=lambda item: (item["identity_key"], item["period"]))
    semantic = {"schema": "unified-commercial-close-scenario-1", "master_sha256": MASTER_SHA256,
                "planning_basis": PLANNING_BASIS, "rules": RULES, "records": audit}
    scenario_hash = fingerprint(semantic)
    source_hash = fingerprint({"contract": CONTRACT, "master_sha256": MASTER_SHA256,
                               "scenario_hash": scenario_hash, "planning_basis": PLANNING_BASIS})
    old_fact_ids, master_id = {}, uid("source:" + source_hash)
    for fact in runtime["facts"]:
        identity = runtime["metadata"][fact["product_id"]]["identity_key"]
        old_id = fact["id"]
        fact["id"] = uid("fact:" + source_hash + ":" + identity + ":" + fact["period"] + ":" + fact["metric_code"])
        fact["source_id"] = master_id
        old_fact_ids[old_id] = fact["id"]
    observed = deepcopy(runtime["engine_records"])
    for record in observed:
        record.update(source_id=old_fact_ids[record["source_id"]], basis="OBSERVED",
                      observed_cutoff=RULES["observed_cutoff"], model_cutoff=RULES["model_cutoff"])
    engine = []
    physical = {(r["product_id"], r["period"]): r["id"] for r in runtime["facts"] if r["metric_code"] == "ORDER"}
    for item in audit:
        if item["value"] is None:
            continue
        attrs = runtime["metadata"][item["product_id"]]
        engine.append({"chain_id": item["chain_id"], "product_id": item["product_id"], "product_code": attrs["upc"],
            "description": attrs["model"], "category": attrs["product_type"] or "UNCLASSIFIED", "variant": attrs["color"],
            "period": item["period"], "objective": "Pedido", "metric_code": "ORDER", "value": item["value"],
            "basis": item["basis"], "source_id": physical[item["product_id"], item["period"]] if item["basis"] == "OBSERVED" else
                uid("scenario:" + scenario_hash + ":" + item["identity_key"] + ":" + item["period"]),
            "available_at": None, "availability_source": "UNKNOWN", "evidence_mode": "RETROSPECTIVE_TRAINING",
            "source_file_sha256": MASTER_SHA256, "target_sheet": "Base_2024_2026", "training_eligible": True,
            "source_reference": item["source_reference"], "source_value_literal": item["source_value_literal"],
            "observed_cutoff": RULES["observed_cutoff"], "model_cutoff": RULES["model_cutoff"],
            "scenario_hash": scenario_hash, "lineage": {key: item[key] for key in (
                "source_field", "source_reference", "source_formula", "commercial_origin", "commercial_original_reference", "commercial_type")}})
    runtime["source_hash"] = source_hash
    runtime["source"]["id"] = master_id
    context = runtime["source_context"]
    context.update(source_hash=source_hash, authorized_source_hash=source_hash, generation=generation,
                   training_cutoff=RULES["model_cutoff"], observed_cutoff=RULES["observed_cutoff"],
                   planning_basis=PLANNING_BASIS, scenario_hash=scenario_hash,
                   planning_start="2027-01", planning_end="2027-12", inventory_source="ESTADISTICA")
    scenario = {**semantic, "scenario_hash": scenario_hash, "source_hash": source_hash, "generation": generation,
                "observed_cutoff": RULES["observed_cutoff"], "training_cutoff": RULES["model_cutoff"],
                "planning_start": "2027-01", "planning_end": "2027-12",
                "engine_records_sha256": None, "observed_records_sha256": None,
                "counts": {"identities": len(runtime["metadata"]), "product_months": len(audit),
                    "final_fit_records": len(engine), "observed_records": len(observed),
                    "basis": dict(Counter(item["basis"] or "ABSENT" for item in audit)),
                    "commercial_zeroes": sum(item["basis"] == "COMMERCIAL_CLOSE" and Decimal(item["value"]) == 0 for item in audit)}}
    runtime.update(engine_records=engine, observed_records=observed, scenario=scenario)
    return runtime
