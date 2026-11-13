import type { PublicSupabaseConfig } from "./types";
import { normalizeSiteUrl } from "./site-url";

// Server pages pass only these public values to browser components.
export function publicPortalConfig(): PublicSupabaseConfig {
  const configured = process.env.NEXT_PUBLIC_SITE_URL?.trim();
  let siteUrl = configured ?? "";
  if (configured) {
    try { siteUrl = normalizeSiteUrl(configured, process.env.NODE_ENV === "development"); }
    catch { /* Preserve invalid input so public config validation fails closed. */ }
  }
  const config: PublicSupabaseConfig = {
    url: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
    key: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim() || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim() || "",
    siteUrl,
  };
  if (process.env.NEXT_PUBLIC_LOCAL_DATA_URL !== undefined || process.env.NEXT_PUBLIC_LOCAL_DATA_PUBLISHABLE_KEY !== undefined ||
      process.env.NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED === "true") {
    // Public connection metadata only. Empty/incomplete split config must fail
    // closed instead of falling back to the hosted commercial Data API.
    config.dataUrl = process.env.NEXT_PUBLIC_LOCAL_DATA_URL ?? "";
    config.dataKey = process.env.NEXT_PUBLIC_LOCAL_DATA_PUBLISHABLE_KEY?.trim() ?? "";
  }
  return config;
}
