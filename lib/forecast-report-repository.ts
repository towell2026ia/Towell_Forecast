import type { SupabaseClient } from "@supabase/supabase-js";
import type { Filters, ForecastReadRepository, Product } from "./supabase/types";
import { emptyFilters } from "./supabase/types";
import type { CustomerForecast, DatabasePage, DatabaseSection, ReportCalculation, ReportDecision, ReportHorizon, ReportLive, ReportSnapshot } from "./forecast-reporting";
import { addReportMonth, amount } from "./forecast-reporting";
import { attachCommercial, filteredProducts, hasCommercialFilters } from "./commercial-filters";
import { combineObservations } from "./forecast-data";

const calculationFields = "id,chain_id,product_id,objective,calculation_no,calculation_code,issue_period,cutoff_at,status,created_at,created_by,preview_id,data_snapshot_hash,content_hash,input_snapshot,candidate_metrics,research_snapshot,suggested_reference,scope_leader,evidence_mode,engine_version,git_sha,recalculation_reason";
const horizonFields = "id,calculation_id,chain_id,product_id,horizon,target_period,statistical_value,ml_value,ensemble_value,ensemble_ml_component,statistical_model,ml_model,ensemble_ml_model,statistical_weight,ml_weight,p10,p50,p90,p95,band_basis,band_observations,band_status";
const decisionFields = "id,calculation_id,chain_id,product_id,system_suggestion,selected_candidate,selected_curve,decision_reason,comment,selected_by,selected_at,supersedes_selection_id";
const liveFields = "id,calculation_id,chain_id,product_id,horizon,target_period,candidate,forecast_value,actual_sale,sale_observation_id,sale_version_no,absolute_error,signed_error,evaluated_at";
export class SupabaseReportReader {
  constructor(private client: SupabaseClient, private repository: ForecastReadRepository, private signal: AbortSignal) {}
  private ensureOpen() { if (this.signal.aborted) throw new Error("session_disposed"); }
  private async read<T>(table: string, fields: string, filters: Filters, products?: Product[]) {
    this.ensureOpen();
    const output: T[] = [];
    if (products && !products.length) return output;
    const chunks = products ? Array.from({ length: Math.ceil(products.length / 80) }, (_, i) => products.slice(i * 80, i * 80 + 80).map(p => p.id)) : [null];
    for (const ids of chunks) {
      for (let offset = 0; offset < 100000; offset += 500) {
        let query = this.client.from(table).select(fields).order(table === "portal_monthly_observations_current" ? "observation_id" : "id").range(offset, offset + 499);
        if (filters.chainId) query = query.eq("chain_id", filters.chainId);
        if (ids) query = query.in("product_id", ids);
        if (table === "portal_monthly_observations_current" || table === "monthly_observations") {
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
  async snapshot(filters: Filters): Promise<ReportSnapshot> {
    const [catalog, allChains, categories] = await Promise.all([filteredProducts(this.repository, filters), this.repository.getVisibleChains(), this.repository.getCategories(filters.chainId)]);
    const products = this.repository.getCommercialCatalog && !hasCommercialFilters(filters) ? attachCommercial(catalog, await this.repository.getCommercialCatalog()) : catalog;
    const chains = allChains;
    const chainIds = new Set(chains.map(c => c.id));
    if (products.some(p => !chainIds.has(p.chain_id))) throw new Error("report_catalog_lineage");
    const capabilities = this.repository.previews?.vintageCapabilities ? await this.repository.previews.vintageCapabilities() : { vintage_persistence: false, official_publication: false, champion_publication: false };
    let calculations: ReportCalculation[] = [], horizons: ReportHorizon[] = [], decisions: ReportDecision[] = [], live: ReportLive[] = [];
    if (capabilities.forecast_calculation_history) {
      calculations = await this.read("forecast_calculations", calculationFields, filters, products);
      horizons = await this.read("forecast_calculation_horizons", horizonFields, filters, products);
      decisions = await this.read("forecast_selection_events", decisionFields, filters, products);
      live = await this.read("forecast_live_evaluations", liveFields, filters, products);
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
    const versions = await this.read<{ id: string; chain_id: string; issue_period: string; version_no: number; status: string }>("customer_forecast_versions", "id,chain_id,issue_period,version_no,status", filters);
    const customerRows = await this.read<{ id: string; chain_id: string; product_id: string; forecast_version_id: string; target_period: string; value: number }>("customer_forecast_rows", "id,chain_id,product_id,forecast_version_id,target_period,value", filters, products);
    const customerVersions = new Map(versions.filter(v => ["VALIDATED", "FROZEN"].includes(v.status)).map(v => [v.id, v]));
    const customers: CustomerForecast[] = [];
    for (const row of customerRows) {
      const version = customerVersions.get(row.forecast_version_id);
      if (!version) continue;
      if (row.chain_id !== version.chain_id || amount(row.value) === null) throw new Error("customer_report_lineage");
      customers.push({ ...row, issue_period: version.issue_period, version_no: version.version_no });
    }
    const provisionals = this.repository.previews?.provisionalForecasts ? await this.repository.previews.provisionalForecasts(filters.chainId, filters.productId) : [];
    const allowed = new Map(products.map(p => [p.id, p.chain_id]));
    for (const row of [...calculations, ...horizons, ...decisions, ...live, ...customers]) {
      if (allowed.get(row.product_id) !== row.chain_id) throw new Error("report_scope_mismatch");
    }
    return { products, chains, categories, calculations, horizons, decisions, live, customers,
      provisionals: provisionals.filter(p => allowed.get(p.product_id) === p.chain_id), capabilities };
  }
  async history(filters: Filters) {
    const products = await filteredProducts(this.repository, filters);
    const observations = await this.read<Parameters<typeof combineObservations>[0][number]>("portal_monthly_observations_current", "observation_id,chain_id,chain_name,product_id,product_code,variant_code,product_description,category_id,period,metric_code,value,availability_source,version_no,available_at", filters, products);
    const catalog = new Map(products.map(p => [p.id, p]));
    if (observations.some(o => catalog.get(o.product_id)?.chain_id !== o.chain_id)) throw new Error("report_scope_mismatch");
    return combineObservations(observations, catalog, observations.length);
  }
  async periods(chainId: string | null) {
    const historical = await this.repository.getPeriods(chainId);
    const caps = this.repository.previews?.vintageCapabilities ? await this.repository.previews.vintageCapabilities() : null;
    if (!caps?.forecast_calculation_history) return historical;
    const products = await this.repository.getProducts(chainId);
    const horizons = await this.read<ReportHorizon>("forecast_calculation_horizons", "id,chain_id,product_id,target_period", { ...emptyFilters, chainId }, products);
    const provisionals = await this.repository.previews?.provisionalForecasts?.(chainId, null) ?? [];
    return [...new Set([...historical, ...horizons.map(h => h.target_period.slice(0, 7)), ...provisionals.flatMap(p => p.horizons.map(h => h.target_period.slice(0, 7)))])].sort();
  }
  async databasePage(section: DatabaseSection, filters: Filters, page: number, size: 50 | 100 | 250): Promise<DatabasePage> {
    if (!Number.isInteger(page) || page < 0 || ![50, 100, 250].includes(size)) throw new Error("invalid_report_page");
    if (section === "history" || section === "observations") return this.observationPage(section, filters, page, size);
    const snapshot = await this.snapshot(filters);
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
      const research = filters.productId || filters.categoryId || filters.search ? [] : await this.read<{ id: string; chain_id: string; provider: string; cutoff_at: string; status: string; created_at: string; summary_json: Record<string, unknown> }>("research_snapshots", "id,chain_id,provider,cutoff_at,status,created_at,summary_json", filters);
      records = research.map(r => ({ id: r.id, cells: [chain.get(r.chain_id) ?? r.chain_id, r.provider, r.cutoff_at, r.status, r.created_at, r.id], detail: r.summary_json }));
      records.push(...snapshot.calculations.filter(c => c.research_snapshot).map(c => ({ id: c.id, cells: [chain.get(c.chain_id) ?? c.chain_id, "Snapshot del cálculo", c.issue_period, "Conservado", c.created_at, c.id], detail: c.research_snapshot! })));
    } else if (section === "performance") {
      headers = ["Tipo", "Cadena", "Producto", "Cálculo", "Periodo", "H", "Candidato", "Error absoluto", "ID"];
      records = snapshot.live.map(v => ({ id: v.id, cells: ["LIVE almacenado", chain.get(v.chain_id) ?? v.chain_id, product.get(v.product_id)?.description ?? v.product_id, calc.get(v.calculation_id)?.calculation_code ?? null, v.target_period.slice(0, 7), v.horizon, v.candidate, v.absolute_error, v.id], detail: { sale_observation_id: v.sale_observation_id, sale_version_no: v.sale_version_no, actual_sale: v.actual_sale, forecast_value: v.forecast_value, evaluated_at: v.evaluated_at } }));
      for (const c of snapshot.calculations) for (const [family, metric] of Object.entries(c.candidate_metrics)) if (metric) records.push({ id: `${c.id}/${family}`, cells: ["Validación retrospectiva", chain.get(c.chain_id) ?? c.chain_id, product.get(c.product_id)?.description ?? c.product_id, c.calculation_code, c.issue_period.slice(0, 7), null, family, null, c.id], detail: { wape_retrospective: metric.wape, bias_retrospective: metric.bias, observations: metric.observations, windows: metric.windows } });
      if (snapshot.capabilities.forecast_calculation_history) {
        const learning = await this.read<{ id: string; calculation_id: string; chain_id: string; product_id: string; target_period: string; horizon: number; metrics_json: Record<string, unknown>; signals_json: unknown[] }>("forecast_learning_events", "id,calculation_id,chain_id,product_id,target_period,horizon,metrics_json,signals_json", filters, snapshot.products);
        records.push(...learning.map(v => ({ id: v.id, cells: ["Aprendizaje almacenado", chain.get(v.chain_id) ?? v.chain_id, product.get(v.product_id)?.description ?? v.product_id, calc.get(v.calculation_id)?.calculation_code ?? null, v.target_period.slice(0, 7), v.horizon, null, null, v.id], detail: { metrics: v.metrics_json, signals: v.signals_json } })));
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
  private async observationPage(section: "history" | "observations", filters: Filters, page: number, size: number): Promise<DatabasePage> {
    this.ensureOpen();
    const products = await filteredProducts(this.repository, filters);
    const chains = await this.repository.getVisibleChains();
    const headers = ["Periodo", "Cadena", "Producto", "Código", "Métrica", "Valor", "Versión", "Disponibilidad", "Observation ID"];
    if (!products.length) return { headers, rows: [], hasMore: false };
    const raw = section === "observations", id = raw ? "id" : "observation_id";
    if (hasCommercialFilters(filters)) {
      const fields = `${id},chain_id,product_id,period,metric_code,value,version_no,availability_source,available_at,source_batch_id`;
      const rows = await this.read<{ id?: string; observation_id?: string; chain_id: string; product_id: string; period: string; metric_code: string; value: number; version_no: number; availability_source: string; available_at: string | null; source_batch_id: string }>(raw ? "monthly_observations" : "portal_monthly_observations_current", fields, filters, products);
      rows.sort((a, b) => a.chain_id.localeCompare(b.chain_id) || a.product_id.localeCompare(b.product_id) || a.period.localeCompare(b.period) || a.metric_code.localeCompare(b.metric_code) || (a.id ?? a.observation_id ?? "").localeCompare(b.id ?? b.observation_id ?? ""));
      const catalog = new Map(products.map(p => [p.id, p])), scopes = new Map(chains.map(c => [c.id, c.name]));
      if (rows.some(r => catalog.get(r.product_id)?.chain_id !== r.chain_id)) throw new Error("report_scope_mismatch");
      return { headers, rows: rows.slice(page * size, (page + 1) * size).map(o => ({ id: (o.id ?? o.observation_id)!, cells: [o.period.slice(0, 7), scopes.get(o.chain_id) ?? o.chain_id, catalog.get(o.product_id)!.description, catalog.get(o.product_id)!.product_code, o.metric_code, o.value, o.version_no, o.availability_source, (o.id ?? o.observation_id)!], detail: { available_at: o.available_at, source_batch_id: o.source_batch_id } })), hasMore: rows.length > (page + 1) * size };
    }
    let query = this.client.from(raw ? "monthly_observations" : "portal_monthly_observations_current")
      .select(`${id},chain_id,product_id,period,metric_code,value,version_no,availability_source,available_at,source_batch_id`)
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
    const rows = result.data.slice(0, size).map(o => {
      const product = catalog.get(o.product_id);
      if (!product || product.chain_id !== o.chain_id) throw new Error("report_scope_mismatch");
      const observationId = "id" in o ? o.id : o.observation_id;
      return { id: observationId, cells: [o.period.slice(0, 7), scopes.get(o.chain_id) ?? o.chain_id, product.description, product.product_code, o.metric_code, o.value, o.version_no, o.availability_source, observationId], detail: { available_at: o.available_at, source_batch_id: o.source_batch_id } };
    });
    return { headers, rows, hasMore: result.data.length > size };
  }
}
