"use client";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useForecastFilters } from "./forecast-towell-app";
import type { VintageCapabilities } from "@/lib/forecast-preview";

export function CaptureCenterView() {
  const { repository, filters, profile } = useForecastFilters();
  const client = repository.previews;
  const [flags, setFlags] = useState<VintageCapabilities | null>(null);
  const [period, setPeriod] = useState("");
  const [order, setOrder] = useState("");
  const [sale, setSale] = useState("");
  const [delivery, setDelivery] = useState("");
  const [notes, setNotes] = useState("");
  const [reason, setReason] = useState("");
  const [draft, setDraft] = useState<string | null>(null);
  const [history, setHistory] = useState<{ observations: { id: string; metric_code: string; value: number; version_no: number; available_at: string | null }[] } | null>(null);
  const [metrics, setMetrics] = useState<Record<string, unknown> | null>(null);
  const [learning, setLearning] = useState<Record<string, unknown>[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    if (client?.vintageCapabilities) void client.vintageCapabilities().then(result => { if (alive) setFlags(result); })
      .catch(() => { if (alive) setFlags(null); });
    return () => { alive = false; };
  }, [client]);
  useEffect(() => {
    let alive = true;
    if (flags?.capture_center && filters.chainId && filters.productId && period && client?.captureHistory) {
      void client.captureHistory(filters.chainId, filters.productId, period).then(result => { if (alive) setHistory(result); })
        .catch(() => { if (alive) setMessage("No fue posible consultar versiones anteriores."); });
    }
    if (flags?.live_learning && filters.chainId && filters.productId && client?.liveMetrics) {
      void client.liveMetrics(filters.chainId, filters.productId).then(result => { if (alive) setMetrics(result); })
        .catch(() => { if (alive) setMetrics(null); });
      if (client.learning) void client.learning(filters.chainId, filters.productId)
        .then(events => { if (alive) setLearning(events); })
        .catch(() => { if (alive) setLearning([]); });
    }
    return () => { alive = false; };
  }, [client, flags?.capture_center, flags?.live_learning, filters.chainId, filters.productId, period, revision]);
  async function save() {
    if (!client?.saveCapture || !filters.chainId || !filters.productId || !period) return;
    setBusy(true); setMessage("");
    try {
      const session = await client.saveCapture({ chain_id: filters.chainId, product_id: filters.productId,
        period: `${period}-01`, order_value: Number(order), sale_value: Number(sale), delivery_value: Number(delivery),
        notes, correction_reason: reason || undefined });
      setDraft(session); setMessage("Borrador guardado. Aún no hay cierre LIVE ni datos confirmados.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "No se pudo guardar el borrador."); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!client?.confirmCapture || !draft) return;
    setBusy(true); setMessage("");
    try { await client.confirmCapture(draft); setDraft(null); setRevision(value => value + 1);
      setMessage("Periodo confirmado. Pedido, Venta y Entrega quedaron versionados; el cierre LIVE es una acción separada."); }
    catch (error) { setMessage(error instanceof Error ? error.message : "No se pudo confirmar el periodo."); }
    finally { setBusy(false); }
  }
  async function close() {
    if (!client?.closeLive || !filters.chainId || !filters.productId || !period) return;
    setBusy(true); setMessage("");
    try { const result = await client.closeLive(filters.chainId, filters.productId, `${period}-01`);
      setRevision(value => value + 1); setMessage(`Cierre LIVE: ${String(result.evaluations ?? 0)} evaluaciones sobre los cálculos maduros.`); }
    catch (error) { setMessage(error instanceof Error ? error.message : "No se pudo evaluar el periodo."); }
    finally { setBusy(false); }
  }
  return <section className="space-y-5"><div><h1 className="text-2xl font-semibold">Centro de Captura Mensual</h1><p className="mt-1 text-sm text-slate-500">Pedido, Venta y Entrega reales. Guardar borrador no modifica la fuente oficial; confirmar añade nuevas versiones.</p></div>
    {!flags?.capture_center ? <p className="rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900">La captura sigue desactivada. Requiere migración aprobada y activación controlada en Railway.</p> :
      !filters.chainId || !filters.productId ? <p className="rounded-xl border bg-white p-5 text-sm">Selecciona cadena y producto en los filtros superiores.</p> :
      <div className="space-y-4 rounded-xl border bg-white p-5"><div className="grid gap-4 md:grid-cols-4"><label className="text-sm">Periodo<Input type="month" value={period} onChange={event => { setPeriod(event.target.value); setDraft(null); setHistory(null); }}/></label>
        <label className="text-sm">Pedido real<Input type="number" min="0" step="0.001" value={order} onChange={event => setOrder(event.target.value)}/></label>
        <label className="text-sm">Venta real<Input type="number" min="0" step="0.001" value={sale} onChange={event => setSale(event.target.value)}/></label>
        <label className="text-sm">Entrega real<Input type="number" min="0" step="0.001" value={delivery} onChange={event => setDelivery(event.target.value)}/></label></div>
        <label className="block text-sm">Notas<Input value={notes} onChange={event => setNotes(event.target.value)}/></label>
        <label className="block text-sm">Motivo de corrección (obligatorio si ya existe una venta para el periodo)<Input value={reason} onChange={event => setReason(event.target.value)}/></label>
        {profile.global_role !== "VIEWER" && <div className="flex flex-wrap gap-2"><Button disabled={busy || !period || order === "" || sale === "" || delivery === ""} onClick={() => void save()}>Guardar borrador</Button><Button variant="outline" disabled={busy || !draft} onClick={() => void confirm()}>Confirmar datos del periodo</Button>{profile.global_role === "ADMIN" && flags.live_learning && <Button variant="outline" disabled={busy || !period} onClick={() => void close()}>Evaluar LIVE</Button>}</div>}
        {message && <p role="status" className="text-sm">{message}</p>}
        {history?.observations.length ? <div><h2 className="font-semibold">Versiones conservadas</h2><div className="overflow-x-auto"><table className="min-w-full text-left text-sm"><thead><tr><th className="p-2">Métrica</th><th className="p-2">Versión</th><th className="p-2">Valor</th><th className="p-2">Disponible desde</th></tr></thead><tbody>{history.observations.map(row => <tr className="border-t" key={row.id}><td className="p-2">{row.metric_code}</td><td className="p-2">V{row.version_no}</td><td className="p-2">{Number(row.value).toLocaleString("es-MX")}</td><td className="p-2">{row.available_at ?? "Desconocida"}</td></tr>)}</tbody></table></div></div> : null}
      </div>}
    {flags?.live_learning && metrics && <div className="rounded-xl border bg-white p-5"><h2 className="font-semibold">Desempeño LIVE</h2><p className="mt-1 text-xs text-slate-500">WAPE = suma de errores absolutos / suma de venta real; separado por horizonte original. No entrena ni promueve Champion.</p><div className="mt-3 overflow-x-auto"><table className="min-w-full text-left text-sm"><thead><tr><th className="p-2">Motor / horizonte</th><th className="p-2">WAPE LIVE</th><th className="p-2">Bias LIVE</th><th className="p-2">Evaluaciones</th><th className="p-2">Fill Rate</th></tr></thead><tbody>{Object.entries((metrics.metrics ?? {}) as Record<string, { wape: number | null; bias: number | null; evaluations: number; fill_rate: number | null }>).map(([label,row]) => <tr className="border-t" key={label}><td className="p-2">{label}</td><td className="p-2">{row.wape == null ? "—" : `${row.wape.toFixed(2)}%`}</td><td className="p-2">{row.bias == null ? "—" : `${row.bias.toFixed(2)}%`}</td><td className="p-2">{row.evaluations}{row.evaluations < 3 ? " · evidencia limitada" : ""}</td><td className="p-2">{row.fill_rate == null ? "—" : `${row.fill_rate.toFixed(2)}%`}</td></tr>)}</tbody></table></div></div>}
    {flags?.live_learning && <div className="rounded-xl border bg-white p-5"><h2 className="font-semibold">Aprendizaje observado</h2><p className="mt-1 text-xs text-slate-500">Señales deterministas del cierre mensual. No modifican modelos, pesos, decisiones ni Champion.</p>{learning.length ? <div className="mt-3 overflow-x-auto"><table className="min-w-full text-left text-sm"><thead><tr><th className="p-2">Periodo / H</th><th className="p-2">Sugerido</th><th className="p-2">Elegido</th><th className="p-2">Menor error observado</th><th className="p-2">Regret</th><th className="p-2">Señales</th></tr></thead><tbody>{learning.slice(0,20).map(row => <tr className="border-t" key={String(row.id)}><td className="p-2">{String(row.target_period ?? "").slice(0,7)} · H{String(row.horizon ?? "—")}</td><td className="p-2">{String(row.suggested_candidate ?? "—")}</td><td className="p-2">{String(row.selected_candidate ?? "Sin decisión")}</td><td className="p-2">{String(row.best_candidate_actual ?? "—")}</td><td className="p-2">{row.decision_regret_absolute == null ? "—" : `${Number(row.decision_regret_absolute).toLocaleString("es-MX")} pzas`}</td><td className="p-2">{Array.isArray(row.signals_json) && row.signals_json.length ? row.signals_json.join(", ") : "Sin señal"}</td></tr>)}</tbody></table></div> : <p className="mt-3 text-sm text-slate-500">Aún no hay cierres LIVE confirmados para este producto.</p>}</div>}
  </section>;
}
