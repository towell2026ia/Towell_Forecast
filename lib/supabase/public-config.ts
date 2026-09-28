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
  return {
    url: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
    key: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim() || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim() || "",
    siteUrl,
  };
}
