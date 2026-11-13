import type { Filters } from "./supabase/types";
import type { PreviewAggregate, PreviewCandidate, PreviewHorizon, PreviewScope, ProductCandidate, SuggestedReference } from "./forecast-preview";
import { hasCommercialFilters, matchesCommercial, matchesChainScope, modelKey, selectedScopeIds } from "./commercial-filters";
import { buildForecastMatrix, emptyReportChoice, matchesOfficialSource, matrixTotal, validDecision, type ReportSnapshot } from "./forecast-reporting";

export type HistoricalMonth = { period: string; sale: number | null; order: number | null; delivery: number | null };
export type CustomerMonth = { period: string; value: number };
export type TraderPoint = HistoricalMonth & { client: number | null; towell: number | null; statistical: number | null; ml: number | null; provisional: number | null; p10: number | null; p50: number | null; p90: number | null; p95: number | null; band90: [number, number] | null; band95: [number, number] | null };
export type Horizon = PreviewHorizon | PreviewAggregate;
export const series = [
  { key: "sale", label: "Venta", color: "#175cd3" },
  { key: "order", label: "Pedido", color: "#7c3aed" },
  { key: "delivery", label: "Entrega", color: "#0891b2" },
  { key: "client", label: "Fcst Cliente", color: "#f59e0b", dash: "5 4" },
  { key: "towell", label: "Ensemble provisional", color: "#0f172a" },
  { key: "statistical", label: "Estadístico", color: "#64748b", dash: "7 5" },
  { key: "ml", label: "Machine Learning", color: "#c026d3", dash: "2 4" },
  { key: "provisional", label: "Estimación provisional", color: "#ea580c", dash: "6 4" },
] as const;
export type SeriesKey = typeof series[number]["key"];
export type SeriesVisibility = Record<SeriesKey, boolean>;
export const defaultVisibility: SeriesVisibility = { sale: true, order: true, delivery: true, client: false, towell: true, statistical: false, ml: false, provisional: true };
export const quantity = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "—" : value.toLocaleString("es-MX", { maximumFractionDigits: 2 });
export const percent = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "—" : `${quantity(value)}%`;
export function periodLabel(period: string | null | undefined) {
  if (!period) return "—";
  const [year, month] = period.split("-").map(Number);
  return `${["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"][month - 1]}-${year}`;
}
export function addMonth(period: string, offset: number) {
  const [year, month] = period.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1 + offset, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}
export function commercialScopeKey(filters: Filters) {
  return JSON.stringify([filters.chainId, filters.categoryId, filters.productId, filters.search.trim(), filters.commercialType ?? null,
    filters.commercialModels?.map(modelKey).sort() ?? null, filters.commercialColor ?? null, filters.commercialSize ?? null, filters.classificationStatus ?? null,
    filters.purchaseType ?? null, selectedScopeIds(filters)?.sort() ?? null]);
}
// Frozen E4 metrics compare the three saved family winners. They are not the
// full internal statistical/ML catalogue, nor a model-level error denominator.
function savedMetric(candidate: ProductCandidate): PreviewCandidate {
  return { family: candidate.family === "ensemble" ? undefined : candidate.family, model: candidate.model, strategy: candidate.strategy,
    product_id: candidate.product_id, retrospective_wape: candidate.wape, retrospective_bias: candidate.bias,
    retrospective_mae: candidate.mae, retrospective_rmse: candidate.rmse, retrospective_stability: candidate.stability,
    observations: candidate.observations, windows: candidate.windows, origins: candidate.windows,
    evaluation_signature: candidate.evaluation_signature, evaluation_mode: "RETROSPECTIVE_EVALUATION",
    by_horizon: candidate.by_horizon, available: true, selected: true,
    status: candidate.observations > 0 && candidate.wape !== null ? "EVALUATED" : "INSUFFICIENT_EVIDENCE" };
}
// Present already stored curves for the exact commercial selection. This does
// not execute a forecast or derive a group WAPE from individual percentages.
export function storedCommercialScope(snapshot: ReportSnapshot, filters: Filters, original?: PreviewScope | null): PreviewScope {
  if (!filters.chainId || !snapshot.sourceContext || snapshot.products.some(product => !matchesChainScope(product.chain_id, filters)
      || filters.productId && product.id !== filters.productId || !matchesCommercial(product, filters))) throw new Error("stored_scope_mismatch");
  const matrix = buildForecastMatrix(snapshot, emptyReportChoice, [null, null]);
  const selected = matrix.rows.find(row => row.calculation)?.calculation;
  const source = original?.chain_id === filters.chainId && matchesOfficialSource(original.source_context, snapshot.sourceContext)
    && (!selected || original.engine_version === selected.engine_version && original.issue_period === selected.issue_period.slice(0, 7)
      && (!original.mode || original.mode === selected.evidence_mode)) ? original : null;
  const issue = matrix.issue ?? snapshot.sourceContext.last_period;
  const distribution: Record<string, number> = {}, mlWinners: Record<string, string | null> = {};
  const candidates: ProductCandidate[] = [], suggestions: Record<string, SuggestedReference> = {};
  const products = matrix.rows.map(row => {
    const previous = source?.products?.find(product => product.product_id === row.product.id);
    const frozenProduct = row.calculation?.input_snapshot?.product as { forecast_status?: string; history_months?: number; classification?: string } | undefined;
    const stored = row.calculation ? snapshot.horizons.filter(h => h.calculation_id === row.calculation!.id) : [];
    const horizons: PreviewHorizon[] = stored.map(h => {
      if ([h.statistical_value, h.ensemble_value, h.p50].some(value => value === null || value === undefined || !Number.isFinite(Number(value)) || Number(value) < 0)
          || h.ml_value !== null && (!Number.isFinite(Number(h.ml_value)) || Number(h.ml_value) < 0)
          || [h.p10, h.p90, h.p95].some(value => value !== null && (!Number.isFinite(Number(value)) || Number(value) < 0))) throw new Error("stored_scope_horizon_incomplete");
      return { horizon: h.horizon, target_period: h.target_period.slice(0, 7), statistical_value: Number(h.statistical_value),
        ml_value: h.ml_value === null ? null : Number(h.ml_value), forecast_towell: Number(h.ensemble_value),
        p10: h.p10 === null ? null : Number(h.p10), p50: Number(h.p50),
        p90: h.p90 === null ? null : Number(h.p90), p95: h.p95 === null ? null : Number(h.p95),
        statistical_model: h.statistical_model ?? undefined, ml_model: h.ml_model, classification: previous?.classification ?? undefined,
        band_basis: h.band_basis as PreviewHorizon["band_basis"], band_observations: h.band_observations,
        band_status: h.band_status as PreviewHorizon["band_status"] };
    });
    const first = horizons.find(h => h.horizon === 1);
    for (const [family, field] of [["STATISTICAL", "statistical_value"], ["ML", "ml_value"], ["ENSEMBLE", "forecast_towell"]] as const) {
      const candidate = row.calculation?.candidate_metrics?.[family];
      if (!candidate) continue;
      // Link metrics to the literal frozen curve, product and evaluation mode.
      // A stale preview or a different family cannot fill missing evidence.
      if (candidate.product_id !== row.product.id || candidate.family !== family.toLowerCase()
        || candidate.evidence_mode !== row.calculation!.evidence_mode || !candidate.model
        || family === "STATISTICAL" && candidate.model !== first?.statistical_model
        || family === "ML" && candidate.model !== first?.ml_model
        || !Number.isSafeInteger(candidate.observations) || candidate.observations < 0
        || !Number.isSafeInteger(candidate.windows) || candidate.windows < 0
        || [candidate.wape, candidate.bias, candidate.mae, candidate.rmse, candidate.stability].some(value => value != null && !Number.isFinite(value))
        || candidate.horizons.length !== 12 || new Set(candidate.horizons.map(h => h.horizon)).size !== 12
        || candidate.horizons.some(h => h.target_period.slice(0, 7) !== addMonth(issue, h.horizon)
          || !horizons.some(stored => stored.horizon === h.horizon && stored[field] !== null && stored[field] === h.value))) throw new Error("stored_candidate_lineage_invalid");
      candidates.push(candidate);
    }
    const suggestion = row.calculation?.suggested_reference;
    // The stored selection policy remains authoritative; never rank a model by
    // averaging percentages or by replacing a saved recommendation in the UI.
    if (suggestion) {
      if (suggestion.product_id && suggestion.product_id !== row.product.id || suggestion.family && suggestion.model
        && !candidates.some(c => c.product_id === row.product.id && c.family === suggestion.family && c.model === suggestion.model)) throw new Error("stored_suggestion_lineage_invalid");
      suggestions[row.product.id] = suggestion;
    }
    if (first?.statistical_model) distribution[first.statistical_model] = (distribution[first.statistical_model] ?? 0) + 1;
    mlWinners[row.product.id] = first?.ml_value !== null && first?.ml_value !== undefined ? first.ml_model ?? null : null;
    return { ...previous, product_id: row.product.id, product_code: row.product.product_code, description: row.product.description,
      category_id: row.product.category_id, forecast_status: frozenProduct?.forecast_status ?? (horizons.length === 12 ? "ACTIVE" : previous?.forecast_status ?? "SIN_CALCULO_COMPATIBLE"),
      history_months: frozenProduct?.history_months ?? previous?.history_months, classification: frozenProduct?.classification ?? previous?.classification,
      statistical_model: first?.statistical_model ?? null, horizons };
  });
  const aggregates: PreviewAggregate[] = [];
  for (const level of ["chain", "category"] as const) {
    const keys = level === "chain" ? [filters.chainId] : [...new Set(matrix.rows.map(row => row.product.category_id).filter((key): key is string => Boolean(key)))];
    for (const key of keys) for (const [index, period] of matrix.periods.entries()) {
      const members = matrix.rows.filter(row => level === "chain" || row.product.category_id === key);
      const stat = matrixTotal(members, period, "STATISTICAL"), ml = matrixTotal(members, period, "ML"), ensemble = matrixTotal(members, period, "ENSEMBLE");
      if (stat.value !== null && ensemble.value !== null) aggregates.push({ level, key, horizon: index + 1, target_period: period,
        statistical_value: stat.value, ml_value: ml.value, forecast_towell: ensemble.value });
    }
  }
  const statistical = products.filter(product => product.horizons.length === 12).length;
  const ml = products.filter(product => product.horizons.length === 12 && product.horizons.every(h => h.ml_value !== null)).length;
  const states = products.reduce<Record<string, number>>((counts, product) => ({ ...counts, [product.forecast_status]: (counts[product.forecast_status] ?? 0) + 1 }), {});
  const statCandidates = candidates.filter(c => c.family === "statistical").map(savedMetric);
  const mlCandidates = [...new Set(candidates.filter(c => c.family === "ml").map(c => c.model))].map(model => {
    const members = candidates.filter(c => c.family === "ml" && c.model === model).map(savedMetric);
    return { ...(products.length === 1 ? members[0] : {}), family: "ml" as const, model, available: true,
      retrospective_wape: products.length === 1 ? members[0].retrospective_wape : null,
      retrospective_bias: products.length === 1 ? members[0].retrospective_bias : null, by_product: members };
  });
  const singleStat = products.length === 1 ? statCandidates[0] : undefined;
  const singleMl = products.length === 1 ? candidates.find(c => c.family === "ml") : undefined;
  return { ...source, chain_id: filters.chainId, chain_name: snapshot.chains.find(chain => chain.id === filters.chainId)?.name,
    status: "PREVIEW", issue_period: issue, latest_actual_period: snapshot.sourceContext.last_period,
    engine_version: selected?.engine_version ?? source?.engine_version, mode: "RETROSPECTIVE_TRAINING", evaluation_mode: "RETROSPECTIVE_EVALUATION",
    source_context: snapshot.sourceContext, provisional_cold_start: undefined, products, aggregates,
    commercial_projection: { filter_key: commercialScopeKey(filters), products: products.length, statistical, ml, ensemble: statistical,
      ml_models: [...new Set(Object.values(mlWinners).filter((model): model is string => Boolean(model)))], source: "STORED_CALCULATIONS" },
    stored_results: { objective: selected?.objective ?? "Venta", issue_period: issue,
      consolidated_metrics_status: products.length === 1 ? "SINGLE_PRODUCT" : "NOT_ACCREDITED",
      consolidated_reason: products.length === 1 ? "Métricas retrospectivas guardadas del producto y cálculo seleccionado."
        : "WAPE consolidado no acreditado: no se guardaron los errores y denominadores comparables por observación. No se promedian porcentajes.",
      products: matrix.rows.map(row => {
        const own = candidates.filter(c => c.product_id === row.product.id);
        const provenance = (row.calculation?.input_snapshot?.product as { offline_provenance?: { emitted_at?: unknown } } | undefined)?.offline_provenance;
        const emittedAt = typeof provenance?.emitted_at === "string" && Number.isFinite(Date.parse(provenance.emitted_at)) ? provenance.emitted_at : null;
        const history = snapshot.decisions.filter(event => event.product_id === row.product.id && event.chain_id === row.product.chain_id).map(event => {
          const calculation = snapshot.calculations.find(c => c.id === event.calculation_id && c.product_id === row.product.id && c.chain_id === row.product.chain_id
            && c.objective === "Venta" && matchesOfficialSource(c.input_snapshot?.source_context, snapshot.sourceContext!));
          if (!calculation) throw new Error("stored_decision_lineage_invalid");
          validDecision({ ...snapshot, decisions: [event] }, calculation);
          return { id: event.id, calculation_id: calculation.id, calculation_code: calculation.calculation_code,
            issue_period: calculation.issue_period.slice(0, 7), selected_forecast: event.selected_candidate,
            selected_model: calculation.candidate_metrics[event.selected_candidate]?.model ?? null,
            reason: event.decision_reason, created_at: event.selected_at, created_by: event.selected_by };
        }).sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
        return { product_id: row.product.id, chain_id: row.product.chain_id, description: row.product.description,
          calculation_id: row.calculation?.id ?? null, calculation_code: row.calculation?.calculation_code ?? null,
          created_at: row.calculation?.created_at ?? null, emitted_at: emittedAt, cutoff_at: row.calculation?.cutoff_at ?? null,
          engine_version: row.calculation?.engine_version ?? null, evidence_mode: row.calculation?.evidence_mode ?? null,
          status: row.calculation?.status ?? "SIN_CALCULO_COMPATIBLE", statistical_model: products.find(p => p.product_id === row.product.id)?.statistical_model ?? null,
          ml_model: mlWinners[row.product.id] ?? null, candidates: own, suggested_reference: suggestions[row.product.id] ?? null, decision_history: history,
          // A newer undecided calculation does not revoke the last explicit
          // FT-Decision. Keep that event's own calculation and cut visible.
          decision: history.at(-1) ?? null };
      }) },
    eligibility: { visible_products: products.length, evaluated: products.length, stat_eligible: statistical, ml_eligible: ml, ...states },
    statistical: { status: statistical ? "COMPLETED" : "NOT_ELIGIBLE", models: distribution,
      candidates: statCandidates, scope_candidates: [], available_candidates: Object.keys(distribution), selected_metrics: singleStat,
      retrospective_wape: singleStat?.retrospective_wape ?? null, retrospective_bias: singleStat?.retrospective_bias ?? null },
    ml: { status: ml ? "COMPLETED" : "NOT_ELIGIBLE", training_samples: null, leader: singleMl?.model ?? null,
      candidates: mlCandidates, available_candidates: mlCandidates.map(c => c.model),
      retrospective_wape: singleMl?.wape ?? null, retrospective_bias: singleMl?.bias ?? null },
    selection: { published_champion: null, preview_leader: null, scope_leader: null, preview_challenger: null,
      suggested_reference: products.length === 1 ? suggestions[products[0].product_id] : undefined,
      suggested_references: suggestions, product_ml_winners: mlWinners, product_candidates: candidates, no_degradation: null, automatic_promotion: false },
  };
}
export function scopeHorizons(scope: PreviewScope | null, filters: Filters): Horizon[] {
  if (!scope || !filters.chainId || scope.chain_id !== filters.chainId || !scope.issue_period) return [];
  // A product-scoped run cannot masquerade as the full category/chain aggregate.
  let rows: Horizon[];
  if (filters.productId) rows = scope.products?.find(p => p.product_id === filters.productId && (!filters.categoryId || p.category_id === filters.categoryId))?.horizons ?? [];
  else if ((filters.search.trim() || hasCommercialFilters(filters)) && scope.commercial_projection?.filter_key !== commercialScopeKey(filters)) rows = [];
  else rows = scope.aggregates?.filter(r => r.level === (filters.categoryId ? "category" : "chain") && r.key === (filters.categoryId ?? filters.chainId)) ?? [];
  // Dates and numbers remain backend values; malformed/partial results are not repaired.
  if (rows.length !== 12 || new Set(rows.map(r => r.horizon)).size !== 12 || rows.some(r => r.horizon < 1 || r.horizon > 12 || r.target_period !== addMonth(scope.issue_period!, r.horizon) || !Number.isFinite(r.forecast_towell))) return [];
  return [...rows].sort((a, b) => a.horizon - b.horizon);
}
export type ProvisionalHorizon = { horizon: number; target_period: string; value: number };
export function scopeProvisionalHorizons(scope: PreviewScope | null, filters: Filters): ProvisionalHorizon[] {
  if (!scope || !filters.chainId || !filters.productId || scope.chain_id !== filters.chainId || !scope.issue_period ||
      !scope.products?.some(product => product.product_id === filters.productId && (!filters.categoryId || product.category_id === filters.categoryId))) return [];
  const rows = scope.provisional_cold_start?.horizons ?? [];
  if (rows.length !== 12 || new Set(rows.map(row => row.horizon)).size !== 12 || rows.some(row =>
    row.horizon < 1 || row.horizon > 12 || row.target_period !== addMonth(scope.issue_period!, row.horizon) ||
    !Number.isFinite(row.value) || row.value < 0)) return [];
  return [...rows].sort((a, b) => a.horizon - b.horizon);
}
export function traderPoints(history: HistoricalMonth[], customer: CustomerMonth[], horizons: Horizon[], cutoff: string | undefined, filters: Filters, provisional: ProvisionalHorizon[] = []): TraderPoint[] {
  if (!filters.chainId) return [];
  const historical = history.filter(r => (!cutoff || r.period <= cutoff) && (!filters.periodRange[0] || r.period >= filters.periodRange[0]) && (!filters.periodRange[1] || r.period <= filters.periodRange[1]));
  const byPeriod = new Map(historical.map(r => [r.period, r]));
  const fcst = new Map(horizons.map(r => [r.target_period, r]));
  const provisionalByPeriod = new Map(provisional.map(r => [r.target_period, r.value]));
  const client = new Map(customer.filter(r => cutoff && (r.period > cutoff || (!filters.periodRange[0] || r.period >= filters.periodRange[0]) && (!filters.periodRange[1] || r.period <= filters.periodRange[1]))).map(r => [r.period, r.value]));
  const dates = [...byPeriod.keys(), ...fcst.keys(), ...provisionalByPeriod.keys(), ...client.keys(), ...(cutoff ? [cutoff] : [])].sort();
  if (!dates.length) return [];
  const points: TraderPoint[] = [];
  for (let period = dates[0], count = 0; period <= dates.at(-1)! && count < 2400; period = addMonth(period, 1), count++) {
    const actual = byPeriod.get(period), future = fcst.get(period);
    const product = future && "p50" in future ? future : null;
    // Frozen quantiles can refer to the statistical centre rather than the
    // Ensemble curve. Do not relabel or move that uncertainty to another curve.
    const alignedCentre = product && product.p50 === product.forecast_towell;
    const bands = alignedCentre
      && [product.p10, product.p50, product.p90, product.p95].every(v => v !== null && Number.isFinite(v))
      && product.p10! <= product.p50 && product.p50 <= product.p90! && product.p90! <= product.p95!;
    points.push({ period, sale: actual?.sale ?? null, order: actual?.order ?? null, delivery: actual?.delivery ?? null,
      client: client.get(period) ?? null, towell: future?.forecast_towell ?? null, statistical: future && "statistical_value" in future ? future.statistical_value ?? null : null, ml: future && "ml_value" in future ? future.ml_value ?? null : null,
      provisional: provisionalByPeriod.get(period) ?? null,
      p10: bands ? product!.p10 : null, p50: alignedCentre ? product.p50 : null, p90: bands ? product!.p90 : null, p95: bands ? product!.p95 : null,
      band90: bands ? [product.p10!, product.p90!] : null, band95: bands ? [product.p10!, product.p95!] : null });
  }
  return points;
}
