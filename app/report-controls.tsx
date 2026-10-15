"use client";

import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ReportChoice, ReportSnapshot } from "@/lib/forecast-reporting";

export const reportNumber = (value: number | null | undefined) => value == null ? "Sin dato" : value.toLocaleString("es-MX", { maximumFractionDigits: 2 });
export const reportPercent = (value: number | null | undefined) => value == null ? "Sin dato" : `${value.toFixed(2)}%`;
export function ReportSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: { value: string; label: string }[] }) {
  return <label className="min-w-0 text-sm font-medium text-slate-700">{label}<select aria-label={label} value={value} onChange={e => onChange(e.target.value)} className="mt-2 block h-10 w-full min-w-0 rounded-md border border-slate-200 bg-white px-3 text-sm font-normal">{options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>;
}
export function CalculationFilters({ snapshot, choice, onChange, decisions = false }: { snapshot: ReportSnapshot; choice: ReportChoice; onChange: (value: ReportChoice) => void; decisions?: boolean }) {
  const versions = [...new Set(snapshot.calculations.map(c => c.calculation_code))].sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)));
  const issues = [...new Set([...snapshot.calculations.map(c => c.issue_period.slice(0, 7)), ...snapshot.provisionals.map(p => p.issue_period)])].sort().reverse();
  return <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
    <ReportSelect label="Versión de cálculo" value={choice.version ?? "latest"} onChange={v => onChange({ ...choice, version: v === "latest" ? null : v, issue: null, calculationId: null })} options={[{ value: "latest", label: "Último compatible por producto" }, ...versions.map(v => ({ value: v, label: v }))]}/>
    <ReportSelect label="Corte del cálculo" value={choice.issue ?? "latest"} onChange={v => onChange({ ...choice, issue: v === "latest" ? null : v, calculationId: null })} options={[{ value: "latest", label: "Último corte disponible" }, ...issues.map(v => ({ value: v, label: v }))]}/>
    {decisions && <ReportSelect label="Estado de decisión" value={choice.decision} onChange={v => onChange({ ...choice, decision: v as ReportChoice["decision"] })} options={[{ value: "ALL", label: "Todos" }, { value: "PENDING", label: "Sin decisión" }, { value: "ADOPTED", label: "Adoptado" }]}/>}
  </div>;
}
export function ReportTable({ label, headers, rows }: { label: string; headers: ReactNode[]; rows: { id: string; cells: ReactNode[] }[] }) {
  return <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white"><table aria-label={label} className="w-full text-left text-sm"><thead className="bg-slate-50"><tr>{headers.map((h, i) => <th key={i} scope="col" className="whitespace-nowrap border-b px-4 py-3 font-semibold text-slate-700">{h}</th>)}</tr></thead><tbody>{rows.map(r => <tr key={r.id} className="border-b last:border-b-0 hover:bg-slate-50">{r.cells.map((c, i) => <td key={i} className="min-w-28 px-4 py-3 align-top text-slate-700">{c}</td>)}</tr>)}</tbody></table></div>;
}
export function ReportPaging({ page, previous, next, hasMore, loading, size, setSize }: { page: number; previous: () => void; next: () => void; hasMore: boolean; loading: boolean; size: number; setSize: (value: 50 | 100 | 250) => void }) {
  return <div className="mt-4 flex flex-wrap items-end justify-between gap-3"><ReportSelect label="Filas por página" value={String(size)} onChange={v => setSize(Number(v) as 50 | 100 | 250)} options={[50, 100, 250].map(v => ({ value: String(v), label: String(v) }))}/><div className="flex items-center gap-3"><Button variant="outline" disabled={page === 1 || loading} onClick={previous}><ChevronLeft className="size-4"/>Anterior</Button><span className="text-sm text-slate-500">Página {page}</span><Button variant="outline" disabled={!hasMore || loading} onClick={next}>Siguiente<ChevronRight className="size-4"/></Button></div></div>;
}
export function ReportState({ loading, error, empty }: { loading: boolean; error: boolean; empty?: boolean }) {
  if (error) return <p role="alert" className="border border-rose-200 bg-rose-50 p-5 text-sm text-rose-700">No fue posible consultar los datos y su trazabilidad.</p>;
  if (loading) return <p role="status" className="p-5 text-sm text-slate-500">Consultando datos autorizados...</p>;
  if (empty) return <p className="border border-slate-200 bg-white p-5 text-sm text-slate-500">Sin registros para estos filtros.</p>;
  return null;
}
