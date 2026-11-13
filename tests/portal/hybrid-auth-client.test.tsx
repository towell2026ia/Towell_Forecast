import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { clearBrowserSession, getBrowserClient, getDataClient, invalidateLocalDataSession, isHybridConfig, validatePublicConfig } from "../../lib/supabase/client";
import { publicPortalConfig } from "../../lib/supabase/public-config";
import { SupabaseForecastReadRepository } from "../../lib/forecast-data";
import { operationalContext } from "./operational-context-fixture";

const state = vi.hoisted(() => ({ replace: vi.fn(), mount: vi.fn(), unmount: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: state.replace }) }));
vi.mock("../../app/forecast-towell-app", () => ({ default: function SyntheticPortal({ profile, onLogout }: { profile: { global_role: string }; onLogout: () => void }) {
  React.useEffect(() => { state.mount(); return () => { state.unmount(); }; }, []);
  return <div>{profile.global_role}<button onClick={onLogout}>Cerrar sesión</button></div>;
} }));
import PortalAuth from "../../app/portal-auth";
import UpdatePassword from "../../app/update-password/update-password";

const hosted = "https://bskoyqhbgrycpwhydnnr.supabase.co";
const local = "http://127.0.0.1:8000";
const config = { url: hosted, key: "sb_publishable_synthetic_auth_test_key", dataUrl: local, dataKey: "sb_publishable_synthetic_local_test_key" };
const uid = "00000000-0000-4000-8000-000000000001";
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>;
function target(input: RequestInfo | URL, init?: RequestInit) {
  return { url: new URL(typeof input === "string" || input instanceof URL ? input : input.url),
    method: (init?.method ?? (typeof input === "object" && "method" in input ? input.method : "GET")).toUpperCase(),
    headers: new Headers(init?.headers ?? (typeof input === "object" && "headers" in input ? input.headers : undefined)) };
}
const response = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
function identity() {
  let token = "synthetic-first-user-token";
  let callback: (event: string, session: unknown) => void = () => {};
  const auth = { getUser: vi.fn(async () => ({ data: { user: { id: uid } }, error: null })),
    getSession: vi.fn(async () => ({ data: { session: token ? { access_token: token, user: { id: uid } } : null }, error: null })),
    onAuthStateChange: vi.fn((fn: typeof callback) => { callback = fn; return { data: { subscription: { unsubscribe: vi.fn() } } }; }),
    signInWithPassword: vi.fn(), resetPasswordForEmail: vi.fn(), updateUser: vi.fn(),
    signOut: vi.fn(async () => { token = ""; callback("SIGNED_OUT", null); return { error: null }; }) };
  return { client: { auth, from: vi.fn(() => { throw new Error("hosted_data_forbidden"); }) } as unknown as SupabaseClient, auth,
    refresh: () => { token = "synthetic-refreshed-user-token"; callback("TOKEN_REFRESHED", { access_token: token, user: { id: uid } }); } };
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("NODE_ENV", "development"); vi.stubEnv("NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED", "true"); vi.stubEnv("NEXT_PUBLIC_FORECAST_API_URL", local);
  localStorage.clear();
  fetcher = vi.fn<typeof fetch>(async () => response([])); vi.stubGlobal("fetch", fetcher);
});
afterEach(async () => { clearBrowserSession(config); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("Explicit hosted identity with isolated local commercial data", () => {
  it("requires the exact project, loopback data origin, both public keys and the development flag", () => {
    expect(isHybridConfig(config)).toBe(true); expect(validatePublicConfig(config)).toBe(true);
    for (const changed of [{ dataUrl: "" }, { dataKey: "" }, { dataUrl: "http://localhost:8000" }, { dataUrl: "https://remote.test" },
      { url: "https://another.supabase.co" }, { dataKey: "sb_secret_synthetic_rejected" }]) expect(validatePublicConfig({ ...config, ...changed })).toBe(false);
    expect(validatePublicConfig({ url: hosted, key: config.key })).toBe(false); // Never hosted-data fallback.
    expect(isHybridConfig({ url: "https://wrong.supabase.co", key: config.key })).toBe(true);
    expect(validatePublicConfig({ url: "https://wrong.supabase.co", key: config.key })).toBe(false);
    expect(() => getBrowserClient({ url: "https://wrong.supabase.co", key: config.key })).toThrow("public_config_unavailable");
    expect(() => getDataClient({ url: "https://wrong.supabase.co", key: config.key }, identity().client)).toThrow("public_config_unavailable");
    expect(fetcher).not.toHaveBeenCalled();
    expect(validatePublicConfig({ url: local, key: config.dataKey })).toBe(false); // No legacy fixture Auth.
    vi.stubEnv("NODE_ENV", "production"); expect(validatePublicConfig(config)).toBe(false);
    vi.stubEnv("NODE_ENV", "development"); vi.stubEnv("NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED", "false"); expect(validatePublicConfig(config)).toBe(false);
  });
  it("exposes incomplete local configuration as invalid rather than omitting the split", () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", hosted); vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", config.key);
    vi.stubEnv("NEXT_PUBLIC_LOCAL_DATA_URL", ""); vi.stubEnv("NEXT_PUBLIC_LOCAL_DATA_PUBLISHABLE_KEY", "");
    const incomplete = publicPortalConfig(); expect(incomplete).toMatchObject({ dataUrl: "", dataKey: "" });
    expect(validatePublicConfig(incomplete)).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://wrong.supabase.co");
    const wrongProject = publicPortalConfig(); expect(wrongProject).toMatchObject({ dataUrl: "", dataKey: "" });
    expect(validatePublicConfig(wrongProject)).toBe(false);
  });
  it("does not let an injected client or the previous local Auth URL bypass configuration checks", async () => {
    const auth = identity(); render(<PortalAuth config={{ url: local, key: config.dataKey }} providedClient={auth.client}/>);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Iniciar sesión" }).hasAttribute("disabled")).toBe(true);
    expect(auth.auth.getUser).not.toHaveBeenCalled(); expect(fetcher).not.toHaveBeenCalled();
  });
  it("disables the hosted-read attestation in hybrid mode before it can access production REST", async () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", hosted); vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", config.key);
    vi.stubEnv("NEXT_PUBLIC_LOCAL_DATA_URL", local); vi.stubEnv("NEXT_PUBLIC_LOCAL_DATA_PUBLISHABLE_KEY", config.dataKey);
    const { GET } = await import("../../app/api/portal-validation/route");
    const result = await GET(new Request("http://127.0.0.1:5173/api/portal-validation", { headers: { Authorization: "Bearer synthetic-user-token" } }));
    expect(result.status).toBe(503); expect(await result.json()).toEqual({ error: "local_validation_unavailable" });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("uses the normal SDK accessToken callback for every local REST request and follows refresh without a local Auth session", async () => {
    const realAuth = identity(), data = getDataClient(config, realAuth.client);
    await data.from("profiles").select("id"); realAuth.refresh(); await data.rpc("operational_read_context");
    expect(fetcher).toHaveBeenCalledTimes(2);
    const calls = fetcher.mock.calls.map(args => target(...args));
    expect(calls.every(row => row.url.origin === local && row.url.pathname.startsWith("/rest/v1/"))).toBe(true);
    expect(calls.map(row => row.headers.get("authorization"))).toEqual(["Bearer synthetic-first-user-token", "Bearer synthetic-refreshed-user-token"]);
    expect(() => data.auth.getSession()).toThrow(/accessToken/);
    expect(localStorage.length).toBe(0);
    const before = fetcher.mock.calls.length; await data.functions.invoke("user-admin", { body: { action: "list" } });
    expect((await data.from("profiles").insert({ id: uid })).error).toBeTruthy();
    expect((await data.rpc("unapproved_mutating_function")).error).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(before); expect(realAuth.client.from).not.toHaveBeenCalled();
  });
  it("allows Auth user verification but fences hosted REST and recovery even through the real SDK", async () => {
    fetcher.mockImplementation(async (input, init) => {
      const { url } = target(input, init); expect(url.origin).toBe(hosted); expect(url.pathname).toBe("/auth/v1/user"); return response({ id: uid });
    });
    const auth = getBrowserClient(config);
    expect((await auth.auth.getUser("synthetic-explicit-token")).data.user?.id).toBe(uid);
    const calls = fetcher.mock.calls.length;
    expect((await auth.from("products").select("id")).error).toBeTruthy();
    expect((await auth.auth.resetPasswordForEmail("synthetic@example.test")).error).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(calls);
  });
  it("verifies identity with the Auth client and reads profile/source from the data client", async () => {
    const auth = identity();
    fetcher.mockImplementation(async (input, init) => {
      const { url, headers } = target(input, init); expect(url.origin).toBe(local); expect(headers.get("authorization")).toBe("Bearer synthetic-first-user-token");
      if (url.pathname === "/rest/v1/profiles") return response({ id: uid, full_name: "Synthetic reader", global_role: "VIEWER", status: "ACTIVE" });
      if (url.pathname === "/rest/v1/rpc/operational_read_context") return response([operationalContext]);
      if (url.pathname === "/api/forecast/commercial-catalog") return response({ source_context: operationalContext, products: [], persisted: false });
      throw new Error("unexpected_test_route");
    });
    const repository = new SupabaseForecastReadRepository(getDataClient(config, auth.client), uid, undefined, auth.client);
    expect((await repository.getCurrentProfile()).global_role).toBe("VIEWER");
    expect(await repository.previews.commercialCatalog(operationalContext)).toEqual([]);
    expect(auth.auth.getUser).toHaveBeenCalledOnce(); expect(auth.client.from).not.toHaveBeenCalled(); repository.dispose();
  });
  it("denies a valid hosted identity without an active local profile and hides recovery", async () => {
    const auth = identity(); fetcher.mockImplementation(async () => response(null));
    render(<PortalAuth config={config} providedClient={auth.client}/>);
    expect((await screen.findByRole("alert")).textContent).toContain("No tienes acceso activo");
    expect(screen.queryByText("VIEWER")).toBeNull(); expect(screen.queryByRole("button", { name: "¿Olvidaste tu contraseña?" })).toBeNull();
    expect(auth.client.from).not.toHaveBeenCalled(); expect(fetcher.mock.calls.every(args => target(...args).url.origin === local)).toBe(true);
  });
  it("keeps the mounted shell on refresh and invalidates the local bearer before hosted local-session logout", async () => {
    const auth = identity();
    fetcher.mockImplementation(async (input, init) => {
      const { url } = target(input, init); expect(url.origin).toBe(local);
      if (url.pathname === "/auth/v1/logout") return response({});
      expect(url.pathname).toBe("/rest/v1/profiles"); return response({ id: uid, full_name: "Synthetic reader", global_role: "VIEWER", status: "ACTIVE" });
    });
    render(<PortalAuth config={config} providedClient={auth.client}/>); await screen.findByText("VIEWER");
    await act(async () => auth.refresh()); expect(state.mount).toHaveBeenCalledOnce(); expect(state.unmount).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Cerrar sesión" }));
    await waitFor(() => expect(auth.auth.signOut).toHaveBeenCalledWith({ scope: "local" }));
    const logoutIndex = fetcher.mock.calls.findIndex(args => target(...args).url.pathname === "/auth/v1/logout");
    expect(logoutIndex).toBeGreaterThanOrEqual(0);
    expect(fetcher.mock.invocationCallOrder[logoutIndex]).toBeLessThan(auth.auth.signOut.mock.invocationCallOrder[0]);
    expect(screen.queryByText("VIEWER")).toBeNull(); expect(fetcher.mock.calls.some(args => target(...args).url.pathname === "/api/portal-validation")).toBe(false);
  });
  it("blocks the password-change page in hybrid mode without contacting hosted Auth", async () => {
    const auth = identity(); render(<UpdatePassword config={config} providedClient={auth.client}/>);
    expect(await screen.findByRole("alert")).toBeTruthy(); expect(screen.queryByRole("form", { name: "Actualizar contraseña" })).toBeNull();
    expect(auth.auth.getUser).not.toHaveBeenCalled(); expect(auth.auth.updateUser).not.toHaveBeenCalled(); expect(fetcher).not.toHaveBeenCalled();
  });
  it("preserves the existing standard client and makes local invalidation a no-op outside hybrid mode", async () => {
    vi.stubEnv("NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED", "false");
    const auth = identity(), standard = { url: "https://synthetic.supabase.co", key: config.key };
    expect(getDataClient(standard, auth.client)).toBe(auth.client); await invalidateLocalDataSession(standard, auth.client);
    expect(fetcher).not.toHaveBeenCalled();
  });
});
