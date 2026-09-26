"""Opt-in residual grain review. No runtime, database or workbook mutation."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
import re

from services.assistant_api.business_membership import certify_scopes, extract_business_memberships
from services.assistant_api.chain_ingestion import identifier, is_formula
from services.assistant_api.global_certification import compare_global_golden, dataset_sha
from services.assistant_api.hierarchical_grain import GrainResolver, SourceScope, SCOPE_BLOCKS, VALUE_REASONS, lookup_reference, period_fields, scope_id
from services.assistant_api.historical_corpus import number
from services.assistant_api.source_adjudication import fingerprint

BLOCK_TYPES = ("PARENT_SCOPE_MISSING", "CHILD_SCOPE_MISSING", "MULTIPLE_POSSIBLE_PARENT", "MULTIPLE_POSSIBLE_CHILD",
               "CALC_SHEET_WITHOUT_VALUE_SCOPE", "RAW_SOURCE_WITHOUT_SCOPE", "IDENTITY_SCOPE_COUPLED", "OTHER")
KINDS = {"PARENT_CHAIN": "CERTIFIED_PARENT_SCOPE", "COMMERCIAL_UNIT": "CERTIFIED_CHILD_SCOPE",
         "EXPLICIT_ROW_UNIT": "CERTIFIED_EXPLICIT_ROW_SCOPE"}


def physical(source):
    return source["source_sha256"], source["sheet"], source["cell"]


def decision_id(decision):
    return fingerprint([decision["key"], decision["reason_code"], sorted(decision["sources"], key=fingerprint)])


def verify_global_baseline(report, certification, golden, expected):
    if (dataset_sha(report) != expected["dataset_sha256"] or certification["dataset_sha256"] != expected["dataset_sha256"]
            or certification["certification_sha256"] != expected["certification_sha256"]
            or compare_global_golden(golden, certification)["status"] != "PASS"):
        raise ValueError("frozen_global_hash_changed_STOP")
    summary = certification["summary"]
    for key in ("selected_observations", "actual_bearing_scopes", "scope_product_identities"):
        if summary[key] != expected[key]:
            raise ValueError("frozen_global_count_changed_STOP")
    rows = {tuple(r["canonical_key"]): r for r in certification["rows"]}
    if len(report["selected"]) != len(rows) or len(rows) != expected["selected_observations"]:
        raise ValueError("frozen_global_facts_changed_STOP")
    for fact in report["selected"]:
        row = rows.get(tuple(fact["key"]))
        if (not row or row["value_fingerprint"] != fingerprint(fact["value"]) or row["scope_type"] != fact["fact_grain"]
                or (row["source_hash"], row["sheet"], row["locator"]) != physical(fact)
                or fact["available_at"] is not None or fact["availability_source"] != "UNKNOWN"):
            raise ValueError("frozen_global_fact_changed_STOP")
    return {"status": "PASS", "observations": len(rows), "dataset_sha256": expected["dataset_sha256"],
            "certification_sha256": expected["certification_sha256"]}


def build_context(books, profiles, membership_authority, scope_authority):
    adapted, business = certify_scopes(books, profiles, membership_authority, allow_empty=True)
    memberships = extract_business_memberships(books, adapted, business)
    return adapted, memberships, GrainResolver(adapted, scope_authority, memberships)


def _cell(books, source):
    digest, sheet, cell = physical(source)
    match = re.fullmatch(r"([A-Z]+)([1-9]\d*)", cell)
    if digest not in books or sheet not in books[digest] or not match:
        raise ValueError("unknown_residual_source_or_locator")
    row_no = int(match[2])
    row = books[digest][sheet]["rows"].get(row_no, {})
    if match[1] not in row:
        raise ValueError("residual_locator_missing")
    return row_no, row, row[match[1]]


def _block_type(reason, profile, parents, children):
    if reason == "INSUFFICIENT_PRODUCT_IDENTITY":
        return "IDENTITY_SCOPE_COUPLED"
    if profile.sheet_role == "CHAIN_CALC":
        return "CALC_SHEET_WITHOUT_VALUE_SCOPE"
    if len(parents) > 1:
        return "MULTIPLE_POSSIBLE_PARENT"
    if len(children) > 1:
        return "MULTIPLE_POSSIBLE_CHILD"
    if profile.sheet_role == "RAW_BASE":
        return "RAW_SOURCE_WITHOUT_SCOPE"
    return "CHILD_SCOPE_MISSING" if children else "PARENT_SCOPE_MISSING" if parents else "OTHER"


def inventory_residuals(report, books, profiles, membership_authority, scope_authority):
    adapted, memberships, resolver = build_context(books, profiles, membership_authority, scope_authority)
    by_profile = {(p.source_hash, p.sheet_name): p for p in adapted}
    parent_contexts = defaultdict(list)
    for scope in resolver.scopes.values():
        if scope.fact_grain == "PARENT_CHAIN":
            parent_contexts[scope.source_hash].append({"parent": scope.parent_chain, "sheet": scope.sheet,
                                                      "source_scope_sha256": fingerprint(asdict(scope)),
                                                      "quantity_ownership_for_other_sheets": False})
    registry, inventory = {}, []
    for decision in sorted(report["resolution_ledger"], key=decision_id):
        if not decision["blocking"] or decision["reason_code"] not in SCOPE_BLOCKS:
            continue
        entries = []
        for source in sorted(decision["sources"], key=fingerprint):
            digest, name, locator = physical(source)
            p = by_profile[digest, name]
            n, row, value = _cell(books, source)
            status, literal = number(value)
            item, upc = identifier(row.get(p.item_column)), identifier(row.get(p.upc_column), upc=True)
            matches = [f for rn, _, f, period in period_fields(p, books[digest][name],
                       period_min=scope_authority.get("period_min", "0000-01"), period_max=scope_authority.get("period_max", "9999-12"))
                       if rn == n and f["column"] + str(n) == locator and [period, f["metric"]] == decision["key"][2:]]
            if len(matches) != 1:
                raise ValueError("residual_period_metric_not_supported")
            calc = is_formula(value) or locator in books[digest][name]["formulas"]
            if not calc and (status != "VALUE" or literal not in decision["values"]):
                raise ValueError("residual_literal_changed")
            scope = resolver.scopes[digest, name]
            scope_ref = fingerprint([digest, name])
            if scope_ref not in registry:
                headers = []
                for rn, values in sorted(books[digest][name]["rows"].items()):
                    if rn <= min(p.header_rows, default=1) or rn in p.header_rows:
                        headers.extend({"locator": col+str(rn), "text": text, "fingerprint": fingerprint(text)}
                                       for col, text in sorted(values.items()) if isinstance(text, str) and not is_formula(text))
                dependencies = []
                counts = Counter()
                for rn, values, field, period in period_fields(p, books[digest][name],
                        period_min=scope_authority.get("period_min", "0000-01"), period_max=scope_authority.get("period_max", "9999-12")):
                    text = values.get(field["column"])
                    formula = is_formula(text) or field["column"]+str(rn) in books[digest][name]["formulas"]
                    counts["FORMULA" if formula else number(text)[0]] += 1
                    ref = lookup_reference(text, p, rn) if formula else None
                    if ref:
                        dependencies.append({"locator": field["column"]+str(rn), "period": period, "metric": field["metric"],
                                             "dependency": ref, "formula_sha256": fingerprint(getattr(text, "text", text)),
                                             "proves_other_literal_cells": False})
                registry[scope_ref] = {"evidence_id": scope_ref, "source_hash": digest, "sheet": name,
                                       "profile": asdict(p), "profile_sha256": fingerprint(asdict(p)), "source_scope": asdict(scope),
                                       "workbook_parent_contexts": sorted(parent_contexts[digest], key=fingerprint),
                                       "headers": headers, "formula_dependencies": sorted(dependencies, key=fingerprint),
                                       "metric_cell_types": dict(sorted(counts.items())),
                                       "membership_evidence_is_quantity_authority": False,
                                       "context_is_quantity_authority": False, "new_value_scope_reviewed": False}
            parents = sorted({s["parent"] for s in parent_contexts[digest]} | ({scope.parent_chain} if scope.parent_chain else set()))
            children = resolver.children(digest, item, upc, decision["key"][2])
            member_rows = [asdict(m) for m in memberships if m.source_hash == digest and decision["key"][2] in m.observed_periods
                           and (m.item == item if item else m.upc == upc) and (not upc or not m.upc or m.upc == upc)]
            entries.append({"source_hash": digest, "sheet": name, "locator": locator, "item": item, "upc": upc,
                            "identity_pattern": "ITEM_UPC" if item and upc else "ITEM_ONLY" if item else "UPC_ONLY",
                            "period": decision["key"][2], "metric": decision["key"][3], "current_reason": decision["reason_code"],
                            "possible_parent": parents, "possible_child": children, "sheet_role": p.sheet_role,
                            "source_role": p.metric_source_type, "value_role": p.value_role,
                            "explicit_chain_fields": {p.chain_column: str(row.get(p.chain_column))} if p.chain_column else {},
                            "explicit_format_fields": {p.format_column: str(row.get(p.format_column))} if p.format_column else {},
                            "membership_evidence": sorted(member_rows, key=fingerprint),
                            "formula_or_literal": "FORMULA" if calc else "LITERAL", "literal": literal,
                            "value_fingerprint": fingerprint(literal), "source_profile": asdict(p), "evidence_id": scope_ref,
                            "block_type": _block_type(decision["reason_code"], p, parents, children),
                            "candidate_unit_from_sheet_authority": p.canonical_chain_code or None,
                            "metric_layout_sha256": fingerprint(p.metric_layout)})
        inventory.append({"decision_id": decision_id(decision), "canonical_key": decision["key"],
                          "reason": decision["reason_code"], "sources": entries, "status": "UNRESOLVED"})
    if len({r["decision_id"] for r in inventory}) != len(inventory):
        raise ValueError("duplicate_residual_decision")
    return inventory, [registry[k] for k in sorted(registry)], adapted, memberships


def cluster_inventory(inventory):
    grouped = defaultdict(list)
    for row in inventory:
        patterns = [{k: s[k] for k in ("source_hash", "sheet", "sheet_role", "source_role", "value_role", "possible_parent", "possible_child",
                    "candidate_unit_from_sheet_authority", "metric_layout_sha256", "identity_pattern", "block_type")}
                    for s in row["sources"]]
        grouped[fingerprint([row["reason"], sorted(patterns, key=fingerprint)])].append(row)
    clusters = []
    for cid, rows in sorted(grouped.items()):
        sources = [s for row in rows for s in row["sources"]]
        clusters.append({"cluster_id": cid, "blocker_count": len(rows), "decision_ids": sorted(r["decision_id"] for r in rows),
                         "source_pairs": sorted({(s["source_hash"], s["sheet"]) for s in sources}), "reason": rows[0]["reason"],
                         "block_types": sorted({s["block_type"] for s in sources}), "sheet_roles": sorted({s["sheet_role"] for s in sources}),
                         "source_roles": sorted({s["source_role"] for s in sources}), "identity_patterns": sorted({s["identity_pattern"] for s in sources}),
                         "candidate_parents": sorted({p for s in sources for p in s["possible_parent"]}),
                         "candidate_children": sorted({u for s in sources for u in s["possible_child"]}),
                         "metric_counts": dict(sorted(Counter(r["canonical_key"][3] for r in rows).items())),
                         "period_min": min(r["canonical_key"][2] for r in rows), "period_max": max(r["canonical_key"][2] for r in rows),
                         "status": "BLOCKED", "evidence_missing": "Independent reviewed quantity ownership or exact dependency for the literal cell"})
    ranked = sorted(clusters, key=lambda c: (-c["blocker_count"], c["cluster_id"]))
    pareto = {}
    for target in (80, 90, 95, 100):
        total, ids = 0, []
        for c in ranked:
            if total * 100 >= len(inventory) * target:
                break
            total += c["blocker_count"]
            ids.append(c["cluster_id"])
        pareto[str(target)] = {"cluster_ids": ids, "clusters": len(ids), "covered_blockers": total,
                               "coverage_percent": 100 * total / len(inventory) if inventory else 100}
    types = Counter(s["block_type"] for r in inventory for s in r["sources"][:1])
    return {"blocker_count": len(inventory), "cluster_count": len(clusters), "clusters": clusters, "pareto": pareto,
            "block_type_counts": {kind: types[kind] for kind in BLOCK_TYPES}}


def prove_dependency(source, profile, books, baseline):
    n, row, text = _cell(books, source)
    if not is_formula(text):
        return None  # Neighbour formulas and equal numbers never prove a literal.
    ref = lookup_reference(text, profile, n)
    if not ref:
        return None
    digest = source["source_sha256"]
    item = identifier(row.get(profile.item_column))
    matches = {}
    for fact in baseline["selected"]:
        if fact["key"][2:] != source["key"][2:]:
            continue
        for target in [fact, *fact.get("equivalent_sources", [])]:
            if target["source_sha256"] != digest or target["sheet"] != ref["sheet"]:
                continue
            rn, target_row, value = _cell(books, target)
            if (target["cell"] != ref["value_column"]+str(rn) or not item or fact.get("item") != item
                    or identifier(target_row.get(ref["item_column"])) != item or is_formula(value)):
                continue
            matches[physical(target)] = fact
    # Prove the exact lookup row, not a first match among repeated identities.
    target_sheet = books.get(digest, {}).get(ref["sheet"], {})
    all_identity_rows = [rn for rn, values in target_sheet.get("rows", {}).items()
                         if item and identifier(values.get(ref["item_column"])) == item]
    if len(matches) != 1 or len(all_identity_rows) != 1:
        return None
    locator, fact = next(iter(matches.items()))
    return {"status": "DERIVED_REPRESENTATION_NOT_A_FACT", "canonical_target_key": fact["key"],
            "target_locator": list(locator), "dependency": ref, "formula_sha256": fingerprint(getattr(text, "text", text)),
            "creates_fact": False, "formula_evaluated": False}


def reviewed_route(entry, books, profile, memberships, rule):
    """Explicitly reviewed, cell-pinned authority only; no context inference."""
    if (profile.value_role != "ACTUAL" or rule.get("owner_reviewed") is not True or not rule.get("approved_by") or not rule.get("rule_sha256")
            or (rule.get("source_hash"), rule.get("sheet")) != (entry["source_hash"], entry["sheet"])
            or rule.get("profile_sha256") != fingerprint(asdict(profile)) or rule.get("sheet") == "*"):
        raise ValueError("unreviewed_or_unbound_residual_rule")
    scope = SourceScope(**rule["source_scope"])
    scope.validate(profile, rule["rule_sha256"])
    expected_role = "PARENT_QUANTITY_SCOPE" if scope.fact_grain == "PARENT_CHAIN" else "CHILD_QUANTITY_SCOPE"
    if scope.fact_grain == "EXPLICIT_ROW_UNIT":
        expected_role = "EXPLICIT_ROW_QUANTITY_SCOPE"
    if scope.fact_grain == "UNKNOWN_SCOPE" or rule.get("evidence_role") != expected_role:
        raise ValueError("membership_or_context_is_not_value_scope_evidence")
    evidence = rule.get("literal_evidence", [])
    if not evidence:
        raise ValueError("independent_quantity_scope_evidence_required")
    for e in evidence:
        if (e["source_sha256"], e["sheet"]) != (entry["source_hash"], entry["sheet"]):
            raise ValueError("evidence_not_scope_bound")
        _, _, value = _cell(books, e)
        if is_formula(value) or fingerprint(value) != e["value_fingerprint"]:
            raise ValueError("quantity_scope_evidence_cell_changed")
    period, metric = entry["period"], entry["metric"]
    if not rule["period_min"] <= period <= rule["period_max"] or metric not in rule["metrics"]:
        raise ValueError("residual_rule_outside_period_metric_domain")
    n, row, value = _cell(books, {"source_sha256": entry["source_hash"], "sheet": entry["sheet"], "cell": entry["locator"]})
    if (identifier(row.get(profile.item_column)), identifier(row.get(profile.upc_column), upc=True)) != (entry["item"], entry["upc"]):
        raise ValueError("new_fact_identity_not_literal")
    fields = [f for rn, _, f, when in period_fields(profile, books[entry["source_hash"]][entry["sheet"]])
              if rn == n and when == period and f["metric"] == metric and f["column"]+str(rn) == entry["locator"]]
    status, literal = number(value)
    if (len(fields) != 1 or status != "VALUE" or is_formula(value) or literal != entry["literal"]
            or entry["locator"] in books[entry["source_hash"]][entry["sheet"]]["formulas"]):
        raise ValueError("new_fact_literal_period_metric_not_supported")
    resolver = GrainResolver([profile], {"approved": True, "rule_sha256": rule["rule_sha256"], "source_scopes": [asdict(scope)]}, memberships)
    route = resolver.route(profile, row, entry["item"], entry["upc"], period)
    if route["scope_blocking"]:
        return None
    fact = {"key": [route["fact_scope_id"], route["stable_product"], period, metric], "value": literal, "version_no": 1,
            "source_sha256": entry["source_hash"], "sheet": entry["sheet"], "cell": entry["locator"],
            "category": str(row.get(profile.category_column) or "UNCLASSIFIED"), "description": str(row.get(profile.description_column) or ""),
            "equivalent_sources": [], "available_at": None, "availability_source": "UNKNOWN", "availability_evidence_id": None,
            **{k: route[k] for k in ("fact_scope_id", "fact_grain", "canonical_code", "parent_chain", "identity_type", "item", "upc", "children", "parent_only")},
            "residual_scope_proof": {"rule_sha256": rule["rule_sha256"], "profile_sha256": rule["profile_sha256"],
                                     "source_scope": asdict(scope), "literal_evidence": evidence, "metric_field": fields[0],
                                     "description_is_identity": False, "quantity_ownership_from_membership": False}}
    fact["residual_scope_proof"]["fact_projection_sha256"] = projection_sha(fact)
    fact["residual_scope_proof"]["proof_sha256"] = fingerprint(fact["residual_scope_proof"])
    return fact


def reviewed_nonfact(entry, books, profile, rule):
    """A literal can be retired only through a separately approved exclusion."""
    if (rule.get("owner_reviewed") is not True or not rule.get("approved_by") or not rule.get("rule_sha256")
            or (rule.get("source_hash"), rule.get("sheet")) != (entry["source_hash"], entry["sheet"])
            or rule.get("profile_sha256") != fingerprint(asdict(profile))
            or rule.get("disposition") != "NON_FACT_SOURCE" or rule.get("evidence_role") != "NON_FACT_SOURCE_EXCLUSION"
            or not rule.get("exclusion_basis") or not rule.get("literal_evidence")):
        raise ValueError("unreviewed_nonfact_exclusion")
    if not rule["period_min"] <= entry["period"] <= rule["period_max"] or entry["metric"] not in rule["metrics"]:
        raise ValueError("nonfact_rule_outside_domain")
    for proof in rule["literal_evidence"]:
        if (proof["source_sha256"], proof["sheet"]) != (entry["source_hash"], entry["sheet"]):
            raise ValueError("nonfact_evidence_not_scope_bound")
        _, _, value = _cell(books, proof)
        if is_formula(value) or fingerprint(value) != proof["value_fingerprint"]:
            raise ValueError("nonfact_evidence_cell_changed")
    source = {"source_sha256": entry["source_hash"], "sheet": entry["sheet"], "cell": entry["locator"]}
    n, row, value = _cell(books, source)
    fields = [f for rn, _, f, when in period_fields(profile, books[entry["source_hash"]][entry["sheet"]])
              if rn == n and when == entry["period"] and f["metric"] == entry["metric"] and f["column"]+str(rn) == entry["locator"]]
    if (len(fields) != 1 or number(value) != ("VALUE", entry["literal"]) or is_formula(value)
            or entry["locator"] in books[entry["source_hash"]][entry["sheet"]]["formulas"]
            or (identifier(row.get(profile.item_column)), identifier(row.get(profile.upc_column), upc=True)) != (entry["item"], entry["upc"])):
        raise ValueError("nonfact_source_changed")
    return {"source": source, "period": entry["period"], "metric": entry["metric"], "creates_fact": False,
            "rule_sha256": rule["rule_sha256"], "profile_sha256": rule["profile_sha256"],
            "literal_evidence": rule["literal_evidence"], "exclusion_basis": rule["exclusion_basis"]}


def projection_sha(fact):
    return fingerprint({k: fact[k] for k in ("key", "value", "source_sha256", "sheet", "cell", "fact_scope_id", "fact_grain",
                                            "canonical_code", "parent_chain", "identity_type", "item", "upc", "available_at", "availability_source")})


def new_proof_valid(fact):
    proof = fact.get("residual_scope_proof", {})
    try:
        projection = projection_sha(fact)
    except KeyError:
        return False
    return (bool(proof.get("literal_evidence")) and proof.get("fact_projection_sha256") == projection
            and proof.get("proof_sha256") == fingerprint({k: v for k, v in proof.items() if k != "proof_sha256"})
            and proof.get("source_scope", {}).get("source_hash") == fact["source_sha256"]
            and proof.get("source_scope", {}).get("sheet") == fact["sheet"])


def trace_global_v1(baseline, candidate):
    old, new = {tuple(f["key"]): f for f in baseline["selected"]}, {tuple(f["key"]): f for f in candidate["selected"]}
    duplicates = len(candidate["selected"]) - len(new)
    missing = sorted(old.keys() - new.keys())
    changed = sorted(k for k in old.keys() & new.keys() if fingerprint(old[k]) != fingerprint(new[k]))
    trace = {"parent_dataset_sha256": baseline["summary"]["dataset_sha256"], "dataset_sha256": dataset_sha(candidate),
             "old_facts": len(old), "old_facts_preserved": len(old) - len(missing) - len(changed), "missing": missing,
             "changed_facts": changed, "changed_values": sum(Decimal(old[k]["value"]) != Decimal(new[k]["value"]) for k in old.keys() & new.keys()),
             "unsupported_regrain": len(missing), "unexpected_duplicates": duplicates, "added_keys": sorted(new.keys() - old.keys()),
             "status": "PASS" if not missing and not changed and not duplicates else "BLOCKED"}
    return trace


def validate_scope_separation(decision, proven_facts):
    """Validation only; this PRD never applies a winner or edits value conflicts."""
    if len({f["fact_scope_id"] for f in proven_facts}) < 2:
        return False
    expected = {physical(s) for s in decision["sources"]}
    covered = set()
    for f in proven_facts:
        if not new_proof_valid(f) or f["key"][2:] != decision["key"][2:] or f["fact_grain"] not in KINDS:
            return False
        covered.add(physical(f))
    return covered == expected and len(covered) == len(proven_facts)


def close_residuals(baseline, inventory, registry, books, profiles, memberships, *, rules=()):
    expected = {decision_id(d): d for d in baseline["resolution_ledger"] if d["blocking"] and d["reason_code"] in SCOPE_BLOCKS}
    if len(inventory) != len(expected) or {r["decision_id"] for r in inventory} != set(expected):
        raise ValueError("residual_blocker_silently_dropped_or_added")
    accepted_sources = {physical(s) for f in baseline["selected"] for s in [f, *f.get("equivalent_sources", [])]}
    for row in inventory:
        sources = {(s["source_hash"], s["sheet"], s["locator"]) for s in row["sources"]}
        if sources != {physical(s) for s in expected[row["decision_id"]]["sources"]} or row["canonical_key"] != expected[row["decision_id"]]["key"]:
            raise ValueError("residual_source_trace_changed")
        if sources & accepted_sources:
            raise ValueError("residual_source_cannot_regrain_frozen_fact")
    by_profile = {(p.source_hash, p.sheet_name): p for p in profiles}
    indexed_rules = defaultdict(list)
    pairs = {(s["source_hash"], s["sheet"]) for row in inventory for s in row["sources"]}
    for rule in rules:
        pair = rule.get("source_hash"), rule.get("sheet")
        if pair not in pairs:
            raise ValueError("residual_rule_must_target_residual_source")
        indexed_rules[pair].append(rule)
    ledger, proposed = [], []
    for row in inventory:
        new, dependencies, exclusions = [], [], []
        for entry in row["sources"]:
            p = by_profile[entry["source_hash"], entry["sheet"]]
            source = {"source_sha256": entry["source_hash"], "sheet": entry["sheet"], "cell": entry["locator"], "key": row["canonical_key"]}
            derived = prove_dependency(source, p, books, baseline)
            if derived:
                dependencies.append(derived)
                continue
            options = [r for r in indexed_rules[entry["source_hash"], entry["sheet"]]
                       if r["period_min"] <= entry["period"] <= r["period_max"] and entry["metric"] in r["metrics"]]
            if len(options) > 1:
                raise ValueError("ambiguous_reviewed_source_rules")
            if options and options[0].get("disposition") == "NON_FACT_SOURCE":
                exclusions.append(reviewed_nonfact(entry, books, p, options[0]))
                continue
            if options and p.value_role == "ACTUAL":
                fact = reviewed_route(entry, books, p, memberships, options[0])
                if fact:
                    new.append(fact)
        complete = len(new) + len(dependencies) + len(exclusions) == len(row["sources"])
        outcome = "BLOCKED"
        if complete and exclusions and not new and not dependencies:
            outcome = "NON_FACT_SOURCE"
        elif complete and dependencies and not new and not exclusions:
            outcome = "DERIVED_REPRESENTATION_NOT_A_FACT"
        elif complete and new and not dependencies and not exclusions and len({f["fact_scope_id"] for f in new}) == 1:
            outcome = KINDS[new[0]["fact_grain"]]
            proposed.extend((row["decision_id"], f) for f in new)
        ledger.append({"decision_id": row["decision_id"], "old_key": row["canonical_key"], "outcome": outcome,
                       "still_scope_blocking": outcome == "BLOCKED", "new_keys": [f["key"] for f in new] if complete else [],
                       "dependency_lineage": dependencies, "nonfact_exclusion_proofs": exclusions,
                       "evidence_refs": sorted({s["evidence_id"] for s in row["sources"]}),
                       "no_silent_drop": True, "winner_selected": False})
    candidate = deepcopy(baseline)
    old_by_key = {tuple(f["key"]): f for f in baseline["selected"]}
    new_by_key = defaultdict(list)
    for did, fact in proposed:
        new_by_key[tuple(fact["key"])].append((did, fact))
    pending_values, duplicates, added = [], [], []
    for key, entries in sorted(new_by_key.items()):
        if any(not new_proof_valid(f) for _, f in entries):
            raise ValueError("new_fact_proof_invalid")
        values = {Decimal(f["value"]) for _, f in entries}
        if key in old_by_key:
            values.add(Decimal(old_by_key[key]["value"]))
        if len(values) > 1:
            pending_values.append({"key": list(key), "reason": "SCOPE_RESOLVED_VALUE_CONFLICT_PENDING", "winner_selected": False,
                                   "decision_ids": sorted({did for did, _ in entries}), "sources": [physical(f) for _, f in entries]})
            continue
        if key in old_by_key:
            duplicates.extend({"decision_id": did, "key": list(key), "source": physical(f), "proof": f["residual_scope_proof"]} for did, f in entries)
            for did, _ in entries:
                next(r for r in ledger if r["decision_id"] == did)["outcome"] = "DUPLICATE_REPRESENTATION"
            continue
        chosen = sorted((f for _, f in entries), key=fingerprint)[0]
        chosen["equivalent_sources"] = [{k: f[k] for k in ("source_sha256", "sheet", "cell", "value")} for _, f in sorted(entries, key=lambda e: fingerprint(e[1]))]
        added.append(chosen)
    # Only already-blocked sources can create proposed facts. Preserve every V1
    # object and every original value-conflict decision, including their lineage.
    resolved_ids = {r["decision_id"] for r in ledger if not r["still_scope_blocking"]}
    candidate["resolution_ledger"] = [d for d in candidate["resolution_ledger"]
                                      if d["reason_code"] not in SCOPE_BLOCKS or decision_id(d) not in resolved_ids]
    candidate["selected"].extend(added)
    candidate["versions"].extend(deepcopy(added))
    assignments = {physical(f): f for _, f in proposed}
    candidate["source_assignments"] = [a for a in candidate["source_assignments"] if physical(a) not in assignments]
    catalog = {s["scope_id"]: s for s in candidate["fact_scope_catalog"]}
    for source, fact in sorted(assignments.items()):
        proof = fact["residual_scope_proof"]
        candidate["source_assignments"].append({"source_sha256": source[0], "sheet": source[1], "cell": source[2], "key": fact["key"],
            **{k: fact[k] for k in ("fact_scope_id", "fact_grain", "canonical_code", "parent_chain", "identity_type", "item", "upc", "children", "parent_only")},
            "stable_product": fact["key"][1], "scope_resolution": KINDS[fact["fact_grain"]], "scope_blocking": False,
            "scope_evidence": proof["source_scope"]["evidence"]})
        level = "PARENT_CHAIN" if fact["fact_grain"] == "PARENT_CHAIN" else "COMMERCIAL_UNIT"
        parent = fact["parent_chain"] if level != "PARENT_CHAIN" else None
        scope = {"scope_id": fact["fact_scope_id"], "code": fact["canonical_code"], "scope_type": level, "level": level,
                 "parent_code": parent, "parent_id": scope_id("PARENT_CHAIN", parent) if parent else None,
                 "aggregation_policy": "SINGLE_SCOPE_ONLY_NO_AUTOMATIC_ROLLUP"}
        previous = catalog.get(scope["scope_id"])
        if previous and previous["parent_code"] and previous["parent_code"] != parent:
            raise ValueError("new_rule_conflicts_with_frozen_scope_hierarchy")
        catalog[scope["scope_id"]] = previous or scope
        if parent:
            parent_id = scope_id("PARENT_CHAIN", parent)
            catalog.setdefault(parent_id, {"scope_id": parent_id, "code": parent, "scope_type": "PARENT_CHAIN", "level": "PARENT_CHAIN",
                "parent_code": None, "parent_id": None, "aggregation_policy": "SINGLE_SCOPE_ONLY_NO_AUTOMATIC_ROLLUP"})
    candidate["fact_scope_catalog"] = [catalog[k] for k in sorted(catalog)]
    candidate["source_assignments"].sort(key=lambda a: (a["source_sha256"], a["sheet"], a["cell"], a["key"][2:]))
    if added:
        candidate["selected"].sort(key=lambda f: (tuple(f["key"]), fingerprint(f)))
        candidate["versions"].sort(key=lambda f: (tuple(f["key"]), fingerprint(f)))
    candidate["summary"]["dataset_sha256"] = dataset_sha(candidate)
    candidate["summary"]["chains"] = len({f["fact_scope_id"] for f in candidate["selected"]})
    candidate["summary"]["products"] = len({tuple(f["key"][:2]) for f in candidate["selected"]})
    candidate["summary"]["categories"] = len({(f["key"][0], f["category"]) for f in candidate["selected"] if f["category"] != "UNCLASSIFIED"})
    candidate["summary"]["observations"] = dict(sorted(Counter(f["key"][3] for f in candidate["selected"]).items()))
    candidate["summary"]["version_rows"] = len(candidate["versions"])
    candidate["summary"]["blocking_keys"] = len({tuple(d["key"]) for d in candidate["resolution_ledger"] if d["blocking"]})
    candidate["summary"]["blocking_decisions"] = sum(d["blocking"] for d in candidate["resolution_ledger"])
    candidate["summary"]["reason_counts"] = dict(sorted(Counter(d["reason_code"] for d in candidate["resolution_ledger"]).items()))
    candidate["blocking"] = bool(candidate["summary"]["blocking_keys"] or pending_values or not candidate["selected"])
    temporal = Counter(f["availability_source"] for f in candidate["selected"])
    candidate["summary"]["temporal"] = {kind: temporal[kind] for kind in ("UNKNOWN", "E1", "E2", "E3")}
    trace = trace_global_v1(baseline, candidate)
    if trace["status"] != "PASS" or len(ledger) != len(inventory):
        raise ValueError("baseline_regression_or_untraced_blocker")
    values_before = sorted((d for d in baseline["resolution_ledger"] if d["blocking"] and d["reason_code"] in VALUE_REASONS), key=decision_id)
    values_after = sorted((d for d in candidate["resolution_ledger"] if d["blocking"] and d["reason_code"] in VALUE_REASONS), key=decision_id)
    if values_before != values_after:
        raise ValueError("original_value_conflicts_modified")
    value_trace = {"before": len(values_before), "remaining_original": len(values_after), "resolved_by_scope_separation": 0,
                   "new_pending_value_conflicts": pending_values,
                   "decisions": [{"decision_id": decision_id(d), "key": d["key"], "reason": d["reason_code"],
                                  "sources": d["sources"], "values": d["values"], "status": "STILL_VALUE_CONFLICT", "winner_selected": False} for d in values_before]}
    counts = Counter(r["outcome"] for r in ledger)
    for kind in (*KINDS.values(), "DERIVED_REPRESENTATION_NOT_A_FACT", "NON_FACT_SOURCE", "DUPLICATE_REPRESENTATION", "BLOCKED"):
        counts.setdefault(kind, 0)
    result = {"type": "GLOBAL_CORPUS_V2_CANDIDATE", "status": "BLOCKED_BY_RESIDUAL_SCOPE_EVIDENCE" if counts["BLOCKED"] else "PASS_SCOPE_CLOSED",
              "scope_resolution_counts": dict(sorted(counts.items())), "observations": len(candidate["selected"]),
              "added_facts": len(added), "remaining_scope_blockers": counts["BLOCKED"], "parent_dataset_sha256": baseline["summary"]["dataset_sha256"],
              "dataset_sha256": candidate["summary"]["dataset_sha256"], "supabase_writes": 0, "runtime_modified": False,
              "golden_v1_modified": False, "source_evidence_registry_count": len(registry)}
    return {"result": result, "canonical_candidate": candidate, "scope_resolution_ledger": ledger,
            "global_v1_to_v2_trace": trace, "value_conflict_trace": value_trace,
            "duplicate_representation_lineage": duplicates,
            "new_fact_certificates": [{"decision_id": did, "key": f["key"], "source": list(physical(f)), "proof": f["residual_scope_proof"]}
                                      for did, f in proposed]}


def grouped_review(clusters, ledger):
    unresolved = {r["decision_id"] for r in ledger if r["still_scope_blocking"]}
    groups = defaultdict(list)
    for cluster in clusters["clusters"]:
        keys = sorted(set(cluster["decision_ids"]) & unresolved)
        if keys:
            # Same structural question may span different snapshots. Exact
            # source hashes remain listed; there is no authority transfer.
            groups[tuple(sorted(sheet for _, sheet in cluster["source_pairs"])), tuple(cluster["block_types"])].append((cluster, keys))
    reviews = []
    for pattern, entries in sorted(groups.items()):
        reviews.append({"decision_id": "S-" + fingerprint(pattern)[:16], "source_pairs": sorted({p for c, _ in entries for p in c["source_pairs"]}),
                        "affected_facts": sum(len(keys) for _, keys in entries), "cluster_ids": sorted(c["cluster_id"] for c, _ in entries),
                        "period_min": min(c["period_min"] for c, _ in entries), "period_max": max(c["period_max"] for c, _ in entries),
                        "candidate_parents": sorted({p for c, _ in entries for p in c["candidate_parents"]}),
                        "candidate_units": sorted({u for c, _ in entries for u in c["candidate_children"]}),
                        "evidence_available": ["Pinned source/profile", "Literal metric cells", "Separate parent context and commercial membership"],
                        "evidence_missing": "Reviewed quantity ownership of these literals, or exact dependency proving non-fact representation",
                        "possible_interpretations": ["Parent quantity", "Independently owned commercial-unit quantity", "Copied/derived non-independent quantity"],
                        "risk": "Wrong allocation, duplicated demand or loss of a genuine independent quantity",
                        "question": "Which quantity scope do the literal cells represent? Provide a hash/sheet-bound source declaration or direct cell lineage, separately for each listed source.",
                        "status": "BLOCKED"})
    return reviews
