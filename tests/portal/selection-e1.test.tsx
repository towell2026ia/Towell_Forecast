import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { EngineComparison } from "../../components/forecast/engine-comparison";
import { ForecastHorizonTable } from "../../components/forecast/forecast-horizon-table";
import type { PreviewScope, ProductCandidate } from "../../lib/forecast-preview";

const horizons = Array.from({ length: 12 }, (_, index) => ({ horizon: index + 1,
  target_period: `2027-${String(index + 1).padStart(2, "0")}`, value: 9800 }));
const candidate: ProductCandidate = { family: "statistical", model: "SBA", strategy: "statistical",
  product_id: "FENDI-AZUL", wape: 13.29, bias: 0.02, mae: 1, rmse: 2,
  observations: 24, windows: 3, evaluation_signature: "same-product-pairs",
  evidence_mode: "RETROSPECTIVE_TRAINING", horizons, by_horizon: [] };
const scope: PreviewScope = { chain_id: "Walmart:BD", status: "PREVIEW",
  products: [{ product_id: "FENDI-AZUL", product_code: "AZUL", description: "TOALLA MB FENDI AZUL",
    category_id: "Toalla", forecast_status: "ACTIVE" }],
  statistical: { status: "COMPLETED", models: { SBA: 1 }, candidates: [],
    scope_candidates: [{ model: "SBA", retrospective_wape: 18.9, retrospective_bias: 11.75, observations: 412 }],
    retrospective_wape: 18.9, retrospective_bias: 11.75 },
  selection: { published_champion: null, preview_leader: { strategy: "ensemble" },
    scope_leader: { strategy: "ensemble" }, preview_challenger: null, no_degradation: null, automatic_promotion: false,
    product_candidates: [candidate], suggested_references: { "FENDI-AZUL": { ...candidate, status: "SUGGESTED_RETROSPECTIVE",
      confidence: "Alta", official: false, comparison_status: "ONLY_ELIGIBLE_CANDIDATE", reason_codes: ["ONLY_ELIGIBLE_CANDIDATE"] } } } };

describe("PRD 09.2E1 product and scope presentation", () => {
  it("shows FENDI AZUL product WAPE apart from Walmart:BD scope WAPE", () => {
    render(<EngineComparison scope={scope} productId="FENDI-AZUL" />);
    expect(screen.getByText("SBA *")).toBeTruthy();
    expect(screen.getByText(/No es Champion publicado/)).toBeTruthy();
    const product = screen.getByText(/Rendimiento del producto/).parentElement!;
    expect(within(product).getByText("13.29%")).toBeTruthy();
    const detail = screen.getByText(/Rendimiento global del scope/).parentElement!;
    expect(within(detail).getAllByText("18.9%").length).toBeGreaterThan(0);
    expect(screen.getByText("Líder retrospectivo del scope")).toBeTruthy();
  });

  it("does not invent a product recommendation from scope-only evidence", () => {
    const without = { ...scope, selection: { ...scope.selection!, product_candidates: [], suggested_references: {} } };
    render(<EngineComparison scope={without} productId="FENDI-AZUL" />);
    expect(screen.getByText("Sin sugerencia confiable")).toBeTruthy();
    expect(screen.getByText(/No se utilizará el WAPE del scope/)).toBeTruthy();
  });

  it("shows an actual published Champion without a suggestion asterisk", () => {
    const published = { ...scope, selection: { ...scope.selection!, published_champion: { version: "RF-CERTIFIED-01" } } };
    render(<EngineComparison scope={published} productId="FENDI-AZUL" />);
    expect(screen.getByText("RF-CERTIFIED-01")).toBeTruthy();
    expect(screen.getByText(/Champion publicado · certificado/)).toBeTruthy();
    expect(screen.queryByText("SBA *")).toBeNull();
  });

  it("shows exact H1-H12 band basis and missing evidence", () => {
    const rows = horizons.map((row, index) => ({ ...row, statistical_value: 9700, ml_value: 9900,
      forecast_towell: 9800, p10: index === 11 ? null : 9000, p50: 9800,
      p90: index === 11 ? null : 10000, p95: index === 11 ? null : 10500,
      band_basis: index === 11 ? "INSUFFICIENT" as const : "CATEGORY" as const,
      band_observations: index === 11 ? 0 : 4,
      band_status: index === 11 ? "INSUFFICIENT_BAND_EVIDENCE" as const : "AVAILABLE" as const }));
    render(<ForecastHorizonTable horizons={rows} product />);
    expect(screen.getByText("Estimación provisional H1–H12")).toBeTruthy();
    expect(screen.getAllByText("CATEGORY")).toHaveLength(11);
    expect(screen.getAllByText("Sin evidencia").length).toBeGreaterThan(0);
    expect(screen.getByText(/tres residuales comparables/)).toBeTruthy();
  });
});
