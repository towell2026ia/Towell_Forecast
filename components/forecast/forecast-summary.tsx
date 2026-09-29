import { Card, CardContent } from "@/components/ui/card";
import { periodLabel, quantity, type Horizon } from "@/lib/forecast-chart-data";
import type { PreviewScope } from "@/lib/forecast-preview";

export const humanStatus = (status?: string) => ({ COMPLETED: "Disponible", NOT_ELIGIBLE: "No elegible", READY_PREVIEW: "Vista previa lista", PREVIEW: "Vista previa lista", FAILED: "Error de cálculo", PROVISIONAL_TEMPORAL_UNKNOWN: "Evidencia temporal no certificada", ACTIVE: "Activo", COLD_START: "Arranque reciente", INSUFFICIENT: "Histórico insuficiente", INACTIVE: "Inactivo", "PRE-LAUNCH": "Prelanzamiento" }[status ?? ""] ?? status ?? "Sin vista previa calculada");
export function coverage(count?: number, total?: number) { return total == null || count == null ? "—" : `${count} de ${total}${total > 0 ? ` · ${(100 * count / total).toFixed(1)}%` : ""}`; }
export function ForecastSummary({ scope, horizons }: { scope: PreviewScope | null; horizons: Horizon[] }) {
  const first = horizons.find(h => h.horizon === 1);
  const total = scope?.eligibility?.evaluated;
  const leader = scope?.selection?.preview_leader;
  const cards = [
    ["Datos reales hasta", periodLabel(scope?.latest_actual_period ?? scope?.issue_period), "Corte operacional independiente de filtros"],
    ["Fcst Towell H1", first ? `${quantity(first.forecast_towell)} pzas` : "—", first ? periodLabel(first.target_period) : "Sin horizonte disponible para este filtro"],
    ["Estadístico", coverage(scope?.eligibility?.stat_eligible, total), humanStatus(scope?.statistical?.status)],
    ["Machine Learning", coverage(scope?.eligibility?.ml_eligible, total), humanStatus(scope?.ml?.status)],
    ["Preview Leader", leader?.model ?? leader?.strategy ?? "—", "Selección preview · No publicado"],
  ];
  return <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">{cards.map(([label, value, detail]) => <Card key={label} className="border-slate-200 shadow-sm"><CardContent className="p-4"><p className="text-xs font-medium text-slate-500">{label}</p><p className="mt-3 text-xl font-semibold tracking-tight text-slate-950">{value}</p><p className="mt-2 text-xs text-slate-500">{detail}</p></CardContent></Card>)}</div>;
}
