"use client";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ForecastGrid } from "./forecast-horizon-table";
export function StatisticalModelDistribution({ rows }: { rows: { model: string; products: number }[] }) {
  return <section className="rounded-xl border bg-white p-4"><h3 className="mb-3 font-semibold">Distribución de modelos · scope completo</h3>{rows.length ? <><div className="h-[300px] md:h-[360px]" role="img" aria-label="Distribución de modelos por productos"><ResponsiveContainer width="100%" height="100%" minWidth={0}><BarChart data={rows} layout="vertical" margin={{ left: 10, right: 20 }}><CartesianGrid horizontal={false} stroke="#e2e8f0"/><XAxis type="number" allowDecimals={false}/><YAxis type="category" dataKey="model" width={130} tick={{ fontSize: 11 }}/><Tooltip/><Bar dataKey="products" name="Productos" fill="#175cd3" isAnimationActive={false}/></BarChart></ResponsiveContainer></div><ForecastGrid headers={["Modelo", "Productos"]} rows={rows.map(r => [r.model, r.products])}/></> : <p className="text-sm text-slate-500">Sin distribución estadística disponible.</p>}</section>;
}
