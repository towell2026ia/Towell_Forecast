import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { OperationalQualityView } from "../../app/operational-quality-view";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { addReportMonth, type ReportCalculation, type ReportSnapshot } from "../../lib/forecast-reporting";
import { emptyFilters, type ForecastReadRepository, type OperationalReadContext } from "../../lib/supabase/types";

// Synthetic records only; never imported by the portal or commercial exports.
const source: OperationalReadContext = { source_hash: "a".repeat(64), generation: 1, first_period: "2025-01", last_period: "2026-10" };
function fixture(): ReportSnapshot {
  const products = ["p1", "p2"].map(id => ({ id, chain_id: "chain", category_id: null, product_code: id, variant_code: null,
    description: id, identifiers: [], commercial: { product_id: id, chain_id: "chain", model: "MODELO", product_type: "TOALLA", color: id, size: null,
      status: "PENDING_CLASSIFICATION" as const, source: "synthetic", reasons: [] } }));
  const calculations = products.map(product => ({ id: product.id, chain_id: "chain", product_id: product.id, objective: "Venta", calculation_no: 1,
    calculation_code: "C1", issue_period: "2026-10-01", status: "READY_FOR_DECISION", created_at: "2026-11-11T00:00:00Z", created_by: "actor",
    suggested_reference: null, recalculation_reason: null, candidate_metrics: {}, input_snapshot: { source_context: source }, research_snapshot: null,
    data_snapshot_hash: "hash", preview_id: "preview", engine_version: "test", git_sha: "test", evidence_mode: "RETROSPECTIVE_TRAINING" }) as ReportCalculation);
  return { products, chains: [{ id: "chain", name: "Cadena", code: "C", parentId: null, scopeType: "CHAIN", has_history: true, status: "ACTIVE" }],
    categories: [], calculations, horizons: products.flatMap(product => Array.from({ length: 12 }, (_, n) => ({ id: product.id + n,
      calculation_id: product.id, chain_id: "chain", product_id: product.id, horizon: n + 1, target_period: addReportMonth("2026-10", n + 1),
      statistical_value: 10, ml_value: product.id === "p1" ? 11 : null, ensemble_value: 12, p10: null, p50: 12, p90: null, p95: null,
      statistical_model: "SBA", ml_model: null, band_basis: "INSUFFICIENT", band_observations: 0 }))),
    decisions: [], live: [], provisionals: [], customers: [], sourceContext: source,
    capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false } };
}
function mount(snapshot = fixture(), changedSource = false, missingSource = false) {
  const filters = { ...emptyFilters, chainId: "chain", commercialModels: ["MODELO"] };
  const repository = {
    openOperationalRead: vi.fn(async () => changedSource ? { ...source, generation: 2 } : source),
    getReportSnapshot: vi.fn(async () => snapshot),
    getHistoricalSummary: vi.fn(async () => ({ productCount: 2, scopeCount: 1, observationCount: 12, latestPeriod: "2026-10", byScope: [] })),
    previews: { qualityGates: vi.fn(async () => ({ chain_id: "chain", preview_id: "preview", dataset_hash: "hash", policy_version: "POLICY",
      data_quality: { status: "SCOPE_DATA_WARNING" }, forecast_quality: { status: "PROVISIONAL" }, publication: "PROVISIONAL" })) },
  };
  render(<ForecastFiltersContext.Provider value={{ repository: repository as unknown as ForecastReadRepository, filters, setFilters: vi.fn(),
    pinOperational: async () => missingSource ? undefined : source, profile: { id: "local", full_name: "Test", global_role: "VIEWER", status: "ACTIVE" } }}><OperationalQualityView/></ForecastFiltersContext.Provider>);
  return { repository, filters };
}
describe("Quality reads current stored data with the same commercial scope", () => {
  it("reports partial ML independently, keeps pending classifications, and never labels scope evaluation as model WAPE", async () => {
    const { repository, filters } = mount();
    const motor = await screen.findByRole("cell", { name: "Machine Learning" });
    expect(within(motor.closest("tr")!).getByText("1 / 2")).toBeTruthy();
    expect(within(motor.closest("tr")!).getByText("Cobertura parcial")).toBeTruthy();
    expect(screen.getByText("Clasificaciones pendientes")).toBeTruthy();
    expect(screen.getByText(/no se presenta como WAPE del modelo filtrado/)).toBeTruthy();
    expect(screen.getByText(/Corte de resultados 2026-10/)).toBeTruthy();
    expect(repository.getReportSnapshot).toHaveBeenCalledWith(filters, source);
    expect(repository.getHistoricalSummary).toHaveBeenCalledWith(filters, source);
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("does not reuse an older source calculation even if its version number is higher", async () => {
    const data = fixture();
    data.calculations = data.calculations.map(row => ({ ...row, calculation_no: 99, issue_period: "2026-07-01", input_snapshot: { source_context: { ...source, source_hash: "b".repeat(64) } } }));
    mount(data);
    await screen.findByText(/Corte de resultados Sin cálculo compatible/);
    expect(screen.getAllByText("0 / 2")).toHaveLength(3);
    expect(screen.queryByText(/Corte de resultados 2026-07/)).toBeNull();
  });
  it("fails closed if the source changes while quality and coverage are being read", async () => {
    mount(fixture(), true);
    expect((await screen.findByRole("alert")).textContent).toContain("No fue posible comprobar una fuente coherente");
    expect(screen.queryByText("SCOPE_DATA_WARNING")).toBeNull();
  });
  it("does not read unpinned reports when no official source is available", async () => {
    const { repository } = mount(fixture(), false, true);
    await screen.findByRole("alert");
    expect(repository.getReportSnapshot).not.toHaveBeenCalled();
    expect(repository.getHistoricalSummary).not.toHaveBeenCalled();
    expect(screen.queryByText("SCOPE_DATA_WARNING")).toBeNull();
  });
  it("counts an explicit zero curve but never fills a missing twelfth horizon", async () => {
    const data = fixture();
    data.horizons = data.horizons.filter(row => !(row.product_id === "p2" && row.horizon === 12))
      .map(row => row.product_id === "p1" ? { ...row, statistical_value: 0, ensemble_value: 0, p50: 0 } : row);
    mount(data);
    for (const family of ["Estadístico", "Ensemble"]) {
      const cell = await screen.findByRole("cell", { name: family });
      expect(within(cell.closest("tr")!).getByText("1 / 2")).toBeTruthy();
      expect(within(cell.closest("tr")!).getByText("Cobertura parcial")).toBeTruthy();
    }
  });
});
