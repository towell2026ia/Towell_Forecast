import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { Profile } from "../../lib/supabase/types";
const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("../../app/forecast-towell-app", () => ({ default: ({ profile, onLogout }: { profile: Profile; onLogout: () => void }) => <div>{profile.global_role}<button onClick={onLogout}>Cerrar sesión</button></div> }));
import PortalAuth, { LoginPanel } from "../../app/portal-auth";

const config = { url: "https://example.supabase.co", key: "sb_publishable_synthetic_test_key" };
function sdk({ loggedIn = false, role = "ADMIN", status = "ACTIVE", fail = false, missing = false } = {}) {
  let signedIn = loggedIn;
  let event: (name: string) => void = () => {};
  const profile = { id: "user-1", full_name: "Usuario de prueba", global_role: role, status };
  const query = { select: () => query, eq: () => query, abortSignal: () => query, maybeSingle: async () => ({ data: missing ? null : profile, error: null }) };
  const client = { from: () => query, auth: {
    getUser: async () => ({ data: { user: signedIn ? { id: "user-1" } : null }, error: null }),
    onAuthStateChange: (callback: typeof event) => { event = callback; return { data: { subscription: { unsubscribe: vi.fn() } } }; },
    signInWithPassword: vi.fn(async () => { if (fail) return { error: new Error("private details") }; signedIn = true; event("SIGNED_IN"); return { error: null }; }),
    signOut: vi.fn(async () => { signedIn = false; event("SIGNED_OUT"); return { error: null }; }),
  } };
  return { client: client as unknown as SupabaseClient, auth: client.auth, expire: () => { signedIn = false; event("SIGNED_OUT"); } };
}
async function submitLogin() {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Correo electrónico"), "reader@example.test");
  await user.type(screen.getByLabelText("Contraseña"), "not-a-real-password");
  await user.click(screen.getByRole("button", { name: "Iniciar sesión" }));
}
describe("Supabase session UI", () => {
  it("UI01 login renders, accessible, no signup", () => {
    render(<LoginPanel configured busy={false} error="" onSubmit={vi.fn()}/>);
    expect(screen.getByLabelText("Correo electrónico")).toBeTruthy();
    expect(screen.getByLabelText("Contraseña").getAttribute("type")).toBe("password");
    expect(screen.queryByText(/Crear cuenta/)).toBeNull();
  });
  it("UI02 valid login routes to portal", async () => {
    const s = sdk(); render(<PortalAuth login config={config} providedClient={s.client}/>);
    await submitLogin(); await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(s.auth.signInWithPassword).toHaveBeenCalledTimes(1);
  });
  it("UI03 wrong password stays login with generic error", async () => {
    render(<PortalAuth login config={config} providedClient={sdk({ fail: true }).client}/>);
    await submitLogin(); expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText("private details")).toBeNull();
  });
  it("UI04 logout clears UI and persisted local session", async () => {
    const s = sdk({ loggedIn: true }); localStorage.setItem("towell-auth-example.supabase.co", "synthetic-session");
    render(<PortalAuth config={config} providedClient={s.client}/>);
    await userEvent.click(await screen.findByRole("button", { name: "Cerrar sesión" }));
    await waitFor(() => expect(localStorage.getItem("towell-auth-example.supabase.co")).toBeNull());
    expect(screen.queryByText("ADMIN")).toBeNull(); expect(s.auth.signOut).toHaveBeenCalled();
  });
  it("UI05 inactive profile blocked", async () => {
    render(<PortalAuth config={config} providedClient={sdk({ loggedIn: true, status: "INACTIVE" }).client}/>);
    expect(await screen.findByRole("alert")).toBeTruthy(); expect(screen.queryByText("ADMIN")).toBeNull();
  });
  it("UI06 verified profile role rendered after persistent session refresh", async () => {
    render(<PortalAuth config={config} providedClient={sdk({ loggedIn: true, role: "VIEWER" }).client}/>);
    expect(await screen.findByText("VIEWER")).toBeTruthy();
  });
  it("expired session removes portal data and routes login", async () => {
    const s = sdk({ loggedIn: true }); render(<PortalAuth config={config} providedClient={s.client}/>);
    await screen.findByText("ADMIN"); s.expire();
    await waitFor(() => expect(screen.queryByText("ADMIN")).toBeNull());
    expect(replace).toHaveBeenCalledWith("/login");
  });
  it("missing profile blocked without public signup", async () => {
    render(<PortalAuth config={config} providedClient={sdk({ loggedIn: true, missing: true }).client}/>);
    expect(await screen.findByRole("alert")).toBeTruthy(); expect(screen.queryByText(/Crear cuenta/)).toBeNull();
  });
});
