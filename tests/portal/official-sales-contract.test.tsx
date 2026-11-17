import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { SupabaseClient } from "@supabase/supabase-js";
import { SupabaseForecastReadRepository } from "../../lib/forecast-data";
import { buildForecastMatrix, emptyReportChoice, matchesOfficialSource, type ReportSnapshot } from "../../lib/forecast-reporting";
import { periodsDashboard } from "../../lib/periods-dashboard";
import { operationalPeriodsSheets } from "../../lib/operational-periods-xlsx";
import { salesSourceState, salesUnavailableMessage } from "../../lib/official-sales-source";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { ForecastPeriodsView } from "../../app/forecast-periods-view";
import { emptyFilters, type HistoricalRow, type OperationalReadContext } from "../../lib/supabase/types";

const source: OperationalReadContext = { source_hash: "c".repeat(64), authorized_source_hash: "c".repeat(64), generation: 2,
  first_period: "2024-01", last_period: "2026-10", sales_source: "TOWELL", inventory_source: "ESTADISTICA", sales_available: true,
  sales_contract: "towell-sales-v1", sales_file_sha256: "d".repeat(64), sales_sheet: "Towell" };
const missing = { ...source, sales_available: false };
const oldSource = { source_hash: "b".repeat(64), generation: 1 };
const filters = { ...emptyFilters, chainId: "chain", purchaseType: "RS" as const };
const product = { id: "product", chain_id: "chain", category_id: null, product_code: "P", variant_code: null, description: "Synthetic variant", identifiers: [] };
const classification = { product_id: product.id, chain_id: "chain", model: "DESIGN", color: "BLUE", size: "MB", product_type: "TOALLA", purchase_type: "RS" as const, status: "PENDING_CLASSIFICATION" as const, source: "SYNTHETIC", reasons: [] };
const stock = { chain_id: "chain", product_id: product.id, period: "2026-10-01", format: "SYNTHETIC", upc: "SYNTHETIC_UPC", purchase_type: "RS", quantity_text: "123.4500" };

function clientFixture() {
  let context = source;
  const calls: string[] = [];
  const tables: Record<string, Record<string, unknown>[]> = { products: [product], chains: [{ id: "chain", code: "C", name: "Synthetic chain", status: "ACTIVE" }], categories: [], import_profile_versions: [],
    operational_read_observations: [{ observation_id: "towell-sale", chain_id: "chain", product_id: product.id, period: "2026-10-01", metric_code: "SALES", value: 17.125, value_text: "17.125", chain_name: "Synthetic chain", availability_source: "UNKNOWN", version_no: 1 }],
    operational_read_inventory: [stock], forecast_calculations: [], forecast_calculation_horizons: [] };
  const query = (name: string, args: Record<string, unknown> = {}, options: Record<string, unknown> = {}) => {
    calls.push(name);
    const predicates: ((row: Record<string, unknown>) => boolean)[] = [];
    let start = 0, end = Infinity;
    const q: Record<string, unknown> = {};
    for (const method of ["select", "order", "abortSignal"]) q[method] = () => q;
    q.eq = (field: string, value: unknown) => { predicates.push(row => row[field] === value); return q; };
    q.in = (field: string, values: unknown[]) => { predicates.push(row => values.includes(row[field])); return q; };
    q.gte = (field: string, value: string) => { predicates.push(row => String(row[field]) >= value); return q; };
    q.lte = (field: string, value: string) => { predicates.push(row => String(row[field]) <= value); return q; };
    q.range = (from: number, to: number) => { start = from; end = to + 1; return q; };
    q.limit = (count: number) => { end = count; return q; };
    q.then = (resolve: (value: unknown) => unknown) => {
      const rows = name === "operational_read_context" ? [context] : (tables[name] ?? []).filter(row =>
        (!args.p_chain_id || row.chain_id === args.p_chain_id) && (!args.p_product_ids || (args.p_product_ids as unknown[]).includes(row.product_id)) && predicates.every(test => test(row)));
      return Promise.resolve({ data: options.head ? null : rows.slice(start, end), count: rows.length, error: null }).then(resolve);
    };
    return q;
  };
  const sdk = { from: (name: string) => query(name), rpc: query } as unknown as SupabaseClient;
  const repository = new SupabaseForecastReadRepository(sdk, "synthetic");
  const catalog = vi.spyOn(repository.previews, "commercialCatalog").mockResolvedValue([classification]);
  vi.spyOn(repository.previews, "vintageCapabilities").mockResolvedValue({ vintage_persistence: false, official_publication: false, champion_publication: false, forecast_calculation_history: true });
  const provisional = vi.spyOn(repository.previews, "provisionalForecasts").mockResolvedValue([]);
  return { repository, tables, calls, catalog, provisional, context: (next: OperationalReadContext) => { context = next; } };
}

function snapshot(context: OperationalReadContext): ReportSnapshot {
  const old = { id: "old-calc", chain_id: "chain", product_id: product.id, objective: "Venta", calculation_no: 1, calculation_code: "C1", issue_period: "2026-10", status: "READY_FOR_DECISION", created_at: "2026-11-01T00:00:00Z", created_by: "synthetic", engine_version: "SYNTHETIC", evidence_mode: "RETROSPECTIVE_TRAINING", input_snapshot: { source_context: oldSource }, suggested_reference: null, recalculation_reason: null, candidate_metrics: {}, research_snapshot: null, data_snapshot_hash: "synthetic", preview_id: "old-preview", git_sha: "synthetic" };
  return { products: [{ ...product, commercial: classification }], chains: [{ id: "chain", code: "C", name: "Synthetic chain", status: "ACTIVE", has_history: true, parentId: null, scopeType: null }], categories: [],
    calculations: [old], horizons: [], decisions: [], live: [], provisionals: [], customers: [], sourceContext: context,
    capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false, forecast_calculation_history: true } };
}

describe("Towell sales / Estadística inventory source boundary", () => {
  it("requires the complete declared contract and its authorized fingerprint without altering legacy hosted contexts", () => {
    expect(salesSourceState(source)).toBe("AVAILABLE"); expect(salesSourceState(missing)).toBe("UNAVAILABLE");
    for (const invalid of [{ ...source, sales_sheet: "BAASE" }, { ...source, authorized_source_hash: oldSource.source_hash }, { ...source, sales_file_sha256: "" }, { ...source, sales_contract: "unknown" }])
      expect(salesSourceState(invalid as OperationalReadContext)).toBe("INVALID");
    expect(salesSourceState({ source_hash: "a".repeat(64), generation: 1 })).toBe("AVAILABLE");
    expect(matchesOfficialSource(source, missing)).toBe(false);
  });
  it("invalidates cached source metadata and never queries sales or prior calculations when the sales file is unavailable", async () => {
    const f = clientFixture(); await f.repository.getCommercialCatalog(source);
    const before = f.catalog.mock.calls.length;
    f.context(missing);
    await f.repository.getCommercialCatalog(missing);
    expect(f.catalog.mock.calls.length).toBe(before + 1);
    f.calls.length = 0;
    const [report, history, inventory, chains] = await Promise.all([f.repository.getReportSnapshot(filters, missing), f.repository.getReportHistory(filters, missing), f.repository.getReportInventory(filters, missing), f.repository.getVisibleChains(missing)]);
    expect(report.calculations).toEqual([]); expect(report.horizons).toEqual([]); expect(history).toEqual([]);
    expect(inventory).toEqual([stock]); expect(chains).toMatchObject([{ has_history: false, has_inventory: true }]);
    expect(await f.repository.previews.latest("chain", null, missing)).toBeNull();
    expect(f.calls).not.toContain("forecast_calculations"); expect(f.calls).not.toContain("operational_read_observations");
    expect(f.provisional).not.toHaveBeenCalled();
    f.repository.dispose();
  });
  it("keeps old generation-one results out of the matrix and export while allowing inventory from the pinned new source", () => {
    expect(buildForecastMatrix(snapshot(source), emptyReportChoice, [null, null]).rows[0].calculation).toBeNull();
    const report = snapshot(missing);
    const history: HistoricalRow[] = [{ ...filters, chain_id: "chain", product_id: product.id, product, chain: "Synthetic chain", period: "2026-10-01", SALES: 999999, ORDER: null, DELIVERY: null, availability: "UNKNOWN" }];
    const dashboard = periodsDashboard(report, history, emptyReportChoice, [null, null], [stock]);
    expect(buildForecastMatrix(report, emptyReportChoice, [null, null]).rows[0].calculation).toBeNull();
    expect(dashboard.available).toBe(0); expect(dashboard.points.every(point => point.values.actual === null && point.values.ENSEMBLE === null)).toBe(true);
    expect(dashboard.points[8].coverage.inventory.exact).toBe("123.4500");
    const sheets = operationalPeriodsSheets(report, dashboard, history, [stock]);
    expect(sheets.find(sheet => sheet.name === "Histórico comercial")!.rows).toEqual([]);
    expect(sheets.find(sheet => sheet.name === "Inventario")!.rows).toHaveLength(1);
    expect(sheets.find(sheet => sheet.name === "Trazabilidad")!.rows).toContainEqual(["Ventas disponibles", salesUnavailableMessage]);
    expect(() => operationalPeriodsSheets({ ...report, sourceContext: source }, dashboard, [], [stock])).toThrow("official_source_changed");
  });
  it("shows the explicit missing-sales state in Períodos without an automatic calculation or old forecast", async () => {
    const f = clientFixture(); f.context(missing);
    render(<ForecastFiltersContext.Provider value={{ repository: f.repository, filters, setFilters: vi.fn(), profile: { id: "synthetic", full_name: "Synthetic", global_role: "VIEWER", status: "ACTIVE" } }}><ForecastPeriodsView/></ForecastFiltersContext.Provider>);
    expect(await screen.findByText(salesUnavailableMessage)).toBeTruthy();
    expect(screen.getByText(/Sin cálculo compatible disponible para los periodos seleccionados/)).toBeTruthy();
    expect(f.calls).not.toContain("forecast_calculations"); expect(f.calls).not.toContain("operational_read_observations");
  });
});
