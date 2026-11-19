import React from "react";
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { SupabaseClient } from "@supabase/supabase-js";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { ForecastEnginesView } from "../../app/operational-engines-view";
import { RailwayPreviewClient, type PreviewClient, type PreviewJob, type ProductCandidate } from "../../lib/forecast-preview";
import { addMonth, exactSelectionScope, scopeHorizons, storedCommercialScope } from "../../lib/forecast-chart-data";
import type { ReportSnapshot } from "../../lib/forecast-reporting";
import { forecastHistoryAvailable, forecastObjective, forecastSourceAvailable, forecastSourceState, forecastTrainingCutoff, unifiedMasterSha } from "../../lib/official-sales-source";
import { emptyFilters, type Filters, type ForecastReadRepository, type OperationalReadContext } from "../../lib/supabase/types";

vi.mock("../../components/forecast/forecast-trader-chart", () => ({ ForecastTraderChart: ({ points }: { points: unknown }) => <output data-testid="exact-points">{JSON.stringify(points)}</output> }));
const chains = ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"];
const ids = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"];
const closed: OperationalReadContext = { source_hash: "a".repeat(64), authorized_source_hash: "a".repeat(64), generation: 3,
  first_period: "2024-01", last_period: "2026-10", forecast_contract: "pedido-towell-v1", objective: "Pedido", target_metric: "ORDER",
  target_sheet: "Pedidos", target_file_sha256: "b".repeat(64), training_cutoff: "2026-10", forecast_available: true,
  target_source_available: true, forecast_block_code: null, forecast_unavailable_reason: null, unit: "PIEZAS", inventory_source: "ESTADISTICA" };
const unclosed: OperationalReadContext = { ...closed, training_cutoff: null, forecast_available: false,
  forecast_block_code: "ORDER_CLOSURE_UNVERIFIED", forecast_unavailable_reason: "Sin cálculo: cierre de Pedido no acreditado" };
const filters: Filters = { ...emptyFilters, purchaseType: "RS", commercialModels: ["DESIGN"] };
function snapshot(source = closed): ReportSnapshot {
  return { products: ids.map((id, i) => ({ id, chain_id: chains[i], category_id: null, product_code: `P${i}`, description: `Variant ${i}`,
    variant_code: null, identifiers: [], commercial: { chain_id: chains[i], product_id: id, purchase_type: "RS", model: "DESIGN", color: `C${i}`,
      size: "MB", product_type: "TOALLA", status: "APPROVED", source: "SYNTHETIC", reasons: [] } })),
    chains: chains.map((id, i) => ({ id, code: String(i), name: `Chain ${i}`, status: "ACTIVE", has_history: true, parentId: null, scopeType: "COMMERCIAL_UNIT" })),
    categories: [], calculations: [], horizons: [], decisions: [], live: [], provisionals: [], customers: [], sourceContext: source,
    capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false } };
}
function mlCandidate(productId: string, value: number, model = "Individual ML", issue = "2026-10"): ProductCandidate {
  return { product_id: productId, family: "ml", model, strategy: "synthetic", wape: 10, bias: 0, mae: 1, rmse: 1, observations: 20,
    windows: 6, evaluation_signature: "synthetic", evidence_mode: "RETROSPECTIVE_TRAINING", by_horizon: [],
    horizons: Array.from({ length: 12 }, (_, i) => ({ horizon: i + 1, target_period: addMonth(issue, i + 1), value })) };
}
function job(source = closed): PreviewJob {
  const issue = source.training_cutoff ?? "2026-10";
  return { job_id: "synthetic-exact-job", status: "READY_PREVIEW", mode: "RETROSPECTIVE_TRAINING", engine_version: "synthetic-operational-preview-4-e4-snapshot",
    objective: "Pedido", chain_ids: chains, requested_product_ids: ids, scopes: chains.map((chain_id, index) => ({ chain_id,
      chain_name: `Chain ${index}`, objective: "Pedido", status: "PREVIEW", issue_period: issue, source_context: source,
      engine_version: "synthetic-operational-preview-4-e4-snapshot", evaluation_mode: "RETROSPECTIVE_EVALUATION",
      selection: { published_champion: null, preview_leader: null, preview_challenger: null, no_degradation: null, automatic_promotion: false,
        product_candidates: [mlCandidate(ids[index], index ? 1.42 : 1.41, "Individual ML", issue)] },
      products: [{ product_id: ids[index], product_code: `P${index}`, description: `Variant ${index}`, category_id: null, forecast_status: "ACTIVE",
        horizons: Array.from({ length: 12 }, (_, i) => ({ horizon: i + 1, target_period: addMonth(issue, i + 1), statistical_value: index ? 1.02 : 1.01,
          ml_value: index ? 1.42 : 1.41, forecast_towell: index ? 1.22 : 1.21, p10: null, p50: index ? 1.22 : 1.21, p90: null, p95: null, statistical_model: "SBA" })) }] })) };
}
function repository(source = closed) {
  const result = job(source);
  const previews = { readOnly: true, latest: vi.fn(async () => null), create: vi.fn(), createSelection: vi.fn(async () => result), result: vi.fn(async () => result),
    vintageCapabilities: vi.fn(async () => ({ vintage_persistence: false, official_publication: false, champion_publication: false, forecast_calculation_history: true, forecast_selection: false, local_bounded_preview: true })) } as unknown as PreviewClient;
  const repo = { previews, openOperationalRead: vi.fn(async () => source), getReportSnapshot: vi.fn(async () => snapshot(source)),
    getForecastHistory: vi.fn(async () => [{ period: "2026-10", sale: null, order: 8, delivery: null }]), getCustomerForecast: vi.fn(async () => []), getCategories: vi.fn(async () => []) } as unknown as ForecastReadRepository;
  return repo;
}
function panel(repo: ForecastReadRepository, selection = filters) {
  return <ForecastFiltersContext.Provider value={{ repository: repo, filters: selection, setFilters: vi.fn(), profile: { id: "synthetic", full_name: "Synthetic", global_role: "ADMIN", status: "ACTIVE" } }}><ForecastEnginesView scope="Authorized scopes"/></ForecastFiltersContext.Provider>;
}

describe("Pedido objective and bounded model selection", () => {
  it("preserves the last observed order month when projecting an exact preview", () => {
    const source: OperationalReadContext = { ...closed, generation: 6, forecast_contract: "pedido-unificado-v1",
      target_sheet: "Base_2024_2026", target_file_sha256: unifiedMasterSha, last_period: "2026-12",
      training_cutoff: "2026-12", observed_cutoff: "2026-09", planning_basis: "OBSERVED_AND_COMMERCIAL_CLOSE",
      scenario_hash: "b".repeat(64), planning_start: "2027-01", planning_end: "2027-12" };
    const result = exactSelectionScope(job(source), snapshot(source), filters);
    expect(result.issue_period).toBe(source.training_cutoff);
    expect(result.latest_actual_period).toBe(source.observed_cutoff);
    expect(scopeHorizons(result, filters)[0].target_period).toBe("2027-01");
  });

  it("projects only seven accredited individual ML alternatives from ten raw global components and preserves Ensemble", async () => {
    const selectedIds = Array.from({ length: 10 }, (_, i) => `33333333-3333-4333-8333-${String(i).padStart(12, "0")}`);
    const data = snapshot();
    data.products = selectedIds.map((id, i) => ({ ...data.products[0], id, description: `Synthetic variant ${i}`,
      commercial: { ...data.products[0].commercial!, product_id: id, color: `C${i}` } }));
    const raw: PreviewJob = { ...job(), chain_ids: [chains[0]], requested_product_ids: selectedIds, scopes: [{ ...job().scopes[0],
      ml: { status: "COMPLETED", training_products: 10, training_samples: 100, leader: "Scope ML", candidates: [], retrospective_wape: null, retrospective_bias: null },
      products: selectedIds.map(id => ({ ...job().scopes[0].products![0], product_id: id,
        horizons: job().scopes[0].products![0].horizons!.map(h => ({ ...h, ml_value: 100, ml_model: "Scope ML", forecast_towell: 75,
          statistical_value: 50, p50: 75, ensemble_ml_model: "Scope ML", ensemble_ml_component: 100, statistical_weight: 0.5, ml_weight: 0.5 })) })),
      selection: { published_champion: null, preview_leader: null, preview_challenger: null, no_degradation: null, automatic_promotion: false,
        product_candidates: selectedIds.slice(0, 7).map((id, i) => mlCandidate(id, (i + 1) * 10)) } }] };
    const before = JSON.stringify(raw), projected = exactSelectionScope(raw, data, filters), horizons = scopeHorizons(projected, filters);
    expect(projected.commercial_projection).toMatchObject({ products: 10, statistical: 10, ml: 7, ensemble: 10 });
    expect(projected.ml?.training_products).toBe(10);
    expect(horizons[0]).toMatchObject({ statistical_value: 500, ml_value: 280, forecast_towell: 750 });
    expect(projected.products?.slice(7).every(product => product.horizons?.every(h => h.ml_value === null && h.ml_model === null))).toBe(true);
    for (const product of projected.products ?? []) for (const horizon of product.horizons ?? [])
      expect(horizon).toMatchObject({ forecast_towell: 75, ensemble_ml_component: 100, ensemble_ml_model: "Scope ML", statistical_weight: 0.5, ml_weight: 0.5 });
    expect(JSON.stringify(raw)).toBe(before);
    const legacySource = { source_hash: closed.source_hash, generation: 2, first_period: closed.first_period, last_period: closed.last_period };
    const legacy = { ...raw, objective: "Venta" as const, scopes: raw.scopes.map(scope => ({ ...scope, objective: "Venta" as const, source_context: legacySource })) };
    expect(exactSelectionScope(legacy, { ...data, sourceContext: legacySource }, filters).commercial_projection?.ml).toBe(10);
    for (const candidate of [{ ...mlCandidate(selectedIds[0], 1), evidence_mode: "POINT_IN_TIME" },
      { ...mlCandidate(selectedIds[0], 1), horizons: mlCandidate(selectedIds[0], 1).horizons.slice(1) }]) {
      const corrupt = { ...raw, scopes: [{ ...raw.scopes[0], selection: { ...raw.scopes[0].selection!, product_candidates: [candidate, ...raw.scopes[0].selection!.product_candidates!.slice(1)] } }] };
      const isolated = exactSelectionScope(corrupt, data, filters);
      expect(isolated.commercial_projection).toMatchObject({ statistical: 10, ml: 6, ensemble: 10 });
      expect(isolated.products?.[0].ml_unavailable_reason).toBe("NO_VALIDATED_PRODUCT_ML_CANDIDATE");
      expect(scopeHorizons(isolated, filters)[0]).toMatchObject({ statistical_value: 500, ml_value: 270, forecast_towell: 750 });
    }
    const repo = repository();
    vi.mocked(repo.getReportSnapshot!).mockResolvedValue(data);
    vi.mocked(repo.previews!.createSelection!).mockResolvedValue(raw); vi.mocked(repo.previews!.result).mockResolvedValue(raw);
    render(panel(repo));
    await waitFor(() => expect((screen.getByRole("button", { name: "Calcular vista previa" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Calcular vista previa" }));
    const state = await screen.findByText(/Vista previa de selección exacta · 10 productos/);
    expect(state.textContent).toContain("ML 7/10");
    expect(state.textContent).toContain("10 productos de entrenamiento acreditados");
    expect(state.textContent).toContain("no sustituye los cálculos guardados en Períodos");
    expect(screen.getByTestId("exact-points").textContent).toContain('"ml":280');
  });
  it("keeps unclosed Pedido history readable but defines no forecast cut or horizons", async () => {
    expect(forecastSourceState(unclosed)).toBe("UNAVAILABLE"); expect(forecastHistoryAvailable(unclosed)).toBe(true);
    expect(forecastSourceAvailable(unclosed)).toBe(false); expect(forecastObjective(unclosed)).toBe("Pedido"); expect(forecastTrainingCutoff(unclosed)).toBeNull();
    const scope = storedCommercialScope(snapshot(unclosed), filters);
    expect(scope.issue_period).toBe(""); expect(scopeHorizons(scope, filters)).toEqual([]);
    const repo = repository(unclosed); render(panel(repo));
    await screen.findByText(/El cierre de Pedidos aún no está acreditado/);
    expect((screen.getByRole("button", { name: "Calcular vista previa" }) as HTMLButtonElement).disabled).toBe(true);
    expect(repo.previews!.createSelection).not.toHaveBeenCalled(); expect(repo.previews!.create).not.toHaveBeenCalled();
    expect(screen.queryByText("2026-11")).toBeNull();
  });
  it("reads all eleven selected variants' Pedido history without accrediting a forecast closure", async () => {
    const repo = repository(unclosed), selectedFilters = { ...filters, chainId: chains[0] };
    const data = snapshot(unclosed);
    data.products = Array.from({ length: 11 }, (_, i) => ({ ...data.products[0], id: `33333333-3333-4333-8333-${String(i).padStart(12, "0")}`,
      description: `Synthetic colour ${i}`, commercial: { ...data.products[0].commercial!, product_id: `33333333-3333-4333-8333-${String(i).padStart(12, "0")}`, color: `S${i}` } }));
    vi.mocked(repo.getReportSnapshot!).mockResolvedValue(data);
    vi.mocked(repo.getForecastHistory!).mockResolvedValue([{ period: "2026-10", sale: 17, order: 31.25, delivery: 29 }]);
    render(panel(repo, selectedFilters));
    await screen.findByText(/Consulta almacenada · 11 productos/);
    await waitFor(() => expect(repo.getForecastHistory).toHaveBeenLastCalledWith(selectedFilters, unclosed));
    await waitFor(() => expect(screen.getByTestId("exact-points").textContent).toContain('"order":31.25'));
    expect(screen.getByTestId("exact-points").textContent).toContain('"sale":17');
    expect(screen.getByTestId("exact-points").textContent).toContain('"towell":null');
    expect(screen.getByText(/Cierre acreditado: Sin acreditar/)).toBeTruthy();
    expect(screen.getByText(/No se han definido H1–H3/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Calcular vista previa" }) as HTMLButtonElement).disabled).toBe(true);
    expect(repo.previews!.createSelection).not.toHaveBeenCalled(); expect(repo.previews!.create).not.toHaveBeenCalled();
  });
  it("sums only the exact authorized model variants across chains by month", async () => {
    const selected = exactSelectionScope(job(), snapshot(), filters), rows = scopeHorizons(selected, filters);
    expect(selected.commercial_projection).toMatchObject({ chain_ids: chains, product_ids: ids, products: 2, source: "EXACT_SELECTION" });
    expect(rows).toHaveLength(12); expect(rows[0]).toMatchObject({ statistical_value: 2.03, ml_value: 2.83, forecast_towell: 2.43 });
    expect(selected.statistical?.retrospective_wape).toBeNull();
    for (const invalid of [{ ...job(), objective: "Venta" as const }, { ...job(), requested_product_ids: [...ids, "foreign"] },
      { ...job(), scopes: job().scopes.map((s, i) => i ? { ...s, issue_period: "2026-09" } : s) },
      { ...job(), scopes: job().scopes.map((s, i) => i ? { ...s, source_context: { ...closed, generation: 2 } } : s) },
      { ...job(), scopes: job().scopes.map(s => ({ ...s, issue_period: "2026-00" })) },
      { ...job(), scopes: job().scopes.map(s => ({ ...s, products: s.products?.map(p => ({ ...p, horizons: p.horizons?.map(h => ({ ...h, statistical_value: -1 })) })) })) }])
      expect(() => exactSelectionScope(invalid, snapshot(), filters)).toThrow();
    const repo = repository(); render(panel(repo));
    await waitFor(() => expect((screen.getByRole("button", { name: "Calcular vista previa" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Calcular vista previa" }));
    await waitFor(() => expect(repo.previews!.createSelection).toHaveBeenCalledWith({ objective: "Pedido", chain_ids: chains, product_ids: ids, issue_period: null, source_context: closed }));
    await waitFor(() => expect(screen.getByTestId("exact-points").textContent).toContain('"statistical":2.03'));
    expect(repo.previews!.create).not.toHaveBeenCalled();
  });
  it("discards a delayed forecast after the selected model changes", async () => {
    const repo = repository(); let resolve!: (value: PreviewJob) => void;
    vi.mocked(repo.previews!.createSelection!).mockImplementation(async () => new Promise(r => { resolve = r; }));
    const view = render(panel(repo));
    await waitFor(() => expect((screen.getByRole("button", { name: "Calcular vista previa" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Calcular vista previa" }));
    vi.mocked(repo.getReportSnapshot!).mockResolvedValue({ ...snapshot(), products: [] });
    view.rerender(panel(repo, { ...filters, commercialModels: ["OTHER DESIGN"] }));
    await act(async () => resolve(job()));
    expect(screen.queryByTestId("exact-points")?.textContent ?? "").not.toContain('"statistical":2.03');
  });
  it("discards an in-flight response and clears busy state when its source changes", async () => {
    const repo = repository(); let resolve!: (value: PreviewJob) => void;
    vi.mocked(repo.previews!.createSelection!).mockImplementation(async () => new Promise(r => { resolve = r; }));
    const view = render(panel(repo));
    await waitFor(() => expect((screen.getByRole("button", { name: "Calcular vista previa" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Calcular vista previa" }));
    const replacement = { ...closed, source_hash: "c".repeat(64), authorized_source_hash: "c".repeat(64), generation: 4 };
    const nextRepo = repository(replacement);
    view.rerender(panel(nextRepo));
    await waitFor(() => expect(nextRepo.getReportSnapshot).toHaveBeenCalled());
    await act(async () => resolve(job()));
    await waitFor(() => expect((screen.getByRole("button", { name: "Calcular vista previa" }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByTestId("exact-points")?.textContent ?? "").not.toContain('"statistical":2.03');
    expect(nextRepo.previews!.createSelection).not.toHaveBeenCalled();
  });
  it("permits only explicit local G3 previews and still blocks legacy create and unclosed Pedido", async () => {
    vi.stubEnv("NODE_ENV", "development"); vi.stubEnv("NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED", "true");
    const fetcher = vi.fn(async (url: string, options: RequestInit) => Response.json(url.endsWith("/api/ready") && options.method === "GET" ? { local_bounded_preview: true } : job()));
    vi.stubGlobal("fetch", fetcher);
    const sdk = { rpc: () => ({ abortSignal: async () => ({ data: [closed], error: null }) }), auth: { getSession: async () => ({ data: { session: { access_token: "synthetic-token" } } }) } } as unknown as SupabaseClient;
    try {
      const client = new RailwayPreviewClient(sdk, "http://127.0.0.1:8000");
      await expect(client.create(null, null)).rejects.toMatchObject({ code: "LOCAL_READ_ONLY" }); expect(fetcher).not.toHaveBeenCalled();
      await expect(client.createSelection({ objective: "Pedido", chain_ids: chains, product_ids: ids, source_context: unclosed })).rejects.toMatchObject({ code: "ORDER_CLOSURE_UNVERIFIED" });
      expect(fetcher).not.toHaveBeenCalled();
      await client.createSelection({ objective: "Pedido", chain_ids: chains, product_ids: ids, source_context: closed });
      const posts = fetcher.mock.calls.filter(([, options]) => (options as RequestInit)?.method === "POST");
      expect(posts).toHaveLength(1);
      expect(JSON.parse(String((posts[0][1] as RequestInit).body))).toMatchObject({ objective: "Pedido", chain_ids: chains, product_ids: ids, source_context: closed });
      client.dispose();
    } finally { vi.unstubAllGlobals(); vi.unstubAllEnvs(); }
  });
});
