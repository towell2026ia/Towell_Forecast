import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SupabaseClient } from "@supabase/supabase-js";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { ForecastEnginesView } from "../../app/operational-engines-view";
import { RailwayPreviewClient, PreviewReadError, type PreviewClient, type PreviewJob } from "../../lib/forecast-preview";
import { emptyFilters, type Filters, type ForecastReadRepository, type Role } from "../../lib/supabase/types";

const job: PreviewJob = { job_id: "OPJ-test", status: "READY_PREVIEW", mode: "RETROSPECTIVE_TRAINING", engine_version: "actual-engine-version", created_at: 1,
  scopes: [{ chain_id: "a", chain_name: "Scope autorizado", status: "PREVIEW", issue_period: "2026-07", eligibility: { evaluated: 7, visible_products: 7, stat_eligible: 2, ml_eligible: 0, INSUFFICIENT: 5 },
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
    harness(api({ latest: vi.fn(async () => ({ ...job, status: "FAILED", scopes: [{ chain_id: "a", status: "FAILED", error_code: "NO_ELIGIBLE_PRODUCTS", products: [{ product_id: "p", product_code: "code", description: "Producto corto", category_id: "cat", forecast_status: "INSUFFICIENT" }] }] })) }), { ...emptyFilters, chainId: "a", productId: "p" });
    await screen.findByText(/Sin forecast elegible: INSUFFICIENT/); expect(screen.getByText("Error de cálculo")).toBeTruthy(); expect(screen.queryByText("H1")).toBeNull();
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
  it("invalid/upstream response never leaks unrecognized error bodies", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error_code: "private database message" }), { status: 500 })));
    await expect(new RailwayPreviewClient(auth(), "https://backend.example").status("job")).rejects.toThrow("DATA_READ_FAILED");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ rows: [] }), { status: 200 })));
    await expect(new RailwayPreviewClient(auth(), "https://backend.example").status("job")).rejects.toBeInstanceOf(PreviewReadError);
  });
});
