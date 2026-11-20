import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { assertHostedG6Context, championPublicationEnabled, championPublicationOrigin, operationalTable } from "../../lib/hosted-g6";
import { SupabaseForecastReadRepository } from "../../lib/forecast-data";
import { RailwayPreviewClient, type LocalChampions } from "../../lib/forecast-preview";
import { verifiedLocalChampions } from "../../lib/local-champion";
import { periodsDashboard } from "../../lib/periods-dashboard";
import { emptyReportChoice, type ReportSnapshot } from "../../lib/forecast-reporting";
import { operationalPeriodsSheets } from "../../lib/operational-periods-xlsx";
import { emptyFilters, type OperationalReadContext } from "../../lib/supabase/types";

const chain = "00000000-0000-4000-8000-000000000001", product = "00000000-0000-4000-8000-000000000002";
const run = "00000000-0000-4000-8000-000000000003", calculation = "00000000-0000-4000-8000-000000000004";
const origin = "https://towellforecast-production.up.railway.app";
const source: OperationalReadContext = { source_hash: "a".repeat(64), authorized_source_hash: "a".repeat(64), generation: 6,
  first_period: "2024-01", last_period: "2026-12", forecast_contract: "pedido-unificado-v1", objective: "Pedido", target_metric: "ORDER",
  target_sheet: "Base_2024_2026", target_file_sha256: "29cac8aebab869e1ded099fa93382046bdbafbaaf4c0156664d52c9488a47c5d",
  planning_basis: "OBSERVED_AND_COMMERCIAL_CLOSE", observed_cutoff: "2026-09", training_cutoff: "2026-12", scenario_hash: "b".repeat(64),
  planning_start: "2027-01", planning_end: "2027-12", unit: "PIEZAS", inventory_source: "ESTADISTICA",
  target_source_available: true, forecast_available: true, forecast_block_code: null };
const query = { source_context: source, planning_year: 2027, chain_ids: [chain], product_ids: [product] };
const classification = { product_id: product, chain_id: chain, model: "SYNTHETIC MODEL", color: "BLUE", size: "MEDIO BAÑO",
  purchase_type: "RS", product_type: "TOALLA", status: "PENDING_CLASSIFICATION", source: "TRANSIENT", reasons: [] };

function saved(): LocalChampions {
  return { ...query, products: [{ id: "synthetic-champion", run_id: run, candidate_family: "ML", source_context: source,
    planning_year: 2027, selected_by: "synthetic-real-authorized-actor", selected_at: "2026-11-19T18:00:00Z", objective: "Pedido",
    environment: "hosted", status: "ACTIVE", publication_scope: "HOSTED_OPERATIONAL", official_publication: false,
    run_content_digest: "c".repeat(64), run_file_sha256: "d".repeat(64), run_emitted_at: "2026-11-19T17:00:00Z", cutoff: "2026-12", unit: "PIEZAS",
    product: { product_id: product, chain_id: chain, candidate_family: "ML", model: "SYNTHETIC ML", origin: "PYTHON_EXISTING_ENGINE",
      horizons: Array.from({ length: 12 }, (_, i) => ({ horizon: i + 1, planning_horizon: i + 1, forecast_distance: i + 1,
        target_period: `2027-${String(i + 1).padStart(2, "0")}`, value: "8.00" })) } }], covered: 1, eligible: 1 };
}
function auth(token: string | null = "synthetic-only-token") {
  return { rpc: vi.fn(() => ({ abortSignal: async () => ({ data: [source], error: null }) })),
    auth: { getSession: vi.fn(async () => ({ data: { session: token ? { access_token: token } : null } })) } } as unknown as SupabaseClient;
}
beforeEach(() => {
  vi.stubEnv("NODE_ENV", "production"); vi.stubEnv("NEXT_PUBLIC_HOSTED_G6_ENABLED", "true");
  vi.stubEnv("NEXT_PUBLIC_FORECAST_API_URL", origin); vi.stubEnv("NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED", "false");
});
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Hosted G6 transport and manual Champion", () => {
  it("selects all six explicit current views, preserving the unflagged local/legacy contracts", () => {
    const tables = ["products", "categories", "chains", "import_profile_versions", "forecast_calculations", "forecast_calculation_horizons"];
    for (const table of tables) expect(operationalTable(table)).toBe(`ft_g6_${table}`);
    expect(operationalTable("profiles")).toBe("profiles");
    expect(() => assertHostedG6Context(source)).not.toThrow();
    const later = { ...source, generation: 7, observed_cutoff: "2027-02", training_cutoff: "2027-02", last_period: "2027-02",
      planning_start: "2027-03", planning_end: "2028-02" };
    expect(() => assertHostedG6Context(later)).not.toThrow();
    expect(() => assertHostedG6Context({ ...later, planning_start: "2027-01" })).toThrow("OFFICIAL_SOURCE_MISMATCH");
    expect(() => assertHostedG6Context({ ...later, observed_cutoff: "2027-03" })).toThrow("OFFICIAL_SOURCE_MISMATCH");
    for (const changed of [{ generation: 5 }, { objective: "Venta" }, { observed_cutoff: "2026-12" }, { training_cutoff: "2026-09" }])
      expect(() => assertHostedG6Context({ ...source, ...changed } as OperationalReadContext)).toThrow("OFFICIAL_SOURCE_MISMATCH");
    expect(championPublicationEnabled({ hosted_champion_publication: true })).toBe(true);
    expect(championPublicationEnabled({ local_champion_publication: true })).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_HOSTED_G6_ENABLED", "false");
    for (const table of tables) expect(operationalTable(table)).toBe(table);
    expect(championPublicationEnabled({ local_champion_publication: true })).toBe(true);
    expect(championPublicationOrigin(saved().products[0])).toBe(false);
  });

  it("reads catalogue, frozen quantities, annual matrix and XLSX exclusively from hosted views", async () => {
    const called: string[] = [];
    const rows = new Map<string, unknown[]>([
      ["ft_g6_products", [{ id: product, chain_id: chain, category_id: "cat", product_code: "000123", variant_code: null, description: "SYNTHETIC MODEL BLUE" }]],
      ["ft_g6_categories", [{ id: "cat", chain_id: chain, name: "SYNTHETIC CATEGORY" }]],
      ["ft_g6_chains", [{ id: chain, code: "SYNTHETIC_SCOPE", name: "SYNTHETIC CHAIN", status: "ACTIVE" }]],
      ["ft_g6_import_profile_versions", [{ chain_id: chain, mapping_json: { scope: { scope_type: "COMMERCIAL_UNIT" }, products: [{ id: product, identifier_mappings: [[null, "000123"]] }] } }]],
      ["ft_g6_forecast_calculations", [{ id: calculation, chain_id: chain, product_id: product, objective: "Pedido", calculation_no: 1,
        calculation_code: "2027", issue_period: "2026-12-01", cutoff_at: "2026-12-31", status: "READY_FOR_DECISION",
        created_at: "2026-11-19", created_by: "synthetic-actor", input_snapshot: { source_context: source, bridge_horizons: [], planning_run_id: run },
        candidate_metrics: {}, engine_version: "synthetic-existing-engine", evidence_mode: "RETROSPECTIVE_TRAINING" }]],
      ["ft_g6_forecast_calculation_horizons", Array.from({ length: 12 }, (_, i) => ({ id: String(i), calculation_id: calculation,
        chain_id: chain, product_id: product, horizon: i + 1, target_period: `2027-${String(i + 1).padStart(2, "0")}-01`,
        statistical_value: 10, ml_value: 8, ensemble_value: 9 }))],
    ]);
    function result(name: string, data: unknown[], count = data.length) {
      called.push(name); const q: Record<string, unknown> = {};
      for (const method of ["select", "order", "limit", "range", "eq", "in", "abortSignal"]) q[method] = () => q;
      q.then = (accept: (r: unknown) => unknown) => Promise.resolve({ data, error: null, count, status: 200 }).then(accept);
      return q;
    }
    const client = { ...auth(), from: (table: string) => {
      if (!rows.has(table)) throw new Error(`unexpected_legacy_read:${table}`);
      return result(table, rows.get(table)!);
    }, rpc: (name: string) => result(name, name === "operational_read_context" ? [source] : [], 1) } as unknown as SupabaseClient;
    let champions = saved();
    const fetcher = vi.fn(async (url: string) => url.endsWith("/api/ready") ? Response.json({ forecast_calculation_history: true, hosted_champion_publication: true })
      : url.endsWith("/champion") ? Response.json(champions)
      : url.endsWith("/commercial-catalog") ? Response.json({ products: [classification], persisted: false, source_context: source })
      : Promise.reject(new Error(`unexpected_legacy_api:${url}`)));
    vi.stubGlobal("fetch", fetcher);
    const repository = new SupabaseForecastReadRepository(client, "synthetic-actor");
    const snapshot = await repository.getReportSnapshot(emptyFilters, source);
    expect(new Set(called.filter(name => name.startsWith("ft_g6_")))).toEqual(new Set(rows.keys()));
    expect(snapshot.calculations).toHaveLength(1); expect(snapshot.horizons).toHaveLength(12);
    expect(snapshot.decisions).toEqual([]); expect(snapshot.live).toEqual([]); expect(snapshot.provisionals).toEqual([]);
    const dashboard = periodsDashboard(snapshot, [], emptyReportChoice, [null, null]);
    expect(dashboard.available).toBe(1); expect(dashboard.matrix.periods).toHaveLength(12);
    expect(dashboard.matrix.rows[0].values["2027-01"]).toMatchObject({ STATISTICAL: 10, ML: 8, ENSEMBLE: 9, TOWELL: null });
    const trace = operationalPeriodsSheets(snapshot, dashboard, [], [])[0].rows;
    expect(trace).toContainEqual(["Corte del cálculo", "2026-12"]); expect(trace).toContainEqual(["Cierre de Pedido", "2026-09"]);
    expect(trace).toContainEqual([`Champion local · ${chain}/${product} · Publicación oficial`, "HOSTED_OPERATIONAL"]);
    champions = { ...query, products: [], covered: 0, eligible: 1 };
    const withoutChampion = await repository.getReportSnapshot(emptyFilters, source);
    expect(withoutChampion.localChampions?.products).toEqual([]);
    expect(operationalPeriodsSheets(withoutChampion, periodsDashboard(withoutChampion, [], emptyReportChoice, [null, null]), [], [])[0].rows)
      .toContainEqual(["Champion local · estado", "Sin Champion local guardado"]);
    expect(fetcher.mock.calls.every(([url]) => !/provisional|vintage|selection/.test(url))).toBe(true);
    repository.dispose();
  });

  it("does not fall back to raw catalogue tables when a hosted view is absent", async () => {
    const from = vi.fn(() => ({ select: () => { throw new Error("hosted_view_missing"); } }));
    const repository = new SupabaseForecastReadRepository({ ...auth(), from } as unknown as SupabaseClient, "synthetic-actor");
    await expect(repository.getCategories()).rejects.toThrow("hosted_view_missing");
    expect(from).toHaveBeenCalledExactlyOnceWith("ft_g6_categories"); repository.dispose();
  });

  it("permits only the explicit authenticated and idempotent hosted Champion POST, never an E3 write", async () => {
    const fetcher = vi.fn(async (url: string) => url.endsWith("/api/ready") ? Response.json({ hosted_champion_publication: true, local_champion_publication: false,
      official_publication: true, vintage_persistence: true, forecast_selection: true, capture_center: true, live_learning: true })
      : url.endsWith("/champion") ? Response.json(saved())
      : Response.json({ champion_id: "synthetic-champion", status: "ACTIVE", planning_year: 2027, source_context: source }));
    vi.stubGlobal("fetch", fetcher);
    const client = new RailwayPreviewClient(auth(), origin);
    expect(client.readOnly).toBe(true);
    await expect(client.promoteChampion({ ...query, run_id: run, candidate_family: "ML", confirm: true, comment: "Synthetic explicit decision" }, "synthetic-idempotency")).resolves.toBe("synthetic-champion");
    await expect(client.currentChampion(query)).resolves.toMatchObject({ covered: 1, products: [{ environment: "hosted", publication_scope: "HOSTED_OPERATIONAL" }] });
    const [readUrl, readOptions] = fetcher.mock.calls.find(([url]) => url.endsWith("/champion")) as unknown as [string, RequestInit];
    expect(readUrl).toBe(origin + "/api/forecast/champion"); expect(readOptions.method).toBe("POST");
    expect(JSON.parse(readOptions.body as string)).toEqual(query);
    expect(readOptions.headers).toMatchObject({ Authorization: "Bearer synthetic-only-token", "Content-Type": "application/json" });
    expect(readOptions.headers).not.toHaveProperty("Idempotency-Key");
    const flags = await client.vintageCapabilities();
    expect(flags).toMatchObject({ hosted_champion_publication: true, local_champion_publication: false, vintage_persistence: false,
      official_publication: false, forecast_selection: false, capture_center: false, live_learning: false });
    await expect(client.createCandidate(chain, run)).rejects.toThrow("LOCAL_READ_ONLY");
    await expect(client.createCalculation(chain, product, run)).rejects.toThrow("LOCAL_READ_ONLY");
    await expect(client.createSelection({ ...query, objective: "Pedido" })).rejects.toThrow("LOCAL_READ_ONLY");
    const [url, options] = fetcher.mock.calls.find(([url]) => url.endsWith("/champion/promote")) as unknown as [string, RequestInit];
    expect(url).toBe(origin + "/api/forecast/champion/promote"); expect(options.method).toBe("POST");
    expect(options.headers).toMatchObject({ Authorization: "Bearer synthetic-only-token", "Idempotency-Key": "synthetic-idempotency" });
    const body = JSON.parse(options.body as string); expect(body.source_context).toEqual(source);
    expect(body.quantities).toBeUndefined(); expect(body.candidate_family).toBe("ML");
    expect((fetcher.mock.calls as unknown as [string, RequestInit][]).filter(([url, options]) => options?.method === "POST" && url.endsWith("/champion/promote"))).toHaveLength(1);
  });

  it("requires remote capability, the accredited HTTPS service, exact source and a real session", async () => {
    const request = { ...query, run_id: run, candidate_family: "ML" as const, confirm: true as const, comment: "Synthetic choice" };
    const fetcher = vi.fn(async () => Response.json({ local_champion_publication: true, hosted_champion_publication: false }));
    vi.stubGlobal("fetch", fetcher);
    await expect(new RailwayPreviewClient(auth(), origin).promoteChampion(request, "key")).rejects.toThrow("HOSTED_CHAMPION_DISABLED");
    fetcher.mockClear();
    await expect(new RailwayPreviewClient(auth(), "https://other.example").promoteChampion(request, "key")).rejects.toThrow("HOSTED_CHAMPION_DISABLED");
    await expect(new RailwayPreviewClient(auth(), origin).promoteChampion({ ...request, source_context: { ...source, generation: 5 } }, "key")).rejects.toThrow("CHAMPION_SOURCE_INELIGIBLE");
    await expect(new RailwayPreviewClient(auth(null), origin).currentChampion(query)).rejects.toThrow("AUTH_REQUIRED");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("keeps a whole 427-product read in JSON instead of a long query URL", async () => {
    const ids = Array.from({ length: 427 }, (_, i) => `00000000-0000-4000-8000-${String(i + 10).padStart(12, "0")}`);
    const fetcher = vi.fn(async () => Response.json({ source_context: source, planning_year: 2027, products: [], covered: 0, eligible: ids.length }));
    vi.stubGlobal("fetch", fetcher);
    const client = new RailwayPreviewClient(auth(), origin);
    await expect(client.currentChampion({ ...query, product_ids: ids })).resolves.toMatchObject({ covered: 0, eligible: 427 });
    const [url, options] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(origin + "/api/forecast/champion"); expect(url).not.toContain("?");
    expect(JSON.parse(options.body as string).product_ids).toEqual(ids);
    expect(options.headers).not.toHaveProperty("Idempotency-Key");
  });

  it("rejects local publication evidence in hosted readers and keeps manual choice separate from FT-Decision", () => {
    const result = saved(), record = result.products[0];
    const snapshot = { products: [{ id: product, chain_id: chain }], calculations: [], horizons: [], sourceContext: source } as unknown as ReportSnapshot;
    expect(verifiedLocalChampions(snapshot, result)).toBe(result);
    expect(() => verifiedLocalChampions(snapshot, { ...result, products: [{ ...record, environment: "localhost", publication_scope: "LOCAL_OPERATIONAL" }] })).toThrow("champion_source_lineage");
    expect(() => verifiedLocalChampions(snapshot, { ...result, products: [{ ...record, source_context: { ...source, generation: 5 } }] })).toThrow("champion_source_lineage");
  });
});
