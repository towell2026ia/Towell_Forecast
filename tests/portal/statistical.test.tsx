import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync, readdirSync } from "node:fs";
import { StatisticalEngineDetail } from "../../components/forecast/statistical-engine-detail";
import { StatisticalCandidateTable } from "../../components/forecast/statistical-candidate-table";
import { StatisticalHorizonAccuracy } from "../../components/forecast/statistical-horizon-chart";
import { StatisticalForecastChart, StatisticalTooltip } from "../../components/forecast/statistical-forecast-chart";
import { ForecastEnginesView } from "../../app/operational-engines-view";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { addMonth, traderPoints } from "../../lib/forecast-chart-data";
import { compareStatisticalCandidates, getStatisticalCandidates, horizonAccuracy, selectedStatisticalModel, selectionExplanation, statisticalCoverage, statisticalModelDistribution, statisticalRanking } from "../../lib/statistical-preview-data";
import { emptyFilters, type Filters, type ForecastReadRepository } from "../../lib/supabase/types";
import type { PreviewCandidate, PreviewHorizon, PreviewJob, PreviewProduct, PreviewScope } from "../../lib/forecast-preview";

vi.mock("recharts", () => {
  const Box = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return { ResponsiveContainer: Box, ComposedChart: Box, BarChart: ({ data, children }: { data: unknown; children?: React.ReactNode }) => <div data-testid="bars" data-rows={JSON.stringify(data)}>{children}</div>, Bar: () => null, CartesianGrid: () => null, XAxis: () => null, YAxis: () => null, Tooltip: () => null,
    Line: (p: { dataKey: string; connectNulls: boolean; strokeWidth: number }) => <div data-testid={`line-${p.dataKey}`} data-connect={String(p.connectNulls)} data-width={p.strokeWidth}/>, ReferenceArea: () => null, ReferenceLine: ({ x }: { x: string }) => <div data-testid="cutoff" data-period={x}/> };
});
const modelNames = ["Naive", "Seasonal Naive", "Moving Average 3", "Moving Average 6", "Moving Average 12", "Weighted Moving Average", "SES", "Holt", "Holt-Winters", "Linear Trend", "Polynomial Trend", "Croston", "SBA", "TSB"];
const filters: Filters = { ...emptyFilters, chainId: "chain", productId: "product" };
const horizons: PreviewHorizon[] = Array.from({ length: 12 }, (_, i) => ({ horizon: i + 1, target_period: addMonth("2026-07", i + 1), statistical_value: 7420 + i, statistical_model: "Holt", classification: "Regular", forecast_status: "ACTIVE", ml_value: null, forecast_towell: 7500 + i, p10: null, p50: 7500 + i, p90: null, p95: null, certification_status: "PROVISIONAL" }));
const product: PreviewProduct = { product_id: "product", product_code: "SKU", description: "Producto elegido", category_id: "cat", forecast_status: "ACTIVE", horizons };
const candidate: PreviewCandidate = { family: "statistical", product_id: "product", model: "Holt", validation_wape: 18.4, validation_bias: -2.1, validation_stability: null, observations: 48, validation_by_horizon: Array.from({ length: 12 }, (_, i) => ({ horizon: i + 1, wape: i === 2 ? null : 12.4 + i })) };
const scope: PreviewScope = { chain_id: "chain", chain_name: "Scope real", issue_period: "2026-07", status: "PREVIEW", eligibility: { evaluated: 45, visible_products: 45, stat_eligible: 20, cold_start: 3, inactive: 2, pre_launch: 1, insufficient: 19 }, statistical: { status: "COMPLETED", models: { Naive: 12, Holt: 5, Croston: 3 }, available_candidates: modelNames, candidates: [candidate, { ...candidate, model: "Naive", validation_wape: 20 }, { ...candidate, family: "ml", model: "Excluded" }, { ...candidate, product_id: "other", model: "Foreign" }], retrospective_wape: 55, retrospective_bias: 10 }, products: [product] };
const history = [{ period: "2026-05", sale: 0, order: null, delivery: null }, { period: "2026-07", sale: 6000, order: null, delivery: null }];
function detail(s = scope, f = filters, scopes = [s]) { return <StatisticalEngineDetail scope={s} scopes={scopes} filters={f} historical={history} cutsStatus="CUTS_NOT_ALIGNED" compareTowell={false} onCompare={vi.fn()}/>; }
function changed(h: Partial<PreviewHorizon>) { return { ...product, horizons: horizons.map((r, i) => i === 11 ? { ...r, ...h } : r) }; }
describe("S01–S07 literal TypeScript contract", () => {
  it.each(["validation_wape", "validation_bias", "observations", "validation_by_horizon"] as const)("candidate exposes %s", key => expect(candidate[key]).toBeDefined());
  it.each(["statistical_model", "classification", "forecast_status"] as const)("horizon exposes %s", key => expect(horizons[0][key]).toBeDefined());
});
describe("S08–S19 product and comparison", () => {
  it("S08 filters both family and product", () => expect(getStatisticalCandidates(scope, "product").map(c => c.model)).toEqual(["Holt", "Naive"]));
  it("S09 takes selected model from H1 regardless of input order", () => expect(selectedStatisticalModel({ ...product, horizons: [...horizons].reverse() }, scope.issue_period).model).toBe("Holt"));
  it("S10 H1–H12 selection is consistent", () => expect(selectedStatisticalModel(product, scope.issue_period).consistent).toBe(true));
  it.each([{ statistical_model: "Naive" }, { horizon: 11 }, { target_period: "2029-01" }, { statistical_value: NaN }])("S10 invalid metadata fails certification and chart closes", mismatch => {
    const invalid = changed(mismatch); expect(selectedStatisticalModel(invalid, scope.issue_period).consistent).toBe(false);
    render(detail({ ...scope, products: [invalid] })); expect(screen.getByRole("alert").textContent).toContain("inconsistente"); expect(screen.queryByText("Real vs Forecast Estadístico")).toBeNull();
  });
  it("S11/S12 product WAPE/Bias do not borrow scope metrics; S18 observations literal", () => { render(detail()); expect(screen.getAllByText("18.4%").length).toBeGreaterThan(0); expect(screen.getAllByText("-2.1%").length).toBeGreaterThan(0); expect(screen.queryByText("55%")).toBeNull(); expect(screen.queryByText("10%")).toBeNull(); expect(screen.getAllByText("48").length).toBeGreaterThan(0); });
  it("S13/S14 null and nonfinite metrics render dash with explanation", () => { render(<StatisticalCandidateTable candidates={[{ ...candidate, validation_wape: null, validation_bias: NaN }]} selected="Holt"/>); expect(screen.getAllByText("—")).toHaveLength(2); expect(document.body.textContent).not.toMatch(/NaN%|undefined%|Sin evidencia%/); expect(document.querySelector('[title*="No existe evidencia retrospectiva"]')).toBeTruthy(); });
  it("S15 ordering is WAPE, absolute Bias, Python string order", () => {
    const rows: PreviewCandidate[] = [{ model: "z", validation_wape: 2, validation_bias: 1 }, { model: "B", validation_wape: 2, validation_bias: -1 }, { model: "a", validation_wape: 2, validation_bias: 1 }, { model: "first", validation_wape: 1, validation_bias: 20 }, { model: "last", validation_wape: null, validation_bias: 0 }];
    expect(rows.sort(compareStatisticalCandidates).map(c => c.model)).toEqual(["first", "B", "a", "z", "last"]);
  });
  it("S16 selected badge and S19 no champion label", () => { render(detail()); expect(screen.getAllByText("Seleccionado")).toHaveLength(1); expect(document.body.textContent).not.toContain("Champion"); });
  it("S17 all 14 models and future unknown models render, no row ceiling", () => { render(<StatisticalCandidateTable candidates={[...modelNames, "Future model"].map(model => ({ ...candidate, model }))} selected="Holt"/>); for (const name of [...modelNames, "Future model"]) expect(screen.getByText(name)).toBeTruthy(); expect(screen.getAllByRole("row")).toHaveLength(16); });
  it("explanation only asserts verifiable ordering; Bias/name tie criteria", () => {
    const selected = { ...candidate, validation_wape: 10, validation_bias: 1 };
    expect(selectionExplanation([selected], selected)).toContain("menor WAPE");
    expect(selectionExplanation([selected, { ...selected, model: "Z", validation_bias: 2 }], selected)).toContain("menor Bias absoluto");
    expect(selectionExplanation([selected, { ...selected, model: "Z" }], selected)).toContain("desempate por nombre");
    expect(selectionExplanation([{ ...selected, validation_wape: 0 }, selected], selected)).toContain("No hay evidencia comparable");
  });
});
describe("S20–S24 horizons and statistical chart", () => {
  it("S20/S21 literal H1/H12 WAPE; S22 null not zero", () => { const rows = horizonAccuracy(candidate); expect(rows[0]).toEqual({ horizon: 1, wape: 12.4 }); expect(rows[11].wape).toBe(23.4); expect(rows[2].wape).toBeNull(); render(<StatisticalHorizonAccuracy candidate={candidate}/>); expect(screen.getByText("12.4%")).toBeTruthy(); expect(screen.getByText("23.4%")).toBeTruthy(); expect(screen.getByText("—")).toBeTruthy(); const bars = JSON.parse(screen.getByTestId("bars").getAttribute("data-rows")!); expect(bars).toHaveLength(11); expect(bars.some((b: { horizon: number }) => b.horizon === 3)).toBe(false); });
  it("S23 absent horizons stay absent, missing evidence never backfilled", () => { expect(horizonAccuracy({ ...candidate, validation_by_horizon: [{ horizon: 1, wape: 0 }] })).toEqual([{ horizon: 1, wape: 0 }]); expect(horizonAccuracy()).toEqual([]); expect(horizonAccuracy({ ...candidate, validation_by_horizon: [{ horizon: 13, wape: 1 }] })).toEqual([]); });
  it("S24 exactly twelve literal future rows", () => { render(detail()); const title = screen.getByText("Forecast estadístico H1–H12"); const table = within(title.parentElement!); expect(table.getAllByText(/^H\d+$/)).toHaveLength(12); expect(table.getByText("Ago-2026")).toBeTruthy(); expect(table.getByText("Jul-2027")).toBeTruthy(); expect(table.getByText("7,420")).toBeTruthy(); });
  it("historical zero and gap preserved, cutoff backend, stat principal and Towell default off", () => { const points = traderPoints(history, [], horizons, scope.issue_period, filters); expect(points[0].sale).toBe(0); expect(points.find(p => p.period === "2026-06")?.sale).toBeNull(); render(<StatisticalForecastChart points={points} cutoff={scope.issue_period} compareTowell={false} onCompare={vi.fn()}/>); expect(screen.getByTestId("cutoff").getAttribute("data-period")).toBe("2026-07"); expect(screen.getByTestId("line-statistical").getAttribute("data-connect")).toBe("false"); expect(screen.getByTestId("line-sale").getAttribute("data-connect")).toBe("false"); expect(screen.queryByTestId("line-towell")).toBeNull(); expect(screen.getByRole("checkbox").getAttribute("checked")).toBeNull(); });
  it("Towell comparison only explicit; tooltips omit hidden/absent series", () => { const points = traderPoints(history, [], horizons, scope.issue_period, filters); render(<StatisticalForecastChart points={points} cutoff={scope.issue_period} compareTowell onCompare={vi.fn()}/>); expect(screen.getByTestId("line-towell")).toBeTruthy(); render(<StatisticalTooltip active payload={[{ payload: points.at(-1)! }]}/>); expect(screen.getByText(/Forecast estadístico: 7,431/)).toBeTruthy(); expect(screen.queryByText(/Fcst Towell:/)).toBeNull(); });
});
describe("S25–S38 scopes and eligibility", () => {
  it("S25/S26 distribution uses literal full-scope counts, most selected", () => { expect(statisticalModelDistribution(scope)[0]).toEqual({ model: "Naive", products: 12 }); render(detail(scope, { ...filters, productId: null })); expect(screen.getByText("Modelo más seleccionado")).toBeTruthy(); expect(screen.getByText("Distribución de modelos · scope completo")).toBeTruthy(); expect(screen.getByText(/Modelos disponibles: 14/)).toBeTruthy(); expect(screen.queryByText("Real vs Forecast Estadístico")).toBeNull(); });
  it("S27 evaluated denominator, not visible or sum of scopes", () => { expect(statisticalCoverage({ ...scope, eligibility: { evaluated: 45, visible_products: 99, stat_eligible: 20 } }).percent).toBeCloseTo(44.444); expect(statisticalCoverage({ ...scope, eligibility: { evaluated: 0, stat_eligible: 0 } }).percent).toBeNull(); render(detail(scope, { ...filters, productId: null })); expect(screen.getByText("44.44%")).toBeTruthy(); });
  it("S28/S29 scope metrics distinct from product metrics", () => { render(detail(scope, { ...filters, productId: null })); expect(screen.getByText("WAPE retrospectivo scope")).toBeTruthy(); expect(screen.getByText("55%")).toBeTruthy(); expect(screen.getByText("10%")).toBeTruthy(); });
  it.each([["ACTIVE", "Activo"], ["COLD_START", "Cold start"], ["INACTIVE", "Inactivo"], ["PRE_LAUNCH", "Pre-lanzamiento"], ["INSUFFICIENT", "Historia insuficiente"]])("S30–S34 %s real state and no fabricated forecast", (state, label) => { render(detail({ ...scope, products: [{ ...product, forecast_status: state, horizons: [] }] })); expect(screen.getByText(/Este producto no cuenta con evidencia suficiente/)).toBeTruthy(); expect(document.body.textContent).toContain(label); expect(screen.queryByText("Forecast estadístico H1–H12")).toBeNull(); });
  it("intermittent badge requires model AND matching classification", () => { const s = { ...scope, products: [{ ...product, horizons: horizons.map(h => ({ ...h, statistical_model: "SBA", classification: "Intermitente" })) }] }; const view = render(detail(s)); expect(screen.getByText("Demanda intermitente")).toBeTruthy(); view.rerender(detail({ ...s, products: [{ ...product, horizons: horizons.map(h => ({ ...h, statistical_model: "SBA", classification: "Regular" })) }] })); expect(screen.queryByText("Demanda intermitente")).toBeNull(); });
  it("ranking respects category/search/product and candidate requirement", () => { const s = { ...scope, products: [product, { ...product, product_id: "no-candidate" }] }; expect(statisticalRanking(s, { ...filters, productId: null })).toHaveLength(1); expect(statisticalRanking(s, { ...filters, categoryId: "other" })).toHaveLength(0); expect(statisticalRanking(s, { ...filters, search: "SKU" })).toHaveLength(1); expect(statisticalRanking(s, { ...filters, search: "absent" })).toHaveLength(0); expect(statisticalRanking({ ...s, chain_id: "foreign" }, filters)).toEqual([]); });
  it("S35/S36/S37/S38 independent scope rows, no global chart or sums, cuts preserved", () => { render(detail(scope, emptyFilters, [scope, { ...scope, chain_id: "child", chain_name: "Child", issue_period: "2026-06", eligibility: { evaluated: 10, stat_eligible: 2 } }])); expect(screen.getAllByRole("row")).toHaveLength(3); expect(screen.getByText("44.44%")).toBeTruthy(); expect(screen.getByText("20%")).toBeTruthy(); expect(screen.getByText(/CUTS_NOT_ALIGNED/)).toBeTruthy(); expect(screen.queryByRole("img")).toBeNull(); });
});
describe("S39–S49 routing, regression and isolation", () => {
  it("tabs never create new runs; summary intact; tab/compare survive filters/focus", async () => {
    const job: PreviewJob = { job_id: "job", engine_version: "E2", mode: "RETROSPECTIVE_TRAINING", status: "READY_PREVIEW", scopes: [scope] };
    const api = { latest: vi.fn(async () => job), create: vi.fn(), status: vi.fn(), result: vi.fn(), dispose: vi.fn() };
    const repository = { previews: api, getForecastHistory: vi.fn(async () => history) } as unknown as ForecastReadRepository;
    const harness = (f: Filters) => <ForecastFiltersContext.Provider value={{ repository, filters: f, setFilters: vi.fn(), profile: { id: "u", global_role: "ADMIN", full_name: "Test", status: "ACTIVE" } }}><ForecastEnginesView scope="Scope real"/></ForecastFiltersContext.Provider>;
    const view = render(harness(filters)); await screen.findByText("Forecast H1–H12"); expect(screen.getByText("Comparativa ejecutiva")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Motor Estadístico" })); await screen.findByText("Real vs Forecast Estadístico"); fireEvent.click(screen.getByRole("checkbox", { name: "Comparar con Fcst Towell" })); expect(screen.getByTestId("line-towell")).toBeTruthy();
    fireEvent(window, new Event("focus")); fireEvent(document, new Event("visibilitychange")); view.rerender(harness({ ...filters, periodRange: ["2026-05", "2026-06"] })); expect(screen.getByRole("tab", { name: "Motor Estadístico" }).getAttribute("aria-selected")).toBe("true"); expect(screen.getByTestId("line-towell")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Machine Learning" })); expect(screen.getByText(/Detalle ML disponible en siguiente fase/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Resumen" })); expect(screen.getByText("Comparativa ejecutiva")).toBeTruthy(); expect(api.create).not.toHaveBeenCalled(); expect(api.latest).toHaveBeenCalledTimes(1);
  });
  it("S48/S49 production statistical route has no demo or pilot imports/hardcodes", () => {
    const files = ["app/forecast-towell-app.tsx", "app/operational-engines-view.tsx", "lib/statistical-preview-data.ts", ...readdirSync("components/forecast").filter(n => n.endsWith(".tsx")).map(n => `components/forecast/${n}`)];
    for (const file of files) expect(readFileSync(file, "utf8")).not.toMatch(/forecast-demo\.json|total-fendi-bd|FENDI|Walmart|statistical-engine-view|ml-engine-view/);
  });
});
