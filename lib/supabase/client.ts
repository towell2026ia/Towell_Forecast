import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import type { PublicSupabaseConfig } from "./types";
import { clearRecoverySession, isRecoveryCallback } from "./recovery";
import { normalizeSiteUrl } from "./site-url";

export function validatePublicConfig(config: PublicSupabaseConfig): boolean {
  try {
    const url = new URL(config.url);
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/") return false;
    if (config.siteUrl?.trim()) normalizeSiteUrl(config.siteUrl.trim(), process.env.NODE_ENV === "development");
    if (config.key.startsWith("sb_publishable_")) return config.key.length > 20;
    const claims = JSON.parse(atob(config.key.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return claims.role === "anon";
  } catch { return false; }
}

let singleton: SupabaseClient | null = null;
let configIdentity = "";
export function getBrowserClient(config: PublicSupabaseConfig): SupabaseClient {
  if (!validatePublicConfig(config)) throw new Error("public_config_unavailable");
  const identity = `${config.url}:${config.key}`;
  if (!singleton || configIdentity !== identity) {
    // The public site URL never changes SDK identity or recreates this client.
    singleton = createClient(config.url, config.key, { auth: {
      persistSession: true, autoRefreshToken: true, flowType: "implicit",
      detectSessionInUrl: isRecoveryCallback,
      storageKey: `towell-auth-${new URL(config.url).hostname}`,
    } });
    configIdentity = identity;
  }
  return singleton;
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
