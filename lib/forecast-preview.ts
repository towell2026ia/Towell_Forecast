import type { SupabaseClient } from "@supabase/supabase-js";
export type PreviewHorizon = { horizon: number; target_period: string; statistical_value: number; ml_value: number | null; forecast_towell: number; p10: number | null; p50: number; p90: number | null; p95: number | null };
export type PreviewProduct = { product_id: string; product_code: string; description: string; category_id: string | null; forecast_status: string; horizons?: PreviewHorizon[] };
export type PreviewCandidate = { model?: string; strategy?: string; available?: boolean; status?: string; reason?: string; retrospective_validation_wape?: number | null; retrospective_validation_bias?: number | null };
export type PreviewAggregate = { level: "category" | "chain"; key: string; horizon: number; target_period: string; forecast_towell: number };
export type PreviewScope = { chain_id: string; chain_name?: string; status: string; error_code?: string; issue_period?: string; latest_actual_period?: string; engine_version?: string; mode?: string;
  eligibility?: Record<string, number>; statistical?: { status: string; models: Record<string, number>; candidates: PreviewCandidate[]; available_candidates?: string[]; retrospective_wape: number | null; retrospective_bias: number | null };
  ml?: { status: string; training_samples: number; leader: string | null; candidates: PreviewCandidate[]; trained_candidates?: PreviewCandidate[]; available_candidates?: string[]; retrospective_wape: number | null; retrospective_bias: number | null };
  selection?: { published_champion: { version: string } | null; preview_leader: PreviewCandidate | null; preview_challenger: PreviewCandidate | null; no_degradation: boolean | null; automatic_promotion: false }; products?: PreviewProduct[]; aggregates?: PreviewAggregate[]; certification_status?: string };
export type PreviewJob = { job_id: string; status: string; mode: string; engine_version: string; created_at?: number; cuts_status?: string; scopes: PreviewScope[] };
export interface PreviewClient {
  create(chainId: string | null, productId: string | null): Promise<PreviewJob>;
  status(jobId: string): Promise<PreviewJob>;
  result(jobId: string, productId?: string | null): Promise<PreviewJob>;
  latest(chainId: string | null, productId: string | null): Promise<PreviewJob | null>;
  dispose(): void;
}
export class PreviewReadError extends Error { constructor(public code: string) { super(code); } }
const safeCodes = new Set(["AUTH_REQUIRED", "SCOPE_FORBIDDEN", "NO_ELIGIBLE_PRODUCTS", "INSUFFICIENT_HISTORY", "DATA_READ_FAILED", "TEMPORAL_METADATA_MISSING", "DATA_LEAKAGE_DETECTED", "STATISTICAL_FAILED", "ML_FAILED", "ENSEMBLE_FAILED", "PREVIEW_NOT_FOUND", "PREVIEW_NOT_READY", "RATE_001"]);
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
  dispose() { this.controller.abort(); }
}
