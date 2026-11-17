"""Run the existing forecast engine privately against the prepared source."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import math
import os
from pathlib import Path
import re

from services.assistant_api.calculation_history import _complete
from services.assistant_api.commercial_catalog import key
from services.assistant_api.forecast_execution import execute_forecast
from services.assistant_api.local_sales_policy import LocalSalesPolicy, SALES_UNAVAILABLE
from services.forecast_engine.engine import (
    ENGINE_VERSION, ForecastPolicy, forecast_dataset, normalize_retrospective_dataset,
)
from .preparation import REPO, file_hash, fingerprint
from .unification import csv_rows, json_read, write_csv, write_json
from .towell_sales_source import read_towell_sales_source


GROUP_FIELDS = ("chain_id", "account", "format", "model", "presentation", "commercial_code")
METADATA_FIELDS = (*GROUP_FIELDS, "product_id", "upc", "color", "product_type")
FAMILIES = ("statistical", "ml", "ensemble")
PERIOD = re.compile(r"20\d\d-(0[1-9]|1[0-2])\Z")
SALES_CONTEXT_FIELDS = {"sales_contract", "sales_file_sha256", "sheet", "inventory_source", "authorized_source_hash"}


def _scope_emissions(directory, expected=None, visited=None):
    directory = Path(directory).resolve()
    visited = set() if visited is None else visited
    if directory in visited:
        raise ValueError("official_forecast_reuse_cycle")
    visited.add(directory)
    context_path = directory / "Context.json"
    if expected is not None and file_hash(context_path) != expected.get("Context.json"):
        raise ValueError("official_forecast_reuse_artifact_changed")
    context = json_read(context_path)
    inherited = _scope_emissions(context["reused_from"], context["reused_scope_sha256"], visited) if context.get("reused_from") else {}
    result = {}
    for path in sorted(directory.glob("Outcome_*.json")):
        if expected is not None and file_hash(path) != expected.get(path.name):
            raise ValueError("official_forecast_reuse_artifact_changed")
        outcome = json_read(path)
        chain = outcome["chain_id"]
        result[chain] = inherited[chain] if outcome.get("reused") else context["emitted_at"]
    return result


def source_inputs(directory, manifest_sha, *, first_period="2025-01"):
    directory = Path(directory)
    if file_hash(directory / "Manifest.json") != manifest_sha:
        raise ValueError("official_manifest_hash_mismatch")
    manifest = json_read(directory / "Manifest.json")
    if manifest["schema"] != "official-source-1":
        raise ValueError("official_source_schema_mismatch")
    for name, sha in manifest["outputs_sha256"].items():
        if Path(name).name != name or file_hash(directory / name) != sha:
            raise ValueError("official_source_artifact_changed")
    packet = json_read(directory / "Import.json")
    if packet["source_hash"] != manifest["source_hash"]:
        raise ValueError("official_source_hash_mismatch")
    facts = csv_rows(directory / "Facts.csv")
    if len({row["id"] for row in facts}) != len(facts):
        raise ValueError("official_source_fact_id_duplicate")
    values = {(row["id"], row["chain_id"], row["product_id"], row["period"], row["metric_code"], row["value"])
              for row in facts}
    expected = {(row["id"], row["chain_id"], row["product_id"], row["period"], row["metric_code"], row["value"])
                for row in packet["observations"]}
    if values != expected or len(values) != len(packet["observations"]):
        raise ValueError("official_csv_import_mismatch")
    if (not isinstance(first_period, str) or not PERIOD.fullmatch(first_period)
            or first_period > manifest["cutoff"]):
        raise ValueError("official_forecast_first_period_invalid")
    if any(row["purchase_type"] != "RS" or row["period"] < first_period or row["period"] > manifest["cutoff"] for row in facts):
        raise ValueError("official_forecast_source_out_of_scope")
    return manifest, facts


def engine_records(facts, cutoff):
    sales = [row for row in facts if row["metric_code"] == "SALES"]
    grouped = defaultdict(list)
    for row in sales:
        grouped[row["chain_id"], row["product_id"]].append(row)
    metadata = {}
    for identity, observations in grouped.items():
        latest = max(row["period"] for row in observations)
        variants = {tuple(row[name] for name in METADATA_FIELDS) for row in observations if row["period"] == latest}
        if len(variants) != 1:
            raise ValueError("official_latest_product_attributes_ambiguous")
        metadata[identity] = dict(zip(METADATA_FIELDS, next(iter(variants)), strict=True))
    records = []
    for row in sales:
        if row["period"] > cutoff or row["available_at"] or row["availability_source"] != "UNKNOWN":
            raise ValueError("official_forecast_evidence_mismatch")
        current = metadata[row["chain_id"], row["product_id"]]
        records.append({
            "chain_id": row["chain_id"], "product_id": row["product_id"], "product_code": row["upc"],
            "description": row["model"], "category": current["product_type"] or "UNCLASSIFIED", "variant": row["color"],
            "period": row["period"], "objective": "Venta", "value": row["value"], "source_id": row["id"],
            "available_at": None, "availability_source": "UNKNOWN", "evidence_mode": "RETROSPECTIVE_TRAINING",
        })
    return records, metadata


def verify_towell_inputs(directory, manifest, facts, installed_policy=None):
    """Verify the sealed packet against the current workbook before any engine runs."""
    policy_file = "Authorized_Sales_Policy.json"
    if policy_file not in manifest["outputs_sha256"]:
        raise ValueError("official_forecast_sales_policy_missing")
    policy = LocalSalesPolicy(json_read(directory / policy_file))
    if installed_policy is not None and policy.config != installed_policy.config:
        raise ValueError("official_forecast_sales_context_invalid")
    context = manifest["forecast_source_context"]
    if (policy.config["master_sha256"] != context["sales_file_sha256"]
            or policy.config["source_hash"] != manifest["source_hash"]
            or policy.config["generation"] != manifest["generation"]
            or policy.config["last_closed_period"] != manifest["cutoff"]):
        raise ValueError("official_forecast_sales_context_invalid")
    try:
        document = read_towell_sales_source(policy.path.parent.parent, confirmed_closed_period=manifest["cutoff"],
                                           expected_sha256=policy.config["master_sha256"])
    except (OSError, ValueError) as error:
        raise ValueError(SALES_UNAVAILABLE) from error
    cells = {record["source_reference"]: record for record in document["records"] if record["eligible"]}
    identities = {row["identity_key"]: row for row in document["identities"]}
    seen = set()
    for fact in facts:
        reference = fact.get("source_reference")
        cell = cells.get(reference)
        if not cell or reference in seen:
            raise ValueError("official_forecast_sales_cell_invalid")
        identity = identities[cell["identity_key"]]
        if (fact.get("metric_code") != "SALES" or fact.get("source_sheet") != "Towell"
                or fact.get("source_sha256") != document["source"]["sha256"]
                or fact.get("source_file") != document["source"]["name"]
                or fact.get("source_raw_value_literal") != cell["source_value_literal"]
                or fact["period"] != cell["period"] or Decimal(fact["value"]) != cell["value"]
                or any(fact.get(field) != identity[field] for field in ("account", "format", "upc"))):
            raise ValueError("official_forecast_sales_cell_mismatch")
        seen.add(reference)
    return policy


def eligibility(records, cutoff, policy):
    states, selected = [], set()
    for product in normalize_retrospective_dataset(records, cutoff):
        history = product.history(cutoff)
        state = product.lifecycle(cutoff, policy)
        if state == "COLD_START" and len(history) < policy.min_product_observations:
            state = "INSUFFICIENT"
        allowed = state in {"ACTIVE", "COLD_START"}
        if allowed:
            selected.add(product.product_id)
        states.append({
            "chain_id": product.chain_id, "product_id": product.product_id, "history_months": len(history),
            "status": state, "eligible": allowed, "minimum_product_observations": policy.min_product_observations,
            "reason": None if allowed else state,
        })
    return states, selected


def variant_forecasts(result, states, metadata, cutoff, context):
    candidates = {}
    if result:
        for row in result["model_audit"]["product_candidates"]:
            identity = row["product_id"], row["family"]
            if identity in candidates or row["family"] not in FAMILIES:
                raise ValueError("official_forecast_candidate_duplicate")
            if not _complete(row["horizons"], cutoff):
                raise ValueError("official_forecast_horizon_incomplete")
            candidates[identity] = row
    outputs, coverage = [], []
    for state in states:
        details = metadata[state["chain_id"], state["product_id"]]
        for family in FAMILIES:
            candidate = candidates.get((state["product_id"], family))
            if candidate and not state["eligible"]:
                raise ValueError("official_forecast_ineligible_product")
            reason = state["reason"] or ("CHAIN_CALCULATION_FAILED" if result is None else "NO_VALIDATED_PRODUCT_CANDIDATE")
            coverage.append({**details, **state, "family": family, "horizons": len(candidate["horizons"]) if candidate else 0,
                             "candidate_model": candidate["model"] if candidate else None,
                             "reason": None if candidate else reason})
            for horizon in candidate["horizons"] if candidate else []:
                value = horizon["value"]
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                    raise ValueError("official_forecast_invalid_quantity")
                outputs.append({
                    **details, **context, "family": family, "method": candidate["model"],
                    "horizon": horizon["horizon"], "period": horizon["target_period"], "pieces": str(Decimal(str(value))),
                    "issue_period": cutoff, "status": "PROVISIONAL", "temporal_certification": False,
                    "retrospective_wape": candidate.get("wape"), "retrospective_bias": candidate.get("bias"),
                })
    return outputs, coverage


def consolidate_models(variants, metadata, cutoff, context):
    groups = defaultdict(set)
    labels = {}
    for identity, details in metadata.items():
        group = tuple(key(details[name]) for name in GROUP_FIELDS)
        groups[group].add(identity)
        labels.setdefault(group, {field: details[field] for field in GROUP_FIELDS})
    values = {}
    for row in variants:
        group = tuple(key(row[name]) for name in GROUP_FIELDS)
        identity = row["chain_id"], row["product_id"]
        lookup = group, row["family"], row["horizon"], identity
        if lookup in values or identity not in groups[group]:
            raise ValueError("official_model_variant_double_count")
        values[lookup] = Decimal(row["pieces"])
    output = []
    with localcontext() as decimal_context:
        decimal_context.prec = 80
        for group, expected in sorted(groups.items()):
            for family in FAMILIES:
                for horizon in range(1, 13):
                    members = [values[group, family, horizon, identity] for identity in sorted(expected)
                               if (group, family, horizon, identity) in values]
                    from services.forecast_engine.engine import add_month
                    output.append({
                        **labels[group], **context, "group_id": fingerprint(group), "family": family,
                        "horizon": horizon, "period": add_month(cutoff, horizon), "issue_period": cutoff,
                        "pieces": str(sum(members, Decimal(0))) if members else None,
                        "covered_variants": len(members), "expected_variants": len(expected),
                        "status": "COMPLETE" if len(members) == len(expected) else "PARTIAL" if members else "NO_COVERAGE",
                        "commercial_code_accredited": bool(labels[group]["commercial_code"]),
                    })
    return output


def run(directory, manifest_sha, output, *, compute=forecast_dataset, reuse_from=None,
        first_period="2025-01", context_generation=1, extra_source_context=None):
    directory, output = Path(directory), Path(output)
    if output.resolve().is_relative_to(REPO) or output.exists():
        raise ValueError("new_private_forecast_output_required")
    if type(context_generation) is not int or context_generation < 1:
        raise ValueError("official_forecast_generation_invalid")
    manifest, facts = source_inputs(directory, manifest_sha, first_period=first_period)
    declared_provenance = manifest.get("forecast_source_context") or {}
    local_config = os.environ.get("LOCAL_POSTGRES_CONFIG")
    if local_config and not declared_provenance:
        raise ValueError("official_forecast_local_sales_contract_required")
    if extra_source_context is not None and extra_source_context != declared_provenance:
        raise ValueError("official_forecast_sales_context_invalid")
    provenance = dict(extra_source_context or declared_provenance)
    if provenance and (set(provenance) != SALES_CONTEXT_FIELDS
            or provenance.get("sales_contract") != "towell-sales-v1"
            or provenance.get("sheet") != "Towell"
            or provenance.get("inventory_source") != "ESTADISTICA"
            or provenance.get("authorized_source_hash") != manifest["source_hash"]
            or not isinstance(provenance.get("sales_file_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", provenance["sales_file_sha256"])):
        raise ValueError("official_forecast_sales_context_invalid")
    if provenance and manifest.get("generation", context_generation) != context_generation:
        raise ValueError("official_forecast_generation_invalid")
    if provenance and reuse_from is not None:
        raise ValueError("towell_forecast_reuse_forbidden")
    configured = LocalSalesPolicy(json_read(Path(local_config)).get("authorized_sales_policy")) if local_config else None
    live_policy = verify_towell_inputs(directory, manifest, facts, configured) if provenance else None
    cutoff = manifest["cutoff"]
    records, metadata = engine_records(facts, cutoff)
    policy = ForecastPolicy()
    context = {"source_hash": manifest["source_hash"], "generation": context_generation,
               "emitted_at": datetime.now(timezone.utc).isoformat(), "engine_version": ENGINE_VERSION,
               "catalogue_policy": "LATEST_SOURCE_ATTRIBUTES_AT_CUTOFF_RETROSPECTIVE", **provenance}
    changes = []
    first, latest = {}, {}
    for row in facts:
        if row["metric_code"] == "SALES":
            identity = row["chain_id"], row["product_id"]
            if identity not in first or row["period"] < first[identity]["period"]:
                first[identity] = row
            if identity not in latest or row["period"] > latest[identity]["period"]:
                latest[identity] = row
    for identity, original in sorted(first.items()):
        current = metadata[identity]
        if original["product_type"] != current["product_type"]:
            changes.append({
                **current, "original_category": original["product_type"],
                "original_period": original["period"], "original_source_cell": original["source_product_type_cell"],
                "current_period": latest[identity]["period"], "current_source_cell": latest[identity]["source_product_type_cell"],
                "source_sha256": latest[identity]["source_sha256"],
                "policy": context["catalogue_policy"],
            })
    reuse, reuse_emissions = None, {}
    if reuse_from:
        reuse_from = Path(reuse_from)
        reuse = json_read(reuse_from / "Summary.json")
        if (reuse["source_context"]["source_hash"] != manifest["source_hash"]
                or reuse["source_context"].get("generation", 1) != context_generation
                or reuse["source_context"]["engine_version"] != ENGINE_VERSION
                or reuse["cutoff"] != cutoff or reuse["policy"] != asdict(policy)):
            raise ValueError("official_forecast_reuse_context_mismatch")
        for name, sha in reuse["outputs_sha256"].items():
            if Path(name).name != name or file_hash(reuse_from / name) != sha:
                raise ValueError("official_forecast_reuse_artifact_changed")
        reuse_emissions = _scope_emissions(reuse_from, reuse["outputs_sha256"])
    changed_chains = {row["chain_id"] for row in changes}
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "Context.json", {**context, "cutoff": cutoff, "policy": asdict(policy),
                                        "source_manifest_sha256": manifest_sha, "evidence_mode": "RETROSPECTIVE_TRAINING",
                                        "reused_from": str(reuse_from) if reuse else None,
                                        "reused_scope_sha256": reuse["outputs_sha256"] if reuse else None})
    write_csv(output / "Catalogue_changes.csv", changes, list(changes[0]) if changes else [])
    variants, all_coverage, outcomes, all_exclusions = [], [], [], []
    chains = sorted({row["chain_id"] for row in records})
    for index, chain in enumerate(chains, 1):
        selected_rows = [row for row in records if row["chain_id"] == chain]
        states, eligible = eligibility(selected_rows, cutoff, policy)
        result, failure, exclusions = None, None, []
        previous = json_read(reuse_from / ("Outcome_" + chain + ".json")) if reuse else None
        reused = (reuse is not None and previous["status"] != "FAILED"
                  and (chain not in changed_chains or reuse["source_context"].get("catalogue_policy") == context["catalogue_policy"]))
        print(f"chain {index}/{len(chains)} products {len(states)} eligible {len(eligible)}", flush=True)
        if reused:
            failure = previous["failure"]
            exclusions = [row for row in reuse.get("recovery_exclusions", []) if row["chain_id"] == chain]
            if previous["status"] == "COMPLETED":
                result = json_read(reuse_from / (chain + ".json"))
                write_json(output / (chain + ".json"), result)
            print(f"chain {index}/{len(chains)} REUSED", flush=True)
        elif eligible:
            try:
                computed, exclusions = execute_forecast(
                    [row for row in selected_rows if row["product_id"] in eligible], cutoff, compute=compute,
                    chain_id=chain, objective="Venta", policy=policy, evidence_mode="RETROSPECTIVE_TRAINING",
                    on_stage=lambda stage: print(f"chain {index}/{len(chains)} {stage}", flush=True))
                if computed["chains"]:
                    result = computed["chains"][0]
                    write_json(output / (chain + ".json"), result)
            except Exception as error:
                failure = type(error).__name__ + ":" + str(error)[:160]
        all_exclusions.extend(exclusions)
        if exclusions:
            write_json(output / ("Exclusions_" + chain + ".json"), exclusions)
            by_product = {row["product_id"]: row for row in exclusions}
            for state in states:
                if state["product_id"] in by_product:
                    state.update(eligible=False, reason=by_product[state["product_id"]]["reason"])
        scope_context = {**context, "emitted_at": reuse_emissions.get(chain, reuse["source_context"]["emitted_at"])} if reused else context
        produced, coverage = variant_forecasts(result, states, metadata, cutoff, scope_context)
        variants.extend(produced)
        all_coverage.extend(coverage)
        outcomes.append({"chain_id": chain, "products": len(states), "eligible": len(eligible),
                         "status": "COMPLETED" if result else "FAILED" if failure else "NO_ELIGIBLE_PRODUCTS",
                         "failure": failure, "forecast_rows": len(produced), "reused": reused})
        write_json(output / ("Outcome_" + chain + ".json"), outcomes[-1])
    models = consolidate_models(variants, metadata, cutoff, context)
    write_csv(output / "Variants.csv", variants, list(variants[0]) if variants else [])
    write_csv(output / "Models.csv", models, list(models[0]) if models else [])
    write_csv(output / "Coverage.csv", all_coverage, list(all_coverage[0]) if all_coverage else [])
    stats = {
        "source_context": context, "cutoff": cutoff, "policy": asdict(policy), "chains": outcomes,
        "products": len(metadata), "families": {
            family: {"products_with_12_horizons": sum(row["family"] == family and row["horizons"] == 12 for row in all_coverage),
                     "products_without_coverage": sum(row["family"] == family and not row["horizons"] for row in all_coverage)}
            for family in FAMILIES
        },
        "eligibility": dict(Counter(row["status"] for row in all_coverage if row["family"] == "statistical")),
        "model_groups": len({row["group_id"] for row in models}), "model_rows": len(models),
        "variant_rows": len(variants), "temporal_certification": False,
        "catalogue_category_changes": len(changes), "recomputed_scopes": len(chains) - sum(row["reused"] for row in outcomes),
        "recovery_exclusions": all_exclusions,
    }
    if source_inputs(directory, manifest_sha, first_period=first_period)[0] != manifest:
        raise ValueError("official_source_changed_during_forecast")
    if live_policy and not live_policy.available():
        raise ValueError(SALES_UNAVAILABLE)
    stats["outputs_sha256"] = {path.name: file_hash(path) for path in sorted(output.iterdir())}
    write_json(output / "Summary.json", stats)
    print("forecast complete " + str(stats["families"]), flush=True)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reuse-from", type=Path)
    parser.add_argument("--first-period", default="2025-01")
    parser.add_argument("--context-generation", type=int, default=1)
    args = parser.parse_args()
    run(args.source, args.manifest_sha256, args.output, reuse_from=args.reuse_from,
        first_period=args.first_period, context_generation=args.context_generation)


if __name__ == "__main__":
    main()
