"use client";

import dynamic from "next/dynamic";
import { KeyboardEvent, useCallback, useRef, useState } from "react";
import { LoaderCircle, Mic, SendHorizonal, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
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
};

type ForecastAssistantProps = {
  authorized: boolean;
  uiEnabled: boolean;
  apiEnabled: boolean;
  voiceEnabled: boolean;
  mode: AssistantMode;
  context: AssistantContext;
};

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

function ForecastAssistantContent({ apiEnabled, voiceEnabled, mode, context }: Omit<ForecastAssistantProps, "authorized" | "uiEnabled">) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [processing, setProcessing] = useState(false);
  const [lottieFailed, setLottieFailed] = useState(false);
  const sequence = useRef(1);
  const [messages, setMessages] = useState<ConversationMessage[]>([
    { id: "welcome", role: "assistant", text: apiEnabled
      ? "Puedes preguntarme por Forecast, WAPE, Bias, Fill Rate, Champion, Challenger y desempeño de productos."
      : visualAssistantNotice },
  ]);

  const logEvent = useCallback((event: "open" | "close" | "response" | "lottie_error" | "ui_error") => {
    console.info("[ForecastAssistant]", { event, screen: context.screen });
  }, [context.screen]);

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    logEvent(nextOpen ? "open" : "close");
  };

  const handleLottieError = useCallback(() => {
    setLottieFailed(true);
    logEvent("lottie_error");
  }, [logEvent]);

  const submit = async () => {
    const message = draft.trim();
    if (!apiEnabled || !message || processing) return;
    setDraft("");
    setMessages((current) => [...current, { id: `user-${sequence.current++}`, role: "user", text: message }]);
    setProcessing(true);
    try {
      const response = await sendAssistantMessage(message, context, { mode, apiEnabled });
      setMessages((current) => [...current, { id: `assistant-${sequence.current++}`, role: "assistant", text: response.message }]);
      logEvent("response");
    } catch {
      setMessages((current) => [...current, { id: `assistant-${sequence.current++}`, role: "assistant", text: "La interfaz del asistente no está disponible en este momento." }]);
      logEvent("ui_error");
    } finally {
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
              {apiEnabled ? "Consultas sobre resultados publicados para el scope seleccionado." : visualAssistantNotice}
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
            {messages.filter(message => apiEnabled || message.id !== "welcome").map((message) => <div key={message.id} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm leading-6 ${message.role === "user" ? "rounded-br-md bg-blue-700 text-white" : "rounded-bl-md border border-slate-200 bg-white text-slate-700 shadow-sm"}`}>
                {message.text}
              </div>
            </div>)}
            {processing && <div className="flex justify-start"><div className="flex items-center gap-2 rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 shadow-sm"><LoaderCircle className="size-4 animate-spin" /> Preparando respuesta…</div></div>}
          </div>
        </ScrollArea>

        <div className="border-t border-slate-200 bg-white p-4">
          <div className="flex items-end gap-2">
            {voiceEnabled && <FutureVoiceControls enabled />}
            <Textarea
              disabled={!apiEnabled || processing}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={apiEnabled ? "Escribe tu pregunta…" : "Consulta inteligente disponible en la siguiente fase"}
              aria-label="Mensaje para el asistente"
              rows={2}
              className="max-h-32 min-h-11 resize-none bg-white"
            />
            <Button type="button" size="icon" disabled={!apiEnabled || !draft.trim() || processing} onClick={() => void submit()} aria-label="Enviar mensaje" className="shrink-0">
              <SendHorizonal className="size-4" />
            </Button>
          </div>
          {apiEnabled && <p className="mt-2 text-center text-[11px] text-slate-500">Enter para enviar · Shift+Enter para una nueva línea</p>}
        </div>
      </SheetContent>
    </Sheet>
  </>;
}

export default function ForecastAssistant(props: ForecastAssistantProps) {
  if (!props.authorized || !props.uiEnabled) return null;
  return <ForecastAssistantContent apiEnabled={props.apiEnabled} voiceEnabled={props.voiceEnabled} mode={props.mode} context={props.context} />;
}
