"use client";

import { useCallback, useState } from "react";
import { Database } from "lucide-react";
import type { DatabaseSection } from "@/lib/forecast-reporting";
import { useForecastFilters, useRead } from "./forecast-towell-app";
import { ReportPaging, ReportSelect, ReportState, ReportTable, reportNumber } from "./report-controls";

const sections: { value: DatabaseSection; label: string }[] = [
  { value: "history", label: "Información histórica" }, { value: "observations", label: "Observaciones comerciales" },
  { value: "catalog", label: "Productos y cadenas" }, { value: "calculations", label: "Cálculos C1-Cn" },
  { value: "models", label: "Resultados de modelos" }, { value: "decisions", label: "Decisiones Forecast Towell" },
  { value: "research", label: "Investigaciones Deep Research" }, { value: "performance", label: "Evaluaciones de desempeño" },
];
export function DatabaseView() {
  const { filters } = useForecastFilters();
  const [section, setSection] = useState<DatabaseSection>("history");
  return <section className="space-y-5"><div className="flex items-center gap-3"><Database className="size-6 text-blue-700"/><h1 className="text-2xl font-semibold text-slate-950">Base de Datos</h1><span className="ml-auto text-xs text-slate-500">Solo lectura</span></div><ReportSelect label="Sección" value={section} onChange={v => setSection(v as DatabaseSection)} options={sections}/><DatabasePages key={`${section}/${JSON.stringify(filters)}`} section={section}/></section>;
}
function DatabasePages({ section }: { section: DatabaseSection }) {
  const { repository, filters } = useForecastFilters();
  const [page, setPage] = useState(0), [size, setSize] = useState<50 | 100 | 250>(50);
  const read = useRead(useCallback(() => repository.getDatabasePage ? repository.getDatabasePage(section, filters, page, size) : Promise.reject(new Error("database_unavailable")), [repository, section, filters, page, size]));
  return <><ReportState loading={read.loading} error={read.error} empty={read.data?.rows.length === 0}/>{read.data && read.data.rows.length > 0 && <ReportTable label={sections.find(s => s.value === section)!.label} headers={[...read.data.headers, "Trazabilidad"]} rows={read.data.rows.map(r => ({ id: r.id, cells: [...r.cells.map((cell, i) => <span key={i} className="block max-w-72 break-words">{cell === null ? "Sin dato" : typeof cell === "number" ? reportNumber(cell) : cell}</span>), r.detail ? <details key="detail" className="min-w-28"><summary className="cursor-pointer">Ver detalle</summary><pre className="mt-3 max-h-72 w-72 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(r.detail, null, 2)}</pre></details> : "Sin dato"] }))}/>}
    <ReportPaging page={page + 1} previous={() => setPage(p => p - 1)} next={() => setPage(p => p + 1)} hasMore={Boolean(read.data?.hasMore)} loading={read.loading} size={size} setSize={v => { setSize(v); setPage(0); }}/>
  </>;
}
