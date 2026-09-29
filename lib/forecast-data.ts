import type { SupabaseClient } from "@supabase/supabase-js";
import type { Category, Chain, Cursor, Filters, ForecastReadRepository, HistoricalPage, HistoricalRow, Product, Profile, Summary } from "./supabase/types";
import { controlledReadCode, logReadDiagnostic, PortalReadError, type PortalReadDiagnostic } from "./portal-read-diagnostic";
import { RailwayPreviewClient } from "./forecast-preview";
import type { HistoricalMonth, CustomerMonth } from "./forecast-chart-data";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type ProductMapping = { id: string; identifier_mappings?: [string | null, string | null][] };
type Mapping = { scope?: { parent_uuid?: string; scope_type?: string }; products?: ProductMapping[] };
const publishedHistoryView = "portal_monthly_observations_current";
const publishedPeriodsView = "portal_history_periods";
type Observation = Cursor & { metric_code: "SALES" | "ORDER" | "DELIVERY"; value: number; availability_source: string; product_code: string; variant_code: string | null; product_description: string; category_id: string | null; chain_name: string };

export function assertActiveProfile(profile: Profile | null, userId: string): Profile {
  if (!profile || profile.id !== userId || profile.status !== "ACTIVE" || !["ADMIN", "EDITOR", "VIEWER"].includes(profile.global_role)) throw new Error("profile_access_denied");
  return profile;
}
export function resetForChain(filters: Filters, chainId: string | null): Filters {
  return { ...filters, chainId, productId: null, categoryId: null, periodRange: [null, null], search: "" };
}
export function combineObservations(observations: Observation[], products: Map<string, Product>, size: number): HistoricalRow[] {
  const groups = new Map<string, HistoricalRow>();
  for (const o of observations) {
    const key = `${o.chain_id}/${o.product_id}/${o.period}`;
    let row = groups.get(key);
    if (!row) {
      row = { chain_id: o.chain_id, product_id: o.product_id, period: o.period, product: products.get(o.product_id) ?? { id: o.product_id, chain_id: o.chain_id, category_id: o.category_id, product_code: o.product_code, variant_code: o.variant_code, description: o.product_description, identifiers: [] }, chain: o.chain_name, SALES: null, ORDER: null, DELIVERY: null, availability: o.availability_source };
      groups.set(key, row);
    }
    // Current view guarantees one version per scope/product/period/metric.
    if (row[o.metric_code] !== null) throw new Error("duplicate_current_metric");
    const value = Number(o.value);
    if (!Number.isFinite(value) || value < 0) throw new Error("invalid_observation_value");
    row[o.metric_code] = value;
  }
  return [...groups.values()].slice(0, size);
}

// Instance-owned cache; discarded on every identity/session change and logout.
// There is no service credential or server-global RLS result cache.
export class SupabaseForecastReadRepository implements ForecastReadRepository {
  private controller = new AbortController();
  private catalog: Product[] | null = null;
  private chains: Chain[] | null = null;
  private metadata: Map<string, Mapping> | null = null;
  private chainTask: Promise<Chain[]> | null = null;
  private mappingTask: Promise<Map<string, Mapping>> | null = null;
  private catalogTask: Promise<Product[]> | null = null;
  private readDiagnostics = new Map<PortalReadDiagnostic["endpoint"], PortalReadDiagnostic>();
  readonly previews: RailwayPreviewClient;
  constructor(private client: SupabaseClient, private userId: string, private onReadFailure?: (diagnostic: PortalReadDiagnostic) => void) { this.previews = new RailwayPreviewClient(client); }
  dispose() { this.controller.abort(); this.previews.dispose(); this.catalog = null; this.chains = null; this.metadata = null; this.chainTask = null; this.mappingTask = null; this.catalogTask = null; this.readDiagnostics.clear(); }
  getReadDiagnostic(endpoint: PortalReadDiagnostic["endpoint"]) { return this.readDiagnostics.get(endpoint) ?? null; }
  private ensureOpen() { if (this.controller.signal.aborted) throw new Error("session_disposed"); }
  private fail(endpoint: PortalReadDiagnostic["endpoint"], table: PortalReadDiagnostic["table"], operation: PortalReadDiagnostic["operation"], code: unknown, status = 0): never {
    const diagnostic = { endpoint, table, operation, code: controlledReadCode(code), http_status: Number.isInteger(status) && status >= 0 && status <= 599 ? status : 0 };
    logReadDiagnostic(diagnostic);
    this.readDiagnostics.set(endpoint, diagnostic);
    try { this.onReadFailure?.(diagnostic); } catch { /* Telemetry never changes the read result. */ }
    throw new PortalReadError(diagnostic);
  }
  private check(result: { error: unknown; status?: number }, endpoint: PortalReadDiagnostic["endpoint"], table: PortalReadDiagnostic["table"], operation: PortalReadDiagnostic["operation"]) {
    this.ensureOpen();
    if (result.error) this.fail(endpoint, table, operation, (result.error as { code?: unknown }).code, result.status);
    this.readDiagnostics.set(endpoint, { endpoint, table, operation, http_status: result.status ?? 0, code: "OK" });
  }
  private async rows<T>(query: PromiseLike<{ data: unknown; error: unknown; status?: number }>, endpoint?: PortalReadDiagnostic["endpoint"], table?: PortalReadDiagnostic["table"], operation: PortalReadDiagnostic["operation"] = "select"): Promise<T> {
    this.ensureOpen(); const result = await query; this.ensureOpen();
    if (endpoint && table) this.check(result, endpoint, table, operation);
    else if (result.error) throw new Error("read_failed");
    return result.data as T;
  }
  async getCurrentProfile() {
    const { data, error } = await this.client.auth.getUser();
    if (error || data.user?.id !== this.userId) throw new Error("session_invalid");
    const profile = await this.rows<Profile | null>(this.client.from("profiles").select("id,full_name,global_role,status").eq("id", this.userId).abortSignal(this.controller.signal).maybeSingle());
    return assertActiveProfile(profile, this.userId);
  }
  private getMappings(): Promise<Map<string, Mapping>> {
    if (this.metadata) return Promise.resolve(this.metadata);
    if (!this.mappingTask) this.mappingTask = this.loadMappings().catch(error => { this.mappingTask = null; throw error; });
    return this.mappingTask;
  }
  private async loadMappings() {
    if (this.metadata) return this.metadata;
    const versions = await this.rows<{ chain_id: string; mapping_json: Mapping }[]>(this.client.from("import_profile_versions").select("chain_id,mapping_json").order("version", { ascending: false }).limit(1000).abortSignal(this.controller.signal), "getProducts", "import_profile_versions");
    const mapping = new Map<string, Mapping>();
    for (const version of versions) if (!mapping.has(version.chain_id)) mapping.set(version.chain_id, version.mapping_json);
    this.metadata = mapping; return mapping;
  }
  getVisibleChains(): Promise<Chain[]> {
    if (this.chains) return Promise.resolve(this.chains);
    if (!this.chainTask) this.chainTask = this.loadChains().catch(error => { this.chainTask = null; throw error; });
    return this.chainTask;
  }
  private async loadChains() {
    if (this.chains) return this.chains;
    const masters = await this.rows<Omit<Chain, "has_history" | "parentId" | "scopeType">[]>(this.client.from("chains").select("id,code,name,status").order("name").limit(1000).abortSignal(this.controller.signal), "getVisibleChains", "chains");
    const mappings = await this.getMappings();
    const result: Chain[] = [];
    // HEAD counts, not raw facts; no per-product query or invented has_history column.
    for (let offset = 0; offset < masters.length; offset += 6) {
      result.push(...await Promise.all(masters.slice(offset, offset + 6).map(async c => {
        const q = await this.client.from(publishedHistoryView).select("observation_id", { count: "exact", head: true }).eq("chain_id", c.id).abortSignal(this.controller.signal);
        this.check(q, "getVisibleChains", publishedHistoryView, "head_count");
        const meta = mappings.get(c.id)?.scope;
        return { ...c, has_history: (q.count ?? 0) > 0, parentId: meta?.parent_uuid ?? null, scopeType: meta?.scope_type ?? null };
      })));
    }
    this.chains = result; return result;
  }
  async getCategories(chainId?: string | null) {
    let query = this.client.from("categories").select("id,chain_id,name").order("name").limit(1000);
    if (chainId) query = query.eq("chain_id", chainId);
    return this.rows<Category[]>(query.abortSignal(this.controller.signal), "getCategories", "categories");
  }
  private async loadCatalog(): Promise<Product[]> {
      const mapping = await this.getMappings();
      const identifiers = new Map<string, ProductMapping>();
      for (const m of mapping.values()) for (const p of m.products ?? []) identifiers.set(p.id, p);
      const catalog: Product[] = [];
      // Small RLS catalog only; facts are never preloaded into browser memory.
      for (let offset = 0; ; offset += 250) {
        const page = await this.rows<Omit<Product, "identifiers">[]>(this.client.from("products").select("id,chain_id,category_id,product_code,variant_code,description").order("id").range(offset, offset + 249).abortSignal(this.controller.signal), "getProducts", "products", "catalog_page");
        catalog.push(...page.map(p => ({ ...p, identifiers: identifiers.get(p.id)?.identifier_mappings ?? [] })));
        if (page.length < 250) break;
      }
      this.catalog = catalog;
      return catalog;
  }
  async getProducts(chainId?: string | null, categoryId?: string | null, search = "") {
    this.ensureOpen();
    if (!this.catalogTask) this.catalogTask = this.loadCatalog().catch(error => { this.catalogTask = null; throw error; });
    const catalog = this.catalog ?? await this.catalogTask;
    const term = search.toLocaleLowerCase().trim();
    try { return catalog.filter(p => (!chainId || p.chain_id === chainId) && (!categoryId || p.category_id === categoryId) && (!term || [p.description, p.product_code, p.variant_code, ...p.identifiers.flat()].some(v => v?.toLocaleLowerCase().includes(term)))); }
    catch { this.fail("getProducts", "products", "catalog_filter", "TRANSFORM_FAILED", 200); }
  }
  private async filtered(filters: Filters, head = false) {
    const selected = await this.getProducts(filters.chainId, filters.categoryId, filters.search);
    let q = this.client.from(publishedHistoryView).select("chain_id,chain_name,product_id,product_code,variant_code,product_description,category_id,period,metric_code,value,availability_source", { count: "exact", head });
    if (filters.chainId) q = q.eq("chain_id", filters.chainId);
    if (filters.productId) q = q.eq("product_id", filters.productId);
    if (filters.categoryId) q = q.eq("category_id", filters.categoryId);
    if (filters.search) q = selected.length ? q.in("product_id", selected.map(p => p.id)) : q.eq("product_id", "00000000-0000-0000-0000-000000000000");
    if (filters.periodRange[0]) q = q.gte("period", filters.periodRange[0] + "-01");
    if (filters.periodRange[1]) q = q.lte("period", filters.periodRange[1] + "-01");
    return { query: q.abortSignal(this.controller.signal), products: selected };
  }
  async getHistoricalObservations(filters: Filters, pagination: { size: 50 | 100 | 250; cursor?: Cursor | null }): Promise<HistoricalPage> {
    if (![50, 100, 250].includes(pagination.size)) throw new Error("invalid_page_size");
    const { query, products } = await this.filtered(filters);
    let q = query;
    const c = pagination.cursor;
    if (c) {
      if (!uuid.test(c.chain_id) || !uuid.test(c.product_id) || !/^\d{4}-\d{2}-01$/.test(c.period)) throw new Error("invalid_cursor");
      q = q.or(`chain_id.gt.${c.chain_id},and(chain_id.eq.${c.chain_id},product_id.gt.${c.product_id}),and(chain_id.eq.${c.chain_id},product_id.eq.${c.product_id},period.gt.${c.period})`);
    }
    const result = await q.order("chain_id").order("product_id").order("period").order("metric_code").limit(pagination.size * 3 + 3);
    this.check(result, "getHistoricalObservations", publishedHistoryView, c ? "cursor_page" : "page_1");
    const raw = result.data as unknown as Observation[];
    let grouped: HistoricalRow[];
    try { grouped = combineObservations(raw, new Map(products.map(p => [p.id, p])), pagination.size + 1); }
    catch (error) {
      const code = error instanceof Error && error.message === "duplicate_current_metric" ? "DUPLICATE_CURRENT_METRIC" : error instanceof Error && error.message === "invalid_observation_value" ? "INVALID_OBSERVATION_VALUE" : "TRANSFORM_FAILED";
      this.fail("getHistoricalObservations", publishedHistoryView, "combine_metrics", code, result.status ?? 200);
    }
    const rows = grouped.slice(0, pagination.size);
    const last = rows.at(-1);
    return { rows, next: grouped.length > pagination.size && last ? { chain_id: last.chain_id, product_id: last.product_id, period: last.period } : null, observationCount: result.count ?? 0 };
  }
  async getPeriods(chainId?: string | null) {
    let query = this.client.from(publishedPeriodsView).select("period");
    if (chainId) query = query.eq("chain_id", chainId);
    const result = await query.order("period", { ascending: true }).abortSignal(this.controller.signal);
    this.check(result, "getPeriods", publishedPeriodsView, "period_catalog");
    // Never infer months from bounds: a gap remains a gap. The compact view is
    // scope x month; union duplicate months only within this user's RLS read.
    const periods = result.data as { period: string }[];
    if (!Array.isArray(periods) || periods.some(p => !p || typeof p.period !== "string" || !/^\d{4}-(0[1-9]|1[0-2])-01$/.test(p.period))) this.fail("getPeriods", publishedPeriodsView, "period_catalog", "INVALID_PERIOD", result.status ?? 200);
    return [...new Set(periods.map(p => p.period.slice(0, 7)))].sort();
  }
  async getForecastHistory(filters: Filters): Promise<HistoricalMonth[]> {
    if (!filters.chainId) return []; // Never consolidate overlapping multi-chain scopes.
    const months = new Map<string, HistoricalMonth>();
    let cursor: Cursor | null = null;
    for (let page = 0; page < 800; page++) {
      const result = await this.getHistoricalObservations(filters, { size: 250, cursor });
      for (const row of result.rows) {
        if (row.chain_id !== filters.chainId || filters.productId && row.product_id !== filters.productId || filters.categoryId && row.product.category_id !== filters.categoryId) throw new Error("chart_identity_mismatch");
        const period = row.period.slice(0, 7);
        const month = months.get(period) ?? { period, sale: null, order: null, delivery: null };
        for (const [metric, key] of [["SALES", "sale"], ["ORDER", "order"], ["DELIVERY", "delivery"]] as const) {
          if (row[metric] !== null) month[key] = (month[key] ?? 0) + row[metric];
        }
        months.set(period, month);
      }
      if (!result.next) return [...months.values()].sort((a, b) => a.period.localeCompare(b.period));
      if (JSON.stringify(cursor) === JSON.stringify(result.next)) throw new Error("chart_pagination_invalid");
      cursor = result.next;
    }
    throw new Error("chart_history_limit"); // Never render a silently truncated aggregate.
  }
  async getCustomerForecast(filters: Filters, issuePeriod: string): Promise<CustomerMonth[]> {
    if (!filters.chainId) return [];
    // Only visible, validated/frozen versions; drafts and superseded versions are not authority.
    const versions = await this.rows<{ id: string; chain_id: string; status: string; issue_period: string }[]>(this.client.from("customer_forecast_versions")
      .select("id,chain_id,status,issue_period").eq("chain_id", filters.chainId).in("status", ["VALIDATED", "FROZEN"])
      .lte("issue_period", issuePeriod + "-01").order("issue_period", { ascending: false }).order("version_no", { ascending: false }).limit(1).abortSignal(this.controller.signal));
    const version = versions[0];
    if (!version) return [];
    if (versions.length !== 1 || version.chain_id !== filters.chainId || !["VALIDATED", "FROZEN"].includes(version.status) || version.issue_period.slice(0, 7) > issuePeriod) throw new Error("customer_version_invalid");
    const products = await this.getProducts(filters.chainId, filters.categoryId, filters.productId ? "" : filters.search);
    const allowed = new Set(products.filter(p => !filters.productId || p.id === filters.productId).map(p => p.id));
    if (!allowed.size) return [];
    const months = new Map<string, number>(), seen = new Set<string>();
    for (let offset = 0; offset < 200000; offset += 500) {
      let query = this.client.from("customer_forecast_rows").select("chain_id,product_id,forecast_version_id,target_period,value")
        .eq("chain_id", filters.chainId).eq("forecast_version_id", version.id);
      if (filters.productId) query = query.eq("product_id", filters.productId);
      const rows: { chain_id: string; product_id: string; forecast_version_id: string; target_period: string; value: number }[] = await this.rows(query.order("product_id").order("target_period").range(offset, offset + 499).abortSignal(this.controller.signal));
      for (const row of rows) {
        if (row.chain_id !== filters.chainId || row.forecast_version_id !== version.id || !products.some(p => p.id === row.product_id) && !filters.categoryId && !filters.search && !filters.productId) throw new Error("customer_identity_mismatch");
        if (!allowed.has(row.product_id)) continue;
        const key = `${row.product_id}/${row.target_period}`;
        if (seen.has(key) || !/^\d{4}-(0[1-9]|1[0-2])-01$/.test(row.target_period) || row.value === null || !Number.isFinite(Number(row.value)) || Number(row.value) < 0) throw new Error("customer_row_invalid");
        seen.add(key);
        const period = row.target_period.slice(0, 7);
        months.set(period, (months.get(period) ?? 0) + Number(row.value));
      }
      if (rows.length < 500) return [...months].sort(([a], [b]) => a.localeCompare(b)).map(([period, value]) => ({ period, value }));
    }
    throw new Error("customer_forecast_limit");
  }
  async getHistoricalSummary(filters: Filters): Promise<Summary> {
    const [chains, products] = await Promise.all([this.getVisibleChains(), this.getProducts(filters.chainId, filters.categoryId, filters.search)]);
    const scoped = chains.filter(c => c.has_history && (!filters.chainId || c.id === filters.chainId));
    const byScope: Summary["byScope"] = [];
    for (const chain of scoped) {
      const { query } = await this.filtered({ ...filters, chainId: chain.id }, true);
      const r = await query; this.ensureOpen(); if (r.error) throw new Error("read_failed");
      if ((r.count ?? 0) > 0) byScope.push({ chain, observations: r.count ?? 0 });
    }
    const { query } = await this.filtered(filters);
    const latest = await this.rows<{ period: string }[]>(query.order("period", { ascending: false }).limit(1));
    return { scopeCount: byScope.length, productCount: products.filter(p => !filters.productId || p.id === filters.productId).length, observationCount: byScope.reduce((n, s) => n + s.observations, 0), latestPeriod: latest[0]?.period.slice(0, 7) ?? null, byScope };
  }
  async getProfiles() {
    const me = await this.getCurrentProfile();
    if (me.global_role !== "ADMIN") throw new Error("admin_required");
    return this.rows<Profile[]>(this.client.from("profiles").select("id,full_name,global_role,status").order("full_name").limit(1000).abortSignal(this.controller.signal));
  }
}
