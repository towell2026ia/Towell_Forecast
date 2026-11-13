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
  const ml = scope.ml, stored = scope.stored_results;
  const candidates = (ml?.candidates ?? []).filter(candidate => !stored || !filters.productId || candidate.by_product?.some(row => row.product_id === filters.productId));
  const commercial = scope.commercial_projection;
  const productWinner = filters.productId ? scope.selection?.product_ml_winners?.[filters.productId] : null;
  const chosenModel = commercial && !filters.productId ? "Selección por producto" : productWinner ?? ml?.leader;
  const selected = candidates.find(c => c.model === chosenModel), metric = productCandidate(selected, filters.productId);
  const productForecast = scope.selection?.product_candidates?.find(c => c.product_id === filters.productId && c.family === "ml" && c.model === chosenModel);
  const displayedHorizons = filters.productId && productForecast?.horizons.length === 12 ? horizons.map(h => ({ ...h,
    ml_value: productForecast.horizons.find(row => row.horizon === h.horizon)?.value ?? null })) : horizons;
  const points = traderPoints(historical, [], displayedHorizons, scope.issue_period, filters);
  const future = displayedHorizons.filter(h => h.ml_value != null && Number.isFinite(h.ml_value));
  return <section className="space-y-5"><h2 className="text-xl font-semibold">Machine Learning</h2><p className="text-sm text-slate-500">{scope.chain_name ?? "Scope seleccionado"} · Corte {periodLabel(scope.issue_period)} · Modelo ML {commercial && !filters.productId ? "consolidado" : filters.productId ? "ganador del producto" : "líder del scope"}: {chosenModel ?? "Ninguno"}</p>
    {commercial && <p className="text-xs text-slate-500">ML {commercial.ml}/{commercial.products} variantes{commercial.ml < commercial.products ? " · TOTAL PARCIAL" : ""}. Métodos de las variantes: {commercial.ml_models.join(" · ") || "Sin dato"}. WAPE y Bias agregados del filtro no calculados; {stored ? "las muestras de entrenamiento no están acreditadas en este resultado guardado y no se infieren de sus curvas." : "las muestras de entrenamiento corresponden al scope completo."}</p>}
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">{[["WAPE retrospectivo", percent(retrospectiveMetric(metric, "wape"))], ["Bias retrospectivo", percent(retrospectiveMetric(metric, "bias"))], ["Observaciones", quantity(metric?.observations)], ["Training samples", quantity(ml?.training_samples)], ["Productos utilizados", quantity(ml?.training_products)]].map(([label, value]) => <div className="rounded-xl border bg-white p-4" key={label}><p className="text-xs text-slate-500">{label}</p><p title={metricEvidence} className="mt-3 text-xl font-semibold">{value}</p></div>)}</div>
    <p className="text-xs text-slate-500">{stored ? "Métricas guardadas de comparación entre familias por variante. No se dispone aquí del catálogo interno completo de backtests ni se infiere entrenamiento a partir de una curva guardada. WAPE y Bias del consolidado no se promedian." : `Entrenamiento global por scope; métricas ${commercial ? "agregadas del filtro no disponibles" : filters.productId ? "del producto seleccionado" : "del scope"}. El ranking entre motores usa únicamente pares de observaciones comunes y puede mostrar un WAPE distinto al catálogo interno ML.`} Validación retrospectiva, no certificación temporal.</p>
    <h3 className="font-semibold">{stored ? "Métodos ML conservados en los cálculos" : "Backtest / validación · Modelos evaluados"}</h3>
    <ForecastGrid headers={["Modelo", "WAPE retrospectivo", "Bias retrospectivo", "MAE", "RMSE", "Observaciones", "Estado / motivo"]} rows={candidates.map(c => { const m = productCandidate(c, filters.productId); return [c.model ?? "—", percent(retrospectiveMetric(m, "wape")), percent(retrospectiveMetric(m, "bias")), quantity(m?.retrospective_mae), quantity(m?.retrospective_rmse), quantity(m?.observations), stored && !filters.productId ? `Métricas individuales disponibles: ${c.by_product?.length ?? 0} variantes` : c.reason ?? c.status ?? "Sin evidencia evaluada"]; })}/>
    {stored && !filters.productId && <section className="space-y-3" aria-label="Métricas ML individuales guardadas"><h3 className="font-semibold">Machine Learning · Métricas guardadas por variante</h3><ForecastGrid headers={["Producto", "Cadena / scope", "Método", "WAPE retrospectivo", "Bias retrospectivo", "Observaciones", "Ventanas", "Estado / motivo"]} rows={stored.products.map(product => {
      const candidate = product.candidates.find(row => row.family === "ml");
      const reasons = product.suggested_reference?.reason_codes ?? [];
      return [product.description, product.chain_id, candidate?.model ?? product.ml_model ?? "Sin método ML guardado", percent(candidate?.wape), percent(candidate?.bias), quantity(candidate?.observations), quantity(candidate?.windows), candidate ? "Candidato guardado · H1–H12" : `Sin candidato ML validado guardado${reasons.length ? ` · Criterios conservados: ${reasons.join(" · ")}` : " · Motivo adicional no acreditado"}`];
    })}/></section>}
    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">Detalles técnicos de entrenamiento</summary><div className="mt-3"><ForecastGrid headers={["Modelo", "Muestras", "Entrenable", "Validado", "Seleccionado"]} rows={candidates.map(c => [c.model ?? "—", quantity(c.training_samples), c.trainable == null ? "—" : c.trainable ? "Sí" : "No", c.validated == null ? "—" : c.validated ? "Sí" : "No", c.selected ? "Sí" : "No"])}/></div></details>
    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">Features utilizadas</summary><p className="mt-3 break-words text-xs text-slate-500">{ml?.features?.join(" · ") || (stored ? "No informadas en el cálculo guardado" : "No informadas en este preview")}</p></details>
    <StatisticalHorizonAccuracy candidate={metric}/>
    <h3 className="font-semibold">Forecast futuro · H1–H12</h3>
    {historyError && <p role="alert" className="text-sm text-rose-700">No fue posible consultar la venta histórica.</p>}
    {future.length === 12 ? <><ForecastTraderChart points={points} cutoff={scope.issue_period} visible={visible} onChange={key => setVisible(v => ({ ...v, [key]: !v[key] }))}/><ForecastGrid headers={["Horizonte", "Periodo", "Modelo ML", "Forecast ML"]} rows={future.map(h => [`H${h.horizon}`, h.target_period, chosenModel ?? "—", quantity("ml_value" in h ? h.ml_value : null)])}/><p className="text-xs text-slate-500">{commercial ? "La línea ML suma los ganadores almacenados de las variantes con dato del filtro." : `La línea ML corresponde sólo al ganador evaluado para ${filters.productId ? "este producto" : "este scope"}.`} No se inventan forecasts de candidatos alternativos.</p></> : <p className="rounded-xl border bg-white p-4 text-sm text-slate-500">Sin forecast ML H1–H12 para este filtro. Consulta el estado/motivo real de los candidatos; training samples no implica validación ni selección.</p>}
  </section>;
}
