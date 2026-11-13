import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import type { PublicSupabaseConfig } from "./types";
import { clearRecoverySession, isRecoveryCallback } from "./recovery";
import { normalizeSiteUrl } from "./site-url";

const hostedAuthOrigin = "https://bskoyqhbgrycpwhydnnr.supabase.co";
const localDataOrigin = "http://127.0.0.1:8000";
export function isHybridConfig(config: PublicSupabaseConfig): boolean {
  return "dataUrl" in config || "dataKey" in config || process.env.NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED === "true";
}
export function validatePublicConfig(config: PublicSupabaseConfig): boolean {
  try {
    const url = new URL(config.url);
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) return false;
    if (isHybridConfig(config) && (process.env.NODE_ENV !== "development" || process.env.NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED !== "true"
      || url.origin !== hostedAuthOrigin || ![localDataOrigin, localDataOrigin + "/"].includes(config.dataUrl ?? "")
      || !config.dataKey?.startsWith("sb_publishable_") || config.dataKey.length <= 20)) return false;
    if (config.siteUrl?.trim()) normalizeSiteUrl(config.siteUrl.trim(), process.env.NODE_ENV === "development");
    if (config.key.startsWith("sb_publishable_")) return config.key.length > 20;
    const claims = JSON.parse(atob(config.key.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return claims.role === "anon";
  } catch { return false; }
}

function requestTarget(input: RequestInfo | URL, init?: RequestInit) {
  return { url: new URL(typeof input === "string" || input instanceof URL ? input : input.url),
    method: (init?.method ?? (typeof input === "object" && "method" in input ? input.method : "GET")).toUpperCase() };
}
function authOnlyFetch(input: RequestInfo | URL, init?: RequestInit) {
  const { url, method } = requestTarget(input, init);
  const allowed = method === "GET" && url.pathname === "/auth/v1/user" || method === "POST" &&
    (url.pathname === "/auth/v1/token" || url.pathname === "/auth/v1/logout" && url.searchParams.get("scope") === "local");
  if (url.origin !== hostedAuthOrigin || !allowed || url.username || url.password) throw new Error("hybrid_auth_route_denied");
  return fetch(input, init);
}
function localDataFetch(input: RequestInfo | URL, init?: RequestInit) {
  const { url, method } = requestTarget(input, init);
  const read = method === "GET" || method === "HEAD" || method === "POST" && /^\/rest\/v1\/rpc\/operational_read_[a-z_]+$/.test(url.pathname);
  if (url.origin !== localDataOrigin || !url.pathname.startsWith("/rest/v1/") || !read || url.username || url.password) throw new Error("hybrid_data_route_denied");
  return fetch(input, init);
}

let singleton: SupabaseClient | null = null;
let configIdentity = "";
export function getBrowserClient(config: PublicSupabaseConfig): SupabaseClient {
  if (!validatePublicConfig(config)) throw new Error("public_config_unavailable");
  const hybrid = isHybridConfig(config);
  const identity = `${config.url}:${config.key}:${hybrid ? "auth-only" : "standard"}`;
  if (!singleton || configIdentity !== identity) {
    // The public site URL never changes SDK identity or recreates this client.
    void singleton?.auth.stopAutoRefresh();
    singleton = createClient(config.url, config.key, { ...(hybrid ? { global: { fetch: authOnlyFetch } } : {}), auth: {
      persistSession: true, autoRefreshToken: true, flowType: "implicit",
      detectSessionInUrl: isRecoveryCallback,
      storageKey: `towell-auth-${new URL(config.url).hostname}`,
    } });
    configIdentity = identity;
  }
  return singleton;
}

export function getDataClient(config: PublicSupabaseConfig, authClient: SupabaseClient = getBrowserClient(config)): SupabaseClient {
  if (!isHybridConfig(config)) return authClient;
  if (!validatePublicConfig(config)) throw new Error("public_config_unavailable");
  return createClient(config.dataUrl!, config.dataKey!, {
    accessToken: async () => {
      const { data, error } = await authClient.auth.getSession();
      return error ? null : data.session?.access_token ?? null;
    },
    global: { fetch: localDataFetch },
  });
}

export async function invalidateLocalDataSession(config: PublicSupabaseConfig, authClient: SupabaseClient) {
  if (!isHybridConfig(config)) return;
  if (!validatePublicConfig(config)) throw new Error("public_config_unavailable");
  const { data } = await authClient.auth.getSession();
  if (!data.session?.access_token) return;
  // Explicit local session invalidation, never a password or refresh-token
  // transfer to the data service. Hosted Auth remains the session authority.
  const response = await fetch(localDataOrigin + "/auth/v1/logout", { method: "POST", headers: {
    Authorization: `Bearer ${data.session.access_token}`, apikey: config.dataKey!,
  }, cache: "no-store" });
  if (!response.ok) throw new Error("local_logout_failed");
}

export function clearBrowserSession(config: PublicSupabaseConfig) {
  clearRecoverySession();
  if (typeof window !== "undefined" && config.url) {
    window.localStorage.removeItem(`towell-auth-${new URL(config.url).hostname}`);
  }
  void singleton?.auth.stopAutoRefresh();
  singleton = null; configIdentity = "";
}

// Log only fixed event names. Never accept payloads, errors or auth objects.
export function logPortalEvent(event: "login_success" | "login_failure" | "logout" | "read_error") {
  console.info(`[TowellPortal] ${event}`);
}
