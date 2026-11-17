import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { forecastComposition } from "../../lib/forecast-composition";
import { addReportMonth, comparableForecastRows, confirmedMissingTowellHistory, emptyReportChoice, type ReportSnapshot } from "../../lib/forecast-reporting";
import { periodsDashboard } from "../../lib/periods-dashboard";
import { operationalPeriodsSheets } from "../../lib/operational-periods-xlsx";
import { planningXlsx } from "../../lib/planning-xlsx";
import { scopeHorizons, storedCommercialScope } from "../../lib/forecast-chart-data";
import { EngineComparison } from "../../components/forecast/engine-comparison";
import { emptyFilters, type ForecastReadRepository, type HistoricalRow } from "../../lib/supabase/types";
import { operationalContext } from "./operational-context-fixture";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { ForecastPeriodsView } from "../../app/forecast-periods-view";

function fixture(): ReportSnapshot {
  const products = ["mixed", "stat-only"].map(id => ({ id, chain_id: "chain", category_id: null,
    product_code: id, description: id, variant_code: null, identifiers: [], commercial: { product_id: id, chain_id: "chain", model: "SYNTHETIC",
      color: id, size: null, product_type: "TOALLA", status: "APPROVED" as const, source: "SYNTHETIC", reasons: [] } }));
  const calculations = products.map(p => ({ id: `calc-${p.id}`, chain_id: "chain", product_id: p.id, objective: "Venta", calculation_no: 2,
    calculation_code: "C2", issue_period: "2026-10", created_at: "2026-11-05T12:00:00Z", created_by: "synthetic", status: "READY_FOR_DECISION",
    suggested_reference: null, recalculation_reason: null, candidate_metrics: {}, input_snapshot: { source_context: operationalContext },
    engine_version: "synthetic-engine-v2", git_sha: "synthetic-sha", data_snapshot_hash: "synthetic-data", preview_id: "synthetic-preview",
    research_snapshot: null, evidence_mode: "RETROSPECTIVE_TRAINING" }));
  return { products, calculations, chains: [{ id: "chain", code: "C", name: "Synthetic chain", status: "ACTIVE", has_history: true, parentId: null, scopeType: "COMMERCIAL_UNIT" }],
    categories: [], horizons: calculations.flatMap((c, index) => Array.from({ length: 12 }, (_, i) => ({ id: `${c.id}/${i}`, calculation_id: c.id,
      chain_id: c.chain_id, product_id: c.product_id, horizon: i + 1, target_period: addReportMonth("2026-10", i + 1),
      statistical_value: 100, statistical_model: "SBA", ml_value: index ? null : 500, ml_model: index ? null : "Individual Forest",
      ensemble_value: index ? 100 : 120, statistical_weight: index ? 1 : .75, ml_weight: index ? 0 : .25,
      ensemble_ml_component: index ? null : 180, ensemble_ml_model: index ? null : "Applied Gradient",
      p10: null, p50: index ? 100 : 120, p90: null, p95: null, band_basis: "INSUFFICIENT", band_observations: 0 }))),
    decisions: [], live: [], customers: [], provisionals: [], sourceContext: operationalContext,
    capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false } };
}

describe("Literal frozen forecast composition", () => {
  it("uses the actual Ensemble ML component, never the independently winning ML curve", () => {
    const s = fixture(), h = s.horizons[0], c = s.calculations[0];
    expect(h.ml_value).toBe(500);
    expect(forecastComposition(h, { engine_version: c.engine_version, source_context: c.input_snapshot.source_context }, operationalContext))
      .toMatchObject({ status: "RECORDED", statisticalMethod: "SBA", statisticalWeight: .75, mlMethod: "Applied Gradient", mlComponent: 180,
        mlWeight: .25, result: 120, engineVersion: "synthetic-engine-v2", sourceHash: operationalContext.source_hash, generation: operationalContext.generation });
  });
  it("identifies explicitly recorded statistical-only weights and leaves missing composition unknown", () => {
    const s = fixture(), c = s.calculations[1], h = s.horizons[12];
    const provenance = { engine_version: c.engine_version, source_context: c.input_snapshot.source_context };
    expect(forecastComposition(h, provenance, operationalContext)).toMatchObject({ status: "STATISTICAL_ONLY", mlWeight: 0, mlComponent: null, mlMethod: null });
    expect(forecastComposition({ ...h, statistical_weight: undefined, ml_weight: undefined }, provenance, operationalContext))
      .toMatchObject({ status: "UNRECORDED", statisticalWeight: null, mlWeight: null });
    expect(forecastComposition({ ...h, ensemble_value: 120 }, provenance, operationalContext).status).toBe("INCONSISTENT");
    expect(forecastComposition(h, { ...provenance, source_context: { ...operationalContext, generation: operationalContext.generation + 1 } }, operationalContext).status).toBe("SOURCE_MISMATCH");
  });
  it("rejects duplicate indexed horizons instead of exporting an arbitrary composition", () => {
    const s = fixture(), d = periodsDashboard(s, [], emptyReportChoice, [null, null]);
    s.horizons.push({ ...s.horizons[0], id: "duplicate", ensemble_ml_model: "Unrelated duplicate" });
    expect(() => operationalPeriodsSheets(s, d, [], [])).toThrow("report_horizon_lineage");
  });
  it("keeps per-product composition through stored UI, exact consolidation and the native XLSX export", () => {
    const s = fixture(), before = JSON.stringify(s), filters = { ...emptyFilters, chainId: "chain", commercialModels: ["SYNTHETIC"] };
    const scope = storedCommercialScope(s, filters), horizons = scopeHorizons(scope, filters);
    expect(scope.products?.[0].horizons?.[0]).toMatchObject({ ml_value: 500, ensemble_ml_model: "Applied Gradient", ensemble_ml_component: 180, statistical_weight: .75, ml_weight: .25 });
    expect(scope.products?.[1].horizons?.[0]).toMatchObject({ ensemble_ml_model: null, ensemble_ml_component: null, statistical_weight: 1, ml_weight: 0 });
    const d = periodsDashboard(s, [], emptyReportChoice, [null, null]), sheets = operationalPeriodsSheets(s, d, [], []);
    expect(d.points[9].values).toMatchObject({ STATISTICAL: 200, ML: 500, ENSEMBLE: 220 });
    const calculated = sheets.find(sheet => sheet.name === "Cálculos almacenados")!;
    const cells = (product: string) => calculated.rows.find(row => row[2] === product && row[5] === "2026-11")!;
    const cell = (product: string, header: string) => cells(product)[calculated.headers.indexOf(header)];
    expect(cell("mixed", "ML")).toBe(500);
    expect(cell("mixed", "Componente ML del Ensemble")).toBe(180);
    expect(cell("mixed", "Método ML individual")).toBe("Individual Forest");
    expect(cell("mixed", "Método ML del Ensemble")).toBe("Applied Gradient");
    expect(cell("stat-only", "Peso ML")).toBe(0);
    expect(cell("stat-only", "Estado composición")).toBe("Estadístico solo");
    expect(cell("mixed", "Fuente SHA-256")).toBe(operationalContext.source_hash);
    const comparison = sheets.find(sheet => sheet.name === "Comparativa")!;
    expect(comparison.rows[9][comparison.headers.indexOf("Estadístico · mismas variantes")]).toBe(100);
    expect(comparison.rows[9][comparison.headers.indexOf("ML · mismas variantes")]).toBe(500);
    expect(comparison.rows[9][comparison.headers.indexOf("Ensemble · mismas variantes")]).toBe(120);
    expect(comparison.rows[9][comparison.headers.indexOf("Cobertura · mismas variantes")]).toBe("1/2");
    expect(comparableForecastRows(d.matrix.rows, d.matrix.periods).map(row => row.product.id)).toEqual(["mixed"]);
    const xml = new TextDecoder().decode(planningXlsx(sheets)).match(/<worksheet[\s\S]*?<\/worksheet>/g)![2];
    expect(xml).toContain("Applied Gradient"); expect(xml).toContain("Estadístico solo"); expect(xml).toContain("synthetic-engine-v2");
    render(<EngineComparison scope={scope} horizons={horizons}/>);
    expect(screen.getByText("Composición guardada por horizonte · mixed")).toBeTruthy();
    expect(screen.getAllByText(/Estadístico solo ·/)).toHaveLength(12);
    expect(JSON.stringify(s)).toBe(before);
  });
  it("shows the same-variant matrix and credits no-history only after a complete successful Towell read", async () => {
    const s = fixture();
    s.sourceContext = { ...operationalContext, sales_source: "TOWELL", inventory_source: "ESTADISTICA", sales_available: true,
      sales_contract: "towell-sales-v1", sales_file_sha256: "d".repeat(64), sales_sheet: "Towell", sales_training_cutoff: "2026-10", authorized_source_hash: operationalContext.source_hash };
    s.calculations = s.calculations.map(c => ({ ...c, input_snapshot: { source_context: s.sourceContext } }));
    const unavailable = { ...s.sourceContext, sales_available: false };
    expect(forecastComposition(s.horizons[0], { engine_version: s.calculations[0].engine_version, source_context: unavailable }, unavailable).status).toBe("SOURCE_MISMATCH");
    const stockOnly = { ...s.products[0], id: "stock-only", description: "Stock-only product" }, awaitingCalculation = { ...s.products[0], id: "has-sales", description: "Sales without calculation" };
    s.products.push(stockOnly, awaitingCalculation);
    const history: HistoricalRow[] = [s.products[0], awaitingCalculation].map(p => ({ chain_id: p.chain_id, product_id: p.id, product: p,
      chain: "Synthetic chain", period: "2026-10", SALES: 0, ORDER: null, DELIVERY: null, availability: "UNKNOWN", exact: { SALES: "0.000" } }));
    const inventory = [{ chain_id: "chain", product_id: stockOnly.id, period: "2026-10", format: "FORMAT", upc: "000001", purchase_type: "RS", quantity_text: "0.000" },
      { chain_id: "chain", product_id: awaitingCalculation.id, period: "2026-10", format: "FORMAT", upc: "000002", purchase_type: "RS", quantity_text: "3.500" }];
    const d = periodsDashboard(s, history, emptyReportChoice, [null, null], inventory);
    const missing = d.matrix.rows.find(row => row.product.id === stockOnly.id)!, zero = d.matrix.rows.find(row => row.product.id === awaitingCalculation.id)!;
    expect(confirmedMissingTowellHistory(missing, s.sourceContext, history, inventory, false)).toBe(false);
    expect(confirmedMissingTowellHistory(missing, s.sourceContext, history, inventory, true)).toBe(true);
    expect(confirmedMissingTowellHistory(zero, s.sourceContext, history, inventory, true)).toBe(false);
    const repository = { openOperationalRead: vi.fn(async () => s.sourceContext), getReportSnapshot: vi.fn(async () => s),
      getReportHistory: vi.fn(async () => history), getReportInventory: vi.fn(async () => inventory) } as unknown as ForecastReadRepository;
    render(<ForecastFiltersContext.Provider value={{ repository, filters: { ...emptyFilters, chainId: "chain" }, setFilters: vi.fn(), profile: { id: "synthetic", full_name: "Synthetic", global_role: "VIEWER", status: "ACTIVE" } }}><ForecastPeriodsView/></ForecastFiltersContext.Provider>);
    const common = await screen.findByRole("table", { name: "Matriz comparativa mismas variantes" });
    expect(within(common).getAllByRole("row")).toHaveLength(13);
    expect(within(common).getAllByText("1/4")).toHaveLength(12);
    const pending = screen.getByRole("table", { name: "Productos pendientes de cálculo" });
    expect(within(pending).getAllByText("Sin historial Towell; sin pronóstico")).toHaveLength(1);
    expect(within(pending).getByText("Sin cálculo compatible con la fuente oficial")).toBeTruthy();
  });
});
