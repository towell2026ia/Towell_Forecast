import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { ForecastReadRepository, Profile } from "../../lib/supabase/types";
import { readFileSync } from "node:fs";

const state = vi.hoisted(() => ({ create: vi.fn(), mount: vi.fn(), unmount: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: state.replace }) }));
vi.mock("next/dynamic", () => ({ default: () => function Animation() { return <svg data-testid="session-lottie"/>; } }));
vi.mock("../../lib/forecast-data", async importOriginal => {
  const original = await importOriginal<typeof import("../../lib/forecast-data")>();
  return { ...original, SupabaseForecastReadRepository: class {
    constructor(_client: unknown, userId: string) { return state.create(userId); }
  } };
});
vi.mock("../../app/forecast-towell-app", async importOriginal => {
  const original = await importOriginal<typeof import("../../app/forecast-towell-app")>();
  return { ...original, default: function ObservedShell(props: React.ComponentProps<typeof original.default>) {
    React.useEffect(() => { state.mount(); return () => { state.unmount(); }; }, []);
    return <original.default {...props}/>;
  } };
});
import PortalAuth from "../../app/portal-auth";
const config = { url: "https://example.supabase.co", key: "sb_publishable_synthetic_test_key", siteUrl: "https://canonical.example.test" };
const chain = { id: "chain-1", code: "C1", name: "Cadena seleccionada", status: "ACTIVE", has_history: true, parentId: null, scopeType: "PARENT_CHAIN" };
const product = { id: "product-1", chain_id: chain.id, category_id: "category-1", product_code: "ITEM-X", variant_code: null, description: "Producto X", identifiers: [] };
let repos: ForecastReadRepository[];
beforeEach(() => {
  vi.clearAllMocks(); repos = [];
  state.create.mockImplementation((id: string) => {
    const profile: Profile = { id, full_name: "Fixture " + id, global_role: "ADMIN", status: "ACTIVE" };
    const repo: ForecastReadRepository = {
      getCurrentProfile: vi.fn(async () => profile), getVisibleChains: vi.fn(async () => [chain]),
      getCategories: vi.fn(async () => [{ id: "category-1", chain_id: chain.id, name: "Categoría seleccionada" }]),
      getProducts: vi.fn(async () => [product]), getPeriods: vi.fn(async () => ["2026-01", "2026-07"]),
      getHistoricalSummary: vi.fn(async () => ({ scopeCount: 1, productCount: 1, observationCount: 6, latestPeriod: "2026-07", byScope: [{ chain, observations: 6 }] })),
      getHistoricalObservations: vi.fn(async (_filters, page) => ({ rows: [{ chain_id: chain.id, product_id: product.id, period: page.cursor ? "2026-07-01" : "2026-01-01", product, chain: chain.name, SALES: 1, ORDER: 2, DELIVERY: 3, availability: "UNKNOWN" }], next: page.cursor ? null : { chain_id: chain.id, product_id: product.id, period: "2026-01-01" }, observationCount: 6 })),
      getProfiles: vi.fn(async () => [profile]), dispose: vi.fn(),
    };
    repos.push(repo); return repo;
  });
});
function sdk(initialId: string | null = "user-1", initialEvent = false) {
  let id = initialId;
  let callback: (event: string, session: { user: { id: string } } | null) => void = () => {};
  const auth = {
    getUser: vi.fn(async () => ({ data: { user: id ? { id } : null }, error: null })),
    onAuthStateChange: vi.fn((cb: typeof callback) => { callback = cb; if (initialEvent) cb("INITIAL_SESSION", id ? { user: { id } } : null); return { data: { subscription: { unsubscribe: vi.fn() } } }; }),
    signInWithPassword: vi.fn(async () => { id = "user-1"; callback("SIGNED_IN", { user: { id } }); return { error: null }; }),
    signOut: vi.fn(async () => { id = null; callback("SIGNED_OUT", null); return { error: null }; }),
  };
  return { client: { auth } as unknown as SupabaseClient, auth,
    emit: (event: string, nextId = id) => { id = event === "SIGNED_OUT" ? null : nextId; callback(event, id ? { user: { id } } : null); },
    hint: (event: string, eventId: string) => callback(event, { user: { id: eventId } }),
  };
}
async function mounted(s = sdk()) {
  const view = render(<PortalAuth config={config} providedClient={s.client}/>);
  await screen.findByRole("button", { name: "Histórico" });
  await waitFor(() => expect(state.mount).toHaveBeenCalledOnce());
  return { s, view };
}
async function pick(label: string, option: string) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: option }));
}
async function historyState() {
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Cadena / unidad comercial" }).hasAttribute("disabled")).toBe(false));
  await pick("Cadena / unidad comercial", chain.name);
  await pick("Categoría", "Categoría seleccionada");
  await userEvent.type(screen.getByLabelText("Buscar producto, ITEM, UPC o variante"), "ITEM");
  await pick("Producto", "Producto X · ITEM-X");
  await pick("Periodo desde", "2026-01"); await pick("Periodo hasta", "2026-07");
  await userEvent.click(screen.getByRole("button", { name: "Histórico" }));
  await screen.findByRole("button", { name: "Siguiente" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Siguiente" }).hasAttribute("disabled")).toBe(false));
  await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
  await screen.findByText("Página 2");
}
function expectStable(repo = repos[0]) {
  expect(state.create).toHaveBeenCalledOnce(); expect(state.mount).toHaveBeenCalledOnce();
  expect(state.unmount).not.toHaveBeenCalled(); expect(repo.dispose).not.toHaveBeenCalled();
  expect(screen.queryByText("Verificando sesión…")).toBeNull();
}
async function focus() { await act(async () => { window.dispatchEvent(new Event("blur")); document.dispatchEvent(new Event("visibilitychange")); window.dispatchEvent(new Event("focus")); }); }

describe("SS identity-scoped session stability", () => {
  it("SS01 initial session and concurrent INITIAL_SESSION create one repository", async () => { await mounted(sdk("user-1", true)); expectStable(); });
  it("SS02 focus creates zero additional repositories", async () => { await mounted(); await focus(); expectStable(); });
  it("SS03 blur/focus causes zero dispose calls", async () => { await mounted(); await focus(); await focus(); expectStable(); });
  for (const [cp, label, value] of [["SS05", "Cadena / unidad comercial", chain.name], ["SS06", "Producto", "Producto X"], ["SS07", "Periodo desde", "2026-01"], ["SS07B", "Periodo hasta", "2026-07"], ["SS07C", "Categoría", "Categoría seleccionada"]]) {
    it(`${cp} focus preserves ${label}`, async () => { await mounted(); await historyState(); await focus(); expect(screen.getByRole("combobox", { name: label }).textContent).toContain(value); expectStable(); });
  }
  it("SS04 focus preserves active module", async () => { await mounted(); await historyState(); await focus(); expect(screen.getByRole("heading", { name: "Histórico operativo" })).toBeTruthy(); expectStable(); });
  it("SS08 focus preserves page 2 and search without another historical read", async () => {
    await mounted(); await historyState(); const calls = vi.mocked(repos[0].getHistoricalObservations).mock.calls.length;
    await focus(); expect(screen.getByText("Página 2")).toBeTruthy();
    expect((screen.getByLabelText("Buscar producto, ITEM, UPC o variante") as HTMLInputElement).value).toBe("ITEM");
    expect(repos[0].getHistoricalObservations).toHaveBeenCalledTimes(calls); expectStable();
  });
  it("SS09/SS10 TOKEN_REFRESHED causes zero remount/dispose and keeps assistant open", async () => {
    const { s } = await mounted(); await historyState();
    await userEvent.click(screen.getByRole("button", { name: "Abrir Asistente FORECAST Towell" }));
    const dialog = screen.getByRole("dialog"); await act(async () => { s.emit("TOKEN_REFRESHED"); });
    expect(screen.getByRole("dialog")).toBe(dialog); expectStable();
    expect(screen.getByText("Página 2")).toBeTruthy();
  });
  it("SS11/SS12 same-user SIGNED_IN keeps shell, repository and pagination", async () => {
    const { s } = await mounted(); await historyState();
    await act(async () => { s.emit("SIGNED_IN"); s.emit("INITIAL_SESSION"); });
    expect(screen.getByText("Página 2")).toBeTruthy(); expectStable();
  });
  it("SS13 SIGNED_OUT disposes exactly once, including eventual unmount", async () => {
    const { s, view } = await mounted(); await act(async () => { s.emit("SIGNED_OUT"); s.emit("SIGNED_OUT"); });
    expect(repos[0].dispose).toHaveBeenCalledOnce(); expect(screen.queryByRole("button", { name: "Abrir Asistente FORECAST Towell" })).toBeNull();
    expect(state.replace).toHaveBeenCalledWith("/login"); view.unmount(); expect(repos[0].dispose).toHaveBeenCalledOnce();
  });
  it("SS14/SS15 genuine identity change disposes old repo and creates one new repo", async () => {
    const { s } = await mounted(); await historyState();
    await act(async () => { s.emit("SIGNED_IN", "user-2"); s.emit("SIGNED_IN", "user-2"); });
    await waitFor(() => expect(state.create).toHaveBeenCalledTimes(2));
    await screen.findByText("Fixture user-2");
    expect(repos[0].dispose).toHaveBeenCalledOnce(); expect(repos[1].dispose).not.toHaveBeenCalled();
    expect(state.create).toHaveBeenLastCalledWith("user-2"); expect(state.mount).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Página 2")).toBeNull(); expect(screen.queryByText("Fixture user-1")).toBeNull();
  });
  it("SS16 recovery routes safely without entering portal with recovery credentials", async () => {
    const { s, view } = await mounted(); await act(async () => { s.emit("PASSWORD_RECOVERY"); s.emit("TOKEN_REFRESHED"); });
    expect(state.replace).toHaveBeenCalledWith("/update-password"); expect(state.create).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Histórico" })).toBeNull(); view.unmount(); expect(repos[0].dispose).toHaveBeenCalledOnce();
    sessionStorage.clear();
  });
  it("SS17 first login creates repository once and routes to portal", async () => {
    const s = sdk(null); render(<PortalAuth login config={config} providedClient={s.client}/>);
    await userEvent.type(await screen.findByLabelText("Correo electrónico"), "fixture@example.test");
    await userEvent.type(screen.getByLabelText("Contraseña"), "synthetic-only-password");
    await userEvent.click(screen.getByRole("button", { name: "Iniciar sesión" }));
    await waitFor(() => expect(state.replace).toHaveBeenCalledWith("/")); expect(state.create).toHaveBeenCalledOnce();
  });
  it("SS18 logout clears portal and assistant, disposes once", async () => {
    const { s } = await mounted(); await userEvent.click(screen.getByRole("button", { name: /Cerrar sesión/ }));
    expect(s.auth.signOut).toHaveBeenCalledWith({ scope: "local" }); expect(repos[0].dispose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Abrir Asistente FORECAST Towell" })).toBeNull();
  });
  it("USER_UPDATED keeps same-user UI and repository without global loading", async () => { const { s } = await mounted(); await historyState(); await act(async () => { s.emit("USER_UPDATED"); }); expect(screen.getByText("Página 2")).toBeTruthy(); expectStable(); });
  it("a manipulated event ID cannot replace verified Auth identity", async () => {
    const { s } = await mounted(); await act(async () => { s.hint("SIGNED_IN", "forged-user"); });
    await screen.findByRole("alert"); expect(state.create).toHaveBeenCalledOnce(); expect(repos[0].dispose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Histórico" })).toBeNull();
  });
  it("source has no destructive focus/visibility listener, polling or visual epoch key", () => {
    const source = readFileSync("app/portal-auth.tsx", "utf8");
    expect(source).not.toMatch(/addEventListener|removeEventListener|setInterval|key=\{[^}]*epoch/);
    expect(source).toContain("key={access.profile.id}");
  });
});
