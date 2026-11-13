import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { attachCommercial, changeCommercial, commercialOptions, filteredProducts, matchesChainScope, matchesCommercial, modelKey, notApplicableCommercial, selectedScopeIds, unknownCommercial } from "../../lib/commercial-filters";
import { periodsDashboard } from "../../lib/periods-dashboard";
import { operationalContext } from "./operational-context-fixture";
import { operationalPeriodsSheets } from "../../lib/operational-periods-xlsx";
import { addReportMonth, buildForecastMatrix, emptyReportChoice, type ReportSnapshot } from "../../lib/forecast-reporting";
import { emptyFilters, type CommercialClassification, type Filters, type ForecastReadRepository, type HistoricalRow, type Product } from "../../lib/supabase/types";
import { ForecastFiltersContext, GlobalFilters } from "../../app/forecast-towell-app";
import { ForecastPeriodsView } from "../../app/forecast-periods-view";
import { PeriodsComparisonChart } from "../../components/forecast/periods-comparison-chart";
import { RailwayPreviewClient } from "../../lib/forecast-preview";
import { SupabaseReportReader } from "../../lib/forecast-report-repository";
import type { SupabaseClient } from "@supabase/supabase-js";

// Isolated records; no private snapshot or credential is imported.
const product: Product = { id: "p", chain_id: "c", category_id: "cat", product_code: "001", variant_code: null, description: "TOALLA MB FENDI OXFORD", identifiers: [["0001", "0002"]] };
const classification: CommercialClassification = { product_id: "p", chain_id: "c", model: "FENDI", color: "OXFORD", size: "MEDIO BAÑO", purchase_type: "RS", product_type: "TOALLA", status: "PENDING_CLASSIFICATION", source: "TRANSIENT", reasons: ["PRODUCT_MAPPING_APPROVAL_REQUIRED"] };
const chain = { id: "c", code: "C", name: "Authorized", status: "ACTIVE", has_history: true, parentId: null, scopeType: "COMMERCIAL_UNIT" };
function snapshot(): ReportSnapshot {
  return { products: [{ ...product, commercial: classification }], chains: [chain], categories: [], calculations: [{ id: "calc", chain_id: "c", product_id: "p", objective: "Venta", calculation_no: 1, calculation_code: "C1", issue_period: "2026-07", status: "READY_FOR_DECISION", created_at: "2026-10-13T00:00:00Z", created_by: "actor", suggested_reference: null, recalculation_reason: null, candidate_metrics: {}, input_snapshot: {}, research_snapshot: null, data_snapshot_hash: "hash", preview_id: "preview", engine_version: "v4", git_sha: "sha", evidence_mode: "RETROSPECTIVE_TRAINING" }], horizons: Array.from({ length: 12 }, (_, i) => ({ id: String(i), calculation_id: "calc", chain_id: "c", product_id: "p", horizon: i + 1, target_period: addReportMonth("2026-07", i + 1), statistical_value: 9800.26, ml_value: 10494.38, ensemble_value: 9973.79, p10: null, p50: 9973.79, p90: null, p95: null, band_basis: "INSUFFICIENT", band_observations: 0, statistical_model: "SBA", ml_model: "RF" })), decisions: [], live: [], provisionals: [], customers: [], capabilities: { vintage_persistence: false, champion_publication: false, official_publication: false } };
}
function history(period: string, sales: number | null = 10, overrides: Partial<HistoricalRow> = {}): HistoricalRow { return { chain_id: "c", product_id: "p", product, chain: chain.name, period, SALES: sales, ORDER: 10, DELIVERY: 9, availability: "UNKNOWN", ...overrides }; }

describe("Model comparison keys and dependent multiselection", () => {
  it("normalizes Unicode NFC, case and whitespace without stripping accents, words or punctuation", () => {
    expect([" Fendi ", "FENDI", "fendi"].map(modelKey)).toEqual(["FENDI", "FENDI", "FENDI"]);
    expect(modelKey("  good\u00a0\tcristy  ")).toBe("GOOD CRISTY"); expect(modelKey("soft  dry")).toBe("SOFT DRY");
    expect(modelKey("Orqui\u0301dea")).toBe(modelKey("ORQUÍDEA"));
    expect(modelKey("AÑO")).not.toBe(modelKey("ANO")); expect(modelKey("SOFT-DRY")).not.toBe(modelKey("SOFT DRY"));
    expect(modelKey("ＦＥＮＤＩ")).not.toBe(modelKey("FENDI"));
  });
  it("homologates option labels while keeping source descriptions, identifiers and status intact", () => {
    const products = [product, { ...product, id: "p2" }];
    const classes = [classification, { ...classification, product_id: "p2", model: " Fendi " }];
    const before = JSON.stringify({ products, classes }); const enriched = attachCommercial(products, classes);
    expect(commercialOptions(enriched, emptyFilters, "commercialModels")).toEqual([{ id: "FENDI", name: "FENDI", count: 2, pending: true, statuses: ["PENDING_CLASSIFICATION"] }]);
    expect(JSON.stringify({ products, classes })).toBe(before); expect(enriched[1].commercial?.model).toBe(" Fendi ");
    expect(enriched[0].identifiers).toEqual(product.identifiers);
  });
  it("keeps unknowns and all dynamically discovered multiword models across 1010 products", () => {
    const products = Array.from({ length: 1010 }, (_, i) => ({ ...product, id: `p${i}` }));
    const enriched = attachCommercial(products, products.map((p, i) => ({ ...classification, product_id: p.id, model: i % 2 ? "NEW MULTIWORD DESIGN" : null })));
    expect(commercialOptions(enriched, emptyFilters, "commercialModels").map(o => o.id)).toContain(unknownCommercial);
    expect(enriched.filter(p => matchesCommercial(p, { ...emptyFilters, commercialModels: ["NEW MULTIWORD DESIGN"] }))).toHaveLength(505);
    expect(enriched.filter(p => matchesCommercial(p, { ...emptyFilters, commercialModels: [] }))).toHaveLength(0);
    expect(enriched.filter(p => matchesCommercial(p, { ...emptyFilters, commercialModels: null }))).toHaveLength(1010);
  });
  it("selects a union once and resets color, size and product without dropping parent filters or periods", () => {
    const before = { ...emptyFilters, commercialType: "TOALLA", commercialModels: ["FENDI"], commercialColor: "OXFORD", commercialSize: "MEDIO BAÑO", productId: "p", periodRange: ["2026-01", "2026-12"] as [string, string] };
    expect(changeCommercial(before, "commercialModels", ["Fendi", "FIORE", "FENDI"])).toMatchObject({ commercialType: "TOALLA", commercialModels: ["FENDI", "FIORE"], commercialColor: null, commercialSize: null, productId: null, periodRange: before.periodRange });
    expect(before.productId).toBe("p");
    const p = { ...product, commercial: classification };
    expect(matchesCommercial(p, { ...emptyFilters, commercialModels: ["Fendi", "FIORE"] })).toBe(true);
    expect(commercialOptions([p], { ...emptyFilters, commercialColor: "unrelated", commercialSize: "unrelated" }, "commercialModels")).toHaveLength(1);
  });
  it("retains pending and documented not-applicable states, never silently approving unknowns", () => {
    const p = attachCommercial([product], [{ ...classification, color: null, not_applicable: ["color"] }])[0];
    expect(commercialOptions([p], emptyFilters, "commercialColor")[0].id).toBe(notApplicableCommercial);
    expect(matchesCommercial(p, { ...emptyFilters, commercialColor: unknownCommercial })).toBe(false);
    expect(matchesCommercial(p, { ...emptyFilters, classificationStatus: "APPROVED" })).toBe(false);
  });
  it("fails closed for missing/duplicate/cross-chain catalog responses", async () => {
    expect(() => attachCommercial([product], [])).toThrow(); expect(() => attachCommercial([product], [classification, classification])).toThrow(); expect(() => attachCommercial([product], [{ ...classification, chain_id: "foreign" }])).toThrow();
    await expect(filteredProducts({ getProducts: async () => [product] } as ForecastReadRepository, { ...emptyFilters, commercialModels: ["FENDI"] })).rejects.toThrow();
  });
  it("uses only GET for the authorized commercial response", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ products: [classification], persisted: false, source_context: operationalContext }), { status: 200 }));
    const client = new RailwayPreviewClient({ rpc: () => ({ abortSignal: async () => ({ data: [operationalContext], error: null }) }), auth: { getSession: async () => ({ data: { session: { access_token: "isolated-test-token" } } }) } } as unknown as SupabaseClient, "https://example.test");
    expect(await client.commercialCatalog()).toEqual([classification]); expect(fetcher).toHaveBeenCalledWith("https://example.test/api/forecast/commercial-catalog", expect.objectContaining({ method: "GET", cache: "no-store" })); client.dispose();
  });
  it("rejects commercial attributes from a different source or a change during its read", async () => {
    for (const changedDuringRead of [false, true]) {
      let read = 0;
      const current = { ...operationalContext, source_hash: "b".repeat(64), generation: 3 };
      vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ products: [classification], persisted: false,
        source_context: changedDuringRead ? operationalContext : current }), { status: 200 }));
      const client = new RailwayPreviewClient({ rpc: () => ({ abortSignal: async () => ({ data: [changedDuringRead && read++ ? current : operationalContext], error: null }) }), auth: { getSession: async () => ({ data: { session: { access_token: "isolated-test-token" } } }) } } as unknown as SupabaseClient, "https://example.test");
      await expect(client.commercialCatalog(operationalContext)).rejects.toThrow("DATA_READ_FAILED");
      client.dispose();
    }
  });
});

describe("Purchase type and optional-format scope isolation", () => {
  const selected: Filters = { ...emptyFilters, chainId: "parent", scopeChainIds: ["format-a", "format-b", "format-a"], purchaseType: "RS" };
  function item(id: string, chainId: string, purchase: "RS" | "CE" | undefined, model = "SHARED MODEL"): Product {
    return { ...product, id, chain_id: chainId, identifiers: [["SAME-UPC", id]],
      commercial: { ...classification, product_id: id, chain_id: chainId, model, purchase_type: purchase } };
  }
  it("uses exact selected UUIDs, including an explicitly empty scope, never a global fallback", () => {
    expect(new Set(selectedScopeIds(selected))).toEqual(new Set(["format-a", "format-b"]));
    expect(matchesChainScope("format-a", selected)).toBe(true);
    expect(matchesChainScope("format-b", selected)).toBe(true);
    expect(matchesChainScope("parent", selected)).toBe(false);
    expect(matchesChainScope("other-chain", selected)).toBe(false);
    const empty = { ...selected, scopeChainIds: [] };
    expect(selectedScopeIds(empty)).toEqual([]);
    expect(matchesChainScope("format-a", empty)).toBe(false);
    expect(selectedScopeIds({ ...emptyFilters, chainId: "format-b" })).toEqual(["format-b"]);
    expect(selectedScopeIds(emptyFilters)).toBeNull();
  });
  it("keeps RS, CE and unknown purchase evidence independent even for identical model labels", () => {
    const rows = [item("rs", "format-a", "RS"), item("ce", "format-a", "CE"), item("unknown", "format-a", undefined)];
    expect(rows.filter(p => matchesCommercial(p, { ...emptyFilters, purchaseType: "RS" })).map(p => p.id)).toEqual(["rs"]);
    expect(rows.filter(p => matchesCommercial(p, { ...emptyFilters, purchaseType: "CE" })).map(p => p.id)).toEqual(["ce"]);
    expect(commercialOptions(rows, { ...emptyFilters, purchaseType: "CE" }, "commercialModels")).toMatchObject([{ id: "SHARED MODEL", count: 1 }]);
    expect(commercialOptions(rows, { ...emptyFilters, purchaseType: "RS" }, "commercialModels")).toMatchObject([{ id: "SHARED MODEL", count: 1 }]);
  });
  it("unites all selected formats once by product UUID while retaining same UPCs in distinct scopes", async () => {
    const a = item("a", "format-a", "RS"), b = item("b", "format-b", "RS"), ce = item("ce", "format-a", "CE"), foreign = item("foreign", "other-chain", "RS");
    const byChain = new Map([["format-a", [a, a, ce]], ["format-b", [b]], ["other-chain", [foreign]]]);
    const repo = { getProducts: vi.fn(async (id: string | null) => byChain.get(id ?? "") ?? []),
      getCommercialCatalog: vi.fn(async () => [a, b, ce, foreign].map(p => p.commercial!)) } as unknown as ForecastReadRepository;
    const actual = await filteredProducts(repo, selected);
    expect(actual.map(p => p.id).sort()).toEqual(["a", "b"]);
    expect(commercialOptions(actual, selected, "commercialModels")).toMatchObject([{ id: "SHARED MODEL", count: 2 }]);
    expect(new Set((repo.getProducts as ReturnType<typeof vi.fn>).mock.calls.map(([id]) => id))).toEqual(new Set(["format-a", "format-b"]));
    expect(await filteredProducts(repo, { ...selected, scopeChainIds: [] })).toEqual([]);
  });
  it("does not call any RS fact, inventory, calculation or provisional endpoint for an empty CE selection", async () => {
    const rs = item("rs", "format-a", "RS");
    const provisionals = vi.fn(async () => []);
    const repo = { getProducts: vi.fn(async () => [rs]), getCommercialCatalog: vi.fn(async () => [rs.commercial!]),
      getVisibleChains: vi.fn(async () => [{ ...chain, id: "format-a" }]), getCategories: vi.fn(async () => []),
      previews: { vintageCapabilities: vi.fn(async () => ({ forecast_calculation_history: true })), provisionalForecasts: provisionals } } as unknown as ForecastReadRepository;
    const client = { from: vi.fn(() => { throw new Error("RS table must not be read for CE"); }), rpc: vi.fn(() => { throw new Error("RS facts must not be read for CE"); }) };
    const reader = new SupabaseReportReader(client as unknown as SupabaseClient, repo, new AbortController().signal);
    const ce: Filters = { ...emptyFilters, chainId: "format-a", purchaseType: "CE" };
    expect(await reader.history(ce, operationalContext)).toEqual([]);
    expect(await reader.inventory(ce, operationalContext)).toEqual([]);
    const snap = await reader.snapshot(ce, operationalContext);
    expect(snap.products).toEqual([]); expect(snap.calculations).toEqual([]); expect(snap.provisionals).toEqual([]);
    expect(client.from).not.toHaveBeenCalled(); expect(client.rpc).not.toHaveBeenCalled(); expect(provisionals).not.toHaveBeenCalled();
    const dashboard = periodsDashboard(snap, [], emptyReportChoice, [null, null]);
    const sheets = operationalPeriodsSheets(snap, dashboard, [], []);
    expect(sheets.find(s => s.name === "Cálculos almacenados")?.rows).toEqual([]);
    expect(sheets.find(s => s.name === "Histórico comercial")?.rows).toEqual([]);
    expect(sheets.find(s => s.name === "Inventario")?.rows).toEqual([]);
  });
});

describe("Rolling 9+3 and compatible frozen calculations", () => {
  it("places Nov-25 through Jul-26 actuals then Aug/Sep/Oct H1/H2/H3, with twelve prior positions", () => {
    const s = snapshot(), before = JSON.stringify(s);
    const h = Array.from({ length: 36 }, (_, i) => history(addReportMonth("2023-11", i), i));
    const d = periodsDashboard(s, h, emptyReportChoice, [null, null]);
    expect(d.points).toHaveLength(12); expect(d.points[0].period).toBe("2025-11"); expect(d.points[8].period).toBe("2026-07");
    expect(d.points.slice(9).map(p => [p.period, p.horizon])).toEqual([["2026-08", 1], ["2026-09", 2], ["2026-10", 3]]);
    expect(d.actualMonths).toBe(9); expect(d.priorComplete).toEqual([12, 12]);
    expect(d.points[0].values.previous2).toBe(0); expect(d.points[11].values.previous2).toBe(11);
    expect(d.points[0].values.previous1).toBe(12); expect(d.points[11].values.previous1).toBe(23);
    expect(d.points.slice(0, 9).every(p => p.values.ENSEMBLE === null)).toBe(true);
    expect(d.points[9].values).toMatchObject({ actual: null, STATISTICAL: 9800.26, ML: 10494.38, ENSEMBLE: 9973.79, TOWELL: null });
    expect(d.matrix.periods.at(-1)).toBe("2027-07"); expect(JSON.stringify(s)).toBe(before);
  });
  it("crosses December to January based on the exact calculation, not a selected calendar year", () => {
    const s = snapshot(); s.calculations[0].issue_period = "2026-11";
    s.horizons.forEach(h => { h.target_period = addReportMonth("2026-11", h.horizon); });
    const d = periodsDashboard(s, [], { ...emptyReportChoice, calculationId: "calc" }, [null, null]);
    expect(d.points.slice(9).map(p => p.period)).toEqual(["2026-12", "2027-01", "2027-02"]); expect(d.matrix.periods.at(-1)).toBe("2027-11");
  });
  it("preserves zero, missing months and partial model coverage without inventing totals", () => {
    const s = snapshot(); s.products.push({ ...product, id: "missing", commercial: { ...classification, product_id: "missing", model: "Fendi" } });
    const d = periodsDashboard(s, [history("2026-01", 0), history("2026-03", null)], emptyReportChoice, [null, null]);
    expect(d.points[2].coverage.actual).toEqual({ value: 0, exact: "0", covered: 1, eligible: 2 }); expect(d.points[3].values.actual).toBeNull();
    expect(d.points[9].coverage.ENSEMBLE).toEqual({ value: 9973.79, covered: 1, eligible: 2 });
    expect(d.coverage).toMatchObject([{ chainId: "c", model: "FENDI", total: 2, calculated: 1, pending: 1, decided: 0, undecided: 2 }]);
  });
  it("respects period masks, pairs Fill Rate and rejects duplicate monthly facts", () => {
    const h = [history("2026-01", 0), history("2026-02", null, { ORDER: 100, DELIVERY: 50 }), history("2026-03", 99, { DELIVERY: null })];
    const d = periodsDashboard(snapshot(), h, emptyReportChoice, ["2026-01", "2026-02"]);
    expect(d.kpis.sales).toBe(0); expect(d.kpis.fillRate).toBeCloseTo(59 / 110 * 100); expect(d.points[4].values.actual).toBeNull(); expect(d.matrix.periods).toEqual([]);
    expect(() => periodsDashboard(snapshot(), [h[0], h[0]], emptyReportChoice, [null, null])).toThrow("duplicate_dashboard_month");
  });
  it("excludes overlapping ancestors transitively, retaining explicitly isolated parent products", () => {
    const s = snapshot(); s.chains.push({ ...chain, id: "child", parentId: "c" }, { ...chain, id: "grandchild", parentId: "child" });
    expect(periodsDashboard(s, [history("2026-01")], emptyReportChoice, [null, null]).kpis.sales).toBe(10);
    s.products.push({ ...product, id: "g", chain_id: "grandchild" });
    const d = periodsDashboard(s, [history("2026-01"), history("2026-01", 20, { product_id: "g", chain_id: "grandchild" })], emptyReportChoice, [null, null]);
    expect(d.kpis.sales).toBe(20); expect(d.matrix.rows.map(r => r.product.id)).toEqual(["g"]);
  });
  it("keeps C1–Cn selectable and never mixes cuts, engine versions or evidence modes", () => {
    const s = snapshot(); s.products.push({ ...product, id: "other" }); s.calculations.push({ ...s.calculations[0], id: "c2", product_id: "other", calculation_code: "C2", calculation_no: 2, issue_period: "2027-01", engine_version: "v5" });
    const exact = buildForecastMatrix(s, { ...emptyReportChoice, calculationId: "calc" }, [null, null]);
    expect(exact.issue).toBe("2026-07"); expect(exact.rows.filter(r => r.calculation)).toHaveLength(1);
    s.calculations[1].issue_period = "2026-07";
    expect(buildForecastMatrix(s, { ...emptyReportChoice, version: "C1" }, [null, null]).rows[0].calculation?.id).toBe("calc");
    expect(buildForecastMatrix(s, emptyReportChoice, [null, null]).rows.filter(r => r.calculation)).toHaveLength(1);
    s.calculations[1].engine_version = "v4"; s.calculations[1].evidence_mode = "OTHER";
    expect(buildForecastMatrix(s, emptyReportChoice, [null, null]).rows.filter(r => r.calculation)).toHaveLength(1);
  });
  it("does not sum repeated product identities or customer versions/future issues", () => {
    const s = snapshot(); s.products.push(s.products[0]);
    s.customers = [1, 2, 3].map(i => ({ id: String(i), chain_id: "c", product_id: "p", forecast_version_id: String(i), issue_period: i === 3 ? "2026-08" : "2026-07", version_no: i, target_period: "2026-08", value: i * 10 }));
    const d = periodsDashboard(s, [], emptyReportChoice, [null, null]); expect(d.points[9].values.ENSEMBLE).toBe(9973.79); expect(d.points[9].values.customer).toBe(20);
  });
});

describe("Multimodel reads remain scoped in history, snapshots and Database", () => {
  it("sends unique selected product IDs upstream for every fact read and fails on foreign results", async () => {
    const products = [product, { ...product, id: "p2" }, { ...product, id: "p3" }];
    const classes = [classification, { ...classification, product_id: "p2", model: "FIORE" }, { ...classification, product_id: "p3", model: null }];
    const repo = { getProducts: async () => products, getCommercialCatalog: async () => classes, getVisibleChains: async () => [chain], getCategories: async () => [] } as unknown as ForecastReadRepository;
    const calls: { table: string; ids: string[] }[] = [];
    let foreign = false;
    const from = (table: string) => {
      let ids: string[] = []; const q: Record<string, unknown> = {};
      for (const method of ["select", "order", "range", "eq", "gte", "lte", "abortSignal"]) q[method] = () => q;
      q.in = (field: string, selected: string[]) => { if (field === "product_id") ids = selected; return q; };
      q.then = (accept: (r: unknown) => unknown) => {
        calls.push({ table, ids });
        const data = table === "operational_read_observations" ? ids.map((id, i) => ({ observation_id: `o${i}`, id: `o${i}`, chain_id: foreign ? "foreign" : "c", product_id: id, period: "2026-01-01", metric_code: "SALES", value: 10, value_text: "10", version_no: 1, availability_source: "UNKNOWN" })) : [];
        return Promise.resolve({ data, error: null }).then(accept);
      }; return q;
    };
    const client = { from, rpc: from } as unknown as SupabaseClient;
    const reader = new SupabaseReportReader(client, repo, new AbortController().signal), f = { ...emptyFilters, commercialModels: ["Fendi", "FIORE", "FENDI"] };
    expect(await filteredProducts(repo, f)).toHaveLength(2); expect(await reader.history(f, operationalContext)).toHaveLength(2); expect((await reader.snapshot(f, operationalContext)).products).toHaveLength(2);
    expect((await reader.databasePage("history", f, 0, 50, operationalContext)).rows).toHaveLength(2);
    for (const call of calls.filter(c => ["operational_read_observations", "customer_forecast_rows"].includes(c.table))) expect(call.ids).toEqual(["p", "p2"]);
    foreign = true; await expect(reader.history(f, operationalContext)).rejects.toThrow("report_scope_mismatch");
    expect(await reader.history({ ...f, commercialModels: [] }, operationalContext)).toEqual([]);
  });
});

describe("Commercial filters and compact Periodos UI", () => {
  it("uses the official closing month, hides previous-source calculations and never accumulates inventory over time", () => {
    const s = { ...snapshot(), sourceContext: operationalContext };
    const historical = [history("2026-09", 10, { exact: { SALES: "10", FCST_TOWELL_HISTORY: "12", FCST_CLIENTE_HISTORY: "14" } }), history("2026-10", 20)];
    const inventory = [
      { chain_id: "c", product_id: "p", format: "BD", upc: "001234", period: "2026-09-01", purchase_type: "RS", quantity_text: "10.1234567890123456" },
      { chain_id: "c", product_id: "p", format: "BD", upc: "001234", period: "2026-10-01", purchase_type: "RS", quantity_text: "0.000" },
    ];
    const d = periodsDashboard(s, historical, emptyReportChoice, [null, null], inventory);
    expect(d.available).toBe(0);
    expect(d.cutoff).toBe("2026-10");
    expect(d.points[7].coverage.inventory).toEqual({ value: Number(inventory[0].quantity_text), exact: inventory[0].quantity_text, covered: 1, eligible: 1 });
    expect(d.points[8].values.inventory).toBe(0);
    expect(d.points[9].values.inventory).toBeNull();
    expect(d.points[7].values.towellHistory).toBe(12);
    expect(d.points[7].values.customer).toBe(14);
    expect(() => periodsDashboard(s, historical, emptyReportChoice, [null, null], [inventory[0], inventory[0]])).toThrow("duplicate_or_invalid_inventory");
    const sheet = operationalPeriodsSheets(s, d, historical, inventory).find(row => row.name === "Comparativa")!;
    const column = sheet.headers.indexOf("Inventario reportado");
    expect(sheet.rows[7][column]).toBe(inventory[0].quantity_text);
    expect(sheet.rows[8][column]).toBe("0.000");
    expect(sheet.rows[9][column]).toBeNull();
    render(<PeriodsComparisonChart points={d.points} cutoff={d.cutoff}/>);
    const table = screen.getByRole("table", { name: "Datos de la gráfica 9+3" });
    expect(within(table).getByText(inventory[0].quantity_text)).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Inventario reportado" })).toBeTruthy();
  });
  it("shares one pin across chart, historical series, inventory and downloadable workbook", async () => {
    const s = { ...snapshot(), sourceContext: operationalContext };
    s.calculations = s.calculations.map(row => ({ ...row, input_snapshot: { source_context: operationalContext } }));
    const exact = "10.1234567890123456";
    const historical = [history("2026-07", Number(exact), { exact: { SALES: exact, FCST_TOWELL_HISTORY: "0.000" } })];
    const inventory = [{ chain_id: "c", product_id: "p", format: "BD", upc: "001234", period: "2026-07-01", purchase_type: "RS", quantity_text: "0.000" }];
    const repository = {
      openOperationalRead: vi.fn(async () => operationalContext),
      getReportSnapshot: vi.fn(async () => s),
      getReportHistory: vi.fn(async () => historical),
      getReportInventory: vi.fn(async () => inventory),
    };
    render(<ForecastFiltersContext.Provider value={{ repository: repository as unknown as ForecastReadRepository, filters: emptyFilters, setFilters: vi.fn(), profile: { id: "actor", full_name: "Test", global_role: "VIEWER", status: "ACTIVE" } }}><ForecastPeriodsView/></ForecastFiltersContext.Provider>);
    expect(await screen.findByRole("button", { name: "Descargar XLSX de Períodos" })).toBeTruthy();
    expect(repository.openOperationalRead).toHaveBeenCalledOnce();
    for (const read of [repository.getReportSnapshot, repository.getReportHistory, repository.getReportInventory]) expect(read).toHaveBeenCalledWith(expect.anything(), operationalContext);
    const dashboard = periodsDashboard(s, historical, emptyReportChoice, [null, null]);
    const sheets = operationalPeriodsSheets(s, dashboard, historical, inventory);
    const sheet = (name: string) => sheets.find(row => row.name === name)!;
    expect(sheet("Trazabilidad").rows).toContainEqual(["Fuente oficial SHA-256", operationalContext.source_hash]);
    expect(sheet("Trazabilidad").rows).toContainEqual(["Cobertura Fill Rate", "Parcial"]);
    expect(sheet("Comparativa").rows[8]).toContain(dashboard.points[8].coverage.actual.exact);
    expect(sheet("Histórico comercial").rows[0][4]).toBe(exact);
    expect(sheet("Histórico comercial").rows[0].slice(-2)).toEqual(["0.000", null]);
    expect(sheet("Inventario").rows[0]).toContain("001234");
    expect(sheet("Inventario").rows[0]).toContain("0.000");
    repository.openOperationalRead.mockResolvedValue({ ...operationalContext, generation: 3 });
    await userEvent.click(screen.getByRole("button", { name: "Descargar XLSX de Períodos" }));
    expect((await screen.findByRole("alert")).textContent).toContain("La fuente oficial cambió");
  });
  it("updates one, multiple, none and all model selections without engine/decision writes", async () => {
    const s = snapshot(); s.products.push({ ...product, id: "missing", commercial: { ...classification, product_id: "missing", model: "FIORE" } });
    const repository = { getProducts: vi.fn(async () => s.products), getCommercialCatalog: vi.fn(async () => s.products.map(p => p.commercial!)), getVisibleChains: vi.fn(async () => s.chains), getCategories: vi.fn(async () => []), getPeriods: vi.fn(async () => []), getReportSnapshot: vi.fn(async (f: Filters) => ({ ...s, products: await filteredProducts(repository as unknown as ForecastReadRepository, f) })), getReportHistory: vi.fn(async () => [history("2026-01")]), previews: { create: vi.fn(), selectCalculation: vi.fn(), research: vi.fn() } };
    function Harness() { const [filters, setFilters] = useState<Filters>(emptyFilters); return <ForecastFiltersContext.Provider value={{ repository: repository as unknown as ForecastReadRepository, filters, setFilters, profile: { id: "actor", full_name: "Test", global_role: "VIEWER", status: "ACTIVE" } }}><GlobalFilters/><ForecastPeriodsView/></ForecastFiltersContext.Provider>; }
    render(<Harness/>); const table = await screen.findByRole("table", { name: "Matriz compacta de forecast" });
    expect(within(table).getAllByRole("columnheader")).toHaveLength(5); expect(within(table).getAllByRole("row")).toHaveLength(13);
    expect(within(table).getByText("Ago-26")).toBeTruthy(); expect(within(table).getByText("Sep-26")).toBeTruthy(); expect(within(table).getByText("Jul-27")).toBeTruthy();
    expect(screen.getByText("TOTAL PARCIAL")).toBeTruthy(); expect(screen.getByText(/Emisión retrospectiva\./)).toBeTruthy();
    await userEvent.click(screen.getByLabelText("Modelo")); await userEvent.click(screen.getByRole("button", { name: "Limpiar selección" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /FENDI/ }));
    await waitFor(() => expect(repository.getReportSnapshot).toHaveBeenLastCalledWith(expect.objectContaining({ commercialModels: ["FENDI"] })));
    await userEvent.click(screen.getByRole("checkbox", { name: /FIORE/ }));
    await waitFor(() => expect(repository.getReportSnapshot).toHaveBeenLastCalledWith(expect.objectContaining({ commercialModels: ["FENDI", "FIORE"] })));
    await userEvent.type(screen.getByLabelText("Buscar modelos"), "fiore"); expect(screen.queryByRole("checkbox", { name: /FENDI/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Seleccionar todos" }));
    await waitFor(() => expect(repository.getReportSnapshot).toHaveBeenLastCalledWith(expect.objectContaining({ commercialModels: null })));
    await screen.findByRole("table", { name: "Matriz compacta de forecast" });
    await userEvent.click(screen.getByText("Productos pendientes de cálculo (1)")); expect(screen.getByRole("table", { name: "Productos pendientes de cálculo" })).toBeTruthy();
    const graph = screen.getByLabelText("Gráfica comparativa comercial"); await userEvent.click(within(graph).getByRole("checkbox", { name: "Forecast ML" })); expect((within(graph).getByRole("checkbox", { name: "Forecast ML" }) as HTMLInputElement).checked).toBe(false);
    expect(repository.previews.create).not.toHaveBeenCalled(); expect(repository.previews.selectCalculation).not.toHaveBeenCalled(); expect(repository.previews.research).not.toHaveBeenCalled();
  });
});
