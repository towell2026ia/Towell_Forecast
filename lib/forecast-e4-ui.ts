"use client";
import { useEffect, useState } from "react";
import type { PreviewClient, SelectionEvent } from "./forecast-preview";
import type { Horizon } from "./forecast-chart-data";

export type CurrentForecastSelection = SelectionEvent & { calculation_code: string; issue_period: string };
export function useCurrentForecastSelection(client: PreviewClient | undefined, chainId: string | null, productId: string | null) {
  const [selection, setSelection] = useState<CurrentForecastSelection | null>(null);
  useEffect(() => {
    let alive = true;
    async function refresh() {
      if (!client?.currentSelection || !client?.vintageCapabilities || !chainId || !productId) {
        if (alive) setSelection(null);
        return;
      }
      try {
        const flags = await client.vintageCapabilities();
        const value = flags.forecast_selection ? await client.currentSelection(chainId, productId) : null;
        if (alive) setSelection(value);
      } catch { if (alive) setSelection(null); }
    }
    void refresh();
    window.addEventListener("towell-selection-changed", refresh);
    return () => { alive = false; window.removeEventListener("towell-selection-changed", refresh); };
  }, [client, chainId, productId]);
  return selection;
}
export function selectedForecastHorizons(selection: CurrentForecastSelection | null): Horizon[] {
  if (!selection || selection.selected_curve.length !== 12 || new Set(selection.selected_curve.map(row => row.horizon)).size !== 12) return [];
  return selection.selected_curve.map(row => ({ horizon: row.horizon, target_period: row.target_period.slice(0,7),
    forecast_towell: Number(row.value), level: "chain" as const, key: selection.calculation_id }))
    .filter(row => Number.isFinite(row.forecast_towell) && row.forecast_towell >= 0);
}
