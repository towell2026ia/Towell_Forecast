import { describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { LocalChampions } from "../../lib/forecast-preview";
import { SupabaseReportReader } from "../../lib/forecast-report-repository";
import { buildForecastMatrix, emptyReportChoice, type ReportSnapshot } from "../../lib/forecast-reporting";
import { periodsDashboard } from "../../lib/periods-dashboard";
import { operationalPeriodsSheets } from "../../lib/operational-periods-xlsx";
import { planningXlsx } from "../../lib/planning-xlsx";
import { emptyFilters, type ForecastReadRepository, type OperationalReadContext } from "../../lib/supabase/types";

const chain = "00000000-0000-4000-8000-000000000011", alpha = "00000000-0000-4000-8000-000000000012";
const beta = "00000000-0000-4000-8000-000000000013", run = "00000000-0000-4000-8000-000000000014";
const source: OperationalReadContext = { source_hash: "a".repeat(64), generation: 5, first_period: "2024-01", last_period: "2026-10",
  forecast_contract: "pedido-unificado-v1", objective: "Pedido", target_metric: "ORDER", target_sheet: "Base_2024_2026",
  target_file_sha256: "29cac8aebab869e1ded099fa93382046bdbafbaaf4c0156664d52c9488a47c5d", authorized_source_hash: "a".repeat(64),
  training_cutoff: "2026-09", forecast_available: true, target_source_available: true, forecast_block_code: null,
  inventory_source: "ESTADISTICA", unit: "PIEZAS", planning_start: "2027-01", planning_end: "2027-12" };
const month = (i: number) => `2027-${String(i + 1).padStart(2, "0")}`;

function fixture(adopted = false) {
  const products = [alpha, beta].map(id => ({ id, chain_id: chain, category_id: null, product_code: id, variant_code: null,
    description: `Synthetic ${id === alpha ? "Alpha" : "Beta"}`, identifiers: [] }));
  const calculations = products.map(p => ({ id: p.id, chain_id: chain, product_id: p.id, objective: "Pedido", issue_period: "2026-09-01",
    cutoff_at: "2026-09-30T23:59:59Z", status: "READY_FOR_DECISION", calculation_no: 1, calculation_code: "2027", created_at: "2026-11-18T00:00:00Z",
    candidate_metrics: {}, engine_version: "SYNTHETIC_ENGINE", evidence_mode: "RETROSPECTIVE_TRAINING", input_snapshot: { planning_run_id: run,
      source_context: source, champion_eligible: ["STATISTICAL", "ML", "ENSEMBLE"], bridge_horizons: ["2026-10", "2026-11", "2026-12"].map(target_period => ({ target_period, statistical_value: 90, ml_value: p.id === alpha ? 70 : null, ensemble_value: 80 })) } }));
  const horizons = products.flatMap(p => Array.from({ length: 12 }, (_, i) => ({ id: `${p.id}/${i}`, calculation_id: p.id, chain_id: chain, product_id: p.id,
    horizon: i + 1, target_period: month(i), statistical_value: 100.11, ml_value: p.id === alpha ? 80.22 : null, ensemble_value: 90.33,
    statistical_model: "SYNTHETIC_SBA", ml_model: p.id === alpha ? "SYNTHETIC_RF" : null, p50: 90.33, p10: null, p90: null, p95: null,
    band_basis: "INSUFFICIENT", band_observations: 0 })));
  const decisions = adopted ? [{ id: "synthetic-ft-decision", calculation_id: alpha, chain_id: chain, product_id: alpha, selected_candidate: "STATISTICAL",
    selected_at: "2026-11-18T01:00:00Z", selected_by: "synthetic-decider", selected_curve: horizons.filter(h => h.product_id === alpha).map(h => ({ horizon: h.horizon, target_period: h.target_period, value: h.statistical_value })) }] : [];
  const snapshot = { products, calculations, horizons, decisions, chains: [{ id: chain, name: "Synthetic chain", code: "S", parentId: null, status: "ACTIVE", has_history: true }],
    categories: [], live: [], customers: [], provisionals: [], sourceContext: source,
    capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false, forecast_calculation_history: true, local_champion_publication: true } } as unknown as ReportSnapshot;
  let persisted: LocalChampions = { source_context: source, planning_year: 2027, products: [], covered: 0, eligible: 2 };
  const currentChampion = vi.fn(async () => structuredClone(persisted)), promoteChampion = vi.fn();
  const repository = { getProducts: vi.fn(async () => products), getVisibleChains: vi.fn(async () => snapshot.chains), getCategories: vi.fn(async () => []),
    openOperationalRead: vi.fn(async () => source), previews: { currentChampion, promoteChampion, vintageCapabilities: vi.fn(async () => snapshot.capabilities) } } as unknown as ForecastReadRepository;
  const tables: Record<string, unknown[]> = { forecast_calculations: calculations, forecast_calculation_horizons: horizons, forecast_selection_events: decisions };
  const from = (table: string) => {
    const query: Record<string, unknown> = {};
    for (const key of ["select", "in", "order", "range"]) query[key] = () => query;
    query.abortSignal = async () => ({ data: tables[table] ?? [], error: null });
    return query;
  };
  const reader = new SupabaseReportReader({ from } as unknown as SupabaseClient, repository, new AbortController().signal);
  const saved: LocalChampions["products"][number] = { id: "synthetic-champion", run_id: run, candidate_family: "ML", source_context: source,
    planning_year: 2027, selected_by: "synthetic-publisher", selected_at: "2026-11-18T02:00:00Z", run_content_digest: "c".repeat(64),
    run_file_sha256: "d".repeat(64), run_emitted_at: "2026-11-18T00:00:00Z", cutoff: "2026-09", unit: "PIEZAS", objective: "Pedido", environment: "localhost", status: "ACTIVE",
    publication_scope: "LOCAL_OPERATIONAL", official_publication: false,
    product: { product_id: alpha, chain_id: chain, candidate_family: "ML", model: "SYNTHETIC_RF", origin: "PYTHON_EXISTING_ENGINE",
      horizons: Array.from({ length: 12 }, (_, i) => ({ planning_horizon: i + 1, horizon: i + 1, target_period: month(i), forecast_distance: i + 4, value: "80.22" })) } };
  const store = (record: typeof saved = saved) => { persisted = { ...persisted, products: [record], covered: 1 }; };
  return { reader, repository, currentChampion, promoteChampion, saved, snapshot, store };
}

describe("One pinned Champion read for Períodos and XLSX", () => {
  it("includes bridge and planning months only within a visible scope, without fetching its forecasts", async () => {
    const f = fixture();
    f.repository.getPeriods = vi.fn(async () => ["2024-01", "2026-09", "2026-10"]);
    const expected = ["2024-01", "2026-09", "2026-10", "2026-11", "2026-12", ...Array.from({ length: 12 }, (_, i) => month(i))];
    expect(await f.reader.periods(null, source)).toEqual(expected);
    expect(await f.reader.periods(chain, source)).toEqual(expected);
    f.repository.getPeriods = vi.fn(async () => []);
    expect(await f.reader.periods("unauthorized-chain", source)).toEqual([]);
    expect(f.currentChampion).not.toHaveBeenCalled();
  });

  it("retrieves persisted publication evidence without changing FT-Decision or any chart/matrix amount", async () => {
    const f = fixture(), original = JSON.stringify(f.snapshot.calculations);
    const before = await f.reader.snapshot(emptyFilters, source);
    const beforeDashboard = periodsDashboard(before, [], emptyReportChoice, [null, null]);
    const beforeSheets = operationalPeriodsSheets(before, beforeDashboard, [], []);
    expect(before.localChampions?.products).toEqual([]);
    expect(beforeSheets[0].rows).toContainEqual(["Champion local · estado", "Sin Champion local guardado"]);
    f.store();
    const after = await f.reader.snapshot(emptyFilters, source);
    expect(after.localChampions?.products[0].id).toBe(f.saved.id);
    expect(after.calculations[0].current_local_champion?.candidate_family).toBe("ML");
    expect(after.calculations[1].current_local_champion).toBeUndefined();
    expect(JSON.stringify(f.snapshot.calculations)).toBe(original);
    const dashboard = periodsDashboard(after, [], emptyReportChoice, [null, null]);
    expect(dashboard.points).toEqual(beforeDashboard.points);
    const matrix = buildForecastMatrix(after, emptyReportChoice, [null, null]);
    expect(matrix.rows.every(row => row.adopted === null && row.values["2027-01"].TOWELL === null)).toBe(true);
    const sheets = operationalPeriodsSheets(after, dashboard, [], []);
    expect(sheets.slice(1)).toEqual(beforeSheets.slice(1));
    const trace = sheets[0];
    expect(trace.headers).toEqual(["Campo", "Valor"]);
    expect(trace.rows).toContainEqual(["Horizonte del plan · 2027-01", "H1"]);
    expect(trace.rows).toContainEqual(["Distancia desde el cierre · 2027-01", 4]);
    expect(trace.rows).toContainEqual(["Horizonte del plan · 2027-12", "H12"]);
    expect(trace.rows).toContainEqual(["Distancia desde el cierre · 2027-12", 15]);
    expect(trace.rows).toContainEqual([`Champion local · ${chain}/${alpha} · 2027-01`, "80.22"]);
    expect(trace.rows).toContainEqual([`Champion local · ${chain}/${alpha} · Usuario`, "synthetic-publisher"]);
    expect(trace.rows).toContainEqual([`Champion local · ${chain}/${alpha} · Publicación oficial`, "LOCAL_OPERATIONAL"]);
    expect(trace.rows).toContainEqual(["Champion local · cobertura", "1/2"]);
    expect(planningXlsx(sheets).length).toBeGreaterThan(1000);
    expect(f.promoteChampion).not.toHaveBeenCalled();
  });

  it("preserves a different persisted FT-Decision as an independent curve", async () => {
    const f = fixture(true); f.store();
    const snapshot = await f.reader.snapshot(emptyFilters, source), dashboard = periodsDashboard(snapshot, [], emptyReportChoice, [null, null]);
    expect(dashboard.matrix.rows[0].values["2027-01"]).toMatchObject({ ML: 80.22, TOWELL: 100.11 });
    expect(dashboard.adopted).toBe(1); expect(snapshot.localChampions?.products[0].candidate_family).toBe("ML");
    const sheet = operationalPeriodsSheets(snapshot, dashboard, [], []).find(s => s.name === "Cálculos almacenados")!;
    const first = sheet.rows.find(row => row[2] === alpha && row[5] === "2027-01")!;
    expect(first[sheet.headers.indexOf("Forecast Towell adoptado")]).toBe(100.11);
    expect(first[sheet.headers.indexOf("ML")]).toBe(80.22);
    expect(f.promoteChampion).not.toHaveBeenCalled();
  });

  it("rejects foreign source, product or quantities instead of using a previous choice", async () => {
    for (const mutation of ["source", "product", "quantity"] as const) {
      const f = fixture(), bad = structuredClone(f.saved);
      if (mutation === "source") bad.source_context.generation = 4;
      if (mutation === "product") bad.product.product_id = run;
      if (mutation === "quantity") bad.product.horizons[0].value = "80.23";
      f.store(bad);
      await expect(f.reader.snapshot(emptyFilters, source)).rejects.toThrow(/champion_(?:source|horizon)_lineage/);
      expect(f.promoteChampion).not.toHaveBeenCalled();
    }
    const f = fixture(); f.store(); await f.reader.snapshot(emptyFilters, source);
    f.currentChampion.mockRejectedValueOnce(new Error("RLS_DENIED"));
    await expect(f.reader.snapshot(emptyFilters, source)).rejects.toThrow("RLS_DENIED");
  });
});
