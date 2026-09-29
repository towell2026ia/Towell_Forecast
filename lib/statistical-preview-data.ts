import { addMonth } from "./forecast-chart-data";
import type { PreviewCandidate, PreviewProduct, PreviewScope } from "./forecast-preview";
import type { Filters } from "./supabase/types";

export const metricEvidence = "No existe evidencia retrospectiva suficiente para esta métrica.";
export const finiteMetric = (value: number | null | undefined) => value != null && Number.isFinite(value) ? value : null;
const score = (value: number | null | undefined) => finiteMetric(value) ?? Infinity;
// Same comparison criteria as the backend; missing scores are not interpreted as zero.
export function compareStatisticalCandidates(a: PreviewCandidate, b: PreviewCandidate) {
  const aw = score(a.validation_wape), bw = score(b.validation_wape);
  if (aw !== bw) return aw < bw ? -1 : 1;
  const ab = Math.abs(score(a.validation_bias)), bb = Math.abs(score(b.validation_bias));
  if (ab !== bb) return ab < bb ? -1 : 1;
  return (a.model ?? "") < (b.model ?? "") ? -1 : (a.model ?? "") > (b.model ?? "") ? 1 : 0;
}
export function getStatisticalCandidates(scope: PreviewScope | null, productId: string) {
  return [...(scope?.statistical?.candidates ?? [])].filter(c => c.family === "statistical" && c.product_id === productId).sort(compareStatisticalCandidates);
}
export function selectedStatisticalModel(product?: PreviewProduct, issuePeriod?: string) {
  const rows = product?.horizons ?? [];
  const model = rows.find(h => h.horizon === 1)?.statistical_model;
  const consistent = Boolean(model && issuePeriod && rows.length === 12 && new Set(rows.map(h => h.horizon)).size === 12 && rows.every(h =>
    Number.isInteger(h.horizon) && h.horizon >= 1 && h.horizon <= 12 && h.statistical_model === model &&
    h.target_period === addMonth(issuePeriod!, h.horizon) && Number.isFinite(h.statistical_value)));
  return { model: model ?? null, consistent, horizons: consistent ? [...rows].sort((a, b) => a.horizon - b.horizon) : [] };
}
export function statisticalModelDistribution(scope: PreviewScope | null) {
  return Object.entries(scope?.statistical?.models ?? {}).map(([model, products]) => ({ model, products }))
    .sort((a, b) => b.products - a.products || (a.model < b.model ? -1 : a.model > b.model ? 1 : 0));
}
export function statisticalCoverage(scope: PreviewScope | null) {
  const total = finiteMetric(scope?.eligibility?.evaluated), eligible = finiteMetric(scope?.eligibility?.stat_eligible);
  return { total, eligible, percent: total != null && total > 0 && eligible != null ? 100 * eligible / total : null };
}
export const statisticalStatus = (status?: string) => ({ ACTIVE: "Activo", COLD_START: "Cold start", INACTIVE: "Inactivo", PRE_LAUNCH: "Pre-lanzamiento", "PRE-LAUNCH": "Pre-lanzamiento", INSUFFICIENT: "Historia insuficiente" }[status ?? ""] ?? status ?? "Sin estado disponible");
export function statisticalRanking(scope: PreviewScope | null, filters: Filters) {
  if (!scope || scope.chain_id !== filters.chainId) return [];
  const search = filters.search.trim().toLocaleLowerCase("es-MX");
  return (scope.products ?? []).filter(p => (!filters.productId || p.product_id === filters.productId) &&
    (!filters.categoryId || p.category_id === filters.categoryId) && (!search || `${p.description} ${p.product_code}`.toLocaleLowerCase("es-MX").includes(search)))
    .flatMap(product => {
      const candidates = getStatisticalCandidates(scope, product.product_id);
      if (!candidates.length) return [];
      const selection = selectedStatisticalModel(product, scope.issue_period);
      return [{ product, selection, candidate: candidates.find(c => c.model === selection.model) }];
    });
}
export function selectionExplanation(candidates: PreviewCandidate[], selected?: PreviewCandidate) {
  if (!selected?.model || finiteMetric(selected.validation_wape) === null || candidates.length === 0 || candidates[0] !== selected) return "El modelo seleccionado es el informado por E2. No hay evidencia comparable suficiente para explicar su selección.";
  const tied = candidates.filter(c => c.validation_wape === selected.validation_wape);
  if (tied.length === 1) return `${selected.model} obtuvo el menor WAPE de validación retrospectiva entre los candidatos comparables devueltos por E2.`;
  if (finiteMetric(selected.validation_bias) === null || tied.some(c => finiteMetric(c.validation_bias) === null)) return "E2 informa un empate en WAPE; no hay Bias suficiente para explicar el desempate.";
  const sameBias = tied.filter(c => Math.abs(c.validation_bias!) === Math.abs(selected.validation_bias!));
  return sameBias.length > 1 ? `${selected.model}: empate en WAPE y Bias absoluto; desempate por nombre según el orden del backend.` : `${selected.model}: empate en WAPE; seleccionado por menor Bias absoluto.`;
}
export function horizonAccuracy(candidate?: PreviewCandidate) {
  const rows = candidate?.validation_by_horizon ?? [];
  // Preserve only literal evidence. Duplicate/invalid horizon metadata fails closed.
  if (new Set(rows.map(r => r.horizon)).size !== rows.length || rows.some(r => !Number.isInteger(r.horizon) || r.horizon < 1 || r.horizon > 12)) return [];
  return [...rows].sort((a, b) => a.horizon - b.horizon).map(r => ({ ...r, wape: finiteMetric(r.wape) }));
}
