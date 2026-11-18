import { describe, expect, it } from "vitest";
import { addReportMonth, emptyReportChoice, type ReportSnapshot } from "../../lib/forecast-reporting";
import { periodsDashboard } from "../../lib/periods-dashboard";
import type { HistoricalRow, OperationalInventory, OperationalReadContext, Product } from "../../lib/supabase/types";

// Synthetic quantities and identities only. A captured month is not a closure.
const source: OperationalReadContext = { source_hash: "a".repeat(64), authorized_source_hash: "a".repeat(64), generation: 3,
  first_period: "2024-01", last_period: "2026-10", forecast_contract: "pedido-towell-v1", objective: "Pedido", target_metric: "ORDER",
  target_sheet: "Pedidos", target_file_sha256: "b".repeat(64), training_cutoff: null, forecast_available: false,
  target_source_available: true, forecast_block_code: "ORDER_CLOSURE_UNVERIFIED", unit: "PIEZAS", inventory_source: "ESTADISTICA" };
const products: Product[] = ["one", "two"].map(id => ({ id, chain_id: "scope", category_id: null, product_code: id, description: id,
  identifiers: [[id, null]], variant_code: null }));
function snapshot(context: OperationalReadContext | undefined = source): ReportSnapshot {
  return { products, chains: [{ id: "scope", code: "S", name: "Synthetic scope", status: "ACTIVE", parentId: null, has_history: true, scopeType: "COMMERCIAL_UNIT" }],
    categories: [], calculations: [], horizons: [], decisions: [], live: [], provisionals: [], customers: [], sourceContext: context,
    capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false } };
}
function row(product: Product, period: string, order: number, delivery: number = order / 2): HistoricalRow {
  return { chain_id: product.chain_id, product_id: product.id, product, chain: "Synthetic scope", period, SALES: order + 1, ORDER: order,
    DELIVERY: delivery, exact: { ORDER: String(order), DELIVERY: String(delivery) }, availability: "UNKNOWN" };
}
const stock = (productId: string, period: string, value: string, chainId = "scope"): OperationalInventory => ({
  chain_id: chainId, product_id: productId, period, format: "FORMAT", upc: productId, purchase_type: "RS", quantity_text: value });

describe("Unclosed Pedido calendar outlook", () => {
  it("keeps nine observed positions and the next three calendar months without a closure or forecast horizons", () => {
    const historical = Array.from({ length: 9 }, (_, i) => row(products[0], addReportMonth(source.last_period, i - 8), i + 1));
    const before = JSON.stringify({ source, historical });
    const dashboard = periodsDashboard(snapshot(), historical, emptyReportChoice, [null, null]);
    expect(dashboard.cutoff).toBeNull(); expect(dashboard.matrix.issue).toBeNull(); expect(dashboard.available).toBe(0);
    expect(dashboard.historySlots).toBe(9); expect(dashboard.points).toHaveLength(12);
    expect(dashboard.points.slice(0, 9).map(point => point.period)).toEqual(Array.from({ length: 9 }, (_, i) => addReportMonth("2026-02", i)));
    expect(dashboard.points.slice(9).map(point => [point.period, point.horizon, point.outlook])).toEqual([
      ["2026-11", null, true], ["2026-12", null, true], ["2027-01", null, true],
    ]);
    expect(dashboard.points.slice(0, 9).every(point => !point.outlook && point.horizon === null)).toBe(true);
    expect(dashboard.kpis.orders).toBe(45); expect(dashboard.kpis.expectedPairs).toBe(18);
    expect(dashboard.actualMonths).toBe(9);
    for (const point of dashboard.points.slice(9)) {
      for (const key of ["actual", "order", "delivery", "STATISTICAL", "ML", "ENSEMBLE", "TOWELL", "COLD_START"] as const)
        expect(point.coverage[key]).toMatchObject({ value: null, covered: 0, eligible: 2 });
      expect(point.fillRate).toMatchObject({ value: null, ordered: null, delivered: null, covered: 0, eligible: 2 });
    }
    expect(JSON.stringify({ source, historical })).toBe(before);
  });
  it("keeps twelve prior-year comparisons, exact zeros and scoped stock in its reported month, including an outlook slot", () => {
    const prior = Array.from({ length: 12 }, (_, i) => products.flatMap(product => [
      row(product, addReportMonth("2026-02", i - 12), i), row(product, addReportMonth("2026-02", i - 24), 100 + i),
    ])).flat();
    const observed = [row(products[0], "2026-10", 20), row(products[1], "2026-10", 0, 0), row(products[0], "2026-11", 999)];
    const inventory = [stock("one", "2026-10", "10.500"), stock("one", "2026-11", "4.250"), stock("two", "2026-12", "0.000"),
      stock("foreign-product", "2026-11", "9000"), stock("one", "2026-11", "8000", "foreign-chain")];
    const dashboard = periodsDashboard(snapshot(), [...prior, ...observed], emptyReportChoice, [null, null], inventory);
    expect(dashboard.priorComplete).toEqual([12, 12]);
    expect(dashboard.points[0].coverage.previous1).toMatchObject({ value: 0, covered: 2, eligible: 2 });
    expect(dashboard.points[11].coverage.previous2).toMatchObject({ value: 222, covered: 2, eligible: 2 });
    expect(dashboard.points[8].coverage.order).toMatchObject({ value: 20, covered: 2, eligible: 2 });
    expect(dashboard.points[9]).toMatchObject({ period: "2026-11", horizon: null, outlook: true,
      coverage: { inventory: { value: 4.25, exact: "4.250", covered: 1, eligible: 2 }, order: { value: null, covered: 0, eligible: 2 } } });
    expect(dashboard.points[10].coverage.inventory).toEqual({ value: 0, exact: "0.000", covered: 1, eligible: 2 });
    expect(dashboard.points[11].coverage.inventory).toEqual({ value: null, covered: 0, eligible: 2 });
    expect(dashboard.kpis.orders).toBe(20); // The archived November record is not presented as an observed outlook.
    expect(dashboard.kpis.fillRate).toBeNull(); // G3 never substitutes its training history for accredited BAASE pairs.
  });
  it("anchors to the captured source month, keeps the calendar under masks and does not promote stock dates into a close", () => {
    const historical = [row(products[0], "2026-08", 3), row(products[0], "2026-09", 4)];
    const dashboard = periodsDashboard(snapshot(), historical, emptyReportChoice, ["2026-09", "2026-12"], [stock("one", "2026-12", "2")]);
    expect(dashboard.cutoff).toBeNull(); expect(dashboard.historySlots).toBe(9);
    expect(dashboard.points[8].period).toBe("2026-10"); expect(dashboard.points[8].values.order).toBeNull();
    expect(dashboard.points[9].period).toBe("2026-11"); expect(dashboard.points[11].period).toBe("2027-01");
    expect(dashboard.points[6].values.order).toBeNull(); expect(dashboard.points[7].values.order).toBe(4);
    expect(dashboard.points[10].coverage.inventory).toMatchObject({ value: 2, covered: 1, eligible: 2 });
    expect(dashboard.kpis.orders).toBe(4); expect(dashboard.kpis.expectedPairs).toBe(4);
  });
  it("preserves the existing closed and legacy 9+3 contracts with H1–H3", () => {
    const historical = [row(products[0], "2026-10", 8)];
    const closed = { ...source, training_cutoff: "2026-10", forecast_available: true, forecast_block_code: null };
    const legacy = snapshot(); delete legacy.sourceContext;
    for (const input of [snapshot(closed), legacy]) {
      const dashboard = periodsDashboard(input, historical, emptyReportChoice, [null, null]);
      expect(dashboard.cutoff).toBe("2026-10"); expect(dashboard.historySlots).toBe(9);
      expect(dashboard.points.slice(9).map(point => [point.period, point.horizon])).toEqual([["2026-11", 1], ["2026-12", 2], ["2027-01", 3]]);
      expect(dashboard.points.every(point => point.outlook === undefined)).toBe(true);
    }
  });
});
