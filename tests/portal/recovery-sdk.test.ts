import { afterEach, expect, it, vi } from "vitest";
import { clearBrowserSession, getBrowserClient } from "../../lib/supabase/client";

const config = { url: "https://example.supabase.co", key: "sb_publishable_synthetic_test_key" };
afterEach(() => { clearBrowserSession(config); localStorage.clear(); sessionStorage.clear(); window.history.replaceState(null, "", "/"); vi.unstubAllGlobals(); });

it("official SDK verifies recovery credentials, emits PASSWORD_RECOVERY and removes the URL fragment", async () => {
  // Entire HTTP boundary is synthetic: no email, real token or remote mutation.
  vi.stubGlobal("BroadcastChannel", undefined);
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    expect(String(input)).toBe("https://example.supabase.co/auth/v1/user");
    return Response.json({ id: "fixture-recovery-user", aud: "authenticated", role: "authenticated", email: "fixture@example.test", created_at: "2026-01-01T00:00:00Z", app_metadata: {}, user_metadata: {} });
  });
  vi.stubGlobal("fetch", fetch);
  window.history.replaceState(null, "", "/update-password#type=recovery&access_token=SYNTHETIC_ONLY&refresh_token=SYNTHETIC_REFRESH_ONLY&expires_in=3600&token_type=bearer");
  const sdk = getBrowserClient(config);
  const event = new Promise<string>(resolve => {
    const { data } = sdk.auth.onAuthStateChange(name => {
      if (name === "PASSWORD_RECOVERY") { data.subscription.unsubscribe(); resolve(name); }
    });
  });
  expect(await event).toBe("PASSWORD_RECOVERY");
  const result = await sdk.auth.getUser();
  expect(result.error).toBeNull(); expect(result.data.user?.id).toBe("fixture-recovery-user");
  expect(fetch).toHaveBeenCalled(); expect(window.location.hash).toBe("");
});
