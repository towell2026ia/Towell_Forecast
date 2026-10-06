"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Chain, ForecastReadRepository, Profile, Role } from "@/lib/supabase/types";
import { normalizedEmail, parseUserMutation, type ChainGrant, type ManagedUser, type UserMutation } from "@/lib/supabase/user-admin-contract";

const emptyDraft: UserMutation = { full_name: "", global_role: "VIEWER", status: "ACTIVE", grants: [] };
const emptyGrant = (chain_id: string): ChainGrant => ({ chain_id, can_edit: false, can_import: false, can_run_forecast: false, can_approve: false });
const permissionLabels: [keyof Omit<ChainGrant, "chain_id">, string][] = [
  ["can_edit", "Editar"], ["can_import", "Importar"], ["can_run_forecast", "Ejecutar forecast"], ["can_approve", "Aprobar"],
];

export function UserAdministrationView({ repository, profile }: { repository: ForecastReadRepository; profile: Profile }) {
  const admin = repository.userAdmin;
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [chains, setChains] = useState<Chain[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [target, setTarget] = useState<ManagedUser | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [draft, setDraft] = useState<UserMutation>(emptyDraft);
  const load = useCallback(async () => {
    if (profile.global_role !== "ADMIN") return;
    if (!admin) {
      const profiles = await repository.getProfiles();
      setUsers(profiles.map(item => ({ id: item.id, email: "", email_confirmed: false, invited: false,
        last_sign_in_at: null, profile: item, grants: [] })));
      return;
    }
    const directory = await admin.list();
    setUsers(directory);
    try {
      const visible = await repository.getVisibleChains();
      setChains(visible.filter(chain => chain.status === "ACTIVE"));
    } catch {
      setChains([]);
      setError("Usuarios disponibles, pero no fue posible consultar cadenas para asignar permisos.");
    }
  }, [admin, profile.global_role, repository]);
  useEffect(() => {
    let alive = true;
    queueMicrotask(() => {
      if (alive) void load().catch(() => { if (alive) setError("No fue posible consultar usuarios y permisos."); })
        .finally(() => { if (alive) setLoading(false); });
    });
    return () => { alive = false; };
  }, [load]);
  if (profile.global_role !== "ADMIN") return null;

  function openInvite(user: ManagedUser | null = null) {
    setTarget(user); setEmail(user?.email ?? "");
    setDraft(user?.profile ? { full_name: user.profile.full_name, global_role: user.profile.global_role,
      status: user.profile.status, grants: [...user.grants] } : { ...emptyDraft, full_name: "", grants: [] });
    setError(""); setNotice(""); setFormOpen(true);
  }
  function toggleChain(chainId: string) {
    setDraft(current => ({ ...current, grants: current.grants.some(grant => grant.chain_id === chainId)
      ? current.grants.filter(grant => grant.chain_id !== chainId) : [...current.grants, emptyGrant(chainId)] }));
  }
  function togglePermission(chainId: string, permission: keyof Omit<ChainGrant, "chain_id">) {
    setDraft(current => ({ ...current, grants: current.grants.map(grant =>
      grant.chain_id === chainId ? { ...grant, [permission]: !grant[permission] } : grant) }));
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!admin || busy) return;
    const mutation = parseUserMutation(draft);
    const normalized = normalizedEmail(email);
    if (!mutation || (!target?.profile && !normalized)) {
      setError("Completa nombre y correo; para VIEWER o EDITOR elige al menos una cadena y permisos válidos."); return;
    }
    setBusy(true); setError(""); setNotice("");
    try {
      if (target?.profile) {
        if (target.id === profile.id) throw new Error("self_change_denied");
        await admin.update(target.id, mutation);
        setNotice("Usuario y permisos actualizados.");
      } else {
        const result = await admin.invite(normalized!, mutation);
        setNotice(result.email_sent ? (result.existing_auth_user
          ? "Perfil activado. Se envió un enlace para establecer contraseña."
          : "Invitación enviada. El usuario recibirá un enlace para establecer contraseña.")
          : "Perfil activado, pero el correo no pudo enviarse. Usa “Enviar enlace” para reintentar.");
      }
      setFormOpen(false); await load();
    } catch { setError("No fue posible completar la operación. Verifica los datos e inténtalo de nuevo."); }
    finally { setBusy(false); }
  }
  async function deactivate(user: ManagedUser) {
    if (!admin || busy || user.id === profile.id || !window.confirm(`¿Dar de baja el acceso de ${user.email || user.profile?.full_name}? Se conservará la auditoría y podrás reactivarlo después.`)) return;
    setBusy(true); setError(""); setNotice("");
    try { await admin.deactivate(user.id); await load(); setNotice("Acceso desactivado y permisos por cadena revocados."); }
    catch { setError("No fue posible dar de baja el acceso."); }
    finally { setBusy(false); }
  }
  async function resend(user: ManagedUser) {
    if (!admin || busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await admin.resend(user.id); setNotice("Enlace para establecer o restablecer contraseña enviado."); }
    catch { setError("No fue posible enviar el enlace. Revisa la configuración de correo de Supabase."); }
    finally { setBusy(false); }
  }
  const activeChains = chains.filter(chain => chain.status === "ACTIVE");
  return <section className="space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-2xl font-semibold">Usuarios y permisos</h1><p className="mt-2 text-sm text-slate-500">Las invitaciones se envían por correo. Sólo un ADMIN activo puede gestionar accesos; las bajas conservan la auditoría.</p></div>
      {admin && <Button onClick={() => openInvite()} disabled={busy}>Agregar usuario</Button>}
    </div>
    {loading && <p role="status" className="text-sm text-slate-500">Consultando usuarios…</p>}
    {error && <p role="alert" className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}
    {notice && <p role="status" className="rounded-lg bg-blue-50 p-3 text-sm text-blue-800">{notice}</p>}
    {!admin && <p className="rounded-lg border p-4 text-sm text-slate-500">La administración segura todavía no está disponible en este entorno.</p>}
    {!loading && <div className="overflow-x-auto rounded-xl border bg-white"><table className="min-w-full text-left text-sm"><thead><tr className="border-b bg-slate-50 text-slate-600"><th className="p-3">Usuario</th><th className="p-3">Correo</th><th className="p-3">Rol</th><th className="p-3">Estado</th><th className="p-3">Cadenas</th><th className="p-3">Acciones</th></tr></thead><tbody>{users.map(user => <tr key={user.id} className="border-b last:border-0">
      <td className="p-3">{user.profile?.full_name || "Sin perfil"}</td><td className="p-3">{user.email || "—"}</td><td className="p-3">{user.profile?.global_role || "Pendiente"}</td><td className="p-3">{user.profile?.status || "Sin acceso"}</td><td className="p-3">{user.profile?.global_role === "ADMIN" ? "Global" : user.grants.length}</td>
      <td className="p-3"><div className="flex flex-wrap gap-2">{admin && user.id !== profile.id && <>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => openInvite(user)}>{user.profile ? "Editar" : "Completar alta"}</Button>
        {user.profile?.status === "ACTIVE" && <Button size="sm" variant="outline" disabled={busy} onClick={() => void resend(user)}>Enviar enlace</Button>}
        {user.profile?.status === "ACTIVE" && <Button size="sm" variant="outline" disabled={busy} onClick={() => void deactivate(user)}>Eliminar acceso</Button>}
      </>}</div></td>
    </tr>)}</tbody></table>{users.length === 0 && <p className="p-4 text-sm text-slate-500">No hay usuarios para mostrar.</p>}</div>}
    {formOpen && admin && <form aria-label={target?.profile ? "Editar usuario" : "Agregar usuario"} onSubmit={save} className="space-y-4 rounded-xl border bg-white p-5">
      <h2 className="text-lg font-semibold">{target?.profile ? "Editar usuario y permisos" : target ? "Completar alta existente" : "Invitar nuevo usuario"}</h2>
      <div className="grid gap-4 md:grid-cols-2"><div className="space-y-2"><Label htmlFor="admin-name">Nombre</Label><Input id="admin-name" value={draft.full_name} onChange={event => setDraft(current => ({ ...current, full_name: event.target.value }))} required minLength={2} maxLength={150}/></div>
        <div className="space-y-2"><Label htmlFor="admin-email">Correo</Label><Input id="admin-email" type="email" value={email} onChange={event => setEmail(event.target.value)} readOnly={Boolean(target)} required/></div></div>
      <div className="grid gap-4 md:grid-cols-2"><div className="space-y-2"><Label htmlFor="admin-role">Rol</Label><select id="admin-role" className="h-10 w-full rounded-md border bg-white px-3" value={draft.global_role} onChange={event => {
        const role = event.target.value as Role;
        setDraft(current => ({ ...current, global_role: role, grants: role === "ADMIN" ? [] : role === "VIEWER"
          ? current.grants.map(grant => emptyGrant(grant.chain_id)) : current.grants }));
      }}><option value="VIEWER">VIEWER · consulta</option><option value="EDITOR">EDITOR · permisos por cadena</option><option value="ADMIN">ADMIN · acceso global</option></select></div>
        {target?.profile && <div className="space-y-2"><Label htmlFor="admin-status">Estado</Label><select id="admin-status" className="h-10 w-full rounded-md border bg-white px-3" value={draft.status} onChange={event => setDraft(current => ({ ...current, status: event.target.value as UserMutation["status"], grants: event.target.value === "INACTIVE" ? [] : current.grants }))}><option value="ACTIVE">Activo</option><option value="INACTIVE">Inactivo</option></select></div>}</div>
      {draft.global_role === "ADMIN" ? <p className="text-sm text-slate-500">ADMIN tiene acceso global; no requiere cadenas individuales.</p> : draft.status === "ACTIVE" && <fieldset className="space-y-3"><legend className="font-medium">Cadenas y permisos</legend><p className="text-xs text-slate-500">La selección concede consulta. EDITOR puede recibir permisos adicionales por cadena.</p>
        {activeChains.map(chain => { const grant = draft.grants.find(row => row.chain_id === chain.id); return <div key={chain.id} className="rounded-lg border p-3"><label className="flex items-center gap-2 text-sm font-medium"><input type="checkbox" checked={Boolean(grant)} onChange={() => toggleChain(chain.id)}/>{chain.name}</label>
          {grant && draft.global_role === "EDITOR" && <div className="mt-3 flex flex-wrap gap-4 pl-6">{permissionLabels.map(([permission, label]) => <label key={permission} className="flex items-center gap-2 text-xs"><input type="checkbox" checked={grant[permission]} onChange={() => togglePermission(chain.id, permission)}/>{label}</label>)}</div>}</div>; })}
        {!activeChains.length && <p className="text-sm text-amber-700">No hay cadenas disponibles para asignar.</p>}
      </fieldset>}
      <div className="flex gap-2"><Button type="submit" disabled={busy}>{busy ? "Guardando…" : target?.profile ? "Guardar cambios" : "Enviar invitación"}</Button><Button type="button" variant="outline" disabled={busy} onClick={() => setFormOpen(false)}>Cancelar</Button></div>
    </form>}
  </section>;
}
