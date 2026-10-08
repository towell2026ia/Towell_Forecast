import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { PreviewPerformancePanel } from "../../components/forecast/preview-performance-panel";
import { EngineComparison } from "../../components/forecast/engine-comparison";
import { currentRetrospectivePreview, distinctChallenger, previewMetrics } from "../../lib/preview-presentation";
import type { PreviewJob, PreviewScope } from "../../lib/forecast-preview";

const version = "fixture-operational-preview-2-retrospective";
const scope: PreviewScope = {
  chain_id: "chain", status: "PREVIEW", engine_version: version, evaluation_mode: "RETROSPECTIVE_EVALUATION", issue_period: "2026-07",
  eligibility: { evaluated: 1, stat_eligible: 1, ml_eligible: 1 },
  statistical: { status: "COMPLETED", models: { Croston: 1 }, retrospective_wape: 30, retrospective_bias: -8,
    selected_metrics: { model: "Selección por producto", retrospective_wape: 30, retrospective_bias: -8, observations: 24 },
    candidates: [{ family: "statistical", product_id: "product", model: "Croston", retrospective_wape: 68, retrospective_bias: 59, observations: 12, status: "EVALUATED", by_horizon: [{ horizon: 1, wape: 60, bias: 4, observations: 10 }] }] },
  ml: { status: "COMPLETED", training_samples: 100, leader: "Random Forest Global", retrospective_wape: 40, retrospective_bias: 2,
    candidates: [{ model: "Random Forest Global", retrospective_wape: 40, retrospective_bias: 2, observations: 24, by_product: [{ product_id: "product", retrospective_wape: 74, retrospective_bias: 8, observations: 12 }] }] },
  selection: { published_champion: null, preview_leader: { strategy: "ensemble", statistical_weight: 0.7, retrospective_wape: 25, retrospective_bias: 1, observations: 24,
    by_product: [{ product_id: "product", retrospective_wape: 64, retrospective_bias: 51, observations: 12 }] },
    preview_challenger: { strategy: "ensemble", statistical_weight: 0.7 }, no_degradation: null, automatic_promotion: false },
  products: [{ product_id: "product", product_code: "synthetic", description: "Synthetic product", category_id: null, forecast_status: "ACTIVE",
    horizons: [{ horizon: 1, target_period: "2026-08", statistical_model: "Croston", statistical_value: 10, ml_value: 11, forecast_towell: 12, p10: null, p50: 12, p90: null, p95: null }] }],
};
const job: PreviewJob = { job_id: "synthetic", status: "READY_PREVIEW", mode: "RETROSPECTIVE_TRAINING", engine_version: version, scopes: [scope] };

describe("PRD 09.2E.2.2B executive closure", () => {
  it("uses product metrics without falling back to scope values", () => {
    const m = previewMetrics(scope, "product");
    expect([m.stat.wape, m.ml.wape, m.towell.wape]).toEqual([68, 74, 64]);
    expect(previewMetrics(scope, "absent").stat.wape).toBeNull();
    expect(previewMetrics(scope, "absent").ml.wape).toBeNull();
  });
  it("uses only scope metrics when no product is selected", () => {
    const m = previewMetrics(scope, null);
    expect([m.stat.wape, m.ml.wape, m.towell.wape]).toEqual([30, 40, 25]);
  });
  it("rejects an old preview, even when it carries plausible metrics", () => {
    expect(currentRetrospectivePreview(job, scope)).toBe(true);
    expect(currentRetrospectivePreview({ ...job, engine_version: "old" }, scope)).toBe(false);
    render(<PreviewPerformancePanel job={{ ...job, engine_version: "old" }} scope={scope} productId="product"/>);
    expect(screen.getByText(/Vista previa desactualizada/)).toBeTruthy();
    expect(screen.queryByText("68%")).toBeNull();
  });
  it("renders retrospective product KPIs, explicit caveat and model evidence", () => {
    render(<PreviewPerformancePanel job={job} scope={scope} productId="product" historical/>);
    expect(screen.getAllByText("68%").length).toBeGreaterThan(0); expect(screen.getAllByText("74%").length).toBeGreaterThan(0);
    expect(screen.getByText("64%")).toBeTruthy(); expect(screen.getByText("51%")).toBeTruthy();
    expect(screen.getByText(/No constituye certificación point-in-time/)).toBeTruthy();
    const detail = screen.getByText("Ver desempeño de modelos").closest("details")!;
    expect(within(detail).getAllByText("Croston").length).toBeGreaterThan(0);
    expect(within(detail).getAllByText("H1")).toHaveLength(3);
  });
  it("shows no duplicate challenger strategy", () => {
    expect(distinctChallenger(scope.selection?.preview_leader, scope.selection?.preview_challenger)).toBeNull();
    render(<EngineComparison scope={scope}/>);
    expect(screen.getByText("Líder retrospectivo del scope")).toBeTruthy();
    expect(distinctChallenger(scope.selection?.preview_leader, { strategy: "statistical", statistical_weight: 1 })?.strategy).toBe("statistical");
  });
});
