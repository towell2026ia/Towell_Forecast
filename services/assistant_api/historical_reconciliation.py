"""Administrative, read-only historical reconciliation. No runtime pilot rules.

Source cutoff orders VALUE versions only with explicit reviewed snapshot proof.
It is never a substitute for evidence of when information became available.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from services.assistant_api.historical_corpus import Candidate, number

REASON_CODES = frozenset({
    "EQUIVALENT_DUPLICATE", "DIRECT_VS_SUMMARY", "VALIDATED_REVISION",
    "SAME_SNAPSHOT_CONFLICT", "INTERNAL_SOURCE_DUPLICATE", "FORMULA_UNVERIFIED",
    "ITEM_MULTI_UPC", "ITEM_ONLY_AMBIGUOUS", "DESCRIPTION_ALIAS", "PRE_LAUNCH_ZERO",
    "UNPROVEN_ZERO", "MISSING", "INVALID_VALUE", "CROSS_SOURCE_CONFLICT",
    "INSUFFICIENT_EVIDENCE", "AGGREGATE_ONLY", "DIRECT_FACT", "CONFIRMED_ZERO",
    "SOURCE_AUTHORITY", "WRONG_GRAIN_EXCLUDED",
})
CLASSIFICATIONS = ("AUTO_RESOLVABLE", "RESOLVABLE_AS_REVISION", "EXCLUDED_NON_FACT",
                   "IDENTITY_BLOCKED", "VALUE_BLOCKED")


@dataclass(frozen=True)
class SnapshotProof:
    """Trusted, reviewed registry record; NOT accepted from browser/CLI fields.

    Identity/value evidence A is distinct from the later-compatible role B.
    Cutoff proof has NO effect on historical available_at.
    """

    source_hash: str
    sequence: str
    cutoff: str
    evidence_hash: str
    locator: str
    approved_by: str
    review_note: str

    def validate(self, source_hash: str) -> None:
        if (self.source_hash != source_hash or not re.fullmatch(r"[0-9a-f]{64}", source_hash)
                or not re.fullmatch(r"[0-9a-f]{64}", self.evidence_hash)
                or not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", self.cutoff)
                or not all((self.sequence.strip(), self.locator.strip(), self.approved_by.strip(), self.review_note.strip()))):
            raise ValueError("invalid_snapshot_proof")


@dataclass(frozen=True)
class TemporalEvidence:
    evidence_id: str
    level: str
    evidence_date: str
    validated_at: str
    validated_by: str
    source_hash: str
    storage_path: str
    chain: str

    def validate(self, chain: str, source_hash: str) -> None:
        if (self.level not in {"E1", "E2", "E3"} or not self.evidence_id
                or not self.validated_by or not self.storage_path or self.chain != chain
                or self.source_hash != source_hash or not re.fullmatch(r"[0-9a-f]{64}", source_hash)):
            raise ValueError("invalid_temporal_evidence")
        try:
            when = datetime.fromisoformat(self.evidence_date.replace("Z", "+00:00"))
            validated = datetime.fromisoformat(self.validated_at.replace("Z", "+00:00"))
            if when.tzinfo is None or validated.tzinfo is None or when > validated:
                raise ValueError("invalid_temporal_evidence")
        except (ValueError, TypeError) as error:
            raise ValueError("invalid_temporal_evidence") from error


def _source(row: Candidate) -> dict[str, Any]:
    return {key: value for key, value in asdict(row).items()
            if key not in {"chain", "description", "category", "period", "metric"}
            and not (key in {"authority_role", "authority_rule_id", "original_evidence_level"} and not value)
            and not (key == "fact_eligible" and value is True)}


def _stable(row: Candidate) -> tuple[str, str, str]:
    # Only used among EQUAL values, never to resolve a conflicting value.
    return row.source_sha256, row.sheet, row.cell


def _decision(key: tuple[str, ...], rows: list[Candidate], reason: str,
              classification: str, *, blocking: bool = False,
              winner: Candidate | None = None, versions: list[dict[str, Any]] | None = None
              ) -> dict[str, Any]:
    if reason not in REASON_CODES or classification not in CLASSIFICATIONS:
        raise ValueError("unclassified_decision")
    return {"key": list(key), "sources": [_source(row) for row in sorted(rows, key=_stable)],
            "values": sorted({row.value for row in rows}), "reason_code": reason,
            "classification": classification, "canonical_value": winner.value if winner else None,
            "canonical_source": _source(winner) if winner else None,
            "version_no": len(versions) if versions else (1 if winner else None),
            "versions": versions or [], "resolution_rule": reason,
            "confidence": "OBJECTIVE" if not blocking else "UNRESOLVED",
            "blocking": blocking, "notes": "No inferred historical availability."}


def _fact(key: tuple[str, ...], row: Candidate, version: int,
          equivalents: list[Candidate], evidence: dict[tuple[str, str], TemporalEvidence]
          ) -> dict[str, Any]:
    proof = evidence.get((row.chain, row.source_sha256))
    if proof:
        proof.validate(row.chain, row.source_sha256)
    return {"key": list(key), "value": row.value, "source_sha256": row.source_sha256,
            "sheet": row.sheet, "cell": row.cell, "version_no": version,
            "category": row.category, "description": row.description,
            "equivalent_sources": [_source(other) for other in sorted(equivalents, key=_stable)],
            "availability_source": proof.level if proof else "UNKNOWN",
            "available_at": proof.evidence_date if proof else None,
            "availability_evidence_id": proof.evidence_id if proof else None}


def _identity(candidates: list[Candidate]) -> tuple[dict[tuple[str, str], dict[str, set[str]]], list[dict[str, Any]]]:
    observed: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    locators: dict[tuple[str, str, str], set[tuple[str, str, str]]] = defaultdict(set)
    for row in candidates:
        if row.item and row.upc:
            observed[(row.chain, row.item)][row.upc].add(row.period)
            locators[(row.chain, row.item, row.upc)].add(_stable(row))
    ledger = []
    for (chain, item), upcs in sorted(observed.items()):
        for upc, periods in sorted(upcs.items()):
            ledger.append({"chain": chain, "ITEM": item, "UPC": upc,
                           "valid_from": min(periods), "valid_to": max(periods),
                           "validity_basis": "OBSERVED_RANGE_NOT_CATALOG_VALIDITY",
                           "observed_periods": sorted(periods),
                           "source": [list(s) for s in sorted(locators[(chain, item, upc)])],
                           "resolution": "EXPLICIT_UPC_PRESERVED",
                           "reason": "ITEM_MULTI_UPC" if len(upcs) > 1 else "EXPLICIT_MAPPING"})
    return observed, ledger


def _canonical(row: Candidate, mapping: dict[tuple[str, str], dict[str, set[str]]]
               ) -> tuple[str | None, bool]:
    upcs = mapping.get((row.chain, row.item), {})
    if row.upc:
        return row.upc, False
    if len(upcs) == 1:
        return next(iter(upcs)), False
    if len(upcs) > 1:
        # Use only explicitly observed validity. Do not extrapolate an open end.
        applicable = [upc for upc, periods in upcs.items()
                      if row.period in periods]
        if len(applicable) == 1:
            return applicable[0], False
        return None, True
    # Stable chain-scoped ITEM is allowed when no contradictory UPC bridge exists.
    return row.item or None, False


def _resolve(key: tuple[str, ...], rows: list[Candidate],
             evidence: dict[tuple[str, str], TemporalEvidence],
             snapshots: dict[str, SnapshotProof]) -> dict[str, Any]:
    direct = [row for row in rows if row.evidence_level == "A" and not row.formula]
    if not direct:
        return _decision(key, rows, "INSUFFICIENT_EVIDENCE", "EXCLUDED_NON_FACT")
    primary = [row for row in direct if row.authority_role == "PRIMARY"]
    authority_selected = bool(primary and all(row.authority_role in {"PRIMARY", "SECONDARY_CONTROL"} for row in direct))
    if authority_selected:
        direct = primary
    by_source: dict[str, set[str]] = defaultdict(set)
    for row in direct:
        by_source[row.source_sha256].add(row.value)
    if any(len(values) > 1 for values in by_source.values()):
        return _decision(key, rows, "INTERNAL_SOURCE_DUPLICATE", "VALUE_BLOCKED", blocking=True)
    if len({row.value for row in direct}) == 1:
        winner = min(direct, key=_stable)
        versions = [_fact(key, winner, 1, rows, evidence)]
        reason = ("SOURCE_AUTHORITY" if authority_selected and any(row.value != winner.value for row in rows)
                  else "DIRECT_VS_SUMMARY" if any(row.value != winner.value for row in rows)
                  else "EQUIVALENT_DUPLICATE" if len(rows) > 1
                  else "CONFIRMED_ZERO" if winner.value == "0" else "DIRECT_FACT")
        return _decision(key, rows, reason, "AUTO_RESOLVABLE", winner=winner, versions=versions)
    by_cutoff: dict[str, list[Candidate]] = defaultdict(list)
    for row in direct:
        by_cutoff[row.source_cutoff].append(row)
    if any(len({row.value for row in group}) > 1 for group in by_cutoff.values()):
        return _decision(key, rows, "SAME_SNAPSHOT_CONFLICT", "VALUE_BLOCKED", blocking=True)
    proofs = [snapshots.get(row.source_sha256) for row in direct]
    sequences = {proof.sequence for proof in proofs if proof}
    # Caller must supply reviewed cutoff proof and a comparable sequence. Neither
    # filenames, filesystem dates, nor the maximum data period constitute proof.
    revision_safe = (len(sequences) == 1 and all(proof is not None for proof in proofs)
                     and all(proof.cutoff == row.source_cutoff and proof.cutoff >= row.period
                             for row, proof in zip(direct, proofs) if proof))
    if not revision_safe:
        return _decision(key, rows, "CROSS_SOURCE_CONFLICT", "VALUE_BLOCKED", blocking=True)
    versions = []
    previous = None
    for _, group in sorted(by_cutoff.items()):
        winner = min(group, key=_stable)
        if winner.value != previous:
            versions.append(_fact(key, winner, len(versions) + 1, group, evidence))
            previous = winner.value
        else:
            versions[-1]["equivalent_sources"].extend(_source(other) for other in sorted(group, key=_stable))
    return _decision(key, rows, "VALIDATED_REVISION", "RESOLVABLE_AS_REVISION",
                     winner=winner, versions=versions)


def reconcile_historical(manifests: list[dict[str, Any]], candidates: list[Candidate], *,
                         excluded: list[dict[str, Any]] | None = None,
                         baseline: dict[str, Any] | None = None,
                         evidence: dict[tuple[str, str], TemporalEvidence] | None = None,
                         snapshots: dict[str, SnapshotProof] | None = None,
                         scoped_snapshots: dict[tuple[str, ...], dict[str, SnapshotProof]] | None = None
                         ) -> dict[str, Any]:
    evidence = evidence or {}
    for (chain, digest), proof in evidence.items():
        proof.validate(chain, digest)
    snapshots = snapshots or {}
    for digest, proof in snapshots.items():
        proof.validate(digest)
    for registry in (scoped_snapshots or {}).values():
        for digest, proof in registry.items():
            proof.validate(digest)
    mapping, identity_ledger = _identity(candidates)
    ledger = []
    by_product: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for row in candidates:
        product, ambiguous = _canonical(row, mapping)
        key = (row.chain, product or row.item, row.period, row.metric)
        status, value = number(row.value)
        if not row.fact_eligible:
            ledger.append(_decision(key, [row], "WRONG_GRAIN_EXCLUDED", "EXCLUDED_NON_FACT"))
        elif row.formula or row.evidence_level == "D":
            ledger.append(_decision(key, [row], "FORMULA_UNVERIFIED", "EXCLUDED_NON_FACT"))
        elif status != "VALUE":
            ledger.append(_decision(key, [row], "MISSING" if status == "MISSING" else "INVALID_VALUE", "EXCLUDED_NON_FACT"))
        elif ambiguous or not product:
            ledger.append(_decision(key, [row], "ITEM_ONLY_AMBIGUOUS" if ambiguous else "INSUFFICIENT_EVIDENCE",
                                    "IDENTITY_BLOCKED", blocking=True))
        else:
            # Normalize numeric representations before duplicate comparison.
            by_product[(row.chain, product)].append(replace(row, value=value))
    first_active = {product: min((row.period for row in rows
                                  if row.evidence_level == "A" and Decimal(row.value) > 0), default=None)
                    for product, rows in by_product.items()}
    grouped: dict[tuple[str, ...], list[Candidate]] = defaultdict(list)
    for product, rows in sorted(by_product.items()):
        for row in rows:
            key = (*product, row.period, row.metric)
            start = first_active[product]
            if row.value == "0" and (start is None or row.period < start):
                ledger.append(_decision(key, [row], "UNPROVEN_ZERO" if start is None else "PRE_LAUNCH_ZERO", "EXCLUDED_NON_FACT"))
            else:
                grouped[key].append(row)
    resolved = {key: _resolve(key, rows, evidence, (scoped_snapshots or {}).get(key, snapshots))
                for key, rows in sorted(grouped.items())}
    ledger.extend(resolved.values())
    # Non-facts are retained at their original hash/locator. They never create facts.
    for row in excluded or []:
        ledger.append({**row, "classification": "EXCLUDED_NON_FACT", "sources": [row],
                       "values": [], "canonical_value": None, "canonical_source": None,
                       "version_no": None, "versions": [], "resolution_rule": row["reason_code"],
                       "confidence": "OBJECTIVE", "notes": "Excluded at source parsing."})
    file_duplicates = [m for m in manifests if m.get("duplicate_relationship")]
    for manifest in file_duplicates:
        ledger.append({"key": ["SOURCE", manifest["sha256"]], "sources": [manifest],
                       "values": [], "classification": "AUTO_RESOLVABLE",
                       "reason_code": "EQUIVALENT_DUPLICATE", "canonical_value": None,
                       "canonical_source": manifest["duplicate_relationship"], "version_no": None,
                       "versions": [], "resolution_rule": "SHA256_IDENTICAL_FILE", "confidence": "OBJECTIVE",
                       "blocking": False, "notes": "One representation; copy retained in manifest."})
    for manifest in manifests:
        if not manifest.get("duplicate_relationship"):
            for sheet in manifest.get("sheets", []):
                if sheet["classification"] == "UNSUPPORTED_OR_AGGREGATE":
                    ledger.append({"key": ["SHEET", manifest["sha256"], sheet["sheet"]],
                                   "sources": [{"source_sha256": manifest["sha256"], "sheet": sheet["sheet"]}],
                                   "values": [], "reason_code": "AGGREGATE_ONLY",
                                   "classification": "EXCLUDED_NON_FACT", "canonical_value": None,
                                   "canonical_source": None, "version_no": None, "versions": [],
                                   "resolution_rule": "NO_SUPPORTED_PRODUCT_ACTUAL_PROFILE",
                                   "confidence": "OBJECTIVE", "blocking": False,
                                   "notes": "Not asserted to contain product facts; no expansion."})
    selected = [decision["versions"][-1] for decision in resolved.values() if decision["versions"]]
    versions = [version for decision in resolved.values() for version in decision["versions"]]
    aliases = []
    for product, rows in sorted(by_product.items()):
        names = sorted({row.description for row in rows if row.description})
        if len(names) > 1:
            aliases.append({"chain": product[0], "product_code": product[1],
                            "descriptions": names, "reason": "DESCRIPTION_ALIAS"})
    baseline_matrix = []
    excluded_keys = defaultdict(list)
    for decision in ledger:
        excluded_keys[tuple(decision["key"])].append(decision)
    if baseline is not None:
        baseline_hashes = {m["sha256"] for m in baseline["manifest"]}
        if baseline_hashes != {m["sha256"] for m in manifests}:
            raise ValueError("baseline_source_hash_mismatch")
        for old in baseline["conflicts"]:
            key = tuple(old["key"])
            decision = resolved.get(key)
            alternatives = excluded_keys.get(key, [])
            if decision is None and alternatives:
                decision = next((d for d in alternatives if d["blocking"]), alternatives[0])
            if decision is None:
                raise ValueError("baseline_conflict_unaccounted")
            # Preserve the original conflict and every original source locator.
            baseline_matrix.append({"key": list(key), "original": old,
                                    "classification": decision["classification"],
                                    "reason_code": decision["reason_code"], "blocking": decision["blocking"]})
        if len({tuple(row["key"]) for row in baseline_matrix}) != len(baseline["conflicts"]):
            raise ValueError("duplicate_baseline_conflict")
    counts = Counter(row["reason_code"] for row in ledger)
    products = {tuple(row["key"][:2]) for row in selected}
    categories = {(row.chain, row.category) for rows in by_product.values() for row in rows
                  if (row.chain, _canonical(row, mapping)[0]) in products and row.category != "UNCLASSIFIED"}
    periods = sorted({row["key"][2] for row in selected})
    coverage = {}
    for chain in sorted({product[0] for product in products}):
        chain_rows = [row for row in selected if row["key"][0] == chain]
        coverage[chain] = {"products": sum(p[0] == chain for p in products),
                           "period_min": min(row["key"][2] for row in chain_rows),
                           "period_max": max(row["key"][2] for row in chain_rows),
                           "by_year": dict(sorted(Counter(row["key"][2][:4] for row in chain_rows).items()))}
    blocking = [row for row in ledger if row["blocking"]]
    content_hash = hashlib.sha256(json.dumps(versions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    preview = {"files": len(manifests), "chains": len(coverage), "products": len(products),
               "preliminary_identities": len(by_product), "categories": len(categories),
               "period_min": periods[0] if periods else None, "period_max": periods[-1] if periods else None,
               "observations": dict(sorted(Counter(row["key"][3] for row in selected).items())),
               "version_rows": len(versions), "revisions": len(versions) - len(selected),
               "confirmed_zero": sum(row["value"] == "0" for row in selected),
               "equivalent_extra_sources": sum(sum(s["value"] == row["value"]
                                                     for s in row["equivalent_sources"]) - 1 for row in selected),
               "excluded": {reason: counts[reason] for reason in
                            ("MISSING", "PRE_LAUNCH_ZERO", "UNPROVEN_ZERO", "FORMULA_UNVERIFIED",
                             "INVALID_VALUE", "AGGREGATE_ONLY", "INSUFFICIENT_EVIDENCE")},
               "reason_counts": dict(sorted(counts.items())), "blocking_decisions": len(blocking),
               "blocking_keys": len({tuple(row["key"]) for row in blocking}),
               "initial_conflicts": len(baseline_matrix),
               "initial_matrix": {name: sum(row["classification"] == name for row in baseline_matrix) for name in CLASSIFICATIONS},
               "initial_reasons": dict(sorted(Counter(row["reason_code"] for row in baseline_matrix).items())),
               "initial_by_year": {year: dict(sorted(Counter(row["reason_code"] for row in baseline_matrix
                                                             if row["key"][2].startswith(year)).items()))
                                   for year in sorted({row["key"][2][:4] for row in baseline_matrix})},
               "item_multi_upc": sum(len(upcs) > 1 for upcs in mapping.values()),
               "item_only_ambiguous": counts["ITEM_ONLY_AMBIGUOUS"], "description_aliases": len(aliases),
               "temporal": {level: sum(row["availability_source"] == level for row in versions)
                            for level in ("UNKNOWN", "E1", "E2", "E3")},
               "fabricated_available_at": 0, "coverage": coverage,
               "dataset_sha256": content_hash,
               "source_hashes": sorted({m["sha256"] for m in manifests})}
    return {"manifest": manifests, "summary": preview, "selected": selected,
            "versions": versions, "resolution_ledger": ledger, "identity_ledger": identity_ledger,
            "snapshot_proofs": {digest: asdict(proof) for digest, proof in sorted(snapshots.items())},
            "temporal_evidence": [asdict(proof) for _, proof in sorted(evidence.items())],
            "description_aliases": aliases, "baseline_conflicts": baseline_matrix,
            "blocking": bool(blocking or not selected),
            "bootstrap_status": "BLOCKED" if blocking or not selected else "PREFLIGHT_REQUIRED"}


def publication_preflight(report: dict[str, Any], *, golden_pass: bool, tests_pass: bool,
                          project_ref: str, remote_gate_pass: bool) -> None:
    if (report["blocking"] or any(row["blocking"] for row in report["resolution_ledger"])
            or not report["selected"] or not golden_pass or not tests_pass
            or not remote_gate_pass or project_ref != "bskoyqhbgrycpwhydnnr"):
        raise ValueError("historical_bootstrap_blocked")
    digest = hashlib.sha256(json.dumps(report["versions"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != report["summary"]["dataset_sha256"]:
        raise ValueError("canonical_dataset_modified")
    if any(row["availability_source"] == "SYSTEM_INGESTION" or
           (row["availability_source"] == "UNKNOWN" and row["available_at"] is not None)
           for row in report["versions"]):
        raise ValueError("invalid_historical_availability")


class HistoricalBootstrapRepository(Protocol):
    """Server-only port. No real credentials or remote implementation here.

    Implementations must create chain/category/product/evidence/batch before
    facts, commit facts + checkpoint atomically per batch, return the SAME result
    for the same idempotency key, and reject a hash mismatch. Never DELETE.
    An adapter must be certified against PostgreSQL/RLS before productive use.
    """

    project_ref: str

    def commit_batch(self, idempotency_key: str, facts: list[dict[str, Any]]) -> int: ...


def bootstrap_historical(report: dict[str, Any], repository: HistoricalBootstrapRepository, *,
                         golden_pass: bool, tests_pass: bool, remote_gate_pass: bool) -> dict[str, int]:
    publication_preflight(report, golden_pass=golden_pass, tests_pass=tests_pass,
                          project_ref=repository.project_ref, remote_gate_pass=remote_gate_pass)
    batches = defaultdict(list)
    for row in report["versions"]:
        batches[(row["key"][0], row["source_sha256"])].append(row)
    results = {}
    for (chain, source_hash), facts in sorted(batches.items()):
        batch_hash = hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        key = hashlib.sha256(f"{chain}:{source_hash}:{batch_hash}".encode()).hexdigest()
        count = repository.commit_batch(key, facts)
        if count != len(facts):
            raise RuntimeError("historical_batch_count_mismatch")
        results[key] = count
    return results


def regression_sample(report: dict[str, Any], candidates: list[Candidate]) -> dict[str, Any]:
    """One stable direct-source sample per chain, checked against original literals."""
    source = {_stable(row): row for row in candidates if row.evidence_level == "A" and not row.formula}
    by_chain = defaultdict(list)
    for row in report["selected"]:
        by_chain[row["key"][0]].append(row)
    mismatches = 0
    for rows in by_chain.values():
        row = min(rows, key=lambda value: tuple(value["key"]))
        direct = source.get((row["source_sha256"], row["sheet"], row["cell"]))
        if direct is None or Decimal(direct.value) != Decimal(row["value"]):
            mismatches += 1
    return {"chains_sampled": len(by_chain), "mismatches": mismatches,
            "status": "PASS" if by_chain and not mismatches else "FAIL"}
