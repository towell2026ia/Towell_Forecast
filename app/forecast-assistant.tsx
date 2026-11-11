"use client";

import dynamic from "next/dynamic";
import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { LoaderCircle, Mic, SendHorizonal, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Input } from "@/components/ui/input";
import { PreviewReadError } from "@/lib/forecast-preview";
import { assistantScopeIsValid, rsMetrics, safeResearchUrl, type RsAssistantAction, type RsAssistantClient, type RsAssistantRequest, type RsAssistantResponse } from "@/lib/rs-assistant";
import {
  type AssistantContext, type AssistantMode, sendAssistantMessage,
} from "./assistant/assistant-service";

const LazyLottie = dynamic(() => import("./forecast-assistant-lottie"), {
  ssr: false,
  loading: () => <AssistantMark />,
});
export const visualAssistantNotice = "Asistente disponible en modo visual. La consulta inteligente y voz se habilitarán en la siguiente fase.";

type ConversationMessage = {
  id: string;
  role: "assistant" | "user";
  text: string;
  scope?: string;
  response?: RsAssistantResponse;
  researchPollingStopped?: boolean;
};

type PendingResearch = { messageId: string; scopeKey: string; request: RsAssistantRequest; version: RsAssistantResponse["scope"] };
const researchPollDelay = 15_000;
const maxResearchPolls = 20;

function sameResearchScope(previous: RsAssistantResponse["scope"], current: RsAssistantResponse["scope"]) {
  return previous && current && previous.chain_id === current.chain_id && previous.period === current.period
    && previous.model === current.model && previous.format === current.format
    && previous.presentation === current.presentation
    && previous.source_hash === current.source_hash && previous.generation === current.generation;
}

function replaceResearch(messages: ConversationMessage[], id: string, research: RsAssistantResponse["research"]) {
  return messages.map(entry => entry.id === id && entry.response
    ? { ...entry, researchPollingStopped: false, response: { ...entry.response, research } }
    : entry);
}

type ForecastAssistantProps = {
  authorized: boolean;
  uiEnabled: boolean;
  apiEnabled: boolean;
  voiceEnabled: boolean;
  mode: AssistantMode;
  context: AssistantContext;
  rsAssistant?: RsAssistantClient;
};

const rsActions = { answer: "Enviar mensaje", research: "Iniciar investigación pública", research_status: "Consultar estado de investigación", forecast: "Calcular / consultar pronóstico RS" } as const;
const researchStatus: Record<string, string> = {
  COMPLETED: "Investigación completada", PENDING: "Investigación pendiente", QUEUED: "Investigación pendiente", IN_PROGRESS: "Investigación en curso",
  DISABLED: "Investigación desactivada", UNAVAILABLE: "Investigación no disponible", FAILED: "No se pudo completar la investigación", NO_EVIDENCE: "Sin evidencia pública suficiente",
  BUSY: "Hay cuatro investigaciones en curso. Vuelve a iniciar la investigación más tarde.", NOT_REQUESTED: "Sin investigación iniciada para este alcance",
};
const researchTime = new Intl.DateTimeFormat("es-MX", { dateStyle: "medium", timeStyle: "short", timeZone: "America/Mexico_City" });
const usageLabels = { input_tokens: "Tokens de entrada", output_tokens: "Tokens de salida", total_tokens: "Tokens totales", web_search_calls: "Búsquedas web" } as const;
const rsErrors: Record<string, string> = {
  SCOPE_FORBIDDEN: "Tu cuenta no tiene acceso a ese alcance o a la acción solicitada.",
  AUTH_REQUIRED: "La sesión debe renovarse antes de consultar el asistente.",
  RS_ASSISTANT_DISABLED: "El asistente RS está desactivado en el servidor.",
  OFFICIAL_SOURCE_UNAVAILABLE: "La fuente comercial oficial aún no está disponible.",
  RS_FORMAT_NOT_VERIFIED: "El formato no está acreditado para la cadena seleccionada.",
  RS_MODEL_NOT_VERIFIED: "El modelo no está acreditado para ese alcance RS.",
  RS_MODEL_PRESENTATION_AMBIGUOUS: "El modelo contiene presentaciones diferentes. Se necesita aclarar el alcance antes de calcular.",
  RS_SCOPE_INVALID: "El alcance o el mes no están disponibles en la versión RS autorizada.",
  RS_FORECAST_SCOPE_UNAVAILABLE: "El alcance no admite el cálculo RS solicitado.",
  RS_FORECAST_HISTORY_INSUFFICIENT: "El histórico acreditado no alcanza para calcular este pronóstico RS.",
  RS_FORECAST_BUSY: "Hay un cálculo en curso. Puedes volver a consultar cuando termine.",
  RATE_001: "Se alcanzó el límite de consultas. Espera antes de volver a intentar.",
};
const metricValue = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) ? `${value.toLocaleString("es-MX", { maximumFractionDigits: 4 })}%` : "Sin dato";
const coverageLabel = (value?: { products_with_data: number; products_expected: number }) => value ? `${value.products_with_data}/${value.products_expected} productos${value.products_with_data < value.products_expected ? " · Parcial" : ""}` : "Cobertura sin dato";
function AssistantFacts({ response }: { response: RsAssistantResponse }) {
  const selected = response.metrics?.by_month.find(row => row.period === response.scope?.period);
  const stock = response.metrics?.inventory;
  const forecast = response.forecast;
  return <details className="mt-2 border-t border-slate-200 pt-2 text-xs" open>
    <summary className="cursor-pointer font-medium">Datos y cobertura del alcance</summary>
    {response.scope && <p className="mt-1 break-words">{response.scope.chain} · {response.scope.format} · {response.scope.model} · {response.scope.presentation} · RS · {response.scope.period}</p>}
    {response.scope?.source_hash && <p className="break-all text-[10px] text-slate-500">Fuente {response.scope.source_hash.slice(0, 12)} · Actualización {response.scope.generation}</p>}
    <dl className="mt-2 space-y-1">{(Object.keys(rsMetrics) as (keyof typeof rsMetrics)[]).map(key => <div key={key}><dt className="inline font-medium">{rsMetrics[key]} </dt><dd className="inline">{selected?.[key]?.pieces ?? "Sin dato"} piezas · {coverageLabel(selected?.[key])}</dd></div>)}
      <div><dt className="inline font-medium">Inventario agregado </dt><dd className="inline">{stock?.pieces ?? "Sin dato"} piezas · {coverageLabel(stock)}</dd></div>
      <div><dt className="inline font-medium">Fill Rate </dt><dd className="inline">{selected?.fill_rate?.ratio !== null && selected?.fill_rate?.ratio !== undefined ? metricValue(Number(selected.fill_rate.ratio) * 100) : "Sin dato"} · {selected?.fill_rate ? `${selected.fill_rate.paired_products}/${selected.fill_rate.products_expected} pares${selected.fill_rate.paired_products < selected.fill_rate.products_expected ? " · Parcial" : ""}` : "Cobertura sin dato"}</dd></div>
    </dl>
    {forecast && <div className="mt-3 space-y-2"><p className="font-medium">Pronóstico provisional · {forecast.engine}</p>
      <div className="max-h-64 overflow-auto"><table aria-label="Pronóstico RS del asistente" className="w-full text-left"><thead><tr><th>Mes</th><th>Estadístico</th><th>ML</th><th>Ensemble</th></tr></thead><tbody>{forecast.periods.map(period => <tr key={period.period}><th className="pr-2 font-normal">{period.period}</th>{[period.statistical, period.ml, period.ensemble_value].map((item, index) => <td key={index} className="px-1 align-top">{item.pieces ?? "Sin dato"}<span className="block text-[10px]">{item.covered}/{item.eligible}{item.covered < item.eligible ? " · Parcial" : ""}</span></td>)}</tr>)}</tbody></table></div>
      {(["statistical", "ml"] as const).map(engine => <p key={engine}>{engine === "statistical" ? "Estadístico" : "ML"} · WAPE retrospectivo {metricValue(forecast.retrospective?.[engine]?.wape)} · Bias retrospectivo {metricValue(forecast.retrospective?.[engine]?.bias)}</p>)}
    </div>}
    {typeof response.quality?.products_excluded_ambiguous === "number" && response.quality.products_excluded_ambiguous > 0 && <p>Variantes con identidad pendiente: {response.quality.products_excluded_ambiguous}</p>}
    <p className="mt-2 text-slate-500">La cobertura incompleta conserva sus faltantes. Sin certificación LIVE en esta consulta.</p>
  </details>;
}
function AssistantEvidence({ response }: { response: RsAssistantResponse }) {
  const research = response.research;
  const sources = research?.sources?.flatMap(source => {
    const href = safeResearchUrl(source.url);
    return href ? [{ ...source, url: href }] : [];
  }) ?? [];
  const usage = (Object.keys(usageLabels) as (keyof typeof usageLabels)[]).flatMap(key => {
    const value = research?.usage?.[key];
    return value === undefined ? [] : [{ label: usageLabels[key], value }];
  });
  return <>
    {response.metrics && <AssistantFacts response={response}/>}
    {response.uncertainty && <p className="mt-2 text-xs text-slate-500">{response.uncertainty}</p>}
    {research && <div className="mt-2 border-t border-slate-200 pt-2 text-xs">
      <p className="font-medium">{researchStatus[research.status] ?? "Estado de investigación sin confirmar"}{research.model ? ` · ${research.model}` : ""}</p>
      {research.evidence_scope === "PUBLIC_CATEGORY_CONTEXT" && <p>Contexto público de la categoría.</p>}
      {(research.started_at || research.finished_at) && <dl aria-label="Fechas de ejecución" className="mt-1">
        {research.started_at && <div><dt className="inline font-medium">Inicio </dt><dd className="inline"><time dateTime={research.started_at}>{researchTime.format(new Date(research.started_at))}</time> · hora de Ciudad de México</dd></div>}
        {research.finished_at && <div><dt className="inline font-medium">Fin </dt><dd className="inline"><time dateTime={research.finished_at}>{researchTime.format(new Date(research.finished_at))}</time> · hora de Ciudad de México</dd></div>}
      </dl>}
      {usage.length > 0 && <dl aria-label="Consumo de la investigación" className="mt-1">
        {usage.map(({ label, value }) => <div key={label}><dt className="inline font-medium">{label} </dt><dd className="inline">{value.toLocaleString("es-MX")}</dd></div>)}
      </dl>}
      {research.summary && research.summary !== response.message && <p className="whitespace-pre-wrap">{research.summary}</p>}
      {sources.length > 0 && <ul aria-label="Fuentes públicas" className="mt-1 list-disc pl-4">{sources.map(source => <li key={source.url}><a href={source.url} target="_blank" rel="noopener noreferrer" className="break-words underline">{source.title}</a></li>)}</ul>}
      {research.source_publication_dates_verified === false && <p className="mt-1 text-slate-500">Fechas de publicación de las fuentes sin acreditar.</p>}
    </div>}
  </>;
}

function AssistantMark() {
  return <span className="grid size-full place-items-center rounded-full bg-gradient-to-br from-blue-600 to-slate-950 text-[13px] font-black tracking-tight text-white">FT</span>;
}

function FutureVoiceControls({ enabled }: { enabled: boolean }) {
  return <Button
    type="button"
    variant="outline"
    size="icon"
    disabled
    aria-label="Control de voz disponible en una fase futura"
    className={enabled ? "shrink-0" : "hidden"}
  ><Mic className="size-4" /></Button>;
}

function ForecastAssistantContent({ apiEnabled, voiceEnabled, mode, context, rsAssistant }: Omit<ForecastAssistantProps, "authorized" | "uiEnabled">) {
  const [open, setOpen] = useState(false);
  const [draftState, setDraftState] = useState({ key: "", value: "" });
  const [processing, setProcessing] = useState(false);
  const [lottieFailed, setLottieFailed] = useState(false);
  const [pendingResearch, setPendingResearch] = useState<PendingResearch | null>(null);
  const sequence = useRef(1);
  const sending = useRef(false);
  const stoppedPoll = useRef<PendingResearch | null>(null);
  const models = context.commercialModels ?? [];
  const filterPeriod = context.periodRange?.[0] && context.periodRange[0] === context.periodRange[1] ? context.periodRange[0] : "";
  const filterKey = JSON.stringify([context.user, context.chainId, models, context.periodRange]);
  const [selection, setSelection] = useState({ key: "", format: "", model: "", period: "", presentation: "" });
  const activeSelection = selection.key === filterKey ? selection : { key: filterKey, format: "", model: "", period: "", presentation: "" };
  const scope = {
    chain_id: context.chainId ?? "", purchase_type: "RS" as const, format: activeSelection.format.trim(),
    model: (models.length === 1 ? models[0] : activeSelection.model).trim(), period: filterPeriod || activeSelection.period,
    ...(activeSelection.presentation.trim() ? { presentation: activeSelection.presentation.trim() } : {}),
  };
  const scopeKey = JSON.stringify([context.user, context.globalRole ?? context.role, scope]);
  const draftKey = rsAssistant ? scopeKey : "";
  const draft = draftState.key === draftKey ? draftState.value : "";
  const setDraft = (value: string) => setDraftState({ key: draftKey, value });
  const scopeReady = assistantScopeIsValid(scope);
  const canSend = rsAssistant ? scopeReady : apiEnabled;
  const canExecute = context.globalRole
    ? context.globalRole === "ADMIN" || context.globalRole === "EDITOR"
    : context.role === "manager" || context.role === "editor";
  const operationalNotice = "Consultas RS por cadena, formato, modelo y mes. Especifica la presentación cuando el modelo tenga varios tamaños. El pronóstico calculado es provisional.";
  const [messages, setMessages] = useState<ConversationMessage[]>([
    { id: "welcome", role: "assistant", text: apiEnabled
      ? "Puedes preguntarme por Forecast, WAPE, Bias, Fill Rate, Champion, Challenger y desempeño de productos."
      : visualAssistantNotice },
  ]);

  const logEvent = useCallback((event: "open" | "close" | "response" | "lottie_error" | "ui_error") => {
    console.info("[ForecastAssistant]", { event, screen: context.screen });
  }, [context.screen]);

  useEffect(() => {
    if (!open || !rsAssistant || !pendingResearch || pendingResearch.scopeKey !== scopeKey || stoppedPoll.current === pendingResearch) return;
    const { messageId, request, version } = pendingResearch;
    const client = rsAssistant;
    const controller = new AbortController();
    let attempts = 0;
    let timer = window.setTimeout(() => void refresh(), researchPollDelay);
    const finish = () => setPendingResearch(current => current === pendingResearch ? null : current);
    const stop = () => {
      setMessages(current => current.map(entry => entry.id === messageId ? { ...entry, researchPollingStopped: true } : entry));
      finish();
    };

    async function refresh() {
      attempts++;
      try {
        const response = await client(request, controller.signal);
        if (controller.signal.aborted) return;
        if (!response.research || !sameResearchScope(version, response.scope)) {
          stop();
          return;
        }
        setMessages(current => replaceResearch(current, messageId, response.research));
        if (response.research.status !== "PENDING") finish();
        else if (attempts >= maxResearchPolls) stop();
        else timer = window.setTimeout(() => void refresh(), researchPollDelay);
      } catch {
        if (!controller.signal.aborted) stop();
      }
    }

    return () => {
      window.clearTimeout(timer);
      controller.abort();
      stoppedPoll.current = pendingResearch;
    };
  }, [open, pendingResearch, rsAssistant, scopeKey]);

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    logEvent(nextOpen ? "open" : "close");
  };

  const handleLottieError = useCallback(() => {
    setLottieFailed(true);
    logEvent("lottie_error");
  }, [logEvent]);

  const submit = async (action: RsAssistantAction = "answer") => {
    const message = action === "answer" || action === "research" ? draft.trim() : "";
    if (!canSend || sending.current || action === "answer" && !message || !rsAssistant && action !== "answer") return;
    if ((action === "forecast" || action === "research") && !canExecute) return;
    const sentScope = rsAssistant ? scopeKey : undefined;
    if (action === "research_status") setPendingResearch(null);
    if (message) setDraft("");
    setMessages(current => [...current, { id: `user-${sequence.current++}`, role: "user", text: message || rsActions[action], scope: sentScope }]);
    sending.current = true;
    setProcessing(true);
    try {
      if (rsAssistant) {
        const response = await rsAssistant({ ...scope, message, action });
        const entry = action === "research_status" && response.research ? messages.findLast(item => item.scope === sentScope && item.response?.research && sameResearchScope(item.response.scope, response.scope)) : undefined;
        const id = entry?.id ?? `assistant-${sequence.current++}`;
        if (entry) setMessages(current => replaceResearch(current, id, response.research));
        else setMessages(current => [...current, { id, role: "assistant", text: response.message, scope: sentScope, response }]);
        if (response.research?.status === "PENDING" && response.scope) {
          setPendingResearch({ messageId: id, scopeKey, version: response.scope, request: { ...scope, message: "", action: "research_status" } });
        }
      } else {
        const response = await sendAssistantMessage(message, context, { mode, apiEnabled });
        setMessages(current => [...current, { id: `assistant-${sequence.current++}`, role: "assistant", text: response.message }]);
      }
      logEvent("response");
    } catch (error) {
      const text = error instanceof PreviewReadError && rsErrors[error.code] ? rsErrors[error.code] : "La interfaz del asistente no está disponible en este momento.";
      setMessages(current => [...current, { id: `assistant-${sequence.current++}`, role: "assistant", text, scope: sentScope }]);
      logEvent("ui_error");
    } finally {
      sending.current = false;
      setProcessing(false);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  };

  return <>
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label="Abrir Asistente FORECAST Towell"
            aria-expanded={open}
            onClick={() => handleOpenChange(true)}
            className="fixed bottom-5 right-5 z-40 size-12 cursor-pointer overflow-hidden rounded-full border border-white/70 bg-white p-0.5 shadow-[0_14px_36px_rgba(15,23,42,.3)] ring-1 ring-slate-900/10 transition hover:-translate-y-0.5 hover:shadow-[0_18px_42px_rgba(30,64,175,.3)] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-300 sm:bottom-6 sm:right-6 sm:size-14 lg:size-16"
          >
            {lottieFailed ? <AssistantMark /> : <LazyLottie onError={handleLottieError} />}
          </button>
        </TooltipTrigger>
        <TooltipContent side="left" sideOffset={10}>Abrir Asistente FORECAST Towell</TooltipContent>
      </Tooltip>
    </TooltipProvider>

    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetContent side="right" className="w-full max-w-none gap-0 border-slate-200 bg-[#f7f9fc] p-0 sm:max-w-[400px]">
        <SheetHeader className="border-b border-slate-200 bg-white px-5 py-5 pr-12 text-left">
          <div className="flex items-center gap-3">
            <div className="grid size-10 place-items-center rounded-2xl bg-blue-700 text-white"><Sparkles className="size-5" /></div>
            <div>
              <SheetTitle className="text-base text-slate-950">Asistente FORECAST Towell</SheetTitle>
              <SheetDescription className="mt-0.5 text-xs">{context.chain ?? "Todas las cadenas"} · {context.screen} · {context.globalRole ?? context.role}</SheetDescription>
            </div>
          </div>
        </SheetHeader>

        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-4 p-5" aria-live="polite" aria-label="Conversación del asistente">
            <div className="rounded-xl border border-blue-100 bg-blue-50 px-3 py-2 text-xs leading-5 text-blue-800">
              {rsAssistant ? operationalNotice : apiEnabled ? "Consultas sobre resultados publicados para el scope seleccionado." : visualAssistantNotice}
            </div>
            <dl aria-label="Contexto del asistente" className="space-y-1 rounded-xl border border-slate-200 bg-white p-3 text-xs text-slate-600">
              <div><dt className="inline font-medium">Scope: </dt><dd className="inline">{context.chain ?? "Todas las cadenas"}</dd></div>
              <div><dt className="inline font-medium">Módulo: </dt><dd className="inline">{context.screen}</dd></div>
              <div><dt className="inline font-medium">Rol: </dt><dd className="inline">{context.globalRole ?? context.role}</dd></div>
              <div><dt className="inline font-medium">Categoría: </dt><dd className="inline">{context.categoryId ?? context.category ?? "Todas"}</dd></div>
              <div><dt className="inline font-medium">Producto: </dt><dd className="inline">{context.productId ?? context.product ?? "Todos"}</dd></div>
              <div><dt className="inline font-medium">Periodo: </dt><dd className="inline">{context.periodRange ? `${context.periodRange[0] ?? "Inicio"} → ${context.periodRange[1] ?? "Último"}` : context.period ?? "Todos"}</dd></div>
              <div><dt className="inline font-medium">Búsqueda: </dt><dd className="inline">{context.search || "Sin filtro"}</dd></div>
            </dl>
            {rsAssistant && <fieldset className="space-y-2 rounded-xl border border-slate-200 bg-white p-3 text-xs" disabled={processing}>
              <legend className="font-medium">Alcance del asistente RS</legend>
              {!context.chainId && <p>Selecciona una cadena en los filtros del portal.</p>}
              <label className="block">Formato comercial<Input value={activeSelection.format} maxLength={60} onChange={event => setSelection({ ...activeSelection, format: event.target.value })}/></label>
              {models.length > 1 ? <label className="block">Modelo<select className="block w-full rounded border px-2 py-1" value={activeSelection.model} onChange={event => setSelection({ ...activeSelection, model: event.target.value })}><option value="">Seleccionar un modelo</option>{models.map(model => <option key={model}>{model}</option>)}</select></label> : <label className="block">Modelo<Input value={models.length === 1 ? models[0] : activeSelection.model} readOnly={models.length === 1} maxLength={80} onChange={event => setSelection({ ...activeSelection, model: event.target.value })}/></label>}
              <label className="block">Mes<Input type="month" value={scope.period} readOnly={Boolean(filterPeriod)} onChange={event => setSelection({ ...activeSelection, period: event.target.value })}/></label>
              <label className="block">Presentación (cuando aplique)<Input value={activeSelection.presentation} maxLength={80} onChange={event => setSelection({ ...activeSelection, presentation: event.target.value })}/></label>
            </fieldset>}
            {messages.filter(message => rsAssistant ? message.scope === scopeKey : apiEnabled || message.id !== "welcome").map((message) => <div key={message.id} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm leading-6 ${message.role === "user" ? "rounded-br-md bg-blue-700 text-white" : "rounded-bl-md border border-slate-200 bg-white text-slate-700 shadow-sm"}`}>
                <p className="whitespace-pre-wrap break-words">{message.text}</p>
                {message.response && <AssistantEvidence response={message.response}/>}
                {message.researchPollingStopped && <p className="mt-2 text-xs text-slate-500">Seguimiento automático detenido. Usa Consultar estado de investigación para actualizar.</p>}
              </div>
            </div>)}
            {processing && <div className="flex justify-start"><div className="flex items-center gap-2 rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 shadow-sm"><LoaderCircle className="size-4 animate-spin" /> Preparando respuesta…</div></div>}
          </div>
        </ScrollArea>

        <div className="border-t border-slate-200 bg-white p-4">
          <div className="flex items-end gap-2">
            {voiceEnabled && <FutureVoiceControls enabled />}
            <Textarea
              disabled={!canSend || processing}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={canSend ? "Escribe tu pregunta…" : rsAssistant ? "Completa el alcance de la consulta" : "Consulta inteligente disponible en la siguiente fase"}
              aria-label="Mensaje para el asistente"
              rows={2}
              maxLength={rsAssistant ? 1000 : undefined}
              className="max-h-32 min-h-11 resize-none bg-white"
            />
            <Button type="button" size="icon" disabled={!canSend || !draft.trim() || processing} onClick={() => void submit()} aria-label="Enviar mensaje" className="shrink-0">
              <SendHorizonal className="size-4" />
            </Button>
          </div>
          {rsAssistant && <div className="mt-2 flex flex-wrap gap-2">
            {(["forecast", "research", "research_status"] as const).map(action => <Button key={action} type="button" size="sm" variant="outline" disabled={!scopeReady || processing || action !== "research_status" && !canExecute} onClick={() => void submit(action)}>{rsActions[action]}</Button>)}
          </div>}
          {(apiEnabled || rsAssistant) && <p className="mt-2 text-center text-[11px] text-slate-500">Enter para enviar · Shift+Enter para una nueva línea</p>}
        </div>
      </SheetContent>
    </Sheet>
  </>;
}

export default function ForecastAssistant(props: ForecastAssistantProps) {
  if (!props.authorized || !props.uiEnabled) return null;
  return <ForecastAssistantContent apiEnabled={props.apiEnabled} voiceEnabled={props.voiceEnabled} mode={props.mode} context={props.context} rsAssistant={props.rsAssistant} />;
}
