"""Read frozen E4 curves for an already authorized commercial selection.

No engine, persistence writes, publication, or cached independent model run.
The selection mirrors Períodos: one latest compatible calculation per variant.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import uuid

from services.forecast_engine.engine import add_month
from .supabase_access import PreviewError, matches_source


STATUSES = {"READY_FOR_DECISION", "DECIDED", "SUPERSEDED"}
HEADER_FIELDS = ("id,chain_id,product_id,objective,calculation_no,calculation_code,issue_period,status,"
                 "created_at,engine_version,evidence_mode,input_snapshot")
HORIZON_FIELDS = ("calculation_id,chain_id,product_id,horizon,target_period,"
                  "statistical_value,ml_value,ensemble_value")
FAMILIES = {"statistical": "statistical_value", "ml": "ml_value", "ensemble_value": "ensemble_value"}


def _invalid():
    raise PreviewError("DATA_READ_FAILED", 503)


def _uuid(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        _invalid()


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is not None:
            return stamp
    except (TypeError, ValueError, AttributeError):
        pass
    _invalid()


def _quantity(value):
    if value is None:
        return None
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            _invalid()
        number = Decimal(str(value))
        if number.is_finite() and number >= 0:
            return number
    except InvalidOperation:
        pass
    _invalid()


def _latest(headers):
    selected = {}
    for row in headers:
        prior = selected.get(row["product_id"])
        if prior is None or (row["calculation_no"], _stamp(row["created_at"])) > (
                prior["calculation_no"], _stamp(prior["created_at"])):
            selected[row["product_id"]] = row
    return selected


def _check_context(client, context):
    # operational_context() is deliberately pinned/cached by the existing read
    # client. Fetch a fresh compact context after raw E4 reads, not that cache.
    fresh = client.get("/rest/v1/rpc/operational_read_context")
    if not isinstance(fresh, list) or len(fresh) != 1 or not isinstance(fresh[0], dict):
        _invalid()
    if not matches_source(fresh[0], context):
        raise PreviewError("OFFICIAL_SOURCE_CHANGED", 409)


def stored_rs_forecast(client, context, *, chain_id, product_ids, period):
    """Consolidate only these resolved RS variants at this exact issue month.

    Model/format/presentation membership comes from the source-native catalogue
    validated by load_rs_scope; UPC/name similarities never expand this scope.
    """
    ids = sorted({_uuid(value) for value in product_ids})
    if not ids:
        return None
    headers, seen = [], set()
    for offset in range(0, len(ids), 80):
        batch = ids[offset:offset + 80]
        rows = client.rows("forecast_calculations", {
            "select": HEADER_FIELDS, "chain_id": "eq." + chain_id,
            "product_id": "in.(" + ",".join(batch) + ")", "issue_period": "eq." + period + "-01",
            "order": "calculation_no.desc,created_at.desc,id.asc",
        })
        for row in rows:
            if (row.get("chain_id") != chain_id or row.get("product_id") not in batch
                    or row.get("issue_period") != period + "-01" or _uuid(row.get("id")) in seen):
                _invalid()
            seen.add(row["id"])
            snapshot = row.get("input_snapshot")
            if not isinstance(snapshot, dict):
                _invalid()
            if not matches_source(snapshot.get("source_context"), context):
                continue
            if row.get("objective") != "Venta" or row.get("status") not in STATUSES:
                continue
            if (type(row.get("calculation_no")) is not int or row["calculation_no"] < 1
                    or not isinstance(row.get("calculation_code"), str) or not row["calculation_code"]
                    or not isinstance(row.get("engine_version"), str) or not row["engine_version"]
                    or row.get("evidence_mode") not in {"POINT_IN_TIME", "RETROSPECTIVE_TRAINING"}):
                _invalid()
            _stamp(row.get("created_at"))
            headers.append(row)
    latest = _latest(headers)
    if not latest:
        _check_context(client, context)
        return None
    # Match buildForecastMatrix's engine/evidence anchor; never mix runs merely
    # because their targets or product names happen to match.
    anchor = max(latest.values(), key=lambda row: (_stamp(row["created_at"]), row["id"]))
    latest = _latest([row for row in headers if row["engine_version"] == anchor["engine_version"]
                      and row["evidence_mode"] == anchor["evidence_mode"]])
    by_calculation = {row["id"]: row for row in latest.values()}
    curves = {row["product_id"]: {} for row in latest.values()}
    calc_ids = sorted(by_calculation)
    for offset in range(0, len(calc_ids), 80):
        batch = calc_ids[offset:offset + 80]
        rows = client.rows("forecast_calculation_horizons", {
            "select": HORIZON_FIELDS, "chain_id": "eq." + chain_id,
            "calculation_id": "in.(" + ",".join(batch) + ")", "order": "calculation_id.asc,horizon.asc",
        })
        for row in rows:
            header = by_calculation.get(row.get("calculation_id"))
            h = row.get("horizon")
            if (header is None or row["calculation_id"] not in batch or row.get("chain_id") != chain_id
                    or row.get("product_id") != header["product_id"] or type(h) is not int
                    or h not in range(1, 13) or row.get("target_period") != add_month(period, h) + "-01"
                    or not set(FAMILIES.values()) <= row.keys()
                    or h in curves[header["product_id"]]):
                _invalid()
            values = {name: _quantity(row.get(field)) for name, field in FAMILIES.items()}
            if values["statistical"] is None:
                _invalid()
            curves[header["product_id"]][h] = values
    for curve in curves.values():
        if set(curve) != set(range(1, 13)):
            _invalid()
        for family in ("ml", "ensemble_value"):
            if sum(curve[h][family] is not None for h in curve) not in {0, 12}:
                _invalid()
    periods = []
    with localcontext() as decimal_context:
        decimal_context.prec = 50
        for h in range(1, 13):
            values = {}
            for family in FAMILIES:
                numbers = [curve[h][family] for curve in curves.values() if curve[h][family] is not None]
                values[family] = {"pieces": format(sum(numbers), "f") if numbers else None,
                                  "covered": len(numbers), "eligible": len(ids)}
            periods.append({"period": add_month(period, h), **values})
    _check_context(client, context)
    provenance = []
    for product_id, row in sorted(latest.items()):
        product = row["input_snapshot"].get("product") or {}
        if not isinstance(product, dict):
            _invalid()
        offline = product.get("offline_provenance") or {}
        if not isinstance(offline, dict):
            _invalid()
        original = offline.get("emitted_at")
        if original is not None:
            _stamp(original)
        provenance.append({"product_id": product_id, "calculation_id": row["id"],
                           "calculation_code": row["calculation_code"], "created_at": row["created_at"],
                           "original_emitted_at": original})
    return {"status": "PROVISIONAL", "engine": anchor["engine_version"], "periods": periods,
            "origin": "STORED_E4", "issue_period": period,
            "source_context": {name: context[name] for name in ("source_hash", "generation")},
            "evidence_mode": anchor["evidence_mode"], "temporal_certification": False,
            "retrospective": {}, "live": None, "calculations": provenance}
