"""Generic server-only source policy. Private configuration, no pilot branches.

Approvals are administrative review records, never frontend role/identity claims.
Registry evidence must be verified from immutable source bytes before use.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from services.assistant_api.historical_corpus import Candidate, HEADER_PERIOD, MONTHLY_METRICS
from services.assistant_api.historical_reconciliation import SnapshotProof, publication_preflight

ROLES = {"PRIMARY", "SECONDARY_CONTROL", "SUMMARY_ONLY", "EXCLUDED_DERIVED", "REVISION_SOURCE", "UNRESOLVED"}
METRICS = {"SALES", "ORDER", "DELIVERY"}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _timestamp(value: str) -> None:
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError()
    except (ValueError, AttributeError) as error:
        raise ValueError("approval_timestamp_required") from error


def _hash(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("source_hash_required")


def _period(value: str) -> None:
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", value):
        raise ValueError("period_required")


def verify_reference(ref: dict[str, Any], registry: dict[str, dict[str, Any]]) -> dict[str, Any]:
    verified = registry.get(ref.get("evidence_hash", ""))
    if (verified is None or fingerprint(verified) != ref.get("evidence_hash")
            or any(ref.get(field) != verified.get(field) for field in ("source_hash", "sheet", "locator", "kind"))):
        raise ValueError("unverified_source_evidence")
    return verified


@dataclass(frozen=True)
class AuthorityRule:
    authority_rule_id: str
    chain: str
    valid_from: str
    valid_to: str
    metric: str
    product_scope: str | tuple[str, ...]
    source_hash: str
    sheet: str
    grain: str
    evidence_level: str
    authority_role: str
    reason: str
    evidence: tuple[dict[str, Any], ...]
    approved: bool = False
    approved_by: str = ""
    approved_at: str = ""
    blocking_if_absent: bool = True
    fact_keys: tuple[tuple[str, ...], ...] = ()

    def validate(self, hashes: set[str], registry: dict[str, dict[str, Any]]) -> None:
        _hash(self.source_hash)
        _period(self.valid_from)
        _period(self.valid_to)
        if (self.source_hash not in hashes or self.valid_from > self.valid_to
                or self.metric not in METRICS or self.authority_role not in ROLES
                or self.evidence_level not in {"A", "B", "C", "D", "E"}
                or not all((self.authority_rule_id, self.chain, self.sheet, self.grain, self.reason))
                or not (self.product_scope == "*" or isinstance(self.product_scope, tuple) and self.product_scope)):
            raise ValueError("invalid_authority_rule")
        if not self.approved:
            return
        if not self.approved_by or not self.evidence:
            raise ValueError("authority_approval_evidence_required")
        _timestamp(self.approved_at)
        proofs = [verify_reference(ref, registry) for ref in self.evidence]
        if any(proof["source_hash"] != self.source_hash for proof in proofs):
            raise ValueError("authority_evidence_hash_mismatch")
        required = {"PRIMARY": "SOURCE_AUTHORITY", "SECONDARY_CONTROL": "SOURCE_AUTHORITY",
                    "SUMMARY_ONLY": "AGGREGATED_MEASURE_HEADER", "EXCLUDED_DERIVED": "GRAIN_DISJOINT"}
        assertion = required.get(self.authority_role)
        if assertion and not any(proof.get("assertion") == assertion for proof in proofs):
            raise ValueError("authority_assertion_required")
        if self.authority_role in {"PRIMARY", "SECONDARY_CONTROL", "EXCLUDED_DERIVED"}:
            scope = {"chain": self.chain, "valid_from": self.valid_from, "valid_to": self.valid_to,
                     "metric": self.metric, "product_scope": self.product_scope, "grain": self.grain}
            if not any(proof.get("assertion") == assertion and fingerprint(proof.get("scope")) == fingerprint(scope)
                       and proof.get("sheet") == self.sheet for proof in proofs):
                raise ValueError("authority_evidence_scope_mismatch")
        if self.authority_role == "SUMMARY_ONLY":
            header = next(proof for proof in proofs if proof.get("assertion") == assertion)
            text = header.get("content", "")
            match = HEADER_PERIOD.search(text)
            metrics = {metric for fragment, metric in MONTHLY_METRICS.items() if match and fragment in match[3]}
            if (header["kind"] != "STRUCTURAL_HEADER" or not text.startswith(("Suma de ", "Sum of "))
                    or metrics != {self.metric} or not match or self.valid_from != f"{match[1]}-{match[2]}"
                    or self.valid_to != self.valid_from or header["sheet"] != self.sheet):
                raise ValueError("aggregated_header_scope_mismatch")

    def matches(self, row: Candidate, product: str | None = None) -> bool:
        product = product or row.upc or row.item
        key = (row.chain, product, row.period, row.metric)
        return (self.approved and row.chain == self.chain and row.source_sha256 == self.source_hash
                and row.sheet == self.sheet and self.metric == row.metric
                and self.valid_from <= row.period <= self.valid_to
                and (self.product_scope == "*" or product in self.product_scope)
                and (not self.fact_keys or key in self.fact_keys))


@dataclass(frozen=True)
class SnapshotPairProof:
    proof_id: str
    chain: str
    older_source_hash: str
    newer_source_hash: str
    sequence_name: str
    older_cutoff: str
    newer_cutoff: str
    scope: dict[str, Any]
    evidence_locator: str
    evidence_hash: str
    review_note: str
    approved_by: str
    approved_at: str

    def validate(self, hashes: set[str], registry: dict[str, dict[str, Any]]) -> None:
        for digest in (self.older_source_hash, self.newer_source_hash):
            _hash(digest)
        for period in (self.older_cutoff, self.newer_cutoff, self.scope.get("valid_from", ""), self.scope.get("valid_to", "")):
            _period(period)
        _timestamp(self.approved_at)
        proof = registry.get(self.evidence_hash)
        if (not all((self.proof_id, self.chain, self.sequence_name, self.review_note, self.approved_by, self.evidence_locator))
                or not {self.older_source_hash, self.newer_source_hash} <= hashes
                or self.older_cutoff >= self.newer_cutoff
                or self.scope["valid_from"] > self.scope["valid_to"]
                or not self.scope.get("metrics") or not set(self.scope["metrics"]) <= METRICS
                or not self.scope.get("product_scope") or proof is None
                or fingerprint(proof) != self.evidence_hash or proof.get("assertion") != "COMPARABLE_SNAPSHOT_SEQUENCE"
                or proof.get("locator") != self.evidence_locator
                or set(proof.get("source_hashes", [])) != {self.older_source_hash, self.newer_source_hash}
                or proof.get("chain") != self.chain or proof.get("scope") != self.scope
                or proof.get("older_cutoff") != self.older_cutoff or proof.get("newer_cutoff") != self.newer_cutoff):
            raise ValueError("invalid_snapshot_pair_proof")

    def covers(self, row: Candidate, product: str | None = None) -> bool:
        scope = self.scope
        return (row.chain == self.chain and scope["valid_from"] <= row.period <= scope["valid_to"]
                and row.metric in scope["metrics"] and (scope["product_scope"] == "*"
                or (product or row.upc or row.item) in scope["product_scope"]))


class AdjudicationConfig:
    def __init__(self, rules: list[AuthorityRule], proofs: list[SnapshotPairProof], *,
                 hashes: set[str], registry: dict[str, dict[str, Any]]) -> None:
        self.rules, self.proofs = rules, proofs
        for rule in rules:
            rule.validate(hashes, registry)
        for proof in proofs:
            proof.validate(hashes, registry)
        if len({r.authority_rule_id for r in rules}) != len(rules) or len({p.proof_id for p in proofs}) != len(proofs):
            raise ValueError("duplicate_adjudication_id")

    def apply(self, rows: list[Candidate]) -> tuple[list[Candidate], dict[tuple[str, ...], dict[str, SnapshotProof]]]:
        from services.assistant_api.historical_reconciliation import _canonical, _identity
        mapping, _ = _identity(rows)
        result = []
        scoped = {}
        for row in rows:
            product, ambiguous = _canonical(row, mapping)
            matches = [rule for rule in self.rules if not ambiguous and rule.matches(row, product)]
            if len(matches) > 1:
                raise ValueError("overlapping_authority_rules")
            if matches:
                rule = matches[0]
                row = replace(row, authority_role=rule.authority_role, authority_rule_id=rule.authority_rule_id,
                              original_evidence_level=row.evidence_level,
                              evidence_level="C" if rule.authority_role == "SUMMARY_ONLY" else row.evidence_level,
                              fact_eligible=rule.authority_role != "EXCLUDED_DERIVED")
            result.append(row)
            applicable = [proof for proof in self.proofs if not ambiguous and proof.covers(row, product)]
            if len(applicable) > 1:
                raise ValueError("overlapping_snapshot_proofs")
            if applicable:
                proof = applicable[0]
                key = (row.chain, product or row.item, row.period, row.metric)
                scoped[key] = {digest: SnapshotProof(digest, proof.sequence_name, cutoff, proof.evidence_hash,
                                                    proof.evidence_locator, proof.approved_by, proof.review_note)
                               for digest, cutoff in ((proof.older_source_hash, proof.older_cutoff),
                                                      (proof.newer_source_hash, proof.newer_cutoff))}
        return result, scoped


def cluster_conflicts(report: dict[str, Any]) -> list[dict[str, Any]]:
    groups = defaultdict(list)
    for decision in report["resolution_ledger"]:
        if not decision["blocking"]:
            continue
        sources = tuple(sorted({(s["source_sha256"], s["sheet"], s["evidence_level"])
                                for s in decision["sources"] if s["evidence_level"] == "A"}))
        pattern = (decision["key"][0], decision["key"][2][:4], decision["key"][3], decision["reason_code"], sources)
        groups[pattern].append(decision)
    clusters = []
    for pattern, facts in sorted(groups.items()):
        chain, year, metric, reason, sources = pattern
        keys = sorted({tuple(d["key"]) for d in facts})
        if len(keys) != len(facts):
            raise ValueError("non_unique_blocking_key")
        clusters.append({"cluster_id": "C-" + fingerprint(pattern)[:12], "chain": chain, "year": year,
                         "period_min": min(k[2] for k in keys), "period_max": max(k[2] for k in keys),
                         "metrics": [metric], "source_pair": [list(s) for s in sources],
                         "sheet_pair": sorted({s[1] for s in sources}),
                         "grain": "CHAIN+STABLE_PRODUCT+MONTH+METRIC",
                         "identity_pattern": "EXPLICIT_UPC_OR_EXPLICIT_ITEM_BRIDGE", "count": len(keys),
                         "sample_count": min(5, len(keys)), "samples": sorted(facts, key=lambda d: tuple(d["key"]))[:5],
                         "keys": [list(k) for k in keys], "reason": reason,
                         "candidate_resolution": "REVIEW_STRUCTURAL_OR_DOCUMENTARY_EVIDENCE",
                         "evidence_required": "Grain/context/authority or comparable snapshot proof. No arbitrary winner.",
                         "blocking": True})
    if sum(c["count"] for c in clusters) != report["summary"]["blocking_keys"]:
        raise ValueError("cluster_count_mismatch")
    return sorted(clusters, key=lambda c: (-c["count"], c["cluster_id"]))


def pareto(clusters: list[dict[str, Any]]) -> dict[str, int]:
    total = sum(c["count"] for c in clusters)
    result = {}
    for threshold in (80, 90, 95, 100):
        running = 0
        result[str(threshold)] = 0
        for index, cluster in enumerate(sorted(clusters, key=lambda c: (-c["count"], c["cluster_id"])), 1):
            running += cluster["count"]
            if running * 100 >= total * threshold:
                result[str(threshold)] = index
                break
    return result


def review_pack(clusters: list[dict[str, Any]], remaining: dict[str, Any]) -> list[dict[str, Any]]:
    blocked = {tuple(d["key"]) for d in remaining["resolution_ledger"] if d["blocking"]}
    grouped = defaultdict(list)
    for cluster in clusters:
        keys = [key for key in cluster["keys"] if tuple(key) in blocked]
        if keys:
            # Group metrics sharing a source pattern: one decision can cover a rule.
            sig = (cluster["chain"], cluster["reason"], tuple(tuple(s) for s in cluster["source_pair"]))
            grouped[sig].append((cluster, keys))
    result = []
    for sig, members in sorted(grouped.items()):
        result.append({"decision_id": "D-" + fingerprint(sig)[:12], "cluster_ids": sorted(c["cluster_id"] for c, _ in members),
                       "affected_facts": sum(len(keys) for _, keys in members), "chain": sig[0],
                       "period_min": min(key[2] for _, keys in members for key in keys),
                       "period_max": max(key[2] for _, keys in members for key in keys),
                       "metrics": sorted({key[3] for _, keys in members for key in keys}),
                       "source_pair": [list(s) for s in sig[2]], "difference_pattern": sig[1],
                       "evidence": "See source inspection and cluster samples in private dossier.",
                       "recommended_interpretation": "Keep blocked until source owner supplies authority/grain/revision proof.",
                       "alternative_interpretation": "A corrected snapshot or disjoint business dimension may explain the difference, but is not established.",
                       "risk": "Wrong demand history and temporal leakage if an arbitrary value/date is selected.",
                       "user_decision_required": True, "approved": False,
                       "status": "MANUAL_REVIEW_REQUIRED"})
    return result


def validate_override(entry: dict[str, Any], clusters: list[dict[str, Any]], *,
                      hashes: set[str], registry: dict[str, dict[str, Any]]) -> None:
    required = ("decision_id", "cluster_id", "source_hashes", "scope", "selected_rule", "reason",
                "approved_by", "approved_at", "evidence_reference")
    if not all(entry.get(field) for field in required):
        raise ValueError("manual_approval_metadata_required")
    _timestamp(entry["approved_at"])
    cluster = next((c for c in clusters if c["cluster_id"] == entry["cluster_id"]), None)
    expected = {source[0] for source in cluster["source_pair"]} if cluster else set()
    if (not cluster or set(entry["source_hashes"]) != expected or not expected <= hashes
            or entry["scope"] != {"keys_sha256": fingerprint(cluster["keys"]), "count": cluster["count"]}):
        raise ValueError("manual_override_hash_or_scope_mismatch")
    verify_reference(entry["evidence_reference"], registry)


def apply_overrides(entries: list[dict[str, Any]], rules: list[AuthorityRule],
                    clusters: list[dict[str, Any]], *, hashes: set[str],
                    registry: dict[str, dict[str, Any]]) -> list[AuthorityRule]:
    """Approve an evidence-backed rule only within reviewed cluster keys.

    This is an administrative file interpreter, not a browser approval endpoint.
    No rule can change an observation value, fabricate a date, or expand scope.
    """
    result = list(rules)
    seen = set()
    for entry in entries:
        validate_override(entry, clusters, hashes=hashes, registry=registry)
        review_key = (entry["decision_id"], entry["cluster_id"], entry["selected_rule"])
        if review_key in seen:
            raise ValueError("duplicate_manual_decision")
        seen.add(review_key)
        index = next((i for i, rule in enumerate(result)
                      if rule.authority_rule_id == entry["selected_rule"]), None)
        if index is None:
            raise ValueError("unknown_manual_rule")
        rule = result[index]
        if rule.approved:
            raise ValueError("already_approved_manual_rule")
        cluster = next(c for c in clusters if c["cluster_id"] == entry["cluster_id"])
        if rule.source_hash not in entry["source_hashes"] or rule.chain != cluster["chain"]:
            raise ValueError("manual_rule_scope_mismatch")
        keys = tuple(tuple(key) for key in cluster["keys"])
        if any(key[3] != rule.metric or not rule.valid_from <= key[2] <= rule.valid_to
               or rule.product_scope != "*" and key[1] not in rule.product_scope for key in keys):
            raise ValueError("manual_rule_scope_mismatch")
        approved = replace(rule, approved=True, approved_by=entry["approved_by"],
                           approved_at=entry["approved_at"], reason=entry["reason"],
                           evidence=(*rule.evidence, entry["evidence_reference"]), fact_keys=keys)
        approved.validate(hashes, registry)
        result[index] = approved
    return result


def adjudicated_preflight(report: dict[str, Any], *, manual_reviews: list[dict[str, Any]],
                          stable_sha: bool, source_hashes: set[str], **gates: Any) -> None:
    if (any(row["user_decision_required"] and not row["approved"] for row in manual_reviews)
            or not stable_sha or set(report["summary"]["source_hashes"]) != source_hashes):
        raise ValueError("source_authority_publication_blocked")
    publication_preflight(report, **gates)


def load_adjudication_files(directory: Path, *, hashes: set[str],
                            registry: dict[str, dict[str, Any]],
                            clusters: list[dict[str, Any]]) -> AdjudicationConfig:
    """Load private administrative files with a caller-verified evidence registry.

    Never load a self-asserted registry from the policy directory or accept browser
    metadata. The administrative CLI must verify source bytes/reviewed artifact SHA.
    """
    def read(name):
        value = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError("adjudication_list_required")
        return value

    rules = []
    for value in read("source_authority_matrix"):
        value["evidence"] = tuple(value["evidence"])
        if isinstance(value["product_scope"], list):
            value["product_scope"] = tuple(value["product_scope"])
        value["fact_keys"] = tuple(tuple(key) for key in value.get("fact_keys", []))
        rules.append(AuthorityRule(**value))
    proofs = [SnapshotPairProof(**value) for value in read("snapshot_proofs")]
    rules = apply_overrides(read("manual_overrides"), rules, clusters, hashes=hashes, registry=registry)
    return AdjudicationConfig(rules, proofs, hashes=hashes, registry=registry)


def resolution_counts(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    original = {tuple(d["key"]): d for d in before["resolution_ledger"] if d["blocking"]}
    current = {tuple(d["key"]): d for d in after["resolution_ledger"] if d["versions"] or d["blocking"]}
    counts = Counter()
    for key in original:
        decision = current.get(key)
        if decision and not decision["blocking"]:
            grain_excluded = any(tuple(row["key"]) == key and row["reason_code"] == "WRONG_GRAIN_EXCLUDED"
                                 for row in after["resolution_ledger"])
            counts["WRONG_GRAIN_EXCLUDED" if grain_excluded else decision["reason_code"]] += 1
    return {"initial_blocking": len(original), "resolved": dict(sorted(counts.items())),
            "final_blocking": after["summary"]["blocking_keys"]}
