import { describe, expect, it } from "vitest";
import { planningXlsx } from "../../lib/planning-xlsx";
import { operationalXlsxCell } from "../../lib/operational-periods-xlsx";
import type { PlanningSheet } from "../../lib/planning-results";

const sheet = (rows: unknown[][]): PlanningSheet => ({ name: "Datos", title: "Datos", note: "Sintético", headers: ["Cero", "Opcional", "Cantidad"], rows: rows.map(row => row.map(operationalXlsxCell)) });

describe("Operational XLSX cell construction with the unchanged shared writer", () => {
  it("projects omitted metadata to an empty cell, preserving column positions and explicit numeric zero", () => {
    const xml = new TextDecoder().decode(planningXlsx([sheet([[0, undefined, 1.25], [0, null, 2]])]));
    expect(xml).toContain('<c r="A5" s="2"><v>0</v></c>');
    expect(xml).not.toContain('r="B5"'); expect(xml).not.toContain('r="B6"');
    expect(xml).toContain('<c r="C5" s="2"><v>1.25</v></c>');
    expect(xml).not.toContain("undefined"); expect(xml).not.toContain("[object Object]");
  });

  it("rejects unprojected objects, booleans and invalid quantities at the operational boundary", () => {
    for (const value of [false, true, { provenance: "anonymous" }, ["anonymous"], NaN, Infinity, -Infinity])
      expect(() => sheet([[0, value, 1]])).toThrow("Tipo de celda operativa inválido");
  });

  it("keeps source identifiers and formula-like text as literal inline strings", () => {
    const xml = new TextDecoder().decode(planningXlsx([sheet([["000123", "=SUM(A1:A2)", 0]])]));
    expect(xml).toContain("000123"); expect(xml).toContain("=SUM(A1:A2)"); expect(xml).not.toContain("<f>");
  });
});
