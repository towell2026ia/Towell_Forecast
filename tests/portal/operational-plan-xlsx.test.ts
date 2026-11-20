import { createElement } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { ForecastPeriodsView } from "../../app/forecast-periods-view";
import { addReportMonth, buildForecastMatrix, emptyReportChoice, type ForecastMatrixRow, type ReportSnapshot } from "../../lib/forecast-reporting";
import { exactPlanSum, operationalPlanSheets, planWindow } from "../../lib/operational-plan-xlsx";
import { localChampionSelection, verifiedLocalChampions } from "../../lib/local-champion";
import { operationalPeriodsSheets } from "../../lib/operational-periods-xlsx";
import { periodsDashboard } from "../../lib/periods-dashboard";
import { planningXlsx } from "../../lib/planning-xlsx";
import * as xlsxModule from "../../lib/planning-xlsx";
import type { LocalChampions } from "../../lib/forecast-preview";
import { emptyFilters, type ForecastReadRepository, type OperationalReadContext } from "../../lib/supabase/types";

const source: OperationalReadContext = { source_hash: "a".repeat(64), authorized_source_hash: "a".repeat(64), generation: 5,
  first_period: "2024-01", last_period: "2026-12", forecast_contract: "pedido-unificado-v1", objective: "Pedido", target_metric: "ORDER",
  target_sheet: "Base_2024_2026", target_file_sha256: "29cac8aebab869e1ded099fa93382046bdbafbaaf4c0156664d52c9488a47c5d",
  training_cutoff: "2026-09", forecast_available: true, target_source_available: true, forecast_block_code: null,
  inventory_source: "ESTADISTICA", unit: "PIEZAS", planning_start: "2027-01", planning_end: "2027-12" };
const run = "00000000-0000-4000-8000-000000000014";
function fixture(start = "2027-01") {
  const months = Array.from({ length: 12 }, (_, index) => addReportMonth(start, index));
  const products = ["alpha", "beta", "different-scope", "different-size", "missing"].map((id, index) => ({ id,
    chain_id: index === 2 ? "chain-b" : "chain-a", category_id: "category", product_code: id, description: id, variant_code: id,
    identifiers: [[`item-${index}`, `000${index}`]] as [string, string | null][], commercial: { product_id: id, chain_id: index === 2 ? "chain-b" : "chain-a",
      format: "BD", model: "FENDI", size: index === 3 ? "BAÑO" : "MEDIO BAÑO", color: id, product_type: "TOALLA", status: "APPROVED" as const, source: "SYNTHETIC", reasons: [] } }));
  const calculations = products.filter(p => p.id !== "missing").map(p => ({ id: `calc-${p.id}`, chain_id: p.chain_id, product_id: p.id,
    calculation_no: 1, calculation_code: "C1", objective: "Pedido", issue_period: "2026-09", cutoff_at: "2026-09-30T23:59:59Z", created_at: "2026-11-18T00:00:00Z",
    status: "READY_FOR_DECISION", created_by: "actor", evidence_mode: "RETROSPECTIVE_TRAINING", engine_version: "SYNTHETIC", git_sha: "synthetic",
    data_snapshot_hash: "b".repeat(64), preview_id: "preview", recalculation_reason: null, suggested_reference: null, candidate_metrics: {}, research_snapshot: null,
    input_snapshot: { source_context: source, planning_run_id: run, champion_eligible: ["STATISTICAL", "ML", "ENSEMBLE"],
      bridge_horizons: ["2026-10", "2026-11", "2026-12"].map(target_period => ({ target_period, statistical_value: 1, ml_value: 1, ensemble_value: 1 })) } }));
  const horizons = calculations.flatMap(c => months.map((month, index) => ({ id: `${c.id}/${index}`, calculation_id: c.id, chain_id: c.chain_id, product_id: c.product_id,
    horizon: index + 1, target_period: month, statistical_value: c.product_id === "alpha" ? .1 : .2,
    ml_value: c.product_id === "beta" && index === 0 ? null : c.product_id === "alpha" ? .2 : .3, ensemble_value: 0,
    statistical_model: "SBA", ml_model: "RF", p10: null, p50: null, p90: null, p95: null, band_basis: "INSUFFICIENT", band_observations: 0 })));
  const snapshot: ReportSnapshot = { products, chains: ["chain-a", "chain-b"].map(id => ({ id, name: id, code: id, parentId: null, status: "ACTIVE", has_history: true, scopeType: "COMMERCIAL_UNIT" })),
    categories: [{ id: "category", chain_id: "chain-a", name: "Familia existente" }], calculations, horizons, decisions: [], live: [], provisionals: [], customers: [], sourceContext: source,
    capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false } };
  const rows: ForecastMatrixRow[] = products.map(product => ({ product, chain: product.chain_id, category: "Familia existente", calculation: calculations.find(c => c.product_id === product.id) ?? null,
    provisional: null, adopted: null, values: {} }));
  return { snapshot, rows, months };
}
function champion(snapshot: ReportSnapshot): LocalChampions {
  const product = snapshot.products[0], months = planWindow(snapshot), start = months[0];
  const cutoff = snapshot.planningRun?.issue_period ?? "2026-09";
  const record: LocalChampions["products"][number] = { id: "choice", run_id: run, candidate_family: "STATISTICAL", source_context: source, planning_year: Number(start.slice(0, 4)),
    selected_by: "actor", selected_at: "2026-11-18T02:00:00Z", objective: "Pedido", environment: "localhost", publication_scope: "LOCAL_OPERATIONAL", official_publication: false, status: "ACTIVE",
    run_content_digest: "c".repeat(64), run_file_sha256: "d".repeat(64), run_emitted_at: "2026-11-18T00:00:00Z", cutoff, unit: "PIEZAS",
    product: { product_id: product.id, chain_id: product.chain_id, candidate_family: "STATISTICAL", model: "SBA", origin: "PYTHON_EXISTING_ENGINE",
      horizons: months.map((month, index) => ({ horizon: index + 1, planning_horizon: index + 1, target_period: month,
        forecast_distance: (Number(month.slice(0, 4)) - Number(cutoff.slice(0,4))) * 12 + Number(month.slice(5, 7)) - Number(cutoff.slice(5,7)), value: "0.1" })) } };
  return { source_context: source, planning_year: Number(start.slice(0, 4)), products: [record], covered: 1, eligible: snapshot.products.length };
}
const value = (sheet: ReturnType<typeof operationalPlanSheets>[number], row: (string | number | null)[], column: string) => row[sheet.headers.indexOf(column)];

describe("Directorial twelve-month and six-bimester export", () => {
  it("sums decimals once, keeps true zeros/missing coverage and never merges scopes or presentations", () => {
    expect(exactPlanSum(["0.1", "0.2", null])).toBe("0.3");
    expect(exactPlanSum([null])).toBeNull(); expect(exactPlanSum(["0", null])).toBe("0");
    const { snapshot, rows } = fixture(), original = JSON.stringify(snapshot);
    const sheets = operationalPlanSheets(snapshot, rows), monthly = sheets[0], bimester = sheets[1];
    const row = monthly.rows.find(r => value(monthly, r, "Cadena") === "chain-a" && value(monthly, r, "Presentación") === "MEDIO BAÑO" && String(value(monthly, r, "Plan")).startsWith("Estadístico"))!;
    expect(value(monthly, row, "2027-01")).toBe(.3); expect(value(monthly, row, "Total de ventana")).toBe(3.6);
    expect(value(monthly, row, "Productos esperados")).toBe(3); expect(value(monthly, row, "Cobertura 2027-01")).toBe("2/3");
    expect(value(monthly, row, "Estado")).toBe("TOTAL PARCIAL");
    const same = bimester.rows.find(r => r[0] === row[0] && r[3] === row[3] && r[6] === row[6])!;
    expect(value(bimester, same, "2027-01 / 2027-02")).toBe(.6); expect(value(bimester, same, "Total de ventana")).toBe(3.6);
    expect(value(bimester, same, "Cobertura 2027-01 / 2027-02")).toBe("4/6");
    const zero = monthly.rows.find(r => r[0] === "chain-a" && r[3] === "MEDIO BAÑO" && String(r[6]).startsWith("Ensemble"))!;
    expect(value(monthly, zero, "2027-01")).toBe(0); expect(value(monthly, zero, "Total de ventana")).toBe(0);
    expect(monthly.rows.filter(r => String(r[6]).startsWith("Estadístico"))).toHaveLength(3);
    expect(monthly.rows.some(r => String(r[6]).includes("Champion elegido"))).toBe(false);
    expect(JSON.stringify(snapshot)).toBe(original);
    expect(() => operationalPlanSheets(snapshot, [...rows, rows[0]])).toThrow("report_plan_duplicate_product");
  });

  it("uses only a verified stored Champion and leaves alternatives and FT-Decision distinct", () => {
    const { snapshot, rows } = fixture(); snapshot.localChampions = champion(snapshot);
    const before = JSON.stringify(snapshot.calculations), [monthly] = operationalPlanSheets(snapshot, rows);
    const selected = monthly.rows.find(row => row[6] === "Champion elegido")!;
    expect(value(monthly, selected, "2027-01")).toBe(.1); expect(value(monthly, selected, "Cobertura 2027-01")).toBe("1/3");
    expect(value(monthly, selected, "Estado")).toBe("TOTAL PARCIAL");
    expect(monthly.rows.some(row => String(row[6]).includes("alternativa sin Champion"))).toBe(true);
    expect(JSON.stringify(snapshot.calculations)).toBe(before); expect(snapshot.decisions).toEqual([]);
    const bad = structuredClone(snapshot); bad.localChampions!.products[0].product.horizons[0].value = "999";
    expect(() => operationalPlanSheets(bad, rows)).toThrow("champion_horizon_lineage");
    const one = structuredClone(snapshot); one.products = [one.products[0]]; one.localChampions!.eligible = 1;
    expect(operationalPlanSheets(one, [rows[0]])[0].rows.every(row => row[6] === "Champion elegido")).toBe(true);
  });

  it("exports Cold Start explicitly as its own method without replicating it into the three families", () => {
    const { snapshot, rows, months } = fixture();
    rows.at(-1)!.provisional = { product_id: "missing", chain_id: "chain-a", issue_period: "2026-09", job_id: "cold-job", created_at: "2026-11-18T01:00:00Z", engine_version: "synthetic", model: "ANALOG_MEDIAN", confidence: "LOW",
      source_context: source, horizons: months.map((target_period, index) => ({ horizon: index + 1, target_period, value: 2 })) };
    const sheets = operationalPlanSheets(snapshot, rows), detail = sheets.find(s => s.name === "Detalle del plan")!;
    const cold = detail.rows.filter(row => row[6] === "missing" && String(row[7]).startsWith("Cold Start"));
    expect(cold).toHaveLength(12); expect(cold.every(row => row[11] === "ANALOG_MEDIAN" && row[9] === 2)).toBe(true);
    expect(cold.every(row => row[5] === "0004")).toBe(true);
    const unaccredited = sheets[0].rows.find(row => String(row[6]).startsWith("Cold Start"))!;
    expect(unaccredited[7]).toBe("Cold Start en detalle; efecto incremental no acreditado");
    expect(unaccredited[12]).toBeNull();
    expect(detail.rows.filter(row => row[6] === "missing" && String(row[7]).startsWith("Estadístico")).every(row => row[9] === null)).toBe(true);
  });

  it("retains the seven original sheets and produces actual downloadable OOXML with the twelve target months", () => {
    const { snapshot } = fixture(), dashboard = periodsDashboard(snapshot, [], emptyReportChoice, ["2027-01", "2027-03"]);
    const sheets = operationalPeriodsSheets(snapshot, dashboard, [], []);
    expect(sheets.slice(0, 7).map(sheet => sheet.name)).toEqual(["Trazabilidad", "Comparativa", "Cálculos almacenados", "Alcance", "Histórico comercial", "Inventario", "Cumplimiento BAASE"]);
    const bytes = planningXlsx(sheets), xml = new TextDecoder().decode(bytes);
    expect(xml).toContain('name="Plan mensual"'); expect(xml).toContain('name="Plan bimestral"');
    const monthly = sheets.find(sheet => sheet.name === "Plan mensual")!;
    expect(monthly.headers).toContain("2027-12"); expect(monthly.rows.some(row => row[12 + 11] === .3)).toBe(true);
    expect(bytes[0]).toBe(0x50); expect(bytes[1]).toBe(0x4b);
  });

  it("uses a rolling March-February window and compares March by target month rather than H1 position", () => {
    const previous = fixture(), current = fixture("2027-03");
    current.snapshot.planningRun = { run_id: run, window_start: "2027-03", window_end: "2028-02", issue_period: "2027-02", created_at: "2027-03-01T00:00:00Z", source_hash: source.source_hash, generation: source.generation, status: "READY" };
    for (const row of current.snapshot.horizons) row.statistical_value = row.horizon;
    const sheets = operationalPlanSheets(current.snapshot, current.rows, previous.snapshot);
    expect(planWindow(current.snapshot)).toEqual(current.months);
    expect(sheets[0].headers).toContain("2028-02"); expect(sheets[1].headers).toContain("2027-03 / 2027-04");
    const comparison = sheets.find(s => s.name === "Comparación versiones")!;
    const march = comparison.rows.find(row => row[4] === "alpha" && row[5] === "Estadístico" && row[6] === "2027-03")!;
    expect(march.slice(9, 12)).toEqual([.1, 1, .9]);
    expect(comparison.rows.some(row => row[6] === "2027-01")).toBe(false);
    expect(comparison.rows.some(row => row[6] === "2028-01")).toBe(false);
    const other = structuredClone(previous.snapshot); other.products[0].chain_id = "foreign";
    expect(() => operationalPlanSheets(current.snapshot, current.rows, other)).toThrow("report_comparison_scope");
    const wrong = structuredClone(current.snapshot); wrong.planningRun!.window_end = "2028-03";
    expect(() => planWindow(wrong)).toThrow("report_plan_window");
  });

  it("validates Champion window/run identity while preserving the legacy January-December read", () => {
    const f = fixture(); f.snapshot.localChampions = champion(f.snapshot);
    expect(verifiedLocalChampions(f.snapshot, f.snapshot.localChampions).covered).toBe(1);
    const rolling = fixture("2027-03");
    rolling.snapshot.planningRun = { run_id: run, window_start: "2027-03", window_end: "2028-02", issue_period: "2027-02", created_at: "2027-03-01T00:00:00Z", source_hash: source.source_hash, generation: source.generation, status: "READY" };
    rolling.snapshot.calculations.forEach(c => { c.issue_period = "2027-02"; c.cutoff_at = "2027-02-28T23:59:59Z"; });
    const chosen = champion(rolling.snapshot);
    Object.assign(chosen, { run_id: run, window_start: "2027-03", window_end: "2028-02" });
    Object.assign(chosen.products[0], { window_start: "2027-03", window_end: "2028-02" });
    expect(verifiedLocalChampions(rolling.snapshot, chosen).covered).toBe(1);
    const wrong = structuredClone(chosen); Object.assign(wrong, { window_start: "2027-01" });
    expect(() => verifiedLocalChampions(rolling.snapshot, wrong)).toThrow("champion_source_lineage");
    expect(localChampionSelection(rolling.snapshot)?.window_start).toBe("2027-03");
    expect(buildForecastMatrix(f.snapshot, emptyReportChoice, [null, null]).rows).toHaveLength(5);
  });

  it("reads and manually admits the scoped provisional without a fabricated calculation, keeping indeterminate Cold Start out of totals", () => {
    const f = fixture("2027-03");
    f.snapshot.planningRun = { run_id: run, window_start: "2027-03", window_end: "2028-02", issue_period: "2027-02", created_at: "2027-03-01T00:00:00Z", source_hash: source.source_hash, generation: source.generation, status: "READY" };
    f.snapshot.calculations = []; f.snapshot.horizons = []; f.rows.forEach(row => { row.calculation = null; });
    const p = { product_id: "missing", chain_id: "chain-a", issue_period: "2027-02", job_id: run, created_at: "2027-03-01T00:00:00Z", engine_version: "synthetic", model: "ANALOG_MEDIAN", confidence: "LOW", source_context: source,
      horizons: f.months.map((target_period, index) => ({ horizon: index + 1, target_period, value: 2 })),
      admission: { manual_selection_allowed: true, status: "REQUIRES_EXPLICIT_ACCEPTANCE", required_acknowledgements: ["COLD_START_METHOD", "NO_TARGET_WAPE", "UNDETERMINED_INCREMENTAL_EFFECT"] }, incremental_effect: { status: "UNDETERMINED", incremental_pieces: null } };
    f.snapshot.provisionals = [p]; f.rows.at(-1)!.provisional = p;
    expect(localChampionSelection(f.snapshot)?.eligible.COLD_START).toEqual(["missing"]);
    const selected = champion(f.snapshot);
    Object.assign(selected, { run_id: run, window_start: "2027-03", window_end: "2028-02" });
    const record = selected.products[0];
    Object.assign(record, { candidate_family: "COLD_START", window_start: "2027-03", window_end: "2028-02" });
    Object.assign(record.product, { product_id: "missing", candidate_family: "COLD_START", model: "ANALOG_MEDIAN", consolidation_allowed: false, admission_status: "ACCEPTED_INDIVIDUAL_ONLY" });
    record.product.horizons.forEach(h => { h.value = "2"; });
    f.snapshot.localChampions = selected;
    expect(verifiedLocalChampions(f.snapshot, selected).products[0].candidate_family).toBe("COLD_START");
    const sheets = operationalPlanSheets(f.snapshot, f.rows), chosen = sheets[0].rows.find(row => row[6] === "Champion elegido")!;
    expect(chosen[12]).toBeNull(); expect(chosen[7]).toContain("efecto incremental no acreditado");
    const detail = sheets.find(sheet => sheet.name === "Detalle del plan")!.rows.filter(row => row[6] === "missing" && row[7] === "Champion elegido");
    expect(detail).toHaveLength(12); expect(detail.every(row => row[9] === 2)).toBe(true);
    const bad = structuredClone(selected); bad.products[0].product.horizons[0].value = "3";
    expect(() => verifiedLocalChampions(f.snapshot, bad)).toThrow("champion_horizon_lineage");
    Object.assign(p, { admission: { manual_selection_allowed: false, status: "REQUIRES_EXPLICIT_ACCEPTANCE" } });
    expect(localChampionSelection(f.snapshot)).toBeNull();
    expect(() => verifiedLocalChampions(f.snapshot, selected)).toThrow("champion_source_lineage");
  });

  it("downloads the monthly and bimonthly workbook from the real Períodos button with a VIEWER and no forecast or publication write", async () => {
    const { snapshot } = fixture(), originalSerializer = xlsxModule.planningXlsx;
    let downloaded: ReturnType<typeof operationalPeriodsSheets> = [], filename = "";
    const serialize = vi.spyOn(xlsxModule, "planningXlsx").mockImplementation(sheets => { downloaded = sheets; return originalSerializer(sheets); });
    const create = Object.getOwnPropertyDescriptor(URL, "createObjectURL"), revoke = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    const objectUrl = vi.fn<(blob: Blob) => string>(() => "blob:synthetic-workbook");
    Object.defineProperty(URL, "createObjectURL", { value: objectUrl, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function(this: HTMLAnchorElement) { filename = this.download; });
    const repository = { openOperationalRead: vi.fn(async () => source), getReportSnapshot: vi.fn(async () => snapshot),
      getReportHistory: vi.fn(async () => []), getReportInventory: vi.fn(async () => []),
      previews: { create: vi.fn(), promoteChampion: vi.fn() } };
    try {
      render(createElement(ForecastFiltersContext.Provider, { value: { repository: repository as unknown as ForecastReadRepository,
        filters: emptyFilters, setFilters: vi.fn(), profile: { id: "viewer", full_name: "Synthetic viewer", global_role: "VIEWER", status: "ACTIVE" } } }, createElement(ForecastPeriodsView)));
      fireEvent.click(await screen.findByRole("button", { name: "Descargar XLSX de Períodos" }));
      await waitFor(() => expect(serialize).toHaveBeenCalledOnce());
      expect(downloaded.map(sheet => sheet.name)).toContain("Plan mensual"); expect(downloaded.map(sheet => sheet.name)).toContain("Plan bimestral");
      expect(downloaded.find(sheet => sheet.name === "Plan mensual")!.headers).toContain("2027-12");
      expect(objectUrl).toHaveBeenCalledOnce(); expect(objectUrl.mock.calls[0][0]).toBeInstanceOf(Blob);
      expect(filename).toBe(`FORECAST_Towell_Periodos_Pedido_${source.source_hash.slice(0,12)}_g5.xlsx`);
      expect(repository.previews.create).not.toHaveBeenCalled(); expect(repository.previews.promoteChampion).not.toHaveBeenCalled();
    } finally {
      if (create) Object.defineProperty(URL, "createObjectURL", create); else Reflect.deleteProperty(URL, "createObjectURL");
      if (revoke) Object.defineProperty(URL, "revokeObjectURL", revoke); else Reflect.deleteProperty(URL, "revokeObjectURL");
    }
  });
});
