import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ForecastTowellApp, { commercialScopeNavigation, ForecastFiltersContext, GlobalFilters } from "../../app/forecast-towell-app";
import { CalculationFilters } from "../../app/report-controls";
import { emptyFilters, type Chain, type CommercialClassification, type Filters, type ForecastReadRepository, type Product } from "../../lib/supabase/types";
import { emptyReportChoice, type ReportChoice, type ReportSnapshot } from "../../lib/forecast-reporting";
import { operationalContext } from "./operational-context-fixture";

vi.mock("../../app/forecast-assistant", () => ({ default: ({ authorized, rsAssistant }: { authorized: boolean; rsAssistant?: unknown }) => <><output data-testid="assistant-authorized">{String(authorized)}</output><output data-testid="assistant-rs-client">{String(Boolean(rsAssistant))}</output></> }));
vi.mock("../../app/commercial-history-view", () => ({ CommercialHistoryView: () => <div>Historical module</div> }));
vi.mock("../../app/forecast-periods-view", () => ({ ForecastPeriodsView: () => <div>Periods module</div> }));

const profile = { id: "synthetic-admin", full_name: "Synthetic", global_role: "ADMIN", status: "ACTIVE" } as const;
const parent: Chain = { id: "parent", code: "P", name: "Cadena acreditada", status: "ACTIVE", has_history: false, parentId: null, scopeType: "PARENT_CHAIN" };
const formats: Chain[] = Array.from({ length: 14 }, (_, i) => ({ ...parent, id: `format-${i}`, code: `F${i}`, name: `Formato ${i}`, parentId: parent.id, has_history: true, scopeType: "COMMERCIAL_UNIT" }));
// Synthetic catalogue independently contains all variants, even with no calculations.
const products: Product[] = Array.from({ length: 487 }, (_, i) => ({ id: `product-${i}`, chain_id: formats[i % 14].id, category_id: "category", product_code: `ITEM${i}`, variant_code: null, description: `Producto ${i}`, identifiers: [[`UPC${i}`, `ITEM${i}`]] }));
const classes: CommercialClassification[] = products.map((p, i) => ({ product_id: p.id, chain_id: p.chain_id, model: `MODELO ${i % 52}`, color: `COLOR ${i}`, size: "PRESENTACIÓN", purchase_type: "RS", product_type: "TOALLA", status: "PENDING_CLASSIFICATION", source: "SYNTHETIC", reasons: ["NOT_APPROVED"] }));
function repository() {
  return {
    openOperationalRead: vi.fn(async () => operationalContext),
    getVisibleChains: vi.fn(async () => [parent, ...formats]),
    getCommercialCatalog: vi.fn(async () => classes),
    getProducts: vi.fn(async (id?: string | null, _category?: string | null, search = "") => products.filter(p => (!id || p.chain_id === id) && (!search || p.identifiers.some(([upc]) => upc === search)))),
    getCategories: vi.fn(async () => [{ id: "category", chain_id: formats[0].id, name: "Categoría original" }]),
    getReportPeriods: vi.fn(async (id?: string | null) => { void id; return ["2025-01", "2026-10", "2026-11"]; }),
    getHistoricalSummary: vi.fn(async () => ({ scopeCount: 0, productCount: 0, observationCount: 0, latestPeriod: null, byScope: [] })),
    previews: { latest: vi.fn(async (id?: string | null) => { void id; return null; }), create: vi.fn(), research: vi.fn(), selectCalculation: vi.fn(), rsAssistant: vi.fn() },
  };
}
function harness(repo: ReturnType<typeof repository>, initial: Filters = { ...emptyFilters, purchaseType: "RS" }, compact = false) {
  function Harness() {
    const [filters, setFilters] = useState<Filters>(initial);
    return <ForecastFiltersContext.Provider value={{ repository: repo as unknown as ForecastReadRepository, filters, setFilters, profile }}><GlobalFilters compact={compact}/><output data-testid="filters">{JSON.stringify(filters)}</output></ForecastFiltersContext.Provider>;
  }
  return render(<Harness/>);
}
async function pick(label: string, name: string | RegExp) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name }));
}
const current = () => JSON.parse(screen.getByTestId("filters").textContent!) as Filters;

describe("Commercial navigation without implicit scope rollups", () => {
  it("uses existing hierarchy only and never invents an absent parent", () => {
    expect(commercialScopeNavigation([parent, ...formats], parent.id)).toMatchObject({ parentId: parent.id, formats });
    const ownHistory = { ...parent, has_history: true };
    // The all-formats union follows the existing leaf policy; a parent total
    // cannot be added again on top of its child formats.
    expect(commercialScopeNavigation([ownHistory, ...formats], parent.id)).toMatchObject({ formats, scopeIds: formats.map(c => c.id) });
    expect(commercialScopeNavigation([ownHistory], parent.id)).toMatchObject({ formats: [ownHistory], scopeIds: [ownHistory.id] });
    expect(commercialScopeNavigation([formats[0]], formats[0].id)).toMatchObject({ parentId: formats[0].id, chains: [formats[0]], formats: [formats[0]] });
  });
  it("lists all 52 models and 487 products, including models without any forecast, without a default model", async () => {
    const repo = repository(); harness(repo);
    await screen.findByText(/487 productos coinciden/);
    expect(screen.getByRole("combobox", { name: "Tipo de compra" }).textContent).toContain("RS");
    expect(current().chainId).toBeNull(); expect(current().commercialModels).toBeUndefined();
    await userEvent.click(screen.getByLabelText("Modelo"));
    expect(within(screen.getByRole("group", { name: "Modelos disponibles" })).getAllByRole("checkbox")).toHaveLength(52);
    expect(repo.getCommercialCatalog).toHaveBeenCalledWith(operationalContext);
    expect(repo.previews.create).not.toHaveBeenCalled(); expect(repo.previews.research).not.toHaveBeenCalled();
  });
  it("keeps all Formats optional and resets incompatible filters without opening other chains", async () => {
    const repo = repository();
    harness(repo, { ...emptyFilters, purchaseType: "RS", chainId: formats[0].id, productId: products[0].id, categoryId: "category", search: "UPC0", commercialModels: ["MODELO 0"], commercialColor: "COLOR 0", commercialSize: "PRESENTACIÓN", classificationStatus: "APPROVED", periodRange: ["2026-10", "2026-11"] });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Cadena" }).hasAttribute("disabled")).toBe(false));
    await pick("Formato", "Formato 1");
    await waitFor(() => expect(current()).toMatchObject({ ...emptyFilters, purchaseType: "RS", chainId: formats[1].id }));
    expect(current().commercialModels ?? null).toBeNull();
    const beforeAll = repo.getProducts.mock.calls.length;
    await userEvent.click(screen.getByRole("combobox", { name: "Formato" }));
    expect(screen.getAllByRole("option")).toHaveLength(15); // all + 14 exact UUIDs
    await userEvent.click(screen.getByRole("option", { name: /Todos los formatos/i }));
    await screen.findByText(/487 productos coinciden/);
    expect(current().chainId).toBe(parent.id);
    expect(new Set(current().scopeChainIds)).toEqual(new Set(formats.map(c => c.id)));
    expect(screen.getByRole("combobox", { name: "Producto" }).hasAttribute("disabled")).toBe(false);
    const selectedScopes = repo.getProducts.mock.calls.slice(beforeAll).map(([id]) => id);
    expect(new Set(selectedScopes)).toEqual(new Set(formats.map(c => c.id)));
    expect(selectedScopes).not.toContain(null);
    expect(repo.getReportPeriods.mock.calls.every(([id]) => id !== parent.id)).toBe(true);
  });
  it("clears hidden model searches across formats and product searches on model selection", async () => {
    const repo = repository(); harness(repo, { ...emptyFilters, purchaseType: "RS", chainId: formats[0].id, search: "UPC0" });
    await screen.findByText(/1 productos coinciden/);
    await userEvent.click(screen.getByLabelText("Modelo"));
    // Searching one UPC must not hide the scope's other models.
    const alternative = () => within(screen.getByRole("group", { name: "Modelos disponibles" })).getByText("MODELO 14").closest("label")!.querySelector("input")!;
    expect(alternative()).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Limpiar selección" }));
    await userEvent.click(alternative());
    expect(current().search).toBe("");
    expect(current().commercialModels).toEqual(["MODELO 14"]);
    await userEvent.type(screen.getByLabelText("Buscar modelos"), "MODELO 0");
    await userEvent.click(screen.getByRole("button", { name: "Limpiar selección" }));
    expect(current().search).toBe("");
    await pick("Formato", "Formato 1");
    await waitFor(() => expect((screen.getByLabelText("Buscar modelos") as HTMLInputElement).value).toBe(""));
    expect(current().commercialModels).toBeUndefined();
    expect(current().chainId).toBe(formats[1].id);
  });
  it("surfaces category, classification, product search and period filters while collapsed", async () => {
    harness(repository(), { ...emptyFilters, purchaseType: "RS", categoryId: "category", search: "UPC0", classificationStatus: "PENDING_CLASSIFICATION", periodRange: ["2026-10", "2026-11"] }, true);
    await screen.findByText(/Categoría: Categoría original/);
    expect(screen.getByText(/Búsqueda: UPC0/).closest("[hidden]")).toBeNull();
    expect(screen.getByText(/Clasificación: Pendiente/).closest("[hidden]")).toBeNull();
    expect(screen.getAllByText(/Periodo 2026-10 a 2026-11/)[0].closest("[hidden]")).toBeNull();
    expect(screen.getByRole("button", { name: "Filtros" }).getAttribute("aria-expanded")).toBe("false");
  });
  it("allows a parent with all Formats to consult data without an obligatory Format or engine call", async () => {
    const repo = repository();
    const otherScope = { ...parent, id: "other-chain", name: "Otra cadena", has_history: true };
    const otherProduct = { ...products[0], id: "other-product", chain_id: otherScope.id };
    repo.getVisibleChains.mockResolvedValue([parent, ...formats, otherScope]);
    repo.getCommercialCatalog.mockResolvedValue([...classes, { ...classes[0], product_id: otherProduct.id, chain_id: otherScope.id, model: "ONLY OTHER CHAIN" }]);
    repo.getProducts.mockImplementation(async id => [...products, otherProduct].filter(p => !id || p.chain_id === id));
    render(<ForecastTowellApp profile={profile} repository={repo as unknown as ForecastReadRepository} onLogout={vi.fn()}/>);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Cadena" }).hasAttribute("disabled")).toBe(false));
    await userEvent.click(screen.getByRole("button", { name: "Histórico" }));
    await screen.findByText("Historical module");
    await pick("Cadena", parent.name);
    await screen.findByText(/487 productos coinciden/);
    expect(screen.getByText("Historical module")).toBeTruthy();
    expect(screen.queryByText(/Selecciona un formato comercial para consultar sus productos/)).toBeNull();
    await userEvent.click(screen.getByLabelText("Modelo"));
    expect(within(screen.getByRole("group", { name: "Modelos disponibles" })).getAllByRole("checkbox")).toHaveLength(52);
    expect(within(screen.getByRole("group", { name: "Modelos disponibles" })).queryByText("ONLY OTHER CHAIN")).toBeNull();
    await pick("Formato", formats[1].name);
    await screen.findByText("Historical module");
    expect(screen.getByTestId("assistant-authorized").textContent).toBe("true");
    expect(repo.previews.create).not.toHaveBeenCalled();
    expect(repo.previews.research).not.toHaveBeenCalled();
  });
  it("does not reveal late RS catalogue data after switching to CE, and preserves CE on chain changes", async () => {
    const repo = repository();
    let resolveProducts!: (value: Product[]) => void;
    const delayed = new Promise<Product[]>(resolve => { resolveProducts = resolve; });
    repo.getProducts.mockImplementation(() => delayed);
    harness(repo);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Tipo de compra" }).hasAttribute("disabled")).toBe(false));
    await pick("Tipo de compra", /^CE/);
    resolveProducts(products);
    await screen.findByText(/0 productos coinciden/);
    expect(current().purchaseType).toBe("CE");
    await userEvent.click(screen.getByLabelText("Modelo"));
    expect(within(screen.getByRole("group", { name: "Modelos disponibles" })).queryAllByRole("checkbox")).toEqual([]);
    await pick("Cadena", parent.name);
    await waitFor(() => expect(current().chainId).toBe(parent.id));
    expect(current().purchaseType).toBe("CE");
    expect(current().commercialModels ?? null).toBeNull();
    expect(current().productId).toBeNull();
    expect(current().search).toBe("");
  });
  it("switches RS to CE without retaining RS models, forecasts or assistant actions", async () => {
    const repo = repository(); render(<ForecastTowellApp profile={profile} repository={repo as unknown as ForecastReadRepository} onLogout={vi.fn()}/>);
    await screen.findByText(/487 productos coinciden/);
    await userEvent.click(screen.getByRole("button", { name: "Histórico" }));
    await screen.findByText("Historical module");
    const beforeCE = repo.previews.latest.mock.calls.length;
    await pick("Tipo de compra", /^CE/);
    await screen.findByText(/0 productos coinciden/);
    expect(screen.queryByText("Historical module")).toBeNull();
    expect(screen.getByText(/Sin datos de Compra Especial/)).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Tipo de compra" }).textContent).toContain("CE");
    expect(screen.getByTestId("assistant-rs-client").textContent).toBe("false");
    expect(repo.previews.latest.mock.calls.slice(beforeCE)).toEqual([]);
    await userEvent.click(screen.getByLabelText("Modelo"));
    expect(within(screen.getByRole("group", { name: "Modelos disponibles" })).queryAllByRole("checkbox")).toEqual([]);
    await pick("Tipo de compra", /^RS/);
    await screen.findByText(/487 productos coinciden/);
    await screen.findByText("Historical module");
    expect(repo.previews.create).not.toHaveBeenCalled();
    expect(repo.previews.research).not.toHaveBeenCalled();
  });
});

describe("Technical calculation choices", () => {
  const snap = { calculations: [{ id: "existing", calculation_code: "C1", issue_period: "2026-10" }], provisionals: [] } as unknown as ReportSnapshot;
  it("clears choices absent from the new scope while preserving valid choices", async () => {
    const onChange = vi.fn();
    const invalid: ReportChoice = { ...emptyReportChoice, version: "C2", issue: "2026-07", calculationId: "foreign", decision: "PENDING" };
    const view = render(<CalculationFilters snapshot={snap} choice={invalid} onChange={onChange}/>);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ version: null, issue: null, calculationId: null, decision: "PENDING" }));
    onChange.mockClear();
    view.rerender(<CalculationFilters snapshot={snap} choice={{ ...emptyReportChoice, version: "C1", issue: "2026-10", calculationId: "existing" }} onChange={onChange}/>);
    expect(Reflect.get(screen.getByRole("combobox", { name: "Cálculo técnico" }), "value")).toBe("C1");
    expect(onChange).not.toHaveBeenCalled();
  });
});
