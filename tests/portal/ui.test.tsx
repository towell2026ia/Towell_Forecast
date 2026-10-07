import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ForecastTowellApp, { ForecastFiltersContext, GlobalFilters, HistoryView, ReadState, UsersView } from "../../app/forecast-towell-app";
import { emptyFilters, type ForecastReadRepository, type Profile, type Filters, type Role } from "../../lib/supabase/types";

const chain1 = { id: "chain-a", code: "A", name: "Cadena dinámica A", has_history: true, status: "ACTIVE", parentId: null, scopeType: "PARENT_CHAIN" };
const chain2 = { ...chain1, id: "chain-b", code: "B", name: "Unidad dinámica B", parentId: "chain-a", scopeType: "COMMERCIAL_UNIT" };
const product = { id: "product-a", chain_id: chain1.id, category_id: "cat-a", product_code: "ITEM-ABC", variant_code: null, description: "Producto desde RLS", identifiers: [["ITEM-ABC", "UPC-XYZ"]] as [string, string][] };
function fixture(role: Role = "ADMIN"): ForecastReadRepository {
  const chains = role === "ADMIN" ? [chain1, chain2] : [chain1];
  const profile: Profile = { id: "fixture-user", full_name: "Usuario real simulado", global_role: role, status: "ACTIVE" };
  return {
    getCurrentProfile: vi.fn(async () => profile),
    getVisibleChains: vi.fn(async () => chains),
    getCategories: vi.fn(async () => [{ id: "cat-a", chain_id: chain1.id, name: "Categoría desde RLS" }]),
    getProducts: vi.fn(async () => [product]), getPeriods: vi.fn(async () => ["2025-02", "2026-03"]),
    getHistoricalSummary: vi.fn(async () => ({ scopeCount: chains.length, productCount: 1, observationCount: 3, latestPeriod: "2026-03", byScope: chains.map(chain => ({ chain, observations: 3 })) })),
    getForecastHistory: vi.fn(async () => [{ period: "2026-03", sale: 1, order: 2, delivery: 3 }]),
    getHistoricalObservations: vi.fn(async (_filters, page) => ({ rows: [{ chain_id: chain1.id, product_id: product.id, period: "2026-03-01", chain: chain1.name, product, SALES: 0, ORDER: 12, DELIVERY: null, availability: "UNKNOWN" }], next: page.cursor ? null : { chain_id: chain1.id, product_id: product.id, period: "2026-03-01" }, observationCount: 3 })),
    getProfiles: vi.fn(async () => [profile]), dispose: vi.fn(),
  };
}
function filterHarness(repository: ForecastReadRepository, children: React.ReactNode, initial: Filters = { ...emptyFilters }) {
  function Wrapper() {
    const [filters, setFilters] = React.useState(initial);
    return <ForecastFiltersContext.Provider value={{ repository, filters, setFilters, profile: { id: "fixture-user", full_name: "Usuario", global_role: "ADMIN", status: "ACTIVE" } }}>{children}<output data-testid="filters">{JSON.stringify(filters)}</output></ForecastFiltersContext.Provider>;
  }
  return render(<Wrapper/>);
}
async function pick(label: string, option: string) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}
describe("Multi-chain read-only UI", () => {
  it("PF04/PF05 all chains -> selected chain -> all chains updates real period options", async () => {
    const repo = fixture(); repo.getPeriods = vi.fn(async id => id ? ["2026-03"] : ["2025-02", "2026-03"]);
    filterHarness(repo, <GlobalFilters/>);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Periodo desde" }).hasAttribute("disabled")).toBe(false));
    await pick("Periodo desde", "2025-02");
    await pick("Cadena / unidad comercial", chain1.name);
    await waitFor(() => expect(repo.getPeriods).toHaveBeenLastCalledWith(chain1.id));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Periodo desde" }).hasAttribute("disabled")).toBe(false));
    await userEvent.click(screen.getByRole("combobox", { name: "Periodo desde" }));
    expect(await screen.findByRole("option", { name: "2026-03" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "2025-02" })).toBeNull(); await userEvent.keyboard("{Escape}");
    await pick("Cadena / unidad comercial", "Todas las cadenas");
    await waitFor(() => expect(repo.getPeriods).toHaveBeenLastCalledWith(null));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Periodo desde" }).hasAttribute("disabled")).toBe(false));
    await userEvent.click(screen.getByRole("combobox", { name: "Periodo desde" }));
    expect(await screen.findByRole("option", { name: "2025-02" })).toBeTruthy();
    expect(screen.queryByText("No fue posible consultar los datos.")).toBeNull();
  });
  it("PF12/PF14 period failure leaves Dashboard visible and disables only period selectors", async () => {
    const repo = fixture(); repo.getPeriods = vi.fn(async () => { throw new Error("private period failure"); });
    render(<ForecastTowellApp profile={{ id: "u", full_name: "Usuario", global_role: "ADMIN", status: "ACTIVE" }} repository={repo} onLogout={vi.fn()}/>);
    expect(await screen.findByText("No fue posible cargar los periodos.")).toBeTruthy();
    await screen.findByText("Último periodo disponible"); expect(screen.getByText("2026-03")).toBeTruthy();
    for (const label of ["Periodo desde", "Periodo hasta"]) expect(screen.getByRole("combobox", { name: label }).hasAttribute("disabled")).toBe(true);
    for (const label of ["Cadena / unidad comercial", "Categoría", "Producto"]) expect(screen.getByRole("combobox", { name: label }).hasAttribute("disabled")).toBe(false);
    expect(screen.queryByText("No fue posible consultar los datos.")).toBeNull(); expect(screen.queryByText("private period failure")).toBeNull();
  });
  it("PF13/PF15 periods failing keeps History, chain/category/product and pagination usable", async () => {
    const repo = fixture(); repo.getPeriods = vi.fn(async () => { throw new Error("period failure"); });
    filterHarness(repo, <><GlobalFilters/><HistoryView/></>);
    await screen.findByText("No fue posible cargar los periodos.");
    await screen.findByText("Producto desde RLS");
    await pick("Cadena / unidad comercial", chain1.name);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Categoría" }).hasAttribute("disabled")).toBe(false));
    await pick("Categoría", "Categoría desde RLS");
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Producto" }).hasAttribute("disabled")).toBe(false));
    await pick("Producto", "Producto desde RLS · ITEM-ABC");
    await waitFor(() => expect(screen.getByRole("button", { name: "Siguiente" }).hasAttribute("disabled")).toBe(false));
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" })); await screen.findByText("Página 2");
    await userEvent.click(screen.getByRole("button", { name: "Anterior" })); await screen.findByText("Página 1");
    expect(screen.queryByText("No fue posible consultar los datos.")).toBeNull();
  });
  for (const [method, label, message] of [["getVisibleChains", "Cadena / unidad comercial", "las cadenas"], ["getCategories", "Categoría", "las categorías"], ["getProducts", "Producto", "los productos"]] as const) {
    it(`isolates ${message} failure to its own control`, async () => {
      const repo = fixture(); repo[method] = vi.fn(async () => { throw new Error("controlled read failure"); });
      filterHarness(repo, <GlobalFilters/>);
      await screen.findByText(`No fue posible cargar ${message}.`);
      expect(screen.getByRole("combobox", { name: label }).hasAttribute("disabled")).toBe(true);
      for (const other of ["Cadena / unidad comercial", "Categoría", "Producto", "Periodo desde", "Periodo hasta"].filter(item => item !== label)) expect(screen.getByRole("combobox", { name: other }).hasAttribute("disabled")).toBe(false);
      expect(screen.queryByText("No fue posible consultar los datos.")).toBeNull();
    });
  }
  it("UI07 chains are dynamic and hierarchy comes from evidence", async () => {
    filterHarness(fixture(), <GlobalFilters/>);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Cadena / unidad comercial" }).hasAttribute("disabled")).toBe(false));
    await pick("Cadena / unidad comercial", "Cadena dinámica A → Unidad dinámica B");
    expect(screen.getByTestId("filters").textContent).toContain("chain-b");
  });
  it("UI08 no hardcoded pilot footer or header", async () => {
    render(<ForecastTowellApp profile={{ id: "u", full_name: "Usuario", global_role: "ADMIN", status: "ACTIVE" }} repository={fixture()} onLogout={vi.fn()}/>);
    expect(screen.queryByText("FENDI BD")).toBeNull(); expect(screen.queryByText("Piloto seleccionado")).toBeNull();
  });
  it("UI09 production product list comes from repository", async () => {
    filterHarness(fixture(), <GlobalFilters/>);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Producto" }).hasAttribute("disabled")).toBe(false));
    await pick("Producto", "Producto desde RLS · ITEM-ABC");
    expect(screen.getByTestId("filters").textContent).toContain("product-a");
  });
  it("selecting a product from Todas attaches its authoritative chain before a preview request", async () => {
    filterHarness(fixture(), <GlobalFilters/>);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Producto" }).hasAttribute("disabled")).toBe(false));
    await pick("Producto", "Producto desde RLS · ITEM-ABC");
    const selected = JSON.parse(screen.getByTestId("filters").textContent ?? "{}") as Filters;
    expect(selected.chainId).toBe(chain1.id);
    expect(selected.productId).toBe(product.id);
  });
  it("UI10 changing chain resets stale product and category", async () => {
    const repo = fixture(); filterHarness(repo, <GlobalFilters/>, { ...emptyFilters, productId: "old", categoryId: "old" });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Cadena / unidad comercial" }).hasAttribute("disabled")).toBe(false));
    await pick("Cadena / unidad comercial", chain1.name);
    await waitFor(() => expect(repo.getProducts).toHaveBeenCalledWith("chain-a", null, ""));
    expect(screen.getByTestId("filters").textContent).toContain('"productId":null');
  });
  it("UI11 category selector reacts to chain", async () => {
    const repo = fixture(); filterHarness(repo, <GlobalFilters/>);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Cadena / unidad comercial" }).hasAttribute("disabled")).toBe(false));
    await pick("Cadena / unidad comercial", chain1.name);
    await waitFor(() => expect(repo.getCategories).toHaveBeenCalledWith(chain1.id));
    await pick("Categoría", "Categoría desde RLS");
    await waitFor(() => expect(repo.getProducts).toHaveBeenCalledWith(chain1.id, "cat-a", ""));
  });
  it("PH24/UI12 tuple pagination defaults to 50, advances and returns to previous page", async () => {
    const repo = fixture(); filterHarness(repo, <HistoryView/>);
    await screen.findByText("Producto desde RLS");
    expect(repo.getHistoricalObservations).toHaveBeenCalledWith(emptyFilters, { size: 50, cursor: null });
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await waitFor(() => expect(repo.getHistoricalObservations).toHaveBeenLastCalledWith(emptyFilters, { size: 50, cursor: expect.any(Object) }));
    await userEvent.click(screen.getByRole("button", { name: "Anterior" }));
    await waitFor(() => expect(repo.getHistoricalObservations).toHaveBeenLastCalledWith(emptyFilters, { size: 50, cursor: null }));
  });
  it("UI13 historical filters passed upstream and zero is not missing", async () => {
    const repo = fixture(), filters: Filters = { ...emptyFilters, chainId: chain1.id, productId: product.id, periodRange: ["2026-03", "2026-03"] };
    filterHarness(repo, <HistoryView/>, filters); await screen.findByText("Producto desde RLS");
    expect(repo.getHistoricalObservations).toHaveBeenCalledWith(filters, { size: 50, cursor: null });
    expect(screen.getByText("0")).toBeTruthy(); expect(screen.getByText("Sin dato")).toBeTruthy();
  });
  it("UI14 empty state explicit", () => { render(<ReadState loading={false} error={false} empty/>); expect(screen.getByText("No hay histórico certificado para este filtro.")).toBeTruthy(); });
  it("UI15 controlled error state, no demo", () => { render(<ReadState loading={false} error/>); expect(screen.getByRole("alert").textContent).toBe("No fue posible consultar los datos."); });
  for (const [id, role] of [["UI16", "VIEWER"], ["UI17", "EDITOR"]] as const) {
    it(`${id} ${role} scope isolation and admin module hidden`, async () => {
      const repo = fixture(role); render(<ForecastTowellApp profile={{ id: "u", full_name: "Usuario", global_role: role, status: "ACTIVE" }} repository={repo} onLogout={vi.fn()}/>);
      expect(screen.queryByText("Usuarios")).toBeNull();
      await waitFor(() => expect(screen.getByRole("combobox", { name: "Cadena / unidad comercial" }).hasAttribute("disabled")).toBe(false));
      await userEvent.click(screen.getByRole("combobox", { name: "Cadena / unidad comercial" }));
      expect(screen.queryByRole("option", { name: /Unidad dinámica B/ })).toBeNull();
    });
  }
  it("PH22/UI18 ADMIN dashboard loads real repository summaries and authorized scopes", async () => {
    render(<ForecastTowellApp profile={{ id: "u", full_name: "Usuario", global_role: "ADMIN", status: "ACTIVE" }} repository={fixture()} onLogout={vi.fn()}/>);
    expect(screen.getByText("Usuarios")).toBeTruthy();
    await screen.findByText(chain2.name);
    expect(await screen.findByRole("combobox", { name: "Cadena mostrada en la gráfica" })).toBeTruthy();
    expect(screen.getByRole("img", { name: /Evolución temporal/ })).toBeTruthy();
  });
  it("UI19 users read real profiles; no simulated create button", async () => {
    filterHarness(fixture(), <UsersView/>);
    expect(await screen.findByText("Usuario real simulado")).toBeTruthy();
    expect(screen.queryByText("Crear usuario")).toBeNull();
  });
  it("UI20 failed production read shows error rather than fixture fallback", async () => {
    const repo = fixture(); repo.getHistoricalObservations = vi.fn(async () => { throw new Error("network"); });
    filterHarness(repo, <HistoryView/>); expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText("Producto desde RLS")).toBeNull();
  });
});
