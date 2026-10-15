import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { attachCommercial, changeCommercial, commercialOptions, filteredProducts, matchesCommercial, notApplicableCommercial, unknownCommercial } from "../../lib/commercial-filters";
import { periodsDashboard } from "../../lib/periods-dashboard";
import { addReportMonth, emptyReportChoice, type ReportSnapshot } from "../../lib/forecast-reporting";
import { emptyFilters, type CommercialClassification, type Filters, type ForecastReadRepository, type HistoricalRow, type Product } from "../../lib/supabase/types";
import { ForecastFiltersContext, GlobalFilters } from "../../app/forecast-towell-app";
import { ForecastPeriodsView } from "../../app/forecast-periods-view";
import { RailwayPreviewClient } from "../../lib/forecast-preview";
import type { SupabaseClient } from "@supabase/supabase-js";

const product: Product = { id: "p", chain_id: "c", category_id: "cat", product_code: "001", variant_code: null, description: "TOALLA MB FENDI OXFORD", identifiers: [["0001", "0002"]] };
const classification: CommercialClassification = { product_id: "p", chain_id: "c", model: "FENDI", color: "OXFORD", size: "MEDIO BAÑO", product_type: "TOALLA", status: "PENDING_CLASSIFICATION", source: "TRANSIENT", reasons: ["PRODUCT_MAPPING_APPROVAL_REQUIRED"] };
const chain = { id: "c", code: "C", name: "Authorized", status: "ACTIVE", has_history: true, parentId: null, scopeType: "COMMERCIAL_UNIT" };
function snapshot(): ReportSnapshot {
  return { products: [product], chains: [chain], categories: [], calculations: [{ id: "calc", chain_id: "c", product_id: "p", objective: "Venta", calculation_no: 1, calculation_code: "C1", issue_period: "2026-07", status: "READY_FOR_DECISION", created_at: "2026-10-13T00:00:00Z", created_by: "actor", suggested_reference: null, recalculation_reason: null, candidate_metrics: {}, input_snapshot: {}, research_snapshot: null, data_snapshot_hash: "hash", preview_id: "preview", engine_version: "v4", git_sha: "sha", evidence_mode: "RETROSPECTIVE_TRAINING" }], horizons: Array.from({ length: 12 }, (_, i) => ({ id: String(i), calculation_id: "calc", chain_id: "c", product_id: "p", horizon: i + 1, target_period: addReportMonth("2026-07", i + 1), statistical_value: 100 + i, ml_value: 200 + i, ensemble_value: 120 + i, p10: null, p50: 120 + i, p90: null, p95: null, band_basis: "INSUFFICIENT", band_observations: 0, statistical_model: "SBA", ml_model: "RF" })), decisions: [], live: [], provisionals: [], customers: [], capabilities: { vintage_persistence: false, champion_publication: false, official_publication: false } };
}
function history(period: string, sales: number | null = 10, overrides: Partial<HistoricalRow> = {}): HistoricalRow { return { chain_id: "c", product_id: "p", product, chain: chain.name, period, SALES: sales, ORDER: 10, DELIVERY: 9, availability: "UNKNOWN", ...overrides }; }
describe("Global commercial catalog facets", () => {
  it("reads the commercial response with GET, without requiring a forecast job or creating one", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ products: [classification], persisted: false }), { status: 200 }));
    const client = new RailwayPreviewClient({ auth: { getSession: async () => ({ data: { session: { access_token: "isolated-test-token" } } }) } } as unknown as SupabaseClient, "https://example.test");
    expect(await client.commercialCatalog()).toEqual([classification]);
    expect(fetcher).toHaveBeenCalledWith("https://example.test/api/forecast/commercial-catalog", expect.objectContaining({ method: "GET", cache: "no-store" }));
    expect(fetcher.mock.calls[0][1]?.body).toBeUndefined(); client.dispose();
  });
  it("retains opaque identity and separates FENDI/OXFORD/MB without promoting status", () => {
    const enriched = attachCommercial([product], [classification]);
    expect(enriched[0]).toMatchObject({ ...product, commercial: classification });
    expect(matchesCommercial(enriched[0], { ...emptyFilters, commercialModel: "FENDI", commercialColor: "OXFORD", commercialSize: "MEDIO BAÑO" })).toBe(true);
    expect(commercialOptions(enriched, emptyFilters, "commercialColor")[0].name).toContain("Pendiente");
    expect(matchesCommercial(enriched[0], { ...emptyFilters, classificationStatus: "APPROVED" })).toBe(false);
  });
  it("includes unknowns, multiword names, new models, reused colors and all 1010 products", () => {
    const products = Array.from({ length: 1010 }, (_, i) => ({ ...product, id: `p${i}` }));
    const enriched = attachCommercial(products, products.map((p, i) => ({ ...classification, product_id: p.id, model: i % 2 ? "NEW MULTIWORD DESIGN" : null })));
    expect(enriched).toHaveLength(1010); expect(commercialOptions(enriched, emptyFilters, "commercialModel").map(o => o.id)).toContain(unknownCommercial);
    expect(enriched.filter(p => matchesCommercial(p, { ...emptyFilters, commercialModel: "NEW MULTIWORD DESIGN", commercialColor: "OXFORD" }))).toHaveLength(505);
  });
  it("never accepts missing, duplicate or cross-chain classifications", async () => {
    expect(() => attachCommercial([product], [])).toThrow(); expect(() => attachCommercial([product], [classification, classification])).toThrow(); expect(() => attachCommercial([product], [{ ...classification, chain_id: "foreign" }])).toThrow();
    await expect(filteredProducts({ getProducts: async () => [product] } as ForecastReadRepository, { ...emptyFilters, commercialColor: "OXFORD" })).rejects.toThrow();
  });
  it("resets dependent facets and product while preserving periods and original filters", () => {
    const before = { ...emptyFilters, commercialType: "TOALLA", commercialModel: "FENDI", commercialColor: "OXFORD", commercialSize: "MEDIO BAÑO", productId: "p" };
    expect(changeCommercial(before, "commercialModel", "NEW DESIGN")).toMatchObject({ commercialType: "TOALLA", commercialModel: "NEW DESIGN", commercialColor: null, commercialSize: null, productId: null }); expect(before.productId).toBe("p");
  });
  it("distinguishes documented not-applicable attributes from missing evidence", () => {
    const products = attachCommercial([product], [{ ...classification, status: "APPROVED", color: null, not_applicable: ["color"] }]);
    expect(commercialOptions(products, emptyFilters, "commercialColor")[0].id).toBe(notApplicableCommercial);
    expect(matchesCommercial(products[0], { ...emptyFilters, commercialColor: unknownCommercial })).toBe(false);
  });
});
describe("Two historical years and 9+3 read-only comparison", () => {
  it("uses twelve actual columns per previous calendar year and only stored Oct-Dec forecast", () => {
    const s = snapshot(), before = JSON.stringify(s);
    const h = [2024, 2025].flatMap(y => Array.from({ length: 12 }, (_, i) => history(`${y}-${String(i + 1).padStart(2, "0")}`, i)));
    h.push(...Array.from({ length: 7 }, (_, i) => history(`2026-${String(i + 1).padStart(2, "0")}`)));
    const d = periodsDashboard(s, h, emptyReportChoice, [null, null], 2026);
    expect(d.points).toHaveLength(12); expect(d.priorComplete).toEqual([12, 12]); expect(d.actualMonths).toBe(7);
    expect(d.points[0].values.previous1).toBe(0); expect(d.points[7].values.actual).toBeNull(); expect(d.points[9].values.actual).toBeNull();
    expect(d.points[8].values.ENSEMBLE).toBeNull(); expect(d.points[9].values.ENSEMBLE).toBe(122); expect(d.points[11].values.ENSEMBLE).toBe(124);
    expect(d.points.every(p => p.values.TOWELL === null)).toBe(true); expect(d.matrix.periods).toHaveLength(12); expect(JSON.stringify(s)).toBe(before);
  });
  it("flags partial scope coverage and never double-counts parents or invents missing years", () => {
    const s = snapshot(); s.products.push({ ...product, id: "missing" }); s.chains.push({ ...chain, id: "parent" }); s.chains[0].parentId = "parent";
    const d = periodsDashboard(s, [history("2026-01"), history("2026-01", 9999, { product_id: "parent", chain_id: "parent" })], emptyReportChoice, [null, null], 2026);
    expect(d.points[0].coverage.actual).toEqual({ value: 10, covered: 1, eligible: 2 }); expect(d.priorComplete).toEqual([0, 0]); expect(d.kpis.sales).toBe(10);
  });
  it("synchronizes exact date/product/decision filters, preserves null and aggregates paired Fill Rate", () => {
    const h = [history("2026-01", 0), history("2026-02", null, { ORDER: 100, DELIVERY: 50 }), history("2026-03", 99, { DELIVERY: null })];
    const d = periodsDashboard(snapshot(), h, emptyReportChoice, ["2026-01", "2026-02"], 2026);
    expect(d.kpis.sales).toBe(0); expect(d.kpis.fillRate).toBeCloseTo(59 / 110 * 100); expect(d.points[2].values.actual).toBeNull(); expect(d.matrix.periods).toEqual([]);
    expect(periodsDashboard(snapshot(), h, { ...emptyReportChoice, decision: "ADOPTED" }, [null, null], 2026).kpis.sales).toBeNull();
    expect(() => periodsDashboard(snapshot(), [h[0], h[0]], emptyReportChoice, [null, null], 2026)).toThrow("duplicate_dashboard_month");
  });
});
describe("Commercial filters and Periodos UI", () => {
  function mount() {
    const s = snapshot();
    const repository = { getProducts: vi.fn(async () => [product]), getCommercialCatalog: vi.fn(async () => [classification]), getVisibleChains: vi.fn(async () => s.chains), getCategories: vi.fn(async () => []), getPeriods: vi.fn(async () => []), getReportSnapshot: vi.fn(async (f: Filters) => ({ ...s, products: (await filteredProducts(repository as unknown as ForecastReadRepository, f)) })), getReportHistory: vi.fn(async () => [history("2026-01")]), previews: { create: vi.fn(), selectCalculation: vi.fn(), research: vi.fn() } };
    function Harness() { const [filters, setFilters] = useState<Filters>(emptyFilters); return <ForecastFiltersContext.Provider value={{ repository: repository as unknown as ForecastReadRepository, filters, setFilters, profile: { id: "actor", full_name: "Test", global_role: "VIEWER", status: "ACTIVE" } }}><GlobalFilters/><ForecastPeriodsView/></ForecastFiltersContext.Provider>; }
    render(<Harness/>); return repository;
  }
  it("updates chart, KPIs and matrix together without writes and retains manual series controls", async () => {
    const repo = mount(); await screen.findByRole("table", { name: "Forecast H1-H12 por producto" });
    await userEvent.click(screen.getByRole("combobox", { name: "Modelo" })); await userEvent.click(await screen.findByRole("option", { name: "FENDI (1) · Pendiente" }));
    await waitFor(() => expect(repo.getReportSnapshot).toHaveBeenLastCalledWith(expect.objectContaining({ commercialModel: "FENDI" })));
    await screen.findByRole("table", { name: "Forecast H1-H12 por producto" }); expect(screen.getByLabelText("Indicadores comerciales")).toBeTruthy(); expect(screen.getByLabelText("Gráfica comparativa comercial")).toBeTruthy();
    const graph = screen.getByLabelText("Gráfica comparativa comercial"); await userEvent.click(within(graph).getByRole("checkbox", { name: "ML 2026" })); expect((within(graph).getByRole("checkbox", { name: "ML 2026" }) as HTMLInputElement).checked).toBe(false);
    await userEvent.selectOptions(screen.getByLabelText("Producto del detalle"), "p"); expect(await screen.findByRole("table", { name: "Detalle de alternativas por producto" })).toBeTruthy();
    expect(repo.previews.create).not.toHaveBeenCalled(); expect(repo.previews.selectCalculation).not.toHaveBeenCalled(); expect(repo.previews.research).not.toHaveBeenCalled();
  });
});
