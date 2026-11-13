import { describe, expect, it } from "vitest";
import { scopeHorizons, storedCommercialScope, traderPoints } from "../../lib/forecast-chart-data";
import { addReportMonth, type ReportCalculation, type ReportSnapshot } from "../../lib/forecast-reporting";
import type { PreviewScope, ProductCandidate, SuggestedReference } from "../../lib/forecast-preview";
import { emptyFilters, type Filters } from "../../lib/supabase/types";
import { operationalContext } from "./operational-context-fixture";

// Entirely synthetic stored records; no engine, private files or clients are used.
const issue = "2026-10", engine = "synthetic-stored-engine", evidence = "RETROSPECTIVE_TRAINING";
function example() {
  const products = ["blue", "red"].map(id => ({ id, chain_id: "chain", category_id: "cat", product_code: id,
    variant_code: null, description: `Synthetic ${id}`, identifiers: [] as [string, string][],
    commercial: { product_id: id, chain_id: "chain", model: "DESIGN", color: id, size: "MB", purchase_type: "RS" as const,
      product_type: "TOALLA", status: "PENDING_CLASSIFICATION" as const, source: "SYNTHETIC", reasons: [] } }));
  const curves = products.flatMap((p, index) => Array.from({ length: 12 }, (_, n) => ({
    id: `${p.id}-h${n + 1}`, calculation_id: p.id, product_id: p.id, chain_id: "chain", horizon: n + 1,
    target_period: addReportMonth(issue, n + 1), statistical_value: index ? 20 : 10, ml_value: index ? null : 5,
    ensemble_value: index ? 21 : 11, p10: null, p50: index ? 21 : 11, p90: null, p95: null,
    band_basis: "INSUFFICIENT", band_observations: 0, statistical_model: index ? "Holt" : "SBA", ml_model: index ? null : "RF",
  })));
  function metric(id: string, family: ProductCandidate["family"], wape: number | null): ProductCandidate {
    const field = { statistical: "statistical_value", ml: "ml_value", ensemble: "ensemble_value" }[family] as "statistical_value" | "ml_value" | "ensemble_value";
    return { family, product_id: id, model: family === "statistical" ? id === "blue" ? "SBA" : "Holt" : family === "ml" ? "RF" : "Ensemble ponderado",
      strategy: family, wape, bias: id === "blue" ? 0 : -4, mae: 2, rmse: 3, observations: 24, windows: 4,
      evaluation_signature: `paired-${id}`, evidence_mode: evidence,
      horizons: curves.filter(h => h.product_id === id).map(h => ({ horizon: h.horizon, target_period: h.target_period, value: h[field]! })),
      by_horizon: [{ horizon: 1, wape, observations: 12 }, { horizon: 3, wape: null, observations: 0 }] };
  }
  function reference(candidate: ProductCandidate): SuggestedReference {
    return { ...candidate, status: "SUGGESTED_RETROSPECTIVE", official: false, comparison_status: "COMPARABLE",
      reason_codes: ["STORED_REFERENCE"], confidence: "Media" };
  }
  const calculations: ReportCalculation[] = products.map(p => {
    const statistical = metric(p.id, "statistical", p.id === "blue" ? 18.5 : 42);
    const ml = p.id === "blue" ? metric(p.id, "ml", 12.25) : null;
    const ensemble = metric(p.id, "ensemble", p.id === "blue" ? 17.75 : 40);
    return { id: p.id, chain_id: "chain", product_id: p.id, objective: "Venta", calculation_no: 1, calculation_code: "C1",
      issue_period: issue, status: "READY_FOR_DECISION", created_at: "2026-11-05T10:00:00Z", created_by: "synthetic-actor",
      suggested_reference: reference(ensemble), recalculation_reason: null, candidate_metrics: { STATISTICAL: statistical, ML: ml, ENSEMBLE: ensemble },
      input_snapshot: { source_context: operationalContext }, research_snapshot: null, data_snapshot_hash: "synthetic",
      preview_id: "archived-preview", engine_version: engine, git_sha: "synthetic", evidence_mode: evidence };
  });
  const snapshot: ReportSnapshot = { products, chains: [{ id: "chain", code: "S", name: "Synthetic", parentId: null,
    scopeType: "COMMERCIAL_UNIT", status: "ACTIVE", has_history: true }], categories: [], calculations, horizons: curves,
    decisions: [], live: [], customers: [], provisionals: [], sourceContext: operationalContext,
    capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false } };
  const filters: Filters = { ...emptyFilters, chainId: "chain", purchaseType: "RS", commercialModels: ["DESIGN"] };
  return { snapshot, filters };
}
function onlyBlue(snapshot: ReportSnapshot) {
  return { ...snapshot, products: snapshot.products.filter(p => p.id === "blue"), calculations: snapshot.calculations.filter(c => c.product_id === "blue"),
    horizons: snapshot.horizons.filter(h => h.product_id === "blue") };
}

describe("Saved main-candidate comparison without a preview or a new ranking", () => {
  it("restores literal product metrics and the saved suggestion even if another candidate has lower WAPE", () => {
    const { snapshot, filters } = example(), single = onlyBlue(snapshot), before = JSON.stringify(single);
    const selected = storedCommercialScope(single, { ...filters, productId: "blue" });
    expect(selected.statistical?.candidates).toMatchObject([{ product_id: "blue", family: "statistical", model: "SBA",
      retrospective_wape: 18.5, retrospective_bias: 0, retrospective_mae: 2, retrospective_rmse: 3, observations: 24,
      windows: 4, evaluation_signature: "paired-blue", by_horizon: [{ horizon: 1, wape: 18.5 }, { horizon: 3, wape: null }] }]);
    expect(selected.ml?.candidates.find(c => c.model === "RF")?.by_product).toMatchObject([{ product_id: "blue", retrospective_wape: 12.25 }]);
    expect(selected.selection?.product_candidates).toEqual(Object.values(single.calculations[0].candidate_metrics));
    expect(selected.selection?.suggested_references?.blue).toEqual(single.calculations[0].suggested_reference);
    expect(selected.selection?.suggested_references?.blue?.family).toBe("ensemble");
    expect(selected.selection?.published_champion).toBeNull();
    expect(selected.selection?.automatic_promotion).toBe(false);
    expect(scopeHorizons(selected, { ...filters, productId: "blue" })).toHaveLength(12);
    expect(JSON.stringify(single)).toBe(before);
  });
  it("retains nulls and zero in saved metrics instead of treating missing evidence as a perfect score", () => {
    const { snapshot, filters } = example(), single = onlyBlue(snapshot);
    single.calculations[0].candidate_metrics.STATISTICAL!.wape = null;
    single.calculations[0].candidate_metrics.STATISTICAL!.bias = 0;
    single.calculations[0].candidate_metrics.STATISTICAL!.by_horizon[0].wape = 0;
    const selected = storedCommercialScope(single, { ...filters, productId: "blue" });
    expect(selected.statistical?.candidates[0]).toMatchObject({ retrospective_wape: null, retrospective_bias: 0,
      by_horizon: [{ horizon: 1, wape: 0 }, { horizon: 3, wape: null }] });
  });
  it("reports a saved manual Forecast Towell decision separately from the suggestion and a published Champion", () => {
    const { snapshot, filters } = example(), single = onlyBlue(snapshot);
    single.decisions = [{ id: "synthetic-decision", calculation_id: "blue", chain_id: "chain", product_id: "blue",
      selected_candidate: "STATISTICAL", selected_curve: single.horizons.map(h => ({ horizon: h.horizon, target_period: h.target_period, value: h.statistical_value })),
      selected_at: "2026-11-06T10:00:00Z", selected_by: "synthetic-reviewer", decision_reason: "Synthetic manual reason", comment: null, system_suggestion: "ENSEMBLE" }];
    const before = JSON.stringify(single), selected = storedCommercialScope(single, { ...filters, productId: "blue" });
    expect(selected.stored_results).toMatchObject({ objective: "Venta", issue_period: issue, consolidated_metrics_status: "SINGLE_PRODUCT",
      products: [{ product_id: "blue", chain_id: "chain", calculation_id: "blue", calculation_code: "C1", engine_version: engine,
        evidence_mode: evidence, decision: { id: "synthetic-decision", selected_forecast: "STATISTICAL", selected_model: "SBA",
          reason: "Synthetic manual reason", created_by: "synthetic-reviewer", created_at: "2026-11-06T10:00:00Z" } }] });
    expect(selected.selection?.suggested_references?.blue?.family).toBe("ensemble");
    expect(selected.selection?.published_champion).toBeNull(); expect(selected.selection?.automatic_promotion).toBe(false);
    expect(JSON.stringify(single)).toBe(before);
  });
  it("keeps an adopted C1 decision with its own cut when a newer C2 remains undecided", () => {
    const { snapshot, filters } = example(), single = onlyBlue(snapshot), c1 = single.calculations[0];
    const c2 = structuredClone(c1);
    c1.issue_period = "2026-09"; c1.status = "DECIDED"; c1.created_at = "2026-10-05T10:00:00Z";
    single.horizons.forEach(h => { h.target_period = addReportMonth(c1.issue_period, h.horizon); });
    Object.values(c1.candidate_metrics).forEach(candidate => candidate?.horizons.forEach(h => { h.target_period = addReportMonth(c1.issue_period, h.horizon); }));
    c2.id = "blue-c2"; c2.calculation_no = 2; c2.calculation_code = "C2"; c2.created_at = "2026-11-06T10:00:00Z";
    const newer = single.horizons.map(h => ({ ...h, id: `${c2.id}-${h.horizon}`, calculation_id: c2.id,
      target_period: addReportMonth(c2.issue_period, h.horizon), statistical_value: 130, ml_value: 70, ensemble_value: 140, p50: 140 }));
    const values = { statistical: 130, ml: 70, ensemble: 140 };
    Object.values(c2.candidate_metrics).forEach(candidate => candidate?.horizons.forEach(h => { h.value = values[candidate.family]; }));
    single.calculations.push(c2); single.horizons.push(...newer);
    single.decisions = [{ id: "adopted-c1", calculation_id: c1.id, chain_id: "chain", product_id: "blue", selected_candidate: "STATISTICAL",
      selected_curve: single.horizons.filter(h => h.calculation_id === c1.id).map(h => ({ horizon: h.horizon, target_period: h.target_period, value: h.statistical_value })),
      selected_at: "2026-10-06T10:00:00Z", selected_by: "synthetic-reviewer", decision_reason: "Original adopted plan", comment: null, system_suggestion: "ENSEMBLE" }];
    const before = JSON.stringify(single), selected = storedCommercialScope(single, { ...filters, productId: "blue" });
    expect(selected.stored_results?.products[0]).toMatchObject({ calculation_id: c2.id, calculation_code: "C2", status: "READY_FOR_DECISION",
      decision: { id: "adopted-c1", calculation_id: c1.id, calculation_code: "C1", issue_period: "2026-09", selected_forecast: "STATISTICAL", selected_model: "SBA" },
      decision_history: [{ id: "adopted-c1", calculation_id: c1.id, issue_period: "2026-09" }] });
    expect(scopeHorizons(selected, { ...filters, productId: "blue" })[0]).toMatchObject({ target_period: "2026-11", statistical_value: 130, forecast_towell: 140 });
    expect(selected.selection?.published_champion).toBeNull(); expect(selected.selection?.automatic_promotion).toBe(false);
    expect(JSON.stringify(single)).toBe(before);
  });
  it("suppresses unrelated central quantiles when P50 differs from the displayed Ensemble curve without changing saved values", () => {
    const frozen = { horizon: 1, target_period: "2026-11", statistical_value: 141, ml_value: 139, forecast_towell: 140,
      p10: 100, p50: 141, p90: 180, p95: 200 };
    const before = JSON.stringify(frozen), filters = { ...emptyFilters, chainId: "chain", productId: "blue" };
    const point = traderPoints([], [], [frozen], issue, filters).find(p => p.period === frozen.target_period)!;
    expect(point).toMatchObject({ towell: 140, statistical: 141, p10: null, p50: null, p90: null, p95: null, band90: null, band95: null });
    expect(JSON.stringify(frozen)).toBe(before);
    const aligned = traderPoints([], [], [{ ...frozen, p50: frozen.forecast_towell }], issue, filters).find(p => p.period === frozen.target_period)!;
    expect(aligned).toMatchObject({ p10: 100, p50: frozen.forecast_towell, p90: 180, p95: 200, band90: [100, 180], band95: [100, 200] });
    const sparse = traderPoints([], [], [{ ...frozen, p50: frozen.forecast_towell, p95: null }], issue, filters).find(p => p.period === frozen.target_period)!;
    expect(sparse).toMatchObject({ p10: null, p50: frozen.forecast_towell, p90: null, p95: null, band90: null, band95: null });
  });
  it("keeps methods and metrics by variant while group WAPE, recommendation and bands are never manufactured", () => {
    const { snapshot, filters } = example(), selected = storedCommercialScope(snapshot, filters);
    expect(selected.statistical?.models).toEqual({ SBA: 1, Holt: 1 });
    expect(selected.statistical?.candidates.map(c => [c.product_id, c.retrospective_wape])).toEqual([["blue", 18.5], ["red", 42]]);
    expect(selected.statistical?.retrospective_wape).toBeNull(); expect(selected.statistical?.retrospective_bias).toBeNull();
    expect(selected.ml?.retrospective_wape).toBeNull(); expect(selected.ml?.retrospective_bias).toBeNull();
    expect(selected.selection?.suggested_reference).toBeFalsy();
    expect(selected.selection?.scope_leader).toBeNull();
    expect(selected.selection?.suggested_references?.red).toEqual(snapshot.calculations[1].suggested_reference);
    expect(selected.selection?.product_candidates).toHaveLength(5);
    expect(selected.commercial_projection).toMatchObject({ products: 2, statistical: 2, ml: 1, ensemble: 2 });
    const future = scopeHorizons(selected, filters), points = traderPoints([], [], future, issue, filters);
    expect(future[0]).toMatchObject({ statistical_value: 30, ml_value: 5, forecast_towell: 32 });
    expect(points.filter(p => p.period > issue).every(p => p.band90 === null && p.band95 === null)).toBe(true);
    expect(selected.products?.find(p => p.product_id === "red")?.horizons?.every(h => h.ml_value === null && h.p10 === null && h.p90 === null && h.p95 === null)).toBe(true);
  });
  it("does not borrow candidates, scope winners or publication from any preview", () => {
    const { snapshot, filters } = example();
    snapshot.calculations.forEach(c => { c.candidate_metrics = {}; c.suggested_reference = null; });
    const preview: PreviewScope = { chain_id: "chain", issue_period: issue, engine_version: engine, status: "PREVIEW", source_context: operationalContext,
      statistical: { status: "COMPLETED", models: { "Preview-only method": 100 }, candidates: [{ family: "statistical", product_id: "blue", model: "Preview-only method", retrospective_wape: 1 }], retrospective_wape: 1, retrospective_bias: 0 },
      ml: { status: "COMPLETED", training_samples: 900, leader: "Preview-only ML", candidates: [{ model: "Preview-only ML", retrospective_wape: 1 }], retrospective_wape: 1, retrospective_bias: 0 },
      selection: { published_champion: { version: "must-not-borrow" }, preview_leader: { model: "Preview-only leader" }, preview_challenger: null,
        scope_leader: { model: "Preview-only leader" }, no_degradation: true, automatic_promotion: false } };
    const selected = storedCommercialScope(snapshot, filters, preview);
    expect(selected.statistical?.candidates).toEqual([]); expect(selected.ml?.candidates).toEqual([]);
    expect(selected.selection?.product_candidates).toEqual([]); expect(selected.selection?.published_champion).toBeNull();
    expect(selected.selection?.scope_leader).toBeNull(); expect(selected.selection?.preview_leader).toBeNull();
  });
  it("excludes mismatched source generation and objective instead of exposing their saved metric or recommendation", () => {
    for (const mismatch of ["hash", "generation", "objective"] as const) {
      const { snapshot, filters } = example(), single = onlyBlue(snapshot), calc = single.calculations[0];
      if (mismatch === "objective") calc.objective = "Pedido";
      else calc.input_snapshot = { source_context: { ...operationalContext, ...(mismatch === "hash" ? { source_hash: "b".repeat(64) } : { generation: operationalContext.generation + 1 }) } };
      const selected = storedCommercialScope(single, { ...filters, productId: "blue" });
      expect(selected.statistical?.candidates).toEqual([]); expect(selected.selection?.product_candidates).toEqual([]);
      expect(selected.selection?.suggested_references?.blue).toBeUndefined();
      expect(selected.products?.[0].horizons).toEqual([]);
    }
  });
  it("does not mix a saved metric from another cut, engine or evidence mode into a selected group's curves", () => {
    for (const mismatch of ["cut", "engine", "evidence"] as const) {
      const { snapshot, filters } = example(), red = snapshot.calculations[1];
      // Red is the latest compatible anchor; blue must remain pending in this view.
      red.created_at = "2026-11-06T10:00:00Z";
      if (mismatch === "cut") {
        red.issue_period = "2026-11";
        snapshot.horizons.filter(h => h.product_id === "red").forEach(h => { h.target_period = addReportMonth("2026-11", h.horizon); });
        Object.values(red.candidate_metrics).forEach(candidate => candidate?.horizons.forEach(h => { h.target_period = addReportMonth("2026-11", h.horizon); }));
      } else if (mismatch === "engine") red.engine_version = "new-synthetic-engine";
      else {
        red.evidence_mode = "OTHER_EVIDENCE";
        Object.values(red.candidate_metrics).forEach(candidate => { if (candidate) candidate.evidence_mode = red.evidence_mode; });
        red.suggested_reference!.evidence_mode = red.evidence_mode;
      }
      const selected = storedCommercialScope(snapshot, filters);
      expect(selected.statistical?.candidates.map(c => c.product_id)).toEqual(["red"]);
      expect(selected.selection?.product_candidates?.every(c => c.product_id === "red")).toBe(true);
      expect(selected.selection?.suggested_references?.blue).toBeUndefined();
      expect(selected.products?.find(p => p.product_id === "blue")?.horizons).toEqual([]);
    }
  });
  it("rejects a candidate metric that names a different product", () => {
    const { snapshot, filters } = example(), single = onlyBlue(snapshot);
    single.calculations[0].candidate_metrics.STATISTICAL!.product_id = "foreign";
    expect(() => storedCommercialScope(single, { ...filters, productId: "blue" })).toThrow();
  });
  it("rejects saved candidates whose family, evidence, horizon membership or quantities contradict their frozen curve", () => {
    for (const defect of ["family", "evidence", "missing", "duplicate", "period", "quantity"] as const) {
      const { snapshot, filters } = example(), single = onlyBlue(snapshot), candidate = single.calculations[0].candidate_metrics.STATISTICAL!;
      if (defect === "family") candidate.family = "ml";
      else if (defect === "evidence") candidate.evidence_mode = "OTHER_EVIDENCE";
      else if (defect === "missing") candidate.horizons.pop();
      else if (defect === "duplicate") candidate.horizons[11].horizon = 1;
      else if (defect === "period") candidate.horizons[0].target_period = "2025-11";
      else candidate.horizons[0].value = 999;
      expect(() => storedCommercialScope(single, { ...filters, productId: "blue" }), defect).toThrow("stored_candidate_lineage_invalid");
    }
  });
  it("rejects a recommendation for a foreign product without recomputing a replacement", () => {
    const { snapshot, filters } = example(), single = onlyBlue(snapshot);
    single.calculations[0].suggested_reference = { ...single.calculations[0].suggested_reference!, product_id: "foreign" };
    expect(() => storedCommercialScope(single, { ...filters, productId: "blue" })).toThrow("stored_suggestion_lineage_invalid");
  });
});
