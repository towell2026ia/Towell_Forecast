"""Opt-in certified bootstrap planning and remote readback checks; no runtime wiring.

This module never opens a network connection or mutates a database. Administrative
execution must separately attest the repository, account, schema and actor gates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import re
from uuid import UUID, uuid5

from services.assistant_api.global_certification import dataset_sha
from services.assistant_api.source_adjudication import fingerprint

BOOTSTRAP_KIND = "HISTORICAL_CERTIFIED_BOOTSTRAP_V1"
BACKLOG_KIND = "HISTORICAL_BACKLOG_V1"
NAMESPACE = UUID("6974888e-7e37-493b-a345-8cd5261db0b4")
METRICS = ("SALES", "ORDER", "DELIVERY")
GRAINS = {"PARENT_CHAIN", "COMMERCIAL_UNIT", "EXPLICIT_ROW_UNIT"}
TABLES = frozenset({
    "chains", "profiles", "user_chain_access", "source_evidence", "import_profiles",
    "import_profile_versions", "import_batches", "categories", "products", "monthly_observations",
    "customer_forecast_versions", "customer_forecast_rows", "research_snapshots", "research_snapshot_sources",
    "forecast_runs", "model_versions", "forecast_vintages", "forecast_horizons", "forecast_aggregates",
    "champion_registry", "actual_evaluations", "performance_metrics", "forecast_decisions", "jobs",
    "run_logs", "audit_log", "forecast_run_inputs", "legacy_identity_map",
})
BUCKETS = frozenset({"source-files", "research-evidence", "model-artifacts", "exports"})


def uid(kind, key):
    """Technical UUIDs only. Business identity never depends on a description."""
    return str(uuid5(NAMESPACE, fingerprint([kind, key])))


def safe_metadata(value):
    """Fail closed, rather than silently redact certified lineage."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"password", "access_token", "service_role_key", "connection_string"}:
                raise ValueError("secret_metadata_forbidden")
            safe_metadata(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            safe_metadata(item)
    elif isinstance(value, str) and (re.search(r"[A-Za-z]:[\\/]", value) or value.startswith("\\\\")):
        raise ValueError("local_path_metadata_forbidden")
    return value


def validate_corpus(report, cert, expected):
    """Certification metadata is necessary, but never substitutes for literal replay."""
    if cert.get("status") != "PASS" or cert.get("global_issues"):
        raise ValueError("corpus_not_certified")
    unsealed = {k: v for k, v in cert.items() if k != "certification_sha256"}
    if fingerprint(unsealed) != cert["certification_sha256"]:
        raise ValueError("certification_content_changed")
    if dataset_sha(report) != expected["dataset_sha256"] or cert["dataset_sha256"] != expected["dataset_sha256"]:
        raise ValueError("dataset_sha_mismatch")
    if cert["certification_sha256"] != expected["certification_sha256"]:
        raise ValueError("certification_sha_mismatch")
    facts, versions = report["selected"], report["versions"]
    if facts != versions:
        raise ValueError("unpublished_versions_not_bootstrap_eligible")
    summary = cert["summary"]
    for field in ("selected_observations", "actual_bearing_scopes", "scope_product_identities", "categories",
                  "metric_counts", "grain_counts", "blocked_counts"):
        if summary[field] != expected[field]:
            raise ValueError("certified_count_mismatch_" + field)
    for field in ("uncertified_selected", "duplicate_canonical_facts", "literal_mismatches",
                  "unsupported_selected_scopes", "unknown_scope_selected", "blocked_facts_selected", "fabricated_available_at"):
        if summary[field] != 0:
            raise ValueError("unsafe_corpus_" + field)
    if summary["literal_matches"] != len(facts) or summary["certified_observations"] != len(facts):
        raise ValueError("incomplete_literal_certification")
    rows = {tuple(r["canonical_key"]): r for r in cert["rows"]}
    if len(rows) != len(facts) or len({tuple(f["key"]) for f in facts}) != len(facts):
        raise ValueError("duplicate_or_missing_certified_key")
    blocked = {tuple(d["canonical_key"]) for d in cert["blocked_trace"]["decisions"]}
    for fact in facts:
        row = rows.get(tuple(fact["key"]))
        if tuple(fact["key"]) in blocked:
            raise ValueError("blocked_fact_selected")
        if (not row or row["certification_status"] != "PASS" or row["issues"]
                or row["value_fingerprint"] != fingerprint(fact["value"])
                or row["scope_type"] != fact["fact_grain"] or fact["fact_grain"] not in GRAINS
                or (row["source_hash"], row["sheet"], row["locator"]) !=
                   (fact["source_sha256"], fact["sheet"], fact["cell"])):
            raise ValueError("fact_certification_mismatch")
        if fact["available_at"] is not None or fact["availability_source"] != "UNKNOWN":
            raise ValueError("historical_availability_fabricated")
        if Decimal(fact["value"]) < 0 or not Decimal(fact["value"]).is_finite():
            raise ValueError("invalid_certified_value")
    return {"status": "PASS", "observations": len(facts), "dataset_sha256": expected["dataset_sha256"],
            "certification_sha256": expected["certification_sha256"]}


def backlog_manifest(cert):
    rows = []
    for decision in cert["blocked_trace"]["decisions"]:
        if decision["kind"] not in {"FACT_SCOPE", "VALUE"}:
            raise ValueError("unclassified_backlog")
        sources = deepcopy(decision["sources"])
        rows.append({"backlog_id": decision["decision_id"], "kind": decision["kind"],
                     "canonical_key": decision["canonical_key"], "reason": decision["reason"],
                     "source_hashes": sorted({s["source_sha256"] for s in sources}),
                     "lineage_fingerprints": sorted(fingerprint(s) for s in sources), "sources": sources,
                     "original_dataset_sha": cert["dataset_sha256"],
                     "original_certification_sha": cert["certification_sha256"],
                     "status": "PENDING_FUTURE_RECONCILIATION"})
    if len({r["backlog_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate_backlog_id")
    counts = dict(sorted(Counter(r["kind"] for r in rows).items()))
    if counts != cert["summary"]["blocked_counts"]:
        raise ValueError("backlog_count_mismatch")
    return safe_metadata({"kind": BACKLOG_KIND, "counts": counts, "total": len(rows),
                          "rows": sorted(rows, key=lambda r: r["backlog_id"])})


def build_plan(report, cert, expected, *, code_commit, bootstrap_at):
    """Plan only; persisted timestamp must be reused on retry, never regenerated."""
    validate_corpus(report, cert, expected)
    if not re.fullmatch(r"[0-9a-f]{40}", code_commit):
        raise ValueError("full_commit_required")
    when = datetime.fromisoformat(bootstrap_at.replace("Z", "+00:00"))
    if when.utcoffset() is None:
        raise ValueError("bootstrap_timestamp_timezone_required")
    bindings = {"kind": BOOTSTRAP_KIND, "dataset_sha": expected["dataset_sha256"],
                "certification_sha": expected["certification_sha256"],
                "source_hashes": sorted(cert["source_fingerprints"]), "code_commit": code_commit,
                "bootstrap_at": bootstrap_at}
    bootstrap_id = uid("bootstrap", bindings)
    manifest = {**bindings, "bootstrap_id": bootstrap_id, "status": "STAGING",
                "observation_count": expected["selected_observations"], "scope_count": expected["actual_bearing_scopes"],
                "product_scope_count": expected["scope_product_identities"], "category_count": expected["categories"],
                "excluded_scope_count": expected["blocked_counts"].get("FACT_SCOPE", 0),
                "excluded_value_count": expected["blocked_counts"].get("VALUE", 0)}
    catalog = {s["scope_id"]: deepcopy(s) for s in report["fact_scope_catalog"]}
    actual = {f["key"][0] for f in report["selected"]}
    needed = set(actual)
    for sid in list(needed):
        parent = catalog[sid].get("parent_id")
        if parent:
            if parent not in catalog or catalog[parent]["scope_type"] != "PARENT_CHAIN":
                raise ValueError("unproven_parent_relationship")
            needed.add(parent)
    scopes = []
    for sid in sorted(needed):
        entry = catalog[sid]
        if entry["scope_type"] not in {"PARENT_CHAIN", "COMMERCIAL_UNIT"} or not entry["code"]:
            raise ValueError("unproven_scope_master")
        scopes.append({**entry, "chain_uuid": uid("scope", sid), "sql_code": sid.upper(),
                       "status": "ACTIVE", "actual_bearing": sid in actual,
                       "parent_uuid": uid("scope", entry["parent_id"]) if entry.get("parent_id") else None})
    categories = []
    # UNCLASSIFIED is missing evidence, not a 51st category. No fuzzy/case merge.
    pairs = {(f["key"][0], f["category"]) for f in report["selected"]
             if f.get("category") and f["category"] != "UNCLASSIFIED"}
    for sid, name in sorted(pairs):
        categories.append({"id": uid("category", [sid, name]), "chain_id": uid("scope", sid),
                           "scope_id": sid, "code": "CAT-" + fingerprint([sid, name])[:24].upper(), "name": name})
    grouped = defaultdict(list)
    for f in report["selected"]:
        grouped[tuple(f["key"][:2])].append(f)
    products = []
    for (sid, code), unsorted in sorted(grouped.items()):
        fs = sorted(unsorted, key=lambda f: (tuple(f["key"]), fingerprint(f)))
        names = sorted({f["category"] for f in fs if f.get("category") and f["category"] != "UNCLASSIFIED"})
        products.append({"id": uid("product", [sid, code, None]), "chain_id": uid("scope", sid),
                         "scope_id": sid, "product_code": code, "variant_code": None,
                         # Multi-category history is kept per fact; never choose an arbitrary current category.
                         "category_id": uid("category", [sid, names[0]]) if len(names) == 1 else None,
                         "historical_category_names": names, "description": fs[0]["description"],
                         "description_aliases": sorted({f["description"] for f in fs}),
                         "first_seen_period": min(f["key"][2] for f in fs) + "-01",
                         "last_seen_period": max(f["key"][2] for f in fs) + "-01",
                         "identifier_mappings": sorted({(f.get("item"), f.get("upc")) for f in fs}, key=fingerprint)})
    batches = [{"id": uid("batch", [bootstrap_id, sid]), "scope_id": sid, "chain_id": uid("scope", sid),
                "profile_id": uid("profile", [bootstrap_id, sid]), "profile_version_id": uid("profile_version", [bootstrap_id, sid]),
                "sha256": fingerprint([bootstrap_id, sid, expected["dataset_sha256"]]),
                "filename": BOOTSTRAP_KIND + ".json", "status": "UPLOADED",
                "row_count": sum(f["key"][0] == sid for f in report["selected"]),
                "period": cert["summary"]["period_max"] + "-01"} for sid in sorted(actual)]
    certificates = {tuple(r["canonical_key"]): r for r in cert["rows"]}
    chunks = []
    for f in report["versions"]:
        key = f["key"]
        proof = certificates[tuple(key)]
        chunks.append({"id": uid("observation", [bootstrap_id, key]), "chain_id": uid("scope", key[0]),
                       "product_id": uid("product", [*key[:2], None]), "period": key[2] + "-01", "metric_code": key[3],
                       "value": f["value"], "version_no": f["version_no"], "available_at": None,
                       "availability_source": "UNKNOWN", "source_batch_id": uid("batch", [bootstrap_id, key[0]]),
                       "availability_evidence_id": uid("evidence", [bootstrap_id, key]),
                       "lineage": {"bootstrap_id": bootstrap_id, "canonical_fact": deepcopy(f),
                                   "certification_sha": cert["certification_sha256"],
                                   "proof_fingerprint": fingerprint(proof),
                                   "source_lineage_fingerprints": sorted(fingerprint(s) for s in proof["source_lineage"])}})
    plan = safe_metadata({"manifest": manifest, "scopes": scopes, "categories": categories,
                          "products": products, "batches": batches, "observations": chunks,
                          "scope_summary": cert["scope_summary"], "backlog": backlog_manifest(cert)})
    if len(categories) != expected["categories"] or len(products) != expected["scope_product_identities"]:
        raise ValueError("master_count_mismatch")
    plan["plan_sha"] = fingerprint(plan)
    return plan


def validate_remote_gate(snapshot, *, expected_project):
    """Fail closed on extra migrations, changed table inventory, or public storage."""
    if snapshot["project_ref"] != expected_project:
        raise ValueError("wrong_supabase_project")
    if snapshot["migrations"] != ["20260925000" + str(i) for i in range(1, 8)]:
        raise ValueError("remote_migration_difference")
    tables = snapshot["tables"]
    if {t["name"] for t in tables} != TABLES or len(tables) != len(TABLES):
        raise ValueError("remote_table_difference")
    if not all(t["rls_enabled"] for t in tables):
        raise ValueError("rls_not_enabled")
    if snapshot["views"] != ["monthly_observations_current"]:
        raise ValueError("remote_view_difference")
    buckets = snapshot["buckets"]
    if {b["id"] for b in buckets} != BUCKETS or any(b["public"] for b in buckets):
        raise ValueError("storage_not_private")
    return {"project": "PASS", "migrations": "PASS", "tables": "PASS", "rls": "PASS", "buckets": "PASS"}


def verify_remote_facts(remote, plan):
    """Hash independently measured business facts, not UUIDs or a copied manifest.

The frozen hash includes lineage/category/alias metadata. Reconstruct that format
only AFTER checking each real observation and joined chain/product/evidence/batch.
Database numeric formatting (e.g. 4.000) is semantically equivalent to literal 4.
"""
    expected = {r["id"]: r for r in plan["observations"]}
    if len(remote) != len(expected) or len({r["id"] for r in remote}) != len(remote):
        raise ValueError("remote_count_or_duplicate")
    reconstructed, keys, physical = [], set(), set()
    for row in remote:
        reference = expected.get(row["id"])
        if not reference:
            raise ValueError("unrelated_remote_fact")
        canonical = row["lineage"]["canonical_fact"]
        if row["lineage"] != reference["lineage"]:
            raise ValueError("remote_lineage_changed")
        for field in ("chain_id", "product_id", "period", "metric_code", "version_no", "available_at",
                      "availability_source", "source_batch_id", "availability_evidence_id"):
            if row[field] != reference[field]:
                raise ValueError("remote_fact_changed_" + field)
        if (row["scope_code"] != canonical["key"][0].upper() or row["product_code"] != canonical["key"][1]
                or row["variant_code"] is not None or row["evidence_source_hash"] != canonical["source_sha256"]
                or row["batch_status"] != "IMPORTED" or Decimal(str(row["value"])) != Decimal(canonical["value"])):
            raise ValueError("remote_business_identity_value_or_lineage_changed")
        key = tuple(canonical["key"])
        if key in keys:
            raise ValueError("remote_duplicate_business_key")
        keys.add(key)
        physical.add((canonical["source_sha256"], canonical["sheet"], canonical["cell"], *key[2:]))
        physical.update((s["source_sha256"], s["sheet"], s["cell"], *key[2:]) for s in canonical["equivalent_sources"])
        reconstructed.append(canonical)
    # Preserve the original deterministic business-key order, not remote UUID order.
    reconstructed.sort(key=lambda f: tuple(f["key"]))
    digest = dataset_sha({"versions": reconstructed})
    if digest != plan["manifest"]["dataset_sha"]:
        raise ValueError("remote_dataset_sha_mismatch")
    counts = Counter()
    for b in plan["backlog"]["rows"]:
        if tuple(b["canonical_key"]) in keys or any(
                (s["source_sha256"], s["sheet"], s["cell"], *b["canonical_key"][2:]) in physical for s in b["sources"]):
            counts[b["kind"]] += 1
    if counts:
        raise ValueError("blocked_lineage_intersection")
    return {"status": "PASS", "dataset_sha": digest, "observations": len(remote),
            "metrics": dict(sorted(Counter(r["metric_code"] for r in remote).items())),
            "unknown": sum(r["availability_source"] == "UNKNOWN" for r in remote),
            "fabricated_available_at": sum(r["available_at"] is not None for r in remote),
            "scope_blockers_loaded": 0, "value_conflicts_loaded": 0}


def classify_future_fact(incoming, accepted, backlog, *, reviewed=False):
    """Conceptual V2 classification only; never updates immutable V1."""
    key = tuple(incoming["key"])
    if key in backlog:
        return "RESOLVES_BACKLOG" if reviewed else "CONFLICT"
    if key not in accepted:
        return "NEW"
    if Decimal(incoming["value"]) == Decimal(accepted[key]["value"]):
        return "SAME"
    return "REVISION" if reviewed else "CONFLICT"


def verify_remote_scopes(remote, plan):
    """Reconcile every actual-bearing scope using joined readback, not planned totals."""
    grouped = defaultdict(list)
    grains = Counter()
    for row in remote:
        fact = row["lineage"]["canonical_fact"]
        if row["scope_code"] != fact["key"][0].upper():
            raise ValueError("remote_scope_identity_changed")
        grouped[fact["key"][0]].append(row)
        grains[fact["fact_grain"]] += 1
    expected = {s["scope_id"]: s for s in plan["scope_summary"]}
    if set(grouped) != set(expected):
        raise ValueError("remote_scope_inventory_changed")
    scopes = []
    for sid, rows in sorted(grouped.items()):
        metrics = Counter(r["metric_code"] for r in rows)
        measured = {"products": len({r["product_code"] for r in rows}),
                    "metric_counts": {metric: metrics[metric] for metric in METRICS},
                    "total_facts": len(rows), "period_min": min(r["period"][:7] for r in rows),
                    "period_max": max(r["period"][:7] for r in rows)}
        if any(measured[key] != expected[sid][key] for key in measured):
            raise ValueError("remote_scope_totals_changed")
        scopes.append({"scope_id": sid, **measured, "status": "PASS"})
    planned_grains = Counter(r["lineage"]["canonical_fact"]["fact_grain"] for r in plan["observations"])
    if grains != planned_grains:
        raise ValueError("remote_grain_totals_changed")
    return {"status": "PASS", "scopes": scopes, "scope_count": len(scopes),
            "observations": sum(s["total_facts"] for s in scopes), "grain_counts": dict(grains)}
