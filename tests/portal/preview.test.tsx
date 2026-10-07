import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SupabaseClient } from "@supabase/supabase-js";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { ForecastEnginesView } from "../../app/operational-engines-view";
import { RailwayPreviewClient, PreviewReadError, type PreviewClient, type PreviewJob } from "../../lib/forecast-preview";
import { emptyFilters, type Filters, type ForecastReadRepository, type Role } from "../../lib/supabase/types";

const job: PreviewJob = { job_id: "OPJ-test", status: "READY_PREVIEW", mode: "RETROSPECTIVE_TRAINING", engine_version: "fixture-operational-preview-2-retrospective", created_at: 1,
  scopes: [{ chain_id: "a", chain_name: "Scope autorizado", status: "PREVIEW", issue_period: "2026-07", engine_version: "fixture-operational-preview-2-retrospective", evaluation_mode: "RETROSPECTIVE_EVALUATION", eligibility: { evaluated: 7, visible_products: 7, stat_eligible: 2, ml_eligible: 0, INSUFFICIENT: 5 },
    statistical: { status: "COMPLETED", models: { Croston: 1, Holt: 1 }, candidates: [{ model: "Croston" }, { model: "Holt" }], retrospective_wape: 12.5, retrospective_bias: -2 },
    ml: { status: "NOT_ELIGIBLE", training_samples: 0, leader: null, candidates: [], retrospective_wape: null, retrospective_bias: null },
    selection: { published_champion: null, preview_leader: { strategy: "statistical" }, preview_challenger: null, no_degradation: null, automatic_promotion: false },
    products: [{ product_id: "p", product_code: "real-code", description: "Producto autorizado", category_id: "cat", forecast_status: "ACTIVE", horizons: Array.from({ length: 12 }, (_, i) => ({ horizon: i + 1, target_period: i < 5 ? `2026-${String(i + 8).padStart(2, "0")}` : `2027-${String(i - 4).padStart(2, "0")}`, statistical_value: 120 + i, ml_value: null, forecast_towell: 120 + i, p10: null, p50: 120 + i, p90: null, p95: null })) }] }] };
function api(overrides: Partial<PreviewClient> = {}): PreviewClient {
  return { latest: vi.fn(async () => job), create: vi.fn(async () => ({ ...job, status: "QUEUED", scopes: [] })), status: vi.fn(async () => job), result: vi.fn(async () => job), dispose: vi.fn(), ...overrides };
}
function harness(client: PreviewClient, filters: Filters = { ...emptyFilters, chainId: "a" }, role: Role = "ADMIN") {
  const repository = { previews: client } as ForecastReadRepository;
  return render(<ForecastFiltersContext.Provider value={{ repository, filters, setFilters: vi.fn(), profile: { id: "u", full_name: "Usuario", global_role: role, status: "ACTIVE" } }}><ForecastEnginesView scope="Scope autorizado"/></ForecastFiltersContext.Provider>);
}
afterEach(() => vi.unstubAllGlobals());
describe("E2 operational UI", () => {
  it("real engine cards, distribution, cutoff, ML not eligible and no pilot/demo", async () => {
    harness(api()); await screen.findAllByText("2 de 7 · 28.6%");
    expect(screen.getByText("12.5%")).toBeTruthy();
    expect(screen.getAllByText("Croston").length).toBeGreaterThan(0); expect(screen.getAllByText("Holt").length).toBeGreaterThan(0);
    expect(screen.getAllByText("No elegible").length).toBeGreaterThan(0); expect(screen.getByText(/Datos reales hasta: 2026-07/)).toBeTruthy();
    expect(screen.getByText("Promoción automática")).toBeTruthy(); expect(screen.getByText("OFF")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/FENDI|Walmart|demo forecast|Ejecución no habilitada/);
  });
  it("selected product H1-H12, literal missing bands, search identifiers do not hide selected product", async () => {
    const client = api(); harness(client, { ...emptyFilters, chainId: "a", productId: "p", search: "UPC-from-catalog" });
    await screen.findByText("H12"); expect(screen.getAllByText("2026-08").length).toBeGreaterThan(0); expect(screen.getAllByText("2027-07").length).toBeGreaterThan(0);
    expect(client.latest).toHaveBeenCalledWith("a", "p"); expect(screen.getAllByText(/^H\d+$/)).toHaveLength(12);
    expect(screen.getAllByText("—").length).toBeGreaterThan(12);
  });
  it("all scopes have independent cuts, no consolidated fake total", async () => {
    harness(api({ latest: vi.fn(async () => ({ ...job, cuts_status: "CUTS_NOT_ALIGNED", scopes: [...job.scopes, { chain_id: "b", chain_name: "Segundo scope", status: "PREVIEW", issue_period: "2026-03" }] })) }), emptyFilters);
    await screen.findByText("Segundo scope"); expect(screen.getByText(/CUTS_NOT_ALIGNED/)).toBeTruthy();
    expect(screen.getByText("2026-07")).toBeTruthy(); expect(screen.getByText("2026-03")).toBeTruthy();
    expect(screen.queryByText("WAPE retrospectivo: 12.5%")).toBeNull();
  });
  it("viewer is read only while authorized results stay visible", async () => {
    const client = api(); harness(client, { ...emptyFilters, chainId: "a" }, "VIEWER"); await screen.findAllByText("2 de 7 · 28.6%");
    expect(screen.getByRole("button", { name: "Calcular vista previa" }).hasAttribute("disabled")).toBe(true); expect(client.create).not.toHaveBeenCalled();
  });
  it("double click stays loading and creates only one asynchronous job", async () => {
    const client = api({ latest: vi.fn(async () => null), create: vi.fn(() => new Promise<PreviewJob>(() => {})) }); harness(client);
    await userEvent.dblClick(screen.getByRole("button", { name: "Calcular vista previa" }));
    expect(client.create).toHaveBeenCalledTimes(1); expect(screen.getByRole("button", { name: "Calculando vista previa…" }).hasAttribute("disabled")).toBe(true);
  });
  it("initial latest arriving late cannot overwrite a running request", async () => {
    let complete!: (value: PreviewJob | null) => void;
    const client = api({ latest: vi.fn(() => new Promise<PreviewJob | null>(resolve => { complete = resolve; })), create: vi.fn(() => new Promise<PreviewJob>(() => {})) }); harness(client);
    await userEvent.click(screen.getByRole("button", { name: "Calcular vista previa" })); complete(null);
    await waitFor(() => expect(screen.getByRole("button", { name: "Calculando vista previa…" }).hasAttribute("disabled")).toBe(true));
  });
  it("safe failed state with insufficient product does not display fake forecast zero", async () => {
    harness(api({ latest: vi.fn(async () => ({ ...job, status: "FAILED", scopes: [{ chain_id: "a", status: "FAILED", error_code: "NO_ELIGIBLE_PRODUCTS", products: [{ product_id: "p", product_code: "code", description: "Producto corto", category_id: "cat", forecast_status: "INSUFFICIENT", history_months: 5, minimum_history_months: 6 }] }] })) }), { ...emptyFilters, chainId: "a", productId: "p" });
    await screen.findByText(/5 meses de venta consecutivos/); expect(screen.getByText("Historial insuficiente")).toBeTruthy();
    expect(screen.queryByText("Error de cálculo")).toBeNull(); expect(screen.queryByText("Comparativa ejecutiva")).toBeNull();
    expect(screen.queryByText("H1")).toBeNull();
  });
  it("new not-eligible outcome is terminal and leaves no empty model tables", async () => {
    harness(api({ latest: vi.fn(async () => ({ ...job, status: "NOT_ELIGIBLE", scopes: [{ chain_id: "a", status: "NOT_ELIGIBLE", error_code: "NO_ELIGIBLE_PRODUCTS", products: [{ product_id: "p", product_code: "code", description: "Producto corto", category_id: "cat", forecast_status: "INSUFFICIENT", history_months: 5, minimum_history_months: 6 }] }] })) }), { ...emptyFilters, chainId: "a", productId: "p" });
    await screen.findByText("Historial insuficiente");
    expect(screen.getByRole("button", { name: "Calcular vista previa" }).hasAttribute("disabled")).toBe(false);
    await userEvent.click(screen.getByRole("tab", { name: /Motor Estadístico/ }));
    expect(screen.getByText(/No se ejecutó el motor estadístico/)).toBeTruthy();
    expect(screen.queryByText("WAPE retro")).toBeNull();
    await userEvent.click(screen.getByRole("tab", { name: /Machine Learning/ }));
    expect(screen.getByText(/No se ejecutó Machine Learning/)).toBeTruthy();
  });
  it("does not attribute scope-level models to an ineligible selected product", async () => {
    const scope = { ...job.scopes[0], products: [{ product_id: "p", product_code: "code", description: "Producto corto", category_id: "cat", forecast_status: "INSUFFICIENT", history_months: 5, minimum_history_months: 6, horizons: [] }] };
    harness(api({ latest: vi.fn(async () => ({ ...job, scopes: [scope] })) }), { ...emptyFilters, chainId: "a", productId: "p" });
    await screen.findByText("Historial insuficiente");
    expect(screen.queryByText("12.5%")).toBeNull();
    expect(screen.queryByText("Comparativa ejecutiva")).toBeNull();
  });
  it("errors expose fixed safe codes, not upstream messages", async () => {
    harness(api({ latest: vi.fn(async () => { throw new Error("private upstream message"); }) }));
    await screen.findByRole("alert"); expect(document.body.textContent).toContain("DATA_READ_FAILED"); expect(document.body.textContent).not.toContain("private upstream message");
  });
  it("historical date filters never change operational cutoff or API input", async () => {
    const client = api(); harness(client, { ...emptyFilters, chainId: "a", periodRange: ["2025-01", "2025-02"] });
    await screen.findByText(/Datos reales hasta: 2026-07/); expect(client.latest).toHaveBeenCalledWith("a", null);
    await userEvent.click(screen.getByRole("button", { name: "Calcular vista previa" })); await waitFor(() => expect(client.create).toHaveBeenCalledWith("a", null));
  });
});
describe("E2 Railway browser boundary", () => {
  function auth(token: string | null = "synthetic-access-token") { return { auth: { getSession: vi.fn(async () => ({ data: { session: token ? { access_token: token } : null } })) } } as unknown as SupabaseClient; }
  it("sends Bearer only to Railway, keeps role/id out and no historical browser dataset", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(job), { status: 202 })); vi.stubGlobal("fetch", fetcher);
    const client = new RailwayPreviewClient(auth(), "https://backend.example"); await client.create("a", "p");
    const [url, options] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://backend.example/api/forecast/preview-runs"); expect(options.credentials).toBe("omit");
    expect(options.headers).toEqual({ Authorization: "Bearer synthetic-access-token", "Content-Type": "application/json" });
    expect(JSON.parse(String(options.body))).toEqual({ chain_id: "a", product_id: "p", objective: "Venta", issue_period: null, mode: "RETROSPECTIVE_TRAINING" });
  });
  it("missing session, malformed origins and logout abort deny before fetch", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    for (const origin of ["", "http://backend.example", "https://user:password@backend.example", "https://backend.example/path", "https://backend.example?x=1"]) await expect(new RailwayPreviewClient(auth(), origin).create(null, null)).rejects.toThrow("PREVIEW_CONFIGURATION_REQUIRED");
    await expect(new RailwayPreviewClient(auth(null), "https://backend.example").create(null, null)).rejects.toThrow("AUTH_REQUIRED");
    const client = new RailwayPreviewClient(auth(), "https://backend.example"); client.dispose(); await expect(client.status("job")).rejects.toThrow("AUTH_REQUIRED"); expect(fetcher).not.toHaveBeenCalled();
  });
  it("latest 404 is empty and foreign scope remains denied", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error_code: "PREVIEW_NOT_FOUND" }), { status: 404 })));
    const client = new RailwayPreviewClient(auth(), "https://backend.example"); expect(await client.latest(null, null)).toBeNull();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error_code: "SCOPE_FORBIDDEN" }), { status: 403 })));
    await expect(client.result("job", "foreign")).rejects.toThrow("SCOPE_FORBIDDEN");
  });
  it("surfaces the backend's safe scope-validation code for a rejected preview", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error_code: "REQUEST_001" }), { status: 400 })));
    await expect(new RailwayPreviewClient(auth(), "https://backend.example").create(null, "product"))
      .rejects.toThrow("REQUEST_001");
  });
  it("invalid/upstream response never leaks unrecognized error bodies", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error_code: "private database message" }), { status: 500 })));
    await expect(new RailwayPreviewClient(auth(), "https://backend.example").status("job")).rejects.toThrow("DATA_READ_FAILED");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ rows: [] }), { status: 200 })));
    await expect(new RailwayPreviewClient(auth(), "https://backend.example").status("job")).rejects.toBeInstanceOf(PreviewReadError);
  });
});
describe("E3 vintages and quality boundary", () => {
  function auth() { return { auth: { getSession: vi.fn(async () => ({ data: { session: { access_token: "synthetic-access-token" } } })) } } as unknown as SupabaseClient; }
  it("keeps the Vintages tab read only while all E3 flags are off", async () => {
    const client = api({ vintages: vi.fn(async () => []), vintageCapabilities: vi.fn(async () => ({ vintage_persistence: false, official_publication: false, champion_publication: false })) });
    harness(client);
    await userEvent.click(screen.getByRole("tab", { name: "Vintages" }));
    await screen.findByText("Todavía no hay vintages en este scope.");
    expect(client.vintages).toHaveBeenCalledWith("a");
    expect(screen.queryByRole("button", { name: "Crear candidato de vintage" })).toBeNull();
  });
  it("viewer never sees candidate or freeze actions even if backend capability is on", async () => {
    const client = api({ vintages: vi.fn(async () => []), vintageCapabilities: vi.fn(async () => ({ vintage_persistence: true, official_publication: false, champion_publication: false })) });
    harness(client, { ...emptyFilters, chainId: "a" }, "VIEWER");
    await userEvent.click(screen.getByRole("tab", { name: "Vintages" }));
    await screen.findByText(/Tu acceso es de consulta/);
    expect(screen.queryByRole("button", { name: "Crear candidato de vintage" })).toBeNull();
  });
  it("shows immutable vintage lineage and H1 without creating a new preview", async () => {
    const vintage = { id: "v1", run_id: "r1", chain_id: "a", objective: "Venta", issue_period: "2026-07-01", cutoff_at: "2026-07-31T23:59:59Z", forecast_version: "v1", certification_status: "PROVISIONAL", frozen_at: "2026-07-31T23:59:59Z", created_at: "2026-07-31T23:59:59Z" };
    const client = api({ vintages: vi.fn(async () => [vintage]), vintageCapabilities: vi.fn(async () => ({ vintage_persistence: false, official_publication: false, champion_publication: false })),
      vintageDetail: vi.fn(async () => ({ vintage, run: { id: "r1", chain_id: "a", actor_id: "admin-1", data_snapshot_hash: "sha-synthetic", engine_version: "e3", git_sha: "commit-synthetic", status: "COMPLETED" },
        horizons: [{ id: "h1", product_id: "p", horizon: 1, target_period: "2026-08-01", statistical_value: 10, ml_value: 9, forecast_towell: 8, p10: null, p50: null, p90: null, p95: null, model_strategy: "ensemble" }],
        aggregates: [], metrics: [{ id: "m1", metric: "WAPE", phase: "VALIDATION", value: 12, period: "2026-07-01" }],
        models: [{ id: "model-1", model_family: "STATISTICAL", algorithm: "Croston", validation_wape: 12, certification_status: "PROVISIONAL" }],
        inputs: [{ id: "input-1", monthly_observation_id: "observation-1", evidence_mode: "RETROSPECTIVE_TRAINING" }],
        gates: [{ id: "gate-1", gate_type: "DATA_QUALITY", status: "DATA_QUALITY_WARNING", policy_version: "E3-GATES-1.0.0", observed: null, target: null }] })) });
    harness(client);
    await userEvent.click(screen.getByRole("tab", { name: "Vintages" }));
    await screen.findByText("v1");
    await userEvent.click(screen.getByRole("button", { name: "Ver detalle" }));
    await screen.findByText(/sha-synthetic/);
    expect(screen.getByText(/commit-synthetic/)).toBeTruthy();
    expect(screen.getByText(/DATA_QUALITY_WARNING/)).toBeTruthy();
    expect(screen.getByText(/Croston \(PROVISIONAL\)/)).toBeTruthy();
    expect(client.create).not.toHaveBeenCalled();
  });
  it("quality and candidate requests use user JWT, never service role", async () => {
    const quality = { chain_id: "a", preview_id: "pid", dataset_hash: "digest", policy_version: "E3-GATES-1.0.0",
      data_quality: { status: "DATA_QUALITY_WARNING" }, forecast_quality: { status: "FORECAST_QUALITY_READY" },
      service_level: { status: "NOT_MEASURABLE" }, publication: "PROVISIONAL" };
    const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(url.includes("quality-gates") ? quality : { vintage_id: "v1" }), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const client = new RailwayPreviewClient(auth(), "https://backend.example");
    expect((await client.qualityGates("a")).publication).toBe("PROVISIONAL");
    expect(await client.createCandidate("a", "pid")).toBe("v1");
    const [url, options] = fetcher.mock.calls[1] as unknown as [string, RequestInit];
    expect(url).toBe("https://backend.example/api/forecast/vintages/candidates");
    expect(options.credentials).toBe("omit");
    expect(options.headers).toHaveProperty("Authorization", "Bearer synthetic-access-token");
    expect(JSON.stringify(options)).not.toMatch(/service.role|sb_secret/i);
    expect(JSON.parse(String(options.body))).toEqual({ chain_id: "a", preview_id: "pid" });
  });
});
