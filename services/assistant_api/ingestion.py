"""Trusted ingestion timestamps and evidence-backed availability for normalized rows."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable

from .persistence import PersistenceProvider
from .availability import _identity


class AvailabilitySource(StrEnum):
    SYSTEM_INGESTION = "SYSTEM_INGESTION"
    DOCUMENTED_RECEIPT = "DOCUMENTED_RECEIPT"
    CORPORATE_LOG = "CORPORATE_LOG"
    LEGACY_EVIDENCE = "LEGACY_EVIDENCE"
    UNKNOWN = "UNKNOWN"


EVIDENCE_LEVELS = frozenset({"E1", "E2", "E3", "UNKNOWN"})
REQUIRED_OBSERVATION_FIELDS = frozenset({
    "chain_id", "product_id", "product_code", "description", "category", "variant",
    "objective", "period", "value", "is_missing", "available_at",
    "availability_source", "availability_evidence_id", "import_batch_id",
})


def utc_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _missing(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.casefold() in {"true", "false"}:
        return value.casefold() == "true"
    raise ValueError("invalid_missing_marker")


@dataclass(frozen=True)
class ImportBatch:
    batch_id: str
    source_name: str
    filename: str
    sha256: str
    period: str
    uploaded_at: str
    uploaded_by: str
    availability_source: str
    evidence_id: str | None
    status: str


class ImportService:
    """Record a real server receipt. Caller cannot supply or backdate uploaded_at."""

    def __init__(self, persistence: PersistenceProvider,
                 clock: Callable[[], datetime] | None = None):
        self.persistence = persistence
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def ingest(self, source_bytes: bytes, *, source_name: str, filename: str,
               period: str, uploaded_by: str, rows: list[dict[str, Any]]) -> ImportBatch:
        if not source_bytes or not source_name or not filename or not uploaded_by or not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", period):
            raise ValueError("invalid_import_metadata")
        received = self._clock()
        if received.tzinfo is None:
            raise ValueError("ingestion_clock_must_be_aware")
        uploaded_at = utc_timestamp(received.isoformat())
        digest = hashlib.sha256(source_bytes).hexdigest()
        batch_id = f"IB-{uuid.uuid4().hex}"
        batch = ImportBatch(batch_id, source_name, filename, digest, period,
                            uploaded_at, uploaded_by, AvailabilitySource.SYSTEM_INGESTION,
                            None, "IMPORTED")
        normalized = []
        for row in rows:
            if row.get("period") != period or not row.get("chain_id") or not row.get("product_id") or not row.get("objective"):
                raise ValueError("invalid_normalized_observation")
            normalized.append({**row, "available_at": uploaded_at,
                               "availability_source": AvailabilitySource.SYSTEM_INGESTION,
                               "availability_evidence_id": None, "import_batch_id": batch_id,
                               "is_missing": _missing(row.get("is_missing", False)),
                               "product_code": row.get("product_code", ""),
                               "description": row.get("description", ""),
                               "category": row.get("category", ""),
                               "variant": row.get("variant", "")})
        self.persistence.put("import_batches", batch_id, asdict(batch))
        for index, row in enumerate(normalized):
            self.persistence.put("normalized_observations", f"{batch_id}-{index:06d}", row)
        return batch


def resolve_availability(row: dict[str, Any], persistence: PersistenceProvider,
                         *, legacy_auditor: Any | None = None) -> dict[str, Any] | None:
    """Only trusted import records or registry-verified historical assignments qualify."""
    batch_id = row.get("import_batch_id")
    if batch_id:
        batch = persistence.get("import_batches", str(batch_id))
        if batch and batch.get("status") == "IMPORTED" and batch.get("availability_source") == AvailabilitySource.SYSTEM_INGESTION and row.get("available_at") == batch.get("uploaded_at") and row.get("period") == batch.get("period"):
            return {**row, "availability_source": AvailabilitySource.SYSTEM_INGESTION,
                    "availability_evidence_id": None}
        return None
    if legacy_auditor is None:
        return None
    assignments = legacy_auditor._assignments()
    assigned = assignments.get(_identity(row))
    if not assigned:
        return None
    resolved = legacy_auditor.resolve(row)
    if resolved.get("availability_confidence") not in {"verified", "documented"}:
        return None
    timestamp = resolved.get("available_at")
    source_hash = resolved.get("source_sha256")
    if (not timestamp or not source_hash or assigned.get("available_at") != timestamp
            or assigned.get("source_sha256", "").casefold() != source_hash.casefold()):
        return None
    for evidence in persistence.list("source_evidence"):
        if (evidence.get("evidence_level") in {"E1", "E2", "E3"}
                and evidence.get("sha256", "").casefold() == source_hash.casefold()
                and evidence.get("evidence_date") == timestamp[:10]
                and (evidence.get("evidence_level") != "E3" or
                     (evidence.get("verified_cells", 0) > 0 and evidence.get("review")))
                and (evidence.get("rule_id") is None or evidence.get("rule_id") == resolved.get("availability_rule_id"))):
            return {**resolved, "available_at": utc_timestamp(timestamp),
                    "availability_source": AvailabilitySource.LEGACY_EVIDENCE,
                    "availability_evidence_id": evidence["evidence_manifest_id"],
                    "import_batch_id": None}
    return None
