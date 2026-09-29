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
  return within(card("Challenger"));
}

describe("PRD 09.2E.2.2B.1 visible Leader / Challenger identity", () => {
  it("LC01 suppresses ensemble challenger when only weights differ", () => {
    expect(previewCandidateLabel(ensemble)).toBe("ensemble");
    expect(distinctChallenger(ensemble, { strategy: "ensemble", statistical_weight: 0.6 })).toBeNull();
    expect(show(ensemble, { strategy: "ensemble", statistical_weight: 0.6 }).getByText("—")).toBeTruthy();
  });
  it("LC02 suppresses identical model and strategy", () => {
    const a = { model: "Random Forest Global", strategy: "ml" };
    expect(show(a, { ...a }).getByText("—")).toBeTruthy();
  });
  it("LC03 ignores case, surrounding and repeated whitespace", () => {
    expect(previewCandidateLabel({ model: " Random   Forest Global " })).toBe("Random Forest Global");
    expect(show({ model: " Random   Forest Global " }, { model: "random forest  global" }).getByText("—")).toBeTruthy();
  });
  it("LC04 preserves a visibly different challenger", () => {
    expect(show(ensemble, { model: "Random Forest Global" }).getByText("Random Forest Global")).toBeTruthy();
  });
  it("LC05 null challenger renders dash", () => {
    expect(show(ensemble, null).getByText("—")).toBeTruthy();
  });
  it("LC06 empty challenger renders dash", () => {
    expect(show(ensemble, {}).getByText("—")).toBeTruthy();
  });
  it("LC07 absent leader does not invent a challenger", () => {
    expect(show(null, { model: "Random Forest Global" }).getByText("—")).toBeTruthy();
  });
  it("LC08 retains Preview Leader as ensemble", () => {
    show(ensemble, { strategy: "ensemble", statistical_weight: 0.6 });
    expect(within(card("Preview Leader")).getByText("ensemble")).toBeTruthy();
  });
  it("LC09 leaves No Degradation unchanged", () => {
    show(ensemble, { strategy: "ensemble", statistical_weight: 0.6 });
    expect(within(card("No Degradation")).getByText("N/A")).toBeTruthy();
  });
  it("LC10 leaves the published Champion unchanged", () => {
    show(ensemble, { strategy: "ensemble", statistical_weight: 0.6 });
    expect(within(card("Champion publicado")).getByText("Ninguno")).toBeTruthy();
  });
  it("LC11 keeps automatic promotion off", () => {
    show(ensemble, { strategy: "ensemble", statistical_weight: 0.6 });
    expect(within(card("Promoción automática")).getByText("OFF")).toBeTruthy();
  });
});
