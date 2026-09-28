"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { LockKeyhole } from "lucide-react";
import type { SupabaseClient } from "@supabase/supabase-js";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { clearBrowserSession, getBrowserClient, logPortalEvent, validatePublicConfig } from "@/lib/supabase/client";
import { SupabaseForecastReadRepository } from "@/lib/forecast-data";
import type { ForecastReadRepository, Profile, PublicSupabaseConfig } from "@/lib/supabase/types";
import ForecastTowellApp from "./forecast-towell-app";
import { passwordRecoveryRedirect, rememberRecoverySession } from "@/lib/supabase/recovery";
import type { PortalReadDiagnostic } from "@/lib/portal-read-diagnostic";
import { recoveryOrigin } from "@/lib/supabase/site-url";

export function LoginPanel({ busy, error, configured, onSubmit, onRequestReset }: { busy: boolean; error: string; configured: boolean; onSubmit: (email: string, password: string) => Promise<void>; onRequestReset?: (email: string) => Promise<void> }) {
  const [reset, setReset] = useState(false);
  const [notice, setNotice] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    if (reset) { await onRequestReset?.(String(form.get("email"))); setNotice(true); }
    else await onSubmit(String(form.get("email")), String(form.get("password")));
  }
  return <main className="grid min-h-svh place-items-center bg-[#f7f9fc] p-5">
    <section className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-9">
      <div className="mb-8 flex items-center gap-3"><div className="grid size-11 place-items-center rounded-xl bg-blue-700 font-black text-white">FT</div><div><h1 className="text-xl font-semibold text-slate-950">FORECAST Towell</h1><p className="text-sm text-slate-500">Acceso al portal</p></div></div>
      {reset && <h2 className="mb-5 text-lg font-semibold">Recuperar contraseña</h2>}
      <form onSubmit={submit} className="space-y-5" aria-label={reset ? "Recuperar contraseña" : "Iniciar sesión"}>
        <div className="space-y-2"><Label htmlFor="login-email">Correo electrónico</Label><Input id="login-email" name="email" type="email" autoComplete="username" required disabled={busy || !configured}/></div>
        {!reset && <div className="space-y-2"><Label htmlFor="login-password">Contraseña</Label><Input id="login-password" name="password" type="password" autoComplete="current-password" required disabled={busy || !configured}/></div>}
        {error && !reset && <p role="alert" className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}
        {notice && reset && <p role="status" className="rounded-lg bg-blue-50 p-3 text-sm text-blue-900">Si existe una cuenta autorizada y la solicitud puede procesarse, recibirás un enlace para restablecer tu contraseña. Revisa también la carpeta de spam.</p>}
        {!configured && <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">El acceso aún no está configurado. Contacta al administrador.</p>}
        <Button type="submit" className="w-full bg-blue-700" disabled={busy || !configured || (reset && notice)}>{busy ? (reset ? "Procesando solicitud…" : "Verificando acceso…") : reset ? "Enviar enlace de recuperación" : "Iniciar sesión"}</Button>
        <Button type="button" variant="link" className="w-full text-blue-700" disabled={busy || !configured} onClick={() => { setReset(!reset); setNotice(false); }}>{reset ? "Volver a iniciar sesión" : "¿Olvidaste tu contraseña?"}</Button>
      </form>
      <p className="mt-6 flex items-start gap-2 text-sm leading-6 text-slate-500"><LockKeyhole className="mt-1 size-4 shrink-0"/>Acceso exclusivo para usuarios autorizados.</p>
    </section>
  </main>;
}

export default function PortalAuth({ config, login = false, providedClient }: { config: PublicSupabaseConfig; login?: boolean; providedClient?: SupabaseClient }) {
  const { replace } = useRouter();
  const [client, setClient] = useState<SupabaseClient | null>(null);
  const [access, setAccess] = useState<{ profile: Profile; repository: ForecastReadRepository } | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const recovering = useRef(false);
  const repository = useRef<ForecastReadRepository | null>(null);
  const identity = useRef<string | null>(null);
  const mountedAccess = useRef<typeof access>(null);
  const pending = useRef<{ expectedId: string | null; epoch: number } | null>(null);
  const navigation = useRef(replace);
  useEffect(() => { navigation.current = replace; }, [replace]);
  const configured = Boolean(providedClient) || validatePublicConfig(config);
  const url = config.url, key = config.key;
  const clear = useCallback(() => {
    generation.current += 1;
    repository.current?.dispose(); repository.current = null;
    identity.current = null; pending.current = null; mountedAccess.current = null; setAccess(null);
  }, []);

  useEffect(() => {
    if (!configured) return;
    const sdk = providedClient ?? getBrowserClient({ url, key });
    let alive = true;
    function initialize(expectedId: string | null = null) {
      if (!alive || recovering.current) return;
      if (mountedAccess.current && (!expectedId || identity.current === expectedId)) {
        if (login) navigation.current("/");
        return;
      }
      if (pending.current) {
        if (!expectedId || pending.current.expectedId === expectedId || identity.current === expectedId) return;
        clear(); // Cancel only a genuinely different identity, never a refresh.
      } else if (identity.current && expectedId && identity.current !== expectedId) clear();
      const task = { expectedId, epoch: generation.current };
      pending.current = task;
      if (!mountedAccess.current) setLoading(true);
      void verifySession(task);
    }
    async function verifySession(task: { expectedId: string | null; epoch: number }) {
      const epoch = task.epoch;
      try {
        const { data, error: authError } = await sdk.auth.getUser();
        if (!alive || epoch !== generation.current || recovering.current) return;
        if (authError || !data.user) { clear(); setLoading(false); if (!login) navigation.current("/login"); return; }
        // Auth event user IDs are hints only. Auth + database profile remain
        // authoritative; a stale or manipulated event cannot select identity.
        if (task.expectedId && data.user.id !== task.expectedId) throw new Error("session_identity_mismatch");
        if (mountedAccess.current?.profile.id === data.user.id) { setLoading(false); return; }
        identity.current = data.user.id;
        const reported = new Set<string>();
        const reportReadFailure = (diagnostic: PortalReadDiagnostic) => {
          const signature = JSON.stringify(diagnostic);
          if (reported.has(signature) || !alive || epoch !== generation.current || typeof sdk.auth.getSession !== "function") return;
          reported.add(signature);
          // The normal SDK session stays inside the app. No extraction, auth
          // material in the body, raw error text or row data is permitted.
          void sdk.auth.getSession().then(({ data: sessionData }) => {
            if (alive && epoch === generation.current && sessionData.session) return fetch("/api/portal-validation", {
              method: "POST", headers: { Authorization: `Bearer ${sessionData.session.access_token}`, "Content-Type": "application/json" },
              body: signature, cache: "no-store",
            });
          }).catch(() => { /* Diagnostics must not affect portal reads. */ });
        };
        const repo = new SupabaseForecastReadRepository(sdk, data.user.id, reportReadFailure);
        repository.current = repo;
        const profile = await repo.getCurrentProfile();
        if (!alive || epoch !== generation.current || recovering.current) return;
        const next = { profile, repository: repo };
        mountedAccess.current = next; setAccess(next); setError(""); setLoading(false);
        // Optional HTTP read attestation. It verifies the same JWT at Supabase,
        // never forwards identity to Python, and does not persist auth material.
        if (typeof sdk.auth.getSession === "function") {
          void sdk.auth.getSession().then(({ data: sessionData }) => {
            if (alive && epoch === generation.current && sessionData.session) {
              return fetch("/api/portal-validation", { headers: { Authorization: `Bearer ${sessionData.session.access_token}` }, cache: "no-store" });
            }
          }).catch(() => logPortalEvent("read_error"));
        }
        if (login) navigation.current("/");
      } catch {
        if (!alive || epoch !== generation.current) return;
        clear(); setLoading(false);
        setError("No tienes acceso activo al portal. Contacta al administrador.");
      } finally {
        if (pending.current === task) pending.current = null;
      }
    }
    queueMicrotask(() => { if (alive) { setClient(sdk); initialize(); } });
    const { data } = sdk.auth.onAuthStateChange((event, session) => {
      if (!alive) return;
      // Classify before mutating. Token renewal and repeated same-user events
      // must be invisible to the mounted shell. No auth polling/focus listener.
      if (event === "SIGNED_OUT") {
        clear(); recovering.current = false; setLoading(false); setBusy(false); setError(""); navigation.current("/login"); return;
      }
      if (event === "PASSWORD_RECOVERY") {
        recovering.current = true; generation.current += 1; pending.current = null;
        mountedAccess.current = null; setAccess(null); setLoading(false); setBusy(false);
        if (session?.user.id) rememberRecoverySession(session.user.id);
        // Redirect unmount owns disposal; a recovery session never opens portal.
        navigation.current("/update-password"); return;
      }
      if (recovering.current) return;
      const userId = session?.user.id ?? null;
      if ((event === "TOKEN_REFRESHED" || event === "USER_UPDATED") && (!userId || userId === identity.current)) return;
      if (!["INITIAL_SESSION", "SIGNED_IN", "TOKEN_REFRESHED", "USER_UPDATED"].includes(event)) return;
      if (userId && mountedAccess.current?.profile.id === userId) return;
      if (userId && identity.current && identity.current !== userId) clear();
      // Never await SDK methods inside the auth callback/lock.
      queueMicrotask(() => { if (alive) initialize(userId); });
    });
    return () => {
      alive = false; data.subscription.unsubscribe(); generation.current += 1;
      repository.current?.dispose(); repository.current = null; identity.current = null; pending.current = null; mountedAccess.current = null;
    };
  }, [clear, url, key, configured, login, providedClient]);

  async function signIn(email: string, password: string) {
    if (!client || busy) return;
    setBusy(true); setError("");
    try {
      const { error: authError } = await client.auth.signInWithPassword({ email, password });
      if (authError) { logPortalEvent("login_failure"); setError("No fue posible iniciar sesión. Verifica tus credenciales e inténtalo de nuevo."); }
      else logPortalEvent("login_success");
    } catch { logPortalEvent("login_failure"); setError("No fue posible iniciar sesión. Inténtalo de nuevo."); }
    finally { setBusy(false); }
  }
  async function requestReset(email: string) {
    if (!client || busy) return;
    setBusy(true);
    try { await client.auth.resetPasswordForEmail(email, { redirectTo: passwordRecoveryRedirect(recoveryOrigin(config.siteUrl, window.location.origin, process.env.NODE_ENV === "development")) }); }
    catch { /* All outcomes use the same public message; no account enumeration. */ }
    finally { setBusy(false); }
  }
  async function signOut() {
    clear(); setError("");
    try { await client?.auth.signOut({ scope: "local" }); logPortalEvent("logout"); }
    catch { /* UI data stays cleared even if the network is unavailable. */ }
    finally { clearBrowserSession(config); replace("/login"); }
  }
  if (loading && configured) return <main className="grid min-h-svh place-items-center bg-[#f7f9fc]" role="status">Verificando sesión…</main>;
  if (!access || login) return <LoginPanel configured={configured} busy={busy} error={error} onSubmit={signIn} onRequestReset={requestReset}/>;
  return <ForecastTowellApp key={access.profile.id} profile={access.profile} repository={access.repository} onLogout={signOut}/>;
}
