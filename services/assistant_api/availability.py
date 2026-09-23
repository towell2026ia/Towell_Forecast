"""Point-in-time evidence gate for the local FENDI BD historical runner.

The normalized CSV remains immutable. Verified assignments live in a separate
local overlay and can only be created after checking the original source bytes.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import logging
import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .data_provider import DataProvider

EVIDENCE_FILE = Path(__file__).with_name("availability_evidence.json")
GATE_VERSION = "prd08c1-availability-1"
SOURCES = {"system_timestamp", "source_file_timestamp", "monthly_closure",
           "business_rule", "manual_verified", "unknown"}
CONFIDENCE = {"verified", "documented", "inferred", "unknown"}
OFFICIAL_CONFIDENCE = {"verified", "documented"}
EVIDENCE_LEVELS = {"E1", "E2", "E3", "E4", "E5", "E6"}
OBJECTIVES = {"venta": "sales", "pedido": "orders", "entrega": "deliveries",
              "fcst cliente": "customer_forecast", "forecast cliente": "customer_forecast",
              "fcst towell": "prior_towell_forecast", "forecast towell": "prior_towell_forecast",
              "forecast ajustado": "adjusted_forecast", "forecast aprobado": "approved_forecast"}
REQUIRED_FIELDS = {"sales"}
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
LOGGER = logging.getLogger("forecast_towell.availability")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def _event(name: str, **details: Any) -> None:
    LOGGER.info(json.dumps({"event": name, "timestamp": datetime.now(timezone.utc).isoformat(),
                            **details}, ensure_ascii=False))


def _iso(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(value.strip()), datetime.min.time(),
                                    timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            return None


def _day(value: Any) -> str | None:
    normalized = _iso(value)
    return normalized[:10] if normalized else None


def _month_end(period: str) -> str:
    year, month = map(int, period.split("-"))
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def _next_period(period: str) -> str:
    year, month = map(int, period.split("-"))
    return f"{year + (month == 12):04d}-{month % 12 + 1:02d}"


def _periods(start: str, end: str) -> list[str]:
    _month_end(start)
    _month_end(end)
    if start > end:
        raise ValueError("invalid_period_range")
    result = []
    period = start
    while period <= end:
        result.append(period)
        period = _next_period(period)
    return result


def _identity(row: dict[str, str]) -> str:
    return row.get("record_hash") or hashlib.sha256(json.dumps(row, sort_keys=True,
        ensure_ascii=False).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     suffix=".tmp", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


@dataclass(frozen=True)
class MonthlyClosure:
    period: str
    closure_status: str
    closed_at: str | None
    closure_source: str
    rule_id: str
    evidence_reference: str
    availability_confidence: str = "documented"
    verified_by: str | None = None
    reopened_at: str | None = None
    corrected_at: str | None = None


class AvailabilityRuleRegistry:
    """Versioned rules and dated closure evidence; never derives a date by guess."""

    def __init__(self, path: Path = EVIDENCE_FILE):
        self.path = Path(path)
        raw = self.path.read_bytes() if self.path.exists() else b"{}"
        self.catalog_sha256 = hashlib.sha256(raw).hexdigest()
        payload = json.loads(raw.decode("utf-8"))
        self.rules: dict[str, dict[str, Any]] = {}
        for rule in payload.get("rules", []):
            rule_id = rule.get("rule_id")
            if not rule_id or rule_id in self.rules or not rule_id.endswith(tuple(f"_V{i}" for i in range(1, 100))):
                raise ValueError("invalid_or_duplicate_availability_rule")
            if not rule.get("evidence_reference") or not _iso(rule.get("available_at")):
                raise ValueError("availability_rule_lacks_evidence_or_date")
            if rule.get("availability_source") not in SOURCES or rule.get("availability_confidence") not in OFFICIAL_CONFIDENCE:
                raise ValueError("invalid_availability_rule_metadata")
            self.rules[rule_id] = rule
        self.closures: dict[str, MonthlyClosure] = {}
        for item in payload.get("monthly_closures", []):
            closure = MonthlyClosure(**item)
            if (closure.period in self.closures or not closure.evidence_reference or
                    closure.closure_status not in {"OPEN", "CLOSING", "CLOSED", "REOPENED", "CORRECTED"} or
                    closure.availability_confidence not in OFFICIAL_CONFIDENCE or
                    (closure.closure_status == "CLOSED" and not _iso(closure.closed_at))):
                raise ValueError("invalid_monthly_closure")
            self.closures[closure.period] = closure

    def source_rule(self, filename: str) -> dict[str, Any] | None:
        return next((rule for rule in self.rules.values() if rule.get("enabled") and
                     rule.get("source_file") == filename), None)


def _source_cells(path: Path, sheet: str, coordinates: set[str]) -> dict[str, float]:
    """Stream cached numeric OOXML cells without altering the source workbook."""
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rel_id = next((node.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                       for node in workbook.iter(NS + "sheet") if node.attrib.get("name") == sheet), None)
        if not rel_id:
            raise ValueError("source_sheet_not_found")
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next((node.attrib.get("Target") for node in relationships
                       if node.attrib.get("Id") == rel_id), None)
        if not target:
            raise ValueError("source_sheet_relationship_missing")
        member = target.lstrip("/") if target.startswith("/") else "xl/" + target
        found: dict[str, float] = {}
        with archive.open(member) as stream:
            events = ET.iterparse(stream, events=("start", "end"))
            _, root = next(events)
            for event, node in events:
                if event == "end" and node.tag == NS + "c" and node.attrib.get("r") in coordinates:
                    value = node.find(NS + "v")
                    if value is not None and value.text is not None:
                        found[node.attrib["r"]] = float(value.text)
                if event == "end" and node.tag == NS + "row":
                    root.clear()
        return found


class TemporalAvailabilityAuditor:
    def __init__(self, provider: DataProvider, state_dir: Path,
                 registry: AvailabilityRuleRegistry | None = None):
        self.provider = provider
        self.state_dir = Path(state_dir)
        self.registry = registry or AvailabilityRuleRegistry()
        self.assignments_path = self.state_dir / "availability" / "assignments.json"
        self.trail_path = self.state_dir / "availability" / "audit_trail.json"

    def _assignments(self) -> dict[str, dict[str, Any]]:
        if not self.assignments_path.exists():
            return {}
        return json.loads(self.assignments_path.read_text(encoding="utf-8"))

    def resolve(self, row: dict[str, str], assignments: dict[str, dict[str, Any]] | None = None) -> dict[str, str]:
        result = dict(row)
        result.setdefault("record_hash", _identity(row))
        assigned = (self._assignments() if assignments is None else assignments).get(_identity(row))
        if assigned:
            result.update(assigned)
            rule = self.registry.rules.get(result.get("availability_rule_id", ""))
            if (not rule or not rule.get("enabled") or
                    rule.get("source_file") != row.get("source_file") or
                    rule.get("chain") != row.get("chain") or
                    rule.get("pilot_scope", "").casefold() not in row.get("pilot_scope", "").casefold() or
                    rule.get("sha256", "").casefold() != result.get("source_sha256", "").casefold() or
                    _iso(rule.get("available_at")) != _iso(result.get("available_at"))):
                result["availability_confidence"] = "unknown"
        elif _iso(row.get("available_at")) and row.get("availability_source") in SOURCES and \
                row.get("availability_confidence") in CONFIDENCE:
            result["available_at"] = _iso(row["available_at"]) or ""
            result["availability_rule_id"] = row.get("availability_rule_id") or "DIRECT_TIMESTAMP_V1"
            if row.get("availability_confidence") in OFFICIAL_CONFIDENCE:
                source = row.get("availability_source")
                rule_id = result["availability_rule_id"]
                if source == "source_file_timestamp":
                    # A naked CSV timestamp cannot stand in for a verified workbook.
                    result["availability_confidence"] = "unknown"
                elif source == "business_rule":
                    rule = self.registry.rules.get(rule_id)
                    if not rule or _iso(rule.get("available_at")) != result["available_at"] or \
                            rule.get("availability_source") != "business_rule":
                        result["availability_confidence"] = "unknown"
                elif source == "monthly_closure":
                    closure = self.registry.closures.get(row.get("period", ""))
                    if not closure or closure.rule_id != rule_id or \
                            _iso(closure.closed_at) != result["available_at"]:
                        result["availability_confidence"] = "unknown"
            if row.get("availability_source") == "manual_verified" and not all(
                    row.get(key) for key in ("verification_method", "verified_by", "verification_notes",
                                              "evidence_reference")):
                result["availability_confidence"] = "unknown"
        else:
            objective = OBJECTIVES.get(row.get("objective", "").casefold())
            candidates = ({"orders": ("order_created_at", "order_received_at", "posted_at"),
                           "deliveries": ("delivery_date", "posted_at", "closed_at"),
                           "customer_forecast": ("forecast_received_at",),
                           "sales": ("closed_at", "system_timestamp"),
                           "prior_towell_forecast": ("forecast_issued_at",),
                           "adjusted_forecast": ("adjusted_at",),
                           "approved_forecast": ("approved_at",)}.get(objective, ()))
            timestamp = next((_iso(row.get(key)) for key in candidates if _iso(row.get(key))), None)
            if timestamp:
                result.update(available_at=timestamp, availability_source="system_timestamp",
                              availability_confidence="verified", availability_rule_id="DIRECT_TIMESTAMP_V1")
            elif objective == "sales":
                closure = self.registry.closures.get(row.get("period", ""))
                if closure and closure.closure_status == "CLOSED" and _iso(closure.closed_at):
                    result.update(available_at=_iso(closure.closed_at) or "",
                                  availability_source="monthly_closure",
                                  availability_confidence=closure.availability_confidence,
                                  availability_rule_id=closure.rule_id, closure_status="CLOSED")
        if result.get("availability_confidence") not in CONFIDENCE:
            result["availability_confidence"] = "unknown"
        if result.get("availability_source") not in SOURCES:
            result["availability_source"] = "unknown"
        return result

    def backfill_availability(self, source_dir: Path, *, dry_run: bool = True,
                              actor: str = "migration") -> dict[str, Any]:
        """Verify source SHA and every mapped cell before assigning a file receipt date."""
        _event("availability_audit_started", operation="backfill", dry_run=dry_run)
        rows = self.provider.records()
        previous = self._assignments()
        proposed = dict(previous)
        changes = []
        verified_files = []
        for rule in self.registry.rules.values():
            if not rule.get("enabled") or not rule.get("source_file"):
                continue
            path = Path(source_dir) / rule["source_file"]
            if not path.is_file():
                raise FileNotFoundError(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest.casefold() != rule["sha256"].casefold():
                raise ValueError("source_file_sha256_mismatch")
            # Use the later receipt/write timestamp, never the older workbook edit date.
            if (_iso(rule["available_at"]) or "") < max(_iso(rule["local_created_at"]) or "",
                                                        _iso(rule["local_last_write_at"]) or ""):
                raise ValueError("availability_predates_local_file_receipt")
            candidates = [row for row in rows if row.get("source_file") == rule["source_file"] and
                          row.get("chain") == rule["chain"] and
                          rule["pilot_scope"].casefold() in row.get("pilot_scope", "").casefold() and
                          row.get("period", "") <= rule["through_period"]]
            sheets = defaultdict(set)
            for row in candidates:
                if not row.get("source_sheet") or not row.get("source_cell") or not row.get("record_hash"):
                    raise ValueError("source_coordinate_or_record_hash_missing")
                sheets[row["source_sheet"]].add(row["source_cell"])
            actual_cells = {sheet: _source_cells(path, sheet, coordinates)
                            for sheet, coordinates in sheets.items()}
            for row in candidates:
                actual = actual_cells[row["source_sheet"]].get(row["source_cell"])
                if actual is None or abs(actual - float(row["value"])) > 1e-6:
                    raise ValueError("normalized_source_cell_mismatch")
                assignment = {"available_at": _iso(rule["available_at"]),
                              "availability_source": rule["availability_source"],
                              "availability_confidence": rule["availability_confidence"],
                              "availability_rule_id": rule["rule_id"],
                              "source_sha256": digest,
                              "data_version": row.get("data_version") or "1"}
                if row.get("objective", "").casefold() == "venta":
                    assignment["closure_status"] = rule["closure_status"]
                record_id = _identity(row)
                if proposed.get(record_id) != assignment:
                    changes.append({"record_id": record_id,
                                    "previous_available_at": proposed.get(record_id, {}).get("available_at"),
                                    "new_available_at": assignment["available_at"],
                                    "rule_id": rule["rule_id"], "source": rule["availability_source"],
                                    "actor": actor, "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "evidence_reference": rule["evidence_reference"]})
                proposed[record_id] = assignment
            verified_files.append({"source_file": rule["source_file"], "sha256": digest,
                                   "verified_records": len(candidates), "rule_id": rule["rule_id"]})
            _event("availability_rule_applied", rule_id=rule["rule_id"], records=len(candidates))
        if not dry_run:
            trail = json.loads(self.trail_path.read_text(encoding="utf-8")) if self.trail_path.exists() else []
            _atomic_json(self.assignments_path, proposed)
            _atomic_json(self.trail_path, trail + changes)
        return {"dry_run": dry_run, "assigned_records": len(proposed),
                "new_assignments": len(changes), "verified_files": verified_files}

    def gate(self, period: str, cutoff: str, *, chain: str = "Walmart",
             pilot_scope: str = "FENDI BD") -> dict[str, Any]:
        if _day(cutoff) is None:
            raise ValueError("invalid_cutoff")
        _month_end(period)
        assignments = self._assignments()
        relevant = [row for row in self.provider.records()
                    if pilot_scope.casefold() in row.get("pilot_scope", "").casefold()]
        included: list[dict[str, str]] = []
        known_future: list[dict[str, str]] = []
        excluded: list[dict[str, str]] = []
        candidates = defaultdict(list)
        for raw in relevant:
            row = self.resolve(raw, assignments)
            reason = None
            if row.get("chain") != chain:
                reason = "chain_unverified"
            elif row.get("is_missing", "").casefold() == "true" or not row.get("value", "").strip():
                reason = "missing_value"
            else:
                try:
                    _month_end(row.get("period", ""))
                except (ValueError, TypeError):
                    reason = "invalid_period"
            if reason is None and (row.get("availability_confidence") not in OFFICIAL_CONFIDENCE or
                                   row.get("availability_source") in {None, "unknown"} or
                                   _day(row.get("available_at")) is None):
                reason = "availability_unknown" if row.get("availability_confidence") != "inferred" else "inferred_rejected"
            if reason is None and (_day(row["available_at"]) or "") > cutoff:
                reason = "available_after_cutoff"
            field = OBJECTIVES.get(row.get("objective", "").casefold())
            if reason is None and row.get("period", "") > period and field != "customer_forecast":
                reason = "future_unknown"
            if reason is None and field == "sales" and row.get("availability_source") == "source_file_timestamp" and \
                    row.get("closure_status") != "CLOSED":
                reason = "not_closed"
            if reason:
                excluded.append({"record_id": _identity(raw), "period": row.get("period", ""),
                                 "field": field or row.get("objective", ""), "reason": reason})
            else:
                key = (row.get("chain"), row.get("canonical_product_id"),
                       row.get("objective"), row.get("period"))
                candidates[key].append(row)
        for versions in candidates.values():
            versions.sort(key=lambda row: (_iso(row.get("available_at")) or "",
                                           int(row.get("data_version") or 1), _identity(row)))
            chosen = versions[-1]
            if len(versions) > 1 and any(row.get("value") != chosen.get("value") and
                                         _iso(row.get("available_at")) == _iso(chosen.get("available_at"))
                                         for row in versions[:-1]):
                raise ValueError("conflicting_data_versions_at_same_timestamp")
            for old in versions[:-1]:
                excluded.append({"record_id": _identity(old), "period": old.get("period", ""),
                                 "field": OBJECTIVES.get(old.get("objective", "").casefold()),
                                 "reason": "superseded_as_of_cutoff"})
            if chosen.get("period", "") > period:
                known_future.append(chosen)
            else:
                included.append(chosen)
        included.sort(key=lambda row: (row.get("period", ""), row.get("objective", ""),
                                       row.get("canonical_product_id", "")))
        known_future.sort(key=lambda row: (row.get("period", ""), row.get("canonical_product_id", "")))
        return {"rows": included, "known_future": known_future, "excluded_records": excluded,
                "exclusion_reasons": dict(Counter(item["reason"] for item in excluded)),
                "relevant_records": len(relevant), "eligible_records": len(included) + len(known_future)}

    def validate_temporal_readiness(self, period: str, *, chain: str = "Walmart",
                                    cutoff: str | None = None) -> dict[str, Any]:
        cutoff = cutoff or _month_end(period)
        gated = self.gate(period, cutoff, chain=chain)
        sales = defaultdict(float)
        for row in gated["rows"]:
            if OBJECTIVES.get(row.get("objective", "").casefold()) == "sales":
                sales[row["period"]] += float(row["value"])
        observed = sorted(sales)
        first_active = next((month for month in observed if sales[month] > 0), None)
        consecutive = []
        current = first_active
        while current and current in sales and current <= period:
            consecutive.append(current)
            current = _next_period(current)
        latest = consecutive[-1] if consecutive else None
        minimum = 7  # Statistical engine origin=6 plus one observed actual.
        within_issue_window = latest in {period, None} or (latest is not None and _next_period(latest) == period)
        scope_raw = [row for row in self.provider.records() if row.get("period", "") <= period and
                     "FENDI BD" in row.get("pilot_scope", "").upper()]
        chain_rows = [row for row in scope_raw if row.get("chain") == chain]
        blocking = []
        if not chain_rows and scope_raw:
            status = "BLOCKED_CHAIN"
            blocking.append({"field": "chain", "reason": "chain_unverified"})
        elif not chain_rows and not gated["rows"]:
            status = "NO_DATA"
        elif not observed:
            status = "BLOCKED_AVAILABILITY"
            blocking.append({"field": "sales", "reason": "availability_unknown"})
        elif len(consecutive) < minimum or not within_issue_window:
            status = "INSUFFICIENT_HISTORY"
            blocking.append({"field": "sales", "reason": "insufficient_consecutive_history"})
        else:
            status = "READY"
        optional = {"orders", "deliveries", "customer_forecast", "prior_towell_forecast",
                    "adjusted_forecast", "approved_forecast"}
        present = {OBJECTIVES.get(row.get("objective", "").casefold()) for row in gated["rows"]}
        warnings = [f"optional_not_available:{field}" for field in sorted(optional - present)] if status == "READY" else []
        if status == "READY" and warnings:
            status = "READY_WITH_WARNINGS"
        _event("period_ready" if status in {"READY", "READY_WITH_WARNINGS"} else "period_blocked",
               period=period, cutoff=cutoff, status=status)
        return {"period": period, "cutoff": cutoff, "status": status,
                "ready": status in {"READY", "READY_WITH_WARNINGS"},
                "required_fields_ready": not blocking, "blocking_fields": blocking,
                "warnings": warnings, "first_active_period": first_active,
                "latest_observed_period": latest, "consecutive_active_periods": len(consecutive),
                "minimum_history_required": minimum,
                "temporal_coverage": round(gated["eligible_records"] / gated["relevant_records"] * 100, 2)
                if gated["relevant_records"] else None,
                "excluded_records": len(gated["excluded_records"]),
                "exclusion_reasons": gated["exclusion_reasons"]}

    def find_first_temporally_valid_period(self, start: str = "2023-01", end: str = "2026-08",
                                           *, chain: str = "Walmart") -> dict[str, Any] | None:
        rows = [self.resolve(row) for row in self.provider.records() if row.get("chain") == chain]
        for period in _periods(start, end):
            dates = sorted({_day(row.get("available_at")) for row in rows
                            if row.get("availability_confidence") in OFFICIAL_CONFIDENCE and
                            _day(row.get("available_at")) and
                            f"{period}-01" <= (_day(row.get("available_at")) or "") <= _month_end(period)})
            for cutoff in dates + [_month_end(period)]:
                result = self.validate_temporal_readiness(period, chain=chain, cutoff=cutoff)
                if result["ready"]:
                    return result
        return None

    def audit_availability(self, start_period: str, end_period: str,
                           chain: str = "Walmart") -> dict[str, Any]:
        _event("availability_audit_started", start_period=start_period,
               end_period=end_period, chain=chain)
        periods = _periods(start_period, end_period)
        readiness = [self.validate_temporal_readiness(period, chain=chain) for period in periods]
        counts = Counter(row["status"] for row in readiness)
        last_gate = self.gate(end_period, _month_end(end_period), chain=chain)
        by_field = defaultdict(lambda: [0, 0])
        by_period = defaultdict(lambda: [0, 0])
        eligible_ids = {_identity(row) for row in last_gate["rows"] + last_gate["known_future"]}
        matrix = []
        for raw in self.provider.records():
            if "FENDI BD" not in raw.get("pilot_scope", "").upper():
                continue
            row = self.resolve(raw)
            field = OBJECTIVES.get(row.get("objective", "").casefold(), row.get("objective", ""))
            eligible = _identity(raw) in eligible_ids
            by_field[field][0] += 1
            by_period[row.get("period", "")][0] += 1
            if eligible:
                by_field[field][1] += 1
                by_period[row.get("period", "")][1] += 1
            source = row.get("availability_source")
            rule = self.registry.rules.get(row.get("availability_rule_id", ""), {})
            if row.get("availability_confidence") == "inferred":
                evidence_level = "E5"
            elif row.get("availability_confidence") == "unknown":
                evidence_level = "E6"
            elif source == "source_file_timestamp" and rule.get("source_file") and rule.get("sha256"):
                evidence_level = "E3"
            elif source in {"business_rule", "monthly_closure", "manual_verified"}:
                evidence_level = "E4"
            else:
                # A naked timestamp is not an immutable corporate system log.
                evidence_level = "E6"
            matrix.append({"record_id": _identity(raw), "period": row.get("period"), "field": field,
                           "value": row.get("value"), "available_at": row.get("available_at") or None,
                           "source": row.get("availability_source"),
                           "confidence": row.get("availability_confidence"),
                           "evidence_level": evidence_level,
                           "rule_id": row.get("availability_rule_id") or None, "eligible": eligible})
        def coverage(values: list[int]) -> float | None:
            return round(values[1] / values[0] * 100, 2) if values[0] else None
        first = self.find_first_temporally_valid_period(start_period, end_period, chain=chain)
        confidence_counts = dict(Counter(row["confidence"] for row in matrix))
        if confidence_counts.get("unknown"):
            _event("availability_unknown", records=confidence_counts["unknown"])
        return {"chain": chain, "pilot_scope": "FENDI BD", "start_period": start_period,
                "end_period": end_period, "periods_audited": len(periods),
                "ready": counts["READY"] + counts["READY_WITH_WARNINGS"],
                "blocked": len(periods) - counts["READY"] - counts["READY_WITH_WARNINGS"],
                "statuses": dict(counts), "periods": readiness, "first_valid_period": first,
                "records_audited": len(matrix), "temporal_coverage": coverage([len(matrix), len(eligible_ids)]),
                "confidence_counts": confidence_counts,
                "evidence_level_counts": dict(Counter(row["evidence_level"] for row in matrix)),
                "coverage_by_field": {key: coverage(value) for key, value in by_field.items()},
                "coverage_by_period": {key: coverage(value) for key, value in by_period.items()},
                "exclusion_reasons": last_gate["exclusion_reasons"], "matrix": matrix}
