"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import type { SupabaseClient } from "@supabase/supabase-js";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { clearBrowserSession, getBrowserClient, validatePublicConfig } from "@/lib/supabase/client";
import { clearRecoverySession, recoveryUserId, rememberRecoverySession } from "@/lib/supabase/recovery";
import type { PublicSupabaseConfig } from "@/lib/supabase/types";

export default function UpdatePassword({ config, providedClient }: { config: PublicSupabaseConfig; providedClient?: SupabaseClient }) {
  const { replace } = useRouter();
  const [state, setState] = useState<"checking" | "ready" | "denied">("checking");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const client = useRef<SupabaseClient | null>(null);
  const verifiedUser = useRef<string | null>(null);
  const generation = useRef(0);
  const configured = Boolean(providedClient) || validatePublicConfig(config);
  const url = config.url, key = config.key;

  useEffect(() => {
    if (!configured) return;
    let alive = true, receivedRecovery = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // Only note the presence of callback parameters; never retain credentials.
    const incomingCallback = Boolean(window.location.hash || window.location.search);
    const sdk = providedClient ?? getBrowserClient({ url, key }); client.current = sdk;
    const scrubCallback = () => window.history.replaceState(null, "", "/update-password");
    const deny = () => { if (!alive) return; generation.current += 1; verifiedUser.current = null; clearRecoverySession(); setState("denied"); scrubCallback(); };
    async function verify(userId: string) {
      const epoch = ++generation.current;
      try {
        const { data, error: authError } = await sdk.auth.getUser();
        if (!alive || epoch !== generation.current) return;
        if (authError || data.user?.id !== userId) { deny(); return; }
        verifiedUser.current = userId; rememberRecoverySession(userId); setState("ready"); scrubCallback();
      } catch { if (epoch === generation.current) deny(); }
    }
    const { data } = sdk.auth.onAuthStateChange((event, session) => {
      if (!alive) return;
      if (event === "PASSWORD_RECOVERY" && session?.user.id) {
        receivedRecovery = true; clearTimeout(timer);
        queueMicrotask(() => { if (alive) void verify(session.user.id); });
      } else if (event === "SIGNED_OUT") deny();
    });
    // Wait for SDK callback initialization. A stored normal login is not enough.
    void sdk.auth.getSession().then(({ data: sessionData, error: sessionError }) => {
      if (!alive || receivedRecovery) return;
      if (sessionError || !sessionData.session) { deny(); return; }
      if (incomingCallback) { timer = setTimeout(deny, 3000); return; }
      const marker = recoveryUserId();
      if (!marker || marker !== sessionData.session.user.id) { deny(); return; }
      void verify(marker);
    }).catch(deny);
    return () => { alive = false; generation.current += 1; verifiedUser.current = null; clearTimeout(timer); data.subscription.unsubscribe(); };
  }, [configured, url, key, providedClient]);

  async function update(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (busy || state !== "ready" || !verifiedUser.current || !client.current) return;
    const form = e.currentTarget;
    const values = new FormData(form), password = String(values.get("password")), confirmation = String(values.get("confirmation"));
    if (password.length < 12) { setError("Usa al menos 12 caracteres para tu nueva contraseña."); return; }
    if (password !== confirmation) { setError("Las contraseñas no coinciden."); return; }
    setBusy(true); setError("");
    const epoch = generation.current, sdk = client.current;
    try {
      const { data, error: authError } = await sdk.auth.getUser();
      if (epoch !== generation.current) return;
      if (authError || data.user?.id !== verifiedUser.current) { verifiedUser.current = null; clearRecoverySession(); setState("denied"); return; }
      const result = await sdk.auth.updateUser({ password });
      if (epoch !== generation.current) return;
      if (result.error) { setError("No fue posible actualizar la contraseña. Inténtalo de nuevo o solicita otro enlace."); return; }
      form.reset(); verifiedUser.current = null; clearRecoverySession();
      // No recovery session may flow directly into the protected portal.
      try { await sdk.auth.signOut({ scope: "local" }); } finally { clearBrowserSession(config); replace("/login"); }
    } catch { if (epoch === generation.current) setError("No fue posible actualizar la contraseña. Inténtalo de nuevo o solicita otro enlace."); }
    finally { setBusy(false); }
  }
  return <main className="grid min-h-svh place-items-center bg-[#f7f9fc] p-5"><section className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-9">
    <div className="mb-8 flex items-center gap-3"><div className="grid size-11 place-items-center rounded-xl bg-blue-700 font-black text-white">FT</div><h1 className="text-xl font-semibold text-slate-950">FORECAST Towell</h1></div>
    <h2 className="mb-5 text-lg font-semibold">Restablecer contraseña</h2>
    {state === "checking" && configured && <p role="status" className="text-sm text-slate-600">Verificando enlace de recuperación…</p>}
    {(state === "denied" || !configured) && <><p role="alert" className="mb-5 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">El enlace no es válido o ha expirado. Solicita un nuevo enlace desde el inicio de sesión.</p><a href="/login" className="text-sm text-blue-700 underline">Volver a iniciar sesión</a></>}
    {state === "ready" && <form aria-label="Actualizar contraseña" onSubmit={update} className="space-y-5">
      <p className="text-sm text-slate-600">Elige una contraseña nueva de al menos 12 caracteres.</p>
      <div className="space-y-2"><Label htmlFor="new-password">Nueva contraseña</Label><Input id="new-password" name="password" type="password" minLength={12} autoComplete="new-password" required disabled={busy}/></div>
      <div className="space-y-2"><Label htmlFor="confirm-password">Confirmar contraseña</Label><Input id="confirm-password" name="confirmation" type="password" minLength={12} autoComplete="new-password" required disabled={busy}/></div>
      {error && <p role="alert" className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}
      <Button type="submit" className="w-full bg-blue-700" disabled={busy}>{busy ? "Actualizando…" : "Guardar nueva contraseña"}</Button>
    </form>}
  </section></main>;
}
