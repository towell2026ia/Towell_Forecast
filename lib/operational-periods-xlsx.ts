import { reportSeries, type ReportSnapshot } from "./forecast-reporting";
import { dashboardSeries, type periodsDashboard } from "./periods-dashboard";
import type { PlanningSheet } from "./planning-results";
import type { HistoricalRow, OperationalInventory } from "./supabase/types";

export function operationalPeriodsSheets(snapshot: ReportSnapshot, dashboard: ReturnType<typeof periodsDashboard>, history: HistoricalRow[], inventory: OperationalInventory[]): PlanningSheet[] {
  const context = snapshot.sourceContext;
  if (!context) throw new Error("official_source_unavailable");
  const scope = new Map(dashboard.matrix.rows.map(row => [row.product.id, row]));
  const series = Object.keys(dashboardSeries) as (keyof typeof dashboardSeries)[];
  const forecast = Object.keys(reportSeries) as (keyof typeof reportSeries)[];
  const inScope = (row: { product_id: string; chain_id: string }) => scope.get(row.product_id)?.product.chain_id === row.chain_id;
  return [
    { name: "Trazabilidad", title: "Consulta operativa de Períodos", note: "Todas las cantidades comerciales están en piezas.", headers: ["Campo", "Valor"], rows: [
      ["Fuente oficial SHA-256", context.source_hash], ["Actualización", context.generation], ["Primer periodo", context.first_period], ["Último periodo", context.last_period], ["Corte del cálculo", dashboard.cutoff],
      ["Productos", scope.size], ["Productos con cálculo", dashboard.available], ["Productos con decisión", dashboard.adopted],
      ["Pares Pedido/Entrega", dashboard.kpis.paired], ["Pares esperados", dashboard.kpis.expectedPairs],
      ["Cobertura Fill Rate", dashboard.kpis.expectedPairs === 0 ? "Sin alcance" : dashboard.kpis.paired < dashboard.kpis.expectedPairs ? "Parcial" : "Completa"], ["Fill Rate (%)", dashboard.kpis.fillRate],
    ] },
    { name: "Comparativa", title: "Series de la gráfica de Períodos", note: "Mismos puntos y cobertura de la consulta visible. El inventario corresponde al saldo de cada mes. Fill Rate (%) divide Entrega entre Pedido de los mismos pares comparables.", headers: ["Periodo", ...series.flatMap(key => [dashboardSeries[key].label, `Cobertura ${dashboardSeries[key].label}`]), "Fill Rate (%)", "Cobertura Fill Rate", "Pedido comparable", "Entrega comparable"], rows: dashboard.points.map(point => [point.period, ...series.flatMap(key => [point.coverage[key].exact ?? point.values[key], `${point.coverage[key].covered}/${point.coverage[key].eligible}`]), point.fillRate?.value ?? null, `${point.fillRate?.covered ?? 0}/${point.fillRate?.eligible ?? scope.size}`, point.fillRate?.orderedExact ?? point.fillRate?.ordered ?? null, point.fillRate?.deliveredExact ?? point.fillRate?.delivered ?? null]) },
    { name: "Cálculos almacenados", title: "Matriz por producto", note: "Resultados congelados y decisión acreditada, si existe.", headers: ["Cadena", "Producto", "ID producto", "Cálculo", "ID cálculo", "Periodo", ...forecast.map(key => reportSeries[key])], rows: dashboard.matrix.rows.flatMap(row => dashboard.matrix.periods.map(period => [row.chain, row.product.description, row.product.id, row.calculation?.calculation_code ?? null, row.calculation?.id ?? null, period, ...forecast.map(key => row.values[period]?.[key] ?? null)])) },
    { name: "Histórico comercial", title: "Series comerciales por producto y mes", note: "Las cantidades de origen se conservan como texto decimal exacto. Incluye los periodos históricos consultados para la comparación.", headers: ["Cadena", "Producto", "ID producto", "Periodo", "Venta", "Pedido", "Entrega", "Fcst Towell histórico", "Fcst Cliente histórico"], rows: history.filter(inScope).map(row => [row.chain, row.product.description, row.product_id, row.period.slice(0, 7), row.exact?.SALES ?? row.SALES, row.exact?.ORDER ?? row.ORDER, row.exact?.DELIVERY ?? row.DELIVERY, row.exact?.FCST_TOWELL_HISTORY ?? null, row.exact?.FCST_CLIENTE_HISTORY ?? null]) },
    { name: "Inventario", title: "Saldo agregado de inventario", note: "Piezas por cadena, formato, UPC y mes. Una ausencia permanece vacía. Cantidades como texto decimal exacto.", headers: ["Cadena", "Formato", "Producto", "ID producto", "UPC", "Periodo", "Tipo compra", "Piezas"], rows: inventory.filter(inScope).map(row => [scope.get(row.product_id)!.chain, row.format, scope.get(row.product_id)!.product.description, row.product_id, row.upc, row.period.slice(0, 7), row.purchase_type, row.quantity_text]) },
  ];
}
