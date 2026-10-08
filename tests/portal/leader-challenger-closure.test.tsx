import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { EngineComparison } from "../../components/forecast/engine-comparison";
import { distinctChallenger, previewCandidateLabel } from "../../lib/preview-presentation";
import type { PreviewCandidate, PreviewScope } from "../../lib/forecast-preview";

const ensemble: PreviewCandidate = { strategy: "ensemble", statistical_weight: 0.8, retrospective_wape: 12 };
const scope: PreviewScope = { chain_id: "synthetic", status: "PREVIEW", selection: {
  published_champion: null, preview_leader: ensemble, preview_challenger: { strategy: "ensemble", statistical_weight: 0.6 },
  no_degradation: null, automatic_promotion: false,
} };
function card(name: string) { return screen.getByText(name).parentElement!; }
function show(leader: PreviewCandidate | null, challenger: PreviewCandidate | null) {
  render(<EngineComparison scope={{ ...scope, selection: { ...scope.selection!, preview_leader: leader, preview_challenger: challenger } }}/>);
}

describe("PRD 09.2E.2.2B.1 visible Leader / Challenger identity", () => {
  it("LC01 suppresses ensemble challenger when only weights differ", () => {
    expect(previewCandidateLabel(ensemble)).toBe("ensemble");
    expect(distinctChallenger(ensemble, { strategy: "ensemble", statistical_weight: 0.6 })).toBeNull();
    expect(distinctChallenger(ensemble, { strategy: "ensemble", statistical_weight: 0.6 })).toBeNull();
  });
  it("LC02 suppresses identical model and strategy", () => {
    const a = { model: "Random Forest Global", strategy: "ml" };
    expect(distinctChallenger(a, { ...a })).toBeNull();
  });
  it("LC03 ignores case, surrounding and repeated whitespace", () => {
    expect(previewCandidateLabel({ model: " Random   Forest Global " })).toBe("Random Forest Global");
    expect(distinctChallenger({ model: " Random   Forest Global " }, { model: "random forest  global" })).toBeNull();
  });
  it("LC04 preserves a visibly different challenger", () => {
    expect(distinctChallenger(ensemble, { model: "Random Forest Global" })?.model).toBe("Random Forest Global");
  });
  it("LC05 null challenger renders dash", () => {
    expect(distinctChallenger(ensemble, null)).toBeNull();
  });
  it("LC06 empty challenger renders dash", () => {
    expect(distinctChallenger(ensemble, {})).toBeNull();
  });
  it("LC07 absent leader does not invent a challenger", () => {
    expect(distinctChallenger(null, { model: "Random Forest Global" })).toBeNull();
  });
  it("LC08 labels the scope leader explicitly, not as product Champion", () => {
    show(ensemble, { strategy: "ensemble", statistical_weight: 0.6 });
    expect(within(card("Líder retrospectivo del scope")).getByText("ensemble")).toBeTruthy();
  });
  it("LC09 never presents an unpublished scope leader as Champion", () => {
    show(ensemble, { strategy: "ensemble", statistical_weight: 0.6 });
    expect(within(card("Champion / Referencia")).getByText("Sin sugerencia confiable")).toBeTruthy();
  });
  it("LC10 leaves the published Champion unchanged", () => {
    show(ensemble, { strategy: "ensemble", statistical_weight: 0.6 });
    expect(screen.getByText(/No es Champion publicado/)).toBeTruthy();
  });
  it("LC11 keeps automatic promotion out of the preview UI", () => {
    show(ensemble, { strategy: "ensemble", statistical_weight: 0.6 });
    expect(document.body.textContent).not.toContain("Promoción automática");
  });
});
