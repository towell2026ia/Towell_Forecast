import { describe, it, expect, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { SupabaseForecastReadRepository, assertActiveProfile, combineObservations, resetForChain } from "../../lib/forecast-data";
import { emptyFilters, type Product } from "../../lib/supabase/types";
import { validatePublicConfig, logPortalEvent } from "../../lib/supabase/client";
import { disabledPortalOperation } from "../../lib/portal-operation-gate";
import { readFileSync } from "node:fs";
import { operationalContext } from "./operational-context-fixture";

type Call = { table: string; method: string; args: unknown[] };
function fakeClient(resolve: (table: string, calls: Call[]) => { data: unknown; count?: number; error?: unknown }, context = () => operationalContext as typeof operationalContext | null) {
  const calls: Call[] = [];
  const client = { from(table: string) {
    const methods = ["select", "eq", "in", "gte", "lte", "or", "order", "range", "limit", "abortSignal", "maybeSingle"];
    const query: Record<string, unknown> = {};
    for (const method of methods) query[method] = (...args: unknown[]) => { calls.push({ table, method, args }); return query; };
    query.then = (accept: (value: unknown) => unknown, reject: (error: unknown) => unknown) => Promise.resolve(table === "operational_read_context" ? { data: [context()] } : resolve(table, calls)).then(accept, reject);
    return query;
  }, rpc(table: string, ...args: unknown[]) { calls.push({ table, method: "rpc", args }); return this.from(table); }, auth: { getUser: async () => ({ data: { user: { id: "user" } }, error: null }) } };
  return { client: client as unknown as SupabaseClient, calls };
}
const product: Product = { id: "p", chain_id: "c", category_id: "cat", description: "Real", product_code: "ITEM1", variant_code: null, identifiers: [] };
const observed = (metric: "SALES" | "ORDER" | "DELIVERY", value: number, period = "2025-01-01") => ({ chain_id: "c", product_id: "p", period, metric_code: metric, value, value_text: String(value), availability_source: "UNKNOWN", product_code: product.product_code, variant_code: product.variant_code, category_id: product.category_id, product_description: product.description, chain_name: "Cadena" });
describe("Bounded repository and security contracts", () => {
  it("SEC02 inactive, missing, forged identity and invalid roles rejected", () => {
    for (const profile of [null, { id: "user", full_name: "u", global_role: "ADMIN" as const, status: "INACTIVE" as const }, { id: "other", full_name: "u", global_role: "ADMIN" as const, status: "ACTIVE" as const }]) expect(() => assertActiveProfile(profile, "user")).toThrow();
  });
  it("SEC06 only publishable or legacy anon key accepted", () => {
    const url = "https://example.supabase.co";
    expect(validatePublicConfig({ url, key: "sb_publishable_synthetic_test_key" })).toBe(true);
    expect(validatePublicConfig({ url, key: "sb_secret_rejected_example" })).toBe(false);
    const token = (role: string) => `x.${btoa(JSON.stringify({ role }))}.x`;
    expect(validatePublicConfig({ url, key: token("service_role") })).toBe(false);
    expect(validatePublicConfig({ url, key: token("anon") })).toBe(true);
    expect(validatePublicConfig({ url: "http://example.supabase.co", key: token("anon") })).toBe(false);
  });
  it("SEC06 browser entry graph does not import privileged or fixture providers", () => {
    for (const path of ["app/page.tsx", "app/login/page.tsx", "app/portal-auth.tsx", "app/forecast-towell-app.tsx", "lib/forecast-data.ts", "lib/supabase/client.ts"]) {
      const text = readFileSync(path, "utf8");
      expect(text).not.toMatch(/SUPABASE_SERVICE_ROLE_KEY|ASSISTANT_API_TOKEN|getChatGPTUser|forecast-demo\.json|fendi-dashboard\.json|const products = \[/);
    }
  });
  it("SEC07 technical logging contains fixed events only", () => {
    const log = vi.spyOn(console, "info").mockImplementation(() => {});
    logPortalEvent("login_success"); logPortalEvent("read_error");
    expect(log.mock.calls).toEqual([["[TowellPortal] login_success"], ["[TowellPortal] read_error"]]);
  });
  it("all portal write/engine routes fail closed before identity or service calls", async () => {
    const response = disabledPortalOperation(); expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ error: "operation_disabled" });
    for (const dir of ["records", "forecast-runs", "ml-runs", "ensemble-runs", "forecast-decisions", "period-closures", "assistant/message"]) {
      const code = readFileSync(`app/api/${dir}/route.ts`, "utf8"); expect(code).toContain("disabledPortalOperation"); expect(code).not.toMatch(/fetch\(|SERVICE_ROLE|oai-authenticated|createHmac/);
    }
  });
  it("combines metrics without summing parent and child, preserves zero/missing", () => {
    const rows = combineObservations([observed("SALES", 0), observed("ORDER", 8), { ...observed("SALES", 5), chain_id: "child", product_id: "child-product" }], new Map(), 50);
    expect(rows).toHaveLength(2); expect(rows[0]).toMatchObject({ SALES: 0, ORDER: 8, DELIVERY: null });
    expect(rows[1].SALES).toBe(5);
  });
  it("rejects duplicate current metric and nonfinite quantities", () => {
    expect(() => combineObservations([observed("SALES", 2), observed("SALES", 3)], new Map(), 50)).toThrow();
    expect(() => combineObservations([observed("SALES", NaN)], new Map(), 50)).toThrow();
  });
  it("chain change clears dependent filters", () => {
    expect(resetForChain({ ...emptyFilters, productId: "old", categoryId: "old", search: "old", periodRange: ["2025-01", "2025-02"] }, "new")).toEqual({ ...emptyFilters, chainId: "new" });
  });
  it("PH25/PH26/PH27/PH28 upstream chain/product/category/period filtering and flat metadata", async () => {
    const mock = fakeClient(table => ({ data: table === "operational_read_observations" ? [observed("SALES", 2)] : table === "products" ? [product] : [], count: 1 }));
    const repo = new SupabaseForecastReadRepository(mock.client, "user");
    await repo.getHistoricalObservations({ ...emptyFilters, chainId: "c", categoryId: "cat", productId: "p", periodRange: ["2025-01", "2025-03"] }, { size: 50 });
    expect(mock.calls).toContainEqual({ table: "operational_read_observations", method: "limit", args: [153] });
    expect(mock.calls).toContainEqual({ table: "operational_read_observations", method: "eq", args: ["chain_id", "c"] });
    expect(mock.calls).toContainEqual({ table: "operational_read_observations", method: "eq", args: ["product_id", "p"] });
    expect(mock.calls).toContainEqual({ table: "operational_read_observations", method: "eq", args: ["category_id", "cat"] });
    expect(mock.calls).toContainEqual({ table: "operational_read_observations", method: "gte", args: ["period", "2025-01-01"] });
    expect(mock.calls).toContainEqual({ table: "operational_read_observations", method: "lte", args: ["period", "2025-03-01"] });
    expect(mock.calls.some(c => c.method === "select" && String(c.args[0]).includes("!inner"))).toBe(false);
  });
  it("missing metrics paginate by complete tuple, not 50 raw observations", async () => {
    const raw = Array.from({ length: 51 }, (_, i) => ({ ...observed("SALES", i), product_id: `p-${i}` }));
    const mock = fakeClient(table => ({ data: table === "operational_read_observations" ? raw : table === "products" ? [product] : [], count: 51 }));
    const result = await new SupabaseForecastReadRepository(mock.client, "user").getHistoricalObservations(emptyFilters, { size: 50 });
    expect(result.rows).toHaveLength(50); expect(result.next?.product_id).toBe("p-49"); expect(result.rows.every(r => r.ORDER === null)).toBe(true);
  });
  it("retains A for continuation after B is activated and refuses a mismatched context", async () => {
    let active = operationalContext;
    const productId = (i: number) => `00000000-0000-0000-0000-${String(i).padStart(12, "0")}`;
    const raw = Array.from({ length: 51 }, (_, i) => ({ ...observed("SALES", i), chain_id: productId(111), product_id: productId(i) }));
    const mock = fakeClient(table => ({ data: table === "operational_read_observations" ? raw : [] }), () => active);
    const repo = new SupabaseForecastReadRepository(mock.client, "user");
    const page = await repo.getHistoricalObservations(emptyFilters, { size: 50 });
    expect(page.next?.context).toEqual(operationalContext);
    active = { ...active, version_id: productId(999), generation: 3 };
    await repo.getHistoricalObservations(emptyFilters, { size: 50, cursor: page.next });
    expect(mock.calls.filter(c => c.table === "operational_read_context" && c.method === "rpc")).toHaveLength(1);
    expect(mock.calls.filter(c => c.table === "operational_read_observations" && c.method === "rpc").map(c => c.args[0])).toEqual(Array(2).fill(expect.objectContaining({ p_version_id: operationalContext.version_id, p_manifest_hash: operationalContext.manifest_hash, p_generation: 2, p_mode: "OPERATIONAL" })));
    await expect(repo.getHistoricalObservations(emptyFilters, { size: 50, cursor: page.next }, active)).rejects.toThrow("invalid_cursor");
    await repo.getHistoricalObservations(emptyFilters, { size: 50 });
    expect(mock.calls.filter(c => c.table === "operational_read_observations" && c.method === "rpc").at(-1)?.args[0]).toMatchObject({ p_version_id: active.version_id, p_generation: 3 });
  });
  it("fails closed if the active version contract is unavailable", async () => {
    const mock = fakeClient(() => ({ data: [] }), () => null);
    const repo = new SupabaseForecastReadRepository(mock.client, "user");
    await expect(repo.getHistoricalObservations(emptyFilters, { size: 50 })).rejects.toThrow("operational_version_unavailable");
    expect(mock.calls.every(call => call.table === "operational_read_context")).toBe(true);
  });
  it("PH29 UPC mapping search is catalog-scoped and filters facts upstream", async () => {
    const mock = fakeClient(table => ({ data: table === "import_profile_versions" ? [{ chain_id: "c", mapping_json: { products: [{ id: "p", identifier_mappings: [["ITEM1", "UPC-SEARCH"]] }] } }] : table === "products" ? [product] : [], count: 0 }));
    const repo = new SupabaseForecastReadRepository(mock.client, "user");
    expect((await repo.getProducts("c", null, "UPC-SEARCH"))[0].id).toBe("p");
    await repo.getHistoricalObservations({ ...emptyFilters, search: "UPC-SEARCH" }, { size: 50 });
    expect(mock.calls).toContainEqual({ table: "operational_read_observations", method: "in", args: ["product_id", ["p"]] });
  });
  it("PH24 bounds 50/100/250 tuple reads and rejects other sizes", async () => {
    for (const size of [50, 100, 250] as const) {
      const mock = fakeClient(() => ({ data: [], count: 0 }));
      await new SupabaseForecastReadRepository(mock.client, "user").getHistoricalObservations(emptyFilters, { size });
      expect(mock.calls).toContainEqual({ table: "operational_read_observations", method: "limit", args: [3 * size + 3] });
    }
    const mock = fakeClient(() => ({ data: [], count: 0 }));
    await expect(new SupabaseForecastReadRepository(mock.client, "user").getHistoricalObservations(emptyFilters, { size: 1000 as 50 })).rejects.toThrow("invalid_page_size");
  });
  it("PH07/PH23 periods, has_history and summary all use published history, never temporal current", async () => {
    const mock = fakeClient(table => ({ data: table === "chains" ? [{ id: "c", code: "C", name: "Cadena", status: "ACTIVE" }] : table === "products" ? [product] : table === "operational_read_observations" ? [observed("SALES", 2)] : table === "operational_read_periods" ? [{ period: "2025-01-01" }] : [], count: 1 }));
    const repo = new SupabaseForecastReadRepository(mock.client, "user");
    expect((await repo.getVisibleChains())[0].has_history).toBe(true);
    expect(await repo.getPeriods()).toEqual(["2025-01"]);
    expect(await repo.getHistoricalSummary(emptyFilters)).toMatchObject({ scopeCount: 1, productCount: 1, observationCount: 1, latestPeriod: "2025-01" });
    expect(mock.calls.some(c => c.table === "monthly_observations_current")).toBe(false);
  });
  it("dispose invalidates user-owned cache and refuses further reads", async () => {
    const mock = fakeClient(() => ({ data: [] })); const repo = new SupabaseForecastReadRepository(mock.client, "user"); repo.dispose();
    await expect(repo.getCategories()).rejects.toThrow("session_disposed");
  });
  it("read errors never become cached demo data", async () => {
    const mock = fakeClient(() => ({ data: null, error: { message: "sensitive error" } }));
    await expect(new SupabaseForecastReadRepository(mock.client, "user").getCategories()).rejects.toThrow("read_failed");
  });
  it("unknown hierarchy is not inferred from a chain's name", async () => {
    const mock = fakeClient(table => ({ data: table === "chains" ? [{ id: "c", code: "C", name: "Parent::Child", status: "ACTIVE" }] : [], count: 0 }));
    const chains = await new SupabaseForecastReadRepository(mock.client, "user").getVisibleChains();
    expect(chains[0].parentId).toBeNull(); expect(chains[0].has_history).toBe(false);
  });
});
