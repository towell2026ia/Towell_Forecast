"""Read-only value and scope regression; legacy labels never grant ownership."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from decimal import Decimal

from services.assistant_api.historical_corpus import sha256_file
from services.assistant_api.source_adjudication import fingerprint


def legacy_cohort(report, policy):
    rows = [f for f in report["selected"] if f["key"][0] == policy["legacy_scope"]
            and f["key"][1].startswith(policy["product_prefix"])
            and f["key"][2].startswith(policy["period_prefix"])]
    keys = [tuple(f["key"][1:]) for f in rows]
    if len(rows) != policy["expected_count"] or len(set(keys)) != len(keys):
        raise ValueError("legacy_cohort_count_or_identity_mismatch")
    return sorted(rows, key=lambda f: tuple(f["key"]))


def read_value_golden(path, *, expected_sha, cohort, metric_map):
    """Pin the ENTIRE original file, even though only a cohort is certified."""
    before = sha256_file(path)
    if before != expected_sha:
        raise ValueError("legacy_golden_bytes_changed")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers, rows = reader.fieldnames, list(reader)
    wanted = {tuple(f["key"][1:]): Decimal(f["value"]) for f in cohort}
    found = {}
    for row in rows:
        key = (row.get("upc"), row.get("period"), metric_map.get(row.get("metric")))
        if key not in wanted:
            continue
        value = Decimal(row["value"])
        if key in found and found[key] != value:
            raise ValueError("legacy_value_golden_internal_conflict")
        found[key] = value
    if wanted != found:
        raise ValueError("legacy_value_golden_cohort_mismatch")
    if sha256_file(path) != before:
        raise ValueError("legacy_golden_changed_during_read")
    return {"type": "LEGACY_VALUE_GOLDEN", "path": str(path.resolve()),
            "sha256": before, "content_sha256": fingerprint([headers, rows]),
            "cohort_value_sha256": fingerprint([[*k, str(wanted[k])] for k in sorted(wanted)]),
            "legacy_facts": len(cohort), "scope_is_not_authority": True, "modified": False}


def _locator(source, period_metric):
    return source["source_sha256"], source["sheet"], source["cell"], *period_metric


def certify_hierarchy(cohort, report, *, reference, policy):
    """Match literal lineage, then choose independently supported specificity.

    This produces references to existing facts, never observations. In particular
    legacy commercial labels and equal numeric values cannot authorize a child.
    Separate evidenced parent observations are not added to child observations.
    """
    by_locator, assignments = defaultdict(list), defaultdict(list)
    for fact in report["selected"]:
        for source in [fact, *fact.get("equivalent_sources", [])]:
            by_locator[_locator(source, fact["key"][2:])].append(fact)
    for assignment in report["source_assignments"]:
        assignments[_locator(assignment, assignment["key"][2:])].append(assignment)
    all_keys = [tuple(f["key"]) for f in report["selected"]]
    duplicates = len(all_keys) - len(set(all_keys))
    missing = changed = unsupported = identity_changed = ambiguous = 0
    matches = []
    for legacy in cohort:
        found = {}
        for source in [legacy, *legacy.get("equivalent_sources", [])]:
            loc = _locator(source, legacy["key"][2:])
            for fact in by_locator[loc]:
                key = tuple(fact["key"])
                proofs = [a for a in assignments[loc] if tuple(a["key"]) == key and not a["scope_blocking"]]
                if not proofs or any(a["stable_product"] != key[1] for a in proofs):
                    identity_changed += 1
                    continue
                child = fact["fact_grain"] in {"COMMERCIAL_UNIT", "EXPLICIT_ROW_UNIT"}
                valid = any(a["fact_grain"] == fact["fact_grain"] and a["canonical_code"] == fact["canonical_code"]
                            and a["fact_scope_id"] == fact["fact_scope_id"]
                            and a["scope_evidence"].get("source_hash") == source["source_sha256"]
                            and a["scope_evidence"].get("locator")
                            and a["scope_evidence"].get("rule_sha256")
                            and ((a["scope_evidence"].get("value_scope_certified")
                                  or (a["fact_grain"] == "EXPLICIT_ROW_UNIT" and a["scope_resolution"] == "RESOLVED_EXPLICIT_UNIT_GRAIN"))
                                 if child else a["fact_grain"] == "PARENT_CHAIN")
                            for a in proofs)
                if not valid:
                    unsupported += 1
                    continue
                if child and key[1] != legacy["key"][1]:
                    identity_changed += 1
                    continue
                found[key] = fact
        if not found:
            missing += 1
            continue
        children = [f for f in found.values() if f["fact_grain"] != "PARENT_CHAIN"]
        candidates = children or list(found.values())
        if len(candidates) != 1:
            ambiguous += 1
            continue
        fact = candidates[0]
        same = Decimal(fact["value"]) == Decimal(legacy["value"])
        changed += not same
        matches.append({"legacy_identity": legacy["key"][1:], "legacy_value": legacy["value"],
                        "canonical_key": fact["key"], "canonical_value": fact["value"],
                        "scope": fact["canonical_code"], "grain": fact["fact_grain"],
                        "value_match": same, "identity_match_basis": "PINNED_LITERAL_LINEAGE_NOT_DESCRIPTION_OR_SCOPE_LABEL",
                        "source": {k: fact[k] for k in ("source_sha256", "sheet", "cell")},
                        "other_evidenced_scope_keys_not_summed": [list(k) for k in sorted(found) if list(k) != fact["key"]],
                        "availability_source": fact["availability_source"], "available_at": fact["available_at"]})
    counts = dict(sorted(Counter(m["scope"] for m in matches).items()))
    parent_periods = {m["legacy_identity"][1] for m in matches if m["grain"] == "PARENT_CHAIN"}
    duplicate_matches = len(matches) - len({tuple(m["canonical_key"]) for m in matches})
    summary = {"legacy_facts": len(cohort), "canonical_facts_matched": len(matches),
               "value_matches": sum(m["value_match"] for m in matches), "scope_counts": counts,
               "child_scoped": sum(m["grain"] != "PARENT_CHAIN" for m in matches),
               "parent_scoped": sum(m["grain"] == "PARENT_CHAIN" for m in matches),
               "missing": missing, "changed_values": changed, "duplicates": duplicates + duplicate_matches,
               "unsupported_child_assignments": unsupported, "identity_changes": identity_changed,
               "ambiguous_matches": ambiguous, "legacy_golden_modified": False, "facts_created": 0}
    passed = (len(matches) == len(cohort) == policy["expected_count"] and counts == policy["expected_scope_counts"]
              and parent_periods == set(policy["parent_periods"])
              and not any((missing, changed, duplicates, duplicate_matches, unsupported, identity_changed, ambiguous)))
    return {"type": "HIERARCHICAL_SCOPE_GOLDEN", "status": "PASS" if passed else "FAIL",
            "legacy_value_golden": reference, "legacy_cohort_sha256": fingerprint(cohort),
            "policy_sha256": fingerprint(policy), "dataset_sha256": report["summary"]["dataset_sha256"],
            "summary": summary, "matches": matches, "membership_is_not_quantity_authority": True,
            "no_automatic_parent_child_sum": True}
