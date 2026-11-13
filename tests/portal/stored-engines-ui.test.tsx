import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ForecastFiltersContext } from "../../app/forecast-towell-app";
import { ForecastEnginesView } from "../../app/operational-engines-view";
import { E4Operations } from "../../components/forecast/e4-operations";
import { addReportMonth, type ReportSnapshot } from "../../lib/forecast-reporting";
import type { PreviewClient, ProductCandidate, SelectionEvent, SuggestedReference } from "../../lib/forecast-preview";
import { emptyFilters, type ForecastReadRepository } from "../../lib/supabase/types";
import { operationalContext } from "./operational-context-fixture";

vi.mock("../../components/forecast/forecast-trader-chart", () => ({ ForecastTraderChart: (props: unknown) => <output data-testid="stored-chart">{JSON.stringify(props)}</output> }));

// All records are synthetic; this exercises the actual snapshot adapter and UI.
function fixture(single = false) {
  const products = (single ? ["blue"] : ["blue", "red"]).map(id => ({ id, chain_id: "chain", category_id: null, product_code: id, variant_code: null, description: `Synthetic ${id}`, identifiers: [],
    commercial: { chain_id: "chain", product_id: id, model: "DESIGN", color: id, size: "MB", purchase_type: "RS" as const, product_type: "TOALLA", status: "PENDING_CLASSIFICATION" as const, source: "SYNTHETIC", reasons: [] } }));
  const horizons = products.flatMap((p, n) => Array.from({ length: 12 }, (_, i) => ({ id: `${p.id}/${i}`, calculation_id: p.id, chain_id: "chain", product_id: p.id, horizon: i + 1,
    target_period: addReportMonth("2026-10", i + 1), statistical_value: n ? 20 : 10, ml_value: n ? null : 5, ensemble_value: n ? 21 : 11,
    p10: null, p50: n ? 21 : 11, p90: null, p95: null, band_basis: "INSUFFICIENT", band_observations: 0, statistical_model: n ? "Holt" : "SBA", ml_model: n ? null : "RF" })));
  function metric(id: string, family: ProductCandidate["family"]): ProductCandidate {
    const field = family === "statistical" ? "statistical_value" : family === "ml" ? "ml_value" : "ensemble_value";
    return { product_id: id, family, model: family === "statistical" ? id === "blue" ? "SBA" : "Holt" : family === "ml" ? "RF" : "Weighted", strategy: family,
      wape: family === "ml" ? 12 : 21, bias: 0, mae: 2, rmse: 3, observations: 24, windows: 4, evaluation_signature: `pairs-${id}`, evidence_mode: "RETROSPECTIVE_TRAINING",
      horizons: horizons.filter(h => h.product_id === id).map(h => ({ horizon: h.horizon, target_period: h.target_period, value: h[field]! })), by_horizon: [] };
  }
  const calculations = products.map(p => {
    const stat = metric(p.id, "statistical");
    const suggestion: SuggestedReference = { ...stat, status: "SUGGESTED_RETROSPECTIVE", official: false, comparison_status: "COMPARABLE", reason_codes: ["STABILITY_CONTROL_FAIL"] };
    return { id: p.id, chain_id: "chain", product_id: p.id, objective: "Venta", calculation_no: 1, calculation_code: "C1", issue_period: "2026-10", status: "READY_FOR_DECISION",
      created_at: "2026-11-08T10:00:00Z", cutoff_at: "2026-10-31T00:00:00Z", created_by: "synthetic-actor", suggested_reference: suggestion, recalculation_reason: null,
      candidate_metrics: { STATISTICAL: stat, ML: p.id === "blue" ? metric(p.id, "ml") : null, ENSEMBLE: metric(p.id, "ensemble") },
      input_snapshot: { source_context: operationalContext, product: { offline_provenance: { emitted_at: "2026-11-05T10:00:00Z" } } }, research_snapshot: null,
      data_snapshot_hash: "synthetic", preview_id: "archived", engine_version: "synthetic", git_sha: "synthetic", evidence_mode: "RETROSPECTIVE_TRAINING" };
  });
  const snapshot: ReportSnapshot = { products, calculations, horizons, chains: [{ id: "chain", code: "S", name: "Synthetic chain", status: "ACTIVE", parentId: null, scopeType: "COMMERCIAL_UNIT", has_history: true }],
    categories: [], decisions: [], live: [], customers: [], provisionals: [], sourceContext: operationalContext, capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false } };
  const client = { readOnly: true, latest: vi.fn(async () => null), create: vi.fn(), createCalculation: vi.fn(), selectCalculation: vi.fn(), currentSelection: vi.fn(async () => null),
    vintageCapabilities: vi.fn(async () => ({ ...snapshot.capabilities, forecast_calculation_history: true, forecast_selection: true })) };
  const repository = { previews: client, openOperationalRead: vi.fn(async () => operationalContext), getReportSnapshot: vi.fn(async () => snapshot), getForecastHistory: vi.fn(async () => []), getCustomerForecast: vi.fn(async () => []) } as unknown as ForecastReadRepository;
  const filters = { ...emptyFilters, chainId: "chain", productId: single ? "blue" : null, purchaseType: "RS" as const, commercialModels: ["DESIGN"] };
  const mount = () => render(<ForecastFiltersContext.Provider value={{ repository, filters, setFilters: vi.fn(), profile: { id: "actor", full_name: "Synthetic", global_role: "ADMIN", status: "ACTIVE" } }}><ForecastEnginesView scope="Synthetic chain"/></ForecastFiltersContext.Provider>);
  return { snapshot, client, repository, filters, mount };
}

describe("Stored engine consultation", () => {
  it("shows a variant's saved candidates without a preview, retains the saved reference and blocks local execution", async () => {
    const { client, mount } = fixture(true); mount();
    const comparison = await screen.findByRole("region", { name: "Comparativa de resultados almacenados" });
    const rows = within(within(comparison).getAllByRole("table")[0]).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(rows[1].textContent).toContain("SBA (1)"); expect(rows[2].textContent).toContain("12%");
    expect(within(comparison).getByText(/Referencia sugerida guardada: SBA/)).toBeTruthy();
    expect(within(comparison).getByText(/control de estabilidad/)).toBeTruthy();
    expect(within(comparison).getByText(/Sin Champion publicado/)).toBeTruthy();
    expect(within(comparison).getByText(/Emisión acreditada 2026-11-05T10:00:00Z/).textContent).toContain("Registro/importación 2026-11-08T10:00:00Z");
    const button = screen.getByRole("button", { name: "Calcular vista previa" });
    expect(button.hasAttribute("disabled")).toBe(true); await userEvent.click(button); expect(client.create).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("tab", { name: "Motor Estadístico" }));
    expect(screen.getByText(/Método conservado en el cálculo: SBA/)).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "Machine Learning" }));
    expect(screen.getByText("Métodos ML conservados en los cálculos")).toBeTruthy();
    expect(client.createCalculation).not.toHaveBeenCalled(); expect(client.selectCalculation).not.toHaveBeenCalled();
  });
  it("shows exactly three primary families, method distribution and partial ML without a consolidated percentage", async () => {
    const { mount } = fixture(); mount();
    const comparison = await screen.findByRole("region", { name: "Comparativa de resultados almacenados" });
    const rows = within(within(comparison).getAllByRole("table")[0]).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(rows[1].textContent).toContain("SBA (1) · Holt (1)"); expect(rows[1].textContent).toContain("30");
    expect(rows[2].textContent).toContain("1/2TOTAL PARCIAL");
    expect(within(rows[1]).getAllByRole("cell").slice(3, 5).map(c => c.textContent)).toEqual(["—", "—"]);
    expect(within(comparison).getByText(/WAPE y Bias del consolidado: Sin métrica acreditada/)).toBeTruthy();
    expect(within(comparison).queryByText(/Líder retrospectivo del scope/)).toBeNull();
    await userEvent.click(screen.getByRole("tab", { name: "Machine Learning" }));
    const individual = screen.getByRole("region", { name: "Métricas ML individuales guardadas" });
    const blue = within(individual).getByText("Synthetic blue").closest("tr")!;
    const red = within(individual).getByText("Synthetic red").closest("tr")!;
    expect(within(blue).getByText("12%")).toBeTruthy();
    expect(red.textContent).toContain("Sin candidato ML validado guardado");
    expect(within(individual).getAllByRole("row")).toHaveLength(3);
    expect(screen.getByText("Métricas individuales disponibles: 1 variantes")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "Decisión" }));
    const decision = screen.getByRole("tabpanel", { name: "Decisión" });
    const decisionComparison = within(decision).getByRole("region", { name: "Comparativa de resultados almacenados" });
    expect(within(within(decisionComparison).getAllByRole("table")[0]).getAllByRole("row")).toHaveLength(4);
    expect(within(decision).getByText(/Selecciona una cadena y un producto para consultar/)).toBeTruthy();
    expect(within(decision).queryByRole("button", { name: /Adoptar|Calcular/ })).toBeNull();
  });
  it("keeps decision history distinct from a Champion and rejects a raw selection for a different calculation", async () => {
    const { snapshot, client, mount } = fixture(true);
    const event: SelectionEvent = { id: "decision", calculation_id: "blue", selected_candidate: "STATISTICAL", selected_curve: snapshot.horizons.map(h => ({ horizon: h.horizon, target_period: h.target_period, value: h.statistical_value })), selected_at: "2026-11-06T10:00:00Z", selected_by: "synthetic-reviewer", decision_reason: "Synthetic reason", comment: null };
    snapshot.decisions = [{ ...event, chain_id: "chain", product_id: "blue", selected_by: "synthetic-reviewer", system_suggestion: "STATISTICAL" }];
    client.currentSelection.mockResolvedValue({ ...event, calculation_id: "foreign", calculation_code: "C9", issue_period: "2026-10" } as never);
    mount(); const comparison = await screen.findByRole("region", { name: "Comparativa de resultados almacenados" });
    expect(within(comparison).getByText(/Forecast Towell vigente:/).parentElement!.textContent).toContain("STATISTICAL · SBA");
    expect(within(comparison).getByText(/Historial de decisiones · Synthetic blue/)).toBeTruthy();
    expect(within(comparison).getAllByText("synthetic-reviewer").length).toBeGreaterThan(0);
    expect(within(comparison).getByText(/Sin Champion publicado/)).toBeTruthy();
    expect(JSON.parse(screen.getByTestId("stored-chart").textContent!).operationalSelection).toBe(false);
  });
  it("blocks E4 write controls even when a local ADMIN caller supplies writable props", async () => {
    const { snapshot, client } = fixture(true);
    const detail = { ...snapshot.calculations[0], horizons: snapshot.horizons, selection_events: [], live_evaluations: [] };
    const e4 = { ...client, calculations: vi.fn(async () => snapshot.calculations), calculationDetail: vi.fn(async () => detail) } as unknown as PreviewClient;
    render(<E4Operations mode="decision" client={e4} chainId="chain" productId="blue" canWrite canDecide/>);
    await screen.findByText(/Referencia sugerida: SBA/);
    expect(screen.queryByRole("button", { name: /Recalcular|Calcular forecast|Adoptar/ })).toBeNull();
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    expect(client.create).not.toHaveBeenCalled(); expect(client.selectCalculation).not.toHaveBeenCalled();
  });
});
