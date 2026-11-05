import { z } from "zod";

const period = z.string().regex(/^20\d\d-(0[1-9]|1[0-2])$/);
const amount = z.number().finite().nonnegative().nullable();
const observation = z.object({ value: amount, original: z.union([z.string(), z.number(), z.null()]), cell: z.string(), pack_factor: z.union([z.string(), z.number(), z.null()]), unit: z.string(), pieces: amount, unit_evidence: z.string().nullable() });
const projection = z.object({ period, horizon: z.number().int().min(1).max(14), value: amount, pieces: amount, reason: z.string() });
const metric = z.object({ value: amount, absolute_error: z.number().optional(), signed_error: z.number().optional(), bias: z.number().finite().nullable().optional(), pairs: z.number(), denominator: z.number(), basis: z.string(), reason: z.string(), temporal_certification: z.literal(false) });
const product = z.object({ id: z.string(), account: z.string(), chain: z.string(), format: z.string(), barcode: z.string(), description: z.string(), model: z.string(), color: z.string(), size: z.string(), commercial_code: z.string().nullable().optional(),
  history: z.record(z.record(observation)), issues: z.array(z.string()), champion: z.null(), champion_reason: z.string(),
  proposal: z.object({ period, value: amount, unit: z.string(), cell: z.string(), code_cell: z.string(), code_evidence: z.string() }).nullable(),
  method: z.string().nullable(), projection: z.array(projection), status: z.enum(["PENDING", "PROVISIONAL"]), reason: z.string().nullable(), unit: z.string().optional(), pieces_factor: z.number().nullable().optional(), retrospective: z.record(metric), training_start: period.optional(), pack_conflict: z.boolean().optional(),
});
const version = z.object({ id: z.string(), issued_at: z.string().datetime({ offset: true }), cutoff: period, last_closed: period, closure_evidence: z.string(), closure_confirmed_on: z.string().nullable().optional(), closure_confirmed_by: z.string().optional(), last_quantity: period.nullable(), code_sha: z.string(), engine: z.string(), policy: z.string(), original_id: z.string(), previous_hash: z.string().nullable(), hash: z.string(), audit: z.record(z.unknown()), products: z.array(product), live: z.string() });
const piecesConfirmation = z.object({ prd: z.literal("3A.1"), unit: z.literal("PIEZAS"), sources: z.tuple([z.literal("Towell"),z.literal("Cliente"),z.literal("Pedidos")]), statement: z.string().min(1), confirmed_by: z.string().min(1), recorded_at: z.string().datetime({offset:true}), presentation_version: z.string().min(1), base_versions: z.array(z.object({id:z.string(),hash:z.string()})).min(1), source_file_sha256: z.string().regex(/^[a-f0-9]{64}$/) });
export const planningSchema = z.object({ schema: z.literal("towell-planning-1"), versions: z.array(version).min(1), pieces_confirmation: piecesConfirmation.optional() });
export type PlanningBundle = z.infer<typeof planningSchema>;
export type PlanningVersion = PlanningBundle["versions"][number];
export type PlanningProduct = PlanningVersion["products"][number];
export type PlanningFilters = { scopes: string[]; models: string[]; colors: string[]; sizes: string[]; products: string[] };
export const noPlanningFilters: PlanningFilters = { scopes: [], models: [], colors: [], sizes: [], products: [] };
export const canonicalLabel = (v: string) => v.normalize("NFC").trim().replace(/\s+/g, " ").toLocaleUpperCase("es-MX");
export const scopeKey = (p: PlanningProduct) => `${p.account} / ${p.format}`;
export function addPlanningMonth(p: string, offset: number) {
  const [y, m] = p.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1 + offset, 1)).toISOString().slice(0, 7);
}
export function nextWindow(cutoff: string, count: 2 | 3) { return Array.from({ length: count }, (_, i) => addPlanningMonth(cutoff, i + 1)); }
export function planMonths(cutoff: string) { return Array.from({ length: 12 }, (_, i) => `${Number(cutoff.slice(0, 4)) + 1}-${String(i + 1).padStart(2, "0")}`); }
export function parsePlanning(value: unknown): PlanningBundle {
  const bundle = planningSchema.parse(value);
  const ids = new Set<string>();
  for (const v of bundle.versions) {
    if (ids.has(v.id)) throw new Error("Versión repetida");
    ids.add(v.id);
    const products = new Set<string>();
    for (const p of v.products) {
      if (products.has(p.id)) throw new Error("Variante repetida");
      products.add(p.id);
      const periods = new Set<string>();
      for (const point of p.projection) {
        if (periods.has(point.period) || point.period !== addPlanningMonth(v.cutoff, point.horizon)) throw new Error("Horizonte incompatible");
        periods.add(point.period);
        if (point.pieces !== null && (!p.pieces_factor || point.value === null || Math.abs(point.pieces - point.value * p.pieces_factor) > 1e-8)) throw new Error("Conversión sin evidencia");
      }
    }
    if (v.original_id !== bundle.versions[0].id) throw new Error("Plan original incompatible");
  }
  return bundle;
}
export function filterPlanning(v: PlanningVersion, f: PlanningFilters) {
  return v.products.filter(p => (!f.scopes.length || f.scopes.includes(scopeKey(p))) && (!f.models.length || f.models.includes(canonicalLabel(p.model))) && (!f.colors.length || f.colors.includes(canonicalLabel(p.color))) && (!f.sizes.length || f.sizes.includes(canonicalLabel(p.size))) && (!f.products.length || f.products.includes(p.id)));
}
export function projectionAt(p: PlanningProduct | undefined, month: string, pieces = false) {
  const point = p?.projection.find(h => h.period === month);
  return (pieces ? point?.pieces : point?.value) ?? null;
}
export function modelRows(products: PlanningProduct[], months: string[]) {
  const groups = new Map<string, PlanningProduct[]>();
  for (const p of products) {
    // Missing commercial code is isolated per variant; never guess a mapping.
    const key = JSON.stringify([p.account, p.format, canonicalLabel(p.model), p.commercial_code ?? p.id]);
    groups.set(key, [...(groups.get(key) ?? []), p]);
  }
  return [...groups.entries()].map(([id, rows]) => ({ id, chain: rows[0].chain, format: rows[0].format, model: canonicalLabel(rows[0].model), code: rows[0].commercial_code ?? "Sin código acreditado", expected: rows.length,
    months: months.map(month => { const values = rows.map(p => p.commercial_code ? projectionAt(p, month, true) : null).filter((x): x is number => x !== null); return { period: month, value: values.length ? values.reduce((s, x) => s + x, 0) : null, covered: values.length }; }),
  }));
}
export function chartRows(products: PlanningProduct[], cutoff: string, mode: "color" | "provisional" | "model", pieces = false) {
  const single = products.length === 1 && mode !== "model" && !pieces;
  const total = (values: (number | null)[]) => ({ value: values.some(x => x !== null) ? values.reduce<number>((s, x) => s + (x ?? 0), 0) : null, covered: values.filter(x=>x!==null).length, eligible: products.length });
  return Array.from({ length: 12 }, (_, i) => {
    const period = addPlanningMonth(cutoff, i - 8);
    const actual = (offset: number, source: string) => total(products.map(p => {
      const obs = p.history[source]?.[addPlanningMonth(period, offset)];
      return (single ? obs?.value : obs?.pieces) ?? null;
    }));
    const coverage = { previous2: actual(-24,"Towell"), previous1: actual(-12,"Towell"), actual: i<9 ? actual(0,"Towell") : total([]), STATISTICAL: i>=9&&mode!=="color" ? total(products.map(p=>mode==="model"&&!p.commercial_code?null:projectionAt(p,period,!single))) : total([]) };
    return { period, previous2: coverage.previous2.value, previous1: coverage.previous1.value, actual: coverage.actual.value, forecast: coverage.STATISTICAL.value, coverage };
  });
}
type Cell = string | number | null;
export type PlanningSheet = { name: string; title: string; note: string; headers: string[]; rows: Cell[][] };

/** PRD 3A.1 declares the existing amounts to be pieces; no scaling or forecasting. */
export function parsePiecesPlanning(input: unknown): PlanningBundle {
  const bundle=parsePlanning(input), confirmation=bundle.pieces_confirmation;
  if(!confirmation)throw new Error("Se requiere el paquete PIEZAS con confirmación PRD 3A.1; V01 permanece intacta");
  if(confirmation.base_versions.length!==bundle.versions.length || new Set(confirmation.base_versions.map(v=>v.id)).size!==bundle.versions.length || bundle.versions.some(v=>!confirmation.base_versions.some(b=>b.id===v.id&&b.hash===v.hash)))throw new Error("Confirmación PIEZAS incompatible con las versiones originales");
  return bundle;
}
export function piecesView(bundle: PlanningBundle): PlanningBundle {
  parsePiecesPlanning(bundle);
  const copy=structuredClone(bundle);
  for(const v of copy.versions)for(const p of v.products){
    p.unit="PIEZAS";
    for(const observations of Object.values(p.history))for(const o of Object.values(observations)){o.unit="PIEZAS";o.pieces=o.value;}
    for(const point of p.projection)point.pieces=point.value;
  }
  return copy;
}
export function piecesModelRows(products: PlanningProduct[],months:string[]) {
  const groups=new Map<string,PlanningProduct[]>(),seen=new Set<string>();
  for(const p of products){
    if(seen.has(p.id))throw new Error("Identidad repetida; no consolidar dos veces");seen.add(p.id);
    const key=JSON.stringify([p.account,p.chain,p.format,canonicalLabel(p.model),canonicalLabel(p.size),p.commercial_code||p.id]);
    groups.set(key,[...(groups.get(key)??[]),p]);
  }
  return [...groups.entries()].map(([key,rows])=>({key,account:rows[0].account,chain:rows[0].chain,format:rows[0].format,model:canonicalLabel(rows[0].model),size:canonicalLabel(rows[0].size),code:rows[0].commercial_code??"Sin código; identidad aislada",ids:rows.map(p=>p.id),expected:rows.length,
    months:months.map(period=>{const values=rows.map(p=>projectionAt(p,period)).filter((x):x is number=>x!==null);return {period,value:values.length?values.reduce((a,b)=>a+b,0):null,covered:values.length};})}));
}
export function piecesChartRows(products:PlanningProduct[],cutoff:string,mode:"color"|"provisional"|"model",source:"Towell"|"Cliente"|"Pedidos"="Towell") {
  const total=(items:(number|null)[])=>({value:items.some(x=>x!==null)?items.reduce<number>((n,x)=>n+(x??0),0):null,covered:items.filter(x=>x!==null).length,eligible:products.length});
  return Array.from({length:12},(_,i)=>{
    const period=addPlanningMonth(cutoff,i-8);
    const actual=(offset:number)=>total(products.map(p=>p.history[source]?.[addPlanningMonth(period,offset)]?.value??null));
    const coverage={previous2:actual(-24),previous1:actual(-12),actual:i<9?actual(0):total([]),STATISTICAL:i>=9&&mode!=="color"?total(products.map(p=>projectionAt(p,period))):total([])};
    return {period,coverage,previous2:coverage.previous2.value,previous1:coverage.previous1.value,actual:coverage.actual.value,forecast:coverage.STATISTICAL.value};
  });
}
/** One prior issuance per product/month; no in-month issue can qualify as LIVE. */
export function piecesLiveMetric(bundle:PlanningBundle,current:PlanningVersion,productId:string,source:"Towell"|"Cliente"|"Pedidos") {
  const product=current.products.find(p=>p.id===productId);
  let pairs=0,error=0,denominator=0;
  for(const [month,actual] of Object.entries(product?.history[source]??{})){
    if(actual.value===null||month>current.last_closed)continue;
    const candidates=bundle.versions.filter(v=>v.issued_at<=current.issued_at && Date.parse(v.issued_at)<Date.parse(`${month}-01T00:00:00Z`))
      .map(v=>({v,p:v.products.find(p=>p.id===productId)})).filter(({p})=>p&&["account","chain","format","barcode","model","size","color"].every(k=>p[k as keyof PlanningProduct]===product![k as keyof PlanningProduct]));
    candidates.sort((a,b)=>Date.parse(b.v.issued_at)-Date.parse(a.v.issued_at));
    const chosen=candidates.find(({p})=>projectionAt(p,month)!==null),forecast=projectionAt(chosen?.p,month);
    if(forecast===null)continue;
    pairs++;error+=Math.abs(actual.value-forecast);denominator+=Math.abs(actual.value);
  }
  return {value:pairs&&denominator>0?100*error/denominator:null,pairs,error,denominator,reason:!pairs?"Sin pronóstico emitido antes del mes y real cerrado comparable":denominator===0?"Denominador real cero":"LIVE en PIEZAS; última emisión previa por mes"};
}
export function piecesPlanningSheets(bundle:PlanningBundle,v:PlanningVersion,f=noPlanningFilters):PlanningSheet[]{
  const c=bundle.pieces_confirmation;if(!c)throw new Error("Falta confirmación PIEZAS");
  const original=bundle.versions[0],products=filterPlanning(v,f),annual=planMonths(original.cutoff),future=Array.from({length:14},(_,i)=>addPlanningMonth(original.cutoff,i+1));
  const history=[...new Set(products.flatMap(p=>Object.values(p.history).flatMap(h=>Object.keys(h))))].sort();
  const header=["Identidad","Cuenta","Cadena","Formato","Barras","Modelo","Tamaño","Color / surtido","Código comercial"];
  const ids=(p:PlanningProduct):Cell[]=>[p.id,p.account,p.chain,p.format,p.barcode,p.model,p.size,p.color,p.commercial_code??"Sin código; identidad aislada"];
  const note=`PIEZAS · ${c.presentation_version} · Cálculo ${v.id} · Emisión original ${v.issued_at} · Corte ${v.cutoff}`;
  const cleanReason=(p:PlanningProduct)=>p.status==="PENDING"?p.reason??"Sin proyección disponible":"Provisional; no adoptado";
  const model=piecesModelRows(products,annual);
  const historySheets:PlanningSheet[]=(['Towell','Cliente','Pedidos'] as const).map(s=>({name:`Historico_${s}`,title:`Histórico ${s} · PIEZAS`,note:note+" · Serie independiente. Vacíos y ceros conservados.",headers:[...header,...history],rows:products.map(p=>[...ids(p),...history.map(m=>p.history[s]?.[m]?.value??null)])}));
  return [
    {name:"Guia_y_control",title:"FORECAST Towell · Planeación en PIEZAS",note,headers:["Concepto","Valor"],rows:[["Versión de presentación",c.presentation_version],["Unidad","PIEZAS"],["Confirmación de negocio",c.statement],["Confirmado por",c.confirmed_by],["Registro de confirmación",c.recorded_at],["Emisión original del cálculo",v.issued_at],["Último cierre acreditado",v.last_closed],["Identidades",products.length],["Fuentes independientes","Towell / Cliente / Pedidos; nunca se suman entre sí"],["Provisionales",products.filter(p=>p.status==="PROVISIONAL").length],["Sin pronóstico",products.filter(p=>p.status==="PENDING").length],["Champion acreditado",0],["Cobertura producto-mes",`${products.reduce((n,p)=>n+p.projection.filter(h=>h.value!==null).length,0)}/${products.length*14}`],["Revisiones","Sólo emisiones existentes; V02-PIEZAS no es una revisión del pronóstico"],["Original preservado",original.id],["SHA256 archivo base",c.source_file_sha256],["Motor",v.engine],["Hash del cálculo original",v.hash],["WAPE","LIVE exige emisión anterior al primer día del mes, real cerrado y misma identidad; retrospectivo separado"]]},
    {name:"Productos",title:"Catálogo de productos · Una fila por identidad",note,headers:[...header,"Descripción original","Estado","Método estadístico","Meses cubiertos / 14","Motivo"],rows:products.map(p=>[...ids(p),p.description,p.status,p.method,`${p.projection.filter(h=>h.value!==null).length}/14`,cleanReason(p)])},
    ...historySheets,
    {name:"Champion_por_color",title:"Champion por color · Sin Champion acreditado",note,headers:[...header,"Estado",...future],rows:products.map(p=>[...ids(p),"Sin Champion",...future.map(()=>null)])},
    {name:"Pronostico_provisional",title:"Estadístico · Provisionales en PIEZAS",note:note+" · Cantidades V01 sin recalcular; consultar método por producto.",headers:[...header,"Método","Estado",...future,...future.map(m=>`Cobertura ${m}`)],rows:products.map(p=>[...ids(p),p.method,p.status,...future.map(m=>projectionAt(p,m)),...future.map(m=>projectionAt(p,m)===null?p.projection.find(h=>h.period===m)?.reason??"Sin cobertura":"Disponible; provisional")])},
    {name:"Plan_anual_por_modelo",title:"Plan anual por modelo · PIEZAS",note:note+" · Cuenta, cadena, formato, modelo, tamaño y código; total parcial cuando falten variantes.",headers:["Cuenta","Cadena","Formato","Modelo","Tamaño","Código comercial","Variantes esperadas",...annual,...annual.map(m=>`Cubiertas ${m}`),"Total parcial anual","Estado"],rows:model.map(r=>[r.account,r.chain,r.format,r.model,r.size,r.code,r.expected,...r.months.map(m=>m.value),...r.months.map(m=>`${m.covered}/${r.expected}`),r.months.some(m=>m.value!==null)?r.months.reduce((n,m)=>n+(m.value??0),0):null,r.months.every(m=>m.covered===r.expected)?"Completo":"Parcial / sin cobertura"])},
    {name:"Complemento_2026",title:"Complemento noviembre–diciembre · PIEZAS",note,headers:[...header,...future.filter(m=>m<annual[0])],rows:products.map(p=>[...ids(p),...future.filter(m=>m<annual[0]).map(m=>projectionAt(p,m))])},
    {name:"Proximos_2_3_meses",title:"Próximos dos meses y tercero opcional · PIEZAS",note:note+" · Ventana de consulta; no sumar otra vez al plan anual.",headers:[...header,"Método",...nextWindow(v.cutoff,3)],rows:products.map(p=>[...ids(p),p.method,...nextWindow(v.cutoff,3).map(m=>projectionAt(p,m))])},
    {name:"Revisiones_y_reales",title:"Plan original, revisiones y reales · PIEZAS",note:note+" · Una fila por identidad, mes y emisión; tres reales independientes.",headers:["Cálculo","Emisión original","Corte","Identidad","Periodo","Plan original","Revisión","Venta Towell","Pedido Towell","Cliente","Cambio vs original"],rows:bundle.versions.filter(r=>r.issued_at<=v.issued_at).flatMap(r=>r.products.filter(p=>products.some(q=>q.id===p.id)).flatMap(p=>future.map(m=>{const old=projectionAt(original.products.find(q=>q.id===p.id),m),now=projectionAt(p,m),latest=products.find(q=>q.id===p.id)!;return [r.id,r.issued_at,r.cutoff,p.id,m,old,now,...["Towell","Pedidos","Cliente"].map(s=>m<=v.last_closed?latest.history[s]?.[m]?.value??null:null),old!==null&&now!==null?now-old:null];})))},
    {name:"Metricas_y_cobertura",title:"WAPE por fuente · PIEZAS",note:"LIVE y retrospectivo separados. La declaración de unidades no crea evidencia temporal ni pares retrospectivos nuevos. No promediar porcentajes entre productos.",headers:["Identidad",...["Towell","Pedidos","Cliente"].flatMap(s=>[`WAPE LIVE ${s} %`,`Pares LIVE ${s}`,`Motivo LIVE ${s}`,`WAPE retrospectivo ${s} %`,`Pares retrospectivos ${s}`]),"Meses cubiertos / 14"],rows:products.map(p=>[p.id,...(["Towell","Pedidos","Cliente"] as const).flatMap(s=>{const live=piecesLiveMetric(bundle,v,p.id,s),m=p.retrospective[s];return [live.value,live.pairs,live.reason,m?.value??null,m?.pairs??0];}),`${p.projection.filter(h=>h.value!==null).length}/14`])},
    {name:"Trazabilidad",title:"Referencias originales para auditoría",note:note+" · Los datos originales completos permanecen en el JSON base y V01.",headers:[...header,"Descripción original","Celda propuesta",...['Towell','Cliente','Pedidos'].map(s=>`Celdas ${s}`),"Hash fuente original"],rows:products.map(p=>[...ids(p),p.description,p.proposal?.cell??null,...['Towell','Cliente','Pedidos'].map(s=>Object.entries(p.history[s]??{}).map(([m,o])=>`${m}: ${o.cell}`).join("; ")),String(v.audit.source_sha256)])},
  ];
}
export function planningSheets(bundle: PlanningBundle, v: PlanningVersion, f = noPlanningFilters): PlanningSheet[] {
  const original = bundle.versions[0];
  const products = filterPlanning(v, f), annual = planMonths(original.cutoff), future = Array.from({ length: 14 }, (_, i) => addPlanningMonth(original.cutoff, i+1));
  const historical = Array.from({ length: 36 }, (_, i) => addPlanningMonth(`${Number(v.cutoff.slice(0, 4))-2}-01`, i));
  const ids = (p: PlanningProduct) => [p.id, p.chain, p.format, p.barcode, p.model, p.color, p.size, p.commercial_code ?? "Sin código acreditado"];
  const headers = ["Variante", "Cadena", "Formato", "Barras", "Artículo–tamaño", "Color / surtido", "Tamaño original", "Código comercial"];
  const note = `Emisión ${v.issued_at} · Corte ${v.cutoff} · Versión ${v.id}`;
  const model = modelRows(products, annual);
  return [
    { name: "Guia_y_control", title: "FORECAST Towell · Planeación de resurtibles", note, headers: ["Concepto", "Valor"], rows: [
      ["Último mes con cantidades", v.last_quantity], ["Último cierre acreditado", v.last_closed], ["Evidencia de cierre", v.closure_evidence], ["Fecha real de emisión", v.issued_at], ["Versión", v.id], ["Versión original", original.id],
      ["Variantes analizadas", products.length], ["Champion acreditado", 0], ["Proyección provisional", products.filter(p => p.status === "PROVISIONAL").length], ["Pendientes de proyección", products.filter(p => p.status === "PENDING").length],
      ["Variantes con equivalencia a piezas", products.filter(p => p.pieces_factor).length], ["LIVE", v.live], ["Extensión H13/H14", "Por variante: requiere H1–H12 idénticos y al menos tres orígenes retrospectivos por horizonte"],
      ["Confirmado por",v.closure_confirmed_by??"No registrado"],["Fecha de confirmación",v.closure_confirmed_on??"No registrada"],["Conflictos de empaque",products.filter(p=>p.pack_conflict).length],
      ["Unidades", "No sumar unidades de origen sin equivalencia. Vacío no equivale a cero."], ["Modelo", "Consolidado provisional en piezas; cobertura por variantes, conservando surtidos"], ["Revisiones", "Sólo versiones realmente emitidas; no se fabrican revisiones futuras"], ["Fuente SHA256", String(v.audit.source_sha256)], ["Motor", v.engine], ["Política", v.policy], ["Código base", v.code_sha], ["Integridad versión", v.hash],
    ] },
    { name: "Historico_por_color", title: "Histórico original por variante y fuente", note: note+" · Fuente SHA256: "+String(v.audit.source_sha256)+". Vacíos intactos; cada fuente conserva su unidad.", headers: [...headers,"Fuente","Unidad de origen",...historical,...historical.map(m=>`Celda ${m}`)], rows: products.flatMap(p=>["Towell","Pedidos","Cliente"].map(s=>[...ids(p),s,p.history[s]?.[v.cutoff]?.unit??"Sin dato",...historical.map(m=>p.history[s]?.[m]?.original??null),...historical.map(m=>p.history[s]?.[m]?.cell??null)])) },
    { name: "Champion_por_color", title: "Champion por variante · Sin publicación acreditada", note, headers: [...headers,"Estado","Validación",...future], rows: products.map(p=>[...ids(p),"SIN_CHAMPION",p.champion_reason,...future.map(()=>null)]) },
    { name: "Pronostico_provisional", title: "Estadístico · Proyecciones provisionales", note: note+" · Cantidades en unidad de origen, no publicadas ni adoptadas.", headers: [...headers,"Método","Unidad","Estado","Motivo",...future,...future.map(m=>`Cobertura ${m}`)], rows: products.map(p=>[...ids(p),p.method,p.unit??"Sin equivalencia",p.status,p.reason,...future.map(m=>projectionAt(p,m)),...future.map(m=>p.projection.find(h=>h.period===m)?.reason??"Sin cobertura")]) },
    { name: "Plan_anual_por_modelo", title: "Plan anual por modelo · Piezas acreditadas", note: note+" · Total parcial provisional; nunca sumar unidades desconocidas.", headers: ["Cadena","Formato","Artículo–tamaño","Código comercial","Variantes esperadas",...annual,...annual.map(m=>`Cubiertas ${m}`),"Total parcial anual","Estado"], rows: model.map(r=>[r.chain,r.format,r.model,r.code,r.expected,...r.months.map(m=>m.value),...r.months.map(m=>`${m.covered}/${r.expected}`),r.months.some(m=>m.value!==null)?r.months.reduce((s,m)=>s+(m.value??0),0):null,r.months.every(m=>m.covered===r.expected)?"Cobertura completa":"PARCIAL / SIN COBERTURA; revisar equivalencias"]) },
    { name: "Complemento_2026", title: "Complemento al plan anual", note: note+" · Periodos posteriores al corte y anteriores al plan anual. No duplicar con la ventana próxima.", headers: [...headers,"Unidad",...future.filter(m=>m<annual[0]),...future.filter(m=>m<annual[0]).map(m=>`Estado ${m}`)], rows: products.map(p=>[...ids(p),p.unit??"Sin equivalencia",...future.filter(m=>m<annual[0]).map(m=>projectionAt(p,m)),...future.filter(m=>m<annual[0]).map(m=>projectionAt(p,m)===null?"SIN_COBERTURA":"PROVISIONAL")]) },
    { name: "Proximos_2_3_meses", title: "Ventana posterior al corte · Dos meses y tercero opcional", note, headers: ["Variante","Periodo","Mes de ventana","Unidad","Original","Revisión vigente","Método","Estado"], rows: products.flatMap(p=>nextWindow(v.cutoff,3).map((m,i)=>[p.id,m,i===2?"3 · opcional":String(i+1),p.unit??"Sin equivalencia",projectionAt(original.products.find(q=>q.id===p.id),m),projectionAt(p,m),p.method,projectionAt(p,m)===null?"SIN_COBERTURA":"PROVISIONAL"])) },
    { name: "Revisiones_y_reales", title: "Original, revisiones bimestrales y reales", note: note+" · Sólo emisiones existentes. Diferencias en unidad de origen de Towell; Pedido requiere equivalencia independiente.", headers: ["Versión","Emisión","Corte","Variante","Periodo","Unidad","Original","Revisión","Venta real","Pedido real","Cliente real","Cambio vs original","Venta menos revisión","Pedido menos revisión","Motivo Pedido","Hash versión"], rows: bundle.versions.filter(revision=>revision.issued_at<=v.issued_at).flatMap(revision=>revision.products.filter(p=>products.some(q=>q.id===p.id)).flatMap(p=>future.map(m=>{const base=projectionAt(original.products.find(q=>q.id===p.id),m),current=projectionAt(p,m),latest=products.find(q=>q.id===p.id)!;const sale=latest.history.Towell?.[m]?.value??null,order=latest.history.Pedidos?.[m]?.value??null,client=latest.history.Cliente?.[m]?.value??null;const orderPieces=latest.history.Pedidos?.[m]?.pieces??null,forecastPieces=projectionAt(p,m,true);return [revision.id,revision.issued_at,revision.cutoff,p.id,m,p.unit??"Sin equivalencia",base,current,sale,order,client,base!==null&&current!==null?current-base:null,sale!==null&&current!==null?sale-current:null,orderPieces!==null&&forecastPieces!==null?orderPieces-forecastPieces:null,orderPieces!==null&&forecastPieces!==null?"Diferencia en piezas acreditadas":"NO_EVALUABLE: unidad Pedido no comparable o real ausente",revision.hash];}))) },
    { name: "Metricas_y_cobertura", title: "WAPE y Bias por fuente · Evaluación retrospectiva", note: "H1 del método seleccionado con toda la muestra; no independiente. LIVE exige emisión previa, reales cerrados y unidades comparables. No promediar WAPE entre variantes.", headers: ["Variante","Fuente","WAPE retrospectivo %","Bias retrospectivo %","Pares","Denominador","Unidad de evaluación","Interpretación","WAPE LIVE","Motivo LIVE","Meses cubiertos / 14","Error absoluto total","Error firmado total"], rows: products.flatMap(p=>["Towell","Pedidos","Cliente"].map(s=>{const m=p.retrospective[s];return [p.id,s,m?.value??null,m?.bias??null,m?.pairs??0,m?.denominator??0,m?.basis??"Sin equivalencia",m?.reason??"NO_EVALUABLE: sin proyección",null,v.live,`${p.projection.filter(h=>h.value!==null).length}/14`,m?.absolute_error??null,m?.signed_error??null];})) },
    { name: "Excepciones_y_linaje", title: "Excepciones e identidad · Todos los productos", note: note+" · Detalle por celda en Histórico; evidencia completa en paquete JSON. Fuente SHA256: "+String(v.audit.source_sha256), headers: [...headers,"Estado","Motivo","Equivalencia a piezas","Conflicto empaque","Surtido","Correspondencia propuesta","Celda propuesta","Propuesta original en piezas","Mes propuesta","Empaques por fuente","Anomalías"], rows: products.map(p=>[...ids(p),p.status,p.reason,p.pieces_factor?"Acreditada":"PENDIENTE: no inferir de empaque",p.pack_conflict?"Conflicto Towell / Cliente":"Sin conflicto detectado",/VARIOS|,/.test(canonicalLabel(p.color))?"Conservar surtido sin repartir":"Variante original",p.proposal?"Presente":"Histórico sin propuesta",p.proposal?.cell??null,p.proposal?.value??null,p.proposal?.period??null,Object.entries(p.history).map(([s,h])=>`${s}: ${[...new Set(Object.values(h).map(o=>o.pack_factor??"vacío"))].join(" / ")}`).join("; "),p.issues.join("; ")]) },
  ];
}
