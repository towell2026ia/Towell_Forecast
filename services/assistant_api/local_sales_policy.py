"""Fail-closed sales provenance for the explicitly configured local pilot.

This guard does not grant database privileges or change hosted authentication.
The private local configuration pins one Towell workbook and one installed
source. Inventory can still be read if that workbook becomes unavailable.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .supabase_access import COMMERCIAL_CLOSE_BASIS, ORDER_SHEETS, UNIFIED_CONTRACT, UNIFIED_MASTER_SHA256, PreviewError, valid_fulfillment_context


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
        if (not isinstance(config, dict) or config.get("contract") not in {SALES_CONTRACT, *ORDER_SHEETS}
                or config.get("sheet") != ORDER_SHEETS.get(config.get("contract"), "Towell") or config.get("unit") != "PIEZAS"
                or any(not isinstance(config.get(key), str)
                       or not re.fullmatch(r"[0-9a-f]{64}", config[key])
                       for key in ("master_sha256", "source_hash"))
                or type(config.get("generation")) is not int or config["generation"] < 1
                or (not (config.get("contract") == "pedido-towell-v1" and config.get("last_closed_period") is None)
                    and not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", str(config.get("last_closed_period", ""))))):
            raise ValueError("local_authorized_sales_policy_required")
        self.path = Path(config.get("master_path", ""))
        if config["contract"] == UNIFIED_CONTRACT:
            commercial = any(key in config for key in ("planning_basis", "observed_cutoff", "scenario_hash"))
            if (not self.path.is_absolute() or self.path.suffix.lower() != ".xlsx"
                    or "datos_privados" not in self.path.resolve().parts
                    or config["master_sha256"] != UNIFIED_MASTER_SHA256
                    or not commercial and config["last_closed_period"] != "2026-09"
                    or commercial and (config.get("planning_basis") != COMMERCIAL_CLOSE_BASIS
                        or config.get("observed_cutoff") != "2026-09" or config["last_closed_period"] != "2026-12"
                        or config["generation"] < 6 or not re.fullmatch(r"[0-9a-f]{64}", str(config.get("scenario_hash", ""))))):
                raise ValueError("local_authorized_unified_master_required")
            if commercial:
                for path_key, hash_key in (("scenario_path", "scenario_file_sha256"),
                        ("scenario_records_path", "scenario_records_sha256"), ("observed_records_path", "observed_records_sha256")):
                    path = Path(config.get(path_key, ""))
                    if (not path.is_absolute() or path.suffix.lower() != ".json" or "datos_privados" not in path.resolve().parts
                            or not re.fullmatch(r"[0-9a-f]{64}", str(config.get(hash_key, "")))):
                        raise ValueError("local_commercial_close_evidence_required")
        elif (not self.path.is_absolute() or self.path.name != MASTER_NAME
                or self.path.parent.name != "datos_privados"):
            raise ValueError("local_authorized_sales_master_required")
        self.config = dict(config)
        if not valid_fulfillment_context(config):
            raise ValueError("local_fulfillment_policy_invalid")
        if config.get("fulfillment_mode") == "REPORTED_PERCENTAGE":
            path = Path(config.get("fulfillment_report_path", ""))
            if not path.is_absolute() or path.suffix.lower() != ".json" or "datos_privados" not in path.resolve().parts:
                raise ValueError("local_fulfillment_policy_invalid")

    def master_available(self):
        # Check the real file on every operation, including after service restart.
        # No workbook copy, prior source, inventory, or result cache is a fallback.
        try:
            if self.config["contract"] == UNIFIED_CONTRACT:
                with self.path.open("rb") as stream:
                    digest = hashlib.sha256()
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                return digest.hexdigest() == UNIFIED_MASTER_SHA256
            from services.operational_update.towell_sales_source import read_master_bytes
            return hashlib.sha256(read_master_bytes(self.path)).hexdigest() == self.config["master_sha256"]
        except (OSError, ValueError):
            return False

    def scenario_available(self):
        if self.config.get("planning_basis") != COMMERCIAL_CLOSE_BASIS:
            return True
        try:
            scenario = None
            for path_key, hash_key in (("scenario_path", "scenario_file_sha256"),
                    ("scenario_records_path", "scenario_records_sha256"), ("observed_records_path", "observed_records_sha256")):
                raw = Path(self.config[path_key]).read_bytes()
                if hashlib.sha256(raw).hexdigest() != self.config[hash_key]:
                    return False
                if path_key == "scenario_path":
                    scenario = json.loads(raw)
            expected = {"schema": "unified-commercial-close-scenario-1", "scenario_hash": self.config["scenario_hash"],
                "master_sha256": self.config["master_sha256"], "source_hash": self.config["source_hash"], "generation": self.config["generation"],
                "planning_basis": COMMERCIAL_CLOSE_BASIS, "observed_cutoff": self.config["observed_cutoff"],
                "training_cutoff": self.config["last_closed_period"], "planning_start": "2027-01", "planning_end": "2027-12",
                "engine_records_sha256": self.config["scenario_records_sha256"], "observed_records_sha256": self.config["observed_records_sha256"]}
            return isinstance(scenario, dict) and all(scenario.get(key) == value for key, value in expected.items())
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def available(self):
        return self.master_available() and self.scenario_available()

    def context(self, actual):
        if (not isinstance(actual, dict)
                or actual.get("source_hash") != self.config["source_hash"]
                or actual.get("generation") != self.config["generation"]
                or not isinstance(actual.get("last_period"), str)
                or self.config["last_closed_period"] is not None and actual["last_period"] < self.config["last_closed_period"]):
            raise PreviewError("OFFICIAL_SOURCE_MISMATCH", 503)
        master_available = self.master_available()
        scenario_available = self.scenario_available()
        available = master_available and scenario_available
        if self.config["contract"] in ORDER_SHEETS:
            closed = self.config["last_closed_period"] is not None
            return {**actual, **{key: self.config[key] for key in ("fulfillment_source", "fulfillment_file_sha256", "fulfillment_available", "fulfillment_mode", "fulfillment_report_sha256") if key in self.config},
                **({"planning_start": "2027-01", "planning_end": "2027-12"} if self.config["contract"] == UNIFIED_CONTRACT else {}),
                **({key: self.config[key] for key in ("planning_basis", "observed_cutoff", "scenario_hash")} if self.config.get("planning_basis") == COMMERCIAL_CLOSE_BASIS else {}),
                "forecast_contract": self.config["contract"], "objective": "Pedido", "target_metric": "ORDER",
                "target_sheet": self.config["sheet"], "target_file_sha256": self.config["master_sha256"], "training_cutoff": self.config["last_closed_period"],
                "authorized_source_hash": self.config["source_hash"], "target_source_available": master_available, "forecast_available": available and closed,
                "forecast_block_code": "PLANNING_SOURCE_UNAVAILABLE" if master_available and not scenario_available else None if closed else "ORDER_CLOSURE_UNVERIFIED",
                "forecast_unavailable_reason": "Sin cálculo: fuente de pedidos no disponible" if not master_available else "Sin cálculo: cierre comercial no disponible" if not scenario_available else None if closed else "Sin cálculo: cierre de Pedido no acreditado",
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
            if resource in {"forecast_calculations", "forecast_calculation_horizons"} and not context.get("forecast_available", True):
                if self.config.get("planning_basis") == COMMERCIAL_CLOSE_BASIS:
                    raise PreviewError("PLANNING_SOURCE_UNAVAILABLE", 503)
        return context
