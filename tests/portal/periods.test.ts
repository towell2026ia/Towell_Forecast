import { describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { SupabaseForecastReadRepository } from "../../lib/forecast-data";
import { PortalReadError } from "../../lib/portal-read-diagnostic";
import { operationalContext } from "./operational-context-fixture";

function client(rows: { chain_id: string; period: string }[], error: unknown = null) {
  const requests: { table: string; selection?: unknown[]; parameters: unknown; orders: unknown[][] }[] = [];
  const sdk = { rpc: vi.fn((table: string, parameters?: { p_chain_id?: string | null }) => {
    const request = { table, selection: undefined as unknown[] | undefined, parameters, orders: [] as unknown[][] };
    const query = {
      select: (...args: unknown[]) => { request.selection = args; return query; },
      order: (...args: unknown[]) => { request.orders.push(args); return query; },
      abortSignal: () => query,
      then: (accept: (value: unknown) => unknown, reject: (reason: unknown) => unknown) => {
        if (table === "operational_read_context") return Promise.resolve({ data: [operationalContext] }).then(accept, reject);
        requests.push(request);
        const selected = rows.filter(r => !parameters?.p_chain_id || r.chain_id === parameters.p_chain_id);
        return Promise.resolve({ data: error ? null : selected, error, status: error ? 403 : 200 }).then(accept, reject);
      },
    };
    return query;
  }) };
  return { sdk: sdk as unknown as SupabaseClient, requests };
}
const rows = [{ chain_id: "a", period: "2026-03-01" }, { chain_id: "b", period: "2026-01-01" }, { chain_id: "a", period: "2026-01-01" }];
describe("PF compact published period read", () => {
  it("PF01/PF02/PF03 uses a compact period query after the pin, no HEAD/count/bounds", async () => {
    const c = client(rows); await new SupabaseForecastReadRepository(c.sdk, "fixture").getPeriods(null);
    expect(c.requests).toHaveLength(1);
    expect(c.requests[0]).toEqual({ table: "operational_read_periods", selection: ["period"], parameters: { p_source_hash: operationalContext.source_hash, p_generation: 2, p_chain_id: null }, orders: [["period", { ascending: true }]] });
  });
  it("PF04/PF06/PF07/PF08 unions visible scopes, deduplicates, sorts and never fills February", async () => {
    const c = client(rows);
    expect(await new SupabaseForecastReadRepository(c.sdk, "fixture").getPeriods()).toEqual(["2026-01", "2026-03"]);
  });
  it("PF05 selects only the requested chain with one upstream equality", async () => {
    const c = client(rows);
    expect(await new SupabaseForecastReadRepository(c.sdk, "fixture").getPeriods("b")).toEqual(["2026-01"]);
    expect(c.requests).toHaveLength(1); expect(c.requests[0].parameters).toMatchObject({ p_chain_id: "b" });
  });
  it("no visible facts returns an empty catalog, not inferred dates", async () => {
    expect(await new SupabaseForecastReadRepository(client([]).sdk, "fixture").getPeriods()).toEqual([]);
  });
  it("fails a rejected current-source period read without a second route", async () => {
    const requests: (string | null)[] = [];
    const sdk = { rpc: vi.fn((name: string, parameters?: { p_chain_id: string | null }) => {
      const chain = parameters?.p_chain_id ?? null;
      const query = {
        select: () => query,
        order: () => query, abortSignal: () => query,
        then: (accept: (value: unknown) => unknown) => {
          if (name === "operational_read_context") return Promise.resolve({ data: [operationalContext] }).then(accept);
          requests.push(chain);
          return Promise.resolve(chain ? { data: rows.filter(row => row.chain_id === chain), error: null, status: 200 }
            : { data: null, error: { code: "57014" }, status: 504 }).then(accept);
        },
      };
      return query;
    }) } as unknown as SupabaseClient;
    const repo = new SupabaseForecastReadRepository(sdk, "fixture");
    repo.getVisibleChains = vi.fn(async () => [
      { id: "a", code: "A", name: "A", status: "ACTIVE", has_history: true, parentId: null, scopeType: null },
      { id: "b", code: "B", name: "B", status: "ACTIVE", has_history: true, parentId: null, scopeType: null },
    ]);
    await expect(repo.getPeriods()).rejects.toBeInstanceOf(PortalReadError);
    expect(requests).toEqual([null]);
    expect(repo.getVisibleChains).not.toHaveBeenCalled();
    expect(repo.getReadDiagnostic("getPeriods")?.code).toBe("57014");
  });
  it("a missing view/permission fails with only the new safe diagnostic", async () => {
    const log = vi.spyOn(console, "info").mockImplementation(() => {}), report = vi.fn();
    const repo = new SupabaseForecastReadRepository(client([], { code: "42501", message: "private upstream bearer" }).sdk, "fixture", report);
    await expect(repo.getPeriods()).rejects.toBeInstanceOf(PortalReadError);
    expect(report).toHaveBeenCalledWith({ endpoint: "getPeriods", table: "operational_read_periods", operation: "period_catalog", http_status: 403, code: "42501" });
    expect(JSON.stringify(log.mock.calls)).not.toContain("private upstream bearer");
  });
  it("invalid upstream dates fail closed and do not become invented months", async () => {
    const repo = new SupabaseForecastReadRepository(client([{ chain_id: "a", period: "2026-13-01" }]).sdk, "fixture");
    await expect(repo.getPeriods()).rejects.toBeInstanceOf(PortalReadError);
    expect(repo.getReadDiagnostic("getPeriods")?.code).toBe("INVALID_PERIOD");
  });
});
