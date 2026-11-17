"""Strict Pedidos K:M ORDER reader, sharing the immutable master parser."""
from __future__ import annotations

from pathlib import Path
import re

from .towell_sales_source import TowellSourceError, _read_historical_sheet

SCHEMA = "pedidos-order-source-1"
CONTRACT = "FT-FIX-PEDIDO-03"


def validate_closure(evidence, period):
    """Require a source-specific business statement, never a Towell closure."""
    if (not isinstance(evidence, dict) or evidence.get("sheet") != "Pedidos"
            or evidence.get("metric_code") != "ORDER" or evidence.get("last_closed_period") != period
            or evidence.get("confirmed_by") != "PROJECT_OWNER_USER"
            or not isinstance(evidence.get("reference"), str) or not evidence["reference"].strip()
            or not isinstance(evidence.get("statement"), str) or not evidence["statement"].strip()
            or not isinstance(evidence.get("document_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", evidence["document_sha256"])
            or evidence.get("available_at") is not None):
        raise TowellSourceError("PEDIDOS_SOURCE_SPECIFIC_CLOSURE_REQUIRED")
    return dict(evidence)


def read_pedidos_order_source(workspace: Path | None = None, *, confirmed_closed_period: str | None = None,
                              closure_evidence: dict | None = None, expected_sha256: str | None = None):
    """Captured ORDER is queryable even when no closed month is accredited.

    Unknown closure never becomes a forecast cutoff. A future caller must
    supply a new source-specific confirmation to enable training eligibility.
    """
    if confirmed_closed_period is None and closure_evidence is not None:
        raise TowellSourceError("PEDIDOS_CLOSURE_PERIOD_REQUIRED")
    evidence = validate_closure(closure_evidence, confirmed_closed_period) if confirmed_closed_period else None
    result = _read_historical_sheet(workspace, confirmed_closed_period=confirmed_closed_period,
        expected_sha256=expected_sha256, sheet_name="Pedidos", metric_code="ORDER", schema=SCHEMA,
        contract=CONTRACT, interpretation="PROJECT_OWNER_AUTHORIZED_PEDIDOS_OBSERVED_ORDER")
    result["closure"] = {"last_closed_period": confirmed_closed_period,
        "explicitly_confirmed_periods": [confirmed_closed_period] if confirmed_closed_period else [],
        "confirmed_by": evidence["confirmed_by"] if evidence else None,
        "confirmed_at": evidence.get("confirmed_at") if evidence else None, "evidence": evidence,
        "available_at": None, "point_in_time_certified": False,
        "forecast_available": bool(confirmed_closed_period),
        "forecast_block_code": None if confirmed_closed_period else "ORDER_CLOSURE_UNVERIFIED"}
    return result
