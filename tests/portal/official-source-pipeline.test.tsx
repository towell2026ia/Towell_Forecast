import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { SupabaseClient } from "@supabase/supabase-js";
import { SupabaseForecastReadRepository } from "../../lib/forecast-data";
import { addReportMonth, emptyReportChoice } from "../../lib/forecast-reporting";
import { periodsDashboard } from "../../lib/periods-dashboard";
import { operationalPeriodsSheets } from "../../lib/operational-periods-xlsx";
import { planningXlsx } from "../../lib/planning-xlsx";
import { emptyFilters, type CommercialClassification, type Filters, type Product } from "../../lib/supabase/types";
import { PeriodsComparisonChart } from "../../components/forecast/periods-comparison-chart";
import { operationalContext } from "./operational-context-fixture";

// Entirely synthetic. Exercise the normal repository and report transforms;
// no fixture contains a private workbook, hosted connection or credential.
type RecordRow = Record<string, unknown>;
type Call = { name: string; args: RecordRow };
function sourceClient(tables: Record<string, RecordRow[]>) {
  const calls: Call[] = [];
  const query = (name: string, args: RecordRow = {}, options: RecordRow = {}) => {
    const tests: ((row: RecordRow) => boolean)[] = [];
    let start = 0, end = Infinity;
    const q: Record<string, unknown> = {};
    for (const method of ["select", "order", "abortSignal"]) q[method] = () => q;
    q.eq = (field: string, value: unknown) => { tests.push(row => row[field] === value); return q; };
    q.in = (field: string, values: unknown[]) => { tests.push(row => values.includes(row[field])); return q; };
    q.gte = (field: string, value: string) => { tests.push(row => String(row[field]) >= value); return q; };
    q.lte = (field: string, value: string) => { tests.push(row => String(row[field]) <= value); return q; };
    q.range = (from: number, to: number) => { start = from; end = to + 1; return q; };
    q.limit = (count: number) => { end = count; return q; };
    q.then = (resolve: (value: unknown) => unknown) => {
      const rows = name === "operational_read_context" ? [operationalContext] : (tables[name] ?? []).filter(row =>
        (!args.p_chain_id || row.chain_id === args.p_chain_id)
        && (!args.p_product_ids || (args.p_product_ids as unknown[]).includes(row.product_id))
        && (!args.p_metric_codes || (args.p_metric_codes as unknown[]).includes(row.metric_code))
        && (!args.p_from || String(row.period) >= String(args.p_from))
        && (!args.p_to || String(row.period) <= String(args.p_to))
        && tests.every(test => test(row)));
      return Promise.resolve({ data: options.head ? null : rows.slice(start, end), count: rows.length, error: null }).then(resolve);
    };
    return q;
  };
  return { calls, client: {
    from: (name: string) => query(name),
    rpc: (name: string, args: RecordRow = {}, options: RecordRow = {}) => { calls.push({ name, args }); return query(name, args, options); },
  } as unknown as SupabaseClient };
}

describe("Official repository → Períodos → XLSX", () => {
  it.each([null, "AZUL"])("keeps identical source, scope and monthly quantities for color %s", async color => {
    const products: Product[] = [
      { id: "blue", chain_id: "chain", category_id: "cat", product_code: "MODEL", variant_code: null, description: "Blue", identifiers: [] },
      { id: "pink", chain_id: "chain", category_id: "cat", product_code: "MODEL", variant_code: null, description: "Pink", identifiers: [] },
      { id: "other-model", chain_id: "chain", category_id: "cat", product_code: "OTHER", variant_code: null, description: "Other", identifiers: [] },
      { id: "other-chain", chain_id: "foreign", category_id: "cat", product_code: "MODEL", variant_code: null, description: "Foreign", identifiers: [] },
      { id: "previous-source", chain_id: "chain", category_id: "cat", product_code: "OLD", variant_code: null, description: "Earlier source only", identifiers: [] },
    ];
    const classes: CommercialClassification[] = products.filter(p => p.id !== "previous-source").map(product => ({ product_id: product.id, chain_id: product.chain_id,
      model: product.id === "other-model" ? "OTHER" : "MODEL", color: product.id === "blue" ? "AZUL" : "ROSA", size: "MEDIO BAÑO", product_type: "TOALLA",
      status: "PENDING_CLASSIFICATION", source: "OFFICIAL_WORKBOOK", reasons: [] }));
    const facts = products.filter(p => p.id !== "previous-source").flatMap(product => ["2026-09-01", "2026-10-01"].flatMap(period =>
      ["SALES", "ORDER", "DELIVERY", "FCST_TOWELL_HISTORY", "FCST_CLIENTE_HISTORY"].map((metric_code, i) => {
        const value_text = product.id === "blue" ? ["10.125", "20", "18", "0.000", "12"][i] : ["20.375", "40", "30", "24", "25"][i];
        return { observation_id: `${product.id}/${period}/${metric_code}`, chain_id: product.chain_id, product_id: product.id, period, metric_code,
          value: Number(value_text), value_text, chain_name: product.chain_id, availability_source: "UNKNOWN", version_no: 1 };
      })));
    const calculations = products.slice(0, 2).map((product, i) => ({ id: `calc-${product.id}`, chain_id: product.chain_id, product_id: product.id,
      objective: "Venta", calculation_no: 1, calculation_code: "C1", issue_period: "2026-10", created_at: "2026-11-05T12:00:00Z", status: "READY_FOR_DECISION",
      candidate_metrics: {}, input_snapshot: { source_context: operationalContext }, engine_version: "synthetic", evidence_mode: "RETROSPECTIVE_TRAINING", value: 31 + i * 10 }));
    const inventory = products.flatMap(product => ["2026-09-01", "2026-10-01"].map(period => ({ chain_id: product.chain_id, product_id: product.id,
      period, format: "BD", upc: product.id, purchase_type: "RS", quantity_text: period === "2026-09-01" ? "0.000" : product.id === "blue" ? "100.2500" : "200.7500" })));
    const mock = sourceClient({ products, chains: [{ id: "chain", code: "C", name: "Chain", status: "ACTIVE" }, { id: "foreign", code: "F", name: "Foreign", status: "ACTIVE" }],
      categories: [], import_profile_versions: [], operational_read_observations: facts, operational_read_inventory: inventory,
      forecast_calculations: calculations,
      forecast_calculation_horizons: calculations.flatMap(calculation => Array.from({ length: 12 }, (_, i) => ({
        id: `${calculation.id}/${i}`, calculation_id: calculation.id, chain_id: calculation.chain_id, product_id: calculation.product_id,
        horizon: i + 1, target_period: addReportMonth("2026-10", i + 1), statistical_value: calculation.value, ml_value: null, ensemble_value: null,
      }))),
    });
    const repository = new SupabaseForecastReadRepository(mock.client, "synthetic-user");
    vi.spyOn(repository.previews, "commercialCatalog").mockResolvedValue(classes);
    vi.spyOn(repository.previews, "vintageCapabilities").mockResolvedValue({ vintage_persistence: false, official_publication: false, champion_publication: false, forecast_calculation_history: true });
    vi.spyOn(repository.previews, "provisionalForecasts").mockResolvedValue([]);
    const filters: Filters = { ...emptyFilters, chainId: "chain", commercialModels: ["MODEL"], commercialColor: color, periodRange: ["2026-09", "2026-11"] };
    const context = await repository.openOperationalRead();
    const [snapshot, history, stock] = await Promise.all([
      repository.getReportSnapshot(filters, context), repository.getReportHistory({ ...filters, periodRange: [null, null] }, context), repository.getReportInventory(filters, context),
    ]);
    const dashboard = periodsDashboard(snapshot, history, emptyReportChoice, filters.periodRange, stock);
    const selected = color ? ["blue"] : ["blue", "pink"];
    const sales = color ? "10.125" : "30.500", inventoryTotal = color ? "100.2500" : "301.0000", forecast = color ? 31 : 72;
    expect(snapshot.products.map(row => row.id)).toEqual(selected);
    expect(history).toHaveLength(selected.length * 2);
    expect(dashboard.matrix.rows.map(row => row.product.id)).toEqual(selected);
    expect(dashboard.points[8].period).toBe("2026-10");
    expect(dashboard.points[8].coverage.actual.exact).toBe(sales);
    expect(dashboard.points[8].coverage.inventory.exact).toBe(inventoryTotal);
    expect(dashboard.points[9].values.STATISTICAL).toBe(forecast);
    expect(dashboard.matrix.rows.reduce((sum, row) => sum + row.values["2026-11"].STATISTICAL!, 0)).toBe(forecast);
    const sheets = operationalPeriodsSheets(snapshot, dashboard, history, stock);
    const comparison = sheets.find(sheet => sheet.name === "Comparativa")!;
    expect(comparison.rows[8][comparison.headers.indexOf("Venta real")]).toBe(sales);
    expect(comparison.rows[8][comparison.headers.indexOf("Inventario reportado")]).toBe(inventoryTotal);
    expect(comparison.rows[9][comparison.headers.indexOf("Forecast Estadístico")]).toBe(forecast);
    expect(sheets.find(sheet => sheet.name === "Trazabilidad")!.rows).toContainEqual(["Fuente oficial SHA-256", context.source_hash]);
    expect(sheets.find(sheet => sheet.name === "Inventario")!.rows).toHaveLength(selected.length * 2);
    // The browser's real serializer is stored ZIP/OOXML. Inspect its worksheet
    // payload too, rather than stopping at the pre-export JavaScript rows.
    const worksheets = new TextDecoder().decode(planningXlsx(sheets)).match(/<worksheet[\s\S]*?<\/worksheet>/g)!;
    const comparisonXml = new DOMParser().parseFromString(worksheets[1], "application/xml");
    const exportedCell = (label: string, row: number) => {
      let index = comparison.headers.indexOf(label) + 1, column = "";
      while (index) { column = String.fromCharCode(65 + (index - 1) % 26) + column; index = Math.floor((index - 1) / 26); }
      return comparisonXml.querySelector(`c[r="${column}${row + 5}"]`)?.textContent;
    };
    expect(exportedCell("Venta real", 8)).toBe(sales);
    expect(exportedCell("Inventario reportado", 8)).toBe(inventoryTotal);
    expect(exportedCell("Forecast Estadístico", 9)).toBe(String(forecast));
    render(<PeriodsComparisonChart points={dashboard.points} cutoff={dashboard.cutoff}/>);
    const october = within(screen.getByRole("table", { name: "Datos de la gráfica 9+3" })).getByText("2026-10").closest("tr")!;
    expect(within(october).getByText(sales)).toBeTruthy();
    expect(within(october).getByText(inventoryTotal)).toBeTruthy();
    for (const call of mock.calls.filter(call => call.name !== "operational_read_context")) {
      expect(call.args).toMatchObject({ p_source_hash: context.source_hash, p_generation: context.generation });
      if (call.args.p_product_ids) expect(call.args.p_product_ids).toEqual(selected);
    }
    expect(mock.calls.some(call => /operational_data_version|manifest/i.test(call.name))).toBe(false);
    repository.dispose();
  });
});
