import { afterEach, describe, expect, it, vi } from "vitest";
import { normalizeSiteUrl, recoveryOrigin } from "../../lib/supabase/site-url";
import { publicPortalConfig } from "../../lib/supabase/public-config";
import { passwordRecoveryRedirect } from "../../lib/supabase/recovery";
import { validatePublicConfig } from "../../lib/supabase/client";
import { readFileSync } from "node:fs";
import { GET } from "../../app/api/portal-version/route";
afterEach(() => vi.unstubAllEnvs());
describe("Canonical Netlify public config", () => {
  it("normalizes HTTPS without a trailing slash", () => { expect(normalizeSiteUrl("https://canonical.netlify.app/")).toBe("https://canonical.netlify.app"); });
  it("rejects credentials, unsafe schemes, paths, query and fragment", () => {
    for (const url of ["http://remote.example", "https://user:password@example.test", "javascript:alert(1)", "https://example.test/path", "https://example.test/?token=secret", "https://example.test/#fragment", "not-a-url", "http://localhost:3000"]) expect(() => normalizeSiteUrl(url)).toThrow();
    expect(normalizeSiteUrl("http://localhost:3000/", true)).toBe("http://localhost:3000");
  });
  it("production recovery always targets configured Netlify, even from rollback host", () => {
    const origin = recoveryOrigin("https://canonical.netlify.app/", "https://rollback.chatgpt.site");
    expect(passwordRecoveryRedirect(origin)).toBe("https://canonical.netlify.app/update-password");
    expect(() => recoveryOrigin("https://user:secret@wrong.test", "https://rollback.chatgpt.site")).toThrow();
  });
  it("local development retains its localhost origin, never production HTTP", () => {
    expect(passwordRecoveryRedirect(recoveryOrigin("https://canonical.netlify.app", "http://localhost:3000", true))).toBe("http://localhost:3000/update-password");
    expect(() => recoveryOrigin(undefined, "http://localhost:3000")).toThrow();
    expect(recoveryOrigin(undefined, "https://canonical.netlify.app")).toBe("https://canonical.netlify.app");
  });
  it("all three pages receive the typed canonical public configuration", () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://example.supabase.co");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "sb_publishable_synthetic_test_key");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "");
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://canonical.netlify.app/");
    const config = publicPortalConfig(); expect(config.siteUrl).toBe("https://canonical.netlify.app"); expect(validatePublicConfig(config)).toBe(true);
    expect(config.key).toBe("sb_publishable_synthetic_test_key");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_preferred_synthetic_key");
    expect(publicPortalConfig().key).toBe("sb_publishable_preferred_synthetic_key");
    for (const path of ["app/page.tsx", "app/login/page.tsx", "app/update-password/page.tsx"]) expect(readFileSync(path, "utf8")).toContain("config={publicPortalConfig()}");
    expect(validatePublicConfig({ ...config, siteUrl: "http://not-local.test" })).toBe(false);
  });
  it("public provenance endpoint never returns secrets or session data", async () => {
    vi.stubEnv("NEXT_PUBLIC_GIT_SHA", "a".repeat(40)); vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://canonical.netlify.app");
    expect(await GET().json()).toEqual({ git_sha: "a".repeat(40), site_url: "https://canonical.netlify.app" });
    expect(GET().headers.get("cache-control")).toBe("no-store");
  });
});
