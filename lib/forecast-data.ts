import type { SupabaseClient } from "@supabase/supabase-js";
import { emptyFilters, type Category, type Chain, type Cursor, type Filters, type ForecastReadRepository, type HistoricalPage, type HistoricalRow, type OperationalMetric, type OperationalReadContext, type Product, type Profile, type Summary } from "./supabase/types";
import { controlledReadCode, logReadDiagnostic, PortalReadError, type PortalReadDiagnostic } from "./portal-read-diagnostic";
import { RailwayPreviewClient } from "./forecast-preview";
import { SupabaseUserAdminClient } from "./supabase/user-admin";
import type { HistoricalMonth, CustomerMonth } from "./forecast-chart-data";
import { SupabaseReportReader } from "./forecast-report-repository";
import { attachCommercial, filteredProducts, hasCommercialFilters, matchesChainScope, scopedQueryChainId } from "./commercial-filters";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type ProductMapping = { id: string; identifier_mappings?: [string | null, string | null][] };
type Mapping = { scope?: { parent_uuid?: string; scope_type?: string }; products?: ProductMapping[] };
const observationReader = "operational_read_observations";
const periodReader = "operational_read_periods";
export const actualMetrics = ["SALES", "ORDER", "DELIVERY"] as const;
const allMetrics: readonly OperationalMetric[] = [...actualMetrics, "FCST_TOWELL_HISTORY", "FCST_CLIENTE_HISTORY"];
export const observationFields = "observation_id,chain_id,chain_name,product_id,product_code,variant_code,product_description,category_id,period,metric_code,value,value_text,availability_source,version_no,available_at";
type Observation = Cursor & { metric_code: OperationalMetric; value: number; value_text?: string; availability_source: string; product_code: string; variant_code: string | null; product_description: string; category_id: string | null; chain_name: string; observation_id?: string; version_no?: number; available_at?: string | null };

export function operationalArguments(context: OperationalReadContext) {
  return { p_source_hash: context.source_hash, p_generation: context.generation };
}

export function observationScope(filters: Filters, productIds?: string[]) {
  return {
    p_chain_id: scopedQueryChainId(filters),
    p_product_ids: productIds ?? (filters.productId ? [filters.productId] : undefined),
    p_from: filters.periodRange[0] ? `${filters.periodRange[0]}-01` : undefined,
    p_to: filters.periodRange[1] ? `${filters.periodRange[1]}-01` : undefined,
  };
}

function sameContext(a: OperationalReadContext, b: OperationalReadContext) {
  return a.source_hash === b.source_hash && a.generation === b.generation;
}

export function assertActiveProfile(profile: Profile | null, userId: string): Profile {
  if (!profile || profile.id !== userId || profile.status !== "ACTIVE" || !["ADMIN", "EDITOR", "VIEWER"].includes(profile.global_role)) throw new Error("profile_access_denied");
  return profile;
}
export function resetForChain(filters: Filters, chainId: string | null): Filters {
  return { ...emptyFilters, chainId, periodRange: [null, null] };
}
export function combineObservations(observations: Observation[], products: Map<string, Product>, size: number, requireExact = false): HistoricalRow[] {
  const groups = new Map<string, HistoricalRow>();
  for (const o of observations) {
    const key = `${o.chain_id}/${o.product_id}/${o.period}`;
    let row = groups.get(key);
    if (!row) {
      row = { chain_id: o.chain_id, product_id: o.product_id, period: o.period, product: products.get(o.product_id) ?? { id: o.product_id, chain_id: o.chain_id, category_id: o.category_id, product_code: o.product_code, variant_code: o.variant_code, description: o.product_description, identifiers: [] }, chain: o.chain_name, SALES: null, ORDER: null, DELIVERY: null, availability: o.availability_source };
      groups.set(key, row);
    }
    if (!allMetrics.includes(o.metric_code)) throw new Error("invalid_observation_metric");
    if (row.exact?.[o.metric_code] !== undefined) throw new Error("duplicate_current_metric");
    const literal = o.value_text ?? String(o.value);
    const value = Number(o.value);
    if (!Number.isFinite(value) || value < 0 || requireExact && typeof o.value_text !== "string" || (o.value_text !== undefined && (!/^\d+(?:\.\d+)?$/.test(literal) || Number(literal) !== value))) throw new Error("invalid_observation_value");
    row.exact = { ...row.exact, [o.metric_code]: literal };
    if (o.metric_code === "FCST_TOWELL_HISTORY" || o.metric_code === "FCST_CLIENTE_HISTORY") continue;
    row[o.metric_code] = value;
    if (o.observation_id) row.observations = { ...row.observations, [o.metric_code]: { id: o.observation_id, version: o.version_no ?? 1, availableAt: o.available_at ?? null } };
  }
  return [...groups.values()].slice(0, size);
}

// Instance-owned cache; discarded on every identity/session change and logout.
// There is no service credential or server-global RLS result cache.
export class SupabaseForecastReadRepository implements ForecastReadRepository {
  private controller = new AbortController();
  private catalog: Product[] | null = null;
  private metadata: Map<string, Mapping> | null = null;
  private chainTask: { context: OperationalReadContext; task: Promise<Chain[]> } | null = null;
  private mappingTask: Promise<Map<string, Mapping>> | null = null;
  private catalogTask: Promise<Product[]> | null = null;
  private commercialTask: { context: OperationalReadContext; task: Promise<import("./supabase/types").CommercialClassification[]> } | null = null;
  private readDiagnostics = new Map<PortalReadDiagnostic["endpoint"], PortalReadDiagnostic>();
  readonly previews: RailwayPreviewClient;
  readonly userAdmin: SupabaseUserAdminClient;
  private reports: SupabaseReportReader;
  constructor(private client: SupabaseClient, private userId: string, private onReadFailure?: (diagnostic: PortalReadDiagnostic) => void, private authClient: SupabaseClient = client) {
    this.previews = new RailwayPreviewClient(authClient, undefined, client); this.userAdmin = new SupabaseUserAdminClient(client);
    this.reports = new SupabaseReportReader(client, this, this.controller.signal);
  }
  async openOperationalRead(): Promise<OperationalReadContext> {
    this.ensureOpen();
    const { data, error } = await this.client.rpc("operational_read_context").abortSignal(this.controller.signal);
    this.ensureOpen();
    const context = Array.isArray(data) && data.length === 1 ? data[0] as OperationalReadContext | null : null;
    if (error || !context || !/^[0-9a-f]{64}$/.test(context.source_hash) || !Number.isSafeInteger(context.generation) || context.generation < 0
        || !/^20\d{2}-(0[1-9]|1[0-2])$/.test(context.first_period) || !/^20\d{2}-(0[1-9]|1[0-2])$/.test(context.last_period) || context.first_period > context.last_period) throw new Error("official_source_unavailable");
    return context;
  }
  private async currentContext(context?: OperationalReadContext) {
    const current = await this.openOperationalRead();
    if (context && !sameContext(context, current)) throw new Error("official_source_changed");
    return current;
  }
  async getReportSnapshot(filters: Filters, context?: OperationalReadContext) { return this.reports.snapshot(filters, await this.currentContext(context)); }
  async getReportHistory(filters: Filters, context?: OperationalReadContext) { return this.reports.history(filters, await this.currentContext(context), true); }
  async getReportInventory(filters: Filters, context: OperationalReadContext) { return this.reports.inventory(filters, await this.currentContext(context)); }
  async getCommercialCatalog(context?: OperationalReadContext) {
    const current = await this.currentContext(context);
    if (this.commercialTask && sameContext(this.commercialTask.context, current)) return this.commercialTask.task;
    const task = this.previews.commercialCatalog(current).then(rows => { this.ensureOpen(); return rows; }).catch(error => {
      if (this.commercialTask?.task === task) this.commercialTask = null;
      throw error;
    });
    this.commercialTask = { context: current, task };
    return task;
  }
  async getReportPeriods(chainId?: string | null, context?: OperationalReadContext) { return this.reports.periods(chainId ?? null, await this.currentContext(context)); }
  async getDatabasePage(section: import("./forecast-reporting").DatabaseSection, filters: Filters, page: number, size: 50 | 100 | 250, context?: OperationalReadContext) { return this.reports.databasePage(section, filters, page, size, await this.currentContext(context)); }
  dispose() { this.controller.abort(); this.previews.dispose(); this.catalog = null; this.metadata = null; this.chainTask = null; this.mappingTask = null; this.catalogTask = null; this.readDiagnostics.clear(); }
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
    const { data, error } = await this.authClient.auth.getUser();
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
  async getVisibleChains(context?: OperationalReadContext): Promise<Chain[]> {
    context = await this.currentContext(context);
    if (this.chainTask && sameContext(this.chainTask.context, context)) return this.chainTask.task;
    const task = this.loadChains(context).catch(error => {
      if (this.chainTask?.task === task) this.chainTask = null;
      throw error;
    });
    this.chainTask = { context, task };
    return task;
  }
  private async loadChains(context: OperationalReadContext) {
    const masters = await this.rows<Omit<Chain, "has_history" | "parentId" | "scopeType">[]>(this.client.from("chains").select("id,code,name,status").order("name").limit(1000).abortSignal(this.controller.signal), "getVisibleChains", "chains");
    const mappings = await this.getMappings();
    const result: Chain[] = [];
    // HEAD counts, not raw facts; no per-product query or invented has_history column.
    for (let offset = 0; offset < masters.length; offset += 6) {
      result.push(...await Promise.all(masters.slice(offset, offset + 6).map(async c => {
        const q = await this.actualObservations(context, { ...emptyFilters, chainId: c.id }, true).eq("chain_id", c.id).abortSignal(this.controller.signal);
        this.check(q, "getVisibleChains", observationReader, "head_count");
        const meta = mappings.get(c.id)?.scope;
        return { ...c, has_history: (q.count ?? 0) > 0, parentId: meta?.parent_uuid ?? null, scopeType: meta?.scope_type ?? null };
      })));
    }
    return result;
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
    const classifications = await this.getCommercialCatalog();
    const identities = new Set(classifications.map(row => row.product_id));
    const represented = catalog.filter(product => identities.has(product.id));
    if (represented.length !== classifications.length) throw new Error("official_catalog_lineage");
    const operational = attachCommercial(represented, classifications);
    const term = search.toLocaleLowerCase().trim();
    try { return operational.filter(p => (!chainId || p.chain_id === chainId) && (!categoryId || p.category_id === categoryId) && (!term || [p.description, p.product_code, p.variant_code, ...p.identifiers.flat()].some(v => v?.toLocaleLowerCase().includes(term)))); }
    catch { this.fail("getProducts", "products", "catalog_filter", "TRANSFORM_FAILED", 200); }
  }
  private actualObservations(context: OperationalReadContext, filters: Filters, head = false) {
    return this.client.rpc(observationReader, { ...operationalArguments(context), ...observationScope(filters), p_mode: "OPERATIONAL", p_metric_codes: actualMetrics }, { count: "exact", head })
      .select(head ? "observation_id" : observationFields).in("metric_code", actualMetrics);
  }
  private async filtered(filters: Filters, context: OperationalReadContext, head = false) {
    const selected = await filteredProducts(this, filters);
    let q = this.actualObservations(context, filters, head);
    if (filters.chainId) q = q.eq("chain_id", filters.chainId);
    if (filters.productId) q = q.eq("product_id", filters.productId);
    if (filters.categoryId) q = q.eq("category_id", filters.categoryId);
    if (filters.search || hasCommercialFilters(filters)) q = selected.length ? q.in("product_id", selected.map(p => p.id)) : q.eq("product_id", "00000000-0000-0000-0000-000000000000");
    if (filters.periodRange[0]) q = q.gte("period", filters.periodRange[0] + "-01");
    if (filters.periodRange[1]) q = q.lte("period", filters.periodRange[1] + "-01");
    return { query: q.abortSignal(this.controller.signal), products: selected };
  }
  async getHistoricalObservations(filters: Filters, pagination: { size: 50 | 100 | 250; cursor?: Cursor | null }, context?: OperationalReadContext): Promise<HistoricalPage> {
    if (![50, 100, 250].includes(pagination.size)) throw new Error("invalid_page_size");
    const c = pagination.cursor;
    if (c && (!uuid.test(c.chain_id) || !uuid.test(c.product_id) || !/^\d{4}-\d{2}-01$/.test(c.period) || !c.context || context && !sameContext(c.context, context))) throw new Error("invalid_cursor");
    context = await this.currentContext(context ?? c?.context);
    const cursorFor = (row: HistoricalRow) => ({ chain_id: row.chain_id, product_id: row.product_id, period: row.period, context });
    if (hasCommercialFilters(filters)) {
      const rows = (await this.reports.history(filters, context)).sort((a, b) => a.chain_id.localeCompare(b.chain_id) || a.product_id.localeCompare(b.product_id) || a.period.localeCompare(b.period));
      const after = c ? rows.filter(r => r.chain_id > c.chain_id || r.chain_id === c.chain_id && (r.product_id > c.product_id || r.product_id === c.product_id && r.period > c.period)) : rows;
      const page = after.slice(0, pagination.size), last = page.at(-1);
      return { rows: page, next: last && after.length > pagination.size ? cursorFor(last) : null, observationCount: rows.reduce((sum, r) => sum + [r.SALES, r.ORDER, r.DELIVERY].filter(v => v !== null).length, 0) };
    }
    const { query, products } = await this.filtered(filters, context);
    let q = query;
    if (c) {
      q = q.or(`chain_id.gt.${c.chain_id},and(chain_id.eq.${c.chain_id},product_id.gt.${c.product_id}),and(chain_id.eq.${c.chain_id},product_id.eq.${c.product_id},period.gt.${c.period})`);
    }
    const result = await q.order("chain_id").order("product_id").order("period").order("metric_code").limit(pagination.size * 3 + 3);
    this.check(result, "getHistoricalObservations", observationReader, c ? "cursor_page" : "page_1");
    const raw = result.data as unknown as Observation[];
    let grouped: HistoricalRow[];
    try { grouped = combineObservations(raw, new Map(products.map(p => [p.id, p])), pagination.size + 1, true); }
    catch (error) {
      const code = error instanceof Error && error.message === "duplicate_current_metric" ? "DUPLICATE_CURRENT_METRIC" : error instanceof Error && error.message === "invalid_observation_value" ? "INVALID_OBSERVATION_VALUE" : "TRANSFORM_FAILED";
      this.fail("getHistoricalObservations", observationReader, "combine_metrics", code, result.status ?? 200);
    }
    const rows = grouped.slice(0, pagination.size);
    const last = rows.at(-1);
    return { rows, next: grouped.length > pagination.size && last ? cursorFor(last) : null, observationCount: result.count ?? 0 };
  }
  async getPeriods(chainId?: string | null, context?: OperationalReadContext) {
    context ??= await this.openOperationalRead();
    const parameters = operationalArguments(context);
    const periodsFor = (scope: string | null) => this.client.rpc(periodReader, { ...parameters, p_chain_id: scope })
      .select("period").order("period", { ascending: true }).abortSignal(this.controller.signal);
    const result = await periodsFor(chainId ?? null);
    this.check(result, "getPeriods", periodReader, "period_catalog");
    // Never infer months from bounds: a gap remains a gap. The compact view is
    // scope x month; union duplicate months only within this user's RLS read.
    const periods = result.data as { period: string }[];
    if (!Array.isArray(periods) || periods.some(p => !p || typeof p.period !== "string" || !/^\d{4}-(0[1-9]|1[0-2])-01$/.test(p.period))) this.fail("getPeriods", periodReader, "period_catalog", "INVALID_PERIOD", result.status ?? 200);
    return [...new Set(periods.map(p => p.period.slice(0, 7)))].sort();
  }
  async getForecastHistory(filters: Filters, context?: OperationalReadContext): Promise<HistoricalMonth[]> {
    if (!filters.chainId) return []; // Never consolidate overlapping multi-chain scopes.
    context ??= await this.openOperationalRead();
    const months = new Map<string, HistoricalMonth>();
    let cursor: Cursor | null = null;
    for (let page = 0; page < 800; page++) {
      const result = await this.getHistoricalObservations(filters, { size: 250, cursor }, context);
      for (const row of result.rows) {
        if (!matchesChainScope(row.chain_id, filters) || filters.productId && row.product_id !== filters.productId || filters.categoryId && row.product.category_id !== filters.categoryId) throw new Error("chart_identity_mismatch");
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
  async getCustomerForecast(filters: Filters, issuePeriod: string, context?: OperationalReadContext): Promise<CustomerMonth[]> {
    if (!filters.chainId) return [];
    if (!/^20\d{2}-(0[1-9]|1[0-2])$/.test(issuePeriod)) throw new Error("invalid_period");
    context ??= await this.openOperationalRead();
    const rows = await this.reports.history(filters, context, true);
    const months = new Map<string, number>();
    for (const row of rows) {
      if (!matchesChainScope(row.chain_id, filters)) throw new Error("customer_identity_mismatch");
      const literal = row.exact?.FCST_CLIENTE_HISTORY;
      if (literal === undefined) continue;
      const period = row.period.slice(0, 7);
      months.set(period, (months.get(period) ?? 0) + Number(literal));
    }
    return [...months].sort(([a], [b]) => a.localeCompare(b)).map(([period, value]) => ({ period, value }));
  }
  async getHistoricalSummary(filters: Filters, context?: OperationalReadContext): Promise<Summary> {
    context ??= await this.openOperationalRead();
    if (hasCommercialFilters(filters)) {
      const [chains, products, rows] = await Promise.all([this.getVisibleChains(context), filteredProducts(this, filters), this.reports.history(filters, context)]);
      const byScope = chains.map(chain => ({ chain, observations: rows.filter(r => r.chain_id === chain.id).reduce((n, r) => n + [r.SALES, r.ORDER, r.DELIVERY].filter(v => v !== null).length, 0) })).filter(r => r.observations > 0);
      return { scopeCount: byScope.length, productCount: products.length, observationCount: byScope.reduce((sum, r) => sum + r.observations, 0), latestPeriod: rows.map(r => r.period.slice(0, 7)).sort().at(-1) ?? null, byScope };
    }
    const [chains, products] = await Promise.all([this.getVisibleChains(context), filteredProducts(this, filters)]);
    const scoped = chains.filter(c => c.has_history && (!filters.chainId || c.id === filters.chainId));
    const byScope: Summary["byScope"] = [];
    for (const chain of scoped) {
      const { query } = await this.filtered({ ...filters, chainId: chain.id }, context, true);
      const r = await query; this.ensureOpen(); if (r.error) throw new Error("read_failed");
      if ((r.count ?? 0) > 0) byScope.push({ chain, observations: r.count ?? 0 });
    }
    const { query } = await this.filtered(filters, context);
    const latest = await this.rows<{ period: string }[]>(query.order("period", { ascending: false }).limit(1));
    return { scopeCount: byScope.length, productCount: products.filter(p => !filters.productId || p.id === filters.productId).length, observationCount: byScope.reduce((n, s) => n + s.observations, 0), latestPeriod: latest[0]?.period.slice(0, 7) ?? null, byScope };
  }
  async getProfiles() {
    const me = await this.getCurrentProfile();
    if (me.global_role !== "ADMIN") throw new Error("admin_required");
    return this.rows<Profile[]>(this.client.from("profiles").select("id,full_name,global_role,status").order("full_name").limit(1000).abortSignal(this.controller.signal));
  }
}
