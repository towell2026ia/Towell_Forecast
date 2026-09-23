"""Append-only source evidence and explicit historical vintage lifecycle."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .availability import TemporalAvailabilityAuditor
from .persistence import PersistenceProvider, content_hash

EVIDENCE_LEVELS = {
    "E1": "immutable_corporate_system_log",
    "E2": "verifiable_external_receipt_or_repository",
    "E3": "original_file_hash_local_metadata_documented_review",
    "E4": "documented_operating_rule",
    "E5": "inference_not_permitted",
    "E6": "unknown_not_permitted",
}
ELIGIBLE_LEVELS = {"E1", "E2", "E3"}
STATES = ("CANDIDATE", "TEMPORALLY_VALID", "VALIDATED", "FROZEN", "REJECTED")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceRegistry:
    def __init__(self, persistence: PersistenceProvider, auditor: TemporalAvailabilityAuditor):
        self.persistence = persistence
        self.auditor = auditor

    def register_local_source(self, source_dir: Path, rule_id: str) -> dict[str, Any]:
        """Recheck the full mapping before recording an E3 manifest.

        Filesystem timestamps are historical claims, not tamper-proof receipts.
        """
        rule = self.auditor.registry.rules[rule_id]
        if not rule.get("enabled"):
            raise ValueError("availability_rule_disabled")
        audit = self.auditor.backfill_availability(source_dir, dry_run=True)
        verified = next((item for item in audit["verified_files"] if item["rule_id"] == rule_id), None)
        if not verified or not verified["verified_records"]:
            raise ValueError("source_has_no_verified_cells")
        source = Path(source_dir) / rule["source_file"]
        stat = source.stat()
        if hashlib.sha256(source.read_bytes()).hexdigest().casefold() != rule["sha256"].casefold():
            raise ValueError("source_file_sha256_mismatch")
        local_created = datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat()
        local_modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        if datetime.fromisoformat(local_created) < datetime.fromisoformat(rule["local_created_at"].replace("Z", "+00:00")) or \
                datetime.fromisoformat(local_modified) < datetime.fromisoformat(rule["local_last_write_at"].replace("Z", "+00:00")):
            raise ValueError("local_metadata_mismatch")
        body = {
            "source_id": f"SRC-{rule_id}", "filename": source.name,
            "sha256": rule["sha256"].lower(), "file_size": stat.st_size,
            "local_created_at": rule["local_created_at"],
            "local_modified_at": rule["local_last_write_at"],
            "current_local_created_at": local_created,
            "current_local_modified_at": local_modified,
            "evidence_date": rule["available_at"][:10],
            "evidence_type": "local_file_metadata",
            "evidence_level": "E3", "confidence": "documented_local",
            "verified_cells": verified["verified_records"],
            "rule_id": rule_id, "review": rule["evidence_reference"],
            "limitation": "Local timestamps can change; this is not an external receipt or immutable log.",
        }
        manifest_id = f"EM-{content_hash(body)[:20]}"
        manifest = {"evidence_manifest_id": manifest_id, **body}
        self.persistence.put("source_evidence", manifest_id, manifest)
        self.persistence.put("availability_rules", rule_id, rule)
        return manifest

    def append_external(self, manifest: dict[str, Any],
                        verifier: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        """Add independently verifiable E1/E2 evidence without rewriting E3."""
        if manifest.get("evidence_level") not in {"E1", "E2"} or not all(
            manifest.get(key) for key in ("source_id", "sha256", "evidence_date", "evidence_reference")
        ):
            raise ValueError("external_evidence_incomplete")
        if not verifier(manifest):
            raise ValueError("external_evidence_not_verified")
        body = {key: value for key, value in manifest.items() if key != "evidence_manifest_id"}
        result = {"evidence_manifest_id": f"EM-{content_hash(body)[:20]}", **body}
        self.persistence.put("source_evidence", result["evidence_manifest_id"], result)
        return result


class VintageRegistry:
    def __init__(self, persistence: PersistenceProvider, state_dir: Path):
        self.persistence = persistence
        self.state_dir = Path(state_dir)

    def get(self, vintage_id: str) -> dict[str, Any] | None:
        return self.persistence.get("vintage_registry", vintage_id)

    def all(self) -> list[dict[str, Any]]:
        return self.persistence.list("vintage_registry")

    def transition(self, vintage_id: str, status: str, **fields: Any) -> dict[str, Any]:
        if status not in STATES:
            raise ValueError("invalid_vintage_state")
        previous = self.get(vintage_id)
        allowed = {None: {"CANDIDATE"}, "CANDIDATE": {"TEMPORALLY_VALID", "REJECTED"},
                   "TEMPORALLY_VALID": {"VALIDATED", "REJECTED"},
                   "VALIDATED": {"FROZEN", "REJECTED"}, "FROZEN": set(), "REJECTED": set()}
        prior_status = previous["status"] if previous else None
        if status not in allowed[prior_status]:
            raise ValueError("invalid_vintage_transition")
        if status == "FROZEN" and (not fields.get("evidence_manifest_id", (previous or {}).get("evidence_manifest_id")) or
                                   not fields.get("validation_status", (previous or {}).get("validation_status"))):
            raise ValueError("frozen_vintage_lacks_evidence_or_validation")
        entry = {**(previous or {}), **fields, "vintage_id": vintage_id, "status": status,
                 "frozen": status == "FROZEN", "updated_at": _now()}
        self.persistence.put("vintage_registry", vintage_id, entry)
        self.persistence.put("run_logs", f"vintage-transition-{vintage_id}-{status}",
                             {"vintage_id": vintage_id, "from": prior_status, "to": status,
                              "timestamp": entry["updated_at"]})
        return entry

    def import_frozen(self, vintage_id: str, evidence_manifest_id: str,
                      *, validation_status: str = "VALID_WITH_DOCUMENTED_LOCAL_EVIDENCE") -> dict[str, Any]:
        existing = self.get(vintage_id)
        if existing:
            if existing.get("evidence_manifest_id") != evidence_manifest_id:
                raise ValueError("frozen_vintage_evidence_conflict")
            return existing
        evidence = self.persistence.get("source_evidence", evidence_manifest_id)
        if not evidence or evidence["evidence_level"] not in ELIGIBLE_LEVELS:
            raise ValueError("vintage_evidence_ineligible")
        path = self.state_dir / "vintages" / f"{vintage_id}.json"
        vintage = json.loads(path.read_text(encoding="utf-8"))
        run_id = vintage_id.removeprefix("V-")
        run = json.loads((self.state_dir / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
        if not vintage.get("frozen") or run.get("status") != "COMPLETED" or \
                vintage.get("hash") != run.get("vintage_hash") or len(vintage.get("forecasts", [])) != 12 or \
                evidence["evidence_date"] > vintage["cutoff_date"] or \
                not all(all(key in (row.get("probability") or {}) for key in ("p10", "p50", "p90", "p95"))
                        for row in vintage["forecasts"]):
            raise ValueError("existing_vintage_not_validated")
        if content_hash({key: value for key, value in vintage.items() if key != "hash"}) != vintage["hash"]:
            raise ValueError("existing_vintage_hash_mismatch")
        self.persistence.put("forecast_vintages", vintage_id, vintage)
        self.persistence.put("monthly_runs", run_id, run)
        fields = {"chain": "Walmart", "pilot_scope": "FENDI BD",
                  "cutoff_date": vintage["cutoff_date"], "evidence_manifest_id": evidence_manifest_id,
                  "evidence_level": evidence["evidence_level"],
                  "research_snapshot_id": vintage["research_snapshot_id"],
                  "data_snapshot_id": vintage["data_snapshot_id"],
                  "forecast_version": vintage["forecast_version"],
                  "champion_at_cutoff": run.get("champion"),
                  "model_versions": run.get("versions", {}),
                  "horizons": len(vintage["forecasts"]),
                  "vintage_hash": vintage["hash"]}
        self.transition(vintage_id, "CANDIDATE", **fields)
        self.transition(vintage_id, "TEMPORALLY_VALID")
        self.transition(vintage_id, "VALIDATED", validation_status=validation_status)
        for horizon, row in enumerate(vintage["forecasts"], 1):
            self.persistence.put("forecast_horizons", f"{vintage_id}-{horizon:02d}",
                                 {"vintage_id": vintage_id, "horizon": horizon, "forecast": row})
            self.persistence.put("forecast_bands", f"{vintage_id}-{horizon:02d}",
                                 {"vintage_id": vintage_id, "horizon": horizon,
                                  "bands": row.get("probability", {})})
        return self.transition(vintage_id, "FROZEN")
