import { afterEach, describe, expect, it, vi } from "vitest";
import { getBrowserClient, validatePublicConfig } from "../../lib/supabase/client";

const key = "sb_publishable_synthetic_local_test_key";
const local = "http://127.0.0.1:8000";
const auth = "https://bskoyqhbgrycpwhydnnr.supabase.co";
function environment(mode: string, enabled?: string) {
  vi.stubEnv("NODE_ENV", mode);
  vi.stubEnv("NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED", enabled);
}
afterEach(() => vi.unstubAllEnvs());

describe("Explicit development-only PostgreSQL endpoint", () => {
  it.each([local, local + "/"])("accepts only the authorized canonical loopback data URL %s alongside hosted Auth", dataUrl => {
    environment("development", "true");
    expect(validatePublicConfig({ url: auth, key, dataUrl, dataKey: key })).toBe(true);
    expect(validatePublicConfig({ url: dataUrl, key })).toBe(false);
    expect(() => getBrowserClient({ url: dataUrl, key })).toThrow("public_config_unavailable");
  });

  it.each(["production", "test", ""])("rejects HTTP in %s even when the local flag is true", mode => {
    environment(mode, "true");
    expect(validatePublicConfig({ url: auth, key, dataUrl: local, dataKey: key })).toBe(false);
    expect(() => getBrowserClient({ url: local, key })).toThrow("public_config_unavailable");
  });

  it.each([undefined, "false", "", "1", "TRUE"])("requires the exact explicit true flag, received %s", flag => {
    environment("development", flag);
    expect(validatePublicConfig({ url: auth, key, dataUrl: local, dataKey: key })).toBe(false);
  });

  it.each([
    "http://remote.example:8000", "http://127.0.0.1.example:8000", "http://localhost:8000",
    "http://127.0.0.2:8000", "http://[::1]:8000", "http://127.0.0.1:8001", "http://127.0.0.1",
    "http://127.1:8000", "http://2130706433:8000", "http://user:password@127.0.0.1:8000",
    "http://127.0.0.1:8000/rest/v1", "http://127.0.0.1:8000?project=remote", "http://127.0.0.1:8000#remote",
  ])("rejects every other HTTP destination or URL shape: %s", url => {
    environment("development", "true");
    expect(validatePublicConfig({ url: auth, key, dataUrl: url, dataKey: key })).toBe(false);
  });

  it("preserves public-key checks while requiring the hosted Auth identity", () => {
    environment("development", "true");
    const token = (role: string) => `synthetic.${btoa(JSON.stringify({ role }))}.synthetic`;
    expect(validatePublicConfig({ url: auth, key: token("anon"), dataUrl: local, dataKey: key })).toBe(true);
    for (const rejected of [token("service_role"), "sb_secret_synthetic_rejected", "sb_publishable_short", "not-a-key"])
      expect(validatePublicConfig({ url: auth, key: rejected, dataUrl: local, dataKey: key })).toBe(false);
  });

  it("preserves the existing HTTPS production path without enabling HTTP", () => {
    environment("production", "false");
    expect(validatePublicConfig({ url: "https://example.supabase.co", key })).toBe(true);
    expect(validatePublicConfig({ url: local, key })).toBe(false);
  });
});
