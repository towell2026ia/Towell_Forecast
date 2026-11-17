import { comparableForecastRows, confirmedMissingTowellHistory, matchesOfficialSource, matrixTotal, reportSeries, type ReportSnapshot } from "./forecast-reporting";
import { dashboardLabel, dashboardSeries, type periodsDashboard } from "./periods-dashboard";
import type { PlanningSheet } from "./planning-results";
import type { HistoricalRow, OperationalInventory } from "./supabase/types";
import { forecastSourceAvailable, forecastHistoryAvailable, forecastSourceState, forecastUnavailableMessage } from "./official-sales-source";
import { forecastComposition } from "./forecast-composition";

export function operationalPeriodsSheets(snapshot: ReportSnapshot, dashboard: ReturnType<typeof periodsDashboard>, history: HistoricalRow[], inventory: OperationalInventory[], historyComplete = false): PlanningSheet[] {
  const context = snapshot.sourceContext;
  if (!context) throw new Error("official_source_unavailable");
  if (forecastSourceState(context) === "INVALID") throw new Error("OFFICIAL_SOURCE_MISMATCH");
  if (!dashboard.sourceContext || !matchesOfficialSource(dashboard.sourceContext, context)) throw new Error("official_source_changed");
  const historyAvailable = forecastHistoryAvailable(context);
  const scope = new Map(dashboard.matrix.rows.map(row => [row.product.id, row]));
  const series = Object.keys(dashboardSeries) as (keyof typeof dashboardSeries)[];
  const forecast = Object.keys(reportSeries) as (keyof typeof reportSeries)[];
  const comparable = comparableForecastRows(dashboard.matrix.rows, dashboard.matrix.periods);
  const horizonKey = (calculationId: string, chainId: string, productId: string, period: string) => JSON.stringify([calculationId, chainId, productId, period.slice(0, 7)]);
  const horizonIndex = new Map<string, ReportSnapshot["horizons"][number]>();
  const selectedCalculations = new Map(dashboard.matrix.rows.filter(row => row.calculation).map(row => [row.calculation!.id, row.product]));
  for (const horizon of snapshot.horizons) {
    const selectedProduct = selectedCalculations.get(horizon.calculation_id);
    if (!selectedProduct) continue;
    if (horizon.chain_id !== selectedProduct.chain_id || horizon.product_id !== selectedProduct.id) throw new Error("report_horizon_lineage");
    const key = horizonKey(horizon.calculation_id, horizon.chain_id, horizon.product_id, horizon.target_period);
    if (horizonIndex.has(key)) throw new Error("report_horizon_lineage");
    horizonIndex.set(key, horizon);
  }
  const inScope = (row: { product_id: string; chain_id: string }) => scope.get(row.product_id)?.product.chain_id === row.chain_id;
  return [
    { name: "Trazabilidad", title: "Consulta operativa de Períodos", note: "Todas las cantidades comerciales están en piezas.", headers: ["Campo", "Valor"], rows: [
      ["Fuente oficial SHA-256", context.source_hash], ["Actualización", context.generation], ["Primer periodo", context.first_period], ["Último periodo", context.last_period], ["Corte del cálculo", dashboard.cutoff],
      ...(context.sales_training_cutoff && context.sales_training_cutoff !== dashboard.cutoff ? [["Cierre de ventas acreditado", context.sales_training_cutoff]] : []),
      ...(context.forecast_contract ? [["Objetivo", context.objective!], ["Métrica", context.target_metric!], ["Unidad", context.unit!], ["Contrato", context.forecast_contract], ["Hoja objetivo", context.target_sheet!], ["SHA-256 del maestro", context.target_file_sha256!], ["Cierre de Pedido", context.training_cutoff ?? "Sin acreditar"], ["Estado del cálculo", forecastSourceAvailable(context) ? "Disponible" : forecastUnavailableMessage(context)], ["Fuente de inventario", context.inventory_source!]] : []),
      ...(context.sales_contract ? [["Fuente de ventas", context.sales_source!], ["Contrato de ventas", context.sales_contract], ["Archivo de ventas SHA-256", context.sales_file_sha256!], ["Hoja de ventas", context.sales_sheet!], ["Ventas disponibles", historyAvailable ? "Sí" : forecastUnavailableMessage(context)], ["Fuente de inventario", context.inventory_source!]] : []),
      ["Productos", scope.size], ["Productos con cálculo", dashboard.available], ["Productos con decisión", dashboard.adopted],
      ["Variantes comunes a tres motores", comparable.length], ["Variantes comunes · IDs", comparable.map(row => `${row.product.chain_id}/${row.product.id}`).join(";")],
      ["Pares Pedido/Entrega", dashboard.kpis.paired], ["Pares esperados", dashboard.kpis.expectedPairs],
      ["Cobertura Fill Rate", dashboard.kpis.expectedPairs === 0 ? "Sin alcance" : dashboard.kpis.paired < dashboard.kpis.expectedPairs ? "Parcial" : "Completa"], ["Fill Rate (%)", dashboard.kpis.fillRate],
    ] },
    { name: "Comparativa", title: "Series de la gráfica de Períodos", note: "Mismos puntos y cobertura de la consulta visible. Los tres subtotales comparables usan una única intersección de variantes. El inventario corresponde al saldo de cada mes. Fill Rate (%) divide Entrega entre Pedido de los mismos pares comparables.", headers: ["Periodo", ...series.flatMap(key => [dashboardLabel(key, dashboard.objective ?? ""), `Cobertura ${dashboardLabel(key, dashboard.objective ?? "")}`]), "Estadístico · mismas variantes", "ML · mismas variantes", "Ensemble · mismas variantes", "Cobertura · mismas variantes", "Fill Rate (%)", "Cobertura Fill Rate", "Pedido comparable", "Entrega comparable"], rows: dashboard.points.map(point => {
      const future = point.horizon !== null && dashboard.matrix.periods.includes(point.period);
      return [point.period, ...series.flatMap(key => [point.coverage[key].exact ?? point.values[key], `${point.coverage[key].covered}/${point.coverage[key].eligible}`]),
        ...(["STATISTICAL", "ML", "ENSEMBLE"] as const).map(family => future ? matrixTotal(comparable, point.period, family).value : null), `${future ? comparable.length : 0}/${scope.size}`,
        point.fillRate?.value ?? null, `${point.fillRate?.covered ?? 0}/${point.fillRate?.eligible ?? scope.size}`, point.fillRate?.orderedExact ?? point.fillRate?.ordered ?? null, point.fillRate?.deliveredExact ?? point.fillRate?.delivered ?? null];
    }) },
    { name: "Cálculos almacenados", title: "Matriz por producto", note: "Resultados congelados y composición literal por variante. El componente ML del Ensemble puede diferir del ganador ML individual. No se agregan pesos entre productos.", headers: ["Cadena", "Producto", "ID producto", "Cálculo", "ID cálculo", "Periodo", ...forecast.map(key => reportSeries[key]), "Método estadístico", "Peso estadístico", "Método ML del Ensemble", "Componente ML del Ensemble", "Peso ML", "Estado composición", "Motivo composición", "Versión motor", "Fuente SHA-256", "Actualización fuente", "Método ML individual"], rows: dashboard.matrix.rows.flatMap(row => dashboard.matrix.periods.map(period => {
      const horizon = row.calculation ? horizonIndex.get(horizonKey(row.calculation.id, row.product.chain_id, row.product.id, period)) : undefined;
      const composition = horizon && row.calculation ? forecastComposition(horizon, { engine_version: row.calculation.engine_version, source_context: row.calculation.input_snapshot?.source_context }, context) : null;
      return [row.chain, row.product.description, row.product.id, row.calculation?.calculation_code ?? null, row.calculation?.id ?? null, period, ...forecast.map(key => row.values[period]?.[key] ?? null),
        composition?.statisticalMethod ?? null, composition?.statisticalWeight ?? null, composition?.mlMethod ?? null, composition?.mlComponent ?? null, composition?.mlWeight ?? null,
        composition?.label ?? (confirmedMissingTowellHistory(row, context, history, inventory, historyComplete) ? (context.forecast_contract ? "Sin historial de pedidos; sin pronóstico" : "Sin historial Towell; sin pronóstico") : "Sin cálculo compatible"), composition?.reason ?? null, composition?.engineVersion ?? null, composition?.sourceHash ?? null, composition?.generation ?? null, horizon?.ml_model ?? null];
    })) },
    ...(context.forecast_contract ? [{ name: "Alcance", title: "Productos seleccionados", note: "Estado del cálculo para cada producto incluido en la consulta.", headers: ["Cadena", "Modelo", "Presentación", "Color", "Producto", "ID producto", "Objetivo", "Estado"], rows: [...scope.values()].map(row => [row.chain, row.product.commercial?.model ?? null, row.product.commercial?.size ?? null, row.product.commercial?.color ?? null, row.product.description, row.product.id, context.objective!, row.calculation ? row.calculation.calculation_code : !forecastSourceAvailable(context) ? forecastUnavailableMessage(context) : "Sin cálculo compatible"]) }] : []),
    { name: "Histórico comercial", title: "Series comerciales por producto y mes", note: "Las cantidades de origen se conservan como texto decimal exacto. Incluye los periodos históricos consultados para la comparación.", headers: ["Cadena", "Producto", "ID producto", "Periodo", "Venta", "Pedido", "Entrega", "Fcst Towell histórico", "Fcst Cliente histórico"], rows: (historyAvailable ? history.filter(inScope) : []).map(row => [row.chain, row.product.description, row.product_id, row.period.slice(0, 7), row.exact?.SALES ?? row.SALES, row.exact?.ORDER ?? row.ORDER, row.exact?.DELIVERY ?? row.DELIVERY, row.exact?.FCST_TOWELL_HISTORY ?? null, row.exact?.FCST_CLIENTE_HISTORY ?? null]) },
    { name: "Inventario", title: "Saldo agregado de inventario", note: "Piezas por cadena, formato, UPC y mes. Una ausencia permanece vacía. Cantidades como texto decimal exacto.", headers: ["Cadena", "Formato", "Producto", "ID producto", "UPC", "Periodo", "Tipo compra", "Piezas"], rows: inventory.filter(inScope).map(row => [scope.get(row.product_id)!.chain, row.format, scope.get(row.product_id)!.product.description, row.product_id, row.upc, row.period.slice(0, 7), row.purchase_type, row.quantity_text]) },
  ];
}
