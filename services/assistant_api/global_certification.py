"""Opt-in, full-corpus literal/identity/grain certification. No runtime or writes."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json

from services.assistant_api.business_membership import certify_scopes, extract_business_memberships
from services.assistant_api.chain_ingestion import identifier, is_formula
from services.assistant_api.hierarchical_grain import GrainResolver, SCOPE_BLOCKS, VALUE_REASONS, coverage, period_fields
from services.assistant_api.historical_corpus import number
from services.assistant_api.source_adjudication import fingerprint

CERTIFIER_VERSION = "global-hierarchical-certifier/1.2"
METRICS = ("SALES", "ORDER", "DELIVERY")
ACCEPTED_GRAINS = {"PARENT_CHAIN", "COMMERCIAL_UNIT", "EXPLICIT_ROW_UNIT"}


def dataset_sha(report):
    return hashlib.sha256(json.dumps(report["versions"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _physical(source):
    return source["source_sha256"], source["sheet"], source["cell"]


def _source_proofs(books, profiles, membership_authority, scope_authority):
    """Re-read cells and approved structure; never trust report assignments."""
    adapted, business = certify_scopes(books, profiles, membership_authority, allow_empty=True)
    members = extract_business_memberships(books, adapted, business)
    resolver = GrainResolver(adapted, scope_authority, members)
    proofs = defaultdict(list)
    for profile in sorted(adapted, key=lambda p: (p.source_hash, p.sheet_name)):
        profile.validate(profile.source_hash, set(books[profile.source_hash]))
        if profile.value_role != "ACTUAL":
            continue
        sheet = books[profile.source_hash][profile.sheet_name]
        scope = resolver.scopes[profile.source_hash, profile.sheet_name]
        scope_record, profile_sha = asdict(scope), fingerprint(asdict(profile))
        scope_sha = fingerprint(scope_record)
        for row_no, row, field, period in period_fields(profile, sheet, period_min=scope_authority.get("period_min", "0000-01"),
                                                       period_max=scope_authority.get("period_max", "9999-12")):
            column, metric = field["column"], field["metric"]
            locator = column + str(row_no)
            value = row.get(column)
            status, literal = number(value)
            if status != "VALUE" or is_formula(value) or locator in sheet["formulas"]:
                continue
            item = identifier(row.get(profile.item_column))
            upc = identifier(row.get(profile.upc_column), upc=True)
            if not (item or upc):
                continue
            route = resolver.route(profile, row, item, upc, period)
            identity = {"basis": "LITERAL_PRODUCT_IDENTIFIERS", "item": item, "upc": upc,
                        "item_locator": profile.item_column + str(row_no) if item else None,
                        "upc_locator": profile.upc_column + str(row_no) if upc else None,
                        "stable_product": route["stable_product"], "description_is_identity": False}
            if route["stable_product"] not in {item, upc}:
                bridge = resolver.identity_index.route(profile.source_hash, item, upc, explicit_chain=route["canonical_code"], period=period)
                identity.update(basis="APPROVED_SOURCE_PRODUCT_UNIT_PERIOD_IDENTITY_BRIDGE_ONLY", bridge=bridge,
                                quantity_ownership_granted_by_bridge=False)
            proof = {"key": [route["fact_scope_id"], route["stable_product"], period, metric],
                     "literal": literal, "route": route, "identity_evidence": identity,
                     "grain_evidence": {"source_scope_sha256": scope_sha, "fact_grain": route["fact_grain"],
                                        "sheet_owned_value_certified": scope.evidence.get("value_scope_certified", False),
                                        "chain_locator": profile.chain_column + str(row_no) if profile.chain_column else None,
                                        "format_locator": profile.format_column + str(row_no) if profile.format_column else None,
                                        "membership_is_not_quantity_authority": True},
                     "scope_evidence": scope_record,
                     "period_evidence": {"period": period, "field": field, "profile_sha256": profile_sha,
                                         "date_locator": field.get("date_column", "") + str(row_no) if field.get("date_column") else None,
                                         "reviewed_profile_evidence": profile.evidence},
                     "metric_evidence": {"metric": metric, "column": column, "profile_sha256": profile_sha}}
            proofs[profile.source_hash, profile.sheet_name, locator, period, metric].append(proof)
    return proofs, resolver


def blocked_trace(report):
    """One trace row per blocked key, grouped questions rather than per-cell asks."""
    catalog = {s["scope_id"]: s for s in report["fact_scope_catalog"]}
    traces, groups = [], defaultdict(list)
    for decision in report["resolution_ledger"]:
        if not decision["blocking"] or len(decision["key"]) != 4:
            continue
        key, reason = decision["key"], decision["reason_code"]
        kind = "FACT_SCOPE" if reason in SCOPE_BLOCKS else "VALUE" if reason in VALUE_REASONS else "OTHER"
        sources = sorted(decision["sources"], key=fingerprint)
        source_pairs = sorted({(s["source_sha256"], s["sheet"]) for s in sources})
        trace = {"decision_id": fingerprint([key, reason, sources]), "canonical_key": key, "kind": kind,
                 "scope_id": key[0], "chain": catalog.get(key[0], {}).get("code"), "metric": key[3], "period": key[2],
                 "reason": reason, "sources": sources, "source_pairs": source_pairs,
                 "value_fingerprints": sorted(fingerprint(v) for v in decision["values"]), "winner_selected": False}
        traces.append(trace)
        groups[kind, key[0], reason, tuple(source_pairs)].append(trace)
    grouped = []
    for (kind, sid, reason, pairs), rows in sorted(groups.items()):
        grouped.append({"group_id": fingerprint([kind, sid, reason, pairs]), "kind": kind, "scope_id": sid,
                        "chain": rows[0]["chain"], "source_pairs": pairs, "reason": reason,
                        "metric_counts": dict(sorted(Counter(r["metric"] for r in rows).items())),
                        "period_min": min(r["period"] for r in rows), "period_max": max(r["period"] for r in rows),
                        "blocked_keys": len(rows), "decisions": sorted(r["decision_id"] for r in rows),
                        "status": "BLOCKED", "winner_selected": False,
                        "question": "Provide independent literal quantity scope evidence; membership is not ownership." if kind == "FACT_SCOPE"
                        else "Provide reviewed source/version authority for these contradictory values; no automatic winner."})
    return {"counts": dict(sorted(Counter(t["kind"] for t in traces).items())),
            "decisions": sorted(traces, key=lambda r: r["decision_id"]), "groups": grouped}


def _product_coverage(report, resolver):
    facts, _ = coverage(report, resolver)
    selected = defaultdict(list)
    for f in report["selected"]:
        selected[tuple(f["key"][:2])].append(f)
    rows = []
    for entry in facts["series"]:
        fs = selected.get((entry["scope_id"], entry["stable_product"]), [])
        counts = Counter(f["key"][3] for f in fs)
        rows.append({**entry, "facts": len(fs), "metric_counts": {m: counts[m] for m in METRICS},
                     "period_min": min((f["key"][2] for f in fs), default=None),
                     "period_max": max((f["key"][2] for f in fs), default=None)})
    return {"actual_scope_product_identities": len(selected), "all_classified_series": len(rows),
            "actual_eligibility_counts": dict(sorted(Counter(r["eligibility"] for r in rows if r["facts"]).items())),
            "all_eligibility_counts": dict(sorted(Counter(r["eligibility"] for r in rows).items())), "series": rows}


def certify_global_corpus(report, books, profiles, membership_authority, scope_authority, *, verified_source_hashes):
    """100% fail-closed certification; no fixed retailer, product or count list."""
    proofs, resolver = _source_proofs(books, profiles, membership_authority, scope_authority)
    catalog = {s["scope_id"]: s for s in report["fact_scope_catalog"]}
    selected, rows = report["selected"], []
    key_counts = Counter(tuple(f["key"]) for f in selected)
    blocked = blocked_trace(report)
    blocked_keys = {tuple(t["canonical_key"]) for t in blocked["decisions"]}
    source_scopes = defaultdict(set)
    global_issues = []
    if dataset_sha(report) != report["summary"]["dataset_sha256"]:
        global_issues.append("DATASET_FINGERPRINT_MISMATCH")
    for fact in sorted(selected, key=lambda f: (tuple(f["key"]), fingerprint(f))):
        key, issues, lineage = fact["key"], [], []
        primary = None
        sources = {_physical(s): s for s in [fact, *fact.get("equivalent_sources", [])]}
        for physical, source in sorted(sources.items()):
            source_scopes[physical].add(key[0])
            if physical[0] not in verified_source_hashes or physical[0] not in books or physical[1] not in books[physical[0]]:
                issues.append("UNKNOWN_OR_UNVERIFIED_SOURCE")
                continue
            options = proofs.get((*physical, *key[2:]), [])
            matching = [p for p in options if p["key"] == key]
            if len(matching) != 1:
                issues.append("UNSUPPORTED_IDENTITY_PERIOD_METRIC_OR_SCOPE")
                continue
            proof = matching[0]
            if Decimal(proof["literal"]) != Decimal(fact["value"]):
                issues.append("LITERAL_MISMATCH")
            route = proof["route"]
            if route["scope_blocking"] or route["fact_grain"] not in ACCEPTED_GRAINS:
                issues.append("UNSUPPORTED_SCOPE")
            lineage.append({"source_hash": physical[0], "sheet": physical[1], "locator": physical[2],
                            "literal_fingerprint": fingerprint(proof["literal"]), "identity_evidence": proof["identity_evidence"],
                            "grain_evidence": proof["grain_evidence"], "scope_evidence": proof["scope_evidence"],
                            "period_evidence": proof["period_evidence"], "metric_evidence": proof["metric_evidence"]})
            if physical == _physical(fact):
                primary = proof
        if primary is None:
            issues.append("PRIMARY_LITERAL_NOT_SUPPORTED")
        elif (primary["route"]["fact_grain"] != fact["fact_grain"] or primary["route"]["canonical_code"] != fact["canonical_code"]
              or primary["route"]["fact_scope_id"] != fact["fact_scope_id"] or fact["fact_scope_id"] != key[0]):
            issues.append("UNSUPPORTED_SCOPE_PROMOTION_OR_GRAIN_CHANGE")
        if (key[0] not in catalog or catalog[key[0]]["code"] != fact["canonical_code"]
                or (catalog[key[0]]["scope_type"] == "PARENT_CHAIN") != (fact["fact_grain"] == "PARENT_CHAIN")):
            issues.append("SCOPE_CATALOG_MISMATCH")
        if key_counts[tuple(key)] != 1:
            issues.append("DUPLICATE_CANONICAL_FACT")
        if tuple(key) in blocked_keys:
            issues.append("BLOCKED_FACT_SELECTED")
        if key[3] not in METRICS:
            issues.append("UNSUPPORTED_METRIC")
        if fact["availability_source"] != "UNKNOWN" or fact["available_at"] is not None:
            issues.append("HISTORICAL_AVAILABILITY_POLICY_VIOLATION")
        row = {"canonical_key": key, "fact_scope_id": key[0], "scope_type": fact["fact_grain"],
               "stable_product": key[1], "period": key[2], "metric": key[3], "value_fingerprint": fingerprint(fact["value"]),
               "source_hash": fact["source_sha256"], "sheet": fact["sheet"], "locator": fact["cell"],
               "grain_evidence": primary["grain_evidence"] if primary else None,
               "scope_evidence": primary["scope_evidence"] if primary else None,
               "identity_evidence": primary["identity_evidence"] if primary else None,
               "source_lineage": lineage, "availability_source": fact["availability_source"], "available_at": fact["available_at"],
               "certification_status": "BLOCKED" if issues else "PASS", "issues": sorted(set(issues))}
        rows.append(row)
    fanout = sum(len(scopes) > 1 for scopes in source_scopes.values())
    if fanout:
        global_issues.append("ONE_SOURCE_FACT_MULTIPLE_SCOPES")
        for row in rows:
            if any(len(source_scopes[s["source_hash"], s["sheet"], s["locator"]]) > 1 for s in row["source_lineage"]):
                row["issues"] = sorted(set(row["issues"]) | {"ONE_SOURCE_FACT_MULTIPLE_SCOPES"})
                row["certification_status"] = "BLOCKED"
    by_scope = defaultdict(list)
    for row in rows:
        by_scope[row["fact_scope_id"]].append(row)
    products = _product_coverage(report, resolver)
    scopes = []
    for sid, entries in sorted(by_scope.items()):
        c = catalog.get(sid, {})
        counts = Counter(r["metric"] for r in entries)
        scopes.append({"scope_id": sid, "scope_type": c.get("scope_type", "UNKNOWN_SCOPE"), "code": c.get("code"),
                       "parent": c.get("parent_code"), "commercial_unit": c.get("code") if c.get("scope_type") == "COMMERCIAL_UNIT" else None,
                       "products": len({r["stable_product"] for r in entries}), "metric_counts": {m: counts[m] for m in METRICS},
                       "actual_product_eligibility": dict(sorted(Counter(p["eligibility"] for p in products["series"]
                                                                         if p["scope_id"] == sid and p["facts"]).items())),
                       "total_facts": len(entries), "certified_facts": sum(r["certification_status"] == "PASS" for r in entries),
                       "period_min": min(r["period"] for r in entries), "period_max": max(r["period"] for r in entries),
                       "source_hash_count": len({s["source_hash"] for r in entries for s in r["source_lineage"]}),
                       "source_sheet_count": len({(s["source_hash"], s["sheet"]) for r in entries for s in r["source_lineage"]}),
                       "literal_mismatch_count": sum("LITERAL_MISMATCH" in r["issues"] for r in entries),
                       "unsupported_scope_count": sum(any("SCOPE" in issue or "SUPPORTED" in issue for issue in r["issues"]) for r in entries),
                       "duplicate_fact_count": sum(key_counts[tuple(r["canonical_key"])] > 1 for r in entries),
                       "temporal": {level: sum(r["availability_source"] == level for r in entries) for level in ("UNKNOWN", "E1", "E2", "E3")},
                       "status": "PASS" if all(r["certification_status"] == "PASS" for r in entries) else "BLOCKED"})
    counts = Counter(r["metric"] for r in rows)
    summary = {"selected_observations": len(selected), "certified_observations": sum(r["certification_status"] == "PASS" for r in rows),
               "uncertified_selected": sum(r["certification_status"] != "PASS" for r in rows),
               "duplicate_canonical_facts": sum(n - 1 for n in key_counts.values()),
               "literal_facts_checked": len(rows), "literal_matches": sum(r["grain_evidence"] is not None and "LITERAL_MISMATCH" not in r["issues"] for r in rows),
               "literal_mismatches": sum("LITERAL_MISMATCH" in r["issues"] for r in rows),
               "unsupported_selected_scopes": sum(any("SCOPE" in issue or "SUPPORTED" in issue for issue in r["issues"]) for r in rows),
               "unknown_scope_selected": sum(r["scope_type"] not in ACCEPTED_GRAINS for r in rows),
               "facts_fanned_out_due_to_membership": fanout, "actual_bearing_scopes": len(scopes),
               "scopes_pass": sum(s["status"] == "PASS" for s in scopes), "scopes_blocked": sum(s["status"] == "BLOCKED" for s in scopes),
               "scope_product_identities": len({tuple(r["canonical_key"][:2]) for r in rows}),
               "categories": len({(f["key"][0], f["category"]) for f in selected
                                  if f.get("category") and f["category"] != "UNCLASSIFIED"}),
               "metric_counts": {m: counts[m] for m in METRICS}, "grain_counts": dict(sorted(Counter(r["scope_type"] for r in rows).items())),
               "temporal": {level: sum(r["availability_source"] == level for r in rows) for level in ("UNKNOWN", "E1", "E2", "E3")},
               "fabricated_available_at": sum(r["available_at"] is not None for r in rows),
               "blocked_facts_selected": sum(tuple(r["canonical_key"]) in blocked_keys for r in rows), "blocked_counts": blocked["counts"],
               "period_min": min((r["period"] for r in rows), default=None), "period_max": max((r["period"] for r in rows), default=None)}
    if sum(s["total_facts"] for s in scopes) != len(selected) or sum(counts[m] for m in METRICS) != len(selected):
        global_issues.append("SCOPE_OR_METRIC_ACCOUNTING_MISMATCH")
    if not rows:
        global_issues.append("EMPTY_CORPUS_NOT_CERTIFIABLE")
    certified = {tuple(r["canonical_key"]): r for r in rows if r["certification_status"] == "PASS"}
    families = defaultdict(list)
    for f in selected:
        if tuple(f["key"]) not in certified:
            continue
        c = catalog[f["key"][0]]
        parent = c["code"] if c["scope_type"] == "PARENT_CHAIN" else c.get("parent_code")
        if not parent and f["fact_grain"] == "COMMERCIAL_UNIT":
            # A reviewed sheet SourceScope may establish the parent relation
            # without a row-unit catalogue link. Never infer it from a name.
            parent = certified[tuple(f["key"])]["scope_evidence"]["parent_chain"]
        if parent and (f.get("item") or f.get("upc")):
            families[parent, f.get("item") or f.get("upc"), *f["key"][2:]].append(f)
    independent = []
    for family, facts in sorted(families.items()):
        if any(f["fact_grain"] == "PARENT_CHAIN" for f in facts) and any(f["fact_grain"] != "PARENT_CHAIN" for f in facts):
            independent.append({"relationship": "INDEPENDENT_SCOPE_REPRESENTATIONS", "family_identity": family,
                                "canonical_keys": sorted(f["key"] for f in facts), "automatic_aggregation_allowed": False,
                                "proof": "EVERY_SOURCE_LITERAL_INDEPENDENTLY_SCOPE_CERTIFIED"})
    payload = {"certifier_version": CERTIFIER_VERSION, "dataset_sha256": report["summary"]["dataset_sha256"],
               "summary": summary, "rows": rows, "scope_summary": scopes, "blocked_trace": blocked,
               "product_coverage": products, "independent_scope_representations": independent,
               "non_actual_scopes": [{**catalog[sid], "status": "BLOCKED", "reason": "NO_ACCEPTED_ACTUALS_CATALOG_OR_PENDING_EVIDENCE"}
                                     for sid in sorted(set(catalog) - set(by_scope))],
               "source_fingerprints": sorted(verified_source_hashes), "global_issues": sorted(set(global_issues))}
    payload["status"] = "PASS" if not global_issues and summary["uncertified_selected"] == 0 else "BLOCKED"
    payload["certification_sha256"] = fingerprint(payload)
    return payload


def freeze_global_golden(certification, *, certified_at):
    if certification["status"] != "PASS":
        raise ValueError("cannot_freeze_failed_global_certification")
    payload = {k: v for k, v in certification.items() if k != "certification_sha256"}
    if fingerprint(payload) != certification["certification_sha256"]:
        raise ValueError("certification_content_changed")
    rows = certification["rows"]
    summary = certification["summary"]
    return {"type": "GLOBAL_HIERARCHICAL_CORPUS_GOLDEN", "dataset_sha256": certification["dataset_sha256"],
            "facts": len(rows), "scope_count": summary["actual_bearing_scopes"], "scope_product_count": summary["scope_product_identities"],
            "metric_counts": summary["metric_counts"], "period_range": [summary["period_min"], summary["period_max"]],
            "scope_fingerprints": {sid: fingerprint([r for r in rows if r["fact_scope_id"] == sid]) for sid in sorted({r["fact_scope_id"] for r in rows})},
            "source_fingerprints": certification["source_fingerprints"], "certification_timestamp": certified_at,
            "certifier_version": CERTIFIER_VERSION, "certification_sha256": certification["certification_sha256"],
            "fact_fingerprints": [{"canonical_key": r["canonical_key"], "sha256": fingerprint(r)} for r in rows],
            "frozen": True, "available_at_is_not_certification_timestamp": True}


def compare_global_golden(golden, certification):
    old = {tuple(r["canonical_key"]): r["sha256"] for r in golden["fact_fingerprints"]}
    new = {tuple(r["canonical_key"]): fingerprint(r) for r in certification["rows"]}
    payload = {k: v for k, v in certification.items() if k != "certification_sha256"}
    result = {"missing_keys": sorted(old.keys() - new.keys()), "new_keys": sorted(new.keys() - old.keys()),
              "changed_fact_fingerprints": sorted(k for k in old.keys() & new.keys() if old[k] != new[k]),
              "current_content_valid": fingerprint(payload) == certification["certification_sha256"],
              "unique_current_keys": len(new) == len(certification["rows"]),
              "certification_fingerprint_equal": golden["certification_sha256"] == certification["certification_sha256"]}
    result["status"] = "PASS" if (not any(result[k] for k in ("missing_keys", "new_keys", "changed_fact_fingerprints"))
                                 and result["certification_fingerprint_equal"] and result["current_content_valid"]
                                 and result["unique_current_keys"] and certification["status"] == "PASS") else "BLOCKED"
    result["changes_require_explicit_reviewed_evidence"] = True
    return result
