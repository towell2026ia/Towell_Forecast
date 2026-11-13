import { retrospectiveMetric } from "@/lib/statistical-preview-data";
import { percent, quantity, type Horizon } from "@/lib/forecast-chart-data";
import type { PreviewCandidate, PreviewScope, ProductCandidate } from "@/lib/forecast-preview";
import { ForecastGrid } from "./forecast-horizon-table";
import { humanStatus } from "./forecast-summary";
import { previewCandidateLabel } from "@/lib/preview-presentation";

function ScopeCandidates({ title, candidates }: { title: string; candidates: PreviewCandidate[] }) {
  return <div className="space-y-2"><h4 className="text-sm font-semibold">{title}</h4>
    <ForecastGrid headers={["Modelo", "WAPE scope", "Bias scope", "Observaciones", "Estado / motivo"]}
      rows={candidates.map(c => [c.model ?? "—", percent(retrospectiveMetric(c, "wape")),
        percent(retrospectiveMetric(c, "bias")), quantity(c.observations), c.reason ?? c.status ?? "Sin evaluación"])} /></div>;
}

export function EngineComparison({ scope, horizons = [], productId }: { scope: PreviewScope; horizons?: Horizon[]; productId?: string | null }) {
  if (scope.stored_results) return <StoredEngineComparison scope={scope} horizons={horizons} productId={productId}/>;
  const selection = scope.selection;
  const product = productId ? scope.products?.find(row => row.product_id === productId) : null;
  const productCandidates = (selection?.product_candidates ?? []).filter(row => row.product_id === productId);
  const suggestion = productId ? selection?.suggested_references?.[productId] ?? selection?.suggested_reference : null;
  const published = selection?.published_champion;
  const scopeLeader = selection?.scope_leader ?? selection?.preview_leader;
  const familyName = (row: ProductCandidate) => ({ statistical: "Estadístico", ml: "Machine Learning", ensemble: "Ensemble" })[row.family];
  const suggestionLabel = suggestion?.model && suggestion.status.startsWith("SUGGESTED") ? `${suggestion.model} *` : "Sin sugerencia confiable";
  const first = horizons.find(row => row.horizon === 1);
  return <section className="space-y-5"><h2 className="font-semibold">Comparativa de motores</h2>
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm">
        <p className="text-xs font-medium text-blue-900">Champion / Referencia</p>
        <p className="mt-2 text-xl font-semibold">{published?.version ?? suggestionLabel}</p>
        {published ? <p className="mt-1 text-xs">Champion publicado · certificado</p> : <>
          <p className="mt-1 text-xs">{suggestion?.status.startsWith("SUGGESTED") ? `Sugerido · Confianza ${suggestion.confidence ?? "Baja"}` : "Evidencia insuficiente para comparar modelos"}</p>
          {suggestion?.wape != null && <p className="mt-2 text-xs">WAPE retro producto: {percent(suggestion.wape)} · Bias: {percent(suggestion.bias)} · Observaciones: {quantity(suggestion.observations)}</p>}
          <p className="mt-2 text-xs">* Modelo sugerido con evidencia retrospectiva/provisional. No es Champion publicado.</p>
        </>}
      </div>
      <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
        <p className="text-xs font-medium text-slate-500">Líder retrospectivo del scope</p>
        <p className="mt-2 text-xl font-semibold">{previewCandidateLabel(scopeLeader ?? null)}</p>
        <p className="mt-1 text-xs text-slate-500">{scope.chain_name ?? scope.chain_id} · No publicado; no sustituye la referencia del producto.</p>
      </div>
    </div>
    {productId ? <div className="space-y-2"><h3 className="text-sm font-semibold">Rendimiento del producto · {product?.description ?? productId}</h3>
      {productCandidates.length ? <ForecastGrid headers={["Motor", "Modelo", "WAPE producto", "Bias producto", "MAE", "RMSE", "Obs.", "H1", "Estado"]}
        rows={productCandidates.map(candidate => [familyName(candidate), candidate.model, percent(candidate.wape), percent(candidate.bias),
          quantity(candidate.mae), quantity(candidate.rmse), quantity(candidate.observations),
          quantity(candidate.horizons.find(row => row.horizon === 1)?.value),
          candidate.family === "ensemble" && (candidate.observations < 24 || candidate.windows < 3) ? "No comparable" : "Evaluado"])} />
        : <p className="text-sm text-slate-500">No hay métricas comparables de producto en esta vista previa. No se utilizará el WAPE del scope para recomendar.</p>}
      {suggestion?.reason_codes?.length ? <p className="text-xs text-slate-500">Reglas: {suggestion.reason_codes.join(" · ")}</p> : null}
      {first && "p50" in first && <p className="text-xs text-slate-500">H1 provisional: {quantity(first.forecast_towell)} pzas. La decisión oficial queda pendiente.</p>}
    </div> : null}
    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">Rendimiento global del scope · {scope.chain_name ?? scope.chain_id}</summary>
      <div className="mt-3 space-y-5"><p className="text-xs text-slate-500">Métricas agregadas de cadena/unidad comercial. No son WAPE ni Bias del producto seleccionado.</p>
        <p className="text-sm">Estadístico scope: WAPE <strong>{percent(scope.statistical?.retrospective_wape)}</strong>, Bias <strong>{percent(scope.statistical?.retrospective_bias)}</strong> · ML scope: WAPE <strong>{percent(scope.ml?.retrospective_wape)}</strong>, Bias <strong>{percent(scope.ml?.retrospective_bias)}</strong></p>
        <ScopeCandidates title="Modelos estadísticos" candidates={scope.statistical?.scope_candidates ?? scope.statistical?.candidates ?? []}/>
        <ScopeCandidates title="Machine Learning" candidates={scope.ml?.candidates ?? []}/></div>
    </details>
    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">Detalles técnicos</summary>
      <p className="mt-3 text-xs text-slate-500">{scope.engine_version ?? "—"} · {scope.mode ?? "—"} · {scope.status} · {scope.certification_status ?? "—"}</p>
      <p className="mt-2 text-sm">{humanStatus(scope.certification_status)}</p>
      <p className="mt-2 text-xs text-slate-500">Política: {selection?.selection_policy_version ?? "Legacy"} · Comparación: {selection?.comparison_status ?? "Sin evaluar"}</p>
      <p className="mt-2 text-xs text-slate-500">Catálogo estadístico: {(scope.statistical?.available_candidates ?? []).map((model, index) => <span key={model}>{index ? " · " : ""}<span>{model}</span></span>)}</p>
    </details>
  </section>;
}

function StoredEngineComparison({ scope, horizons, productId }: { scope: PreviewScope; horizons: Horizon[]; productId?: string | null }) {
  const stored = scope.stored_results!;
  const products = stored.products.filter(product => !productId || product.product_id === productId);
  const families = [["statistical", "Estadístico"], ["ml", "Machine Learning"], ["ensemble", "Ensemble"]] as const;
  const first = horizons.find(row => row.horizon === 1);
  const single = stored.consolidated_metrics_status === "SINGLE_PRODUCT" && products.length === 1;
  const summary = families.map(([family, label]) => {
    const field = family === "ensemble" ? "forecast_towell" : family === "ml" ? "ml_value" : "statistical_value";
    const methods = new Map<string, number>();
    let covered = 0;
    for (const product of products) {
      const curve = scope.products?.find(row => row.product_id === product.product_id)?.horizons ?? [];
      if (curve.length !== 12 || curve.some(row => row[field] == null || !Number.isFinite(row[field]))) continue;
      covered++;
      const method = family === "statistical" ? product.statistical_model : family === "ml" ? product.ml_model : product.candidates.find(row => row.family === family)?.model;
      const name = method ?? "Método no informado";
      methods.set(name, (methods.get(name) ?? 0) + 1);
    }
    const metric = single ? products[0].candidates.find(row => row.family === family) : undefined;
    return [label, [...methods].map(([name, count]) => `${name} (${count})`).join(" · ") || "Sin método validado guardado",
      quantity(first?.[field]), single ? percent(metric?.wape) : "—", single ? percent(metric?.bias) : "—",
      single ? quantity(metric?.observations) : "Sin agregación acreditada", `${covered}/${products.length}`,
      !covered ? "Sin curva validada" : covered < products.length ? "TOTAL PARCIAL" : "Disponible"];
  });
  return <section className="space-y-5" aria-label="Comparativa de resultados almacenados">
    <h2 className="font-semibold">Comparativa de motores · resultados almacenados</h2>
    <p className="text-xs text-slate-600">Objetivo {stored.objective} · Corte {stored.issue_period}. Métricas retrospectivas guardadas de comparación entre familias; no constituyen el catálogo completo de métodos internos ni una evaluación LIVE.</p>
    {stored.consolidated_metrics_status === "NOT_ACCREDITED" && <p className="rounded-xl border bg-slate-50 p-3 text-sm text-slate-600">WAPE y Bias del consolidado: Sin métrica acreditada. {stored.consolidated_reason} No se promedian métricas de variantes ni se toma el líder de la cadena como referencia del modelo.</p>}
    <ForecastGrid headers={["Motor", "Método / distribución", "H1", "WAPE retrospectivo", "Bias retrospectivo", "Observaciones", "Cobertura", "Estado"]} rows={summary}/>
    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">Métricas individuales guardadas por variante</summary><div className="mt-3">
    <ForecastGrid headers={["Producto", "Cadena / scope", "Cálculo", "Motor", "Método / estrategia", "WAPE retrospectivo", "Bias retrospectivo", "MAE", "RMSE", "Obs.", "Ventanas", "H1", "Cobertura"]}
      rows={products.flatMap(product => families.map(([family, label]) => {
        const candidate = product.candidates.find(row => row.family === family);
        return [product.description, product.chain_id, product.calculation_code ?? "Sin cálculo compatible", label, candidate?.model ?? candidate?.strategy ?? "Sin candidato validado guardado",
          percent(candidate?.wape), percent(candidate?.bias), quantity(candidate?.mae), quantity(candidate?.rmse), quantity(candidate?.observations), quantity(candidate?.windows),
          quantity(candidate?.horizons.find(row => row.horizon === 1)?.value), candidate?.horizons.length === 12 ? "H1–H12" : "Sin curva completa"];
      }))}/></div></details>
    <div className="space-y-3">{products.map(product => {
      const reference = product.suggested_reference;
      const suggested = reference?.status.startsWith("SUGGESTED") ? reference.model ?? reference.strategy : null;
      return <article key={product.product_id} className="rounded-xl border bg-white p-4 text-sm">
        <h3 className="font-semibold">{product.description} · {product.calculation_code ?? "Sin cálculo compatible"}</h3>
        <p className="mt-2">Referencia sugerida guardada: {suggested ? `${suggested} *` : "Sin sugerencia comparable"}. No es una decisión ni un Champion publicado.</p>
        {reference?.comparison_reason && <p className="mt-1 text-xs text-slate-500">Motivo guardado: {reference.comparison_reason}</p>}
        {reference?.reason_codes?.length ? <p className="mt-1 text-xs text-slate-500">{reference.reason_codes.map(code => ({ STABILITY_CONTROL_FAIL: "El control de estabilidad impide promover el candidato con menor error aislado.", INSUFFICIENT_PRODUCT_OBSERVATIONS: "Faltan observaciones comparables del producto.", PRACTICAL_TIE: "Diferencias de desempeño dentro del empate práctico de la política.", PRACTICAL_EQUIVALENCE: "La política considera equivalentes las diferencias observadas." })[code] ?? "Criterio de la política conservada.").filter((value, index, all) => all.indexOf(value) === index).join(" ")} Criterios conservados: {reference.reason_codes.join(" · ")}</p> : null}
        <p className="mt-2"><strong>Forecast Towell vigente:</strong> {product.decision ? `${product.decision.selected_forecast} · ${product.decision.selected_model ?? "Método no informado"} · ${product.decision.calculation_code} · Corte ${product.decision.issue_period}` : "Sin decisión"}.</p>
        {product.decision && <p className="mt-1 text-xs text-slate-500">Usuario {product.decision.created_by ?? "Sin dato"} · {product.decision.created_at} · Motivo: {product.decision.reason ?? "Sin motivo registrado"}.</p>}
        <p className="mt-2">{scope.selection?.published_champion?.version ? `Champion publicado: ${scope.selection.published_champion.version}` : "Sin Champion publicado"}.</p>
        <details className="mt-3"><summary className="cursor-pointer text-sm font-medium">Historial de decisiones · {product.description}</summary><div className="mt-2">{product.decision_history.length ? <ForecastGrid headers={["Cálculo", "Corte", "Alternativa", "Método", "Usuario", "Fecha", "Motivo"]} rows={product.decision_history.map(event => [event.calculation_code, event.issue_period, event.selected_forecast, event.selected_model ?? "Sin dato", event.created_by, event.created_at, event.reason ?? "Sin motivo registrado"])}/> : <p className="text-xs text-slate-500">Sin decisiones registradas.</p>}</div></details>
        <p className="mt-2 break-words text-xs text-slate-500">Cálculo {product.calculation_id ?? "Sin dato"} · Emisión acreditada {product.emitted_at ?? "Sin dato"} · Registro/importación {product.created_at ?? "Sin dato"} · Corte {product.cutoff_at ?? "Sin dato"} · Motor {product.engine_version ?? "Sin dato"} · Evidencia {product.evidence_mode ?? "Sin dato"} · Estado {product.status}.</p>
      </article>;
    })}</div>
  </section>;
}
