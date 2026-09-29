"use client";
import { useState } from "react";
import type { PreviewScope } from "@/lib/forecast-preview";
import type { Filters } from "@/lib/supabase/types";
import { defaultVisibility, percent, periodLabel, quantity, traderPoints, type HistoricalMonth, type Horizon, type SeriesVisibility } from "@/lib/forecast-chart-data";
import { productCandidate } from "@/lib/ml-preview-data";
import { retrospectiveMetric, metricEvidence } from "@/lib/statistical-preview-data";
import { ForecastGrid } from "./forecast-horizon-table";
import { ForecastTraderChart } from "./forecast-trader-chart";
import { StatisticalHorizonAccuracy } from "./statistical-horizon-chart";

export function MLEngineDetail({ scope, scopes, filters, historical, horizons, historyError, cutsStatus }: { scope: PreviewScope | null; scopes: PreviewScope[]; filters: Filters; historical: HistoricalMonth[]; horizons: Horizon[]; historyError?: boolean; cutsStatus?: string }) {
  const [visible, setVisible] = useState<SeriesVisibility>({ ...defaultVisibility, order: false, delivery: false, statistical: true, ml: true });
  if (!filters.chainId) return <section className="space-y-4"><h2 className="text-xl font-semibold">Machine Learning · Cobertura por cadena</h2><p className="text-sm text-slate-500">Sin suma global de scopes.</p>{cutsStatus === "CUTS_NOT_ALIGNED" && <p className="text-xs text-blue-800">CUTS_NOT_ALIGNED</p>}<ForecastGrid headers={["Cadena", "Corte", "Training samples", "ML elegibles", "Modelo", "WAPE retrospectivo", "Bias retrospectivo"]} rows={scopes.map(s => [s.chain_name ?? s.chain_id, periodLabel(s.issue_period), quantity(s.ml?.training_samples), quantity(s.eligibility?.ml_eligible), s.ml?.leader ?? "—", percent(s.ml?.retrospective_wape), percent(s.ml?.retrospective_bias)])}/></section>;
  if (!scope || scope.chain_id !== filters.chainId) return <p className="text-sm text-slate-500">Sin vista previa ML para la cadena seleccionada.</p>;
  const ml = scope.ml, candidates = ml?.candidates ?? [];
  const selected = candidates.find(c => c.model === ml?.leader), metric = productCandidate(selected, filters.productId);
  const points = traderPoints(historical, [], horizons, scope.issue_period, filters);
  const future = horizons.filter(h => h.ml_value != null && Number.isFinite(h.ml_value));
  return <section className="space-y-5"><h2 className="text-xl font-semibold">Machine Learning</h2><p className="text-sm text-slate-500">{scope.chain_name ?? "Scope seleccionado"} · Corte {periodLabel(scope.issue_period)} · Modelo ML seleccionado: {ml?.leader ?? "Ninguno"}</p>
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">{[["Training samples", quantity(ml?.training_samples)], ["Productos utilizados", quantity(ml?.training_products)], ["Features", quantity(ml?.features?.length)], ["WAPE retrospectivo", percent(retrospectiveMetric(metric, "wape"))], ["Bias retrospectivo", percent(retrospectiveMetric(metric, "bias"))]].map(([label, value]) => <div className="rounded-xl border bg-white p-4" key={label}><p className="text-xs text-slate-500">{label}</p><p title={metricEvidence} className="mt-3 text-xl font-semibold">{value}</p></div>)}</div>
    <p className="text-xs text-slate-500">Entrenamiento global por scope; métricas {filters.productId ? "del producto seleccionado" : "del scope"}. Validación descriptiva expanding-window, no certificación temporal.</p>
    <h3 className="font-semibold">Backtest / validación · Modelos evaluados</h3>
    <ForecastGrid headers={["Modelo", "WAPE retrospectivo", "Bias retrospectivo", "MAE", "RMSE", "Observaciones", "Estado / motivo"]} rows={candidates.map(c => { const m = productCandidate(c, filters.productId); return [c.model ?? "—", percent(retrospectiveMetric(m, "wape")), percent(retrospectiveMetric(m, "bias")), quantity(m?.retrospective_mae), quantity(m?.retrospective_rmse), quantity(m?.observations), c.reason ?? c.status ?? "Sin evidencia evaluada"]; })}/>
    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">Detalles técnicos de entrenamiento</summary><div className="mt-3"><ForecastGrid headers={["Modelo", "Muestras", "Entrenable", "Validado", "Seleccionado"]} rows={candidates.map(c => [c.model ?? "—", quantity(c.training_samples), c.trainable == null ? "—" : c.trainable ? "Sí" : "No", c.validated == null ? "—" : c.validated ? "Sí" : "No", c.selected ? "Sí" : "No"])}/></div></details>
    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">Features utilizadas</summary><p className="mt-3 break-words text-xs text-slate-500">{ml?.features?.join(" · ") || "No informadas en este preview"}</p></details>
    <StatisticalHorizonAccuracy candidate={metric}/>
    <h3 className="font-semibold">Forecast futuro · H1–H12</h3>
    {historyError && <p role="alert" className="text-sm text-rose-700">No fue posible consultar la venta histórica.</p>}
    {future.length === 12 ? <><ForecastTraderChart points={points} cutoff={scope.issue_period} visible={visible} onChange={key => setVisible(v => ({ ...v, [key]: !v[key] }))}/><ForecastGrid headers={["Horizonte", "Periodo", "Modelo ML", "Forecast ML"]} rows={future.map(h => [`H${h.horizon}`, h.target_period, ml?.leader ?? "—", quantity("ml_value" in h ? h.ml_value : null)])}/><p className="text-xs text-slate-500">La línea ML corresponde sólo al modelo seleccionado. No se inventan forecasts de candidatos alternativos.</p></> : <p className="rounded-xl border bg-white p-4 text-sm text-slate-500">Sin forecast ML H1–H12 para este filtro. Consulta el estado/motivo real de los candidatos; training samples no implica validación ni selección.</p>}
  </section>;
}
