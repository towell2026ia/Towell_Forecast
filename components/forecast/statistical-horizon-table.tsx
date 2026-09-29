import type { PreviewHorizon } from "@/lib/forecast-preview";
import { periodLabel, quantity } from "@/lib/forecast-chart-data";
import { ForecastGrid } from "./forecast-horizon-table";
export function StatisticalHorizonTable({ horizons }: { horizons: PreviewHorizon[] }) {
  return <section><h3 className="mb-3 font-semibold">Forecast estadístico H1–H12</h3><ForecastGrid headers={["Horizonte", "Periodo", "Modelo", "Forecast Estadístico"]} rows={horizons.map(h => [`H${h.horizon}`, periodLabel(h.target_period), h.statistical_model ?? "—", quantity(h.statistical_value)])}/></section>;
}
