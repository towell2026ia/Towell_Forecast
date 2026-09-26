"""Reviewed administrative commercial membership, never value authority.

No runtime/browser entry point. The caller must pin the private authority file
and the owner-provided rule before loading it. Source, sheet, product and observed
period remain explicit; absence of evidence is not proof of non-membership.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.assistant_api.chain_ingestion import (
    SourceSheetProfile, append_membership_blocks, dimensions, identifier, is_formula, parse_candidates,
)
from services.assistant_api.historical_corpus import number, sha256_file
from services.assistant_api.historical_reconciliation import reconcile_historical
from services.assistant_api.source_adjudication import fingerprint

BUSINESS_ROLES = {"CHAIN_DETAIL", "CHAIN_CALC"}
MEMBERSHIP_OUTCOMES = {
    "RESOLVED_BUSINESS_SHEET_MEMBERSHIP", "RESOLVED_EXPLICIT_ROW_CHAIN", "RESOLVED_UNIQUE_ITEM_UPC",
    "MULTIPLE_COMMERCIAL_MEMBERSHIP", "NO_COMMERCIAL_MEMBERSHIP", "INSUFFICIENT_IDENTITY",
}
UNRESOLVED = {"MULTIPLE_COMMERCIAL_MEMBERSHIP", "NO_COMMERCIAL_MEMBERSHIP", "INSUFFICIENT_IDENTITY"}
OLD_MEMBERSHIP_REASONS = {"AMBIGUOUS_CHAIN_MEMBERSHIP", "MISSING_CHAIN_MEMBERSHIP"}
VALUE_REASONS = {"INTERNAL_SOURCE_DUPLICATE", "CROSS_SOURCE_CONFLICT", "SAME_SNAPSHOT_CONFLICT"}


def load_authority(path: Path, *, expected_sha: str, rule_path: Path, rule_sha: str):
    if sha256_file(path) != expected_sha or sha256_file(rule_path) != rule_sha:
        raise ValueError("reviewed_authority_hash_mismatch")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (value.get("rule_sha256") != rule_sha or value.get("approved") is not True
            or not value.get("approved_by") or not value.get("review_note")
            or value.get("evidence_type") != "OWNER_BUSINESS_MEMBERSHIP_RULE"):
        raise ValueError("unapproved_business_authority")
    return value


def content_structure(data, profile):
    atomic = sum(bool(identifier(row.get(profile.item_column)) or identifier(row.get(profile.upc_column), upc=True))
                 for n, row in data["rows"].items() if n not in profile.header_rows)
    blocks = sum(any(isinstance(v, str) and v.strip().casefold() == "fecha" and not is_formula(v)
                     for v in row.values()) for row in data["rows"].values())
    return {"atomic_rows": atomic, "product_blocks": blocks}


def certify_scopes(books, profiles, authority):
    """Reject wildcard/all-sheet policies and unreviewed role upgrades."""
    if authority.get("approved") is not True or not authority.get("rule_sha256"):
        raise ValueError("unapproved_business_authority")
    by_sheet = {(p.source_hash, p.sheet_name): p for p in profiles}
    aliases = authority.get("commercial_sheet_aliases", [])
    alias_units = {}
    for alias in aliases:
        digest, a, b, unit = (alias.get(k) for k in ("source_hash", "sheet_a", "sheet_b", "canonical_unit"))
        if (alias.get("approved") is not True or not alias.get("evidence") or digest not in books
                or a == b or (digest, a) not in by_sheet or (digest, b) not in by_sheet
                or not unit or alias.get("rule_sha256") != authority["rule_sha256"]):
            raise ValueError("invalid_commercial_sheet_alias")
        for name in (a, b):
            if (digest, name) in alias_units and alias_units[digest, name] != unit:
                raise ValueError("conflicting_sheet_alias")
            alias_units[digest, name] = unit
    scopes = {}
    for scope in authority.get("scopes", []):
        digest, name, unit = (scope.get(k) for k in ("source_hash", "sheet", "canonical_commercial_unit"))
        original = by_sheet.get((digest, name))
        if (scope.get("approved") is not True or scope.get("classification") != "BUSINESS_COMMERCIAL"
                or not scope.get("evidence_locator") or not scope.get("evidence_id") or not unit or unit == "*"
                or scope.get("rule_sha256") != authority["rule_sha256"] or (digest, name) in scopes
                or digest not in books or name not in books[digest] or original is None
                or original.sheet_role != scope.get("original_sheet_role")):
            raise ValueError("invalid_business_sheet_scope")
        role = scope.get("reviewed_sheet_role", original.sheet_role)
        paired = original.sheet_role == "ALTERNATE_REPRESENTATION" and alias_units.get((digest, name)) == unit
        if original.sheet_role not in BUSINESS_ROLES | {"ALTERNATE_REPRESENTATION"}:
            raise ValueError("non_business_sheet_cannot_certify")
        if role not in BUSINESS_ROLES and not (role == "ALTERNATE_REPRESENTATION" and paired):
            raise ValueError("alternate_representation_requires_review")
        if (digest, name) in alias_units and alias_units[digest, name] != unit:
            raise ValueError("alias_scope_unit_mismatch")
        structure = content_structure(books[digest][name], original)
        if not structure["atomic_rows"] and not structure["product_blocks"]:
            raise ValueError("business_product_structure_required")
        scopes[digest, name] = {**scope, **structure, "review_status": "APPROVED"}
    if not scopes:
        raise ValueError("explicit_business_scopes_required")
    # Both sides of an alias must have approved membership scopes.
    if any(key not in scopes or scopes[key]["canonical_commercial_unit"] != unit for key, unit in alias_units.items()):
        raise ValueError("alias_requires_both_business_scopes")
    adapted = []
    for p in profiles:
        scope = scopes.get((p.source_hash, p.sheet_name))
        if scope:
            p = replace(p, sheet_role=scope.get("reviewed_sheet_role", p.sheet_role),
                        canonical_chain_code=scope["canonical_commercial_unit"],
                        canonical_chain_name=scope.get("canonical_name", scope["canonical_commercial_unit"]),
                        parent_chain=scope.get("parent_code", ""), commercial_format=scope.get("format_code", ""),
                        membership_role="CHAIN_MEMBERSHIP", review_required=False)
        elif not p.chain_column:
            # A raw/support sheet cannot approve itself merely because an old
            # profile contained a chain hint or undocumented implicit mapping.
            p = replace(p, canonical_chain_code="", membership_chain_code="", membership_role="NONE")
        adapted.append(p)
    return adapted, scopes


def observed_periods(row_no, row, profile, data):
    periods = set()
    cutoff = (profile.period_layout or {}).get("cutoff", "")
    for field in profile.metric_layout:
        if not field.get("row_from", 0) <= row_no <= field.get("row_to", data.get("max_row", row_no)):
            continue
        period = field.get("period")
        if field.get("date_column"):
            when = row.get(field["date_column"])
            period = when.strftime("%Y-%m") if isinstance(when, (date, datetime)) else None
        value = row.get(field["column"])
        if (period and re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period) and (not cutoff or period <= cutoff)
                and value is not None and str(value).strip()):
            periods.add(period)
    return tuple(sorted(periods))


@dataclass(frozen=True)
class BusinessMembership:
    source_hash: str
    sheet: str
    canonical_commercial_unit: str
    item: str
    upc: str
    valid_from: str | None
    valid_to: str | None
    observed_periods: tuple[str, ...]
    evidence_locator: str
    evidence_id: str
    evidence_type: str
    review_status: str
    sources: tuple[dict[str, Any], ...]
    validity_basis: str = "OBSERVED_MEMBERSHIP_NOT_ABSOLUTE_CATALOG_VALIDITY"


def extract_business_memberships(books, profiles, scopes):
    entries = []
    for p in profiles:
        scope = scopes.get((p.source_hash, p.sheet_name))
        explicit_master = p.sheet_role == "RAW_BASE" and bool(p.chain_column)
        if scope is None and not explicit_master:
            continue
        data = books[p.source_hash][p.sheet_name]
        for n, row in sorted(data["rows"].items()):
            if n <= min(p.header_rows, default=0) or n in p.header_rows:
                continue
            item, upc = identifier(row.get(p.item_column)), identifier(row.get(p.upc_column), upc=True)
            if not (item or upc):
                continue
            _, _, unit = dimensions(row, p)
            if not unit:
                continue
            periods = observed_periods(n, row, p, data)
            locator = f"{p.sheet_name}!{p.item_column if item else p.upc_column}{n}"
            proof = scope["evidence_id"] if scope else "ROW-" + fingerprint([p.source_hash, p.sheet_name, n])[:16]
            kind = "BUSINESS_SHEET_MEMBERSHIP" if scope else "EXPLICIT_ROW_CHAIN"
            entries.append({"source_hash": p.source_hash, "sheet": p.sheet_name, "unit": unit, "item": item, "upc": upc,
                            "periods": periods, "locator": locator, "evidence_id": proof, "evidence_type": kind,
                            "source": {"sheet": p.sheet_name, "row": n, "item_locator": f"{p.item_column}{n}" if item else "",
                                       "upc_locator": f"{p.upc_column}{n}" if upc else "", "explicit_upc": bool(upc), "evidence_id": proof}})
    # Identity-only pairs join by literal ITEM, unit, source and period. Equal
    # membership representations merge, not their demand quantities.
    bridges = defaultdict(set)
    for e in entries:
        if e["item"] and e["upc"]:
            for period in e["periods"]:
                bridges[e["source_hash"], e["unit"], e["item"], period].add(e["upc"])
    groups = defaultdict(list)
    for entry in entries:
        pieces = defaultdict(list)
        for period in entry["periods"]:
            options = bridges[entry["source_hash"], entry["unit"], entry["item"], period]
            upc = entry["upc"] or (next(iter(options)) if len(options) == 1 else "")
            pieces[upc].append(period)
        if not pieces:
            pieces[entry["upc"]] = []
        for upc, periods in pieces.items():
            groups[entry["source_hash"], entry["unit"], entry["item"], upc, entry["evidence_type"]].append({**entry, "periods": periods})
    result = []
    for (digest, unit, item, upc, kind), values in sorted(groups.items()):
        values.sort(key=lambda e: (e["sheet"], e["locator"], e["evidence_id"]))
        periods = tuple(sorted({period for e in values for period in e["periods"]}))
        sources = {fingerprint(e["source"]): e["source"] for e in values}
        result.append(BusinessMembership(digest, values[0]["sheet"], unit, item, upc,
                                         periods[0] if periods else None, periods[-1] if periods else None, periods,
                                         values[0]["locator"], values[0]["evidence_id"], kind, "APPROVED",
                                         tuple(sources[k] for k in sorted(sources))))
    return result


class BusinessMembershipIndex:
    def __init__(self, memberships, scopes):
        self.scopes = scopes
        self.by_item, self.by_upc, self.global_upcs = defaultdict(list), defaultdict(list), defaultdict(set)
        self.cache = {}
        for m in memberships:
            if m.review_status != "APPROVED":
                raise ValueError("unapproved_membership")
            if m.item:
                self.by_item[m.source_hash, m.item].append(m)
                if m.upc:
                    self.global_upcs[m.canonical_commercial_unit, m.item].add(m.upc)
            if m.upc:
                self.by_upc[m.source_hash, m.upc].append(m)

    def route(self, digest, item, upc, *, explicit_chain="", period=None, source_sheet=""):
        key = (digest, item, upc, explicit_chain, period, source_sheet)
        if key in self.cache:
            return self.cache[key]
        all_members = self.by_upc.get((digest, upc), []) if upc else self.by_item.get((digest, item), [])
        members = [m for m in all_members if period in m.observed_periods]
        units = {m.canonical_commercial_unit for m in members}
        unit, mapped_upc = "", upc
        final = "NO_COMMERCIAL_MEMBERSHIP"
        if explicit_chain:
            unit = explicit_chain
            members = [m for m in members if m.canonical_commercial_unit == unit]
            scope = self.scopes.get((digest, source_sheet))
            final = ("RESOLVED_BUSINESS_SHEET_MEMBERSHIP" if scope and scope["canonical_commercial_unit"] == unit
                     else "RESOLVED_EXPLICIT_ROW_CHAIN")
            units = {unit}
        elif len(units) > 1:
            final = "MULTIPLE_COMMERCIAL_MEMBERSHIP"
        elif len(units) == 1:
            unit, final = next(iter(units)), "RESOLVED_UNIQUE_ITEM_UPC"
        options = {m.upc for m in members if m.upc} if unit else set()
        if unit and not upc:
            if len(options) == 1:
                mapped_upc = next(iter(options))
            elif len(options) > 1 or self.global_upcs.get((unit, item)):
                # Never let the downstream legacy identity bridge import a UPC
                # from another period/source when the current scope cannot prove it.
                unit, final = "", "INSUFFICIENT_IDENTITY"
        evidence = sorted({e["evidence_id"] for m in members for e in m.sources})
        result = {"unit": unit, "upc": mapped_upc, "membership_resolution": final,
                  "candidate_units": sorted(units), "membership_evidence": evidence,
                  "outside_observed_units": sorted({m.canonical_commercial_unit for m in all_members if period not in m.observed_periods}),
                  "membership_absence_is_not_nonmembership": True}
        self.cache[key] = result
        return result

    def resolve(self, *args, **kwargs):
        result = self.route(*args, **kwargs)
        return result["unit"], result["upc"], result["membership_resolution"]

    def describe(self, *args, **kwargs):
        return {k: v for k, v in self.route(*args, **kwargs).items() if k not in {"unit", "upc"}}


def reconcile_business_history(books, manifests, profiles, authority):
    expected = {(h, name) for h, sheets in books.items() for name in sheets}
    if expected != {(p.source_hash, p.sheet_name) for p in profiles} or len(profiles) != len(expected):
        raise ValueError("every_sheet_requires_profile")
    for p in profiles:
        p.validate(p.source_hash, set(books[p.source_hash]))
    adapted, scopes = certify_scopes(books, profiles, authority)
    memberships = extract_business_memberships(books, adapted, scopes)
    index = BusinessMembershipIndex(memberships, scopes)
    candidates, blocked, excluded, assignments = parse_candidates(books, adapted, [], membership_index=index)
    report = reconcile_historical(manifests, candidates, excluded=excluded)
    report = append_membership_blocks(report, blocked, candidates, memberships, assignments)
    report["bootstrap_status"] = "ADMINISTRATIVE_ONLY_NO_PUBLICATION_AUTHORIZATION"
    report["business_sheet_evidence"] = [scopes[k] for k in sorted(scopes)]
    return report, adapted, memberships, scopes


def trace_membership_baseline(old, new):
    assignments = defaultdict(list)
    for row in new["source_assignments"]:
        assignments[row["source_sha256"], row["sheet"], row["cell"], *row["key"][2:]].append(row)
    result = []
    for decision in old["resolution_ledger"]:
        if not decision["blocking"] or decision["reason_code"] not in OLD_MEMBERSHIP_REASONS:
            continue
        sources = decision["sources"]
        matched, missing = [], False
        for source in sources:
            rows = assignments[source["source_sha256"], source["sheet"], source["cell"], *decision["key"][2:]]
            if not rows:
                missing = True
            matched.extend(rows)
        statuses = {r["membership_resolution"] for r in matched}
        if "MULTIPLE_COMMERCIAL_MEMBERSHIP" in statuses:
            final = "MULTIPLE_COMMERCIAL_MEMBERSHIP"
        elif "INSUFFICIENT_IDENTITY" in statuses:
            final = "INSUFFICIENT_IDENTITY"
        elif missing or not statuses or "NO_COMMERCIAL_MEMBERSHIP" in statuses:
            final = "NO_COMMERCIAL_MEMBERSHIP"
        elif "RESOLVED_UNIQUE_ITEM_UPC" in statuses:
            final = "RESOLVED_UNIQUE_ITEM_UPC"
        elif "RESOLVED_EXPLICIT_ROW_CHAIN" in statuses:
            final = "RESOLVED_EXPLICIT_ROW_CHAIN"
        else:
            final = "RESOLVED_BUSINESS_SHEET_MEMBERSHIP"
        result.append({"old_decision_id": "M-" + fingerprint(decision["key"])[:16], "old_key": decision["key"],
                       "old_status": decision["reason_code"], "candidate_units": sorted({u for r in matched for u in r["candidate_units"]}),
                       "evidence": sorted({e for r in matched for e in r["membership_evidence"]}),
                       "final_status": final, "resolution_rule": "SOURCE_PRODUCT_PERIOD_CERTIFIED_BUSINESS_MEMBERSHIP",
                       "still_membership_blocking": final in UNRESOLVED,
                       "new_keys": sorted({tuple(r["key"]) for r in matched}),
                       "source_locators": [[s["source_sha256"], s["sheet"], s["cell"]] for s in sources]})
    expected = sum(d["blocking"] and d["reason_code"] in OLD_MEMBERSHIP_REASONS for d in old["resolution_ledger"])
    if len(result) != expected or len({r["old_decision_id"] for r in result}) != expected:
        raise ValueError("membership_baseline_accounting_mismatch")
    return sorted(result, key=lambda row: row["old_decision_id"])


def trace_value_baseline(old, new):
    assignments, decisions = defaultdict(list), defaultdict(list)
    accepted_keys = {tuple(row["key"]) for row in new["selected"]}
    for row in new["source_assignments"]:
        assignments[row["source_sha256"], row["sheet"], row["cell"], *row["key"][2:]].append(row)
    for row in new["resolution_ledger"]:
        if len(row["key"]) == 4:
            decisions[tuple(row["key"])].append(row)
    result = []
    for old_row in old["resolution_ledger"]:
        if not old_row["blocking"] or old_row["reason_code"] not in VALUE_REASONS:
            continue
        matched, missing = [], False
        for source in old_row["sources"]:
            rows = assignments[source["source_sha256"], source["sheet"], source["cell"], *old_row["key"][2:]]
            if not rows:
                missing = True
            matched.extend(rows)
        pending = missing or not matched or any(r["membership_resolution"] in UNRESOLVED for r in matched)
        keys = sorted({tuple(r["key"]) for r in matched})
        new_value_block = any(d["blocking"] and d["reason_code"] in VALUE_REASONS for key in keys for d in decisions[key])
        separated = (not pending and not new_value_block and len({key[0] for key in keys}) > 1
                     and all(key in accepted_keys for key in keys))
        result.append({"old_decision_id": "V-" + fingerprint(old_row["key"])[:16], "old_key": old_row["key"],
                       "old_status": old_row["reason_code"], "new_keys": keys,
                       "final_status": "RESOLVED_BY_CHAIN_SEPARATION" if separated else old_row["reason_code"],
                       "still_blocking": not separated, "membership_pending": pending,
                       "source_locators": [[s["source_sha256"], s["sheet"], s["cell"]] for s in old_row["sources"]],
                       "original_values": old_row["values"], "no_value_authority_added": True})
    if len(result) != sum(d["blocking"] and d["reason_code"] in VALUE_REASONS for d in old["resolution_ledger"]):
        raise ValueError("value_baseline_accounting_mismatch")
    return sorted(result, key=lambda row: row["old_decision_id"])


def commercial_catalog(profiles, scopes, memberships, previous_catalog=()):
    catalog = {}
    def entry(unit):
        if unit not in catalog:
            catalog[unit] = {"commercial_unit_id": "CU-"+fingerprint(unit)[:16], "canonical_code": unit, "canonical_name": unit,
                             "parent_code": "", "format_code": "", "aliases": set(), "source_hashes": set(),
                             "sheets": set(), "evidence": set(), "status": "OBSERVED"}
        return catalog[unit]
    for row in previous_catalog:
        e = entry(row["canonical_chain"])
        e["source_hashes"].update(row["sources"])
        if len(row.get("parents", [])) == 1:
            e["parent_code"] = row["parents"][0]
        if len(row.get("formats", [])) == 1:
            e["format_code"] = row["formats"][0]
    for p in profiles:
        scope = scopes.get((p.source_hash, p.sheet_name))
        if not scope:
            continue
        e = entry(scope["canonical_commercial_unit"])
        e.update(canonical_name=p.canonical_chain_name, parent_code=p.parent_chain, format_code=p.commercial_format,
                 status="CERTIFIED")
        e["aliases"].add(p.sheet_name)
        e["source_hashes"].add(p.source_hash)
        e["sheets"].add(p.sheet_name)
        e["evidence"].add(scope["evidence_id"])
    for m in memberships:
        e = entry(m.canonical_commercial_unit)
        e["status"] = "CERTIFIED"
        e["source_hashes"].add(m.source_hash)
        e["sheets"].update(s["sheet"] for s in m.sources)
        e["evidence"].update(s["evidence_id"] for s in m.sources)
    return [{k: sorted(v) if isinstance(v, set) else v for k, v in catalog[unit].items()} for unit in sorted(catalog)]


def membership_counts(report):
    blocked = [d for d in report["resolution_ledger"] if d["blocking"]]
    return {"membership": dict(sorted(Counter(d["reason_code"] for d in blocked if d["reason_code"] not in VALUE_REASONS).items())),
            "values": dict(sorted(Counter(d["reason_code"] for d in blocked if d["reason_code"] in VALUE_REASONS).items()))}


def certify_literals(report, books):
    assignments = {(r["source_sha256"], r["sheet"], r["cell"]): r for r in report["source_assignments"]}
    samples = {}
    for fact in report["selected"]:
        key = (fact["source_sha256"], fact["sheet"], fact["cell"])
        data = books[key[0]][key[1]]
        col = "".join(c for c in key[2] if c.isalpha())
        row = int("".join(c for c in key[2] if c.isdigit()))
        status, value = number(data["rows"][row].get(col))
        if (key[2] in data["formulas"] or status != "VALUE" or Decimal(value) != Decimal(fact["value"])
                or assignments[key]["key"] != fact["key"]):
            raise ValueError("literal_identity_or_lineage_regression")
        if fact["availability_source"] != "UNKNOWN" or fact["available_at"] is not None:
            raise ValueError("membership_approval_is_not_historical_availability")
        samples.setdefault(fact["key"][0], fact)
    return {"status": "PASS", "all_literals_checked": len(report["selected"]), "differences": 0,
            "units_sampled": len(samples)}, [samples[k] for k in sorted(samples)]


def manual_membership_review(report):
    assignments = {(r["source_sha256"], r["sheet"], r["cell"]): r for r in report["source_assignments"]}
    grouped = defaultdict(list)
    for decision in report["resolution_ledger"]:
        if not decision["blocking"] or decision["reason_code"] in VALUE_REASONS:
            continue
        source_pattern = tuple(sorted({(s["source_sha256"], s["sheet"]) for s in decision["sources"]}))
        grouped[decision["reason_code"], source_pattern].append(decision)
    lines = ["# Revisión de pertenencia comercial", "", "Decisiones agrupadas, no aprobación de cantidades.", "",
             "La falta de evidencia en un periodo no demuestra ausencia histórica del producto.", ""]
    for (reason, sources), decisions in sorted(grouped.items()):
        candidates, outside = set(), set()
        for decision in decisions:
            for source in decision["sources"]:
                assignment = assignments.get((source["source_sha256"], source["sheet"], source["cell"]), {})
                candidates.update(assignment.get("candidate_units", []))
                outside.update(assignment.get("outside_observed_units", []))
        question = ("¿Qué dimensión explícita de cadena/unidad distingue este detalle agregado cuando el producto participa en varias pestañas? Aporta el campo o mapping verificable; no repartiremos el mismo fact."
                    if reason == "MULTIPLE_COMMERCIAL_MEMBERSHIP" else
                    "¿Cuál es el bridge ITEM/UPC válido para estos periodos? Aporta catálogo fechado o mapping revisado, no una coincidencia por descripción."
                    if reason == "INSUFFICIENT_IDENTITY" else
                    "¿Qué pestaña comercial o catálogo observado en estos periodos contiene los productos de esta base? Si sólo hay evidencia fuera del rango, aporta el registro anterior o alcance documental, no una fecha inventada.")
        lines.extend(["## Decisión M-" + fingerprint([reason, sources])[:12], "",
                      "Pestañas fuente: " + ", ".join(sorted({s[1] for s in sources})),
                      "Hashes: " + ", ".join(sorted({s[0] for s in sources})),
                      f"Claves afectadas: {len(decisions)}. Productos en este grupo: {len({d['key'][1] for d in decisions})}.",
                      "Unidades candidatas: " + (", ".join(sorted(candidates)) or "sin evidencia en alcance"),
                      "Observadas fuera del periodo: " + (", ".join(sorted(outside)) or "ninguna registrada"),
                      "Motivo: " + reason, "", "Pregunta: " + question, "", "Estado: PENDIENTE. No aplicar una respuesta automáticamente a filas sin alcance demostrado.", ""])
    return "\n".join(lines), len(grouped)
