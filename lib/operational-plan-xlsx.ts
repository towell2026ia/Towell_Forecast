import { addReportMonth, buildForecastMatrix, emptyReportChoice, matchesOfficialSource, type ForecastMatrixRow, type ReportSnapshot } from "./forecast-reporting";
import type { PlanningSheet } from "./planning-results";
import { verifiedLocalChampions } from "./local-champion";
import { modelKey } from "./commercial-filters";

type Cell = PlanningSheet["rows"][number][number];
type Family = "CHAMPION" | "STATISTICAL" | "ML" | "ENSEMBLE" | "COLD_START" | "COMMERCIAL_PLAN";
const names: Record<Family, string> = { CHAMPION: "Champion elegido", STATISTICAL: "Estadístico", ML: "Machine Learning", ENSEMBLE: "Ensemble", COLD_START: "Cold Start", COMMERCIAL_PLAN: "Plan comercial" };
const families: Family[] = ["STATISTICAL", "ML", "ENSEMBLE", "COLD_START", "COMMERCIAL_PLAN"];
const period = (value: unknown) => typeof value === "string" && /^20\d{2}-(0[1-9]|1[0-2])$/.test(value) ? value : null;
const literal = (value: unknown): string | null => {
  if (value == null) return null;
  if ((typeof value === "number" || typeof value === "string") && /^\d+(?:\.\d+)?$/.test(String(value)) && Number.isFinite(Number(value))) return String(value);
  throw new Error("report_plan_quantity");
};

/** Sum decimal literals before the one conversion required by Excel's numeric cell. */
export function exactPlanSum(values: (string | null)[]): string | null {
  const present = values.filter((v): v is string => v !== null);
  if (!present.length) return null;
  if (present.some(v => !/^-?\d+(?:\.\d+)?$/.test(v))) throw new Error("report_plan_quantity");
  const scale = Math.max(...present.map(v => v.split(".")[1]?.length ?? 0));
  const sum = present.reduce((total, value) => {
    const sign = BigInt(value.startsWith("-") ? -1 : 1);
    const [whole, fraction = ""] = value.replace(/^-/, "").split(".");
    return total + sign * BigInt(whole + fraction.padEnd(scale, "0"));
  }, BigInt(0));
  const sign = sum < BigInt(0) ? "-" : "", digits = (sum < BigInt(0) ? -sum : sum).toString().padStart(scale + 1, "0");
  return scale ? `${sign}${digits.slice(0, -scale)}.${digits.slice(-scale)}` : `${sign}${digits}`;
}
const numberCell = (value: string | null): Cell => {
  if (value === null) return null;
  const [whole, fraction = ""] = value.replace(/^-/, "").split(".");
  const significant = `${whole}${fraction.replace(/0+$/, "")}`.replace(/^0+/, "").length;
  // Excel numeric cells have fifteen significant digits. Preserve an exact
  // literal rather than silently round a longer commercial quantity.
  return significant > 15 ? value : Number(value);
};
const unique = (values: string[]) => [...new Set(values)].sort().join("; ");
const upc = (row: ForecastMatrixRow) => row.product.identifiers.find(pair => pair[1])?.[1] ?? null;

export function planWindow(snapshot: ReportSnapshot): string[] {
  const source = snapshot.sourceContext;
  const run = snapshot.planningRun;
  if (run && (!source || run.source_hash !== source.source_hash || run.generation !== source.generation || run.status !== "READY")) throw new Error("report_plan_window");
  const start = period(run?.window_start ?? source?.planning_start), end = period(run?.window_end ?? source?.planning_end);
  if (!start || !end) return [];
  const months = Array.from({ length: 12 }, (_, index) => addReportMonth(start, index));
  if (months[11] !== end) throw new Error("report_plan_window");
  return months;
}

type Curve = { values: (string | null)[]; method: string | null; run: string | null; emission: string | null; source: string | null; status: string; consolidatable?: boolean };
function indexHorizons(snapshot: ReportSnapshot) {
  const index = new Map<string, ReportSnapshot["horizons"]>();
  for (const row of snapshot.horizons) { const group = index.get(row.calculation_id); if (group) group.push(row); else index.set(row.calculation_id, [row]); }
  return index;
}
const coldConsolidatable = (candidate: unknown) => {
  if (!candidate || typeof candidate !== "object") return false;
  const effect = (candidate as Record<string, unknown>).incremental_effect;
  const status = effect && typeof effect === "object" ? (effect as Record<string, unknown>).status : effect;
  return ["INCREMENTAL", "VERIFIED_INCREMENTAL", "SUBSTITUTION_ACCOUNTED"].includes(String(status));
};
function curve(snapshot: ReportSnapshot, row: ForecastMatrixRow, months: string[], family: Family,
  champions: ReturnType<typeof verifiedLocalChampions>["products"], index: ReturnType<typeof indexHorizons>): Curve {
  const calculation = row.calculation, input = calculation?.input_snapshot;
  const result: Curve = { values: months.map(() => null), method: null,
    run: typeof input?.planning_run_id === "string" ? input.planning_run_id : null,
    emission: typeof input?.emitted_at === "string" ? input.emitted_at : calculation?.created_at ?? null,
    source: snapshot.sourceContext?.source_hash ?? null, status: "Sin cantidad acreditada" };
  if (family === "CHAMPION") {
    const champion = champions.find(c => c.product.chain_id === row.product.chain_id && c.product.product_id === row.product.id);
    if (!champion) return result;
    return { values: months.map(month => literal(champion.product.horizons.find(h => h.target_period.slice(0, 7) === month)?.value)),
      method: `${names[champion.candidate_family as Family]} · ${champion.product.model}`, run: champion.run_id,
      emission: champion.run_emitted_at, source: champion.source_context.source_hash, status: "Champion elegido",
      ...(champion.candidate_family === "COLD_START" ? { consolidatable: (champion.product as unknown as Record<string, unknown>).consolidation_allowed === true } : {}) };
  }
  if (family === "COLD_START") {
    if (row.provisional) return { ...result, values: months.map(month => literal(row.provisional!.horizons.find(h => h.target_period.slice(0, 7) === month)?.value)),
      method: row.provisional.model, run: row.provisional.job_id, emission: row.provisional.created_at, status: "Cold Start provisional; sin adopción automática",
      consolidatable: coldConsolidatable(row.provisional) };
  }
  const candidate = calculation?.candidate_metrics[family];
  if (family === "COMMERCIAL_PLAN" || family === "COLD_START") {
    if (!candidate) return result;
    return { ...result, method: candidate.model, values: months.map(month => literal(candidate.horizons.find(h => h.target_period.slice(0, 7) === month)?.value)), status: `${names[family]} sin adoptar`,
      ...(family === "COLD_START" ? { consolidatable: coldConsolidatable(candidate) } : {}) };
  }
  const horizons = calculation ? index.get(calculation.id) ?? [] : [];
  if (horizons.some(h => h.product_id !== row.product.id || h.chain_id !== row.product.chain_id)
    || new Set(horizons.map(h => h.target_period.slice(0, 7))).size !== horizons.length) throw new Error("report_horizon_lineage");
  const field = family === "STATISTICAL" ? "statistical_value" : family === "ML" ? "ml_value" : "ensemble_value";
  result.values = months.map(month => literal(horizons.find(h => h.target_period.slice(0, 7) === month)?.[field]));
  result.method = unique(horizons.map(h => family === "STATISTICAL" ? h.statistical_model ?? "" : family === "ML" ? h.ml_model ?? "" :
    [h.statistical_model, h.ensemble_ml_model].filter(Boolean).join(" + ")).filter(Boolean)) || candidate?.model || null;
  result.status = "Alternativa sin adoptar";
  return result;
}

function groupIdentity(row: ForecastMatrixRow, commercialUnit: boolean) {
  const c = row.product.commercial;
  // An accredited commercial-unit UUID already separates its chain/format.
  // Parent scopes with missing format, or missing model/presentation, cannot
  // merge unrelated identities. Normalization is exact NFC/case/whitespace.
  return [row.product.chain_id, c?.format ? modelKey(c.format) : commercialUnit ? "" : `Producto ${row.product.id}`,
    c?.model ? modelKey(c.model) : `Producto ${row.product.id}`,
    c?.size ? modelKey(c.size) : c?.not_applicable?.includes("size") ? "No aplica" : `Producto ${row.product.id}`,
    c?.product_type ? modelKey(c.product_type) : "", row.product.category_id ?? ""];
}

/** A directorial projection of frozen quantities. It never executes or adopts a forecast. */
export function operationalPlanSheets(snapshot: ReportSnapshot, selectedRows: ForecastMatrixRow[], previousSnapshot?: ReportSnapshot): PlanningSheet[] {
  const months = planWindow(snapshot);
  if (!months.length) return [];
  const rows = [...new Map(selectedRows.map(row => [`${row.product.chain_id}/${row.product.id}`, row])).values()];
  if (rows.length !== selectedRows.length) throw new Error("report_plan_duplicate_product");
  const catalog = new Map(snapshot.products.map(product => [product.id, product.chain_id]));
  for (const row of rows) {
    if (catalog.get(row.product.id) !== row.product.chain_id) throw new Error("report_plan_scope");
    const c = row.calculation, p = row.provisional;
    if (c && (c.product_id !== row.product.id || c.chain_id !== row.product.chain_id || !matchesOfficialSource(c.input_snapshot.source_context, snapshot.sourceContext!)
      || snapshot.planningRun && c.input_snapshot.planning_run_id !== snapshot.planningRun.run_id)) throw new Error("report_horizon_lineage");
    if (p && (p.product_id !== row.product.id || p.chain_id !== row.product.chain_id || !matchesOfficialSource(p.source_context, snapshot.sourceContext!))) throw new Error("report_horizon_lineage");
  }
  const champions = snapshot.localChampions ? verifiedLocalChampions(snapshot, snapshot.localChampions).products : [];
  const horizons = indexHorizons(snapshot);
  const groups = new Map<string, ForecastMatrixRow[]>();
  const commercialUnits = new Set(snapshot.chains.filter(chain => chain.scopeType === "COMMERCIAL_UNIT").map(chain => chain.id));
  for (const row of rows) { const key = JSON.stringify(groupIdentity(row, commercialUnits.has(row.product.chain_id))); groups.set(key, [...(groups.get(key) ?? []), row]); }
  const identityHeaders = ["Cadena", "Formato", "Modelo", "Presentación", "Línea", "Familia / categoría", "Plan", "Estado", "Corrida", "Emisión", "Fuente SHA-256", "Productos esperados"];
  const monthly: Cell[][] = [], bimonthly: Cell[][] = [], details: Cell[][] = [];
  const bimesters = Array.from({ length: 6 }, (_, index) => `${months[index * 2]} / ${months[index * 2 + 1]}`);
  for (const members of groups.values()) {
    const first = members[0], commercial = first.product.commercial;
    const available = families.filter(f => members.some(row => curve(snapshot, row, months, f, champions, horizons).values.some(v => v !== null)));
    const chosen = members.filter(row => champions.some(c => c.product.product_id === row.product.id && c.product.chain_id === row.product.chain_id)).length;
    const outputFamilies: Family[] = [...(chosen ? ["CHAMPION" as const] : []), ...(chosen === members.length ? [] : available)];
    if (!outputFamilies.length) outputFamilies.push("STATISTICAL");
    for (const family of outputFamilies) {
      const curves = members.map(row => curve(snapshot, row, months, family, champions, horizons));
      const values = months.map((_, index) => exactPlanSum(curves.map(c => c.consolidatable === false ? null : c.values[index])));
      const coverage = months.map((_, index) => curves.filter(c => c.consolidatable !== false && c.values[index] !== null).length);
      const complete = coverage.every(count => count === members.length);
      const label = family === "CHAMPION" ? names[family] : `${names[family]} · alternativa sin Champion`;
      const identity: Cell[] = [first.chain, commercial?.format ?? null, commercial?.model ?? `Producto ${first.product.id}`, commercial?.size ?? null,
        commercial?.product_type ?? null, first.category, label, curves.some(c => c.consolidatable === false && c.values.some(v => v !== null)) ? "Cold Start en detalle; efecto incremental no acreditado" : complete ? "Completo" : coverage.some(Boolean) ? "TOTAL PARCIAL" : "Sin dato",
        unique(curves.map(c => c.run ?? "").filter(Boolean)) || null, unique(curves.map(c => c.emission ?? "").filter(Boolean)) || null,
        snapshot.sourceContext!.source_hash, members.length];
      const total = exactPlanSum(values);
      monthly.push([...identity, ...values.map(numberCell), numberCell(total), ...coverage.map(c => `${c}/${members.length}`)]);
      const pairs = bimesters.map((_, index) => exactPlanSum(values.slice(index * 2, index * 2 + 2)));
      bimonthly.push([...identity, ...pairs.map(numberCell), numberCell(exactPlanSum(pairs)), ...bimesters.map((_, index) => `${coverage[index * 2] + coverage[index * 2 + 1]}/${members.length * 2}`)]);
      members.forEach((row, index) => {
        const c = curves[index];
        for (let m = 0; m < months.length; m++) {
          const frozen = row.calculation ? horizons.get(row.calculation.id)?.find(h => h.target_period.slice(0, 7) === months[m]) : undefined;
          const champion = family === "CHAMPION" ? champions.find(c => c.product.product_id === row.product.id && c.product.chain_id === row.product.chain_id) : null;
          const effective = champion?.candidate_family ?? family;
          const method = effective === "STATISTICAL" ? frozen?.statistical_model ?? c.method : effective === "ML" ? frozen?.ml_model ?? c.method : c.method;
          const cold = effective === "COLD_START" ? row.provisional as unknown as Record<string, unknown> | null : null;
          const metadata = (value: unknown): Cell => value == null ? null : typeof value === "string" ? value : JSON.stringify(value);
          details.push([row.chain, row.product.commercial?.format ?? null, row.product.commercial?.model ?? null,
          row.product.commercial?.size ?? null, row.product.commercial?.color ?? null, upc(row), row.product.id, label, months[m], numberCell(c.values[m]),
          c.values[m] === null ? "Sin dato" : c.status, method, c.run, row.calculation?.calculation_code ?? null, c.emission,
          snapshot.planningRun?.issue_period ?? snapshot.sourceContext!.training_cutoff ?? null,
          snapshot.sourceContext!.observed_cutoff ?? null, snapshot.sourceContext!.source_hash, snapshot.sourceContext!.generation, row.calculation?.engine_version ?? row.provisional?.engine_version ?? null,
          effective === "ENSEMBLE" ? frozen?.statistical_weight ?? null : null, effective === "ENSEMBLE" ? frozen?.ensemble_ml_model ?? null : null,
          effective === "ENSEMBLE" ? frozen?.ensemble_ml_component ?? null : null, effective === "ENSEMBLE" ? frozen?.ml_weight ?? null : null,
          metadata(cold?.admission), metadata(cold?.incremental_effect), metadata(cold?.comparables), c.consolidatable === false ? "No; sólo detalle individual" : c.values[m] !== null ? "Sí" : "Sin dato"]);
        }
      });
    }
  }
  const note = "PIEZAS. Cada alternativa es independiente; no sumar filas de distintos planes. Champion procede sólo de una elección guardada. Los subtotales parciales conservan sus faltantes; ausencia no equivale a cero. Se suman decimales antes de convertir a celda numérica. Cantidades de más de 15 cifras significativas se conservan como texto exacto.";
  const sheets: PlanningSheet[] = [
    { name: "Plan mensual", title: "Plan de Pedido · doce meses", note, headers: [...identityHeaders, ...months, "Total de ventana", ...months.map(m => `Cobertura ${m}`)], rows: monthly },
    { name: "Plan bimestral", title: "Plan de Pedido · seis bimestres", note: `${note} Un bimestre suma dos meses de la misma corrida; no representa otra revisión.`, headers: [...identityHeaders, ...bimesters, "Total de ventana", ...bimesters.map(m => `Cobertura ${m}`)], rows: bimonthly },
    { name: "Detalle del plan", title: "Pedido por producto, mes y alternativa", note, headers: ["Cadena", "Formato", "Modelo", "Presentación", "Color", "UPC", "ID producto", "Plan", "Mes objetivo", "Piezas", "Estado", "Método efectivo", "Corrida", "Cálculo", "Emisión", "Corte del cálculo", "Cierre observado", "Fuente SHA-256", "Actualización", "Versión motor", "Peso estadístico del Ensemble", "Método ML del Ensemble", "Componente ML del Ensemble", "Peso ML del Ensemble", "Admisión Cold Start", "Efecto incremental", "Comparables", "Consolidación permitida"], rows: details },
  ];
  if (previousSnapshot) {
    const currentSource = snapshot.sourceContext, previousSource = previousSnapshot.sourceContext;
    if (!currentSource || !previousSource || (["source_hash", "generation", "forecast_contract", "objective", "target_metric", "unit", "target_file_sha256"] as const)
      .some(field => currentSource[field] !== previousSource[field])) throw new Error("report_comparison_source");
    const allowed = new Map(rows.map(row => [row.product.id, row.product.chain_id]));
    if (previousSnapshot.products.some(p => allowed.has(p.id) && allowed.get(p.id) !== p.chain_id)) throw new Error("report_comparison_scope");
    const oldMonths = planWindow(previousSnapshot), oldRows = buildForecastMatrix({ ...previousSnapshot,
      products: previousSnapshot.products.filter(p => allowed.get(p.id) === p.chain_id) }, emptyReportChoice, [null, null]).rows;
    const oldChampions = previousSnapshot.localChampions ? verifiedLocalChampions(previousSnapshot, previousSnapshot.localChampions).products : [];
    const oldHorizons = indexHorizons(previousSnapshot);
    const comparisons: Cell[][] = [];
    for (const row of rows) for (const family of [...families, "CHAMPION" as const]) {
      const old = oldRows.find(r => r.product.id === row.product.id && r.product.chain_id === row.product.chain_id);
      const now = curve(snapshot, row, months, family, champions, horizons), before = old ? curve(previousSnapshot, old, oldMonths, family, oldChampions, oldHorizons) : null;
      for (let i = 0; i < months.length; i++) {
        const oldIndex = oldMonths.indexOf(months[i]);
        if (oldIndex < 0) continue;
        const previous = before?.values[oldIndex] ?? null, current = now.values[i];
        if (previous === null && current === null) continue;
        const delta = current === null || previous === null ? null : exactPlanSum([current, `-${previous}`]);
        comparisons.push([row.chain, row.product.commercial?.format ?? null, row.product.commercial?.model ?? null, upc(row), row.product.id, names[family], months[i],
          before?.run ?? null, now.run, numberCell(previous), numberCell(current), numberCell(delta), previous === null || current === null ? "Cobertura parcial; diferencia no evaluable" : "Mismo producto, familia y mes objetivo"]);
      }
    }
    sheets.push({ name: "Comparación versiones", title: "Diferencias por mes objetivo común", note: "Se comparan las cantidades de la misma identidad y alternativa por mes objetivo, nunca H1 contra H1 de ventanas diferentes. No se recalculan métodos ni métricas.", headers: ["Cadena", "Formato", "Modelo", "UPC", "ID producto", "Alternativa", "Mes objetivo", "Corrida anterior", "Corrida seleccionada", "Cantidad anterior", "Cantidad seleccionada", "Diferencia", "Comparabilidad"], rows: comparisons });
  }
  return sheets;
}
