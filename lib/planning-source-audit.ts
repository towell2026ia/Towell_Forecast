import { operationalPlanningSheets } from "./planning-operations";
import { type PlanningBundle, type PlanningVersion, type PlanningFilters, noPlanningFilters } from "./planning-results";

/** PRD 3A.3 business confirmation; source names are not interchangeable actuals. */
export const planningSourceLabels = { Towell: "Fcst Towell histórico", Cliente: "Fcst Cliente recibido", Pedidos: "Pedido real" } as const;
export const sourceAuditNotice = "PRD 3A.3: Towell = forecast interno histórico; Cliente = forecast recibido; Pedidos = pedido real. Venta y Entrega: Sin dato en el paquete privado. Provisionales calculados sobre Towell, no sobre venta acreditada. Integración de inventario bloqueada hasta acreditar identidades, tiendas y fuente.";

/** Correct only consultation semantics. Frozen packages, engine and WAPE remain unchanged. */
export function auditedPlanningSheets(b: PlanningBundle, v: PlanningVersion, f: PlanningFilters = noPlanningFilters) {
  const sheets = operationalPlanningSheets(b, v, f);
  const byId = new Map(v.products.map(p => [p.id, p]));
  for (const s of sheets) {
    s.note += " · Consulta PRD 3A.3; no es nueva emisión. " + sourceAuditNotice;
    if (s.name.startsWith("Historico_")) s.title = `${planningSourceLabels[s.name.slice(10) as keyof typeof planningSourceLabels]} · PIEZAS`;
    if (s.name === "Guia_y_control") {
      s.rows = s.rows.filter(r => !["Fcst Cliente / Entrega", "WAPE"].includes(String(r[0])));
      s.rows.push(["Semántica confirmada", sourceAuditNotice], ["WAPE heredado", "Conservado como evidencia del cálculo anterior; Towell y Cliente son forecasts, no venta real. No acredita LIVE de venta."]);
    }
    if (s.name === "Series_comerciales") {
      // Previous row: identity[0..8], month, provisional, null, order, Towell, null, Cliente, Champion.
      s.headers = [...s.headers.slice(0, 10), "Estadístico provisional (base Towell)", "Fcst Towell histórico", "Fcst Cliente recibido", "Pedido real", "Venta real", "Entrega", "Estado Champion"];
      s.rows = s.rows.map(r => {
        const p = byId.get(String(r[0])), month = String(r[9]);
        // A forecast's target month need not be an already closed actual month.
        return [...r.slice(0, 11), p?.history.Towell?.[month]?.value ?? null, p?.history.Cliente?.[month]?.value ?? null, r[12], null, null, r[16]];
      });
      s.note = sourceAuditNotice + " · Fuentes independientes en PIEZAS; no sumar forecasts, pedidos, ventas ni entregas entre sí.";
    }
    if (s.name === "Revisiones_y_reales") {
      s.title = "Plan original, revisiones y fuentes independientes · PIEZAS";
      s.headers = s.headers.map(h => h === "Venta Towell" ? planningSourceLabels.Towell : h === "Cliente" ? planningSourceLabels.Cliente : h);
      s.headers.push("Venta real", "Entrega");
      s.rows.forEach(r => {
        const p = byId.get(String(r[3])), month = String(r[4]);
        r[7] = p?.history.Towell?.[month]?.value ?? null;
        r[9] = p?.history.Cliente?.[month]?.value ?? null;
        r.push(null, null);
      });
      s.note = sourceAuditNotice + " · Se conservan original y revisiones existentes; no se acreditan ventas con Towell.";
    }
    if (s.name === "Metricas_y_cobertura") {
      s.title = "WAPE heredado · Interpretación comercial pendiente";
      s.headers = s.headers.map(h => h.startsWith("WAPE LIVE") ? h.replace("WAPE LIVE", "WAPE heredado (no certifica LIVE)") : h);
      s.note = "Valores y método WAPE intactos. Towell y Cliente comparan forecasts históricos; no acreditan venta real. La evidencia de emisión previa y de fuentes debe revisarse antes de uso operativo.";
    }
  }
  return sheets;
}
