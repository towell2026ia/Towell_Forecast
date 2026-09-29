import type { PreviewCandidate } from "@/lib/forecast-preview";
import { percent, quantity } from "@/lib/forecast-chart-data";
import { metricEvidence } from "@/lib/statistical-preview-data";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export function StatisticalCandidateTable({ candidates, selected }: { candidates: PreviewCandidate[]; selected: string | null }) {
  return <section><h3 className="mb-3 font-semibold">Comparación de modelos</h3>{candidates.length ? <div className="overflow-x-auto rounded-xl border bg-white"><Table><TableHeader><TableRow>{["Modelo", "WAPE validación", "Bias", "Observaciones", "Selección"].map(h => <TableHead key={h} className="whitespace-nowrap">{h}</TableHead>)}</TableRow></TableHeader><TableBody>{candidates.map((c, i) => <TableRow key={`${c.model}/${i}`}><TableCell>{c.model ?? "—"}</TableCell><TableCell title={metricEvidence}>{percent(c.validation_wape)}</TableCell><TableCell title={metricEvidence}>{percent(c.validation_bias)}</TableCell><TableCell>{quantity(c.observations)}</TableCell><TableCell>{c.model === selected ? <span className="rounded-full bg-blue-50 px-2 py-1 text-xs text-blue-800">Seleccionado</span> : c.status ?? "Candidato"}</TableCell></TableRow>)}</TableBody></Table></div> : <p className="text-sm text-slate-500">Sin candidatos retrospectivos para este producto.</p>}</section>;
}
