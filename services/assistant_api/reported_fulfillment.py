"""Pinned BAASE percentages, independent of training and of comparable pairs."""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from .supabase_access import PreviewError, uuid_value

REPORTED_COLUMNS = """chain_id uuid,product_id uuid,period date,format text,upc text,
    purchase_type text,order_text text,delivery_text text,source_reference text,
    order_source_reference text,delivery_source_reference text,source_sha256 text,
    partial_period boolean,reported_fill_rate_pct numeric,reported_fill_rate_pct_text text"""


def reported_fulfillment(config, context, supplied, catalog):
    """Catalog is obtained under the caller's SQL RLS in the same transaction."""
    allowed_parameters = {"p_source_hash", "p_generation", "p_chain_id", "p_product_ids",
        "p_from", "p_to", "p_format", "p_purchase_type"}
    try:
        if (not isinstance(supplied, dict) or not set(supplied).issubset(allowed_parameters)
                or supplied.get("p_source_hash") != context["source_hash"]
                or str(supplied.get("p_generation")) != str(context["generation"])):
            raise PreviewError("OFFICIAL_SOURCE_CHANGED", 409)
        if supplied.get("p_purchase_type", "RS") != "RS":
            raise PreviewError("OFFICIAL_FILTER_INVALID", 400)
        chain = uuid_value(supplied["p_chain_id"]) if supplied.get("p_chain_id") else None
        ids = supplied.get("p_product_ids")
        if isinstance(ids, str):
            ids = json.loads(ids) if ids.startswith("[") else ids.strip("{}").split(",")
        if ids is not None and not isinstance(ids, list):
            raise PreviewError("OFFICIAL_FILTER_INVALID", 400)
        ids = {uuid_value(value) for value in ids} if ids is not None else None
        limits = {}
        for key in ("p_from", "p_to"):
            if supplied.get(key) is not None:
                limits[key] = date.fromisoformat(supplied[key]).isoformat()
        if limits.get("p_from", "0001-01-01") > limits.get("p_to", "9999-12-31"):
            raise PreviewError("OFFICIAL_FILTER_INVALID", 400)
    except (ValueError, TypeError, KeyError):
        raise PreviewError("OFFICIAL_FILTER_INVALID", 400) from None
    try:
        raw = Path(config["fulfillment_report_path"]).read_bytes()
        if (not context.get("fulfillment_available")
                or hashlib.sha256(raw).hexdigest() != context["fulfillment_report_sha256"]):
            raise ValueError("report pin")
        source = json.loads(raw)
        if not isinstance(source, list):
            raise ValueError("report shape")
        partial = config.get("fulfillment_partial_periods", [])
        if not isinstance(partial, list) or any(not isinstance(month, str) or not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month) for month in partial):
            raise ValueError("report closing state")
        visible = {(str(row["chain_id"]), str(row["product_id"])): row for row in catalog}
        result, seen = [], set()
        for row in source:
            identity = (uuid_value(row["chain_id"]), uuid_value(row["product_id"]))
            period = row["period"]
            if re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", str(period)):
                period += "-01"
            if date.fromisoformat(period).day != 1 or not context["first_period"] <= period[:7] <= context["last_period"]:
                raise ValueError("report period")
            exact = row["reported_fill_rate_pct"]
            if not isinstance(exact, str):
                raise ValueError("exact percentage required")
            value = Decimal(exact)
            if (not value.is_finite() or value < 0 or row.get("purchase_type") != "RS"
                    or row.get("source_sha256") != context["fulfillment_file_sha256"]
                    or not isinstance(row.get("source_reference"), str) or not row["source_reference"].strip()
                    or (identity, period) in seen):
                raise ValueError("invalid report")
            seen.add((identity, period))
            native = visible.get(identity)
            if native is None or value == 0:
                continue
            if (row.get("upc") != native["upc"] or not isinstance(row.get("format"), str)
                    or row["format"].strip().casefold() != native["format"].strip().casefold()
                    or native["purchase_type"] != "RS"):
                raise ValueError("report identity contradiction")
            if (chain and identity[0] != chain or ids is not None and identity[1] not in ids
                    or supplied.get("p_format") is not None and native["format"].strip().casefold() != supplied["p_format"].strip().casefold()
                    or limits.get("p_from", "0001-01-01") > period or limits.get("p_to", "9999-12-31") < period):
                continue
            result.append({"chain_id": identity[0], "product_id": identity[1], "period": period,
                "format": native["format"], "upc": native["upc"], "purchase_type": "RS",
                "order_text": None, "delivery_text": None, "order_source_reference": None,
                "delivery_source_reference": None, "source_reference": row["source_reference"],
                "source_sha256": row["source_sha256"], "partial_period": period[:7] in partial,
                "reported_fill_rate_pct": exact, "reported_fill_rate_pct_text": exact})
        return result
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation, PreviewError):
        raise PreviewError("FILL_RATE_REPORT_UNAVAILABLE", 503) from None
