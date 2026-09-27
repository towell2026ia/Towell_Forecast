"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  BrainCircuit, CalendarRange, ChevronRight, ClipboardCheck,
  FileClock, History, Home, PanelLeft, Search,
  ShieldCheck, ShoppingCart, Sparkles, TrendingUp, Truck, UserCog, Users,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent,
  SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu,
  SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger,
} from "@/components/ui/sidebar";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Toaster } from "@/components/ui/sonner";
import ForecastEnginesView from "./forecast-engines-view";
import ExecutiveDashboard from "./executive-dashboard";
import ForecastAssistant from "./forecast-assistant";
import ForecastAssistantErrorBoundary from "./forecast-assistant-error-boundary";
import type { AssistantMode } from "./assistant/assistant-service";

type ModuleId = "inicio" | "captura" | "historico" | "motor" | "periodos" | "calidad" | "usuarios" | "auditoria";
type CaptureType = "pedido" | "venta" | "entrega" | "fcst";

const products = [
  ["101285353", "7501897813122", "Chocolate"], ["101285349", "7501897813139", "Morado"],
  ["101285357", "7501897813146", "Azul"], ["101285351", "7501897813153", "Aqua"],
  ["101285355", "7501897813160", "Gris"], ["101285348", "7501897813177", "Beige"],
  ["101285354", "7501897813184", "Rosa"], ["101285356", "7501897813191", "Rojo"],
  ["101558813", "7501897816260", "Negro"], ["101558815", "7501897816277", "Navidad"],
  ["101558814", "7501897816314", "Oxford"],
];

const modules = [
  ["inicio", "Inicio", Home], ["captura", "Centro de captura", ClipboardCheck],
  ["historico", "Histórico", History], ["motor", "Motores de Forecast", BrainCircuit], ["periodos", "Periodos", CalendarRange],
  ["calidad", "Calidad de datos", ShieldCheck], ["usuarios", "Usuarios", Users],
  ["auditoria", "Auditoría", FileClock],
] as const;

const captureMeta = {
  pedido: { title: "Registrar pedido", icon: ShoppingCart, color: "bg-blue-600", copy: "OC, productos, cantidades y fecha requerida." },
  venta: { title: "Registrar venta", icon: TrendingUp, color: "bg-cyan-600", copy: "Unidades, importe opcional y estado del dato." },
  entrega: { title: "Registrar entrega", icon: Truck, color: "bg-violet-600", copy: "Pedido, parcialidad, fecha y saldo pendiente." },
  fcst: { title: "Registrar Fcst Cliente", icon: Sparkles, color: "bg-amber-500", copy: "Pronóstico recibido, versión, vigencia y periodo futuro." },
};

const historyRows = [
  ["Jul 2026", "Chocolate", "7501897813122", "101285353", "18,036", "13,600", "16,530"],
  ["Jul 2026", "Morado", "7501897813139", "101285349", "19,590", "14,428", "18,684"],
  ["Jul 2026", "Azul", "7501897813146", "101285357", "10,098", "8,858", "9,306"],
  ["Jul 2026", "Aqua", "7501897813153", "101285351", "10,950", "9,421", "9,060"],
  ["Jul 2026", "Gris", "7501897813160", "101285355", "0", "116", "0"],
  ["Jul 2026", "Beige", "7501897813177", "101285348", "10,524", "7,795", "10,164"],
];

const users = [
  ["Gerencia 1", "gerencia1@towell.local", "Gerente", "Pendiente de correo"],
  ["Gerencia 2", "gerencia2@towell.local", "Gerente", "Pendiente de correo"],
  ["Gerencia 3", "gerencia3@towell.local", "Gerente", "Pendiente de correo"],
  ["Operación piloto", "editor@towell.local", "Editor", "Activo"],
  ["Consulta piloto", "lector@towell.local", "Lector", "Activo"],
];

const audits = [
  ["18 sep, 10:42", "Sistema", "Migración preparada", "Lote PRD01", "Pendiente → Validando"],
  ["18 sep, 10:18", "Operación piloto", "Venta capturada", "Jul 2026 · Azul", "— → 1,176"],
  ["18 sep, 09:54", "Gerencia 1", "Periodo abierto", "Sep 2026", "Planeado → Abierto"],
  ["17 sep, 18:31", "Sistema", "Regla aplicada", "UPC 7501897813177", "Texto conservado"],
];

function StatusBadge({ children, tone = "blue" }: { children: React.ReactNode; tone?: "blue" | "amber" | "green" | "slate" | "red" }) {
  const colors = { blue: "border-blue-200 bg-blue-50 text-blue-700", amber: "border-amber-200 bg-amber-50 text-amber-800", green: "border-emerald-200 bg-emerald-50 text-emerald-700", slate: "border-slate-200 bg-slate-50 text-slate-700", red: "border-rose-200 bg-rose-50 text-rose-700" };
  return <Badge variant="outline" className={colors[tone]}>{children}</Badge>;
}

export default function ForecastTowellApp({ supabaseConfigured, assistantConfig }: {
  supabaseConfigured: boolean;
  assistantConfig: { authorized: boolean; uiEnabled: boolean; apiEnabled: boolean; voiceEnabled: boolean; mode: AssistantMode };
}) {
  const [active, setActive] = useState<ModuleId>("inicio");
  const [capture, setCapture] = useState<CaptureType | null>(null);
  const [periodState, setPeriodState] = useState("Abierto");
  const title = useMemo(() => modules.find(([id]) => id === active)?.[1] ?? "Inicio", [active]);

  useEffect(() => {
    type ModelContext = { registerTool?: (tool: unknown, options?: { signal?: AbortSignal }) => unknown };
    const context = (document as Document & { modelContext?: ModelContext }).modelContext;
    if (!context?.registerTool) return;
    const lifecycle = new AbortController();
    for (const [type, meta] of Object.entries(captureMeta) as [CaptureType, typeof captureMeta[CaptureType]][]) {
      void Promise.resolve(context.registerTool({
        name: `start_${type}_capture`, title: meta.title,
        description: `Abre el formulario de ${meta.title.toLowerCase()}.`,
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
        annotations: { readOnlyHint: false, untrustedContentHint: false },
        execute: async (input: unknown) => {
          if (typeof input !== "object" || input === null || Object.keys(input).length > 0) {
            throw new Error("Este comando no admite parámetros.");
          }
          setActive("captura"); setCapture(type);
          return { status: "formulario_abierto", type };
        },
      }, { signal: lifecycle.signal })).catch(() => undefined);
    }
    void Promise.resolve(context.registerTool({
      name: "open_statistical_engine", title: "Abrir Motor Estadístico",
      description: "Abre la vista de clasificación, backtesting, modelo ganador y forecast a 12 meses.",
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
      annotations: { readOnlyHint: true, untrustedContentHint: false },
      execute: async (input: unknown) => {
        if (typeof input !== "object" || input === null || Object.keys(input).length > 0) throw new Error("Este comando no admite parámetros.");
        setActive("motor"); return { status: "opened", module: "motor_estadistico" };
      },
    }, { signal: lifecycle.signal })).catch(() => undefined);
    return () => lifecycle.abort();
  }, []);

  async function submitCapture(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!capture) return;
    const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
    let persisted = false;
    if (supabaseConfigured) {
      try {
        const result = await fetch("/api/records", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ type: capture, payload }) });
        persisted = result.ok;
      } catch { persisted = false; }
    }
    setCapture(null);
    toast.success(persisted ? "Registro guardado y auditado" : "Captura validada en modo demostración", {
      description: persisted ? "Supabase creó también la versión y el evento." : "Conecta Supabase para convertirla en registro oficial.",
    });
  }

  return <SidebarProvider>
    <Sidebar collapsible="icon" className="border-r border-slate-200">
      <SidebarHeader className="border-b border-slate-200 p-4"><div className="flex items-center gap-3"><div className="grid size-9 shrink-0 place-items-center rounded-xl bg-blue-700 text-sm font-black text-white">FT</div><div className="min-w-0 group-data-[collapsible=icon]:hidden"><p className="truncate text-sm font-bold">FORECAST Towell</p><p className="truncate text-xs text-slate-500">Operación y control</p></div></div></SidebarHeader>
      <SidebarContent><SidebarGroup><SidebarGroupLabel>Módulos</SidebarGroupLabel><SidebarGroupContent><SidebarMenu>{modules.map(([id, label, Icon]) => <SidebarMenuItem key={id}><SidebarMenuButton isActive={active === id} tooltip={label} onClick={() => setActive(id)} className="cursor-pointer"><Icon/><span>{label}</span></SidebarMenuButton></SidebarMenuItem>)}</SidebarMenu></SidebarGroupContent></SidebarGroup></SidebarContent>
      <SidebarFooter className="border-t border-slate-200 p-3"><div className="rounded-xl bg-slate-900 p-3 text-white group-data-[collapsible=icon]:hidden"><p className="text-xs font-semibold">Piloto seleccionado</p><p className="mt-1 text-sm">FENDI BD</p><p className="mt-2 text-[11px] text-slate-300">Cadena · Walmart</p></div><div className="hidden size-8 place-items-center rounded-lg bg-slate-900 text-white group-data-[collapsible=icon]:grid">FB</div></SidebarFooter>
    </Sidebar>
    <SidebarInset className="min-w-0 bg-[#f7f9fc]">
      <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-slate-200 bg-white/95 px-4 backdrop-blur md:px-7"><div className="flex min-w-0 items-center gap-3"><SidebarTrigger aria-label="Abrir navegación"><PanelLeft/></SidebarTrigger><div className="h-6 w-px bg-slate-200"/><div><p className="truncate text-sm font-semibold text-slate-950">{active === "inicio" ? "FORECAST Towell" : title}</p><p className="hidden text-xs text-slate-500 sm:block">{active === "inicio" ? "Piloto FENDI BD" : `Septiembre 2026 · Periodo ${periodState.toLowerCase()}`}</p></div></div><div className="flex items-center gap-2"><div className="hidden items-center gap-2 rounded-full border border-slate-200 px-3 py-1.5 sm:flex"><div className="grid size-7 place-items-center rounded-full bg-blue-100 text-xs font-bold text-blue-700">G1</div><div><p className="text-sm font-medium leading-4">Gerencia 1</p><p className="text-[11px] text-slate-500">Gerente</p></div></div></div></header>
      <main className="mx-auto w-full max-w-[1480px] p-4 md:p-7">
        {active === "inicio" && <HomeView onGo={setActive} periodState={periodState}/>}
        {active === "captura" && <CaptureView onCapture={setCapture}/>} 
        {active === "historico" && <HistoryView/>}
        {active === "motor" && <ForecastEnginesView supabaseConfigured={supabaseConfigured}/>}
        {active === "periodos" && <PeriodsView state={periodState} setState={setPeriodState}/>} 
        {active === "calidad" && <QualityView/>} 
        {active === "usuarios" && <UsersView/>} 
        {active === "auditoria" && <AuditView/>}
      </main>
    </SidebarInset>
    <CaptureDialog type={capture} onClose={() => setCapture(null)} onSubmit={submitCapture}/>
    <ForecastAssistantErrorBoundary>
      <ForecastAssistant
        {...assistantConfig}
        context={{
          user: "Gerencia 1",
          role: "manager",
          screen: active,
          activeFilters: { periodState },
          chain: "Walmart",
          category: "FENDI BD",
          product: null,
          color: null,
          period: "2026-09",
        }}
      />
    </ForecastAssistantErrorBoundary>
    <Toaster richColors position="bottom-right"/>
  </SidebarProvider>;
}

function HomeView({ onGo, periodState }: { onGo: (id: ModuleId) => void; periodState: string }) { return <ExecutiveDashboard onGo={onGo} periodState={periodState}/>; }

function CaptureView({ onCapture }: { onCapture: (t: CaptureType) => void }) {
  return <div><PageIntro eyebrow="Operación" title="Centro de captura" copy="Cada dato entra por un formulario guiado, conserva su versión anterior y genera un evento auditable."/><div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">{(Object.entries(captureMeta) as [CaptureType, typeof captureMeta[CaptureType]][]).map(([key,meta]) => <Card key={key} className="group cursor-pointer border-slate-200 shadow-sm transition hover:-translate-y-1 hover:border-blue-300 hover:shadow-lg" onClick={() => onCapture(key)}><CardContent className="p-6"><div className={`grid size-12 place-items-center rounded-2xl ${meta.color} text-white`}><meta.icon/></div><h3 className="mt-8 text-lg font-semibold">{meta.title}</h3><p className="mt-2 min-h-12 text-sm leading-6 text-slate-500">{meta.copy}</p><div className="mt-6 flex items-center text-sm font-semibold text-blue-700">Abrir formulario <ChevronRight className="ml-1 size-4 transition group-hover:translate-x-1"/></div></CardContent></Card>)}</div><Card className="mt-6 border-blue-100 bg-blue-50/50 shadow-none"><CardContent className="flex gap-3 p-5"><ShieldCheck className="mt-0.5 size-5 text-blue-700"/><div><p className="font-semibold text-blue-950">Reglas activas</p><p className="mt-1 text-sm leading-6 text-blue-800">ITEM y UPC se conservan como texto. Vacío significa “Sin dato”; cero significa una cantidad observada de cero. Los periodos cerrados no aceptan cambios ordinarios.</p></div></CardContent></Card></div>;
}

function HistoryView() {
  return <div><PageIntro eyebrow="Consulta" title="Histórico operativo" copy="Vista de solo lectura del universo publicado para el piloto FENDI BD."/><div className="mb-4 flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white p-4 sm:flex-row"><div className="relative flex-1"><Search className="absolute left-3 top-2.5 size-4 text-slate-400"/><Input className="pl-9" placeholder="Buscar por producto, UPC o ITEM"/></div><Select defaultValue="all"><SelectTrigger className="w-full sm:w-44"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">Todos los periodos</SelectItem><SelectItem value="2026-07">Julio 2026</SelectItem><SelectItem value="2026-06">Junio 2026</SelectItem></SelectContent></Select><Select defaultValue="all"><SelectTrigger className="w-full sm:w-40"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">Todos los colores</SelectItem>{products.map((p)=><SelectItem key={p[2]} value={p[2].toLowerCase()}>{p[2]}</SelectItem>)}</SelectContent></Select></div><DataTable headers={["Periodo","Producto","UPC","ITEM","Pedido","Venta","Entrega"]} rows={historyRows}/></div>;
}

function PeriodsView({ state, setState }: { state: string; setState: (s: string) => void }) {
  const rows = [["Sep 2026",state,"70%","En captura"],["Ago 2026","Cerrado","100%","Oficial"],["Jul 2026","Cerrado","100%","Publicado"],["Jun 2026","Cerrado","100%","Publicado"]];
  return <div><PageIntro eyebrow="Calendario" title="Periodos mensuales" copy="La captura, validación, cierre y reapertura se controlan por estado y permiso."/><Card className="mb-5 border-slate-200 shadow-sm"><CardContent className="grid gap-5 p-6 lg:grid-cols-[1fr_auto]"><div><div className="flex flex-wrap items-center gap-3"><h3 className="text-xl font-semibold">Septiembre 2026</h3><StatusBadge tone={state==="Abierto"?"green":state==="En validación"?"amber":"slate"}>{state}</StatusBadge></div><p className="mt-2 text-sm text-slate-500">11 productos · 31 de 44 capturas esperadas · 4 observaciones</p><div className="mt-4 h-2 max-w-xl rounded-full bg-slate-100"><div className="h-full w-[70%] rounded-full bg-blue-600"/></div></div><div className="flex flex-wrap items-center gap-2"><Button variant="outline" disabled={state!=="Abierto"} onClick={()=>{setState("En validación");toast.success("Periodo enviado a validación")}}>Enviar a validación</Button><Button disabled={state!=="En validación"} onClick={()=>{setState("Cerrado");toast.success("Periodo cerrado y versionado")}}>Cerrar periodo</Button><Button variant="outline" disabled={state!=="Cerrado"} onClick={()=>{setState("Reabierto");toast.success("Reapertura registrada")}}>Reabrir</Button></div></CardContent></Card><DataTable headers={["Periodo","Estado","Completitud","Resultado"]} rows={rows}/></div>;
}

function QualityView() {
  const items = [["D01","Definición de segmento BD","Bloqueante","Cadena y equivalencia histórica pendientes de aprobación."],["D09","16 diferencias en 2025","Revisión","Pedido y Entrega difieren en periodos superpuestos."],["D05","Unidad oficial","Bloqueante","Piezas/eaches permanece como interpretación provisional."],["D10","Equivalencia FENDI 2023","Cuarentena","No existe llave reproducible para publicar esos registros."]];
  return <div><PageIntro eyebrow="Control" title="Calidad de datos" copy="Los conflictos permanecen visibles y en cuarentena hasta contar con una decisión gerencial."/><div className="mb-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{[["4","Bloqueos críticos","text-rose-700"],["16","Diferencias 2025","text-amber-700"],["0","Duplicados publicados","text-emerald-700"],["1,350","Registros preparados","text-blue-700"]].map(([n,l,c])=><Card key={l} className="border-slate-200 shadow-sm"><CardContent className="p-5"><p className={`text-3xl font-semibold ${c}`}>{n}</p><p className="mt-2 text-sm text-slate-500">{l}</p></CardContent></Card>)}</div><div className="grid gap-4 lg:grid-cols-2">{items.map(([id,title,status,copy])=><Card key={id} className="border-slate-200 shadow-sm"><CardContent className="p-5"><div className="flex items-start justify-between"><div className="grid size-9 place-items-center rounded-xl bg-slate-100 text-sm font-bold text-slate-600">{id}</div><StatusBadge tone={status==="Bloqueante"?"red":"amber"}>{status}</StatusBadge></div><h3 className="mt-5 font-semibold">{title}</h3><p className="mt-2 text-sm leading-6 text-slate-500">{copy}</p></CardContent></Card>)}</div></div>;
}

function UsersView() {
  return <div><PageIntro eyebrow="Administración" title="Usuarios y permisos" copy="Tres perfiles gerenciales conservan todos los permisos; editores y lectores tienen alcance restringido."/><div className="mb-4 flex justify-end"><Button><UserCog/> Crear usuario</Button></div><DataTable headers={["Usuario","Correo","Rol","Estado"]} rows={users}/><div className="mt-5 grid gap-4 md:grid-cols-3">{[["Gerente","Captura, corrige, aprueba, cierra, reabre y administra."],["Editor","Captura y corrige únicamente periodos abiertos."],["Lector","Consulta información autorizada sin modificar."]].map(([role,copy])=><Card key={role} className="border-slate-200 shadow-sm"><CardContent className="p-5"><p className="font-semibold">{role}</p><p className="mt-2 text-sm leading-6 text-slate-500">{copy}</p></CardContent></Card>)}</div></div>;
}

function AuditView() { return <div><PageIntro eyebrow="Trazabilidad gerencial" title="Auditoría" copy="Cada acción conserva actor, fecha, registro, valor anterior, valor nuevo, motivo y periodo."/><DataTable headers={["Fecha y hora","Actor","Acción","Registro","Cambio"]} rows={audits}/></div>; }

function CaptureDialog({ type, onClose, onSubmit }: { type: CaptureType | null; onClose: () => void; onSubmit: (e: FormEvent<HTMLFormElement>) => void }) {
  if (!type) return null;
  const meta = captureMeta[type]; const Icon = meta.icon;
  return <Dialog open onOpenChange={(open)=>!open&&onClose()}><DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl"><DialogHeader><div className={`mb-2 grid size-11 place-items-center rounded-xl ${meta.color} text-white`}><Icon className="size-5"/></div><DialogTitle>{meta.title}</DialogTitle><DialogDescription>Los campos marcados son obligatorios. El registro creará versión, auditoría y evento.</DialogDescription></DialogHeader><form onSubmit={onSubmit} className="space-y-5">
    {type==="pedido"&&<><Field label="Número de pedido u OC" name="reference" placeholder="OC-2026-0918" required/><div className="grid gap-4 sm:grid-cols-2"><Field label="Fecha de recepción" name="event_date" type="date" required/><Field label="Fecha requerida" name="required_date" type="date" required/></div></>}
    {type==="entrega"&&<><Field label="Buscar pedido" name="order_reference" placeholder="OC-2026-0918" required/><div className="rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-800">Saldo pendiente: <strong>184 piezas</strong></div></>}
    {type==="fcst"&&<div className="grid gap-4 sm:grid-cols-2"><Field label="Fecha de recepción" name="received_at" type="date" required/><Field label="Versión" name="version" placeholder="Cliente v1" required/></div>}
    <div className="grid gap-4 sm:grid-cols-2"><label className="space-y-2"><Label>Periodo {type==="fcst"?"futuro":""}</Label><Select name="period" defaultValue={type==="fcst"?"2026-10":"2026-09"}><SelectTrigger className="w-full"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="2026-09">Septiembre 2026</SelectItem><SelectItem value="2026-10">Octubre 2026</SelectItem><SelectItem value="2026-11">Noviembre 2026</SelectItem></SelectContent></Select></label><label className="space-y-2"><Label>Producto</Label><Select name="product" defaultValue={products[0][1]}><SelectTrigger className="w-full"><SelectValue/></SelectTrigger><SelectContent>{products.map(([,upc,color])=><SelectItem key={upc} value={upc}>{color} · {upc}</SelectItem>)}</SelectContent></Select></label></div>
    <div className="grid gap-4 sm:grid-cols-2"><Field label={type==="pedido"?"Cantidad pedida":type==="venta"?"Unidades vendidas":type==="entrega"?"Cantidad entregada":"Cantidad pronosticada"} name="quantity" type="number" min="0" placeholder="0" required/>{type==="venta"?<Field label="Importe (opcional)" name="amount" type="number" min="0" placeholder="Sin dato"/>:type==="entrega"?<Field label="Fecha de entrega" name="delivery_date" type="date" required/>:type==="fcst"?<Field label="Vigencia" name="valid_until" type="date" required/>:<Field label="Unidad" name="unit" value="piezas" readOnly/>}</div>
    {type==="venta"&&<label className="space-y-2"><Label>Estado</Label><Select name="status" defaultValue="partial"><SelectTrigger className="w-full"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="partial">Parcial</SelectItem><SelectItem value="final">Definitivo</SelectItem></SelectContent></Select></label>}
    <label className="block space-y-2"><Label>Comentarios</Label><Textarea name="comments" placeholder="Contexto, incidencia o referencia documental"/></label><div className="rounded-xl bg-slate-50 p-3 text-xs leading-5 text-slate-600">Vacío conserva “Sin dato”. Escribir 0 registra una observación real de cero.</div><DialogFooter><Button type="button" variant="outline" onClick={onClose}>Cancelar</Button><Button type="submit">Validar y guardar</Button></DialogFooter>
  </form></DialogContent></Dialog>;
}

function Field({ label, name, ...props }: React.ComponentProps<typeof Input> & { label: string; name: string }) { return <label className="block space-y-2"><Label htmlFor={name}>{label}</Label><Input id={name} name={name} {...props}/></label>; }
function PageIntro({ eyebrow, title, copy }: { eyebrow: string; title: string; copy: string }) { return <div className="mb-6"><p className="text-xs font-bold uppercase tracking-[.16em] text-blue-700">{eyebrow}</p><h1 className="mt-2 text-3xl font-semibold tracking-[-.035em] text-slate-950">{title}</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">{copy}</p></div>; }
function DataTable({ headers, rows }: { headers: string[]; rows: string[][] }) { return <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"><div className="overflow-x-auto"><Table><TableHeader><TableRow className="bg-slate-50">{headers.map((h)=><TableHead key={h} className="whitespace-nowrap font-semibold text-slate-700">{h}</TableHead>)}</TableRow></TableHeader><TableBody>{rows.map((row,i)=><TableRow key={`${row[0]}-${i}`}>{row.map((cell,c)=><TableCell key={c} className={`whitespace-nowrap ${c===0?"font-medium":"text-slate-600"}`}>{cell}</TableCell>)}</TableRow>)}</TableBody></Table></div></div>; }
