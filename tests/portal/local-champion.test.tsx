import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { SupabaseClient } from "@supabase/supabase-js";
import { E4Operations } from "../../components/forecast/e4-operations";
import { RailwayPreviewClient, type LocalChampions, type PreviewClient } from "../../lib/forecast-preview";
import { localChampionSelection } from "../../lib/local-champion";
import type { ReportSnapshot } from "../../lib/forecast-reporting";
import type { OperationalReadContext } from "../../lib/supabase/types";

const chain = "00000000-0000-4000-8000-000000000001", product = "00000000-0000-4000-8000-000000000002";
const second = "00000000-0000-4000-8000-000000000003", run = "00000000-0000-4000-8000-000000000004";
const source: OperationalReadContext = { source_hash: "a".repeat(64), generation: 5, first_period: "2024-01", last_period: "2026-10",
  forecast_contract: "pedido-unificado-v1", objective: "Pedido", target_metric: "ORDER", target_sheet: "Base_2024_2026",
  target_file_sha256: "29cac8aebab869e1ded099fa93382046bdbafbaaf4c0156664d52c9488a47c5d", authorized_source_hash: "a".repeat(64),
  training_cutoff: "2026-09", forecast_available: true, target_source_available: true, forecast_block_code: null,
  inventory_source: "ESTADISTICA", unit: "PIEZAS", planning_start: "2027-01", planning_end: "2027-12" };
function fixture() {
  return { sourceContext: source, products: [product, second].map(id => ({ id, chain_id: chain })),
    calculations: [product, second].map(id => ({ id, chain_id: chain, product_id: id, issue_period: "2026-09", cutoff_at: "2026-09-30",
      objective: "Pedido", status: "READY_FOR_DECISION", calculation_no: 1, created_at: "2026-11-18", candidate_metrics: {},
      input_snapshot: { source_context: source, planning_run_id: run, champion_eligible: ["STATISTICAL", "ML", "ENSEMBLE"] } })),
    horizons: [product, second].flatMap(id => Array.from({ length: 12 }, (_, i) => ({ calculation_id: id, chain_id: chain, product_id: id,
      horizon: i + 1, target_period: `2027-${String(i + 1).padStart(2, "0")}`, statistical_value: 10, ml_value: id === product ? 8 : null, ensemble_value: 9 }))),
    chains: [], categories: [], provisionals: [], decisions: [], live: [], customers: [] } as unknown as ReportSnapshot;
}
const selection = () => localChampionSelection(fixture())!;
function empty(): LocalChampions { return { source_context: source, planning_year: 2027, products: [], covered: 0, eligible: 2 }; }
function saved(): LocalChampions {
  return { ...empty(), covered: 1, products: [{ id: "champion-synthetic", run_id: run, candidate_family: "ML", source_context: source,
    planning_year: 2027, selected_by: "synthetic-user", selected_at: "2026-11-18T00:00:00Z", objective: "Pedido", environment: "localhost", status: "ACTIVE",
    publication_scope: "LOCAL_OPERATIONAL", official_publication: false,
    run_content_digest: "c".repeat(64), run_file_sha256: "d".repeat(64), run_emitted_at: "2026-11-17T00:00:00Z", cutoff: "2026-09", unit: "PIEZAS",
    product: { product_id: product, chain_id: chain, candidate_family: "ML", model: "Synthetic ML", origin: "PYTHON_EXISTING_ENGINE",
      horizons: Array.from({ length: 12 }, (_, i) => ({ planning_horizon: i + 1, horizon: i + 1, forecast_distance: i + 4,
        target_period: `2027-${String(i + 1).padStart(2, "0")}`, value: "8.00" })) } }] };
}
function auth() {
  return { rpc: () => ({ abortSignal: async () => ({ data: [source], error: null }) }),
    auth: { getSession: async () => ({ data: { session: { access_token: "synthetic-only-token" } } }) } } as unknown as SupabaseClient;
}
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Explicit local Champion", () => {
  it("derives one run and exact per-family coverage without promoting a raw ML component", () => {
    const selected = selection();
    expect(selected.run_id).toBe(run); expect(selected.planning_year).toBe(2027);
    expect(selected.eligible).toEqual({ STATISTICAL: [product, second], ML: [product], ENSEMBLE: [product, second], COMMERCIAL_PLAN: [] });
    const snapshot = fixture();
    snapshot.calculations[1].input_snapshot.planning_run_id = second;
    expect(localChampionSelection(snapshot)).toBeNull();
    expect(localChampionSelection({ ...fixture(), sourceContext: { ...source, generation: 4, forecast_contract: "pedido-towell-v1", target_sheet: "Pedidos", planning_start: undefined, planning_end: undefined } })).toBeNull();
  });

  it("requires explicit family and confirmation, publishes only eligible IDs and restores the saved choice on remount", async () => {
    let current = empty();
    const currentChampion = vi.fn(async () => current);
    const promoteChampion = vi.fn(async (request: unknown, key: string) => {
      expect(request).toMatchObject({ candidate_family: "ML" }); expect(key).toBeTruthy();
      current = saved(); return "champion-synthetic";
    });
    const selectCalculation = vi.fn();
    const client = { readOnly: true, currentChampion, promoteChampion, selectCalculation } as unknown as PreviewClient;
    const view = () => <E4Operations mode="decision" client={client} chainId={chain} productId={null} canWrite={false} canDecide={false} champion={selection()} canChampion/>;
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const first = render(view());
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Champion" }).hasAttribute("disabled")).toBe(false));
    expect((screen.getByRole("combobox", { name: "Champion" }) as unknown as HTMLSelectElement).value).toBe("");
    expect(screen.getByRole("option", { name: "Machine Learning (1/2)" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Plan comercial (0/2)" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Guardar Champion" }).hasAttribute("disabled")).toBe(true);
    expect(promoteChampion).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("combobox", { name: "Champion" }), { target: { value: "ML" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar Champion" }));
    expect(confirm).toHaveBeenCalled(); expect(promoteChampion).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Guardar Champion" }));
    await waitFor(() => expect(promoteChampion).toHaveBeenCalledTimes(1));
    expect(promoteChampion.mock.calls[0]).toEqual([expect.objectContaining({ run_id: run, candidate_family: "ML", planning_year: 2027,
      product_ids: [product], chain_ids: [chain], source_context: source, confirm: true }), expect.any(String)]);
    expect(promoteChampion.mock.calls[0][0]).not.toHaveProperty("quantities");
    await waitFor(() => expect(screen.getByRole("button", { name: "Guardar Champion" }).hasAttribute("disabled")).toBe(true));
    expect(selectCalculation).not.toHaveBeenCalled();
    first.unmount(); render(view());
    await waitFor(() => expect((screen.getByRole("combobox", { name: "Champion" }) as unknown as HTMLSelectElement).value).toBe("ML"));
    expect(promoteChampion).toHaveBeenCalledTimes(1);
  });

  it("keeps query failures and readers blocked without a write or automatic choice", async () => {
    const promoteChampion = vi.fn();
    const client = { currentChampion: vi.fn(async () => { throw new Error(); }), promoteChampion } as unknown as PreviewClient;
    const { rerender } = render(<E4Operations mode="decision" client={client} chainId={chain} productId={null} canWrite={false} canDecide={false} champion={selection()} canChampion/>);
    await screen.findByRole("alert");
    expect(screen.getByRole("combobox", { name: "Champion" }).hasAttribute("disabled")).toBe(true);
    rerender(<E4Operations mode="decision" client={{ ...client, currentChampion: async () => saved() }} chainId={chain} productId={null} canWrite={false} canDecide={false} champion={selection()} canChampion={false}/>);
    await waitFor(() => expect((screen.getByRole("combobox", { name: "Champion" }) as unknown as HTMLSelectElement).value).toBe("ML"));
    expect(screen.getByRole("button", { name: "Guardar Champion" }).hasAttribute("disabled")).toBe(true);
    expect(promoteChampion).not.toHaveBeenCalled();
  });

  it("ignores a late Champion response after changing the selected scope", async () => {
    let finish!: (result: LocalChampions) => void;
    const pending = new Promise<LocalChampions>(resolve => { finish = resolve; });
    const client = { currentChampion: vi.fn().mockImplementationOnce(() => pending).mockResolvedValue(empty()), promoteChampion: vi.fn() } as unknown as PreviewClient;
    const first = selection(), next = { ...selection(), run_id: second };
    const view = (selected: typeof first) => <E4Operations mode="decision" client={client} chainId={chain} productId={null} canWrite={false} canDecide={false} champion={selected} canChampion/>;
    const rendered = render(view(first));
    await waitFor(() => expect(client.currentChampion).toHaveBeenCalledTimes(1));
    rendered.rerender(view(next));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Champion" }).hasAttribute("disabled")).toBe(false));
    await act(async () => finish(saved()));
    expect((screen.getByRole("combobox", { name: "Champion" }) as unknown as HTMLSelectElement).value).toBe("");
  });

  it("uses only the guarded local authenticated POST with the same source pin", async () => {
    vi.stubEnv("NODE_ENV", "development"); vi.stubEnv("NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED", "true");
    const fetcher = vi.fn(async (url: string) => url.endsWith("/api/ready") ? Response.json({ local_champion_publication: true })
      : url.includes("/champion?") ? Response.json(saved()) : Response.json({ champion_id: "champion-synthetic", status: "ACTIVE", planning_year: 2027, source_context: source }));
    vi.stubGlobal("fetch", fetcher);
    const client = new RailwayPreviewClient(auth(), "http://127.0.0.1:8000");
    const selected = selection();
    await expect(client.promoteChampion({ ...selected, candidate_family: "ML", product_ids: [product], confirm: true, comment: "Synthetic explicit choice" }, "synthetic-idempotency")).resolves.toBe("champion-synthetic");
    await expect(client.currentChampion(selected)).resolves.toMatchObject({ covered: 1, eligible: 2 });
    const [url, options] = fetcher.mock.calls.find(([url]) => url.endsWith("/champion/promote")) as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/api/forecast/champion/promote");
    expect(options.method).toBe("POST"); expect(options.credentials).toBe("omit");
    expect(options.headers).toMatchObject({ Authorization: "Bearer synthetic-only-token", "Idempotency-Key": "synthetic-idempotency" });
    const body = JSON.parse(options.body as string); expect(body.source_context).toEqual(source); expect(body.quantities).toBeUndefined();
    expect(body.eligible).toBeUndefined(); expect(body.productChains).toBeUndefined();
  });

  it("cannot open the hosted or non-development writer through this exception", async () => {
    vi.stubEnv("NODE_ENV", "production"); vi.stubEnv("NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED", "true");
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const client = new RailwayPreviewClient(auth(), "https://backend.example");
    await expect(client.promoteChampion({ ...selection(), candidate_family: "ML", confirm: true, comment: "Synthetic explicit choice" }, "one")).rejects.toThrow("LOCAL_CHAMPION_DISABLED");
    expect(fetcher).not.toHaveBeenCalled();
  });
});
