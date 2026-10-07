import type { SupabaseClient } from "@supabase/supabase-js";
export type PreviewHorizon = { horizon: number; target_period: string; statistical_value: number; ml_value: number | null; forecast_towell: number; p10: number | null; p50: number; p90: number | null; p95: number | null;
  statistical_model?: string; ml_model?: string | null; model_strategy?: string; classification?: string; forecast_status?: string; certification_status?: string; confidence?: string; band_basis?: string; band_observations?: number };
export type PreviewProduct = { product_id: string; product_code: string; description: string; category_id: string | null; forecast_status: string; history_months?: number; minimum_history_months?: number; horizons?: PreviewHorizon[]; statistical_model?: string | null; classification?: string | null };
export type RetrospectiveMetrics = { retrospective_wape?: number | null; retrospective_bias?: number | null; retrospective_mae?: number | null; retrospective_rmse?: number | null; retrospective_stability?: number | null };
export type PreviewCandidate = RetrospectiveMetrics & { family?: "statistical" | "ml"; product_id?: string; model?: string; strategy?: string;
  statistical_weight?: number;
  validation_wape?: number | null; validation_bias?: number | null; validation_stability?: number | null; observations?: number;
  validation_by_horizon?: { horizon: number; wape: number | null }[];
  available?: boolean; status?: string; reason?: string | null; retrospective_validation_wape?: number | null; retrospective_validation_bias?: number | null;
  retrospective_validation_by_horizon?: { horizon: number; wape: number | null }[]; by_horizon?: { horizon: number; wape: number | null; bias?: number | null; observations?: number }[];
  by_product?: PreviewCandidate[]; evaluation_mode?: string; classification?: string; origins?: number; training_samples?: number; validation_observations?: number; trainable?: boolean; validated?: boolean; selected?: boolean; operational_ready?: boolean };
export type PreviewAggregate = { level: "category" | "chain"; key: string; horizon: number; target_period: string; forecast_towell: number; statistical_value?: number; ml_value?: number | null };
export type PreviewScope = { chain_id: string; chain_name?: string; status: string; error_code?: string; issue_period?: string; latest_actual_period?: string; engine_version?: string; mode?: string; preview_id?: string;
  dataset_hash?: string;
  eligibility?: Record<string, number>; evaluation_mode?: string; statistical?: { status: string; models: Record<string, number>; candidates: PreviewCandidate[]; scope_candidates?: PreviewCandidate[]; selected_metrics?: PreviewCandidate; available_candidates?: string[]; retrospective_wape: number | null; retrospective_bias: number | null };
  ml?: { status: string; training_samples: number; training_products?: number; features?: string[]; leader: string | null; candidates: PreviewCandidate[]; trained_candidates?: PreviewCandidate[]; available_candidates?: string[]; retrospective_wape: number | null; retrospective_bias: number | null };
  selection?: { published_champion: { version: string } | null; preview_leader: PreviewCandidate | null; preview_challenger: PreviewCandidate | null; no_degradation: boolean | null; automatic_promotion: false }; products?: PreviewProduct[]; aggregates?: PreviewAggregate[]; certification_status?: string };
export type PreviewJob = { job_id: string; status: string; mode: string; engine_version: string; created_at?: number; cuts_status?: string; scopes: PreviewScope[] };
export type QualityGates = { chain_id: string; preview_id: string; dataset_hash: string; policy_version: string;
  data_quality: { status: string; history_months: number; missing_months: number; continuity_rate: number; products_total: number; products_with_history: number };
  forecast_quality: { status: string; stat_wape: number | null; ml_wape: number | null; candidate_wape: number | null; candidate_bias: number | null; no_degradation: boolean };
  service_level: { status: string; observed_fill_rate: number | null; target_fill_rate: number; gap_pp: number | null; service_simulation_status: string }; publication: string };
export type VintageSummary = { id: string; run_id: string; chain_id: string; objective: string; issue_period: string; cutoff_at: string; forecast_version: string; certification_status: string; frozen_at: string | null; created_at: string };
export type VintageDetail = { vintage: VintageSummary; run: { id: string; chain_id: string; actor_id: string | null; data_snapshot_hash: string; engine_version: string; git_sha: string; status: string };
  horizons: { id: string; product_id: string; horizon: number; target_period: string; statistical_value: number | null; ml_value: number | null; forecast_towell: number; p10: number | null; p50: number | null; p90: number | null; p95: number | null; model_strategy: string }[];
  aggregates: { id: string; level: string; horizon: number; target_period: string; forecast_towell: number }[];
  metrics: { id: string; metric: string; phase: string; value: number | null; period: string }[];
  models: { id: string; model_family: string; algorithm: string; validation_wape: number | null; certification_status: string }[];
  inputs: { id: string; monthly_observation_id: string | null; evidence_mode: string }[];
  gates: { id: string; gate_type: string; status: string; policy_version: string; observed: number | null; target: number | null }[] };
export type VintageCapabilities = { vintage_persistence: boolean; official_publication: boolean; champion_publication: boolean };
export interface PreviewClient {
  create(chainId: string | null, productId: string | null): Promise<PreviewJob>;
  status(jobId: string): Promise<PreviewJob>;
  result(jobId: string, productId?: string | null): Promise<PreviewJob>;
  latest(chainId: string | null, productId: string | null): Promise<PreviewJob | null>;
  qualityGates?(chainId: string): Promise<QualityGates>;
  vintages?(chainId: string): Promise<VintageSummary[]>;
  vintageDetail?(vintageId: string): Promise<VintageDetail>;
  vintageCapabilities?(): Promise<VintageCapabilities>;
  createCandidate?(chainId: string, previewId: string): Promise<string>;
  freezeVintage?(vintageId: string): Promise<string>;
  publishVintage?(vintageId: string, comment: string): Promise<string>;
  dispose(): void;
}
export class PreviewReadError extends Error { constructor(public code: string) { super(code); } }
const safeCodes = new Set(["AUTH_REQUIRED", "SCOPE_FORBIDDEN", "REQUEST_001", "PREVIEW_DISABLED", "DUPLICATE_OBSERVATION_CONFLICT", "NO_ELIGIBLE_PRODUCTS", "INSUFFICIENT_HISTORY", "DATA_READ_FAILED", "TEMPORAL_METADATA_MISSING", "DATA_LEAKAGE_DETECTED", "STATISTICAL_FAILED", "ML_FAILED", "ENSEMBLE_FAILED", "PREVIEW_NOT_FOUND", "PREVIEW_NOT_READY", "RATE_001", "VINTAGE_PERSISTENCE_DISABLED", "OFFICIAL_PUBLICATION_DISABLED", "CHAMPION_PUBLICATION_DISABLED", "REQUIRED_SECRET_MISSING", "VINTAGE_WRITE_FAILED", "DATA_QUALITY_BLOCKED", "LINEAGE_MISMATCH", "HASH_MISMATCH"]);
export class RailwayPreviewClient implements PreviewClient {
  private controller = new AbortController();
  constructor(private auth: SupabaseClient, private origin = process.env.NEXT_PUBLIC_FORECAST_API_URL ?? "") {}
  private async request(path: string, body?: unknown): Promise<PreviewJob> {
    let url: URL;
    try { url = new URL(this.origin); if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error(); }
    catch { throw new PreviewReadError("PREVIEW_CONFIGURATION_REQUIRED"); }
    if (this.controller.signal.aborted) throw new PreviewReadError("AUTH_REQUIRED");
    const { data } = await this.auth.auth.getSession();
    if (!data.session?.access_token) throw new PreviewReadError("AUTH_REQUIRED");
    const response = await fetch(url.origin + path, { method: body ? "POST" : "GET", credentials: "omit", cache: "no-store", signal: this.controller.signal,
      headers: { Authorization: `Bearer ${data.session.access_token}`, ...(body ? { "Content-Type": "application/json" } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}) });
    const result = await response.json() as { error_code?: string; job_id?: string; scopes?: unknown[] };
    if (!response.ok) throw new PreviewReadError(result.error_code && safeCodes.has(result.error_code) ? result.error_code : "DATA_READ_FAILED");
    if (typeof result.job_id !== "string" || !Array.isArray(result.scopes)) throw new PreviewReadError("DATA_READ_FAILED");
    return result as PreviewJob;
  }
  create(chainId: string | null, productId: string | null) { return this.request("/api/forecast/preview-runs", { chain_id: chainId, product_id: productId, objective: "Venta", issue_period: null, mode: "RETROSPECTIVE_TRAINING" }); }
  status(jobId: string) { return this.request(`/api/forecast/preview-runs/${encodeURIComponent(jobId)}`); }
  result(jobId: string, productId?: string | null) { return this.request(`/api/forecast/preview-runs/${encodeURIComponent(jobId)}/result${productId ? `?product_id=${encodeURIComponent(productId)}` : ""}`); }
  async latest(chainId: string | null, productId: string | null) {
    const params = new URLSearchParams(); if (chainId) params.set("chain_id", chainId); if (productId) params.set("product_id", productId);
    try { return await this.request(`/api/forecast/preview-latest?${params}`); }
    catch (error) { if (error instanceof PreviewReadError && error.code === "PREVIEW_NOT_FOUND") return null; throw error; }
  }
  async qualityGates(chainId: string): Promise<QualityGates> {
    let url: URL;
    try { url = new URL(this.origin); if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error(); }
    catch { throw new PreviewReadError("PREVIEW_CONFIGURATION_REQUIRED"); }
    if (this.controller.signal.aborted) throw new PreviewReadError("AUTH_REQUIRED");
    const { data } = await this.auth.auth.getSession();
    if (!data.session?.access_token) throw new PreviewReadError("AUTH_REQUIRED");
    const response = await fetch(url.origin + `/api/forecast/quality-gates?chain_id=${encodeURIComponent(chainId)}`, {
      method: "GET", credentials: "omit", cache: "no-store", signal: this.controller.signal,
      headers: { Authorization: `Bearer ${data.session.access_token}` },
    });
    const result = await response.json() as QualityGates & { error_code?: string };
    if (!response.ok) throw new PreviewReadError(result.error_code ?? "DATA_READ_FAILED");
    if (result.chain_id !== chainId || !result.dataset_hash || !result.data_quality || !result.forecast_quality || !result.service_level) throw new PreviewReadError("DATA_READ_FAILED");
    return result;
  }
  async vintages(chainId: string): Promise<VintageSummary[]> {
    let url: URL;
    try { url = new URL(this.origin); if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error(); }
    catch { throw new PreviewReadError("PREVIEW_CONFIGURATION_REQUIRED"); }
    if (this.controller.signal.aborted) throw new PreviewReadError("AUTH_REQUIRED");
    const { data } = await this.auth.auth.getSession();
    if (!data.session?.access_token) throw new PreviewReadError("AUTH_REQUIRED");
    const response = await fetch(url.origin + `/api/forecast/vintages?chain_id=${encodeURIComponent(chainId)}`, {
      method: "GET", credentials: "omit", cache: "no-store", signal: this.controller.signal,
      headers: { Authorization: `Bearer ${data.session.access_token}` },
    });
    const result = await response.json() as { error_code?: string; chain_id?: string; vintages?: VintageSummary[] };
    if (!response.ok) throw new PreviewReadError(result.error_code ?? "DATA_READ_FAILED");
    if (result.chain_id !== chainId || !Array.isArray(result.vintages) || result.vintages.some(row => row.chain_id !== chainId)) throw new PreviewReadError("DATA_READ_FAILED");
    return result.vintages;
  }
  async vintageDetail(vintageId: string): Promise<VintageDetail> {
    const url = new URL(this.origin);
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new PreviewReadError("PREVIEW_CONFIGURATION_REQUIRED");
    const { data } = await this.auth.auth.getSession();
    if (!data.session?.access_token) throw new PreviewReadError("AUTH_REQUIRED");
    const response = await fetch(url.origin + `/api/forecast/vintages/${encodeURIComponent(vintageId)}`, {
      method: "GET", credentials: "omit", cache: "no-store", signal: this.controller.signal,
      headers: { Authorization: `Bearer ${data.session.access_token}` },
    });
    const result = await response.json() as VintageDetail & { error_code?: string };
    if (!response.ok) throw new PreviewReadError(result.error_code && safeCodes.has(result.error_code) ? result.error_code : "DATA_READ_FAILED");
    if (result.vintage?.id !== vintageId || result.run?.chain_id !== result.vintage.chain_id ||
        !Array.isArray(result.horizons) || !Array.isArray(result.gates) || !Array.isArray(result.models)) throw new PreviewReadError("DATA_READ_FAILED");
    return result;
  }
  async vintageCapabilities(): Promise<VintageCapabilities> {
    const url = new URL(this.origin);
    const response = await fetch(url.origin + "/api/ready", { method: "GET", credentials: "omit", cache: "no-store", signal: this.controller.signal });
    const result = await response.json() as Partial<VintageCapabilities>;
    return { vintage_persistence: response.ok && result.vintage_persistence === true,
      official_publication: response.ok && result.official_publication === true,
      champion_publication: response.ok && result.champion_publication === true };
  }
  private async writeVintage(path: string, body: unknown): Promise<string> {
    const url = new URL(this.origin);
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new PreviewReadError("PREVIEW_CONFIGURATION_REQUIRED");
    const { data } = await this.auth.auth.getSession();
    if (!data.session?.access_token) throw new PreviewReadError("AUTH_REQUIRED");
    const response = await fetch(url.origin + path, { method: "POST", credentials: "omit", cache: "no-store",
      signal: this.controller.signal, headers: { Authorization: `Bearer ${data.session.access_token}`,
        "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify(body) });
    const result = await response.json() as { error_code?: string; vintage_id?: string };
    if (!response.ok) throw new PreviewReadError(result.error_code && safeCodes.has(result.error_code) ? result.error_code : "VINTAGE_WRITE_FAILED");
    if (!result.vintage_id) throw new PreviewReadError("VINTAGE_WRITE_FAILED");
    return result.vintage_id;
  }
  createCandidate(chainId: string, previewId: string) { return this.writeVintage("/api/forecast/vintages/candidates", { chain_id: chainId, preview_id: previewId }); }
  freezeVintage(vintageId: string) { return this.writeVintage(`/api/forecast/vintages/${encodeURIComponent(vintageId)}/freeze`, {}); }
  publishVintage(vintageId: string, comment: string) { return this.writeVintage(`/api/forecast/vintages/${encodeURIComponent(vintageId)}/publish`, { confirm: true, comment }); }
  dispose() { this.controller.abort(); }
}
