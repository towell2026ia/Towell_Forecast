"""Whitelist-based local intent routing and deterministic responses."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from .data_provider import DataProvider
from .tools import ForecastTools, TOOL_METHODS, fold

UNKNOWN_MESSAGE = ("Todavía no puedo interpretar esa consulta. Puedes preguntarme por Forecast, "
                   "WAPE, Bias, Fill Rate, Champion, Challenger, vintages o desempeño de productos.")
ADVICE_MESSAGE = ("Actualmente puedo mostrarte datos y desempeño. Las recomendaciones inteligentes "
                  "se habilitarán cuando se conecte el Agente Líder.")
MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def _periods(text: str) -> list[str]:
    mentions: list[tuple[int, str]] = []
    for match in re.finditer(r"(?<!\d)(20\d{2})[-/](0?[1-9]|1[0-2])(?!\d)", text):
        mentions.append((match.start(), f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"))
    for name, month in MONTHS.items():
        for match in re.finditer(rf"\b{name}\b(?:\s+(?:de\s+)?(20\d{{2}}))?", text):
            year = int(match.group(1)) if match.group(1) else None
            if year:
                mentions.append((match.start(), f"{year:04d}-{month:02d}"))
    return [period for _, period in sorted(mentions)]


class LocalIntentRouter:
    def __init__(self, product_aliases: dict[str, str] | None = None):
        self.product_aliases = product_aliases or {}

    def route(self, message: str, context: dict[str, Any] | None = None) -> tuple[str | None, dict[str, Any]]:
        plain = fold(message)
        context = context or {}
        filters = context.get("activeFilters") or {}
        query = {
            "chain": context.get("chain") or "Walmart",
            "category": context.get("category") or filters.get("category"),
            "product": context.get("product") or filters.get("product"),
            "color": context.get("color") or filters.get("color"),
            "period": context.get("period") or filters.get("period"),
        }
        # Explicit text takes precedence over the current UI filter.
        for alias, product in self.product_aliases.items():
            if re.search(rf"(?<!\w){re.escape(fold(alias))}(?!\w)", plain):
                query["product"] = product
                break
        periods = _periods(plain)
        query["period_explicit"] = bool(periods)
        if periods:
            query["period"] = periods[-1]
        elif "actual" in plain or "vigente" in plain or "ultim" in plain:
            query["period"] = None

        if re.search(r"\b(que debo hacer|recomienda|recomendacion|que sugieres)\b", plain):
            return "limited_advice", query
        if "vintage" in plain or "pronosticabamos" in plain or "pronosticamos" in plain or re.search(r"\b(que|cuanto) pronostic\w* en", plain):
            mentions = []
            month_pattern = "|".join(MONTHS)
            for match in re.finditer(rf"\b({month_pattern})\b(?:\s+(?:de\s+)?(20\d{{2}}))?", plain):
                mentions.append((MONTHS[match.group(1)], int(match.group(2)) if match.group(2) else None))
            if len(mentions) >= 2:
                context_year = int(str(context.get("period") or "")[:4]) if re.match(r"^20\d{2}", str(context.get("period") or "")) else None
                first_month, first_year = mentions[0]
                second_month, second_year = mentions[1]
                first_year = first_year or context_year
                second_year = second_year or (first_year + (second_month < first_month) if first_year else None)
                if first_year and second_year:
                    query["issue_period"] = f"{first_year:04d}-{first_month:02d}"
                    query["target_period"] = f"{second_year:04d}-{second_month:02d}"
            elif len(periods) >= 2:
                query["issue_period"], query["target_period"] = periods[:2]
            elif len(periods) == 1:
                query["target_period"] = periods[0]
            return "forecast_vintage", query
        if "fva" in plain or "ajustes humanos" in plain or "ajuste humano" in plain:
            return "fva_summary", query
        if "decision" in plain or "aprobado" in plain:
            return "decision_history", query
        if "drift" in plain or "cambio de patron" in plain:
            return "drift_status", query
        if "fill rate" in plain or "fillrate" in plain or "nivel de servicio" in plain or "entrega vs pedido" in plain:
            return "fill_rate", query
        if "p90" in plain or "p95" in plain or "p50" in plain or "p10" in plain or "banda" in plain or "probabilidad" in plain:
            return "probability_bands", query
        if "peor" in plain or "mayor error" in plain or "mas error" in plain:
            return "highest_error_series", query
        if "challenger" in plain or "retador" in plain:
            return "challenger_status", query
        if "champion" in plain or "campeon" in plain:
            return "champion_status", query
        if "wape" in plain or "error porcentual" in plain:
            return "wape_summary", query
        if "bias" in plain or "sesgo" in plain:
            return "bias_summary", query
        if "compar" in plain and ("forecast" in plain or "motor" in plain or "estadistic" in plain or "ml" in plain):
            return "forecast_comparison", query
        if "periodo" in plain or "cierre" in plain:
            return "period_status", query
        if "forecast" in plain or "pronostico" in plain or "fendi bd" in plain:
            if "12" in plain or "doce" in plain or "horizonte" in plain:
                return "forecast_12m", query
            return "current_forecast", query
        if "producto" in plain or "desempeno" in plain or "rendimiento" in plain:
            return "product_performance", query
        return None, query


class AssistantProvider(ABC):
    def health(self) -> dict[str, str]:
        return {"status": "healthy", "provider": type(self).__name__}

    @abstractmethod
    def render(self, intent: str | None, data: dict[str, Any]) -> str: ...


class LocalAssistantProvider(AssistantProvider):
    @staticmethod
    def _number(value: Any) -> str:
        return f"{value:,.0f}" if isinstance(value, (int, float)) else "sin dato"

    def render(self, intent: str | None, data: dict[str, Any]) -> str:
        if intent is None:
            return UNKNOWN_MESSAGE
        if intent == "limited_advice":
            return ADVICE_MESSAGE
        if data.get("available") is False:
            return data.get("reason", "Ese dato no está disponible.")
        if intent == "current_forecast":
            return (f"Forecast Towell para {data['period']}: {self._number(data['forecast_towell'])} piezas. "
                    f"Motor {data['motor'].upper()}, versión {data['version']}. "
                    f"P50 {self._number(data['p50'])}, P90 {self._number(data['p90'])}, "
                    f"P95 {self._number(data['p95'])}.")
        if intent == "forecast_12m":
            return "Forecast Towell de 12 meses: " + "; ".join(
                f"{row['period']} {self._number(row['forecast_towell'])}" for row in data["horizons"]
            ) + " piezas."
        if intent == "wape_summary":
            if data["level"] == "product":
                statistical = (f"WAPE estadístico de {data['product']}: {data['statistical']:.2f}%. "
                               if data["statistical"] is not None else
                               f"WAPE estadístico de {data['product']}: sin evidencia suficiente. ")
                return (statistical
                        + (f"WAPE ML del producto a un mes: {data['ml']:.2f}%." if data["ml"] is not None else "ML sin evidencia suficiente."))
            return (f"WAPE Forecast Towell: {data['forecast_towell']:.2f}% en el total FENDI BD. "
                    f"Baseline estadístico: {data['statistical']:.2f}%. "
                    f"El WAPE interno producto-mes del ML es {data['ml_product_month_internal']:.2f}%."
                    + (f" Fcst Cliente: {data['client']:.2f}% en {data['client_period_evaluated'][0]} a "
                       f"{data['client_period_evaluated'][1]} (periodo de evaluación distinto)." if data["client"] is not None else ""))
        if intent == "bias_summary":
            label = data.get("product") or "total FENDI BD"
            return f"Bias de {label}: {data['bias']:+.2f}% ({data['direction']})."
        if intent == "fill_rate":
            return (f"Fill Rate de {data['period']}: {data['fill_rate']:.2f}%, calculado con "
                    f"Entrega real {self._number(data['entrega_real'])} / Pedido real {self._number(data['pedido_real'])}.")
        if intent == "champion_status":
            return (f"El Champion vigente es {data['model']} ({data['motor']}), "
                    f"versión {data['version']}, con WAPE {data['wape']:.2f}% en el total FENDI BD.")
        if intent == "challenger_status":
            return (f"Challenger ML: {data['model']}, versión {data['version']}, "
                    f"WAPE interno producto-mes {data['wape']:.2f}%. No hay cierres de validación registrados.")
        if intent == "forecast_comparison":
            return (f"Forecast Towell {data['forecast_towell_wape']:.2f}% WAPE frente a "
                    f"{data['statistical_wape']:.2f}% del baseline estadístico: "
                    f"mejora de {data['improvement_points']:.2f} puntos.")
        if intent == "product_performance":
            return (f"{data['product']}: WAPE estadístico "
                    f"{data['wape']:.2f}%." if data["wape"] is not None else
                    f"{data['product']}: aún no hay WAPE válido.")
        if intent == "highest_error_series":
            return f"El mayor WAPE válido por producto es {data['product']}: {data['wape']:.2f}%."
        if intent == "forecast_vintage":
            return "Vintages disponibles: " + "; ".join(
                f"{row.get('issue_period')}→{row.get('target_period')}: {self._number(row.get('forecast_towell'))}"
                for row in data["vintages"][:12]
            )
        if intent == "probability_bands":
            return (f"Bandas de {data['period']}: P10 {self._number(data['p10'])}, "
                    f"P50 {self._number(data['p50'])}, P90 {self._number(data['p90'])}, "
                    f"P95 {self._number(data['p95'])} piezas.")
        if intent == "drift_status":
            return (f"Drift {'detectado' if data['detected'] else 'no detectado'} "
                    f"en {data['series']}; score {data['score']:.2f}.")
        if intent == "decision_history":
            return f"Hay {len(data['decisions'])} decisiones reales registradas para la consulta."
        if intent == "fva_summary":
            return (f"FVA {data['classification']}: {data['fva_points']:+.2f} puntos de WAPE "
                    f"en {data['decisions_evaluated']} decisiones evaluadas.")
        if intent == "period_status":
            return f"Último periodo con datos cerrados: {data['last_closed_with_data']}."
        return UNKNOWN_MESSAGE


class ForecastOrchestrator:
    def __init__(self, provider: DataProvider, assistant_provider: AssistantProvider | None = None):
        self.provider = provider
        aliases = {}
        try:
            statistical_series = provider.load("statistical")["series"]
        except (OSError, ValueError, KeyError):
            # Keep liveness available; /api/ready reports missing required data.
            statistical_series = []
        for row in statistical_series:
            if row.get("target") == "Venta" and row["series_id"] != "total-fendi-bd":
                aliases[row["label"]] = row["label"]
                aliases[row["series_id"]] = row["label"]
                for word in fold(row["label"]).split():
                    if word not in {"toalla", "mb", "fendi"} and len(word) > 3:
                        aliases[word] = row["label"]
        self.router = LocalIntentRouter(aliases)
        self.tools = ForecastTools(provider)
        self.assistant_provider = assistant_provider or LocalAssistantProvider()

    def query(self, intent: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        # User input is never converted into a Python function name.
        if intent not in TOOL_METHODS:
            raise ValueError("unsupported_intent")
        params = params or {}
        if params.get("chain") and fold(params["chain"]) not in {"walmart", "wm", "fendi bd"}:
            return {"available": False, "reason": "La cadena solicitada está fuera del piloto Walmart / FENDI BD."}
        return getattr(self.tools, TOOL_METHODS[intent])(params)

    def answer(self, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        intent, params = self.router.route(message, context)
        data = self.query(intent, params) if intent in TOOL_METHODS else {}
        return {
            "status": "success" if intent else "unrecognized",
            "intent": intent, "message": self.assistant_provider.render(intent, data),
            "data": data, "source": "local", "actions": [],
            "metadata": {"provider": self.provider.name, "executed_command": False},
        }
