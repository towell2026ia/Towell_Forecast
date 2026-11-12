import { describe, expect, it } from "vitest";
import { periodsDashboard } from "../../lib/periods-dashboard";
import { emptyReportChoice, type ReportSnapshot } from "../../lib/forecast-reporting";
import { operationalPeriodsSheets } from "../../lib/operational-periods-xlsx";
import { planningXlsx } from "../../lib/planning-xlsx";
import type { HistoricalRow } from "../../lib/supabase/types";
import { operationalContext } from "./operational-context-fixture";

function snapshot(): ReportSnapshot {
  return { products: ["p1", "p2", "p3", "p4"].map(id => ({ id, chain_id: "chain", category_id: null, product_code: id, variant_code: null, description: id, identifiers: [] })),
    chains: [{ id: "chain", code: "CHAIN", name: "Chain", status: "ACTIVE", has_history: true, parentId: null, scopeType: "COMMERCIAL_UNIT" }],
    categories: [], calculations: [], horizons: [], decisions: [], live: [], customers: [], provisionals: [], sourceContext: operationalContext,
    capabilities: { vintage_persistence: false, champion_publication: false, official_publication: false } };
}
function history(product: string, period: string, ordered: string | null, delivered: string | null): HistoricalRow {
  const p = snapshot().products.find(row => row.id === product)!;
  return { chain_id: "chain", product_id: product, product: p, chain: "Chain", period, SALES: null,
    ORDER: ordered === null ? null : Number(ordered), DELIVERY: delivered === null ? null : Number(delivered), availability: "UNKNOWN",
    exact: { ...(ordered === null ? {} : { ORDER: ordered }), ...(delivered === null ? {} : { DELIVERY: delivered }) } };
}
const point = (rows: HistoricalRow[], period = "2026-09", range: [string | null, string | null] = [null, null]) => periodsDashboard(snapshot(), rows, emptyReportChoice, range).points.find(row => row.period === period)!;

describe("Monthly Fill Rate uses comparable pairs independently of piece series", () => {
  it("keeps same-name models in different chains separate without changing the selected quantities", () => {
    const s = snapshot();
    const first = { ...s.products[0], commercial: { chain_id: "chain", product_id: "p1", model: "GRANADA", color: "BLUE", size: "MB", product_type: "TOALLA", status: "PENDING_CLASSIFICATION" as const, source: "SYNTHETIC", reasons: [] } };
    const second = { ...first, id: "other-product", chain_id: "other-chain", commercial: { ...first.commercial, chain_id: "other-chain", product_id: "other-product" } };
    s.products = [first, second]; s.chains.push({ ...s.chains[0], id: "other-chain", code: "OTHER", name: "Other chain" });
    const firstHistory = history("p1", "2026-09", "10", "9");
    const secondHistory = { ...history("p1", "2026-09", "20", "15"), chain_id: second.chain_id, product_id: second.id, product: second, chain: "Other chain" };
    const d = periodsDashboard(s, [firstHistory, secondHistory], emptyReportChoice, [null, null]);
    expect(d.coverage).toHaveLength(2);
    expect(d.coverage.map(row => row.model)).toEqual(["GRANADA", "GRANADA"]);
    expect(new Set(d.coverage.map(row => row.key)).size).toBe(2);
    expect(d.coverage.map(row => [row.chainId, row.chain, row.total])).toEqual([["chain", "Chain", 1], ["other-chain", "Other chain", 1]]);
    expect(d.matrix.rows.map(row => row.product.id)).toEqual(["p1", "other-product"]);
    expect(d.points.find(row => row.period === "2026-09")!.values).toMatchObject({ order: 30, delivery: 24 });
  });
  it("divides summed comparable delivery by order, excludes unpaired and foreign scope, and exposes partial coverage", () => {
    const rows = [history("p1", "2026-09", "90000", "82000"), history("p2", "2026-09", "10000", "9580"),
      history("p3", "2026-09", "12345", null), history("p4", "2026-09", null, "99999"),
      { ...history("p1", "2026-09", "900000", "1"), chain_id: "foreign" }];
    const value = point(rows), fillRate = value.fillRate!;
    expect(fillRate).toMatchObject({ ordered: 100000, delivered: 91580, orderedExact: "100000", deliveredExact: "91580", covered: 2, eligible: 4 });
    expect(fillRate.value).toBeCloseTo(91580 / 100000 * 100, 10);
    expect(fillRate.value!.toFixed(2)).toBe("91.58");
    expect(fillRate.value).not.toBeCloseTo((82000 / 90000 + 9580 / 10000) * 50, 4);
    expect(value.values.order).toBe(112345); // Displayed piece series retain their independent coverage.
    expect(value.values.delivery).toBe(191579);
    expect(Object.keys(value.values)).not.toContain("fillRate");
  });
  it("preserves exact decimal numerators and denominators and permits genuine percentages above 100", () => {
    const fillRate = point([history("p1", "2026-09", "0.1000000000000001", "0.2"), history("p2", "2026-09", "0.2", "0.4")]).fillRate!;
    expect(fillRate.orderedExact).toBe("0.3000000000000001");
    expect(fillRate.deliveredExact).toBe("0.6");
    expect(fillRate.value).toBeGreaterThan(100);
    expect(fillRate.value!.toFixed(2)).toBe("200.00");
  });
  it("distinguishes zero delivery, zero denominator, absent pairs and different months", () => {
    expect(point([history("p1", "2026-09", "10", "0")]).fillRate).toMatchObject({ value: 0, orderedExact: "10", deliveredExact: "0", covered: 1 });
    expect(point([history("p1", "2026-09", "0.00", "0")]).fillRate).toMatchObject({ value: null, orderedExact: "0.00", deliveredExact: "0", covered: 1 });
    expect(point([history("p1", "2026-09", "10", null), history("p1", "2026-08", null, "9")]).fillRate).toMatchObject({ value: null, ordered: null, delivered: null, covered: 0, eligible: 4 });
  });
  it("does not create future or range-masked rates even when future rows exist", () => {
    const rows = [history("p1", "2026-09", "10", "9"), history("p1", "2026-11", "10", "8")];
    expect(point(rows, "2026-11").fillRate).toMatchObject({ value: null, ordered: null, delivered: null, covered: 0, eligible: 4 });
    expect(point(rows, "2026-09", ["2026-10", "2026-10"]).fillRate).toMatchObject({ value: null, ordered: null, delivered: null, covered: 0, eligible: 4 });
    expect(point(rows, "2026-10").fillRate?.value).toBeNull();
  });
  it("rejects invalid supplied quantities instead of producing a misleading percentage", () => {
    expect(() => point([history("p1", "2026-09", "-1", "2")])).toThrow();
    expect(() => point([{ ...history("p1", "2026-09", "1", "2"), ORDER: Infinity }])).toThrow("invalid_fill_rate_value");
    expect(() => point([{ ...history("p1", "2026-09", "1", "2"), exact: { ORDER: "3", DELIVERY: "2" } }])).toThrow("invalid_fill_rate_value");
  });
  it("exports the exact monthly basis and same percentage/coverage while keeping the global KPI separate", () => {
    const s = snapshot(), rows = [history("p1", "2026-09", "100.000", "50.00"), history("p2", "2026-10", "10", "10")];
    const original = JSON.stringify({ s, rows }), dashboard = periodsDashboard(s, rows, emptyReportChoice, [null, null]);
    expect(dashboard.kpis.fillRate).toBeCloseTo(60 / 110 * 100);
    expect(dashboard.points.find(row => row.period === "2026-09")!.fillRate!.value).toBe(50);
    const sheets = operationalPeriodsSheets(s, dashboard, rows, []), comparative = sheets.find(sheet => sheet.name === "Comparativa")!;
    const september = comparative.rows.find(row => row[0] === "2026-09")!;
    expect(september.slice(-4)).toEqual([50, "1/4", "100.000", "50.00"]);
    expect(comparative.headers.slice(-4)).toEqual(["Fill Rate (%)", "Cobertura Fill Rate", "Pedido comparable", "Entrega comparable"]);
    expect(comparative.rows.find(row => row[0] === "2026-11")!.slice(-4)).toEqual([null, "0/4", null, null]);
    expect(sheets.find(sheet => sheet.name === "Trazabilidad")!.rows).toContainEqual(["Fill Rate (%)", dashboard.kpis.fillRate]);
    expect(planningXlsx(sheets).length).toBeGreaterThan(100);
    expect(JSON.stringify({ s, rows })).toBe(original);
  });
});
