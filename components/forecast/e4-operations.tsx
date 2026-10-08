"use client";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import type { CalculationDetail, CalculationHorizon, CalculationSummary, PreviewClient, SelectionEvent } from "@/lib/forecast-preview";

type Candidate = "STATISTICAL" | "ML" | "ENSEMBLE";
const names: Record<Candidate, string> = { STATISTICAL: "Estadístico", ML: "Machine Learning", ENSEMBLE: "Ensemble" };
const reasons = ["Información directa del cliente", "Promoción comercial", "Cambio de precio", "Desabasto esperado",
  "Apertura/cierre de tiendas", "Cambio de distribución", "Pedido extraordinario", "Evento especial", "Experiencia comercial", "Otro"];
const fmt = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("es-MX", { maximumFractionDigits: 2 });

function CalculationCurveChart({ detail, compare }: { detail: CalculationDetail; compare: CalculationDetail | null }) {
  const rows = [...detail.horizons].sort((a, b) => a.horizon - b.horizon);
  const selected = detail.selection_events.at(-1);
  const previous = compare?.selection_events.at(-1);
  const previousByPeriod = new Map(previous?.selected_curve.map(row => [row.target_period.slice(0, 7), Number(row.value)]) ?? []);
  const actualByPeriod = new Map<string, { value: number; version: number }>();
  for (const evaluation of [...detail.live_evaluations, ...(compare?.live_evaluations ?? [])]) {
    const period = String(evaluation.target_period ?? "").slice(0, 7);
    const version = Number(evaluation.sale_version_no ?? 0);
    const value = Number(evaluation.actual_sale);
    if (period && Number.isFinite(value) && version > (actualByPeriod.get(period)?.version ?? 0))
      actualByPeriod.set(period, { value, version });
  }
  const curves = [
    { name: "Estadístico", color: "#2563eb", values: rows.map(row => row.statistical_value) },
    { name: "ML", color: "#a855f7", values: rows.map(row => row.ml_value) },
    { name: "Ensemble", color: "#e78b19", values: rows.map(row => row.ensemble_value) },
    { name: selected ? `Towell ${detail.calculation_code}` : "Towell sin decisión", color: "#111827",
      values: rows.map(row => selected?.selected_curve.find(item => item.horizon === row.horizon)?.value ?? null) },
    ...(previous ? [{ name: `Towell ${compare?.calculation_code}`, color: "#64748b",
      values: rows.map(row => previousByPeriod.get(row.target_period.slice(0, 7)) ?? null) }] : []),
    { name: "Venta real", color: "#059669", values: rows.map(row => actualByPeriod.get(row.target_period.slice(0,7))?.value ?? null) },
  ];
  const maxima = curves.flatMap(curve => curve.values).filter((value): value is number => value !== null && Number.isFinite(value));
  const ceiling = Math.max(1, ...maxima) * 1.08;
  const x = (index: number) => 54 + index * 66;
  const y = (value: number) => 222 - value / ceiling * 190;
  const segments = (values: (number | null)[]) => {
    const result: string[] = []; let current: string[] = [];
    values.forEach((value, index) => {
      if (value === null || !Number.isFinite(value)) { if (current.length > 1) result.push(current.join(" ")); current = []; }
      else current.push(`${x(index)},${y(value)}`);
    });
    if (current.length > 1) result.push(current.join(" "));
    return result;
  };
  return <div className="rounded-xl border border-slate-200 p-3" role="img" aria-label={`Curvas H1 a H12 de ${detail.calculation_code}; Estadístico, ML, Ensemble${selected ? ", Forecast Towell seleccionado" : ""}${previous ? ` y ${compare?.calculation_code}` : ""}; Venta real cuando existe`}>
    <p className="text-sm font-semibold">Curvas congeladas H1–H12</p>
    <div className="mt-2 flex flex-wrap gap-3 text-xs">{curves.filter(curve => curve.values.some(value => value !== null)).map(curve => <span key={curve.name} className="flex items-center gap-1"><span className="inline-block h-0.5 w-4" style={{ background: curve.color }}/>{curve.name}</span>)}</div>
    <svg className="mt-2 w-full" viewBox="0 0 850 258" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      {[0, .25, .5, .75, 1].map(fraction => <g key={fraction}><line x1="46" x2="797" y1={222-190*fraction} y2={222-190*fraction} stroke="#e2e8f0"/><text x="42" y={226-190*fraction} textAnchor="end" fontSize="9" fill="#64748b">{fmt(ceiling*fraction)}</text></g>)}
      {curves.flatMap(curve => segments(curve.values).map((points, index) => <polyline key={`${curve.name}-${index}`} points={points} fill="none" stroke={curve.color} strokeWidth={curve.name.startsWith("Towell") ? 3 : 2} strokeDasharray={curve.name === `Towell ${compare?.calculation_code}` ? "5 4" : undefined}/>))}
      {rows.map((row, index) => <text key={row.horizon} x={x(index)} y="242" textAnchor="middle" fontSize="9" fill="#64748b">H{row.horizon}</text>)}
    </svg>
    <p className="text-xs text-slate-500">Líneas ausentes = candidato no elegible; la línea Towell sólo aparece después de una decisión. {previous ? "La curva anterior se alinea por periodo calendario, no por número de horizonte." : ""}</p>
  </div>;
}

export function E4Operations({ mode, client, chainId, productId, canWrite, canDecide, children }: {
  mode: "decision" | "history"; client: PreviewClient; chainId: string | null; productId: string | null;
  canWrite: boolean; canDecide: boolean; children?: React.ReactNode;
}) {
  const [rows, setRows] = useState<CalculationSummary[]>([]);
  const [current, setCurrent] = useState<(SelectionEvent & { calculation_code: string; issue_period: string }) | null>(null);
  const [detail, setDetail] = useState<CalculationDetail | null>(null);
  const [compare, setCompare] = useState<CalculationDetail | null>(null);
  const [tab, setTab] = useState<"calculations" | "official">("calculations");
  const [candidate, setCandidate] = useState<Candidate>("STATISTICAL");
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const [recalcReason, setRecalcReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    if (chainId && productId && client.calculations && client.currentSelection) {
      void Promise.all([client.calculations(chainId, productId), client.currentSelection(chainId, productId)])
        .then(([list, selection]) => { if (alive) { setRows(list); setCurrent(selection); } })
        .catch(() => { if (alive) setError("No fue posible consultar los cálculos."); });
    }
    return () => { alive = false; };
  }, [client, chainId, productId, revision]);
  useEffect(() => {
    let alive = true;
    const target = rows.find(row => row.id === current?.calculation_id) ?? rows[0];
    if (!detail && target && client.calculationDetail) void client.calculationDetail(target.id)
      .then(async result => { if (alive) { setDetail(result); const family = result.suggested_reference?.family?.toUpperCase();
        if (family === "STATISTICAL" || family === "ML" || family === "ENSEMBLE") setCandidate(family);
        if (mode === "history") {
          const previous = rows.find(row => row.id !== target.id && row.status === "SUPERSEDED");
          if (previous) try { const old = await client.calculationDetail!(previous.id); if (alive) setCompare(old); } catch { /* comparison remains optional */ }
        }
      } })
      .catch(() => { if (alive) setError("No fue posible abrir el cálculo."); });
    return () => { alive = false; };
  }, [client, rows, current, detail, mode]);
  async function open(id: string) {
    if (!client.calculationDetail) return;
    setError("");
    try { const result = await client.calculationDetail(id); setDetail(result);
      const family = result.suggested_reference?.family?.toUpperCase();
      if (family === "STATISTICAL" || family === "ML" || family === "ENSEMBLE") setCandidate(family);
      setReason(""); setComment(""); }
    catch { setError("No fue posible abrir el cálculo."); }
  }
  async function calculate() {
    if (!chainId || !productId || !client.createCalculation || !client.create || !client.status || !client.result) return;
    if (rows.length && !recalcReason.trim()) { setError("Selecciona un motivo de recálculo."); return; }
    setBusy(true); setError("");
    try {
      const job = await client.create(chainId, productId);
      let status = job;
      for (let attempt = 0; attempt < 60 && !["READY_PREVIEW", "READY_PROVISIONAL", "NOT_ELIGIBLE", "FAILED"].includes(status.status); attempt++) {
        await new Promise(resolve => setTimeout(resolve, 1500));
        status = await client.status(job.job_id);
      }
      if (status.status !== "READY_PREVIEW") throw new Error("El motor no entregó un preview elegible. No se creó un cálculo.");
      const result = await client.result(job.job_id, productId);
      const scope = result.scopes.find(row => row.chain_id === chainId);
      if (!scope?.preview_id) throw new Error("Falta el identificador del preview. No se creó un cálculo.");
      const id = await client.createCalculation(chainId, productId, scope.preview_id, rows.length ? recalcReason : undefined);
      setRevision(value => value + 1);
      await open(id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "No se pudo calcular el forecast."); }
    finally { setBusy(false); }
  }
  async function decide() {
    if (!detail?.id || !client.selectCalculation) return;
    setBusy(true); setError("");
    try {
      await client.selectCalculation(detail.id, candidate, reason || undefined, comment || undefined);
      window.dispatchEvent(new Event("towell-selection-changed"));
      setRevision(value => value + 1);
      await open(detail.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "No se pudo guardar la decisión."); }
    finally { setBusy(false); }
  }
  async function chooseCompare(id: string) {
    if (!client.calculationDetail) return;
    try { setCompare(id ? await client.calculationDetail(id) : null); }
    catch { setError("No se pudo abrir el cálculo comparativo."); }
  }
  if ((!chainId || !productId) && mode === "history") return <div className="space-y-3"><div className="flex gap-2"><Button variant={tab === "calculations" ? "default" : "outline"} onClick={() => setTab("calculations")}>Cálculos C1/C2/C3</Button><Button variant={tab === "official" ? "default" : "outline"} onClick={() => setTab("official")}>Versiones oficiales</Button></div>{tab === "official" ? children : <p className="rounded-xl border bg-white p-5 text-sm text-slate-600">Selecciona cadena y producto para consultar sus cálculos.</p>}</div>;
  if (!chainId || !productId) return <p className="rounded-xl border bg-white p-5 text-sm text-slate-600">Selecciona una cadena y un producto para consultar sus cálculos C1/C2/C3.</p>;
  const suggested = String(detail?.suggested_reference?.family ?? "").toUpperCase();
  const chosen = detail?.selection_events?.at(-1);
  const hasCandidate = (kind: Candidate) => detail?.horizons.length === 12 && detail.horizons.every(row =>
    row[`${kind === "STATISTICAL" ? "statistical" : kind === "ML" ? "ml" : "ensemble"}_value`] != null);
  const metrics = detail?.candidate_metrics ?? {};
  return <section className="space-y-4" aria-label={mode === "decision" ? "Decisión Forecast Towell" : "Historial de cálculos"}>
    {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}
    {mode === "history" && <div className="flex gap-2"><Button variant={tab === "calculations" ? "default" : "outline"} onClick={() => setTab("calculations")}>Cálculos C1/C2/C3</Button><Button variant={tab === "official" ? "default" : "outline"} onClick={() => setTab("official")}>Versiones oficiales</Button></div>}
    {mode === "history" && tab === "official" ? children : <>
      <div className="rounded-xl border bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-semibold">{mode === "decision" ? "Decisión del Forecast Towell" : "Cálculos inmutables"}</h2><p className="mt-1 text-sm text-slate-500">C1/C2/C3 son cálculos operativos. Un vintage y un Champion tienen certificación y publicación separadas.</p></div>
          {canWrite && <Button onClick={() => void calculate()} disabled={busy}>{busy ? "Calculando H1–H12…" : rows.length ? "Recalcular forecast H1–H12" : "Calcular forecast H1–H12"}</Button>}</div>
        {rows.length > 0 && canWrite && <label className="mt-3 block text-sm">Motivo del recálculo
          <select className="mt-1 w-full rounded border p-2" value={recalcReason} onChange={event => setRecalcReason(event.target.value)}><option value="">Selecciona un motivo</option>{["Nuevo mes disponible", "Nueva información del cliente", "Cambio comercial", "Cambio importante de demanda", "Revisión gerencial", "Nueva evidencia externa", "Corrección de datos", "Otro"].map(value => <option key={value}>{value}</option>)}</select></label>}
        <p className="mt-3 text-sm"><strong>Forecast Towell vigente:</strong> {current ? `${current.calculation_code} · ${names[current.selected_candidate]}` : "Sin decisión"}. Un nuevo cálculo no sustituye la decisión vigente.</p>
        <div className="mt-4 overflow-x-auto"><table className="min-w-full text-left text-sm"><thead><tr className="border-b"><th className="p-2">Cálculo</th><th className="p-2">Corte</th><th className="p-2">Referencia*</th><th className="p-2">Estado</th><th className="p-2">Fecha</th><th className="p-2">Abrir</th></tr></thead><tbody>{rows.map(row => <tr className="border-b" key={row.id}><td className="p-2">{row.calculation_code}</td><td className="p-2">{row.issue_period.slice(0,7)}</td><td className="p-2">{row.suggested_reference?.model ?? "—"}</td><td className="p-2">{current?.calculation_code === row.calculation_code ? "Vigente" : row.status}</td><td className="p-2">{row.created_at.slice(0,10)}</td><td className="p-2"><Button variant="outline" size="sm" onClick={() => void open(row.id)}>Ver detalle</Button></td></tr>)}</tbody></table>{!rows.length && <p className="mt-3 text-slate-500">Aún no existe C1 para este producto.</p>}</div>
      </div>
      {detail && <div className="space-y-4 rounded-xl border bg-white p-5 text-sm"><h3 className="font-semibold">{detail.calculation_code} · {detail.issue_period.slice(0,7)} · {detail.status}</h3>
        <CalculationCurveChart detail={detail} compare={mode === "history" ? compare : null}/>
        <p>Referencia sugerida: {detail.suggested_reference?.model ?? "Sin recomendación comparable"} * · No es Champion publicado.</p>
        <p>Lineage: preview {detail.preview_id ?? "—"} · motor {detail.engine_version} · Git {detail.git_sha} · SHA datos {detail.data_snapshot_hash}</p>
        <p>Investigación: {detail.research_snapshot ? "snapshot conservado; no altera matemáticas" : "sin snapshot"}. Observaciones de entrada: {Array.isArray(detail.input_snapshot?.observation_ids) ? detail.input_snapshot.observation_ids.length : 0}.</p>
        {detail.horizons[0]?.ensemble_value != null && <p>Ensemble congelado: {fmt((detail.horizons[0].statistical_weight ?? 0)*100)}% Estadístico + {fmt((detail.horizons[0].ml_weight ?? 0)*100)}% {detail.horizons[0].ensemble_ml_model ?? "ML del scope"}. Su componente ML puede ser distinto del ganador ML individual.</p>}
        <div className="overflow-x-auto"><table className="min-w-full text-left"><thead><tr className="border-b"><th className="p-2">Candidato</th><th className="p-2">Modelo / estrategia</th><th className="p-2">WAPE retro producto</th><th className="p-2">Bias retro</th><th className="p-2">H1</th><th className="p-2">Estado</th></tr></thead><tbody>{(["STATISTICAL","ML","ENSEMBLE"] as Candidate[]).map(kind => { const metric = metrics[kind]; const field = `${kind === "STATISTICAL" ? "statistical" : kind === "ML" ? "ml" : "ensemble"}_value` as keyof CalculationHorizon; return <tr className="border-b" key={kind}><td className="p-2">{names[kind]}</td><td className="p-2">{metric?.model ?? metric?.strategy ?? "—"}</td><td className="p-2">{fmt(metric?.wape)}%</td><td className="p-2">{fmt(metric?.bias)}%</td><td className="p-2">{fmt(detail.horizons[0]?.[field] as number | null)}</td><td className="p-2">{hasCandidate(kind) ? "Elegible" : "Sin curva"}</td></tr>; })}</tbody></table></div>
        {mode === "decision" && canDecide && detail.status !== "SUPERSEDED" && <div className="space-y-3 rounded-xl border border-blue-100 bg-blue-50 p-4"><h4 className="font-semibold">¿Qué curva utilizará Towell?</h4><div className="flex flex-wrap gap-4">{(["STATISTICAL","ML","ENSEMBLE"] as Candidate[]).map(kind => <label className="flex items-center gap-2" key={kind}><input type="radio" name="forecast-candidate" value={kind} disabled={!hasCandidate(kind)} checked={candidate === kind} onChange={() => setCandidate(kind)}/>{names[kind]} {suggested === kind ? "*" : ""}</label>)}</div>
          {candidate !== suggested && <label className="block">Motivo obligatorio<select className="mt-1 w-full rounded border p-2" value={reason} onChange={event => setReason(event.target.value)}><option value="">Selecciona un motivo</option>{reasons.map(value => <option key={value}>{value}</option>)}</select></label>}
          {reason === "Otro" && <label className="block">Comentario obligatorio<textarea className="mt-1 w-full rounded border p-2" value={comment} onChange={event => setComment(event.target.value)}/></label>}
          <Button onClick={() => void decide()} disabled={busy || !hasCandidate(candidate) || !["READY_FOR_DECISION","DECIDED"].includes(detail.status) || (candidate !== suggested && !reason) || (reason === "Otro" && !comment.trim())}>Adoptar como Forecast Towell</Button><p className="text-xs">La decisión conserva H1–H12 exactos y no publica Champion. Una corrección crea otro evento.</p></div>}
        {chosen && <p><strong>Última decisión en este cálculo:</strong> {names[chosen.selected_candidate]} · {chosen.selected_at.slice(0,10)} · {chosen.decision_reason ?? "Siguió la sugerencia"}</p>}
        <div className="overflow-x-auto"><table className="min-w-full text-left"><thead><tr className="border-b"><th className="p-2">H</th><th className="p-2">Periodo</th><th className="p-2">Stat</th><th className="p-2">ML</th><th className="p-2">Ensemble</th><th className="p-2">P10/P50/P90/P95</th><th className="p-2">Base de banda</th></tr></thead><tbody>{detail.horizons.map(row => <tr className="border-b" key={row.horizon}><td className="p-2">H{row.horizon}</td><td className="p-2">{row.target_period.slice(0,7)}</td><td className="p-2">{fmt(row.statistical_value)}</td><td className="p-2">{fmt(row.ml_value)}</td><td className="p-2">{fmt(row.ensemble_value)}</td><td className="p-2">{row.band_basis === "INSUFFICIENT" ? "Sin evidencia" : [row.p10,row.p50,row.p90,row.p95].map(fmt).join(" / ")}</td><td className="p-2">{row.band_basis} · {row.band_observations}</td></tr>)}</tbody></table></div>
        {mode === "history" && rows.length > 1 && <div><label>Comparar con <select className="ml-2 rounded border p-2" value={compare?.id ?? ""} onChange={event => void chooseCompare(event.target.value)}><option value="">Sin comparación</option>{rows.filter(row => row.id !== detail.id).map(row => <option key={row.id} value={row.id}>{row.calculation_code}</option>)}</select></label>{compare && <div className="mt-3 overflow-x-auto"><table className="min-w-full text-left"><thead><tr className="border-b"><th className="p-2">Periodo</th><th className="p-2">{detail.calculation_code} Towell</th><th className="p-2">{compare.calculation_code} Towell</th><th className="p-2">Variación</th><th className="p-2">Venta real</th></tr></thead><tbody>{detail.horizons.map(row => { const now = detail.selection_events.at(-1)?.selected_curve.find(item => item.target_period.slice(0,7) === row.target_period.slice(0,7))?.value;
          const before = compare.selection_events.at(-1)?.selected_curve.find(item => item.target_period.slice(0,7) === row.target_period.slice(0,7))?.value;
          const actual = [...detail.live_evaluations, ...compare.live_evaluations].filter(item => String(item.target_period).slice(0,7) === row.target_period.slice(0,7))
            .sort((a,b) => Number(b.sale_version_no) - Number(a.sale_version_no))[0];
          return <tr className="border-b" key={row.horizon}><td className="p-2">{row.target_period.slice(0,7)}</td><td className="p-2">{fmt(now)}</td><td className="p-2">{fmt(before)}</td><td className="p-2">{now != null && before != null && before !== 0 ? `${fmt((now-before)/before*100)}%` : "—"}</td><td className="p-2">{fmt(actual ? Number(actual.actual_sale) : null)}</td></tr>; })}</tbody></table><p className="mt-2 text-xs text-slate-500">Se comparan decisiones congeladas por periodo calendario. Sin decisión o real confirmado, la celda queda vacía; no se sustituye por la curva estadística.</p></div>}</div>}
      </div>}
    </>}
  </section>;
}
