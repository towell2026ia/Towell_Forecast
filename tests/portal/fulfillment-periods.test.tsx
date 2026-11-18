import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { SupabaseClient } from "@supabase/supabase-js";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { ForecastPeriodsView } from "../../app/forecast-periods-view";
import { SupabaseReportReader } from "../../lib/forecast-report-repository";
import { addReportMonth, emptyReportChoice, matchesOfficialSource, type ReportSnapshot } from "../../lib/forecast-reporting";
import { forecastSourceState, officialSourceKey } from "../../lib/official-sales-source";
import { operationalPeriodsSheets } from "../../lib/operational-periods-xlsx";
import { periodsDashboard } from "../../lib/periods-dashboard";
import { planningXlsx } from "../../lib/planning-xlsx";
import { emptyFilters, type ForecastReadRepository, type HistoricalRow, type OperationalFulfillment, type OperationalReadContext, type Product } from "../../lib/supabase/types";

const source: OperationalReadContext = { source_hash: "a".repeat(64), authorized_source_hash: "a".repeat(64), generation: 4,
  first_period: "2024-01", last_period: "2026-10", forecast_contract: "pedido-towell-v1", objective: "Pedido", target_metric: "ORDER", target_sheet: "Pedidos",
  target_file_sha256: "b".repeat(64), training_cutoff: "2026-09", forecast_available: true, target_source_available: true, forecast_block_code: null,
  inventory_source: "ESTADISTICA", unit: "PIEZAS", fulfillment_source: "ESTADISTICA_BAASE", fulfillment_file_sha256: "c".repeat(64), fulfillment_available: true };
const products: Product[] = ["p1", "p2"].map(id => ({ id, chain_id: "chain", category_id: null, product_code: id, description: `Synthetic ${id}`, variant_code: null, identifiers: [[`00${id}`, null]] }));
function snapshot(): ReportSnapshot {
  const calculation = { id: "calculation", chain_id: "chain", product_id: "p1", objective: "Pedido", calculation_no: 1, calculation_code: "C1", issue_period: "2026-09", cutoff_at: "2026-09-30T23:59:59Z", status: "READY_FOR_DECISION", created_at: "2026-11-18T00:00:00Z", created_by: "synthetic", preview_id: "synthetic", data_snapshot_hash: "synthetic", input_snapshot: { source_context: source }, candidate_metrics: {}, research_snapshot: null, suggested_reference: null, engine_version: "synthetic-v4", git_sha: "synthetic", evidence_mode: "RETROSPECTIVE_TRAINING", recalculation_reason: null };
  return { products, chains: [{ id: "chain", code: "CHAIN", name: "Synthetic chain", status: "ACTIVE", has_history: true, parentId: null, scopeType: "COMMERCIAL_UNIT" }],
    categories: [], calculations: [calculation], horizons: Array.from({ length: 12 }, (_, i) => ({ id: `h${i}`, calculation_id: calculation.id, chain_id: "chain", product_id: "p1", horizon: i + 1,
      target_period: addReportMonth("2026-09", i + 1), statistical_value: 10, ml_value: null, ensemble_value: 11, p10: null, p50: 11, p90: null, p95: null,
      band_basis: "INSUFFICIENT", band_observations: 0, statistical_model: "Synthetic method", ml_model: null })),
    decisions: [], live: [], provisionals: [], customers: [], sourceContext: source, capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false } };
}
function history(product: Product, order: number): HistoricalRow {
  return { chain_id: "chain", product_id: product.id, product, chain: "Synthetic chain", period: "2026-09", SALES: null, ORDER: order, DELIVERY: order,
    exact: { ORDER: String(order), DELIVERY: String(order) }, availability: "UNKNOWN" };
}
function pair(productId: string, period: string, ordered: string | null, delivered: string | null): OperationalFulfillment {
  return { chain_id: "chain", product_id: productId, period, format: "FORMAT", upc: `00${productId}`, purchase_type: "RS", order_text: ordered, delivery_text: delivered,
    source_reference: `BAASE!${productId}/${period}`, order_source_reference: ordered === null ? null : "BAASE!ORDER", delivery_source_reference: delivered === null ? null : "BAASE!DELIVERY",
    source_sha256: source.fulfillment_file_sha256!, partial_period: period.slice(0, 7) > "2026-09" };
}
const historical = [...products.map((product, i) => history(product, (i + 1) * 1000)), { ...history(products[0], 7000), period: "2026-10" }];
const fulfillment = [pair("p1", "2026-09-01", "100.000", "90.00"), pair("p2", "2026-09-01", "50", "10"),
  pair("p1", "2026-10-01", "0.000", "0"), pair("p2", "2026-10-01", "10", "0"),
  pair("p1", "2026-08-01", "5", null), pair("p2", "2026-08-01", null, "3")];

describe("BAASE fulfillment is independent of Pedido training", () => {
  it("uses exact same-product/month BAASE pairs for Fill Rate, including partial observed H1, without substituting training quantities", () => {
    const before = JSON.stringify({ historical, fulfillment });
    const dashboard = periodsDashboard(snapshot(), historical, emptyReportChoice, [null, null], [], fulfillment);
    expect(dashboard.cutoff).toBe("2026-09");
    expect(dashboard.points.slice(9).map(point => [point.period, point.horizon])).toEqual([["2026-10", 1], ["2026-11", 2], ["2026-12", 3]]);
    const september = dashboard.points[8], october = dashboard.points[9];
    expect(september.values.order).toBe(3000); expect(september.values.delivery).toBe(3000);
    expect(september.fillRate).toMatchObject({ orderedExact: "150.000", deliveredExact: "100.00", covered: 2, eligible: 2, source: "ESTADISTICA_BAASE", reported: true, partialPeriod: false });
    expect(september.fillRate!.value).toBeCloseTo(100 / 150 * 100);
    expect(october.values).toMatchObject({ order: 7000, delivery: null, STATISTICAL: 10, ENSEMBLE: 11 });
    expect(october.partialOrder).toBe(true); expect(october.horizon).toBe(1);
    expect(october.fillRate).toMatchObject({ value: 0, orderedExact: "10.000", deliveredExact: "0", covered: 2, eligible: 2, source: "ESTADISTICA_BAASE", reported: true, partialPeriod: true });
    expect(dashboard.points[7].fillRate).toMatchObject({ value: null, covered: 0, reported: false });
    expect(dashboard.points[10].fillRate).toMatchObject({ value: null, covered: 0, reported: false });
    expect(dashboard.kpis).toMatchObject({ orders: 3000, delivery: 3000, fillRate: 62.5, paired: 4, expectedPairs: 20,
      fillRateOrderedExact: "160.000", fillRateDeliveredExact: "100.00", fillRateSource: "ESTADISTICA_BAASE", fillRatePartialPeriod: true });
    expect(JSON.stringify({ historical, fulfillment })).toBe(before);
  });
  it("never substitutes historical Order/Delivery if BAASE is absent, preserves denominator zero and applies scope/month masks", () => {
    expect(periodsDashboard(snapshot(), historical, emptyReportChoice, [null, null]).kpis.fillRate).toBeNull();
    const zero = periodsDashboard(snapshot(), historical, emptyReportChoice, ["2026-09", "2026-10"], [], [pair("p1", "2026-10", "0.00", "0")]);
    expect(zero.points[9].fillRate).toMatchObject({ value: null, orderedExact: "0.00", deliveredExact: "0", covered: 1, reported: true });
    expect(zero.kpis.expectedPairs).toBe(4);
    const masked = periodsDashboard(snapshot(), historical, emptyReportChoice, ["2026-10", "2026-10"], [], fulfillment);
    expect(masked.points[8].fillRate!.value).toBeNull(); expect(masked.kpis.fillRate).toBe(0); expect(masked.kpis.expectedPairs).toBe(2);
    expect(masked.fulfillment).toHaveLength(2); expect(masked.fulfillment.every(row => row.period.slice(0, 7) === "2026-10")).toBe(true);
    const foreign = { ...pair("p1", "2026-09", "900000", "1"), chain_id: "other-chain" };
    expect(periodsDashboard(snapshot(), historical, emptyReportChoice, [null, null], [], [...fulfillment, foreign]).kpis.fillRate).toBe(62.5);
  });
  it("rejects duplicates, unaccredited sources and invalid quantities before producing a ratio", () => {
    for (const data of [[fulfillment[0], fulfillment[0]], [{ ...fulfillment[0], source_sha256: "d".repeat(64) }],
      [{ ...fulfillment[0], order_text: "-1" }], [{ ...fulfillment[0], order_source_reference: null }], [pair("p1", "2026-11", "1", "1")]])
      expect(() => periodsDashboard(snapshot(), historical, emptyReportChoice, [null, null], [], data)).toThrow();
    expect(() => periodsDashboard({ ...snapshot(), sourceContext: { ...source, fulfillment_available: false } }, historical, emptyReportChoice, [null, null], [], fulfillment)).toThrow("fulfillment_source_invalid");
  });
  it("exports the same percentages, exact basis, partial flag and evidence separately from the historical target", () => {
    const s = snapshot(), dashboard = periodsDashboard(s, historical, emptyReportChoice, [null, null], [], fulfillment);
    const sheets = operationalPeriodsSheets(s, dashboard, historical, []), comparison = sheets.find(sheet => sheet.name === "Comparativa")!;
    for (const point of dashboard.points) {
      const exported = comparison.rows.find(row => row[0] === point.period)!;
      expect(exported[comparison.headers.indexOf("Fill Rate (%)")]).toBe(point.fillRate!.value);
      expect(exported[comparison.headers.indexOf("Pedido comparable")]).toBe(point.fillRate!.orderedExact);
      expect(exported[comparison.headers.indexOf("Entrega comparable")]).toBe(point.fillRate!.deliveredExact);
    }
    expect(comparison.rows[9][comparison.headers.indexOf("Captura Fill Rate")]).toBe("Parcial");
    expect(comparison.rows[9][comparison.headers.indexOf("Pedido histórico")]).toBe("7000");
    expect(comparison.rows[9][comparison.headers.indexOf("Estado Pedido")]).toContain("Capturado parcial");
    expect(sheets.find(sheet => sheet.name === "Histórico comercial")!.rows[0][5]).toBe("1000");
    expect(sheets.find(sheet => sheet.name === "Cumplimiento BAASE")!.rows[0]).toContain("100.000");
    expect(sheets.find(sheet => sheet.name === "Cumplimiento BAASE")!.rows[0]).toContain("00p1");
    expect(planningXlsx(sheets).length).toBeGreaterThan(100);
    expect(() => operationalPeriodsSheets({ ...s, sourceContext: { ...source, fulfillment_file_sha256: "d".repeat(64) } }, dashboard, historical, [])).toThrow("official_source_changed");
  });
  it("reads the same pin for the existing Periodos UI and reports a fulfillment read failure without falling back", async () => {
    const repository = { openOperationalRead: vi.fn(async () => source), getReportSnapshot: vi.fn(async () => snapshot()), getReportHistory: vi.fn(async () => historical),
      getReportInventory: vi.fn(async () => []), getReportFulfillment: vi.fn(async () => fulfillment) };
    const panel = (repo: typeof repository) => <ForecastFiltersContext.Provider value={{ repository: repo as unknown as ForecastReadRepository, filters: emptyFilters,
      setFilters: vi.fn(), profile: { id: "actor", full_name: "Synthetic", global_role: "VIEWER", status: "ACTIVE" } }}><ForecastPeriodsView/></ForecastFiltersContext.Provider>;
    const view = render(panel(repository));
    await screen.findByText(/Estos pares se utilizan sólo para Fill Rate/);
    const indicators = screen.getByLabelText("Indicadores comerciales");
    expect(within(indicators).getByText("62.50%")).toBeTruthy();
    expect(repository.getReportFulfillment).toHaveBeenCalledWith(emptyFilters, source);
    expect(repository.openOperationalRead).toHaveBeenCalledOnce();
    const failed = { ...repository, getReportFulfillment: vi.fn(async (): Promise<OperationalFulfillment[]> => { throw new Error("synthetic read failure"); }) };
    view.rerender(panel(failed));
    await screen.findByText(/Error de consulta de cumplimiento BAASE/);
    expect(within(screen.getByLabelText("Indicadores comerciales")).queryByText("100.00%")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Descargar XLSX de Períodos" }));
    await screen.findByText(/La consulta de cumplimiento BAASE falló/);
  });
});

describe("Pinned fulfillment read boundary", () => {
  it("includes the accredited BAASE document in source equality and cache identity without rejecting earlier G3 contracts", () => {
    const different = { ...source, fulfillment_file_sha256: "d".repeat(64) };
    expect(matchesOfficialSource(different, source)).toBe(false);
    expect(officialSourceKey(different)).not.toBe(officialSourceKey(source));
    const legacy = { ...source }; delete legacy.fulfillment_source; delete legacy.fulfillment_file_sha256; delete legacy.fulfillment_available;
    expect(forecastSourceState(legacy)).toBe("AVAILABLE"); expect(matchesOfficialSource(legacy, source)).toBe(false);
    expect(forecastSourceState({ ...source, fulfillment_available: false })).toBe("INVALID");
    expect(forecastSourceState({ ...source, fulfillment_file_sha256: "invalid" })).toBe("INVALID");
  });
  it("paginates only the dedicated RPC and rejects a different source or a context change", async () => {
    const catalog = Array.from({ length: 50 }, (_, i) => ({ ...products[0], id: `sku${i}` }));
    const records = catalog.flatMap(product => Array.from({ length: 10 }, (_, i) => pair(product.id, `2026-${String(i + 1).padStart(2, "0")}-01`, "10.000", "9")));
    const calls: { name: string; args: unknown; start: number; ids: string[] }[] = [];
    let badSource = false, changed = false;
    const rpc = (name: string, args: unknown) => {
      let start = 0, ids: string[] = []; const query: Record<string, unknown> = {};
      for (const method of ["select", "order", "gte", "lte"]) query[method] = () => query;
      query.in = (_field: string, selected: string[]) => { ids = selected; return query; };
      query.range = (value: number) => { start = value; return query; };
      query.abortSignal = async () => {
        calls.push({ name, args, start, ids });
        return { data: start ? [] : records.map(row => badSource ? { ...row, source_sha256: "d".repeat(64) } : row), error: null };
      };
      return query;
    };
    const repository = { getProducts: vi.fn(async () => catalog), openOperationalRead: vi.fn(async () => changed ? { ...source, generation: 5 } : source) } as unknown as ForecastReadRepository;
    const reader = new SupabaseReportReader({ rpc } as unknown as SupabaseClient, repository, new AbortController().signal);
    expect(await reader.fulfillment(emptyFilters, source)).toHaveLength(500);
    expect(calls.map(call => call.start)).toEqual([0, 500]);
    for (const call of calls) {
      expect(call.name).toBe("operational_read_fulfillment");
      expect(call.args).toMatchObject({ p_source_hash: source.source_hash, p_generation: 4, p_product_ids: catalog.map(product => product.id), p_purchase_type: "RS" });
      expect(call.ids).toEqual(catalog.map(product => product.id));
    }
    badSource = true; await expect(reader.fulfillment(emptyFilters, source)).rejects.toThrow("fulfillment_lineage_invalid");
    badSource = false; changed = true; await expect(reader.fulfillment(emptyFilters, source)).rejects.toThrow("official_source_changed");
    const count = calls.length;
    expect(await reader.fulfillment(emptyFilters, { ...source, fulfillment_available: false })).toEqual([]);
    expect(calls).toHaveLength(count);
  });
});
