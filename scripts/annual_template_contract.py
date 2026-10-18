"""Local annual workbook contract. No database, network or forecasting imports."""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "templates/annual_template_schema_v1.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
SHEETS = {sheet["name"]: sheet for sheet in SCHEMA["sheets"]}
INPUT_SHEETS = [name for name, sheet in SHEETS.items() if sheet["role"] in {"input", "optional_input", "rules"}]
QUANTITIES = {"venta_real", "pedido", "enviado", "cantidad_forecast_cliente"}
AMBIGUOUS = {"AMBIGUO", "AMBIGUA", "POR CONFIRMAR", "POR DEFINIR", "VARIOS", "VARIOS COLORES", "SURTIDO", "SIN IDENTIFICAR", "UNKNOWN", "?"}
CHECK_FORMULAS = {
    '=IF(\'01_CONTROL_CARGA\'!A2="","Sin declarar",\'01_CONTROL_CARGA\'!A2)',
    '=IF(\'01_CONTROL_CARGA\'!A2="","ERROR","ADVERTENCIA")',
    '=IF(\'01_CONTROL_CARGA\'!C2="","Sin declarar",\'01_CONTROL_CARGA\'!C2)',
    '=IF(\'01_CONTROL_CARGA\'!C2="","ERROR","ADVERTENCIA")',
    '=COUNTA(T_Productos[codigo_producto_origen])',
    '=COUNTA(T_Historico[codigo_producto_origen])',
}


def comparison_key(value: str) -> str:
    """NFC, case and spaces only. Never used for product/source identity."""
    return " ".join(unicodedata.normalize("NFC", value).upper().split())


def headers(name: str) -> list[str]:
    return [column["name"] for column in SHEETS[name]["columns"]]


def confirmed_rows() -> list[list[str]]:
    return [[attribute, original, normalized, "CONFIRMADA", "PRD 09.2E5-02A.0", "Homologación de escritura; no aprueba productos."]
            for attribute, original, normalized in SCHEMA["confirmed_equivalences"]]


def blank(value: object) -> bool:
    return value is None or isinstance(value, str) and not value.strip()
