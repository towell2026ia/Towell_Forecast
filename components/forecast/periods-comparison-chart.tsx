"use client";

import { useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { dashboardSeries, dashboardLabel, shortPeriod, type DashboardPoint, type DashboardSeries } from "@/lib/periods-dashboard";
import { addReportMonth } from "@/lib/forecast-reporting";
import { planningSourceLabels } from "@/lib/planning-source-audit";
import { reportNumber, reportPercent } from "@/app/report-controls";

const initial = new Set<DashboardSeries>(["previous2", "previous1", "actual", "inventory", "STATISTICAL", "ML", "ENSEMBLE", "TOWELL"]);
type ChartSeries = DashboardSeries | "fillRate";
const fillRateColor = "#c83824";
function observedFill(point: DashboardPoint) {
  return point.horizon || point.outlook ? (point.fillRate?.reported ? point.fillRate : undefined) : point.fillRate;
}
export function PeriodsComparisonChart({ points, cutoff, planning, title = "Comparativo comercial 9+3", showFillRate = false, inventoryError = false, objective = "Venta" }: { points: DashboardPoint[]; cutoff: string | null; planning?: { unit: string; provisional: boolean; source: "Towell" | "Cliente" | "Pedidos" }; title?: string; showFillRate?: boolean; inventoryError?: boolean; objective?: string }) {
  const monthlyEnabled = showFillRate && !planning;
  const orders = !planning && objective === "Pedido";
  const available: ChartSeries[] = planning ? ["previous2", "previous1", "actual", ...(planning.provisional ? ["STATISTICAL" as const] : [])] : [...Object.keys(dashboardSeries) as DashboardSeries[], ...(monthlyEnabled ? ["fillRate" as const] : [])];
  const [shown, setShown] = useState<Set<ChartSeries>>(() => new Set<ChartSeries>(orders ? ["order", "inventory", "previous2", "previous1", "STATISTICAL", "ML", "ENSEMBLE", ...(monthlyEnabled ? ["fillRate" as const] : [])] : [...initial, ...(monthlyEnabled ? ["fillRate" as const] : [])].filter(key => available.includes(key))));
  const sourceLabel = planning ? planningSourceLabels[planning.source] : "";
  const planningLabels: Partial<Record<DashboardSeries, string>> = { previous2: `${sourceLabel} año −2`, previous1: `${sourceLabel} año −1`, actual: sourceLabel, STATISTICAL: "Estadístico provisional" };
  const label = (key: ChartSeries) => key === "fillRate" ? "Fill Rate (%)" : (planning && planningLabels[key]) || dashboardLabel(key, objective);
  const color = (key: ChartSeries) => key === "fillRate" ? fillRateColor : orders && key === "order" ? "#184a86" : dashboardSeries[key].color;
  const partialLabel = (key: ChartSeries) => {
    if (key === "fillRate") return null;
    if (!["STATISTICAL", "ML", "ENSEMBLE"].includes(key)) return null;
    const partial = points.filter(point => point.horizon).map(point => point.coverage[key]).filter(row => row.value !== null && row.covered < row.eligible);
    return partial.length ? `PARCIAL ${Math.min(...partial.map(row => row.covered))}/${Math.max(...partial.map(row => row.eligible))}` : null;
  };
  const records = points.map(p => ({ ...p, ...p.values, fillRate: observedFill(p)?.value ?? null }));
  const pendingOutlook = points.filter(point => point.outlook);
  const forecastMonths = points.filter(point => point.horizon);
  const historical = points.filter(point => !point.horizon && !point.outlook);
  const lastOrder = points.filter(point => (!point.horizon && !point.outlook || point.partialOrder) && point.values.order !== null).at(-1);
  const lastFill = points.filter(point => observedFill(point)?.value != null).at(-1);
  const fillStatus = lastFill
    ? `${lastFill.month} · ${reportPercent(lastFill.fillRate?.value)}${lastFill.fillRate?.source === "ESTADISTICA_BAASE" ? " · BAASE" : ""}${lastFill.fillRate?.partialPeriod ? " · mes parcial" : ""}`
    : historical.some(point => (point.fillRate?.covered ?? 0) > 0) ? "Sin dato · pedido comparable igual a cero"
    : historical.some(point => point.values.order !== null) && historical.every(point => point.values.delivery === null)
      ? "Sin dato · faltan entregas comparables" : "Sin dato · sin pares Pedido–Entrega";
  const maxFillRate = Math.max(100, ...records.flatMap(p => p.fillRate !== null && Number.isFinite(p.fillRate) ? [Math.ceil(p.fillRate / 10) * 10] : []));
  const visibleSeries = [...shown].filter(key => key !== "inventory" || !inventoryError);
  const tableSeries: ChartSeries[] = visibleSeries.filter(key => key !== "fillRate");
  if (monthlyEnabled && (!orders || shown.has("fillRate"))) tableSeries.push("fillRate");
  return <section aria-label="Gráfica comparativa comercial" className="min-w-0 border-y border-slate-200 bg-white py-5">
    <div className="flex flex-wrap items-center justify-between gap-2 px-4"><h2 className="text-lg font-semibold text-slate-950">{title}</h2><span className="text-xs text-slate-500">{planning?.unit ?? "PIEZAS"} · Corte {cutoff ?? "Sin dato"} · {planning ? "9 meses históricos / 3 posteriores" : orders && !cutoff ? "Cierre de Pedido pendiente" : "9 meses históricos / H1–H3 almacenados"}</span></div>
    {orders && <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 px-4 text-sm text-slate-700">
      {shown.has("order") && <span><strong>Pedido</strong> {lastOrder ? `${lastOrder.month}${lastOrder.partialOrder ? " (parcial)" : ""} · ${reportNumber(lastOrder.values.order)} piezas (${lastOrder.coverage.order.covered}/${lastOrder.coverage.order.eligible} productos)` : "Sin dato en los meses visibles"}</span>}
      {monthlyEnabled && shown.has("fillRate") && <span><strong>Fill Rate</strong> {fillStatus}</span>}
      {pendingOutlook.length > 0 && <span>Pronóstico {pendingOutlook[0].month} a {pendingOutlook[pendingOutlook.length - 1].month} · pendiente de cierre de Pedido</span>}
      {shown.has("ENSEMBLE") && forecastMonths.length > 0 && <span><strong>Ensemble provisional</strong>{" "}
        {forecastMonths.map(point => {
          const { value, covered, eligible } = point.coverage.ENSEMBLE;
          return `${point.month} · ${reportNumber(value)}${value !== null && covered < eligible ? ` (${covered}/${eligible})` : ""}`;
        }).join("  /  ")}
      </span>}
    </div>}
    <div className="mt-4 h-[360px] min-w-0 sm:h-[420px]" data-testid="periods-chart">
      <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 900, height: 420 }}>
        <ComposedChart data={records} margin={{ top: 12, right: 18, bottom: 8, left: 4 }} accessibilityLayer>
          <CartesianGrid stroke="#e8edf2" vertical={false}/><XAxis dataKey="month" interval={0} angle={-50} height={70} textAnchor="end" tickLine={false} axisLine={false} tick={{ fontSize: 10 }} tickFormatter={(month: string) => { const p = points.find(p => p.month === month); return p?.horizon ? `${month} H${p.horizon}` : month; }}/><YAxis yAxisId="pieces" width={monthlyEnabled ? 56 : 45} label={monthlyEnabled ? { value: "Piezas", angle: -90, position: "insideLeft", fontSize: 11 } : undefined} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} tickFormatter={v => Intl.NumberFormat("es-MX", { notation: "compact" }).format(v)}/>
          {monthlyEnabled && <YAxis yAxisId="fillRate" orientation="right" width={72} domain={[0, maxFillRate]} label={{ value: "Fill Rate (%)", angle: 90, position: "insideRight", fontSize: 11, fill: fillRateColor }} tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: fillRateColor }} tickFormatter={v => `${v}%`}/>}
          {((points[9]?.horizon && points[11]?.horizon) || pendingOutlook.length === 3) && <ReferenceArea yAxisId="pieces" x1={points[9].month} x2={points[11].month} fill="#e4f0e9" fillOpacity={0.55} label={{ value: planning ? (planning.provisional ? "Provisional" : "Sin Champion") : pendingOutlook.length ? "Pronóstico pendiente" : "H1–H3", position: "insideTop", fontSize: 11, fill: "#52665a" }}/>}
          <Tooltip content={({ active, label: month, payload }) => {
            if (!active || !monthlyEnabled && !payload?.length) return null;
            const point = points.find(p => p.month === month);
            return <div className="max-w-72 rounded-md border border-slate-200 bg-white p-3 text-xs shadow-md"><strong>{month}{point?.horizon ? ` · H${point.horizon}` : ""}</strong>{point && visibleSeries.map(key => {
              if (key === "fillRate") {
                const basis = observedFill(point);
                return <div key={key} className="mt-2 border-t border-slate-100 pt-2"><span style={{ color: fillRateColor }}>{label(key)}: {reportPercent(basis?.value)}</span><p>Pedido comparable{basis?.source === "ESTADISTICA_BAASE" ? " BAASE" : ""}: {basis?.orderedExact ?? reportNumber(basis?.ordered)} piezas</p><p>Entrega comparable{basis?.source === "ESTADISTICA_BAASE" ? " BAASE" : ""}: {basis?.deliveredExact ?? reportNumber(basis?.delivered)} piezas</p><p className="text-slate-500">Cobertura {basis?.covered ?? 0}/{basis?.eligible ?? point.coverage.order.eligible} pares Pedido/Entrega{basis?.covered && basis.covered < basis.eligible ? " · Cobertura parcial" : ""}{basis?.partialPeriod ? " · Mes parcial" : (point.horizon || point.outlook) && !basis ? " · Sin pares registrados" : ""}</p></div>;
              }
              const q = point.coverage[key];
              return <p key={key} className="mt-2"><span style={{ color: color(key) }}>{label(key)}{key === "order" && point.partialOrder ? " · captura parcial" : ""}{key === "previous1" || key === "previous2" ? ` · ${shortPeriod(addReportMonth(point.period, key === "previous1" ? -12 : -24))}` : ""}</span>: {q.exact ?? reportNumber(q.value)}<span className="block text-slate-500">Cobertura {q.covered}/{q.eligible}{q.covered && q.covered < q.eligible ? " · TOTAL PARCIAL" : ""}</span></p>;
            })}</div>;
          }}/>
          {(["previous2", "previous1", "inventory"] as const).filter(k => visibleSeries.includes(k)).map(k => <Bar key={k} yAxisId="pieces" dataKey={k} name={label(k)} fill={dashboardSeries[k].color} maxBarSize={26} isAnimationActive={false}/>)}
          {(Object.keys(dashboardSeries) as DashboardSeries[]).filter(k => !["previous2", "previous1", "inventory"].includes(k) && shown.has(k)).map(k => <Line key={k} yAxisId="pieces" dataKey={k} name={label(k)} stroke={color(k)} strokeWidth={orders && k === "order" ? 3.5 : k === "actual" ? 3 : 2} strokeDasharray={["actual", "order", "delivery"].includes(k) ? undefined : "6 4"} dot={orders && k === "order" ? { r: 4, fill: color(k), stroke: "white", strokeWidth: 1.5 } : { r: 3 }} connectNulls={false} isAnimationActive={false}/>)}
          {monthlyEnabled && shown.has("fillRate") && <Line yAxisId="fillRate" dataKey="fillRate" name="Fill Rate (%)" stroke={fillRateColor} strokeWidth={2.5} dot={{ r: 3 }} connectNulls={false} isAnimationActive={false}/>}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
    <fieldset className="flex flex-wrap gap-x-5 gap-y-3 px-4 pt-3"><legend className="sr-only">Series de la gráfica</legend>{available.map(key => <label key={key} className="flex min-h-8 items-center gap-2 text-xs text-slate-700"><input type="checkbox" aria-label={label(key)} disabled={inventoryError && key === "inventory"} checked={visibleSeries.includes(key)} onChange={() => setShown(previous => { const next = new Set(previous); if (next.has(key)) next.delete(key); else next.add(key); return next; })}/><span className="h-2 w-4 rounded-sm" style={{ background: color(key) }}/>{label(key)}{partialLabel(key) && <span className="text-amber-800">{partialLabel(key)}</span>}</label>)}</fieldset>
    {inventoryError && <p role="alert" className="mt-3 px-4 text-xs text-amber-800">Error de consulta de inventario. Actualiza la consulta para recuperar los saldos.</p>}
    {!planning && !orders && <p className="mt-3 px-4 text-xs text-slate-500">Inventario reportado en piezas por cadena, formato, UPC y mes.</p>}
    <details className="mt-4 px-4 text-xs text-slate-600"><summary className="cursor-pointer">Consultar las 12 posiciones y ausencias de dato</summary><div className="mt-3 overflow-x-auto"><table aria-label={monthlyEnabled ? "Datos de la gráfica Período" : "Datos de la gráfica 9+3"} className="w-full text-left"><thead><tr>{["Periodo", "H", ...tableSeries.map(label)].map(h => <th key={h} className="whitespace-nowrap p-2">{h}</th>)}</tr></thead><tbody>{points.map(p => <tr key={p.period}><th className="whitespace-nowrap p-2">{p.period}</th><td className="p-2">{p.horizon ? `H${p.horizon}` : p.outlook ? "Pronóstico pendiente" : orders && !cutoff ? "Capturado" : "Real"}</td>{tableSeries.map(k => <td key={k} className="whitespace-nowrap p-2">{k === "fillRate" ? <><span>{reportPercent(observedFill(p)?.value)}</span><span className="mt-1 block text-slate-500">Cobertura {p.fillRate?.covered ?? 0}/{p.fillRate?.eligible ?? p.coverage.order.eligible} pares{p.fillRate?.covered && p.fillRate.covered < p.fillRate.eligible ? " · Parcial" : ""}</span></> : p.coverage[k].exact ?? reportNumber(p.values[k])}</td>)}</tr>)}</tbody></table></div></details>
  </section>;
}
