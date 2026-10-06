import { createClient, type SupabaseClient, type User } from "npm:@supabase/supabase-js@2.117.2";
import { normalizedEmail, parseUserMutation, validUserId, type ManagedUser, type UserMutation } from "../../../lib/supabase/user-admin-contract.ts";

// This function is deployed in Supabase, never in Netlify or the browser.
// The platform's JWT check stays enabled; getUser verifies the exact session
// again and the database profile, not request-supplied role headers, is authority.
const productionOrigin = "https://towell-forecastia.netlify.app";
const allowedOrigins = new Set([productionOrigin, "http://localhost:3000", "http://localhost:5173"]);
const redirectTo = `${productionOrigin}/update-password`;
const maxBodyBytes = 16_384;

function reply(status: number, payload: Record<string, unknown>, origin: string | null) {
  return Response.json(payload, { status, headers: {
    "Cache-Control": "no-store", "Vary": "Origin",
    ...(origin && allowedOrigins.has(origin) ? { "Access-Control-Allow-Origin": origin } : {}),
  } });
}
function serviceClient(): SupabaseClient | null {
  const url = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !key) return null;
  return createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
}
async function allAuthUsers(client: SupabaseClient): Promise<User[]> {
  const users: User[] = [];
  for (let page = 1; page <= 20; page++) {
    const { data, error } = await client.auth.admin.listUsers({ page, perPage: 1000 });
    if (error || !data) throw new Error("directory_unavailable");
    users.push(...data.users);
    if (data.users.length < 1000) return users;
  }
  throw new Error("directory_limit");
}
async function manage(client: SupabaseClient, actorId: string, targetId: string, mutation: UserMutation) {
  const { error } = await client.rpc("portal_manage_user", {
    p_actor_id: actorId, p_target_id: targetId, p_full_name: mutation.full_name,
    p_role: mutation.global_role, p_status: mutation.status, p_access: mutation.grants,
  });
  if (error) throw new Error("management_failed");
}
async function directory(client: SupabaseClient): Promise<ManagedUser[]> {
  const [users, profilesResult, grantsResult] = await Promise.all([
    allAuthUsers(client),
    client.from("profiles").select("id,full_name,global_role,status").limit(20_000),
    client.from("user_chain_access").select("user_id,chain_id,can_edit,can_import,can_run_forecast,can_approve").limit(20_000),
  ]);
  if (profilesResult.error || grantsResult.error || !profilesResult.data || !grantsResult.data) throw new Error("directory_unavailable");
  const profiles = new Map(profilesResult.data.map(profile => [profile.id, profile]));
  const grants = new Map<string, ManagedUser["grants"]>();
  for (const row of grantsResult.data) {
    const values = grants.get(row.user_id) ?? [];
    values.push({ chain_id: row.chain_id, can_edit: row.can_edit, can_import: row.can_import,
      can_run_forecast: row.can_run_forecast, can_approve: row.can_approve });
    grants.set(row.user_id, values);
  }
  return users.map(user => ({ id: user.id, email: user.email ?? "", email_confirmed: Boolean(user.email_confirmed_at),
    invited: Boolean(user.invited_at), last_sign_in_at: user.last_sign_in_at ?? null,
    profile: profiles.get(user.id) ?? null, grants: grants.get(user.id) ?? [],
  })).sort((a, b) => a.email.localeCompare(b.email));
}

Deno.serve(async request => {
  const origin = request.headers.get("origin");
  if (origin && !allowedOrigins.has(origin)) return reply(403, { error: "origin_denied" }, origin);
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: {
    "Access-Control-Allow-Origin": origin ?? productionOrigin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
    "Access-Control-Max-Age": "3600", "Vary": "Origin",
  } });
  if (request.method !== "POST") return reply(405, { error: "method_not_allowed" }, origin);
  const authorization = request.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ") || authorization.length > 8192) return reply(401, { error: "unauthorized" }, origin);
  const client = serviceClient();
  if (!client) return reply(503, { error: "service_unavailable" }, origin);
  try {
    const { data: verified, error: identityError } = await client.auth.getUser(authorization.slice(7));
    if (identityError || !verified.user) return reply(401, { error: "unauthorized" }, origin);
    const actorId = verified.user.id;
    const { data: actor, error: actorError } = await client.from("profiles")
      .select("id,status,global_role").eq("id", actorId).maybeSingle();
    if (actorError || actor?.status !== "ACTIVE" || actor.global_role !== "ADMIN") return reply(403, { error: "admin_required" }, origin);
    if (request.headers.get("content-type")?.split(";")[0] !== "application/json") return reply(400, { error: "invalid_request" }, origin);
    if (Number(request.headers.get("content-length") ?? 0) > maxBodyBytes) return reply(413, { error: "request_too_large" }, origin);
    const raw = await request.text();
    if (new TextEncoder().encode(raw).byteLength > maxBodyBytes) return reply(413, { error: "request_too_large" }, origin);
    const body = JSON.parse(raw) as Record<string, unknown>;
    if (!body || typeof body !== "object" || Array.isArray(body)) return reply(400, { error: "invalid_request" }, origin);

    if (body.action === "list") return reply(200, { users: await directory(client) }, origin);
    if (body.action === "invite") {
      const email = normalizedEmail(body.email);
      const mutation = parseUserMutation(body.user);
      if (!email || !mutation || mutation.status !== "ACTIVE") return reply(400, { error: "invalid_request" }, origin);
      const existing = (await allAuthUsers(client)).find(user => user.email?.toLowerCase() === email);
      if (existing) {
        const { data: profile, error } = await client.from("profiles").select("id").eq("id", existing.id).maybeSingle();
        if (error) throw new Error("management_failed");
        if (profile) return reply(409, { error: "already_managed" }, origin);
        await manage(client, actorId, existing.id, mutation);
        const { error: mailError } = await client.auth.resetPasswordForEmail(email, { redirectTo });
        return reply(200, { id: existing.id, existing_auth_user: true, email_sent: !mailError }, origin);
      }
      const { data: invited, error: inviteError } = await client.auth.admin.inviteUserByEmail(email, { redirectTo });
      if (inviteError || !invited.user?.id) return reply(502, { error: "invitation_failed" }, origin);
      try { await manage(client, actorId, invited.user.id, mutation); }
      catch {
        // Compensate only the identity created by this request. Never delete an
        // existing Auth account, even when its application profile is missing.
        await client.auth.admin.deleteUser(invited.user.id);
        throw new Error("management_failed");
      }
      return reply(200, { id: invited.user.id, existing_auth_user: false, email_sent: true }, origin);
    }
    if (body.action === "update") {
      if (!validUserId(body.target_id) || body.target_id === actorId) return reply(400, { error: "invalid_target" }, origin);
      const mutation = parseUserMutation(body.user);
      if (!mutation) return reply(400, { error: "invalid_request" }, origin);
      const { data: current, error } = await client.from("profiles").select("id").eq("id", body.target_id).maybeSingle();
      if (error || !current) return reply(404, { error: "user_not_managed" }, origin);
      await manage(client, actorId, body.target_id, mutation);
      return reply(200, { ok: true }, origin);
    }
    if (body.action === "deactivate") {
      if (!validUserId(body.target_id) || body.target_id === actorId) return reply(400, { error: "invalid_target" }, origin);
      const { data: current, error } = await client.from("profiles")
        .select("id,full_name,global_role,status").eq("id", body.target_id).maybeSingle();
      if (error || !current) return reply(404, { error: "user_not_managed" }, origin);
      const { count: leftoverGrants, error: grantError } = await client.from("user_chain_access")
        .select("chain_id", { count: "exact", head: true }).eq("user_id", body.target_id);
      if (grantError) throw new Error("management_failed");
      if (current.status !== "INACTIVE" || (leftoverGrants ?? 0) > 0) await manage(client, actorId, body.target_id,
        { full_name: current.full_name, global_role: current.global_role, status: "INACTIVE", grants: [] });
      return reply(200, { ok: true }, origin);
    }
    if (body.action === "resend") {
      if (!validUserId(body.target_id)) return reply(400, { error: "invalid_target" }, origin);
      const { data: current, error } = await client.from("profiles")
        .select("id,status").eq("id", body.target_id).maybeSingle();
      if (error || current?.status !== "ACTIVE") return reply(404, { error: "user_not_managed" }, origin);
      const { data: target, error: targetError } = await client.auth.admin.getUserById(body.target_id);
      if (targetError || !target.user?.email) return reply(404, { error: "user_not_managed" }, origin);
      const { error: mailError } = await client.auth.resetPasswordForEmail(target.user.email, { redirectTo });
      if (mailError) return reply(502, { error: "email_not_sent" }, origin);
      return reply(200, { email_sent: true }, origin);
    }
    return reply(400, { error: "invalid_action" }, origin);
  } catch {
    // Never return SDK errors, SQL, identity details or credentials to clients.
    return reply(502, { error: "user_management_unavailable" }, origin);
  }
});
