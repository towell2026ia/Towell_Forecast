import { createClient } from "@supabase/supabase-js";
import { assertActiveProfile, SupabaseForecastReadRepository } from "@/lib/forecast-data";
import { validatePublicConfig } from "@/lib/supabase/client";
import { emptyFilters, type Profile } from "@/lib/supabase/types";
import { logReadDiagnostic, parseReadDiagnostic, PortalReadError, type PortalReadDiagnostic } from "@/lib/portal-read-diagnostic";
import { publicPortalConfig } from "@/lib/supabase/public-config";

async function authorize(request: Request) {
  const config = publicPortalConfig();
  const authorization = request.headers.get("authorization");
  const denied = (status: number) => Response.json({ error: "portal_access_denied" }, { status, headers: { "Cache-Control": "no-store" } });
  if (!authorization?.startsWith("Bearer ") || authorization.length > 8192) return denied(401);
  if (!validatePublicConfig(config)) return denied(503);
  const token = authorization.slice(7);
  const client = createClient(config.url, config.key, { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false }, global: { headers: { Authorization: authorization } } });
  const { data, error } = await client.auth.getUser(token);
  if (error || !data.user) return denied(401);
  const lookup = await client.from("profiles").select("id,full_name,global_role,status").eq("id", data.user.id).maybeSingle();
  let profile: Profile;
  try { profile = assertActiveProfile(lookup.data as Profile | null, data.user.id); } catch { return denied(403); }
  if (lookup.error) return denied(403);
  return { client, profile, userId: data.user.id };
}

export async function GET(request: Request) {
  let repository: SupabaseForecastReadRepository | undefined;
  try {
    const access = await authorize(request);
    if (access instanceof Response) return access;
    repository = new SupabaseForecastReadRepository(access.client, access.userId);
    const repo = repository;
    // Independent probes: a broken period picker must not conceal whether the
    // first history page actually works. Use the verified user's JWT/RLS only.
    async function probe<T>(endpoint: PortalReadDiagnostic["endpoint"], table: PortalReadDiagnostic["table"], operation: PortalReadDiagnostic["operation"], read: () => Promise<T>) {
      try {
        const value = await read();
        logReadDiagnostic(repo.getReadDiagnostic(endpoint) ?? { endpoint, table, operation, http_status: 0, code: "OK" });
        return value;
      } catch (error) {
        if (!(error instanceof PortalReadError)) logReadDiagnostic({ endpoint, table, operation, http_status: 0, code: "READ_FAILED" });
        return null;
      }
    }
    const [chains, categories, products, periods, history] = await Promise.all([
      probe("getVisibleChains", "chains", "complete", () => repo.getVisibleChains()),
      probe("getCategories", "categories", "complete", () => repo.getCategories()),
      probe("getProducts", "products", "complete", () => repo.getProducts()),
      probe("getPeriods", "portal_monthly_observations_current", "complete", () => repo.getPeriods()),
      probe("getHistoricalObservations", "portal_monthly_observations_current", "page_1", () => repo.getHistoricalObservations(emptyFilters, { size: 50 })),
    ]);
    if (!chains || !categories || !products || !periods || !history) return Response.json({ error: "read_failed" }, { status: 502, headers: { "Cache-Control": "no-store" } });
    const summary = await repo.getHistoricalSummary(emptyFilters);
    const profile = access.profile;
    const result = { status: "PASS_AUTHENTICATED_READ", role: profile.global_role, chains: chains.length, scopes_with_history: chains.filter(c => c.has_history).length, products: summary.productCount, categories: categories.length, observations: summary.observationCount };
    // Aggregate technical attestation only. No bearer, email or row-level data.
    console.info(`[TowellPortal] ${JSON.stringify(result)}`);
    return Response.json(result, { headers: { "Cache-Control": "private, no-store" } });
  } catch {
    console.info("[TowellPortal] authenticated_read_error");
    return Response.json({ error: "read_failed" }, { status: 502, headers: { "Cache-Control": "no-store" } });
  } finally { repository?.dispose(); }
}

export async function POST(request: Request) {
  try {
    // Browser-only controlled failure reports. They are not server attestations,
    // authorization claims, audit writes or proof that an operation passed.
    const access = await authorize(request);
    if (access instanceof Response) return access;
    if (request.headers.get("content-type")?.split(";")[0] !== "application/json") return Response.json({ error: "invalid_diagnostic" }, { status: 400 });
    const reader = request.body?.getReader();
    if (!reader) return Response.json({ error: "invalid_diagnostic" }, { status: 400 });
    const chunks: Uint8Array[] = []; let length = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > 2048) { await reader.cancel(); return Response.json({ error: "invalid_diagnostic" }, { status: 400 }); }
      chunks.push(value);
    }
    const body = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
    const diagnostic = parseReadDiagnostic(JSON.parse(new TextDecoder().decode(body)));
    if (!diagnostic || diagnostic.code === "OK") return Response.json({ error: "invalid_diagnostic" }, { status: 400 });
    console.info(`[TowellPortalBrowserRead] ${JSON.stringify(diagnostic)}`);
    return new Response(null, { status: 204, headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ error: "invalid_diagnostic" }, { status: 400, headers: { "Cache-Control": "no-store" } });
  }
}
