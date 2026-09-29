import { describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { SupabaseForecastReadRepository } from "../../lib/forecast-data";
import { emptyFilters, type HistoricalRow } from "../../lib/supabase/types";

const product = { id: "p", chain_id: "c", category_id: "cat", product_code: "code", variant_code: null, description: "Real", identifiers: [] };
function fake(data: Record<string, unknown>, errorTable = "") {
  const calls: { table: string; method: string; args: unknown[] }[] = [];
  const client = { from(table: string) { const q: Record<string, unknown> = {}; for (const method of ["select", "eq", "in", "gte", "lte", "order", "range", "limit", "abortSignal"]) q[method] = (...args: unknown[]) => { calls.push({ table, method, args }); return q; }; q.then = (resolve: (result: unknown) => unknown) => Promise.resolve({ data: data[table] ?? [], error: table === errorTable ? { code: "denied" } : null }).then(resolve); return q; } } as unknown as SupabaseClient;
  return { repository: new SupabaseForecastReadRepository(client, "user"), calls };
}
const version = { id: "v", chain_id: "c", status: "VALIDATED", issue_period: "2026-07-01" };
const customer = { chain_id: "c", product_id: "p", forecast_version_id: "v", target_period: "2026-08-01", value: 0 };
describe("Trader scoped GET-only reads", () => {
  it("historical pages aggregate only one scope, zero is real, missing remains null", async () => {
    const { repository } = fake({});
    const rows: HistoricalRow[] = [ { chain_id: "c", product_id: "p", period: "2026-07-01", product, chain: "c", SALES: 0, ORDER: null, DELIVERY: 8, availability: "UNKNOWN" }, { chain_id: "c", product_id: "q", period: "2026-07-01", product: { ...product, id: "q" }, chain: "c", SALES: 5, ORDER: null, DELIVERY: null, availability: "UNKNOWN" } ];
    const read = vi.spyOn(repository, "getHistoricalObservations").mockResolvedValueOnce({ rows: rows.slice(0, 1), next: { chain_id: "c", product_id: "p", period: "2026-07-01" }, observationCount: 2 }).mockResolvedValueOnce({ rows: rows.slice(1), next: null, observationCount: 2 });
    expect(await repository.getForecastHistory({ ...emptyFilters, chainId: "c" })).toEqual([{ period: "2026-07", sale: 5, order: null, delivery: 8 }]); expect(read).toHaveBeenCalledTimes(2);
  });
  it("all-chain history never fetches or combines facts", async () => { const { repository } = fake({}); const read = vi.spyOn(repository, "getHistoricalObservations"); expect(await repository.getForecastHistory(emptyFilters)).toEqual([]); expect(read).not.toHaveBeenCalled(); });
  it("foreign historical scope fails closed", async () => { const { repository } = fake({}); vi.spyOn(repository, "getHistoricalObservations").mockResolvedValue({ rows: [{ chain_id: "foreign", product_id: "p", period: "2026-07-01", product, chain: "foreign", SALES: 1, ORDER: null, DELIVERY: null, availability: "UNKNOWN" }], next: null, observationCount: 1 }); await expect(repository.getForecastHistory({ ...emptyFilters, chainId: "c" })).rejects.toThrow("chart_identity_mismatch"); });
  it("customer forecast absent has no rows query and no fabricated zeros", async () => { const { repository, calls } = fake({}); expect(await repository.getCustomerForecast({ ...emptyFilters, chainId: "c" }, "2026-07")).toEqual([]); expect(calls.some(c => c.table === "customer_forecast_rows")).toBe(false); });
  it("customer reads visible validated/frozen version at cutoff and retains literal zero", async () => { const { repository, calls } = fake({ customer_forecast_versions: [version], customer_forecast_rows: [customer], products: [product] }); expect(await repository.getCustomerForecast({ ...emptyFilters, chainId: "c", productId: "p" }, "2026-07")).toEqual([{ period: "2026-08", value: 0 }]); expect(calls).toContainEqual({ table: "customer_forecast_versions", method: "in", args: ["status", ["VALIDATED", "FROZEN"]] }); expect(calls).toContainEqual({ table: "customer_forecast_versions", method: "lte", args: ["issue_period", "2026-07-01"] }); expect(calls).toContainEqual({ table: "customer_forecast_rows", method: "eq", args: ["chain_id", "c"] }); expect(calls).toContainEqual({ table: "customer_forecast_rows", method: "eq", args: ["forecast_version_id", "v"] }); expect(calls.every(c => !["insert", "update", "upsert", "rpc"].includes(c.method))).toBe(true); });
  it.each([{ ...version, status: "DRAFT" }, { ...version, chain_id: "foreign" }, { ...version, issue_period: "2026-08-01" }])("invalid customer version never displayed", async invalid => { const { repository } = fake({ customer_forecast_versions: [invalid] }); await expect(repository.getCustomerForecast({ ...emptyFilters, chainId: "c" }, "2026-07")).rejects.toThrow("customer_version_invalid"); });
  it("foreign chain row and duplicate forecast rows rejected", async () => { for (const rows of [[{ ...customer, chain_id: "foreign" }], [customer, customer]]) { const { repository } = fake({ customer_forecast_versions: [version], customer_forecast_rows: rows, products: [product] }); await expect(repository.getCustomerForecast({ ...emptyFilters, chainId: "c" }, "2026-07")).rejects.toThrow(); } });
  it("RLS denied is a read error, not empty/demo customer forecast", async () => { const { repository } = fake({}, "customer_forecast_versions"); await expect(repository.getCustomerForecast({ ...emptyFilters, chainId: "c" }, "2026-07")).rejects.toThrow("read_failed"); });
});
