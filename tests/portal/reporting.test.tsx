import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import type { SupabaseClient } from "@supabase/supabase-js";
import { addReportMonth, amount, buildForecastMatrix, commercialRows, commercialTotals, emptyReportChoice, matrixTotal, temporalLabel, validDecision, type ReportCalculation, type ReportSnapshot } from "../../lib/forecast-reporting";
import { SupabaseReportReader } from "../../lib/forecast-report-repository";
import { emptyFilters, type ForecastReadRepository, type HistoricalRow } from "../../lib/supabase/types";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { ForecastPeriodsView } from "../../app/forecast-periods-view";
import { CommercialHistoryView } from "../../app/commercial-history-view";
import { DatabaseView } from "../../app/database-view";

// Isolated test records only. Production views never import this module.
const product = { id: "p", chain_id: "c", category_id: "cat", product_code: "CODE", variant_code: null, description: "Producto de prueba", identifiers: [] };
const chain = { id: "c", code: "C", name: "Cadena de prueba", parentId: null, scopeType: "PARENT_CHAIN", has_history: true, status: "ACTIVE" };
const calculation: ReportCalculation = { id: "calc", chain_id: "c", product_id: "p", objective: "Venta", calculation_no: 1, calculation_code: "C1", issue_period: "2026-07-01", status: "READY_FOR_DECISION", created_at: "2026-10-13T20:58:08Z", created_by: "actor", suggested_reference: null, recalculation_reason: null, candidate_metrics: {}, input_snapshot: {}, research_snapshot: null, data_snapshot_hash: "digest", preview_id: "preview", engine_version: "v4", git_sha: "sha", evidence_mode: "RETROSPECTIVE_TRAINING" };
const snapshot = (): ReportSnapshot => ({ products: [product, { ...product, id: "missing", description: "Sin forecast" }], chains: [chain], categories: [{ id: "cat", chain_id: "c", name: "Categoría" }], calculations: [{ ...calculation }], horizons: Array.from({ length: 12 }, (_, i) => ({ id: `h${i}`, calculation_id: "calc", chain_id: "c", product_id: "p", horizon: i + 1, target_period: addReportMonth("2026-07", i + 1) + "-01", statistical_value: 100, ml_value: 120, ensemble_value: 105, p10: null, p50: 105, p90: null, p95: null, band_basis: "INSUFFICIENT", band_observations: 0, statistical_model: "SBA", ml_model: "RF" })), decisions: [], live: [], customers: [], provisionals: [], capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false, forecast_calculation_history: true, forecast_selection: true, capture_center: false, live_learning: false } });
const history = (overrides: Partial<HistoricalRow> = {}): HistoricalRow => ({ product, chain: chain.name, chain_id: "c", product_id: "p", period: "2026-08-01", SALES: 80, ORDER: 100, DELIVERY: 90, availability: "UNKNOWN", ...overrides });
function adopt(s: ReportSnapshot) { s.decisions.push({ id: "event", calculation_id: "calc", chain_id: "c", product_id: "p", selected_candidate: "ENSEMBLE", selected_by: "actor", selected_at: "2026-10-14T00:00:00Z", selected_curve: s.horizons.map(h => ({ horizon: h.horizon, target_period: h.target_period, value: h.ensemble_value! })), decision_reason: "Motivo de prueba", comment: null, system_suggestion: "ENSEMBLE" }); }

describe("E5 forecast lineage and commercial arithmetic", () => {
  it("keeps all twelve stored targets, including August/September without actuals", () => {
    const s = snapshot(), before = JSON.stringify(s), m = buildForecastMatrix(s, emptyReportChoice, [null, null]);
    expect(m.periods).toHaveLength(12); expect(m.periods.slice(0, 2)).toEqual(["2026-08", "2026-09"]); expect(m.periods.at(-1)).toBe("2027-07");
    expect(m.rows[0].calculation?.created_at).toBe(calculation.created_at); expect(m.rows[0].values["2026-08"].TOWELL).toBeNull(); expect(JSON.stringify(s)).toBe(before);
  });
  it("does not convert absent, empty, NaN, negative or boolean quantities to zero", () => {
    for (const v of [null, undefined, "", " ", NaN, -1, false]) expect(amount(v)).toBeNull(); expect(amount(0)).toBe(0);
    const m = buildForecastMatrix(snapshot(), emptyReportChoice, [null, null]);
    expect(matrixTotal(m.rows, "2026-08", "STATISTICAL")).toEqual({ value: 100, covered: 1, eligible: 2 }); expect(matrixTotal(m.rows, "2026-08", "TOWELL").value).toBeNull();
  });
  it("uses authoritative parent links, never name heuristics, for consolidated totals", () => {
    const s = snapshot(); s.chains.push({ ...chain, id: "child", parentId: "c", name: "Unrelated label" }); s.products.push({ ...product, id: "child-p", chain_id: "child" });
    const m = buildForecastMatrix(s, emptyReportChoice, [null, null]); expect(m.excludedParentScopes).toBe(1); expect(m.rows.map(r => r.product.id)).toEqual(["child-p"]);
  });
  it("filters period/version/decision and never adopts a provisional", () => {
    const s = snapshot(); s.provisionals.push({ job_id: "job", chain_id: "c", product_id: "missing", issue_period: "2026-07", created_at: calculation.created_at, engine_version: "v4", model: "cold", confidence: "LOW", horizons: s.horizons.map(h => ({ horizon: h.horizon, target_period: h.target_period, value: 7 })) });
    const m = buildForecastMatrix(s, { ...emptyReportChoice, version: "C1", decision: "PENDING" }, ["2026-08", "2026-09"]); expect(m.periods).toHaveLength(2); expect(m.rows[1].values["2026-08"]).toMatchObject({ COLD_START: 7, TOWELL: null });
    expect(buildForecastMatrix(s, { ...emptyReportChoice, decision: "ADOPTED" }, [null, null]).rows).toHaveLength(0);
    adopt(s); expect(buildForecastMatrix(s, { ...emptyReportChoice, decision: "ADOPTED" }, [null, null]).rows[0].values["2026-08"].TOWELL).toBe(105);
  });
  it("rejects incomplete or mismatched saved decision curves", () => {
    const s = snapshot(); adopt(s); s.decisions[0].selected_curve[0].value++; expect(() => validDecision(s, s.calculations[0])).toThrow("decision_lineage_invalid");
    s.decisions[0].selected_curve.pop(); expect(() => validDecision(s, s.calculations[0])).toThrow();
  });
  it("labels elapsed and late-issued targets honestly", () => {
    expect(temporalLabel("2026-08", calculation.created_at, new Date("2026-10-14T12:00:00Z"))).toContain("Transcurrido · Emisión retrospectiva");
    expect(temporalLabel("2027-01", calculation.created_at, new Date("2026-10-14T12:00:00Z"))).toContain("Futuro · Emisión anterior al mes");
  });
  it("joins only exact chain/product/target and finds an older compatible version", () => {
    const s = snapshot(); s.calculations.push({ ...calculation, id: "calc2", calculation_no: 2, calculation_code: "C2", issue_period: "2027-07-01" });
    expect(commercialRows([history()], s, emptyReportChoice)[0].calculation?.id).toBe("calc");
    for (const row of [history({ period: "2026-07-01" }), history({ chain_id: "other" }), history({ product_id: "other" })]) expect(commercialRows([row], s, emptyReportChoice)[0].statistical).toBeNull();
  });
  it("links only validated customer version targets and preserves missing/zero actuals", () => {
    const s = snapshot(); s.customers.push({ id: "customer", chain_id: "c", product_id: "p", forecast_version_id: "version", issue_period: "2026-07-01", version_no: 1, target_period: "2026-08-01", value: 88 });
    const rows = commercialRows([history({ SALES: 0, DELIVERY: null }), history({ SALES: null })], s, emptyReportChoice, "STATISTICAL");
    expect(rows[0]).toMatchObject({ customer: 88, absoluteError: 100, wapeLive: null, fillRate: null }); expect(rows[1].absoluteError).toBeNull();
  });
  it("never derives LIVE WAPE from retrospective metrics or late issuance", () => {
    const s = snapshot(); expect(commercialRows([history()], s, emptyReportChoice, "STATISTICAL")[0]).toMatchObject({ absoluteError: 20, wapeLive: null });
    s.live.push({ id: "live", calculation_id: "calc", chain_id: "c", product_id: "p", target_period: "2026-08-01", horizon: 1, candidate: "STATISTICAL", forecast_value: 100, actual_sale: 80, sale_observation_id: "sale", sale_version_no: 1, absolute_error: 20, signed_error: 20, evaluated_at: "2026-09-03T00:00:00Z" });
    const observed = history({ observations: { SALES: { id: "sale", version: 1, availableAt: "2026-09-02T00:00:00Z" } } });
    expect(commercialRows([observed], s, emptyReportChoice, "STATISTICAL")[0].wapeLive).toBeNull();
    s.calculations[0].created_at = "2026-07-31T00:00:00Z";
    expect(commercialRows([observed], s, emptyReportChoice, "STATISTICAL")[0].wapeLive).toBe(25);
    observed.observations!.SALES!.version++; expect(commercialRows([observed], s, emptyReportChoice, "STATISTICAL")[0].wapeLive).toBeNull();
    observed.observations!.SALES!.version--; observed.observations!.SALES!.availableAt = null; expect(commercialRows([observed], s, emptyReportChoice, "STATISTICAL")[0].wapeLive).toBeNull();
  });
  it("monthly/cumulative ratios use sums of paired evidence, not mean percentages", () => {
    const rows = commercialRows([history({ ORDER: 100, DELIVERY: 90 }), history({ ORDER: 10, DELIVERY: 5 }), history({ ORDER: 1000, DELIVERY: null })], snapshot(), emptyReportChoice);
    expect(commercialTotals(rows).fillRate).toBeCloseTo(95 / 110 * 100); expect(commercialTotals(rows).wapeLive).toBeNull();
  });
});

function repository(s = snapshot()): ForecastReadRepository {
  return { getReportSnapshot: vi.fn(async () => s), getVisibleChains: vi.fn(async () => s.chains), getCategories: vi.fn(async () => s.categories), getProducts: vi.fn(async () => s.products), getPeriods: vi.fn(async () => []), getHistoricalObservations: vi.fn(async () => ({ rows: [], next: null, observationCount: 0 })), dispose: vi.fn(), previews: { create: vi.fn(), selectCalculation: vi.fn(), research: vi.fn(), latest: vi.fn(async () => null), vintageCapabilities: vi.fn(async () => s.capabilities), provisionalForecasts: vi.fn(async () => []) } } as unknown as ForecastReadRepository;
}
function mount(repo: ForecastReadRepository, children: React.ReactNode) { return render(<ForecastFiltersContext.Provider value={{ repository: repo, filters: emptyFilters, setFilters: vi.fn(), profile: { id: "actor", full_name: "Test", global_role: "ADMIN", status: "ACTIVE" } }}>{children}</ForecastFiltersContext.Provider>); }
describe("E5 read-only interactive views", () => {
  it("renders all 12 matrix months, comparison and partial coverage without adoption", async () => {
    const repo = repository(); mount(repo, <ForecastPeriodsView/>); const table = await screen.findByRole("table", { name: "Matriz compacta de forecast" });
    expect(within(table).getAllByRole("columnheader")).toHaveLength(5); expect(within(table).getByText("Ago-26")).toBeTruthy(); expect(within(table).getByText("Sep-26")).toBeTruthy(); expect(within(table).getByText("Jul-27")).toBeTruthy();
    expect(within(table).getAllByRole("row")).toHaveLength(13); expect(screen.getByText("TOTAL PARCIAL")).toBeTruthy();
    expect(screen.getByText("Sin decisión", { selector: "dd" })).toBeTruthy();
    await userEvent.selectOptions(screen.getByLabelText("Estado de decisión"), "ADOPTED"); expect(await screen.findByText("Sin cálculo compatible disponible para los periodos seleccionados.")).toBeTruthy();
    expect(repo.previews!.create).not.toHaveBeenCalled(); expect(repo.previews!.selectCalculation).not.toHaveBeenCalled(); expect(repo.previews!.research).not.toHaveBeenCalled();
  });
  it("commercial matrix shows stored targets with no fabricated observations or LIVE WAPE", async () => {
    const repo = repository(); mount(repo, <CommercialHistoryView/>); await screen.findAllByText("Sin registros para estos filtros.");
    await userEvent.selectOptions(screen.getByLabelText("Periodos comerciales"), "calculation"); const table = await screen.findByRole("table", { name: "Histórico comercial" });
    expect(within(table).getAllByRole("row")).toHaveLength(13); expect(within(table).getByText("2026-08")).toBeTruthy(); expect(within(table).getAllByText("Sin observación comercial")).toHaveLength(12); expect(within(table).getByRole("columnheader", { name: "WAPE LIVE" })).toBeTruthy();
    expect(repo.getHistoricalObservations).toHaveBeenCalledWith(expect.objectContaining({ chainId: "c", productId: "p", periodRange: ["2026-08", "2027-07"] }), { size: 50 }); expect(repo.previews!.selectCalculation).not.toHaveBeenCalled();
  });
  it("database offers all eight read-only sections and resets pagination on section change", async () => {
    const repo = repository(); repo.getDatabasePage = vi.fn(async () => ({ headers: ["ID"], rows: [{ id: "entry", cells: ["Stored record"] }], hasMore: true }));
    mount(repo, <DatabaseView/>); await screen.findByText("Stored record"); expect(within(screen.getByLabelText("Sección")).getAllByRole("option")).toHaveLength(8);
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" })); await waitFor(() => expect(repo.getDatabasePage).toHaveBeenCalledWith("history", emptyFilters, 1, 50));
    await userEvent.selectOptions(screen.getByLabelText("Sección"), "decisions"); await waitFor(() => expect(repo.getDatabasePage).toHaveBeenCalledWith("decisions", emptyFilters, 0, 50));
    expect(screen.queryByRole("button", { name: /Editar|Eliminar|Adoptar|SQL/i })).toBeNull();
  });
  it("failed lineage/read is fail-closed with no stale matrix", async () => {
    const repo = repository(); repo.getReportSnapshot = vi.fn(async () => { throw new Error("rls_denied"); }); mount(repo, <ForecastPeriodsView/>); expect(await screen.findByRole("alert")).toBeTruthy(); expect(screen.queryByRole("table")).toBeNull();
  });
  it("navigation order and production browser graph contain no demo, secrets or report writes", () => {
    const app = readFileSync("app/forecast-towell-app.tsx", "utf8"); expect(app).not.toContain('"auditoria"'); expect(app.indexOf('["base-datos"')).toBeLessThan(app.indexOf('["usuarios"'));
    for (const path of ["app/database-view.tsx", "app/forecast-periods-view.tsx", "app/commercial-history-view.tsx", "lib/forecast-report-repository.ts"]) expect(readFileSync(path, "utf8")).not.toMatch(/service_role|SERVICE_ROLE|\.insert\(|\.update\(|\.delete\(|\.rpc\(|\.create\(|selectCalculation\(|forecast-demo|reporting\.test/);
    expect(readFileSync("app/operational-engines-view.tsx", "utf8")).not.toContain("client.research!");
  });
});
describe("E5 RLS-bound report repository", () => {
  function client(data: Record<string, unknown[]>) {
    const calls: { table: string; method: string; args: unknown[] }[] = [];
    const from = (table: string) => { const query: Record<string, unknown> = {}; for (const method of ["select", "eq", "in", "gte", "lte", "order", "range", "abortSignal"]) query[method] = (...args: unknown[]) => { calls.push({ table, method, args }); return query; }; query.then = (resolve: (value: unknown) => void) => Promise.resolve({ data: data[table] ?? [], error: null }).then(resolve); return query; };
    return { client: { from } as unknown as SupabaseClient, calls };
  }
  it("preserves complete C1 and future period choices from stored horizons", async () => {
    const s = snapshot(), repo = repository(s), mock = client({ forecast_calculations: s.calculations, forecast_calculation_horizons: s.horizons });
    const reader = new SupabaseReportReader(mock.client, repo, new AbortController().signal); expect((await reader.snapshot(emptyFilters)).horizons).toHaveLength(12); expect(await reader.periods("c")).toContain("2026-08");
    expect(mock.calls).toContainEqual({ table: "forecast_calculations", method: "in", args: ["product_id", ["p", "missing"]] });
  });
  it("rejects foreign scope, missing horizon or wrong target", async () => {
    for (const bad of [snapshot().horizons.slice(1), snapshot().horizons.map(h => ({ ...h, chain_id: "foreign" })), snapshot().horizons.map(h => ({ ...h, target_period: "2026-07-01" }))]) {
      const mock = client({ forecast_calculations: [calculation], forecast_calculation_horizons: bad }); await expect(new SupabaseReportReader(mock.client, repository(), new AbortController().signal).snapshot(emptyFilters)).rejects.toThrow();
    }
  });
  it("bounds commercial fact pagination upstream and preserves zero and observation metadata", async () => {
    const mock = client({ monthly_observations: [{ id: "obs", chain_id: "c", product_id: "p", period: "2026-08-01", metric_code: "SALES", value: 0, version_no: 1, availability_source: "UNKNOWN", available_at: null, source_batch_id: "batch" }] });
    const filters = { ...emptyFilters, chainId: "c", productId: "p", periodRange: ["2026-08", "2026-09"] as [string, string] };
    const result = await new SupabaseReportReader(mock.client, repository(), new AbortController().signal).databasePage("observations", filters, 2, 50); expect(result.rows[0].cells[5]).toBe(0);
    expect(mock.calls).toContainEqual({ table: "monthly_observations", method: "range", args: [100, 150] }); expect(mock.calls).toContainEqual({ table: "monthly_observations", method: "eq", args: ["chain_id", "c"] }); expect(mock.calls).toContainEqual({ table: "monthly_observations", method: "gte", args: ["period", "2026-08-01"] });
  });
  it("refuses reads after session disposal", async () => { const controller = new AbortController(); controller.abort(); await expect(new SupabaseReportReader(client({}).client, repository(), controller.signal).databasePage("history", emptyFilters, 0, 50)).rejects.toThrow("session_disposed"); });
});
