import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  getUser: vi.fn(), lookup: vi.fn(), createClient: vi.fn(),
  chains: vi.fn(), categories: vi.fn(), summary: vi.fn(), dispose: vi.fn(),
}));
vi.mock("@supabase/supabase-js", () => ({ createClient: state.createClient }));
vi.mock("../../lib/forecast-data", async importOriginal => {
  const original = await importOriginal<typeof import("../../lib/forecast-data")>();
  return { ...original, SupabaseForecastReadRepository: class {
    getVisibleChains = state.chains;
    getCategories = state.categories;
    getHistoricalSummary = state.summary;
    dispose = state.dispose;
  } };
});
import { GET } from "../../app/api/portal-validation/route";

const jwt = "synthetic-user-bearer-not-a-real-token";
const profile = { id: "user", full_name: "Controlled fixture", global_role: "VIEWER", status: "ACTIVE" };
const request = (headers: Record<string, string> = { authorization: `Bearer ${jwt}` }) => new Request("https://example.test/api/portal-validation", { headers });
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://example.supabase.co");
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_synthetic_test_key");
  state.getUser.mockResolvedValue({ data: { user: { id: "user" } }, error: null });
  state.lookup.mockResolvedValue({ data: profile, error: null });
  const query = { select: vi.fn().mockReturnThis(), eq: vi.fn().mockReturnThis(), maybeSingle: state.lookup };
  state.createClient.mockReturnValue({ auth: { getUser: state.getUser }, from: vi.fn().mockReturnValue(query) });
  state.chains.mockResolvedValue([{ has_history: true }]);
  state.categories.mockResolvedValue([{}]);
  state.summary.mockResolvedValue({ productCount: 2, observationCount: 6 });
  vi.spyOn(console, "info").mockImplementation(() => {});
});
describe("Read-only authenticated diagnostic", () => {
  it("rejects missing identity and forged role/user headers before querying", async () => {
    const response = await GET(request({ "X-User-Role": "admin", "X-User-Id": "user" }));
    expect(response.status).toBe(401); expect(state.createClient).not.toHaveBeenCalled();
  });
  it("verifies bearer with Auth and rejects an expired/invalid token", async () => {
    state.getUser.mockResolvedValue({ data: { user: null }, error: { message: "private detail" } });
    const response = await GET(request());
    expect(response.status).toBe(401); expect(state.getUser).toHaveBeenCalledWith(jwt);
    expect(state.lookup).not.toHaveBeenCalled(); expect(JSON.stringify(await response.json())).not.toContain("private detail");
  });
  it("rejects inactive, missing and mismatched database profiles", async () => {
    for (const data of [null, { ...profile, status: "INACTIVE" }, { ...profile, id: "forged" }]) {
      state.lookup.mockResolvedValue({ data, error: null });
      expect((await GET(request())).status).toBe(403);
    }
    expect(state.chains).not.toHaveBeenCalled();
  });
  it("forwards only the verified user JWT and never accepts a frontend admin role", async () => {
    const response = await GET(request({ authorization: `Bearer ${jwt}`, "X-User-Role": "ADMIN" }));
    expect(response.status).toBe(200);
    expect(state.createClient).toHaveBeenCalledWith("https://example.supabase.co", "sb_publishable_synthetic_test_key", expect.objectContaining({ global: { headers: { Authorization: `Bearer ${jwt}` } } }));
    expect(await response.json()).toEqual({ status: "PASS_AUTHENTICATED_READ", role: "VIEWER", chains: 1, scopes_with_history: 1, products: 2, categories: 1, observations: 6 });
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(JSON.stringify(vi.mocked(console.info).mock.calls)).not.toMatch(/synthetic-user-bearer|Controlled fixture/);
    expect(state.dispose).toHaveBeenCalledOnce();
  });
  it("hides upstream exceptions and releases session-owned reads", async () => {
    state.summary.mockRejectedValue(new Error(`private exception ${jwt}`));
    const response = await GET(request()); expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "read_failed" });
    expect(state.dispose).toHaveBeenCalledOnce();
    expect(JSON.stringify(vi.mocked(console.info).mock.calls)).not.toContain(jwt);
  });
  it("handles Auth network exceptions without a stack trace or token", async () => {
    state.getUser.mockRejectedValue(new Error(jwt));
    const response = await GET(request()); expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ error: "read_failed" });
    expect(state.lookup).not.toHaveBeenCalled();
  });
});
