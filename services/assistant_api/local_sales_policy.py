"""Fail-closed sales provenance for the explicitly configured local pilot.

This guard does not grant database privileges or change hosted authentication.
The private local configuration pins one Towell workbook and one installed
source. Inventory can still be read if that workbook becomes unavailable.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .supabase_access import PreviewError


SALES_CONTRACT = "towell-sales-v1"
SALES_UNAVAILABLE = "Sin cálculo: fuente de ventas no disponible"
MASTER_NAME = "Copia de Pronostico RS Dic Def V1.xlsx"
SALES_READERS = frozenset({
    "rpc/operational_read_observations", "rpc/operational_read_fill_rate",
    "forecast_calculations", "forecast_calculation_horizons",
})
LEGACY_READERS = frozenset({
    "portal_monthly_observations_current", "portal_history_periods",
    "forecast_runs", "forecast_horizons",
})


class LocalSalesPolicy:
    def __init__(self, config):
        if (not isinstance(config, dict) or config.get("contract") not in {SALES_CONTRACT, "pedido-towell-v1"}
                or config.get("sheet") != ("Pedidos" if config.get("contract") == "pedido-towell-v1" else "Towell") or config.get("unit") != "PIEZAS"
                or any(not isinstance(config.get(key), str)
                       or not re.fullmatch(r"[0-9a-f]{64}", config[key])
                       for key in ("master_sha256", "source_hash"))
                or type(config.get("generation")) is not int or config["generation"] < 1
                or (not (config.get("contract") == "pedido-towell-v1" and config.get("last_closed_period") is None)
                    and not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", str(config.get("last_closed_period", ""))))):
            raise ValueError("local_authorized_sales_policy_required")
        self.path = Path(config.get("master_path", ""))
        if (not self.path.is_absolute() or self.path.name != MASTER_NAME
                or self.path.parent.name != "datos_privados"):
            raise ValueError("local_authorized_sales_master_required")
        self.config = dict(config)
        fulfillment = {"fulfillment_source", "fulfillment_file_sha256", "fulfillment_available"}
        if fulfillment.intersection(config) and (config.get("fulfillment_source") != "ESTADISTICA_BAASE"
                or not re.fullmatch(r"[0-9a-f]{64}", str(config.get("fulfillment_file_sha256", "")))
                or config.get("fulfillment_available") is not True):
            raise ValueError("local_fulfillment_policy_invalid")

    def available(self):
        # Check the real file on every operation, including after service restart.
        # No workbook copy, prior source, inventory, or result cache is a fallback.
        try:
            from services.operational_update.towell_sales_source import read_master_bytes
            return hashlib.sha256(read_master_bytes(self.path)).hexdigest() == self.config["master_sha256"]
        except (OSError, ValueError):
            return False

    def context(self, actual):
        if (not isinstance(actual, dict)
                or actual.get("source_hash") != self.config["source_hash"]
                or actual.get("generation") != self.config["generation"]
                or not isinstance(actual.get("last_period"), str)
                or self.config["last_closed_period"] is not None and actual["last_period"] < self.config["last_closed_period"]):
            raise PreviewError("OFFICIAL_SOURCE_MISMATCH", 503)
        available = self.available()
        if self.config["contract"] == "pedido-towell-v1":
            closed = self.config["last_closed_period"] is not None
            return {**actual, **{key: self.config[key] for key in ("fulfillment_source", "fulfillment_file_sha256", "fulfillment_available") if key in self.config},
                "forecast_contract": "pedido-towell-v1", "objective": "Pedido", "target_metric": "ORDER",
                "target_sheet": "Pedidos", "target_file_sha256": self.config["master_sha256"], "training_cutoff": self.config["last_closed_period"],
                "authorized_source_hash": self.config["source_hash"], "target_source_available": available, "forecast_available": available and closed,
                "forecast_block_code": None if closed else "ORDER_CLOSURE_UNVERIFIED",
                "forecast_unavailable_reason": "Sin cálculo: fuente de pedidos no disponible" if not available else None if closed else "Sin cálculo: cierre de Pedido no acreditado",
                "inventory_source": "ESTADISTICA", "unit": "PIEZAS"}
        return {**actual, "sales_contract": SALES_CONTRACT,
                "sales_source": "TOWELL", "sales_sheet": "Towell",
                "sales_file_sha256": self.config["master_sha256"],
                "sales_training_cutoff": self.config["last_closed_period"],
                "authorized_source_hash": self.config["source_hash"],
                "sales_available": available,
                "sales_unavailable_reason": None if available else SALES_UNAVAILABLE,
                "inventory_source": "ESTADISTICA"}

    def require(self, resource, actual):
        context = self.context(actual)
        if resource in LEGACY_READERS:
            raise PreviewError("REPLACED_SOURCE_AUDIT_ONLY", 403)
        if resource in SALES_READERS:
            if context.get("target_source_available") is False:
                raise PreviewError("ORDER_SOURCE_UNAVAILABLE", 503)
            if context.get("sales_available") is False:
                raise PreviewError("SALES_SOURCE_UNAVAILABLE", 503)
        return context
