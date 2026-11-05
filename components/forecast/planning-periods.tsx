"use client";
import { useMemo, useState } from "react";
import { canonicalLabel, chartRows, filterPlanning, nextWindow, noPlanningFilters, parsePlanning, planningSheets, projectionAt, scopeKey, type PlanningBundle, type PlanningFilters } from "@/lib/planning-results";
import { dashboardSeries, type DashboardPoint, type DashboardSeries } from "@/lib/periods-dashboard";
import { PeriodsComparisonChart } from "./periods-comparison-chart";
import { unitCoverageRows, unitView, validateUnitCatalog, type UnitCatalog } from "@/lib/unit-equivalences";

const number = (v: string | number | null) => v === null ? "Sin dato" : typeof v === "number" ? v.toLocaleString("es-MX", { maximumFractionDigits: 6 }) : v;
function Multi({ label, values, options, onChange }: { label: string; values: string[]; options: { value: string; label: string }[]; onChange: (v: string[]) => void }) {
  return <fieldset className="min-w-0"><legend className="text-sm font-medium">{label}</legend><p className="text-xs text-slate-500">Sin selección = todos. Ctrl/Cmd permite varios.</p><select className="mt-1 h-28 w-full rounded border border-slate-300 text-sm" multiple aria-label={label} value={values} onChange={e=>onChange(Array.from(e.target.selectedOptions,o=>o.value))}>{options.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}</select><button type="button" className="text-xs underline" onClick={()=>onChange([])}>Todos</button></fieldset>;
}
export function PlanningPeriods({ initialBundle }: { initialBundle?: PlanningBundle }) {
  const [bundle,setBundle] = useState(initialBundle ?? null), [error,setError] = useState("");
  const [catalog,setCatalog] = useState<UnitCatalog|null>(null), [pieces,setPieces] = useState(false);
  const derived = useMemo(()=>bundle&&catalog?unitView(bundle,catalog):bundle,[bundle,catalog]);
  const [selected,setSelected] = useState(initialBundle?.versions.at(-1)?.id ?? "");
  const [filters,setFilters] = useState<PlanningFilters>(noPlanningFilters), [view,setView] = useState("Por color"), [window,setWindow] = useState<2|3>(2), [page,setPage] = useState(0);
  const version = derived?.versions.find(v=>v.id===selected);
  const products = useMemo(()=>version ? filterPlanning(version,filters):[],[version,filters]);
  const sheets = useMemo(()=>{
    if(!derived||!version)return [];
    const result=planningSheets(derived,version,filters);
    const provisional=result.find(s=>s.name==="Pronostico_provisional")!;
    const months=provisional.headers.filter(h=>/^20\d\d-\d\d$/.test(h));
    provisional.headers.push(...months.map(m=>`Piezas acreditadas ${m}`));
    provisional.rows.forEach((row,i)=>row.push(...months.map(m=>projectionAt(products[i],m,true))));
    const guide=result.find(s=>s.name==="Guia_y_control")!;
    const equivalent=guide.rows.find(r=>r[0]==="Variantes con equivalencia a piezas");
    if(equivalent)equivalent[1]=products.filter(p=>p.projection.some(h=>h.pieces!==null)).length;
    const exceptions=result.find(s=>s.name==="Excepciones_y_linaje")!;
    exceptions.rows.forEach((row,i)=>{if(products[i].projection.some(h=>h.pieces!==null))row[10]="Equivalencia por periodo; consultar Unidades";});
    result.push({name:"Unidades",title:"Unidades y equivalencias por fuente y periodo",note:"Cantidades nativas intactas. Piezas sólo con evidencia y vigencia aplicable; el empaque no acredita la unidad. Sin conversiones entre fuentes.",headers:["Variante","Fuente","Periodo","Tipo","Cantidad original","Unidad acreditada","Factor a piezas","Piezas acreditadas","Consolidable","Estado / motivo","Evidencia","Responsable"],rows:unitCoverageRows(version,catalog,products)});
    return result;
  },[derived,version,filters,products,catalog]);
  const viewSheets: Record<string,string> = {"Por color":"Champion_por_color","Histórico":"Historico_por_color","Por modelo":"Plan_anual_por_modelo","Provisionales":"Pronostico_provisional","Unidades":"Unidades","Revisiones":"Revisiones_y_reales","WAPE":"Metricas_y_cobertura","Excepciones":"Excepciones_y_linaje"};
  const sheet = sheets.find(s=>s.name===viewSheets[view]);
  const mode = view==="Por modelo" ? "model" : view==="Provisionales"||view==="Unidades" ? "provisional" : "color";
  const points = useMemo(()=>version ? chartRows(products,version.cutoff,mode,pieces).map(r=>{
    const values = Object.fromEntries(Object.keys(dashboardSeries).map(k=>[k,null])) as DashboardPoint["values"];
    Object.assign(values,{previous2:r.previous2,previous1:r.previous1,actual:r.actual,STATISTICAL:r.forecast});
    return {month:r.period,period:r.period,values,coverage:{...Object.fromEntries(Object.entries(values).map(([key,value])=>[key,{value,covered:0,eligible:products.length}])),...r.coverage} as Record<DashboardSeries,{value:number|null;covered:number;eligible:number}>};
  }):[],[products,version,mode,pieces]);
  function change(key: keyof PlanningFilters, value: string[]) {
    setFilters(f=>({...f,[key]:value,...(key==="scopes"?{models:[],colors:[],sizes:[],products:[]}:key==="models"?{colors:[],sizes:[],products:[]}:key==="colors"||key==="sizes"?{products:[]}: {})}));setPage(0);
  }
  const scoped=version?filterPlanning(version,{...filters,models:[],colors:[],sizes:[],products:[]}):[];
  const modeled=version?filterPlanning(version,{...filters,colors:[],sizes:[],products:[]}):[];
  const colored=version?filterPlanning(version,{...filters,products:[]}):[];
  const opts=(values:string[])=>[...new Set(values)].sort().map(value=>({value,label:value}));
  return <section className="min-w-0 space-y-5" aria-label="Planeación privada de resurtibles">
    <div><h2 className="text-2xl font-semibold">Periodos · Planeación privada</h2><p className="text-sm text-slate-600">Abre el paquete JSON entregado con el XLSX. Se consulta sólo en memoria de este navegador; no se envía al servidor.</p></div>
    <label className="block text-sm">Abrir paquete privado<input aria-label="Abrir paquete privado" className="ml-3" type="file" accept=".json" onChange={async e=>{ const file=e.target.files?.[0];if(!file)return;try{if(file.size>32_000_000)throw new Error();const data=parsePlanning(JSON.parse(await file.text()));setBundle(data);setCatalog(null);setPieces(false);setSelected(data.versions.at(-1)!.id);setFilters(noPlanningFilters);setPage(0);setError("");}catch{setError("Paquete inválido o mayor a 32 MB. Se conserva la última consulta válida.");}}}/></label>
    {error&&<p role="alert">{error}</p>}
    {version&&<>
      <div className="rounded border border-slate-200 p-3 text-sm space-y-2">
        <label className="block">Importar equivalencias validadas<input aria-label="Importar equivalencias validadas" className="ml-3" type="file" accept=".json" onChange={async e=>{const file=e.target.files?.[0];if(!file||!bundle)return;try{if(file.size>16_000_000)throw new Error("Catálogo mayor a 16 MB");const candidate=validateUnitCatalog(JSON.parse(await file.text()),bundle.versions.at(-1)!);unitView(bundle,candidate);setCatalog(candidate);setPage(0);setError("");}catch(err){setError(`Equivalencias rechazadas: ${err instanceof Error?err.message:"archivo inválido"}. Se conserva la vista anterior.`);}}}/></label>
        <p>Utiliza el JSON generado por el importador del catálogo Excel. La validación documental corresponde a Planeación.</p>
        <p>{catalog?`${catalog.rows.filter(r=>r.status==="Validado").length} equivalencias validadas · ${catalog.rows.filter(r=>r.status==="Pendiente").length} pendientes`:"Sin catálogo de equivalencias importado"}. Vista derivada en memoria; V01 y su emisión permanecen intactas.</p>
        {catalog&&<button type="button" className="underline" onClick={()=>{setCatalog(null);setPieces(false);setPage(0);}}>Quitar equivalencias</button>}
      </div>
      <div className="flex flex-wrap gap-4 text-sm"><label>Versión <select aria-label="Versión de planeación" value={selected} onChange={e=>{setSelected(e.target.value);setPage(0);}}>{bundle!.versions.map(v=><option key={v.id}>{v.id}</option>)}</select></label><span>Corte: {version.cutoff}</span><span>Cierre acreditado: {version.last_closed} · {version.closure_evidence}</span><span>Emisión: {version.issued_at}</span></div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5"><Multi label="Cadena / formato" values={filters.scopes} options={opts(version.products.map(scopeKey))} onChange={v=>change("scopes",v)}/><Multi label="Modelo / artículo–tamaño" values={filters.models} options={opts(scoped.map(p=>canonicalLabel(p.model)))} onChange={v=>change("models",v)}/><Multi label="Color / surtido" values={filters.colors} options={opts(modeled.map(p=>canonicalLabel(p.color)))} onChange={v=>change("colors",v)}/><Multi label="Tamaño / presentación" values={filters.sizes} options={opts(modeled.map(p=>canonicalLabel(p.size)))} onChange={v=>change("sizes",v)}/><Multi label="Producto" values={filters.products} options={colored.map(p=>({value:p.id,label:`${p.description} · ${p.barcode}`}))} onChange={v=>change("products",v)}/></div>
      <p className="text-sm">{products.length} variantes · {products.filter(p=>p.status==="PROVISIONAL").length} con proyección provisional · Sin Champion acreditado. Las clasificaciones conservan sus pendientes.</p>
      <p data-testid="unit-summary" className="rounded bg-amber-50 p-3 text-sm">{products.filter(p=>p.projection.some(h=>h.pieces!==null)).length} productos con pronóstico convertible a piezas · {products.filter(p=>!p.projection.some(h=>h.pieces!==null)).length} sin pronóstico acreditado en piezas. Consolidables por modelo: {products.filter(p=>p.commercial_code&&p.projection.some(h=>h.pieces!==null)).length}. Cobertura nativa: {products.reduce((n,p)=>n+p.projection.filter(h=>h.value!==null).length,0)}/{products.length*14} producto-meses; en piezas: {products.reduce((n,p)=>n+p.projection.filter(h=>h.pieces!==null).length,0)}. Consultar Unidades para motivos y vigencias.</p>
      <label className="block text-sm">Vista <select aria-label="Vista de planeación" value={view} onChange={e=>{setView(e.target.value);setPage(0);}}>{Object.keys(viewSheets).map(v=><option key={v}>{v}</option>)}</select></label>
      <label className="block text-sm">Unidad de la gráfica <select aria-label="Unidad de la gráfica" value={pieces?"pieces":"native"} onChange={e=>setPieces(e.target.value==="pieces")}><option value="native">Origen por variante; piezas para consolidados</option><option value="pieces">Piezas acreditadas</option></select></label>
      <PeriodsComparisonChart key={`${selected}/${mode}`} points={points} year={Number(version.cutoff.slice(0,4))} planning={{unit:!pieces&&products.length===1&&mode!=="model"?products[0].unit??"Unidad de origen":"Piezas acreditadas; cobertura parcial",provisional:mode!=="color"}}/>
      <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0"><h3 className="font-semibold">{sheet?.title}</h3><p className="mb-3 text-xs text-slate-500">{sheet?.note}</p><div className="max-h-[600px] overflow-auto"><table className="w-full text-sm" aria-label="Matriz de planeación"><thead className="sticky top-0 bg-slate-100"><tr>{sheet?.headers.map((h,i)=><th key={i} className="min-w-32 border-b p-2 text-left">{h}</th>)}</tr></thead><tbody>{sheet?.rows.slice(page*25,(page+1)*25).map((r,i)=><tr key={i}>{r.map((c,j)=><td key={j} className="border-b p-2">{number(c)}</td>)}</tr>)}</tbody></table></div><div className="flex gap-4 py-3"><button disabled={!page} onClick={()=>setPage(p=>p-1)}>Anterior</button><span>{page+1} / {Math.max(1,Math.ceil((sheet?.rows.length??0)/25))}</span><button disabled={(page+1)*25>=(sheet?.rows.length??0)} onClick={()=>setPage(p=>p+1)}>Siguiente</button></div></div>
        <aside><h3 className="font-semibold">Estadístico · ventana posterior al corte</h3><label className="text-sm">Meses <select aria-label="Meses próximos" value={window} onChange={e=>setWindow(Number(e.target.value) as 2|3)}><option value={2}>2</option><option value={3}>3</option></select></label><p className="my-2 text-xs">{nextWindow(version.cutoff,window).join(" / ")} · Mismos periodos y versión; no sumar dos veces.</p>{products.length===1 ? <><p>{products[0].method??"Sin método"} · Provisional · {products[0].unit}</p>{nextWindow(version.cutoff,window).map(m=><p key={m}>{m}: {number(projectionAt(products[0],m))}</p>)}</> : <p className="text-sm">Selecciona una variante para consultar el método y su ventana. El consolidado en piezas está en Por modelo.</p>}</aside>
      </div>
    </>}
  </section>;
}
