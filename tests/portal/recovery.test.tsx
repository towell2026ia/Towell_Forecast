import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SupabaseClient } from "@supabase/supabase-js";
import { isRecoveryCallback, clearRecoverySession, rememberRecoverySession } from "../../lib/supabase/recovery";
const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("../../app/forecast-towell-app", () => ({ default: () => <p>Protected portal</p> }));
import PortalAuth from "../../app/portal-auth";
import UpdatePassword from "../../app/update-password/update-password";

const config = { url: "https://example.supabase.co", key: "sb_publishable_synthetic_test_key" };
const session = { user: { id: "user-1" }, access_token: "synthetic" };
function sdk() {
  let callback: (event: string, value: typeof session | null) => void = () => {};
  const auth = {
    getUser: vi.fn().mockResolvedValue({ data: { user: session.user }, error: null }),
    getSession: vi.fn().mockResolvedValue({ data: { session }, error: null }),
    onAuthStateChange: vi.fn((handler: typeof callback) => { callback = handler; return { data: { subscription: { unsubscribe: vi.fn() } } }; }),
    resetPasswordForEmail: vi.fn().mockResolvedValue({ data: {}, error: null }),
    updateUser: vi.fn().mockResolvedValue({ data: { user: session.user }, error: null }),
    signOut: vi.fn().mockResolvedValue({ error: null }),
  };
  const query = { select: () => query, eq: () => query, abortSignal: () => query, maybeSingle: async () => ({ data: { id: "user-1", full_name: "Fixture", global_role: "ADMIN", status: "ACTIVE" }, error: null }) };
  return { client: { auth, from: () => query } as unknown as SupabaseClient, auth, emit: (event: string, accessToken = session.access_token) => callback(event, event === "SIGNED_OUT" ? null : { ...session, access_token: accessToken }) };
}
beforeEach(() => { vi.stubEnv("NODE_ENV", "development"); });
afterEach(() => { clearRecoverySession(); window.history.replaceState(null, "", "/"); vi.clearAllMocks(); vi.unstubAllEnvs(); });
async function recoveryForm(s: ReturnType<typeof sdk>) {
  window.history.replaceState(null, "", "/update-password");
  render(<UpdatePassword config={config} providedClient={s.client}/>);
  await act(async () => { s.emit("PASSWORD_RECOVERY"); });
  await screen.findByLabelText("Nueva contraseña");
}
async function submitPassword(password = "Synthetic-password-456", confirmation = password) {
  await userEvent.type(screen.getByLabelText("Nueva contraseña"), password);
  await userEvent.type(screen.getByLabelText("Confirmar contraseña"), confirmation);
  await userEvent.click(screen.getByRole("button", { name: "Guardar nueva contraseña" }));
}
describe("Password recovery", () => {
  it("request reset in production uses canonical Netlify rather than the browser origin", async () => {
    vi.stubEnv("NODE_ENV", "production");
    const s = sdk(); s.auth.getUser.mockResolvedValue({ data: { user: null }, error: null });
    render(<PortalAuth login config={{ ...config, siteUrl: "https://towell-forecastia.netlify.app/" }} providedClient={s.client}/>);
    await userEvent.click(await screen.findByRole("button", { name: "¿Olvidaste tu contraseña?" }));
    await userEvent.type(screen.getByLabelText("Correo electrónico"), "fixture@example.test");
    await userEvent.click(screen.getByRole("button", { name: "Enviar enlace de recuperación" }));
    expect(s.auth.resetPasswordForEmail).toHaveBeenCalledWith("fixture@example.test", { redirectTo: "https://towell-forecastia.netlify.app/update-password" });
    expect(await screen.findByText(/Si existe una cuenta autorizada/)).toBeTruthy();
  });
  for (const outcome of ["success", "unknown-account", "network"] as const) {
    it(`request reset: ${outcome} uses the identical generic notice`, async () => {
      const s = sdk(); s.auth.getUser.mockResolvedValue({ data: { user: null }, error: null });
      if (outcome === "unknown-account") s.auth.resetPasswordForEmail.mockResolvedValue({ data: {}, error: { message: "account does not exist" } });
      if (outcome === "network") s.auth.resetPasswordForEmail.mockRejectedValue(new Error("private network detail"));
      render(<PortalAuth login config={config} providedClient={s.client}/>);
      await userEvent.click(await screen.findByRole("button", { name: "¿Olvidaste tu contraseña?" }));
      expect(screen.queryByLabelText("Contraseña")).toBeNull();
      await userEvent.type(screen.getByLabelText("Correo electrónico"), "fixture@example.test");
      await userEvent.click(screen.getByRole("button", { name: "Enviar enlace de recuperación" }));
      expect(s.auth.resetPasswordForEmail).toHaveBeenCalledWith("fixture@example.test", { redirectTo: window.location.origin + "/update-password" });
      expect(await screen.findByText(/Si existe una cuenta autorizada/)).toBeTruthy();
      expect(screen.queryByText(/account does not exist|private network detail|Crear cuenta/)).toBeNull();
    });
  }
  it("SDK callback detection is restricted to recovery on /update-password", () => {
    expect(isRecoveryCallback(new URL("https://example.test/update-password"), { type: "recovery" })).toBe(true);
    expect(isRecoveryCallback(new URL("https://example.test/update-password"), { type: "invite" })).toBe(true);
    expect(isRecoveryCallback(new URL("https://example.test/login"), { type: "recovery" })).toBe(false);
    expect(isRecoveryCallback(new URL("https://example.test/login"), { type: "invite" })).toBe(false);
    expect(isRecoveryCallback(new URL("https://example.test/update-password"), { type: "signup" })).toBe(false);
  });
  it("a verified invitation opens password setup and then returns to login", async () => {
    const s = sdk();
    window.history.replaceState(null, "", "/update-password#access_token=synthetic&refresh_token=synthetic&type=invite");
    render(<UpdatePassword config={config} providedClient={s.client}/>);
    await screen.findByLabelText("Nueva contraseña");
    await submitPassword();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(s.auth.updateUser).toHaveBeenCalledWith({ password: "Synthetic-password-456" });
    expect(window.location.hash).toBe("");
  });
  it("an older session is not accepted as the invitation until the SDK signs in", async () => {
    const s = sdk();
    s.auth.getSession.mockResolvedValue({ data: { session: { ...session, access_token: "older-session" } }, error: null });
    window.history.replaceState(null, "", "/update-password#access_token=invitation-token&refresh_token=synthetic&type=invite");
    render(<UpdatePassword config={config} providedClient={s.client}/>);
    expect(screen.queryByLabelText("Nueva contraseña")).toBeNull();
    await act(async () => { s.emit("SIGNED_IN", "invitation-token"); });
    expect(await screen.findByLabelText("Nueva contraseña")).toBeTruthy();
  });
  it("a forged invitation type without callback credentials cannot open password setup", async () => {
    window.history.replaceState(null, "", "/update-password#type=invite");
    const s = sdk(); render(<UpdatePassword config={config} providedClient={s.client}/>);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(s.auth.updateUser).not.toHaveBeenCalled();
  });
  it("portal recovery event discards portal access and routes to update-password", async () => {
    const s = sdk(); render(<PortalAuth config={config} providedClient={s.client}/>);
    // Skip diagnostic: this synthetic client is not an actual HTTP identity.
    await screen.findByText("Protected portal");
    await act(async () => { s.emit("PASSWORD_RECOVERY"); });
    expect(screen.queryByText("Protected portal")).toBeNull();
    expect(replace).toHaveBeenCalledWith("/update-password");
  });
  it("bare route plus ordinary logged-in session does not open the update form", async () => {
    const s = sdk(); render(<UpdatePassword config={config} providedClient={s.client}/>);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByLabelText("Nueva contraseña")).toBeNull(); expect(s.auth.updateUser).not.toHaveBeenCalled();
  });
  it("PASSWORD_RECOVERY requires a verified Auth user before showing the form", async () => {
    const s = sdk(); await recoveryForm(s);
    expect(s.auth.getUser).toHaveBeenCalled(); expect(screen.queryByText("Protected portal")).toBeNull();
  });
  it("invalid/expired callback is rejected without echoing error details", async () => {
    const s = sdk(); s.auth.getSession.mockResolvedValue({ data: { session: null }, error: { message: "sensitive callback detail" } });
    window.history.replaceState(null, "", "/update-password#error=access_denied&error_code=otp_expired");
    render(<UpdatePassword config={config} providedClient={s.client}/>);
    expect(await screen.findByRole("alert")).toBeTruthy(); expect(window.location.hash).toBe("");
    expect(screen.queryByText("sensitive callback detail")).toBeNull(); expect(s.auth.updateUser).not.toHaveBeenCalled();
  });
  it("forged/unverifiable recovery identity cannot update a password", async () => {
    const s = sdk(); s.auth.getUser.mockResolvedValue({ data: { user: { id: "different-user" } }, error: null });
    render(<UpdatePassword config={config} providedClient={s.client}/>);
    await act(async () => { s.emit("PASSWORD_RECOVERY"); });
    expect(await screen.findByRole("alert")).toBeTruthy(); expect(s.auth.updateUser).not.toHaveBeenCalled();
  });
  it("successful update uses updateUser, clears recovery/session and routes login", async () => {
    const s = sdk(); await recoveryForm(s);
    localStorage.setItem("towell-auth-example.supabase.co", "synthetic-session");
    await submitPassword();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(s.auth.updateUser).toHaveBeenCalledWith({ password: "Synthetic-password-456" });
    expect(s.auth.signOut).toHaveBeenCalledWith({ scope: "local" });
    expect(sessionStorage.getItem("towell-password-recovery")).toBeNull();
    expect(localStorage.getItem("towell-auth-example.supabase.co")).toBeNull();
  });
  it("mismatched confirmation never calls updateUser", async () => {
    const s = sdk(); await recoveryForm(s); await submitPassword("Synthetic-password-456", "Other-synthetic-789");
    expect(await screen.findByText("Las contraseñas no coinciden.")).toBeTruthy(); expect(s.auth.updateUser).not.toHaveBeenCalled();
  });
  it("update rejection is controlled and does not expose password or SDK errors", async () => {
    const s = sdk(); s.auth.updateUser.mockResolvedValue({ data: { user: null }, error: { message: "secret SDK detail" } });
    const log = vi.spyOn(console, "info").mockImplementation(() => {});
    await recoveryForm(s); await submitPassword();
    expect(await screen.findByRole("alert")).toBeTruthy(); expect(screen.queryByText("secret SDK detail")).toBeNull();
    expect(JSON.stringify(log.mock.calls)).not.toContain("Synthetic-password-456");
    expect(replace).not.toHaveBeenCalledWith("/login");
  });
  it("verified recovery context survives refresh, but session expiry blocks it", async () => {
    const s = sdk(); rememberRecoverySession(session.user.id);
    render(<UpdatePassword config={config} providedClient={s.client}/>);
    await screen.findByLabelText("Nueva contraseña");
    await act(async () => { s.emit("SIGNED_OUT"); });
    expect(await screen.findByRole("alert")).toBeTruthy(); expect(screen.queryByLabelText("Nueva contraseña")).toBeNull();
  });
  it("expired recovery marker cannot reuse a normal session", async () => {
    sessionStorage.setItem("towell-password-recovery", JSON.stringify({ userId: "user-1", expiresAt: Date.now() - 1 }));
    render(<UpdatePassword config={config} providedClient={sdk().client}/>);
    expect(await screen.findByRole("alert")).toBeTruthy(); expect(sessionStorage.getItem("towell-password-recovery")).toBeNull();
  });
});
