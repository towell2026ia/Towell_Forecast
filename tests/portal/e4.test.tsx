import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { E4Operations } from "../../components/forecast/e4-operations";
import { ForecastEnginesTabs } from "../../components/forecast/forecast-engines-tabs";
import { selectedForecastHorizons } from "../../lib/forecast-e4-ui";
import { PreviewReadError } from "../../lib/forecast-preview";
import type { CalculationDetail, CalculationSummary, PreviewClient } from "../../lib/forecast-preview";

const issue = "2026-07-01";
const horizons = Array.from({ length: 12 }, (_, index) => ({
  horizon: index + 1, target_period: new Date(Date.UTC(2026, 7 + index, 1)).toISOString().slice(0,10),
  statistical_value: 100 + index, ml_value: 90 + index, ensemble_value: 95 + index,
  p10: null, p50: 95 + index, p90: null, p95: null, band_basis: "INSUFFICIENT", band_observations: 0,
  statistical_model: "SBA", ml_model: "Random Forest Global",
}));
const summary: CalculationSummary = { id: "calculation-1", chain_id: "chain-1", product_id: "product-1",
  calculation_no: 1, calculation_code: "C1", issue_period: issue, status: "READY_FOR_DECISION",
  created_at: "2026-10-08T10:00:00Z", suggested_reference: null, recalculation_reason: null };
const detail: CalculationDetail = { ...summary, preview_id: "preview-1", data_snapshot_hash: "a".repeat(64),
  engine_version: "test", git_sha: "test", input_snapshot: { observation_ids: ["source-1"] }, research_snapshot: null,
  candidate_metrics: { STATISTICAL: null, ML: null, ENSEMBLE: null }, horizons,
  selection_events: [], live_evaluations: [] };
const client = { calculations: vi.fn(async () => [summary]), currentSelection: vi.fn(async () => null),
  calculationDetail: vi.fn(async () => detail) } as unknown as PreviewClient;

describe("E4 UI contracts", () => {
  it("loads Calculation History while Selection is still disabled", async () => {
    const historyOnly = { calculations: vi.fn(async () => [summary]),
      currentSelection: vi.fn(async () => { throw new PreviewReadError("E4_DISABLED"); }),
      calculationDetail: vi.fn(async () => detail) } as unknown as PreviewClient;
    render(<E4Operations mode="history" client={historyOnly} chainId="chain-1" productId="product-1"
      canWrite={false} canDecide={false}/>);
    await waitFor(() => expect(screen.getByText(/Lineage: preview preview-1/)).toBeTruthy());
    expect(screen.queryByText("No fue posible consultar los cálculos.")).toBeNull();
  });

  it("shows a confirmed actual point without LIVE and preserves unknown months", async () => {
    const confirmed = { ...detail, actual_sales: [{ observation_id: "sale-1", period: "2026-08-01",
      value: 98, version_no: 1, available_at: "2026-09-01T00:00:00Z", source_batch_id: "batch-1" }] };
    const actualClient = { calculations: vi.fn(async () => [summary]), currentSelection: vi.fn(async () => null),
      calculationDetail: vi.fn(async () => confirmed) } as unknown as PreviewClient;
    render(<E4Operations mode="history" client={actualClient} chainId="chain-1" productId="product-1"
      canWrite={false} canDecide={false}/>);
    await waitFor(() => expect(screen.getByText("Venta real capturada")).toBeTruthy());
    const chart = screen.getByRole("img", { name: /Curvas H1 a H12/ });
    expect(chart.querySelectorAll('circle[fill="#059669"]')).toHaveLength(1);
    expect(confirmed.live_evaluations).toHaveLength(0);
  });

  it("reuses the existing C1 preview, prevents double clicks and gives C2 its own action key", async () => {
    const job = { job_id: "job-1", status: "READY_PREVIEW", mode: "RETROSPECTIVE_TRAINING", engine_version: "test",
      scopes: [{ chain_id: "chain-1", status: "READY_PREVIEW", preview_id: "preview-1" }] };
    const calculations = vi.fn().mockResolvedValueOnce([]).mockResolvedValue([summary]);
    const create = vi.fn(async () => job), createCalculation = vi.fn<NonNullable<PreviewClient["createCalculation"]>>(async () => summary.id);
    const writable = { calculations, currentSelection: vi.fn(async () => null), calculationDetail: vi.fn(async () => detail),
      latest: vi.fn(async () => job), create, status: vi.fn(async () => job), result: vi.fn(async () => job),
      createCalculation } as unknown as PreviewClient;
    render(<E4Operations mode="decision" client={writable} chainId="chain-1" productId="product-1"
      canWrite canDecide={false}/>);
    await waitFor(() => expect(calculations).toHaveBeenCalledOnce());
    const initial = screen.getByRole("button", { name: "Calcular forecast H1–H12" });
    fireEvent.click(initial); fireEvent.click(initial);
    await waitFor(() => expect(createCalculation).toHaveBeenCalledOnce());
    expect(create).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByLabelText("Motivo del recálculo")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Motivo del recálculo"), { target: { value: "Revisión gerencial" } });
    fireEvent.click(screen.getByRole("button", { name: "Recalcular forecast H1–H12" }));
    await waitFor(() => expect(createCalculation).toHaveBeenCalledTimes(2));
    expect(create).toHaveBeenCalledOnce();
    expect(createCalculation.mock.calls[0][4]).not.toBe(createCalculation.mock.calls[1][4]);
  });
  it("shows operational decision/history tabs only after capability is enabled", () => {
    const { rerender } = render(<ForecastEnginesTabs value="summary" onChange={() => {}}/>);
    expect(screen.queryByText("Decisión")).toBeNull();
    expect(screen.getByText("Versiones")).toBeTruthy();
    rerender(<ForecastEnginesTabs value="summary" onChange={() => {}} operational/>);
    expect(screen.getByText("Decisión")).toBeTruthy();
    expect(screen.getByText("Historial")).toBeTruthy();
    expect(screen.queryByText("Versiones")).toBeNull();
  });

  it("reconstructs exact selected H1-H12 instead of a provisional preview", () => {
    const curve = horizons.map(row => ({ horizon: row.horizon, target_period: row.target_period, value: row.ensemble_value! }));
    const result = selectedForecastHorizons({ id: "event-1", calculation_id: "calculation-1", calculation_code: "C1",
      issue_period: issue, selected_candidate: "ENSEMBLE", selected_curve: curve, selected_at: "2026-10-08T10:00:00Z",
      decision_reason: null, comment: null });
    expect(result).toHaveLength(12);
    expect(result[0].forecast_towell).toBe(95);
    expect(result[11].target_period).toBe("2027-07");
  });

  it("reads a frozen C1 detail and keeps versions official separate", async () => {
    render(<E4Operations mode="history" client={client} chainId="chain-1" productId="product-1"
      canWrite={false} canDecide={false}><p>Vintage separado</p></E4Operations>);
    await waitFor(() => expect(screen.getByText(/Lineage: preview preview-1/)).toBeTruthy());
    expect(screen.getAllByText("C1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("H12").length).toBeGreaterThan(0);
    expect(screen.getByRole("img", { name: /Curvas H1 a H12 de C1/ })).toBeTruthy();
    expect(screen.getAllByText(/Sin evidencia/).length).toBeGreaterThan(0);
    for (const horizon of horizons) {
      const cell = screen.getByText(`Sin evidencia / ${horizon.p50} / Sin evidencia / Sin evidencia`);
      const row = cell.closest("tr")!;
      expect(within(row).getByText(`H${horizon.horizon}`)).toBeTruthy();
    }
    expect(screen.getByText("Versiones oficiales")).toBeTruthy();
    expect(screen.queryByText("Vintage separado")).toBeNull();
  });

  it("compares selected C2/C1 curves and actuals rather than statistical defaults", async () => {
    const old = { ...summary, status: "SUPERSEDED" };
    const latest = { ...summary, id: "calculation-2", calculation_no: 2, calculation_code: "C2", status: "DECIDED" };
    const oldCurve = horizons.map(row => ({ horizon: row.horizon, target_period: row.target_period, value: row.statistical_value }));
    const newCurve = horizons.map(row => ({ horizon: row.horizon, target_period: row.target_period, value: row.ensemble_value! }));
    const oldDetail = { ...detail, ...old, selection_events: [{ id: "s1", calculation_id: old.id,
      selected_candidate: "STATISTICAL" as const, selected_curve: oldCurve, selected_at: "2026-09-01T00:00:00Z",
      decision_reason: null, comment: null }], live_evaluations: [{ target_period: horizons[0].target_period,
        actual_sale: 98, sale_version_no: 1 }] };
    const newDetail = { ...detail, ...latest, selection_events: [{ id: "s2", calculation_id: latest.id,
      selected_candidate: "ENSEMBLE" as const, selected_curve: newCurve, selected_at: "2026-10-01T00:00:00Z",
      decision_reason: null, comment: null }], live_evaluations: [] };
    const historyClient = { calculations: vi.fn(async () => [latest, old]),
      currentSelection: vi.fn(async () => ({ ...newDetail.selection_events[0], calculation_code: "C2", issue_period: issue })),
      calculationDetail: vi.fn(async (id: string) => id === latest.id ? newDetail : oldDetail) } as unknown as PreviewClient;
    render(<E4Operations mode="history" client={historyClient} chainId="chain-1" productId="product-1"
      canWrite={false} canDecide={false}/>);
    await waitFor(() => expect(screen.getByText("C2 Towell")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("C1 Towell")).toBeTruthy());
    expect(screen.getByText("-5%")).toBeTruthy();
    expect(screen.getAllByText("98").length).toBeGreaterThan(0);
  });
});
