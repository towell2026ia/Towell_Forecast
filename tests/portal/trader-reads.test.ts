import { describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { SupabaseForecastReadRepository } from "../../lib/forecast-data";
import { emptyFilters, type HistoricalRow } from "../../lib/supabase/types";
import { operationalContext } from "./operational-context-fixture";

const product = { id: "p", chain_id: "c", category_id: "cat", product_code: "code", variant_code: null, description: "Real", identifiers: [] };
function fake(data: Record<string, unknown>, errorTable = "") {
  const calls: { table: string; method: string; args: unknown[] }[] = [];
  const sdk = { from(table: string) { const q: Record<string, unknown> = {}; for (const method of ["select", "eq", "in", "gte", "lte", "order", "range", "limit", "abortSignal"]) q[method] = (...args: unknown[]) => { calls.push({ table, method, args }); return q; }; q.then = (resolve: (result: unknown) => unknown) => Promise.resolve({ data: table === "operational_read_context" ? [operationalContext] : data[table] ?? [], error: table === errorTable ? { code: "denied" } : null }).then(resolve); return q; }, rpc(table: string, ...args: unknown[]) { calls.push({ table, method: "rpc", args }); return this.from(table); } };
  const client = sdk as unknown as SupabaseClient;
  const repository = new SupabaseForecastReadRepository(client, "user");
  vi.spyOn(repository.previews, "commercialCatalog").mockResolvedValue(((data.products ?? []) as typeof product[]).map(p => ({
    chain_id: p.chain_id, product_id: p.id, model: "MODEL", color: null, size: null, product_type: null, source: "OFFICIAL_WORKBOOK", status: "PENDING_CLASSIFICATION", reasons: [],
  })));
  return { repository, calls };
}
const customer = { observation_id: "o", chain_id: "c", product_id: "p", period: "2026-08-01", metric_code: "FCST_CLIENTE_HISTORY", value: 0, value_text: "0.000", availability_source: "UNKNOWN" };
describe("Trader scoped GET-only reads", () => {
  it("historical pages aggregate only one scope, zero is real, missing remains null", async () => {
    const { repository } = fake({});
    const rows: HistoricalRow[] = [ { chain_id: "c", product_id: "p", period: "2026-07-01", product, chain: "c", SALES: 0, ORDER: null, DELIVERY: 8, availability: "UNKNOWN" }, { chain_id: "c", product_id: "q", period: "2026-07-01", product: { ...product, id: "q" }, chain: "c", SALES: 5, ORDER: null, DELIVERY: null, availability: "UNKNOWN" } ];
    const read = vi.spyOn(repository, "getHistoricalObservations").mockResolvedValueOnce({ rows: rows.slice(0, 1), next: { chain_id: "c", product_id: "p", period: "2026-07-01" }, observationCount: 2 }).mockResolvedValueOnce({ rows: rows.slice(1), next: null, observationCount: 2 });
    expect(await repository.getForecastHistory({ ...emptyFilters, chainId: "c" }, operationalContext)).toEqual([{ period: "2026-07", sale: 5, order: null, delivery: 8 }]); expect(read).toHaveBeenCalledTimes(2);
  });
  it("all-chain history never fetches or combines facts", async () => { const { repository } = fake({}); const read = vi.spyOn(repository, "getHistoricalObservations"); expect(await repository.getForecastHistory(emptyFilters)).toEqual([]); expect(read).not.toHaveBeenCalled(); });
  it("foreign historical scope fails closed", async () => { const { repository } = fake({}); vi.spyOn(repository, "getHistoricalObservations").mockResolvedValue({ rows: [{ chain_id: "foreign", product_id: "p", period: "2026-07-01", product, chain: "foreign", SALES: 1, ORDER: null, DELIVERY: null, availability: "UNKNOWN" }], next: null, observationCount: 1 }); await expect(repository.getForecastHistory({ ...emptyFilters, chainId: "c" }, operationalContext)).rejects.toThrow("chart_identity_mismatch"); });
  it("missing client history remains empty and never queries the previous forecast contract", async () => {
    const { repository, calls } = fake({ products: [product] });
    expect(await repository.getCustomerForecast({ ...emptyFilters, chainId: "c" }, "2026-07")).toEqual([]);
    expect(calls.some(c => c.table.startsWith("customer_forecast_"))).toBe(false);
  });
  it("reads client history from the current official source and preserves zero", async () => {
    const { repository, calls } = fake({ operational_read_observations: [customer], products: [product] });
    expect(await repository.getCustomerForecast({ ...emptyFilters, chainId: "c", productId: "p" }, "2026-07")).toEqual([{ period: "2026-08", value: 0 }]);
    expect(calls).toContainEqual({ table: "operational_read_observations", method: "rpc", args: [expect.objectContaining({ p_source_hash: operationalContext.source_hash, p_generation: 2 })] });
    expect(calls.some(c => c.table.startsWith("customer_forecast_"))).toBe(false);
  });
  it("rejects foreign scopes and duplicate official history rows", async () => {
    for (const rows of [[{ ...customer, chain_id: "foreign" }], [customer, customer]]) {
      const { repository } = fake({ operational_read_observations: rows, products: [product] });
      await expect(repository.getCustomerForecast({ ...emptyFilters, chainId: "c" }, "2026-07")).rejects.toThrow();
    }
  });
  it("fails an official source permission error without historical fallback", async () => {
    const { repository } = fake({ products: [product] }, "operational_read_observations");
    await expect(repository.getCustomerForecast({ ...emptyFilters, chainId: "c" }, "2026-07")).rejects.toThrow("report_read_failed");
  });
});
