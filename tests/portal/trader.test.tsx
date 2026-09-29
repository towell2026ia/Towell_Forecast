import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ForecastTowellApp, { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { ForecastEnginesView } from "../../app/operational-engines-view";
import { EngineComparison } from "../../components/forecast/engine-comparison";
import { ForecastTraderChart, TraderTooltip } from "../../components/forecast/forecast-trader-chart";
import { ForecastHorizonTable } from "../../components/forecast/forecast-horizon-table";
import { addMonth, defaultVisibility, scopeHorizons, traderPoints, type SeriesVisibility } from "../../lib/forecast-chart-data";
import { emptyFilters, type Filters, type ForecastReadRepository } from "../../lib/supabase/types";
import type { PreviewClient, PreviewJob, PreviewScope } from "../../lib/forecast-preview";
import { readFileSync } from "node:fs";

vi.mock("recharts", () => {
  const Box = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return { ResponsiveContainer: Box, ComposedChart: Box, CartesianGrid: () => null, XAxis: () => null, YAxis: () => null, Tooltip: () => null,
    Line: (p: { dataKey: string; connectNulls: boolean }) => <div data-testid={`line-${p.dataKey}`} data-connect={String(p.connectNulls)}/>,
    Area: (p: { dataKey: string }) => <div data-testid={p.dataKey}/>, ReferenceArea: (p: { x1: string; x2: string }) => <div data-testid="future-area" data-start={p.x1} data-end={p.x2}/>, ReferenceLine: (p: { x: string }) => <div data-testid="cutoff" data-cutoff={p.x}/> };
});
const filters: Filters = { ...emptyFilters, chainId: "chain", productId: "product" };
const horizons = Array.from({ length: 12 }, (_, i) => ({ horizon: i + 1, target_period: addMonth("2026-07", i + 1), statistical_value: 12 + i, ml_value: 13 + i, forecast_towell: 14 + i, p10: 10 + i, p50: 14 + i, p90: 18 + i, p95: 20 + i }));
const scope: PreviewScope = { chain_id: "chain", chain_name: "Scope real", issue_period: "2026-07", latest_actual_period: "2026-07", status: "PREVIEW", certification_status: "PROVISIONAL_TEMPORAL_UNKNOWN",
  eligibility: { evaluated: 45, visible_products: 45, stat_eligible: 20, ml_eligible: 0 },
  statistical: { status: "COMPLETED", models: { Naive: 12, Holt: 8 }, available_candidates: ["Naive", "Holt"], candidates: [{ model: "Naive", available: true }], retrospective_wape: null, retrospective_bias: null },
  ml: { status: "NOT_ELIGIBLE", training_samples: 832, leader: null, available_candidates: ["Linear Global", "Random Forest Global", "Gradient Boosting Global"], candidates: [], trained_candidates: [{ model: "Linear Global", available: true }], retrospective_wape: null, retrospective_bias: null },
  selection: { published_champion: null, preview_leader: { strategy: "statistical" }, preview_challenger: null, no_degradation: null, automatic_promotion: false },
  products: [{ product_id: "product", product_code: "code", description: "Producto", category_id: "cat", forecast_status: "ACTIVE", horizons }],
  aggregates: horizons.flatMap(h => [{ level: "chain" as const, key: "chain", horizon: h.horizon, target_period: h.target_period, forecast_towell: 150 + h.horizon }, { level: "category" as const, key: "cat", horizon: h.horizon, target_period: h.target_period, forecast_towell: 100 + h.horizon }]) };
const job: PreviewJob = { job_id: "job", status: "READY_PREVIEW", engine_version: "engine", mode: "RETROSPECTIVE_TRAINING", scopes: [scope] };
const history = [{ period: "2026-05", sale: 0, order: 5, delivery: 4 }, { period: "2026-07", sale: 10, order: null, delivery: null }];
const points = () => traderPoints(history, [], horizons, scope.issue_period, filters);
function client(): PreviewClient { return { latest: vi.fn(async () => job), create: vi.fn(async () => ({ ...job, status: "QUEUED", scopes: [] })), status: vi.fn(async () => job), result: vi.fn(async () => job), dispose: vi.fn() }; }
function Harness({ api, f = filters }: { api: PreviewClient; f?: Filters }) {
  const [visibility, setVisibility] = useState<SeriesVisibility>(defaultVisibility);
  const repo = React.useMemo(() => ({ previews: api, getForecastHistory: vi.fn(async () => history), getCustomerForecast: vi.fn(async () => []) } as unknown as ForecastReadRepository), [api]);
  return <ForecastFiltersContext.Provider value={{ repository: repo, profile: { id: "user", full_name: "Test", status: "ACTIVE", global_role: "ADMIN" }, filters: f, setFilters: vi.fn(), seriesVisibility: visibility, setSeriesVisibility: setVisibility }}><ForecastEnginesView scope="Scope real"/></ForecastFiltersContext.Provider>;
}
describe("Trader chart V01–V16", () => {
  it.each([["sale", 0], ["order", 5], ["delivery", 4]])("V01/V02/V03/V05 observed %s and literal zero", (key, value) => { expect(points()[0][key as "sale"]).toBe(value); });
  it("V04 absent month and absent metric stay null", () => { expect(points().find(p => p.period === "2026-06")?.sale).toBeNull(); expect(points().find(p => p.period === "2026-07")?.order).toBeNull(); });
  it("V06/V07/V08 cutoff and H1/H12 derive from E2", () => { expect(points().find(p => p.period === "2026-08")?.towell).toBe(14); expect(points().at(-1)?.period).toBe("2027-07"); render(<ForecastTraderChart points={points()} cutoff={scope.issue_period} visible={defaultVisibility} onChange={vi.fn()}/>); expect(screen.getByTestId("cutoff").getAttribute("data-cutoff")).toBe("2026-07"); });
  it("V09/V10/V11 future Stat, ML and Towell remain literal", () => { expect(points().find(p => p.period === "2026-08")).toMatchObject({ statistical: 12, ml: 13, towell: 14, p50: 14 }); });
  it("V12 valid bands use literal quantiles and V15 future shaded area", () => { render(<ForecastTraderChart points={points()} cutoff={scope.issue_period} visible={{ ...defaultVisibility, statistical: true, ml: true }} onChange={vi.fn()}/>); expect(screen.getByTestId("band90")).toBeTruthy(); expect(screen.getByTestId("band95")).toBeTruthy(); expect(screen.getByTestId("future-area").getAttribute("data-start")).toBe("2026-08"); expect(screen.getByTestId("line-sale").getAttribute("data-connect")).toBe("false"); expect(screen.getByTestId("line-statistical")).toBeTruthy(); expect(screen.getByTestId("line-ml")).toBeTruthy(); });
  it("V13 null or invalid bands hidden without inventing", () => { const p = traderPoints(history, [], horizons.map(h => ({ ...h, p10: null, p90: null, p95: null, ml_value: null })), scope.issue_period, filters); render(<ForecastTraderChart points={p} cutoff={scope.issue_period} visible={defaultVisibility} onChange={vi.fn()}/>); expect(screen.queryByTestId("band90")).toBeNull(); expect(p.find(r => r.towell !== null)?.ml).toBeNull(); expect(p.find(r => r.towell !== null)?.p50).toBe(14); });
  it("V14 absent customer forecast not fabricated", () => { expect(points().every(p => p.client === null)).toBe(true); render(<ForecastTraderChart points={points()} cutoff={scope.issue_period} visible={defaultVisibility} onChange={vi.fn()}/>); expect(screen.getByRole("button", { name: /Fcst Cliente — Sin datos/ }).hasAttribute("disabled")).toBe(true); });
  it("customer values and tooltip preserve zero but omit null", () => { const p = traderPoints(history, [{ period: "2026-08", value: 0 }], horizons, scope.issue_period, filters).find(p => p.period === "2026-08")!; render(<TraderTooltip active payload={[{ payload: p }]}/>); expect(screen.getByText("Fcst Cliente")).toBeTruthy(); expect(screen.getByText("0 pzas")).toBeTruthy(); expect(screen.queryByText("Venta")).toBeNull(); });
  it("V16 visual date filters never alter forecast dates", () => { const f = { ...filters, periodRange: ["2026-05", "2026-05"] as Filters["periodRange"] }; const p = traderPoints(history, [], scopeHorizons(scope, f), scope.issue_period, f); expect(p.find(r => r.period === "2026-07")?.sale).toBeNull(); expect(p.at(-1)?.period).toBe("2027-07"); expect(scope.issue_period).toBe("2026-07"); });
});
describe("Scopes A01–A06", () => {
  it("A01 product values not reconstructed", () => expect(scopeHorizons(scope, filters)[0].forecast_towell).toBe(14));
  it("A02 category uses only backend aggregates", () => { const h = scopeHorizons(scope, { ...filters, productId: null, categoryId: "cat" }); expect(h[0].forecast_towell).toBe(101); expect("p90" in h[0]).toBe(false); });
  it("A03 chain uses only backend aggregates", () => expect(scopeHorizons(scope, { ...filters, productId: null })[0].forecast_towell).toBe(151));
  it("A04/A06 all scopes and foreign IDs never summed", () => { expect(scopeHorizons(scope, emptyFilters)).toEqual([]); expect(traderPoints(history, [], horizons, scope.issue_period, emptyFilters)).toEqual([]); expect(scopeHorizons(scope, { ...filters, chainId: "foreign" })).toEqual([]); expect(scopeHorizons(scope, { ...filters, categoryId: "foreign" })).toEqual([]); });
  it("A05 all chains show coverage and mismatched cuts, no trader", async () => { const api = client(); api.latest = vi.fn(async () => ({ ...job, cuts_status: "CUTS_NOT_ALIGNED", scopes: [scope, { chain_id: "child", chain_name: "Child scope", issue_period: "2026-03", status: "PREVIEW" }] })); render(<Harness api={api} f={emptyFilters}/>); await screen.findByText("Child scope"); expect(screen.getByText(/Cortes diferentes por cadena/)).toBeTruthy(); expect(screen.queryByText("Evolución y pronóstico")).toBeNull(); });
  it("search aggregate is unavailable rather than falsely comparing subset", () => expect(scopeHorizons(scope, { ...filters, productId: null, search: "partial" })).toEqual([]));
  it("malformed or partial horizons fail closed", () => { const invalid = { ...scope, products: [{ ...scope.products![0], horizons: horizons.slice(1) }] }; expect(scopeHorizons(invalid, filters)).toEqual([]); });
  it("aggregate table has exactly H1–H12 without statistical/quantile columns", () => { render(<ForecastHorizonTable horizons={scopeHorizons(scope, { ...filters, productId: null })} product={false}/>); expect(screen.getAllByText(/^H\d+$/)).toHaveLength(12); expect(screen.queryByText("P90")).toBeNull(); });
});
describe("Comparison C01–C09", () => {
  it("C01 distribution not global champion, C02 ML candidates, C03 no eligibility error", () => { render(<EngineComparison scope={scope}/>); expect(screen.getByText(/Modelo más seleccionado: Naive/)).toBeTruthy(); expect(screen.getByText("Gradient Boosting Global")).toBeTruthy(); expect(screen.getByText(/832 muestras disponibles/)).toBeTruthy(); expect(screen.queryByText("Error de cálculo")).toBeNull(); });
  it("C04/C05/C06 leader, no published Champion, promotion OFF", () => { render(<EngineComparison scope={scope}/>); expect(screen.getByText("Ninguno")).toBeTruthy(); expect(screen.getAllByText("statistical")).toHaveLength(2); expect(screen.getByText("OFF")).toBeTruthy(); });
  it("C07/C08/C09 null metrics use dash with explanation", () => { render(<EngineComparison scope={scope}/>); expect(screen.getAllByText("—").length).toBeGreaterThan(3); expect(document.body.textContent).not.toContain("Sin evidencia%"); expect(document.querySelector('[title*="No existe evidencia retrospectiva"]')).toBeTruthy(); });
});
describe("Session and execution R01–R10", () => {
  it("R04 module round-trip preserves series, product filters and preview without refetch", async () => {
    const api = client();
    const product = { id: "product", chain_id: "chain", category_id: "cat", description: "Producto", product_code: "code", variant_code: null, identifiers: [] };
    const repository = { previews: api, getVisibleChains: vi.fn(async () => [{ id: "chain", name: "Scope real", code: "S", status: "ACTIVE", has_history: true, parentId: null, scopeType: "parent" }]), getCategories: vi.fn(async () => []), getProducts: vi.fn(async () => [product]), getPeriods: vi.fn(async () => ["2026-05", "2026-07"]), getHistoricalSummary: vi.fn(async () => ({ scopeCount: 1, productCount: 1, observationCount: 2, latestPeriod: "2026-07", byScope: [] })), getHistoricalObservations: vi.fn(async () => ({ rows: [], next: null, observationCount: 0 })), getForecastHistory: vi.fn(async () => history), getCustomerForecast: vi.fn(async () => []), dispose: vi.fn() } as unknown as ForecastReadRepository;
    render(<ForecastTowellApp repository={repository} profile={{ id: "u", full_name: "Test", global_role: "ADMIN", status: "ACTIVE" }} onLogout={vi.fn(async () => {})}/>);
    await userEvent.click(screen.getByRole("combobox", { name: "Cadena / unidad comercial" })); await userEvent.click(await screen.findByRole("option", { name: "Scope real" }));
    await userEvent.click(screen.getByRole("combobox", { name: "Producto" })); await userEvent.click(await screen.findByRole("option", { name: "Producto · code" }));
    await userEvent.click(screen.getByRole("button", { name: "Motores de Forecast" })); await screen.findByText("H12");
    await userEvent.click(screen.getByRole("button", { name: "Estadístico" })); const calls = vi.mocked(api.latest).mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: "Histórico" })); await screen.findByText("Histórico operativo");
    await userEvent.click(screen.getByRole("button", { name: "Motores de Forecast" })); expect(screen.getByRole("button", { name: "Estadístico" }).getAttribute("aria-pressed")).toBe("true"); expect(screen.getByTestId("line-statistical")).toBeTruthy(); expect(vi.mocked(api.latest).mock.calls).toHaveLength(calls); expect(api.create).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("tab", { name: "Motor Estadístico" }));
    await userEvent.click(screen.getByRole("button", { name: "Histórico" }));
    await userEvent.click(screen.getByRole("button", { name: "Motores de Forecast" }));
    expect(screen.getByRole("tab", { name: "Motor Estadístico" }).getAttribute("aria-selected")).toBe("true");
    expect(vi.mocked(api.latest).mock.calls).toHaveLength(calls); expect(api.create).not.toHaveBeenCalled();
  });
  it("R01/R02 focus and visibility events do not refetch or remount", async () => { const api = client(); render(<Harness api={api}/>); await screen.findByText("H12"); fireEvent(window, new Event("focus")); fireEvent(document, new Event("visibilitychange")); expect(api.latest).toHaveBeenCalledTimes(1); expect(api.create).not.toHaveBeenCalled(); });
  it("R03 category/period changes only affect visualization; R04 switches survive", async () => { const api = client(); const view = render(<Harness api={api}/>); await screen.findByText("H12"); await userEvent.click(screen.getByRole("button", { name: "Estadístico" })); expect(screen.getByTestId("line-statistical")).toBeTruthy(); view.rerender(<Harness api={api} f={{ ...filters, periodRange: ["2026-05", "2026-05"] }}/>); await waitFor(() => expect(screen.getByTestId("line-statistical")).toBeTruthy()); expect(api.latest).toHaveBeenCalledTimes(1); expect(api.create).not.toHaveBeenCalled(); });
  it("chain switch never reuses foreign preview", async () => { const api = client(); const view = render(<Harness api={api}/>); await screen.findByText("H12"); api.latest = vi.fn(async () => null); view.rerender(<Harness api={api} f={{ ...filters, chainId: "other", productId: null }}/>); await screen.findAllByText("Sin vista previa calculada"); expect(screen.queryByText("H12")).toBeNull(); expect(api.create).not.toHaveBeenCalled(); });
  it("R10 button-only creation keeps previous preview visible and stepper compact", async () => { const api = client(); render(<Harness api={api}/>); await screen.findByText("H12"); await userEvent.click(screen.getByRole("button", { name: "Calcular vista previa" })); await screen.findByText("Vista previa anterior"); expect(screen.getByText("H12")).toBeTruthy(); expect(api.create).toHaveBeenCalledWith("chain", "product"); expect(screen.getByText("● En cola")).toBeTruthy(); });
  it("R05–R09 existing Auth/History/Lottie untouched and no demo imports", () => { for (const file of ["app/operational-engines-view.tsx", "lib/forecast-chart-data.ts", "components/forecast/forecast-trader-chart.tsx", "components/forecast/engine-comparison.tsx"]) expect(readFileSync(file, "utf8")).not.toMatch(/forecast-demo|ml-demo|ensemble-demo|total-fendi|Walmart|service_role/); const source = readFileSync("app/forecast-towell-app.tsx", "utf8"); expect(source).toContain("<ForecastAssistantErrorBoundary>"); expect(source).toContain("<HistoryView/>"); expect(source).toContain('hidden={active !== "motor"}'); });
});
