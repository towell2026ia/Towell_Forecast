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
