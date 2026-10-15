"use client";

import { useState } from "react";
import { modelKey } from "@/lib/commercial-filters";

type Option = { id: string; name: string; count: number; statuses: string[] };
export function ModelMultiselect({ value, options, disabled, onChange }: { value: string[] | null; options: Option[]; disabled: boolean; onChange: (value: string[] | null) => void }) {
  const [search, setSearch] = useState("");
  const selected = value === null ? options.map(o => o.id) : value;
  const visible = options.filter(o => modelKey(o.name).includes(modelKey(search)));
  return <details className="relative min-w-0 self-start rounded-md border border-slate-200 bg-white">
    <summary aria-label="Modelo" className="flex min-h-10 cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-sm font-medium"><span>Modelo</span><span className="ml-auto text-xs text-slate-500">{selected.length} seleccionados{value === null ? " · Todos" : ""}</span>
      {value !== null && <span className="flex w-full flex-wrap gap-1">{selected.slice(0, 3).map(id => <span key={id} className="max-w-full truncate rounded bg-blue-50 px-2 py-1 text-xs text-blue-800">{options.find(o => o.id === id)?.name ?? id}</span>)}{selected.length > 3 && <span className="text-xs">+{selected.length - 3}</span>}{!selected.length && <span className="text-xs text-slate-500">Sin selección</span>}</span>}
    </summary>
    <fieldset disabled={disabled} className="space-y-3 border-t p-3">
      <legend className="sr-only">Seleccionar modelos</legend>
      <input type="search" aria-label="Buscar modelos" placeholder="Buscar modelos" value={search} onChange={e => setSearch(e.target.value)} className="h-10 w-full min-w-0 rounded border border-slate-200 px-2 text-sm"/>
      <div className="flex flex-wrap gap-3 text-xs"><button type="button" onClick={() => onChange(null)} className="min-h-9 text-blue-700">Seleccionar todos</button><button type="button" onClick={() => onChange([])} className="min-h-9 text-slate-600">Limpiar selección</button></div>
      <div className="max-h-60 space-y-1 overflow-y-auto" role="group" aria-label="Modelos disponibles">{visible.map(o => <label key={o.id} title={`${o.count} productos · ${o.statuses.join(", ")}`} className="flex min-h-10 cursor-pointer items-center gap-2 text-sm"><input type="checkbox" checked={selected.includes(o.id)} onChange={e => onChange(e.target.checked ? [...selected, o.id] : selected.filter(id => id !== o.id))}/><span className="min-w-0 break-words">{o.name}</span><span className="ml-auto text-xs text-slate-400">{o.count}</span></label>)}{!visible.length && <p className="text-xs text-slate-500">Sin modelos coincidentes</p>}</div>
      <details className="text-xs text-slate-500"><summary className="cursor-pointer">Estados de clasificación</summary>{visible.map(o => <p key={o.id} className="mt-2 break-words">{o.name}: {o.statuses.join(", ")}</p>)}</details>
    </fieldset>
  </details>;
}
