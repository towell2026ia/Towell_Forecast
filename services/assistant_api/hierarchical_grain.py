"""Admin-only fact ownership. Commercial membership never allocates quantities."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from services.assistant_api.business_membership import (
    BusinessMembershipIndex, VALUE_REASONS, certify_scopes, extract_business_memberships,
)
from services.assistant_api.chain_ingestion import dimensions, identifier, is_formula
from services.assistant_api.historical_corpus import Candidate, number, sha256_file
from services.assistant_api.historical_reconciliation import reconcile_historical
from services.assistant_api.source_adjudication import fingerprint

GRAINS = {"PARENT_CHAIN", "COMMERCIAL_UNIT", "EXPLICIT_ROW_UNIT", "UNKNOWN_SCOPE"}
SCOPE_BLOCKS = {"TRUE_SCOPE_BLOCKER", "INSUFFICIENT_PRODUCT_IDENTITY"}
BASELINE_MEMBERSHIP = {"MULTIPLE_COMMERCIAL_MEMBERSHIP", "NO_COMMERCIAL_MEMBERSHIP", "INSUFFICIENT_IDENTITY"}


def scope_id(kind, code):
    """Row-unit and sheet-unit refer to the same unit, not separate quantities."""
    kind = "COMMERCIAL_UNIT" if kind == "EXPLICIT_ROW_UNIT" else kind
    return ("PC-" if kind == "PARENT_CHAIN" else "CU-") + fingerprint([kind, code])[:20]


@dataclass(frozen=True)
class SourceScope:
    source_hash: str
    sheet: str
    parent_chain: str
    fact_grain: str
    commercial_unit: str
    explicit_unit_columns: dict
    evidence: dict
    approved: bool
    profile_sha256: str
    rule_sha256: str

    def validate(self, profile, rule_sha):
        if (self.approved is not True or self.fact_grain not in GRAINS
                or (self.source_hash, self.sheet) != (profile.source_hash, profile.sheet_name)
                or self.rule_sha256 != rule_sha or self.profile_sha256 != fingerprint(asdict(profile))
                or not self.evidence.get("locator") or not self.evidence.get("basis")
                or self.evidence.get("source_hash") != self.source_hash
                or self.evidence.get("rule_sha256") != rule_sha or self.sheet == "*"):
            raise ValueError("invalid_source_scope")
        if self.fact_grain == "PARENT_CHAIN" and (not self.parent_chain or self.commercial_unit):
            raise ValueError("parent_scope_requires_parent_not_child")
        if self.fact_grain == "COMMERCIAL_UNIT" and (not self.commercial_unit or not self.evidence.get("value_scope_certified")):
            raise ValueError("unit_value_scope_requires_independent_certification")
        if self.explicit_unit_columns != {"chain": profile.chain_column, "format": profile.format_column}:
            raise ValueError("explicit_columns_mismatch")
        if self.fact_grain == "EXPLICIT_ROW_UNIT" and not profile.chain_column:
            raise ValueError("row_scope_requires_explicit_columns")


def load_scope_authority(path: Path, *, expected_sha, rule_path: Path, rule_sha):
    if sha256_file(path) != expected_sha or sha256_file(rule_path) != rule_sha:
        raise ValueError("parent_authority_hash_mismatch")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("approved") is not True or not data.get("approved_by") or data.get("rule_sha256") != rule_sha:
        raise ValueError("unapproved_parent_authority")
    return data


class GrainResolver:
    def __init__(self, profiles, authority, memberships):
        self.memberships = memberships
        self.by_item, self.by_upc = defaultdict(list), defaultdict(list)
        for m in memberships:
            if m.item:
                self.by_item[m.source_hash, m.item].append(m)
            if m.upc:
                self.by_upc[m.source_hash, m.upc].append(m)
        self.identity_index = BusinessMembershipIndex(memberships, {})
        self.profiles = {(p.source_hash, p.sheet_name): p for p in profiles}
        self.scopes = {}
        if authority.get("approved") is not True or not authority.get("rule_sha256"):
            raise ValueError("unapproved_parent_authority")
        for record in authority.get("source_scopes", []):
            scope = SourceScope(**record)
            key = (scope.source_hash, scope.sheet)
            if key not in self.profiles or key in self.scopes:
                raise ValueError("unknown_or_duplicate_source_scope")
            scope.validate(self.profiles[key], authority["rule_sha256"])
            self.scopes[key] = scope
        if set(self.scopes) != {k for k, p in self.profiles.items() if p.value_role == "ACTUAL"}:
            raise ValueError("every_actual_source_requires_scope")
        self.catalog = {}
        self.cache = {}

    def register(self, kind, code, parent=""):
        sid = scope_id(kind, code)
        level = "PARENT_CHAIN" if kind == "PARENT_CHAIN" else "COMMERCIAL_UNIT"
        value = {"scope_id": sid, "code": code, "scope_type": level,
                 "parent_id": scope_id("PARENT_CHAIN", parent) if parent and level != "PARENT_CHAIN" else None,
                 "parent_code": (parent or None) if level != "PARENT_CHAIN" else None, "level": level,
                 "aggregation_policy": "SINGLE_SCOPE_ONLY_NO_AUTOMATIC_ROLLUP"}
        if sid in self.catalog and self.catalog[sid] != value:
            old = self.catalog[sid]
            if old["parent_id"] and value["parent_id"] and old["parent_id"] != value["parent_id"]:
                raise ValueError("conflicting_scope_hierarchy:" + code)
            # Null is unknown, not evidence denying an explicit row parent.
            if old["parent_id"] and not value["parent_id"]:
                value = old
        self.catalog[sid] = value
        if parent and level != "PARENT_CHAIN":
            self.register("PARENT_CHAIN", parent)
        return sid

    def children(self, digest, item, upc, period):
        rows = self.by_item.get((digest, item), []) if item else self.by_upc.get((digest, upc), [])
        return sorted({m.canonical_commercial_unit for m in rows if period in m.observed_periods
                       and (not upc or not m.upc or upc == m.upc)})

    def route(self, profile, row, item, upc, period):
        key = (profile.source_hash, profile.sheet_name, item, upc, period,
               str(row.get(profile.chain_column)), str(row.get(profile.format_column)))
        if key in self.cache:
            return self.cache[key]
        source = self.scopes[profile.source_hash, profile.sheet_name]
        parent, _, explicit = dimensions(row, profile)
        if not profile.chain_column:
            parent = source.parent_chain
        grain, code, product, reason = "UNKNOWN_SCOPE", "", upc or item, "TRUE_SCOPE_BLOCKER"
        if profile.chain_column:
            # Missing/formula dimensions never fall back to an unrelated parent.
            if explicit:
                grain, code, reason = "EXPLICIT_ROW_UNIT", explicit, "RESOLVED_EXPLICIT_UNIT_GRAIN"
        elif source.fact_grain == "PARENT_CHAIN":
            grain, code, parent = "PARENT_CHAIN", source.parent_chain, source.parent_chain
            product = item or upc
            reason = "RESOLVED_PARENT_CHAIN_GRAIN"
        elif source.fact_grain == "COMMERCIAL_UNIT":
            grain, code, parent, reason = "COMMERCIAL_UNIT", source.commercial_unit, source.parent_chain, "RESOLVED_EXPLICIT_UNIT_GRAIN"
        if not (item or upc):
            reason = "INSUFFICIENT_PRODUCT_IDENTITY"
        elif grain in {"COMMERCIAL_UNIT", "EXPLICIT_ROW_UNIT"} and not upc:
            identity = self.identity_index.route(profile.source_hash, item, "", explicit_chain=code, period=period)
            if identity["membership_resolution"] == "INSUFFICIENT_IDENTITY":
                reason = "INSUFFICIENT_PRODUCT_IDENTITY"
            else:
                product = identity["upc"] or item
        sid = self.register(grain, code, parent) if code else "UNKNOWN-" + fingerprint([source.source_hash, source.sheet])[:20]
        if not code:
            self.catalog.setdefault(sid, {"scope_id": sid, "code": None, "scope_type": "UNKNOWN_SCOPE", "parent_id": None,
                                         "parent_code": source.parent_chain or None, "level": "UNKNOWN_SCOPE",
                                         "aggregation_policy": "BLOCKED_NO_ROLLUP"})
        children = self.children(profile.source_hash, item, upc, period)
        if grain == "PARENT_CHAIN" and reason not in SCOPE_BLOCKS:
            reason = "PARENT_VALID_CHILD_MULTI" if len(children) > 1 else "PARENT_VALID_CHILD_UNKNOWN" if not children else reason
        result = {"fact_scope_id": sid, "fact_grain": grain, "canonical_code": code or None,
                  "parent_chain": parent or source.parent_chain or None, "stable_product": product,
                  "identity_type": "ITEM" if grain == "PARENT_CHAIN" and item else "UPC" if product != item else "ITEM",
                  "item": item, "upc": upc, "children": children, "parent_only": grain == "PARENT_CHAIN" and not children,
                  "scope_resolution": reason, "scope_blocking": reason in SCOPE_BLOCKS,
                  "scope_evidence": source.evidence}
        self.cache[key] = result
        return result


def period_fields(profile, data, *, period_min="0000-01", period_max="9999-12"):
    cutoff = (profile.period_layout or {}).get("cutoff", "")
    for n, row in sorted(data["rows"].items()):
        if n <= min(profile.header_rows, default=0) or n in profile.header_rows:
            continue
        for field in profile.metric_layout:
            if not field.get("row_from", 0) <= n <= field.get("row_to", data["max_row"]):
                continue
            when = row.get(field["date_column"]) if field.get("date_column") else None
            period = when.strftime("%Y-%m") if isinstance(when, (date, datetime)) else field.get("period") if not field.get("date_column") else None
            if period and re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period) and period_min <= period <= period_max and (not cutoff or period <= cutoff):
                yield n, row, field, period


def lookup_reference(value, profile, row_no):
    """Parse dependency shape, never evaluate Excel or use a cached value.

    IFERROR's fallback does not prove a zero; all formula quantities stay excluded.
    Recognize only a direct exact XLOOKUP by ITEM, optionally IFERROR-wrapped.
    """
    text = getattr(value, "text", value)
    if not isinstance(text, str):
        return None
    text = text.strip().replace("_xlfn.", "").replace("_xlws.", "")
    if text.startswith("=IFERROR(") and text.endswith(",0)"):
        text = "=" + text[len("=IFERROR("):-3]
    match = re.fullmatch(r'=XLOOKUP\(([^,]+),([^,]+),([^,\)]+)(?:,(?:"-"|""|0))?(?:,0)?\)', text, re.I)
    if not match:
        return None
    lookup = match[1].replace("$", "").upper()
    if lookup not in {f"{profile.item_column}{row_no}".upper(), f"{profile.item_column}:{profile.item_column}".upper()}:
        return None
    references = []
    for arg in (match[2], match[3]):
        ref = re.fullmatch(r"(?:'([^']+)'|([^'!]+))!\$?([A-Z]+)(\$?\d*)?:\$?([A-Z]+)(\$?\d*)?", arg, re.I)
        if not ref or ref[3].upper() != ref[5].upper() or ref[4] or ref[6]:
            # Bounded ranges can have different start/end rows; full columns
            # are the supported historical shape, unsupported shapes stay excluded.
            return None
        references.append((ref[1] or ref[2], ref[3].upper()))
    if references[0][0] != references[1][0]:
        return None
    return {"sheet": references[0][0], "item_column": references[0][1], "value_column": references[1][1]}


def reconcile_grain(books, manifests, profiles, membership_authority, scope_authority):
    expected = {(h, name) for h, sheets in books.items() for name in sheets}
    if expected != {(p.source_hash, p.sheet_name) for p in profiles} or len(profiles) != len(expected):
        raise ValueError("every_sheet_requires_profile")
    for p in profiles:
        p.validate(p.source_hash, set(books[p.source_hash]))
    adapted, business_scopes = certify_scopes(books, profiles, membership_authority, allow_empty=True)
    memberships = extract_business_memberships(books, adapted, business_scopes)
    resolver = GrainResolver(adapted, scope_authority, memberships)
    for p in adapted:
        if (p.source_hash, p.sheet_name) in business_scopes:
            # Catalog metadata is not proof of quantity ownership.
            resolver.register("COMMERCIAL_UNIT", p.canonical_chain_code, p.parent_chain)
    candidates, blocked, excluded, assignments, formula_rows = [], [], [], [], []
    raw_lookup = defaultdict(list)
    actual_profiles = [p for p in adapted if p.value_role == "ACTUAL"]
    for p in sorted(actual_profiles, key=lambda p: (p.source_hash, p.sheet_name)):
        data = books[p.source_hash][p.sheet_name]
        for n, row, field, period in period_fields(p, data, period_min=scope_authority.get("period_min", "0000-01"),
                                                  period_max=scope_authority.get("period_max", "9999-12")):
            item, upc = identifier(row.get(p.item_column)), identifier(row.get(p.upc_column), upc=True)
            value, cell = row.get(field["column"]), field["column"] + str(n)
            status, literal = number(value)
            is_calc = is_formula(value) or cell in data["formulas"]
            if not (item or upc):
                # Totals/model descriptions never become product observations.
                continue
            route = resolver.route(p, row, item, upc, period)
            key = [route["fact_scope_id"], route["stable_product"], period, field["metric"]]
            assignment = {"source_sha256": p.source_hash, "sheet": p.sheet_name, "cell": cell, "key": key, **route}
            if is_calc or status != "VALUE":
                entry = {"key": key, "source_sha256": p.source_hash, "sheet": p.sheet_name, "cell": cell,
                         "reason_code": "FORMULA_UNVERIFIED" if is_calc else "MISSING" if status == "MISSING" else "INVALID_VALUE", "blocking": False}
                excluded.append(entry)
                if is_calc:
                    formula_rows.append((p, n, item, period, field["metric"], value, assignment, entry))
                continue
            assignments.append(assignment)
            if route["scope_blocking"]:
                blocked.append({"key": key, "reason_code": route["scope_resolution"], "blocking": True,
                                "sources": [{"source_sha256": p.source_hash, "sheet": p.sheet_name, "cell": cell}], "values": [literal]})
                continue
            # Freeze canonical identity before the legacy reconciler so its
            # cross-period bridge cannot re-grain or remap these observations.
            candidate = Candidate(key[0], key[1], "", str(row.get(p.description_column) or ""),
                                  str(row.get(p.category_column) or "UNCLASSIFIED"), period, field["metric"], literal,
                                  p.source_hash, p.sheet_name, cell, (p.period_layout or {}).get("cutoff") or period,
                                  evidence_level=field.get("evidence_level", "A"))
            candidates.append(candidate)
            if route["fact_grain"] == "PARENT_CHAIN":
                raw_lookup[p.source_hash, p.sheet_name, item, field["column"], period, field["metric"]].append(assignment)
    derived = []
    for p, n, item, period, metric, value, assignment, entry in formula_rows:
        reference = lookup_reference(value, p, n)
        target_profile = resolver.profiles.get((p.source_hash, reference["sheet"])) if reference else None
        targets = raw_lookup[p.source_hash, reference["sheet"], item, reference["value_column"], period, metric] if reference else []
        if (reference and target_profile and reference["item_column"] == target_profile.item_column and targets):
            entry["reason_code"] = "DERIVED_CHILD_DUPLICATE"
            derived.append({**assignment, "canonical_raw_keys": sorted({tuple(t["key"]) for t in targets}),
                            "raw_locators": [[t["source_sha256"], t["sheet"], t["cell"]] for t in targets],
                            "dependency": reference, "dependency_formula_sha256": fingerprint(getattr(value, "text", value)),
                            "no_formula_evaluation": True, "no_child_allocation": True})
    report = reconcile_historical(manifests, candidates, excluded=excluded)
    grouped = {}
    for row in blocked:
        key = tuple(row["key"])
        group = grouped.setdefault(key, {**row, "sources": [], "values": [], "versions": [], "classification": "IDENTITY_BLOCKED"})
        group["sources"].extend(row["sources"])
        group["values"].extend(row["values"])
    for row in grouped.values():
        row["sources"] = sorted(row["sources"], key=fingerprint)
        row["values"] = sorted(set(row["values"]))
    report["resolution_ledger"] += list(grouped.values())
    report["resolution_ledger"].sort(key=lambda d: (tuple(d["key"]), fingerprint(d)))
    report["source_assignments"] = sorted(assignments, key=lambda r: (r["source_sha256"], r["sheet"], r["cell"], r["key"][2:]))
    report["derived_lineage"] = sorted(derived, key=fingerprint)
    report["commercial_memberships"] = [{**asdict(m), "commercial_unit_id": scope_id("COMMERCIAL_UNIT", m.canonical_commercial_unit),
                                         "creates_observations": False} for m in memberships]
    report["fact_scope_catalog"] = [resolver.catalog[k] for k in sorted(resolver.catalog)]
    lineage = {(a["source_sha256"], a["sheet"], a["cell"]): a for a in assignments}
    for fact in report["versions"]:
        route = lineage[fact["source_sha256"], fact["sheet"], fact["cell"]]
        fact.update({k: route[k] for k in ("fact_scope_id", "fact_grain", "canonical_code", "parent_chain", "identity_type", "item", "upc", "children", "parent_only")})
    # Selected and versions share objects, not independently mutable copies.
    report["summary"]["dataset_sha256"] = hashlib.sha256(json.dumps(report["versions"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    report["summary"]["reason_counts"] = dict(sorted(Counter(d["reason_code"] for d in report["resolution_ledger"]).items()))
    report["summary"]["blocking_keys"] = len({tuple(d["key"]) for d in report["resolution_ledger"] if d["blocking"]})
    report["summary"]["blocking_decisions"] = sum(d["blocking"] for d in report["resolution_ledger"])
    report["blocking"] = bool(report["summary"]["blocking_keys"] or not report["selected"])
    report["bootstrap_status"] = "ADMIN_ONLY_HIERARCHICAL_NO_PUBLICATION_AUTHORIZATION"
    one_scope = defaultdict(set)
    for a in assignments:
        one_scope[a["source_sha256"], a["sheet"], a["cell"]].add(a["fact_scope_id"])
    if any(len(ids) != 1 for ids in one_scope.values()):
        raise ValueError("source_fact_allocated_to_multiple_scopes")
    report["ownership_invariants"] = {"one_source_fact_one_scope": True, "membership_creates_facts": False,
                                     "mixed_scope_sum_forbidden": True, "derived_child_facts_created": 0}
    return report, resolver


def aggregate_scope(facts, *, fact_scope_id):
    """Only one scope/period/metric rollup; never sum parent and child copies."""
    if any(f["fact_scope_id"] != fact_scope_id for f in facts) or len({tuple(f["key"][2:]) for f in facts}) > 1:
        raise ValueError("mixed_scope_or_period_aggregation_forbidden")
    if len({tuple(f["key"]) for f in facts}) != len(facts):
        raise ValueError("duplicate_fact_aggregation_forbidden")
    return sum((Decimal(f["value"]) for f in facts), Decimal(0))


def coverage(report, resolver):
    scopes = {r["scope_id"]: r for r in report["fact_scope_catalog"]}
    accepted = defaultdict(list)
    blocked = defaultdict(list)
    for f in report["selected"]:
        accepted[tuple(f["key"][:2])].append(f)
    for d in report["resolution_ledger"]:
        if d["blocking"] and len(d["key"]) == 4:
            blocked[tuple(d["key"][:2])].append(d)
    series = {}
    for key in sorted(set(accepted) | set(blocked)):
        parent = scopes.get(key[0], {}).get("scope_type") == "PARENT_CHAIN"
        series[key] = {"scope_id": key[0], "stable_product": key[1], "actuals": len(accepted[key]),
                       "eligibility": "BLOCKED" if blocked[key] else "FORECAST_PARENT_ELIGIBLE" if parent else "FORECAST_CHILD_ELIGIBLE",
                       "temporal_asof_eligibility": "BLOCKED_UNKNOWN_AVAILABILITY"}
    for m in report["commercial_memberships"]:
        key = (m["commercial_unit_id"], m["upc"] or m["item"])
        series.setdefault(key, {"scope_id": key[0], "stable_product": key[1], "actuals": 0,
                               "eligibility": "CATALOG_ONLY", "temporal_asof_eligibility": "BLOCKED_UNKNOWN_AVAILABILITY"})
    parent_products = defaultdict(set)
    incomplete_products = set()
    for f in report["selected"]:
        if f["fact_grain"] == "PARENT_CHAIN":
            parent_products[f["fact_scope_id"], f["key"][1]].update(f["children"])
            if not f["children"]:
                incomplete_products.add((f["fact_scope_id"], f["key"][1]))
    parent_only = sum(not c for c in parent_products.values())
    fact = {"selected_observations": len(report["selected"]),
            "by_grain": dict(sorted(Counter(f["fact_grain"] for f in report["selected"]).items())),
            "true_scope_blockers": sum(d["blocking"] and d["reason_code"] == "TRUE_SCOPE_BLOCKER" for d in report["resolution_ledger"]),
            "insufficient_identity": sum(d["blocking"] and d["reason_code"] == "INSUFFICIENT_PRODUCT_IDENTITY" for d in report["resolution_ledger"]),
            "series": [series[k] for k in sorted(series)], "not_a_cross_scope_quantity_total": True}
    child = {"parent_products_with_one_child": sum(len(c) == 1 for c in parent_products.values()),
             "parent_products_with_multiple_children": sum(len(c) > 1 for c in parent_products.values()),
             "parent_products_parent_only": parent_only, "child_membership_incomplete": len(incomplete_products),
             "products_without_child_is_not_fact_blocking": True,
             "catalog_only_child_series": sum(s["eligibility"] == "CATALOG_ONLY" for s in series.values()),
             "membership_rows": len(report["commercial_memberships"])}
    return fact, child


def trace_baseline(old, new):
    assignments = defaultdict(list)
    decisions = defaultdict(list)
    accepted_keys = {tuple(f["key"]) for f in new["selected"]}
    for a in new["source_assignments"]:
        assignments[a["source_sha256"], a["sheet"], a["cell"], *a["key"][2:]].append(a)
    for d in new["resolution_ledger"]:
        decisions[tuple(d["key"])].append(d)
    membership, values, groups = [], [], defaultdict(list)
    for d in old["resolution_ledger"]:
        if not d["blocking"] or d["reason_code"] not in BASELINE_MEMBERSHIP | VALUE_REASONS:
            continue
        matches, missing = [], False
        for s in d["sources"]:
            rows = assignments[s["source_sha256"], s["sheet"], s["cell"], *d["key"][2:]]
            missing |= not bool(rows)
            matches.extend(rows)
        keys = sorted({tuple(a["key"]) for a in matches})
        pending = missing or not matches or any(a["scope_blocking"] for a in matches)
        base = {"old_decision_id": fingerprint([d["key"], d["reason_code"]])[:20], "old_key": d["key"],
                "old_reason": d["reason_code"], "new_keys": keys, "source_locators": [[s["source_sha256"], s["sheet"], s["cell"]] for s in d["sources"]],
                "scope_pending": pending, "original_values": d["values"]}
        if d["reason_code"] in BASELINE_MEMBERSHIP:
            reasons = {a["scope_resolution"] for a in matches}
            final = ("INSUFFICIENT_PRODUCT_IDENTITY" if "INSUFFICIENT_PRODUCT_IDENTITY" in reasons else "TRUE_SCOPE_BLOCKER" if pending
                     else "PARENT_VALID_CHILD_MULTI" if "PARENT_VALID_CHILD_MULTI" in reasons else "PARENT_VALID_CHILD_UNKNOWN" if "PARENT_VALID_CHILD_UNKNOWN" in reasons
                     else "RESOLVED_PARENT_CHAIN_GRAIN" if "RESOLVED_PARENT_CHAIN_GRAIN" in reasons else "RESOLVED_EXPLICIT_UNIT_GRAIN")
            row = {**base, "final_status": final, "fact_scope_blocking": final in SCOPE_BLOCKS,
                   "value_blocking": any(r["blocking"] and r["reason_code"] in VALUE_REASONS for k in keys for r in decisions[k]),
                   "children": sorted({c for a in matches for c in a["children"]})}
            membership.append(row)
            source_pattern = tuple(sorted({(s["source_sha256"], s["sheet"]) for s in d["sources"]}))
            groups[d["reason_code"], source_pattern].append(row)
        else:
            conflicts = [r for k in keys for r in decisions[k] if r["blocking"] and r["reason_code"] in VALUE_REASONS]
            if pending:
                final = "SCOPE_PENDING_VALUE_CONFLICT"
            elif any(r["reason_code"] == "SAME_SNAPSHOT_CONFLICT" for r in conflicts):
                final = "SAME_CUTOFF_CONFLICT"
            elif any(r["reason_code"] == "CROSS_SOURCE_CONFLICT" for r in conflicts):
                final = "CROSS_SNAPSHOT_CONFLICT"
            elif conflicts:
                final = "TRUE_PARENT_VALUE_CONFLICT" if all(a["fact_grain"] == "PARENT_CHAIN" for a in matches) else "TRUE_UNIT_VALUE_CONFLICT"
            elif len({k[0] for k in keys}) > 1 and all(k in accepted_keys for k in keys):
                final = "RESOLVED_DISTINCT_FACT_SCOPES"
            else:
                # Missing accepted values is not resolution by disappearance.
                final = "VALUE_REVIEW_REQUIRED"
            values.append({**base, "final_status": final, "value_blocking": final != "RESOLVED_DISTINCT_FACT_SCOPES", "no_value_winner_chosen": True})
    group_rows = []
    for (reason, sources), rows in sorted(groups.items()):
        outcomes = Counter("TRUE_SCOPE_BLOCKER" if r["fact_scope_blocking"] else "CHILD_MEMBERSHIP_MULTI" if r["final_status"] == "PARENT_VALID_CHILD_MULTI"
                           else "CHILD_MEMBERSHIP_UNKNOWN" if r["final_status"] == "PARENT_VALID_CHILD_UNKNOWN" else "PARENT_FACT_VALID" for r in rows)
        group_rows.append({"old_decision_id": "M-"+fingerprint([reason, sources])[:12], "old_reason": reason,
                           "old_affected_facts": len(rows), "source_pattern": sources, "new_results": dict(sorted(outcomes.items())),
                           "child_allocation_approved": False, "value_blocking_facts": sum(r["value_blocking"] for r in rows)})
    return {"baseline_membership": sorted(membership, key=lambda r: r["old_decision_id"]),
            "review_decisions": group_rows, "baseline_values": sorted(values, key=lambda r: r["old_decision_id"])}


def value_conflicts(report, baseline_values):
    catalog = {s["scope_id"]: s for s in report["fact_scope_catalog"]}
    rows = []
    for d in report["resolution_ledger"]:
        if not d["blocking"] or d["reason_code"] not in VALUE_REASONS:
            continue
        classification = ("SAME_CUTOFF_CONFLICT" if d["reason_code"] == "SAME_SNAPSHOT_CONFLICT" else
                          "CROSS_SNAPSHOT_CONFLICT" if d["reason_code"] == "CROSS_SOURCE_CONFLICT" else
                          "TRUE_PARENT_VALUE_CONFLICT" if catalog[d["key"][0]]["scope_type"] == "PARENT_CHAIN" else "TRUE_UNIT_VALUE_CONFLICT")
        rows.append({**d, "grain_classification": classification})
    return {"baseline": baseline_values, "baseline_outcomes": dict(sorted(Counter(r["final_status"] for r in baseline_values).items())),
            "current_conflicts": rows, "current_counts": dict(sorted(Counter(r["grain_classification"] for r in rows).items())),
            "derived_child_duplicate_representations": len(report["derived_lineage"]), "no_value_authority_added": True}
