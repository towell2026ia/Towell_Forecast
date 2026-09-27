import { createClient } from "@supabase/supabase-js";
import { assertActiveProfile, SupabaseForecastReadRepository } from "@/lib/forecast-data";
import { validatePublicConfig } from "@/lib/supabase/client";
import { emptyFilters, type Profile } from "@/lib/supabase/types";

export async function GET(request: Request) {
  const config = { url: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "", key: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "" };
  const authorization = request.headers.get("authorization");
  const denied = (status: number) => Response.json({ error: "portal_access_denied" }, { status, headers: { "Cache-Control": "no-store" } });
  if (!authorization?.startsWith("Bearer ") || authorization.length > 8192) return denied(401);
  if (!validatePublicConfig(config)) return denied(503);
  const token = authorization.slice(7);
  const client = createClient(config.url, config.key, { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false }, global: { headers: { Authorization: authorization } } });
  let repository: SupabaseForecastReadRepository | undefined;
  try {
    const { data, error } = await client.auth.getUser(token);
    if (error || !data.user) return denied(401);
    const lookup = await client.from("profiles").select("id,full_name,global_role,status").eq("id", data.user.id).maybeSingle();
    let profile: Profile;
    try { profile = assertActiveProfile(lookup.data as Profile | null, data.user.id); } catch { return denied(403); }
    if (lookup.error) return denied(403);
    repository = new SupabaseForecastReadRepository(client, data.user.id);
    const [chains, categories, summary] = await Promise.all([repository.getVisibleChains(), repository.getCategories(), repository.getHistoricalSummary(emptyFilters)]);
    const result = { status: "PASS_AUTHENTICATED_READ", role: profile.global_role, chains: chains.length, scopes_with_history: chains.filter(c => c.has_history).length, products: summary.productCount, categories: categories.length, observations: summary.observationCount };
    // Aggregate technical attestation only. No bearer, email or row-level data.
    console.info(`[TowellPortal] ${JSON.stringify(result)}`);
    return Response.json(result, { headers: { "Cache-Control": "private, no-store" } });
  } catch {
    console.info("[TowellPortal] authenticated_read_error");
    return Response.json({ error: "read_failed" }, { status: 502, headers: { "Cache-Control": "no-store" } });
  } finally { repository?.dispose(); }
}
