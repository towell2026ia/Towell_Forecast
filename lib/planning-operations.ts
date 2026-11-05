import { filterPlanning, noPlanningFilters, piecesModelRows, piecesPlanningSheets, planMonths, projectionAt, type PlanningBundle, type PlanningFilters, type PlanningProduct, type PlanningSheet, type PlanningVersion } from "./planning-results";

/** Stock support only: no inventory source or ingestion is activated by PRD 3A.2. */
export const inventoryStatus = "Sin dato · fuente de inventario pendiente";
export type InventoryPoint = { productId: string; store: string; date: string; version: string; pieces: number };
export function inventorySummary(rows: InventoryPoint[], productIds: string[], stores: string[], date: string, version: string) {
  if (!productIds.length || !stores.length || new Set(productIds).size !== productIds.length || new Set(stores).size !== stores.length) throw new Error("Scope de inventario vacío o duplicado");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(date)) || new Date(date).toISOString().slice(0,10)!==date || !version) throw new Error("Corte o versión inválidos");
  const seen = new Set<string>(); let total = 0;
  for (const r of rows) {
    if (r.date !== date || r.version !== version || !productIds.includes(r.productId) || !stores.includes(r.store)) throw new Error("No mezclar fechas, versiones, productos o tiendas");
    const key=JSON.stringify([r.productId,r.store]);
    if (seen.has(key)) throw new Error("Inventario duplicado");
    if (!Number.isFinite(r.pieces) || r.pieces < 0) throw new Error("Inventario inválido");
    seen.add(key); total += r.pieces;
  }
  const expected=productIds.length*stores.length;
  return {pieces:seen.size?total:null,covered:seen.size,expected,status:seen.size===expected?"Completo":seen.size?"Parcial":"Sin dato"};
}

/** Same immutable forecast values and shared group keys as the graph and V02. */
export function operationalPlanningSheets(bundle: PlanningBundle, version: PlanningVersion, filters: PlanningFilters=noPlanningFilters): PlanningSheet[] {
  const sheets=piecesPlanningSheets(bundle,version,filters),products=filterPlanning(version,filters);
  const annual=planMonths(bundle.versions[0].cutoff),groups=piecesModelRows(products,annual);
  const headers=["Identidad","Cuenta","Cadena","Formato","Barras","Modelo","Tamaño","Color / surtido","Código comercial"];
  const identity=(p:PlanningProduct)=>[p.id,p.account,p.chain,p.format,p.barcode,p.model,p.size,p.color,p.commercial_code??"Sin código; identidad aislada"];
  const note=`PIEZAS · Presentación V03 / PRD 3A.2 · Cálculo ${version.id} · Corte ${version.cutoff} · Emisión ${version.issued_at}. Inventario sólo estructura; sin carga.`;
  const annualColor:PlanningSheet={name:"Plan_anual_por_color",title:"Estadístico por color · Plan anual provisional",note,headers:[...headers,"Método","Champion",...annual,...annual.map(m=>`Cobertura ${m}`),"Inventario PIEZAS","Estado inventario"],rows:products.map(p=>[...identity(p),p.method,"Sin Champion",...annual.map(m=>projectionAt(p,m)),...annual.map(m=>projectionAt(p,m)===null?"Sin cobertura":"Provisional"),null,inventoryStatus])};
  const pos:PlanningSheet={name:"Inventario_PV_color",title:"Inventario por punto de venta y color · Pendiente",note:note+" Las filas son marcadores de cobertura del catálogo, no observaciones de inventario.",headers:[...headers,"Tienda","Fecha de corte","Versión inventario","Inventario PIEZAS","Estado"],rows:products.map(p=>[...identity(p),null,null,null,null,inventoryStatus])};
  const stockModel:PlanningSheet={name:"Inventario_por_modelo",title:"Inventario por modelo · Pendiente",note:note+" Sólo podrá consolidarse la misma fecha, versión y conjunto de tiendas.",headers:["Cuenta","Cadena","Formato","Modelo","Tamaño","Código comercial","Variantes esperadas","Tiendas esperadas","Fecha de corte","Versión inventario","Inventario PIEZAS","Variantes con inventario","Estado"],rows:groups.map(g=>[g.account,g.chain,g.format,g.model,g.size,g.code,g.expected,null,null,null,null,0,inventoryStatus])};
  for(const s of sheets){
    if(["Champion_por_color","Plan_anual_por_modelo"].includes(s.name)){s.headers.push("Inventario PIEZAS","Estado inventario");s.rows.forEach(r=>r.push(null,inventoryStatus));}
    if(s.name==="Guia_y_control")s.rows.push(["Presentación actual","V03 / PRD 3A.2; no constituye nueva emisión ni revisión"],["Inventario","Sólo estructura de base de datos pendiente de aplicar; sin observaciones"],["Fcst Cliente / Entrega","Sin dato acreditado; Cliente histórico se conserva como referencia independiente"]);
  }
  // Independent columns, never a total across different commercial measures.
  const periods=[...new Set([...annual,...products.flatMap(p=>Object.values(p.history).flatMap(h=>Object.keys(h)))])].sort();
  const commercial:PlanningSheet={name:"Series_comerciales",title:"Series comerciales independientes · PIEZAS",note:note+" Fcst Towell es provisional. Cliente histórico no se interpreta como Fcst Cliente. Valores posteriores al cierre no se acreditan como reales.",headers:[...headers,"Periodo","Fcst Towell provisional","Fcst Cliente","Pedido","Venta","Entrega","Cliente referencia","Estado Champion"],rows:products.flatMap(p=>periods.map(m=>[...identity(p),m,projectionAt(p,m),null,m<=version.last_closed?p.history.Pedidos?.[m]?.value??null:null,m<=version.last_closed?p.history.Towell?.[m]?.value??null:null,null,m<=version.last_closed?p.history.Cliente?.[m]?.value??null:null,"Sin Champion"]))};
  return [annualColor,sheets.find(s=>s.name==="Plan_anual_por_modelo")!,...sheets.filter(s=>s.name!=="Plan_anual_por_modelo"),pos,stockModel,commercial];
}
