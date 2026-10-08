import type { PreviewCandidate, PreviewJob, PreviewScope } from "./forecast-preview";
import { productCandidate } from "./ml-preview-data";
import { getStatisticalCandidates, retrospectiveMetric } from "./statistical-preview-data";

// Only the rolling-origin contract may supply executive retrospective metrics.
export function currentRetrospectivePreview(job: PreviewJob | null, scope: PreviewScope | null): boolean {
  const supported = ["-operational-preview-2-retrospective", "-operational-preview-3-selection-e1"];
  return Boolean(supported.some(suffix => job?.engine_version?.endsWith(suffix) && scope?.engine_version?.endsWith(suffix)) &&
    scope?.evaluation_mode === "RETROSPECTIVE_EVALUATION");
}

export function previewMetrics(scope: PreviewScope, productId: string | null) {
  const product = productId ? scope.products?.find(p => p.product_id === productId) : null;
  const statModel = product?.horizons?.find(h => h.horizon === 1)?.statistical_model ?? product?.statistical_model;
  const stat = productId ? getStatisticalCandidates(scope, productId).find(c => c.model === statModel) : scope.statistical?.selected_metrics;
  const ml = productCandidate(scope.ml?.candidates.find(c => c.model === scope.ml?.leader), productId);
  const towell = productCandidate(scope.selection?.preview_leader ?? undefined, productId);
  return {
    stat: { model: statModel ?? (productId ? null : "Selección por producto"), candidate: stat,
      wape: retrospectiveMetric(stat, "wape") ?? (!productId ? scope.statistical?.retrospective_wape ?? null : null),
      bias: retrospectiveMetric(stat, "bias") ?? (!productId ? scope.statistical?.retrospective_bias ?? null : null) },
    ml: { model: scope.ml?.leader ?? null, candidate: ml,
      wape: retrospectiveMetric(ml, "wape") ?? (!productId ? scope.ml?.retrospective_wape ?? null : null),
      bias: retrospectiveMetric(ml, "bias") ?? (!productId ? scope.ml?.retrospective_bias ?? null : null) },
    towell: { model: scope.selection?.preview_leader?.model ?? scope.selection?.preview_leader?.strategy ?? null, candidate: towell,
      wape: retrospectiveMetric(towell, "wape"), bias: retrospectiveMetric(towell, "bias") },
  };
}

export function previewCandidateLabel(candidate?: PreviewCandidate | null): string {
  const model = candidate?.model?.replace(/\s+/g, " ").trim();
  const strategy = candidate?.strategy?.replace(/\s+/g, " ").trim();
  return model || strategy || "—";
}

export function distinctChallenger(leader?: PreviewCandidate | null, challenger?: PreviewCandidate | null): PreviewCandidate | null {
  const leaderLabel = previewCandidateLabel(leader);
  const challengerLabel = previewCandidateLabel(challenger);
  if (leaderLabel === "—" || challengerLabel === "—") return null;
  return leaderLabel.toLowerCase() === challengerLabel.toLowerCase() ? null : challenger ?? null;
}
