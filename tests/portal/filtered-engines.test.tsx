import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { ForecastEnginesView } from "../../app/operational-engines-view";
import { ForecastPeriodsView } from "../../app/forecast-periods-view";
import { scopeHorizons, storedCommercialScope } from "../../lib/forecast-chart-data";
import { addReportMonth, emptyReportChoice, type ReportSnapshot } from "../../lib/forecast-reporting";
import { periodsDashboard } from "../../lib/periods-dashboard";
import { operationalPeriodsSheets } from "../../lib/operational-periods-xlsx";
import type { PreviewJob, PreviewScope } from "../../lib/forecast-preview";
import { emptyFilters, type ForecastReadRepository } from "../../lib/supabase/types";
import { operationalContext } from "./operational-context-fixture";

const version = "synthetic-operational-preview-4-e4-snapshot";
const filters = { ...emptyFilters, chainId: "c", commercialModels: ["FENDI"] };
function fixture() {
  const products = ["blue", "red"].map(id => ({ id, chain_id: "c", category_id: "cat", product_code: id, variant_code: null, description: id, identifiers: [],
    commercial: { chain_id: "c", product_id: id, model: "FENDI", color: id, size: "MB", purchase_type: "RS" as const, product_type: "TOALLA", status: "PENDING_CLASSIFICATION" as const, source: "OFFICIAL_WORKBOOK", reasons: [] } }));
  const calculations = products.map(p => ({ id: p.id, chain_id: "c", product_id: p.id, objective: "Venta", calculation_no: 1, calculation_code: "C1", issue_period: "2026-10", status: "READY_FOR_DECISION", created_at: "2026-11-05T00:00:00Z", created_by: "actor", suggested_reference: null, recalculation_reason: null, candidate_metrics: {}, input_snapshot: { source_context: operationalContext }, research_snapshot: null, data_snapshot_hash: "hash", preview_id: "preview", engine_version: version, git_sha: "sha", evidence_mode: "RETROSPECTIVE_TRAINING" }));
  const snapshot: ReportSnapshot = { products, chains: [{ id: "c", code: "C", name: "Chain", status: "ACTIVE", has_history: true, parentId: null, scopeType: "COMMERCIAL_UNIT" }], categories: [], calculations,
    horizons: calculations.flatMap((c, index) => Array.from({ length: 12 }, (_, i) => ({ id: `${c.id}/${i}`, calculation_id: c.id, chain_id: "c", product_id: c.product_id, horizon: i + 1, target_period: addReportMonth("2026-10", i + 1), statistical_value: 10 + index * 10, ml_value: index ? null : 5, ensemble_value: 11 + index * 10, p10: null, p50: 11 + index * 10, p90: null, p95: null, band_basis: "INSUFFICIENT", band_observations: 0, statistical_model: "SBA", ml_model: index ? null : "RF" }))),
    decisions: [], live: [], provisionals: [], customers: [], sourceContext: operationalContext, capabilities: { vintage_persistence: false, champion_publication: false, official_publication: false } };
  const original: PreviewScope = { chain_id: "c", chain_name: "Chain", issue_period: "2026-10", status: "PREVIEW", source_context: operationalContext, engine_version: version, evaluation_mode: "RETROSPECTIVE_EVALUATION", eligibility: { evaluated: 3, stat_eligible: 3, ml_eligible: 3 },
    products: [...products.map(p => ({ product_id: p.id, product_code: p.id, description: p.description, category_id: "cat", forecast_status: "ACTIVE" })), { product_id: "foreign-model", product_code: "other", description: "Other model", category_id: "cat", forecast_status: "ACTIVE" }],
    statistical: { status: "COMPLETED", models: { SBA: 3 }, candidates: [], retrospective_wape: 99, retrospective_bias: 88 },
    ml: { status: "COMPLETED", training_samples: 300, training_products: 3, leader: "RF", candidates: [{ model: "RF", retrospective_wape: 77, by_product: [{ product_id: "blue", retrospective_wape: 7 }] }], retrospective_wape: 77, retrospective_bias: 66 },
    aggregates: Array.from({ length: 12 }, (_, i) => ({ level: "chain", key: "c", horizon: i + 1, target_period: addReportMonth("2026-10", i + 1), forecast_towell: 9999, statistical_value: 8888, ml_value: 7777 })),
  };
  const job: PreviewJob = { job_id: "job", status: "READY_PREVIEW", mode: "RETROSPECTIVE_TRAINING", engine_version: version, scopes: [original] };
  const repository = { openOperationalRead: vi.fn(async () => operationalContext), getReportSnapshot: vi.fn(async () => snapshot), getReportHistory: vi.fn(async () => []), getReportInventory: vi.fn(async () => []), getForecastHistory: vi.fn(async () => []), getCustomerForecast: vi.fn(async () => []),
    previews: { latest: vi.fn(async () => job), create: vi.fn(), vintageCapabilities: vi.fn(async () => ({ ...snapshot.capabilities, forecast_calculation_history: true })) } } as unknown as ForecastReadRepository;
  return { snapshot, original, job, repository };
}
function mount(repository: ForecastReadRepository, view: React.ReactNode) {
  return render(<ForecastFiltersContext.Provider value={{ repository, filters, setFilters: vi.fn(), profile: { id: "actor", full_name: "Test", global_role: "ADMIN", status: "ACTIVE" } }}>{view}</ForecastFiltersContext.Provider>);
}

describe("Commercial engine selection uses stored filtered curves", () => {
  it("sums stored variants once across an explicit chain family without merging their format identities", () => {
    const { snapshot } = fixture();
    snapshot.products = snapshot.products.map(p => p.id === "red" ? { ...p, chain_id: "c2", commercial: { ...p.commercial!, chain_id: "c2" } } : p);
    snapshot.chains = [
      { ...snapshot.chains[0], id: "parent", name: "Logical chain", scopeType: "PARENT_CHAIN", has_history: false },
      { ...snapshot.chains[0], parentId: "parent" },
      { ...snapshot.chains[0], id: "c2", name: "Second format", parentId: "parent" },
    ];
    snapshot.calculations = snapshot.calculations.map(c => c.product_id === "red" ? { ...c, chain_id: "c2" } : c);
    snapshot.horizons = snapshot.horizons.map(h => h.product_id === "red" ? { ...h, chain_id: "c2" } : h);
    const family = { ...filters, chainId: "parent", scopeChainIds: ["c", "c2"], purchaseType: "RS" as const };
    const before = JSON.stringify(snapshot);
    const selected = storedCommercialScope(snapshot, family), horizons = scopeHorizons(selected, family);
    expect(horizons).toHaveLength(12);
    expect(horizons.every(h => h.statistical_value === 30 && h.ml_value === 5 && h.forecast_towell === 32)).toBe(true);
    expect(selected.eligibility).toMatchObject({ evaluated: 2, stat_eligible: 2, ml_eligible: 1 });
    expect(new Set(selected.products?.map(p => p.product_id))).toEqual(new Set(["blue", "red"]));
    expect(selected.statistical?.retrospective_wape).toBeNull();
    expect(scopeHorizons(selected, { ...family, scopeChainIds: ["c"] })).toEqual([]);
    const dashboard = periodsDashboard(snapshot, [], emptyReportChoice, [null, null]);
    expect(dashboard.points[9].values.STATISTICAL).toBe(horizons[0].statistical_value);
    expect(dashboard.points[9].coverage.STATISTICAL).toMatchObject({ covered: 2, eligible: 2 });
    expect(dashboard.coverage).toMatchObject([{ chainId: "c", total: 1 }, { chainId: "c2", total: 1 }]);
    expect(JSON.stringify(snapshot)).toBe(before);
  });
  it("rejects foreign scope, incompatible product and CE rows rather than borrowing an RS family total", () => {
    const { snapshot } = fixture();
    const family = { ...filters, chainId: "parent", scopeChainIds: ["c", "c2"], purchaseType: "RS" as const };
    const foreign = { ...snapshot, products: snapshot.products.map(p => p.id === "red" ? { ...p, chain_id: "foreign", commercial: { ...p.commercial!, chain_id: "foreign" } } : p) };
    expect(() => storedCommercialScope(foreign, family)).toThrow("stored_scope_mismatch");
    expect(() => storedCommercialScope(snapshot, { ...family, productId: "blue" })).toThrow("stored_scope_mismatch");
    const ce = { ...snapshot, products: snapshot.products.map(p => p.id === "red" ? { ...p, commercial: { ...p.commercial!, purchase_type: "CE" as const } } : p) };
    expect(() => storedCommercialScope(ce, family)).toThrow("stored_scope_mismatch");
    expect(() => storedCommercialScope(snapshot, { ...family, purchaseType: "CE" })).toThrow("stored_scope_mismatch");
  });
  it("projects exact products, preserves missing ML and never borrows aggregate WAPE", () => {
    const { snapshot, original } = fixture(), before = JSON.stringify({ snapshot, original });
    expect(scopeHorizons(original, filters)).toEqual([]);
    const selected = storedCommercialScope(snapshot, filters, original), h = scopeHorizons(selected, filters);
    expect(h).toHaveLength(12); expect(h[0]).toMatchObject({ statistical_value: 30, ml_value: 5, forecast_towell: 32 });
    expect(selected.eligibility).toMatchObject({ evaluated: 2, stat_eligible: 2, ml_eligible: 1 });
    expect(selected.products?.find(p => p.product_id === "red")?.horizons?.every(row => row.ml_value === null)).toBe(true);
    expect(selected.statistical?.retrospective_wape).toBeNull();
    // A broader preview's blue WAPE is not an archived calculation metric.
    expect(selected.ml?.candidates).toEqual([]);
    expect(scopeHorizons(selected, { ...filters, commercialModels: ["OTHER"] })).toEqual([]);
    expect(JSON.stringify({ snapshot, original })).toBe(before);
  });
  it("rejects a missing stored median and preserves the original state of an uncalculated variant", () => {
    const { snapshot, original } = fixture();
    const malformed = { ...snapshot, horizons: snapshot.horizons.map((h, i) => i ? h : { ...h, p50: null }) };
    expect(() => storedCommercialScope(malformed as unknown as ReportSnapshot, filters, original)).toThrow("stored_scope_horizon_incomplete");
    snapshot.calculations = snapshot.calculations.filter(row => row.product_id === "blue");
    snapshot.horizons = snapshot.horizons.filter(row => row.product_id === "blue");
    original.products!.find(row => row.product_id === "red")!.forecast_status = "PRE_LAUNCH";
    const selected = storedCommercialScope(snapshot, filters, original);
    expect(selected.products?.find(row => row.product_id === "red")).toMatchObject({ forecast_status: "PRE_LAUNCH", horizons: [] });
    expect(selected.eligibility).toMatchObject({ PRE_LAUNCH: 1, stat_eligible: 1 });
  });
  it("renders the same aggregate in summary, statistical and ML tabs without executing", async () => {
    const { repository } = fixture(); mount(repository, <ForecastEnginesView scope="Chain"/>);
    await screen.findByText(/Consulta almacenada · 2 productos/);
    const summary = document.getElementById("engine-panel-summary")!;
    expect(within(summary).getByText("2 de 2 · 100.0%")).toBeTruthy();
    expect(within(summary).getByText("1 de 2 · 50.0%")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Calcular vista previa" }).hasAttribute("disabled")).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Calcular vista previa" }));
    await userEvent.click(screen.getByRole("tab", { name: "Motor Estadístico" }));
    const stat = document.getElementById("engine-panel-statistical")!;
    expect(within(stat).getByText(/Cobertura y distribución del alcance comercial/)).toBeTruthy();
    expect(within(stat).queryByText("99%")).toBeNull();
    expect(within(stat).getAllByText("30").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("tab", { name: "Machine Learning" }));
    const ml = document.getElementById("engine-panel-ml")!;
    expect(within(ml).getByText(/ML 1\/2 variantes · TOTAL PARCIAL/)).toBeTruthy();
    expect(within(ml).getAllByText("5").length).toBeGreaterThan(0);
    expect(within(ml).queryByText("77%")).toBeNull();
    expect(repository.previews!.create).not.toHaveBeenCalled();
    expect(repository.getReportSnapshot).toHaveBeenCalledWith(filters, operationalContext);
  });
  it("fails closed instead of substituting the full chain when the filtered read fails", async () => {
    const { repository } = fixture(); vi.mocked(repository.getReportSnapshot!).mockRejectedValue(new Error("read_failed"));
    mount(repository, <ForecastEnginesView scope="Chain"/>);
    expect((await screen.findByRole("alert")).textContent).toContain("No se sustituyen por el total de la cadena");
    expect(document.getElementById("engine-panel-summary")!.textContent).not.toContain("3 de 3");
    expect(repository.previews!.create).not.toHaveBeenCalled();
  });
  it("labels partial ML visibly in the compact matrix and chart legend even when every product has a calculation", async () => {
    const { repository } = fixture(); mount(repository, <ForecastPeriodsView/>);
    const table = await screen.findByRole("table", { name: "Matriz compacta de forecast" });
    const row = within(table).getByText("Nov-26").closest("tr")!;
    const cells = within(row).getAllByRole("cell");
    expect(cells[3].textContent).toContain("PARCIAL 1/2");
    expect(cells[2].textContent).not.toContain("PARCIAL");
    expect(screen.getAllByText("PARCIAL 1/2").length).toBeGreaterThan(12);
  });
  it("formats floating-point aggregate tails without changing data, export quantities, zero or missing values", async () => {
    const { repository, snapshot } = fixture();
    snapshot.horizons = snapshot.horizons.map(h => ({ ...h, statistical_value: h.product_id === "blue" ? 10002.74 : 47125.67,
      ml_value: h.product_id === "blue" ? 0 : null, ensemble_value: null }));
    const original = JSON.stringify(snapshot);
    const sheets = () => operationalPeriodsSheets(snapshot, periodsDashboard(snapshot, [], emptyReportChoice, [null, null]), [], []);
    const exportBefore = JSON.stringify(sheets());
    const comparative = sheets().find(s => s.name === "Comparativa")!;
    expect(comparative.rows.find(row => row[0] === "2026-11")![comparative.headers.indexOf("Forecast Estadístico")]).toBe(10002.74 + 47125.67);
    mount(repository, <ForecastPeriodsView/>);
    const table = await screen.findByRole("table", { name: "Matriz compacta de forecast" });
    const cells = within(within(table).getByText("Nov-26").closest("tr")!).getAllByRole("cell");
    expect(cells[2].textContent).toBe("57,128.41");
    expect(cells[3].textContent).toBe("0PARCIAL 1/2");
    expect(cells[4].textContent).toBe("Sin dato0/2");
    expect(JSON.stringify(snapshot)).toBe(original);
    expect(JSON.stringify(sheets())).toBe(exportBefore);
    expect(repository.previews!.create).not.toHaveBeenCalled();
  });
});
