import { describe, expect, it } from "vitest";
import { planningXlsx } from "../../lib/planning-xlsx";
import type { PlanningSheet } from "../../lib/planning-results";

const sheet = (rows: unknown[][]): PlanningSheet => ({ name: "Datos", title: "Datos", note: "Sintético", headers: ["Cero", "Opcional", "Cantidad"], rows: rows as PlanningSheet["rows"] });

describe("Native XLSX runtime cell boundary", () => {
  it("preserves omitted metadata as an empty cell, column positions and an explicit numeric zero", () => {
    const xml = new TextDecoder().decode(planningXlsx([sheet([[0, undefined, 1.25], [0, null, 2]])]));
    expect(xml).toContain('<c r="A5" s="2"><v>0</v></c>');
    expect(xml).not.toContain('r="B5"'); expect(xml).not.toContain('r="B6"');
    expect(xml).toContain('<c r="C5" s="2"><v>1.25</v></c>');
    expect(xml).not.toContain("undefined"); expect(xml).not.toContain("[object Object]");
  });

  it("rejects an unprojected object or boolean explicitly instead of inventing a quantity or a text representation", () => {
    for (const value of [false, true, { provenance: "anonymous" }, ["anonymous"]])
      expect(() => planningXlsx([sheet([[0, value, 1]])])).toThrow("Tipo de celda inválido");
    for (const value of [NaN, Infinity, -Infinity])
      expect(() => planningXlsx([sheet([[0, null, value]])])).toThrow("Cantidad inválida");
  });

  it("keeps source identifiers and formula-like text as literal inline strings", () => {
    const xml = new TextDecoder().decode(planningXlsx([sheet([["000123", "=SUM(A1:A2)", 0]])]));
    expect(xml).toContain("000123"); expect(xml).toContain("=SUM(A1:A2)"); expect(xml).not.toContain("<f>");
  });
});
