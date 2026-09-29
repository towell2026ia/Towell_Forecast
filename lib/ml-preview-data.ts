import type { PreviewCandidate } from "./forecast-preview";
export function productCandidate(candidate: PreviewCandidate | undefined, productId?: string | null): PreviewCandidate | undefined {
  if (!candidate || !productId) return candidate;
  const metrics = candidate.by_product?.find(p => p.product_id === productId);
  return { ...candidate, retrospective_wape: null, retrospective_bias: null, retrospective_mae: null,
    retrospective_rmse: null, retrospective_stability: null, observations: undefined, by_horizon: [], ...metrics };
}
