import type { SupabaseClient } from "@supabase/supabase-js";
import type { Filters, ForecastReadRepository, OperationalInventory, OperationalReadContext, Product } from "./supabase/types";
import { emptyFilters } from "./supabase/types";
import type { CustomerForecast, DatabasePage, DatabaseSection, ReportCalculation, ReportDecision, ReportHorizon, ReportLive, ReportSnapshot } from "./forecast-reporting";
import { addReportMonth, amount, matchesOfficialSource } from "./forecast-reporting";
import { attachCommercial, filteredProducts, hasCommercialFilters, selectedScopeIds } from "./commercial-filters";
import { actualMetrics, combineObservations, observationFields, observationScope, operationalArguments } from "./forecast-data";

const calculationFields = "id,chain_id,product_id,objective,calculation_no,calculation_code,issue_period,cutoff_at,status,created_at,created_by,preview_id,data_snapshot_hash,content_hash,input_snapshot,candidate_metrics,research_snapshot,suggested_reference,scope_leader,evidence_mode,engine_version,git_sha,recalculation_reason";
const horizonFields = "id,calculation_id,chain_id,product_id,horizon,target_period,statistical_value,ml_value,ensemble_value,ensemble_ml_component,statistical_model,ml_model,ensemble_ml_model,statistical_weight,ml_weight,p10,p50,p90,p95,band_basis,band_observations,band_status";
const decisionFields = "id,calculation_id,chain_id,product_id,system_suggestion,selected_candidate,selected_curve,decision_reason,comment,selected_by,selected_at,supersedes_selection_id";
const liveFields = "id,calculation_id,chain_id,product_id,horizon,target_period,candidate,forecast_value,actual_sale,sale_observation_id,sale_version_no,absolute_error,signed_error,evaluated_at";
export class SupabaseReportReader {
  constructor(private client: SupabaseClient, private repository: ForecastReadRepository, private signal: AbortSignal) {}
  private ensureOpen() { if (this.signal.aborted) throw new Error("session_disposed"); }
  private observations(context: OperationalReadContext, fields: string, includeForecasts: boolean, filters: Filters, productIds?: string[]) {
    const query = this.client.rpc("operational_read_observations", { ...operationalArguments(context), ...observationScope(filters, productIds), p_metric_codes: includeForecasts ? undefined : actualMetrics }).select(fields);
    return includeForecasts ? query : query.in("metric_code", actualMetrics);
  }
  private async read<T>(table: string, fields: string, filters: Filters, products?: Product[], pin?: { context: OperationalReadContext; includeForecasts: boolean }) {
    this.ensureOpen();
    const output: T[] = [];
    const scopes = selectedScopeIds(filters);
    if (scopes?.length === 0) return output;
    if (products && !products.length) return output;
    const chunks = products ? Array.from({ length: Math.ceil(products.length / 80) }, (_, i) => products.slice(i * 80, i * 80 + 80).map(p => p.id)) : [null];
    for (const ids of chunks) {
      for (let offset = 0; offset < 100000; offset += 500) {
        let query = (pin ? this.observations(pin.context, fields, pin.includeForecasts, filters, ids ?? undefined) : this.client.from(table).select(fields))
          .order(pin ? "observation_id" : "id").range(offset, offset + 499);
        if (scopes) query = query.in("chain_id", scopes);
        if (ids) query = query.in("product_id", ids);
        if (pin) {
          if (filters.periodRange[0]) query = query.gte("period", filters.periodRange[0] + "-01");
          if (filters.periodRange[1]) query = query.lte("period", filters.periodRange[1] + "-01");
        }
        const result = await query.abortSignal(this.signal);
        this.ensureOpen();
        if (result.error || !Array.isArray(result.data)) throw new Error("report_read_failed");
        output.push(...result.data as T[]);
        if (result.data.length < 500) break;
        if (offset === 99500) throw new Error("report_read_limit");
      }
    }
    return output;
  }
  async snapshot(filters: Filters, context: OperationalReadContext): Promise<ReportSnapshot> {
    const scopes = selectedScopeIds(filters);
    const categoryTask = scopes === null ? this.repository.getCategories(null) :
      Promise.all(scopes.map(id => this.repository.getCategories(id))).then(pages => [...new Map(pages.flat().map(c => [c.id, c])).values()]);
    const [catalog, allChains, categories] = await Promise.all([filteredProducts(this.repository, filters), this.repository.getVisibleChains(context), categoryTask]);
    const products = this.repository.getCommercialCatalog && !hasCommercialFilters(filters) ? attachCommercial(catalog, await this.repository.getCommercialCatalog()) : catalog;
    const chains = allChains;
    const chainIds = new Set(chains.map(c => c.id));
    if (products.some(p => !chainIds.has(p.chain_id))) throw new Error("report_catalog_lineage");
    const capabilities = this.repository.previews?.vintageCapabilities ? await this.repository.previews.vintageCapabilities() : { vintage_persistence: false, official_publication: false, champion_publication: false };
    let calculations: ReportCalculation[] = [], horizons: ReportHorizon[] = [], decisions: ReportDecision[] = [], live: ReportLive[] = [];
    if (capabilities.forecast_calculation_history) {
      const stored = await this.read<ReportCalculation>("forecast_calculations", calculationFields, filters, products);
      calculations = stored.filter(row => matchesOfficialSource(row.input_snapshot?.source_context, context));
      const currentIds = new Set(calculations.map(row => row.id));
      if (currentIds.size) {
        const [storedHorizons, storedDecisions, storedLive] = await Promise.all([
          this.read<ReportHorizon>("forecast_calculation_horizons", horizonFields, filters, products),
          this.read<ReportDecision>("forecast_selection_events", decisionFields, filters, products),
          this.read<ReportLive>("forecast_live_evaluations", liveFields, filters, products),
        ]);
        horizons = storedHorizons.filter(row => currentIds.has(row.calculation_id));
        decisions = storedDecisions.filter(row => currentIds.has(row.calculation_id));
        live = storedLive.filter(row => currentIds.has(row.calculation_id));
      }
      const calculationMap = new Map(calculations.map(c => [c.id, c]));
      for (const row of [...horizons, ...decisions, ...live]) {
        const calculation = calculationMap.get(row.calculation_id);
        if (!calculation || row.chain_id !== calculation.chain_id || row.product_id !== calculation.product_id) throw new Error("report_calculation_lineage");
      }
      for (const c of calculations.filter(c => ["READY_FOR_DECISION", "DECIDED", "SUPERSEDED"].includes(c.status))) {
        const rows = horizons.filter(h => h.calculation_id === c.id);
        if (rows.length !== 12 || new Set(rows.map(h => h.horizon)).size !== 12 || rows.some(h => h.horizon < 1 || h.horizon > 12 || h.target_period.slice(0, 7) !== addReportMonth(c.issue_period, h.horizon) || amount(h.statistical_value) === null)) throw new Error("report_horizon_lineage");
      }
    }
    const customers: CustomerForecast[] = [];
    const provisionals = this.repository.previews?.provisionalForecasts && products.length ?
      (await Promise.all((scopes ?? [null]).map(id => this.repository.previews!.provisionalForecasts!(id, filters.productId, context)))).flat() : [];
    const allowed = new Map(products.map(p => [p.id, p.chain_id]));
    for (const row of [...calculations, ...horizons, ...decisions, ...live, ...customers]) {
      if (allowed.get(row.product_id) !== row.chain_id) throw new Error("report_scope_mismatch");
    }
    if (this.repository.openOperationalRead && !matchesOfficialSource(await this.repository.openOperationalRead(), context)) throw new Error("official_source_changed");
    return { products, chains, categories, calculations, horizons, decisions, live, customers, sourceContext: context,
      provisionals: provisionals.filter(p => allowed.get(p.product_id) === p.chain_id && matchesOfficialSource(p.source_context, context)), capabilities };
  }
  async history(filters: Filters, context: OperationalReadContext, includeForecasts = false) {
    const products = await filteredProducts(this.repository, filters);
    const observations = await this.read<Parameters<typeof combineObservations>[0][number]>("operational_read_observations", observationFields, filters, products, { context, includeForecasts });
    const catalog = new Map(products.map(p => [p.id, p]));
    if (observations.some(o => catalog.get(o.product_id)?.chain_id !== o.chain_id)) throw new Error("report_scope_mismatch");
    return combineObservations(observations, catalog, observations.length, true);
  }
  async inventory(filters: Filters, context: OperationalReadContext) {
    this.ensureOpen();
    const products = await filteredProducts(this.repository, filters);
    const byId = new Map(products.map(p => [p.id, p.chain_id]));
    const output: OperationalInventory[] = [], seen = new Set<string>();
    for (let start = 0; start < products.length; start += 80) {
      const ids = products.slice(start, start + 80).map(p => p.id);
      for (let offset = 0; ; offset += 500) {
        let query = this.client.rpc("operational_read_inventory", { ...operationalArguments(context), ...observationScope(filters, ids) })
          .select("chain_id,product_id,period,format,upc,purchase_type,quantity_text")
          .in("product_id", ids).order("chain_id").order("product_id").order("period").range(offset, offset + 499);
        if (filters.periodRange[0]) query = query.gte("period", `${filters.periodRange[0]}-01`);
        if (filters.periodRange[1]) query = query.lte("period", `${filters.periodRange[1]}-01`);
        const { data, error } = await query.abortSignal(this.signal);
        this.ensureOpen();
        if (error || !Array.isArray(data)) throw new Error("inventory_read_failed");
        for (const row of data as OperationalInventory[]) {
          const key = `${row.chain_id}/${row.product_id}/${row.period}`;
          if (seen.has(key) || !ids.includes(row.product_id) || byId.get(row.product_id) !== row.chain_id || !/^\d+(?:\.\d+)?$/.test(row.quantity_text)) throw new Error("inventory_lineage_invalid");
          seen.add(key); output.push(row);
        }
        if (data.length < 500) break;
        if (offset >= 99500) throw new Error("report_read_limit");
      }
    }
    return output;
  }
  async periods(chainId: string | null, context: OperationalReadContext) {
    const historical = await this.repository.getPeriods(chainId, context);
    const caps = this.repository.previews?.vintageCapabilities ? await this.repository.previews.vintageCapabilities() : null;
    if (!caps?.forecast_calculation_history) return historical;
    const snapshot = await this.snapshot({ ...emptyFilters, chainId }, context);
    return [...new Set([...historical, ...snapshot.horizons.map(h => h.target_period.slice(0, 7)), ...snapshot.provisionals.flatMap(p => p.horizons.map(h => h.target_period.slice(0, 7)))])].sort();
  }
  async databasePage(section: DatabaseSection, filters: Filters, page: number, size: 50 | 100 | 250, context: OperationalReadContext): Promise<DatabasePage> {
    if (!Number.isInteger(page) || page < 0 || ![50, 100, 250].includes(size)) throw new Error("invalid_report_page");
    if (section === "history" || section === "observations") return this.observationPage(section, filters, page, size, context);
    const snapshot = await this.snapshot(filters, context);
    const chain = new Map(snapshot.chains.map(c => [c.id, c.name]));
    const product = new Map(snapshot.products.map(p => [p.id, p]));
    const calc = new Map(snapshot.calculations.map(c => [c.id, c]));
    let headers: string[] = [], records: DatabasePage["rows"] = [];
    if (section === "catalog") {
      headers = ["Cadena", "Scope padre", "Producto", "Código", "Variante", "Categoría", "ID producto"];
      records = snapshot.products.map(p => ({ id: p.id, cells: [chain.get(p.chain_id) ?? p.chain_id, chain.get(snapshot.chains.find(c => c.id === p.chain_id)?.parentId ?? "") ?? null, p.description, p.product_code, p.variant_code, snapshot.categories.find(c => c.id === p.category_id)?.name ?? null, p.id] }));
      records.push(...snapshot.chains.filter(c => !(filters.productId || filters.categoryId || filters.search) || snapshot.products.some(p => p.chain_id === c.id)).map(c => ({ id: `chain/${c.id}`, cells: [c.name, c.parentId, "Scope", c.code, null, c.scopeType, c.id] })));
    } else if (section === "calculations") {
      headers = ["Cálculo", "Cadena", "Producto", "Corte", "Creado", "Estado", "Usuario", "Calculation ID"];
      records = snapshot.calculations.map(c => ({ id: c.id, cells: [c.calculation_code, chain.get(c.chain_id) ?? c.chain_id, product.get(c.product_id)?.description ?? c.product_id, c.issue_period.slice(0, 7), c.created_at, c.status, c.created_by, c.id], detail: { preview_id: c.preview_id, engine_version: c.engine_version, git_sha: c.git_sha, data_snapshot_hash: c.data_snapshot_hash, input_snapshot: c.input_snapshot } }));
    } else if (section === "models") {
      headers = ["Cálculo", "Producto", "Periodo objetivo", "H", "Estadístico", "ML", "Ensemble", "Bandas", "Calculation ID"];
      records = snapshot.horizons.map(h => ({ id: h.id, cells: [calc.get(h.calculation_id)?.calculation_code ?? null, product.get(h.product_id)?.description ?? h.product_id, h.target_period.slice(0, 7), h.horizon, h.statistical_value, h.ml_value, h.ensemble_value, h.band_status ?? null, h.calculation_id], detail: { statistical_model: h.statistical_model, ml_model: h.ml_model, ensemble_ml_model: h.ensemble_ml_model, candidate_metrics: calc.get(h.calculation_id)?.candidate_metrics, statistical_weight: h.statistical_weight, ml_weight: h.ml_weight } }));
    } else if (section === "decisions") {
      headers = ["Cadena", "Producto", "Cálculo", "Alternativa", "Usuario", "Fecha", "Motivo", "Event ID"];
      records = snapshot.decisions.map(e => ({ id: e.id, cells: [chain.get(e.chain_id) ?? e.chain_id, product.get(e.product_id)?.description ?? e.product_id, calc.get(e.calculation_id)?.calculation_code ?? null, e.selected_candidate, e.selected_by, e.selected_at, e.decision_reason, e.id], detail: { comment: e.comment, selected_curve: e.selected_curve, calculation_id: e.calculation_id } }));
    } else if (section === "research") {
      headers = ["Cadena", "Proveedor", "Corte", "Estado", "Creado", "Snapshot ID"];
      records = snapshot.calculations.filter(c => c.research_snapshot).map(c => ({ id: c.id, cells: [chain.get(c.chain_id) ?? c.chain_id, "Snapshot del cálculo", c.issue_period, "Conservado", c.created_at, c.id], detail: c.research_snapshot! }));
    } else if (section === "performance") {
      headers = ["Tipo", "Cadena", "Producto", "Cálculo", "Periodo", "H", "Candidato", "Error absoluto", "ID"];
      records = snapshot.live.map(v => ({ id: v.id, cells: ["LIVE almacenado", chain.get(v.chain_id) ?? v.chain_id, product.get(v.product_id)?.description ?? v.product_id, calc.get(v.calculation_id)?.calculation_code ?? null, v.target_period.slice(0, 7), v.horizon, v.candidate, v.absolute_error, v.id], detail: { sale_observation_id: v.sale_observation_id, sale_version_no: v.sale_version_no, actual_sale: v.actual_sale, forecast_value: v.forecast_value, evaluated_at: v.evaluated_at } }));
      for (const c of snapshot.calculations) for (const [family, metric] of Object.entries(c.candidate_metrics)) if (metric) records.push({ id: `${c.id}/${family}`, cells: ["Validación retrospectiva", chain.get(c.chain_id) ?? c.chain_id, product.get(c.product_id)?.description ?? c.product_id, c.calculation_code, c.issue_period.slice(0, 7), null, family, null, c.id], detail: { wape_retrospective: metric.wape, bias_retrospective: metric.bias, observations: metric.observations, windows: metric.windows } });
      if (snapshot.capabilities.forecast_calculation_history) {
        const learning = await this.read<{ id: string; calculation_id: string; chain_id: string; product_id: string; target_period: string; horizon: number; metrics_json: Record<string, unknown>; signals_json: unknown[] }>("forecast_learning_events", "id,calculation_id,chain_id,product_id,target_period,horizon,metrics_json,signals_json", filters, snapshot.products);
        records.push(...learning.filter(v => calc.has(v.calculation_id)).map(v => ({ id: v.id, cells: ["Aprendizaje almacenado", chain.get(v.chain_id) ?? v.chain_id, product.get(v.product_id)?.description ?? v.product_id, calc.get(v.calculation_id)?.calculation_code ?? null, v.target_period.slice(0, 7), v.horizon, null, null, v.id], detail: { metrics: v.metrics_json, signals: v.signals_json } })));
      }
    }
    if (section === "models" || section === "performance") records = records.filter(r => { const period = String(r.cells[section === "models" ? 2 : 4] ?? ""); return !period || (!filters.periodRange[0] || period >= filters.periodRange[0]) && (!filters.periodRange[1] || period <= filters.periodRange[1]); });
    if (["calculations", "decisions", "research"].includes(section)) records = records.filter(r => {
      const period = section === "calculations" ? String(r.cells[3]) : section === "research" ? String(r.cells[2]).slice(0, 7) : snapshot.calculations.find(c => c.id === snapshot.decisions.find(d => d.id === r.id)?.calculation_id)?.issue_period.slice(0, 7);
      return !period || (!filters.periodRange[0] || period >= filters.periodRange[0]) && (!filters.periodRange[1] || period <= filters.periodRange[1]);
    });
    records.sort((a, b) => a.id.localeCompare(b.id));
    return { headers, rows: records.slice(page * size, (page + 1) * size), hasMore: records.length > (page + 1) * size };
  }
  private async observationPage(section: "history" | "observations", filters: Filters, page: number, size: number, context: OperationalReadContext): Promise<DatabasePage> {
    this.ensureOpen();
    const products = await filteredProducts(this.repository, filters);
    const chains = await this.repository.getVisibleChains(context);
    const headers = ["Periodo", "Cadena", "Producto", "Código", "Métrica", "Valor", "Versión", "Disponibilidad", "Observation ID"];
    if (!products.length) return { headers, rows: [], hasMore: false };
    const raw = section === "observations", id = raw ? "id" : "observation_id";
    const fields = `${id},chain_id,product_id,period,metric_code,value_text,version_no,availability_source,available_at,source_batch_id,source_reference`;
    type AuditObservation = { id?: string; observation_id?: string; chain_id: string; product_id: string; period: string; metric_code: string; value_text: string; version_no: number; availability_source: string; available_at: string | null; source_batch_id: string; source_reference: string | null };
    if (hasCommercialFilters(filters)) {
      const rows = await this.read<AuditObservation>("operational_read_observations", fields, filters, products, { context, includeForecasts: true });
      rows.sort((a, b) => a.chain_id.localeCompare(b.chain_id) || a.product_id.localeCompare(b.product_id) || a.period.localeCompare(b.period) || a.metric_code.localeCompare(b.metric_code) || (a.id ?? a.observation_id ?? "").localeCompare(b.id ?? b.observation_id ?? ""));
      const catalog = new Map(products.map(p => [p.id, p])), scopes = new Map(chains.map(c => [c.id, c.name]));
      if (rows.some(r => catalog.get(r.product_id)?.chain_id !== r.chain_id)) throw new Error("report_scope_mismatch");
      return { headers, rows: rows.slice(page * size, (page + 1) * size).map(o => ({ id: (o.id ?? o.observation_id)!, cells: [o.period.slice(0, 7), scopes.get(o.chain_id) ?? o.chain_id, catalog.get(o.product_id)!.description, catalog.get(o.product_id)!.product_code, o.metric_code, o.value_text, o.version_no, o.availability_source, (o.id ?? o.observation_id)!], detail: { available_at: o.available_at, source_batch_id: o.source_batch_id, source_reference: o.source_reference ?? null } })), hasMore: rows.length > (page + 1) * size };
    }
    let query = this.observations(context, fields, true, filters)
      .order("chain_id").order("product_id").order("period").order("metric_code").order(id).range(page * size, (page + 1) * size);
    if (filters.chainId) query = query.eq("chain_id", filters.chainId);
    if (filters.productId) query = query.eq("product_id", filters.productId);
    else if (filters.categoryId || filters.search || hasCommercialFilters(filters)) query = query.in("product_id", products.map(p => p.id));
    if (filters.periodRange[0]) query = query.gte("period", filters.periodRange[0] + "-01");
    if (filters.periodRange[1]) query = query.lte("period", filters.periodRange[1] + "-01");
    const result = await query.abortSignal(this.signal);
    this.ensureOpen();
    if (result.error || !Array.isArray(result.data)) throw new Error("report_read_failed");
    const catalog = new Map(products.map(p => [p.id, p])), scopes = new Map(chains.map(c => [c.id, c.name]));
    const rows = (result.data as unknown as AuditObservation[]).slice(0, size).map(o => {
      const product = catalog.get(o.product_id);
      if (!product || product.chain_id !== o.chain_id) throw new Error("report_scope_mismatch");
      const observationId = o.id ?? o.observation_id;
      if (!observationId) throw new Error("report_observation_lineage");
      return { id: observationId, cells: [o.period.slice(0, 7), scopes.get(o.chain_id) ?? o.chain_id, product.description, product.product_code, o.metric_code, o.value_text, o.version_no, o.availability_source, observationId], detail: { available_at: o.available_at, source_batch_id: o.source_batch_id, source_reference: o.source_reference ?? null } };
    });
    return { headers, rows, hasMore: result.data.length > size };
  }
}
