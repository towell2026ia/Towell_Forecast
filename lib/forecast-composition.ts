import { matchesOfficialSource } from "./forecast-reporting";
import type { OperationalReadContext } from "./supabase/types";
import { forecastSourceAvailable, forecastUnavailableMessage } from "./official-sales-source";

type CompositionHorizon = {
  statistical_value: number | null; ensemble_value?: number | null; forecast_towell?: number | null;
  statistical_model?: string | null; statistical_weight?: number | null; ml_weight?: number | null;
  ensemble_ml_model?: string | null; ensemble_ml_component?: number | null;
};
export function forecastComposition(horizon: CompositionHorizon,
  provenance: { engine_version?: string | null; source_context?: unknown }, expectedSource?: OperationalReadContext) {
  const literal = (value: number | null | undefined) => value != null && Number.isFinite(value) && value >= 0 ? value : null;
  const method = (value: string | null | undefined) => typeof value === "string" && value.trim() ? value : null;
  const source = provenance.source_context && typeof provenance.source_context === "object"
    ? provenance.source_context as Partial<OperationalReadContext> : null;
  const sourceHash = typeof source?.source_hash === "string" && /^[0-9a-f]{64}$/.test(source.source_hash) ? source.source_hash : null;
  const generation = Number.isSafeInteger(source?.generation) && source!.generation! > 0 ? source!.generation! : null;
  const result = literal(horizon.ensemble_value === undefined ? horizon.forecast_towell : horizon.ensemble_value);
  const statistical = literal(horizon.statistical_value), statisticalWeight = literal(horizon.statistical_weight), mlWeight = literal(horizon.ml_weight);
  const statisticalMethod = method(horizon.statistical_model), mlMethod = method(horizon.ensemble_ml_model);
  const mlComponent = literal(horizon.ensemble_ml_component);
  const fields = { statistical, statisticalMethod, statisticalWeight, mlMethod, mlComponent, mlWeight, result,
    engineVersion: method(provenance.engine_version), sourceHash, generation };
  if (!forecastSourceAvailable(source) || expectedSource && !forecastSourceAvailable(expectedSource))
    return { ...fields, status: "SOURCE_MISMATCH", label: expectedSource?.forecast_contract ? "Fuente de pedidos no disponible" : "Fuente de ventas no disponible", reason: forecastUnavailableMessage(expectedSource ?? source) };
  if (expectedSource && !matchesOfficialSource(provenance.source_context, expectedSource))
    return { ...fields, status: "SOURCE_MISMATCH", label: "Fuente incompatible", reason: "La composición pertenece a otra fuente; no se utiliza como respaldo de la consulta." };
  if (result === null) return { ...fields, status: "NO_RESULT", label: "Sin resultado Ensemble", reason: "No hay una cantidad Ensemble guardada para este horizonte." };
  if (!sourceHash || generation === null || !fields.engineVersion || statistical === null || !statisticalMethod || statisticalWeight === null || mlWeight === null)
    return { ...fields, status: "UNRECORDED", label: "Composición no acreditada", reason: "Faltan métodos, pesos, versión o fuente guardados. No se infieren desde otro candidato." };
  if (statisticalWeight > 1 || mlWeight > 1 || Math.abs(statisticalWeight + mlWeight - 1) > 1e-12)
    return { ...fields, status: "INCONSISTENT", label: "Composición inconsistente", reason: "Los pesos guardados no representan una composición completa." };
  if (mlWeight > 0 && (!mlMethod || mlComponent === null))
    return { ...fields, status: "UNRECORDED", label: "Composición no acreditada", reason: "Falta el método o la cantidad ML realmente utilizada. El ganador ML individual no lo sustituye." };
  // The frozen E4 contract permits at most two cents from its rounded inputs.
  // This check describes the saved result; it never recomputes or replaces it.
  if (Math.abs(result - (statistical * statisticalWeight + (mlComponent ?? 0) * mlWeight)) > .0200000001)
    return { ...fields, status: "INCONSISTENT", label: "Composición inconsistente", reason: "La cantidad guardada no corresponde a los componentes y pesos conservados." };
  if (statisticalWeight === 1 && mlWeight === 0)
    return { ...fields, status: "STATISTICAL_ONLY", label: "Estadístico solo", reason: "El resultado utiliza 100% Estadístico y 0% ML según los pesos guardados." };
  return { ...fields, status: "RECORDED", label: "Composición guardada", reason: "Métodos, componentes, pesos, versión y fuente conservados para este producto y horizonte." };
}
