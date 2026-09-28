import { describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { SupabaseForecastReadRepository } from "../../lib/forecast-data";
import { PortalReadError } from "../../lib/portal-read-diagnostic";

function client(rows: { chain_id: string; period: string }[], error: unknown = null) {
  const requests: { table: string; selection?: unknown[]; filters: unknown[][]; orders: unknown[][] }[] = [];
  const sdk = { from: vi.fn((table: string) => {
    const request = { table, selection: undefined as unknown[] | undefined, filters: [] as unknown[][], orders: [] as unknown[][] };
    const query = {
      select: (...args: unknown[]) => { request.selection = args; return query; },
      eq: (...args: unknown[]) => { request.filters.push(args); return query; },
      order: (...args: unknown[]) => { request.orders.push(args); return query; },
      abortSignal: () => query,
      then: (accept: (value: unknown) => unknown, reject: (reason: unknown) => unknown) => {
        requests.push(request);
        const selected = rows.filter(r => request.filters.every(([field, value]) => r[field as keyof typeof r] === value));
        return Promise.resolve({ data: error ? null : selected, error, status: error ? 403 : 200 }).then(accept, reject);
      },
    };
    return query;
  }) };
  return { sdk: sdk as unknown as SupabaseClient, requests };
}
const rows = [{ chain_id: "a", period: "2026-03-01" }, { chain_id: "b", period: "2026-01-01" }, { chain_id: "a", period: "2026-01-01" }];
describe("PF compact published period read", () => {
  it("PF01/PF02/PF03 uses portal_history_periods with exactly one GET, no HEAD/count/bounds", async () => {
    const c = client(rows); await new SupabaseForecastReadRepository(c.sdk, "fixture").getPeriods(null);
    expect(c.requests).toHaveLength(1);
    expect(c.requests[0]).toEqual({ table: "portal_history_periods", selection: ["period"], filters: [], orders: [["period", { ascending: true }]] });
  });
  it("PF04/PF06/PF07/PF08 unions visible scopes, deduplicates, sorts and never fills February", async () => {
    const c = client(rows);
    expect(await new SupabaseForecastReadRepository(c.sdk, "fixture").getPeriods()).toEqual(["2026-01", "2026-03"]);
  });
  it("PF05 selects only the requested chain with one upstream equality", async () => {
    const c = client(rows);
    expect(await new SupabaseForecastReadRepository(c.sdk, "fixture").getPeriods("b")).toEqual(["2026-01"]);
    expect(c.requests).toHaveLength(1); expect(c.requests[0].filters).toEqual([["chain_id", "b"]]);
  });
  it("no visible facts returns an empty catalog, not inferred dates", async () => {
    expect(await new SupabaseForecastReadRepository(client([]).sdk, "fixture").getPeriods()).toEqual([]);
  });
  it("a missing view/permission fails with only the new safe diagnostic", async () => {
    const log = vi.spyOn(console, "info").mockImplementation(() => {}), report = vi.fn();
    const repo = new SupabaseForecastReadRepository(client([], { code: "42501", message: "private upstream bearer" }).sdk, "fixture", report);
    await expect(repo.getPeriods()).rejects.toBeInstanceOf(PortalReadError);
    expect(report).toHaveBeenCalledWith({ endpoint: "getPeriods", table: "portal_history_periods", operation: "period_catalog", http_status: 403, code: "42501" });
    expect(JSON.stringify(log.mock.calls)).not.toContain("private upstream bearer");
  });
  it("invalid upstream dates fail closed and do not become invented months", async () => {
    const repo = new SupabaseForecastReadRepository(client([{ chain_id: "a", period: "2026-13-01" }]).sdk, "fixture");
    await expect(repo.getPeriods()).rejects.toBeInstanceOf(PortalReadError);
    expect(repo.getReadDiagnostic("getPeriods")?.code).toBe("INVALID_PERIOD");
  });
});
