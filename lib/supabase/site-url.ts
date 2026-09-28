// Public deployment configuration, never identity or a credential.
export function normalizeSiteUrl(value: string, development = false): string {
  const url = new URL(value);
  const localhost = url.hostname === "localhost";
  if (url.username || url.password || url.search || url.hash || url.pathname !== "/") throw new Error("invalid_site_url");
  if (url.protocol !== "https:" && !(development && localhost && url.protocol === "http:")) throw new Error("invalid_site_url");
  if (localhost && !development) throw new Error("invalid_site_url");
  return url.origin;
}

export function recoveryOrigin(siteUrl: string | undefined, browserOrigin: string, development = false): string {
  const current = new URL(browserOrigin);
  if (development && current.hostname === "localhost") return normalizeSiteUrl(browserOrigin, true);
  // Invalid configured URLs fail closed; they never silently redirect to Sites.
  return normalizeSiteUrl(siteUrl?.trim() || browserOrigin, development);
}
