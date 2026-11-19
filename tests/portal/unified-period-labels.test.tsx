import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { ForecastPeriodsView } from "../../app/forecast-periods-view";
import { addReportMonth, type ReportSnapshot } from "../../lib/forecast-reporting";
import { unifiedMasterSha } from "../../lib/official-sales-source";
import { emptyFilters, type ForecastReadRepository, type OperationalReadContext, type Product } from "../../lib/supabase/types";

// Entirely synthetic business fixture; only the source contract fingerprint is real.
function fixture(): ReportSnapshot {
  const sourceContext: OperationalReadContext = {
    source_hash: "b".repeat(64), authorized_source_hash: "b".repeat(64), generation: 5,
    first_period: "2024-01", last_period: "2026-12", forecast_contract: "pedido-unificado-v1",
    objective: "Pedido", target_metric: "ORDER", target_sheet: "Base_2024_2026",
    target_file_sha256: unifiedMasterSha, unit: "PIEZAS", inventory_source: "ESTADISTICA",
    target_source_available: true, training_cutoff: "2026-09", forecast_available: true,
    forecast_block_code: null, planning_start: "2027-01", planning_end: "2027-12",
  };
  const product: Product = { id: "synthetic-product", chain_id: "synthetic-chain", category_id: null,
    product_code: "000001", variant_code: null, description: "Synthetic annual product", identifiers: [["SYNTHETIC-ITEM", "000001"]],
    commercial: { product_id: "synthetic-product", chain_id: "synthetic-chain", model: "SYNTHETIC MODEL",
      color: "SYNTHETIC COLOR", size: "MEDIO BAÑO", purchase_type: "RS", product_type: "TOALLA",
      status: "PENDING_CLASSIFICATION", source: "TRANSIENT", reasons: [] } };
  return { sourceContext, products: [product], chains: [{ id: product.chain_id, code: "SYNTHETIC", name: "Synthetic Chain",
      status: "ACTIVE", has_history: true, parentId: null, scopeType: "COMMERCIAL_UNIT" }], categories: [],
    calculations: [{ id: "synthetic-calculation", chain_id: product.chain_id, product_id: product.id,
      objective: "Pedido", calculation_no: 1, calculation_code: "2027", issue_period: "2026-09",
      status: "READY_FOR_DECISION", created_at: "2026-10-01T00:00:00Z", created_by: "synthetic-actor",
      suggested_reference: null, recalculation_reason: null, candidate_metrics: {},
      input_snapshot: { source_context: sourceContext, bridge_horizons: [1, 2, 3].map(distance => ({
        target_period: addReportMonth("2026-09", distance), statistical_value: 21 + distance,
        ml_value: null, ensemble_value: 31 + distance })) }, research_snapshot: null,
      data_snapshot_hash: "synthetic-dataset", preview_id: "synthetic-preview", engine_version: "synthetic-engine",
      git_sha: "synthetic-sha", evidence_mode: "RETROSPECTIVE_TRAINING" }],
    horizons: Array.from({ length: 12 }, (_, index) => ({ id: `synthetic-horizon-${index + 1}`,
      calculation_id: "synthetic-calculation", chain_id: product.chain_id, product_id: product.id,
      horizon: index + 1, target_period: addReportMonth("2026-12", index + 1),
      statistical_value: 101 + index, ml_value: 201 + index, ensemble_value: 151 + index,
      statistical_model: "Synthetic statistical", ml_model: "Synthetic ML", p10: null, p50: null,
      p90: null, p95: null, band_basis: "INSUFFICIENT", band_observations: 0 })),
    decisions: [], live: [], provisionals: [], customers: [],
    capabilities: { vintage_persistence: false, champion_publication: false, official_publication: false } };
}

describe("G5 annual labels in the actual Períodos view", () => {
  it("labels January H1 and December H12 while retaining the September training cutoff", async () => {
    const snapshot = fixture();
    const source = snapshot.sourceContext!;
    const repository = {
      openOperationalRead: vi.fn(async () => source), getReportSnapshot: vi.fn(async () => snapshot),
      getReportHistory: vi.fn(async () => []), getReportInventory: vi.fn(async () => []),
      previews: { create: vi.fn(), selectCalculation: vi.fn() },
    };
    render(<ForecastFiltersContext.Provider value={{ repository: repository as unknown as ForecastReadRepository,
      filters: emptyFilters, setFilters: vi.fn(), profile: { id: "synthetic-actor", full_name: "Synthetic viewer",
        global_role: "VIEWER", status: "ACTIVE" } }}><ForecastPeriodsView/></ForecastFiltersContext.Provider>);
    const table = await screen.findByRole("table", { name: "Matriz compacta de forecast" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(12);
    expect(rows.map(row => within(row).getAllByRole("cell")[1].textContent)).toEqual(
      Array.from({ length: 12 }, (_, index) => `H${index + 1}`));
    const january = within(rows[0]).getAllByRole("cell");
    const december = within(rows[11]).getAllByRole("cell");
    expect(january[0].textContent).toBe("Ene-27");
    expect(january[1].textContent).toBe("H1");
    expect(december[0].textContent).toBe("Dic-27");
    expect(december[1].textContent).toBe("H12");
    expect(within(table).queryByText("H4", { selector: "td" })).not.toBeNull();
    expect(within(table).queryByText("H13")).toBeNull();
    expect(within(table).queryByText("H15")).toBeNull();
    expect(screen.getByLabelText("Alcance de Periodos").textContent).toContain("Corte 2026-09");
    expect(repository.getReportSnapshot).toHaveBeenCalledWith(emptyFilters, source);
    expect(repository.previews.create).not.toHaveBeenCalled();
    expect(repository.previews.selectCalculation).not.toHaveBeenCalled();
  });
});
