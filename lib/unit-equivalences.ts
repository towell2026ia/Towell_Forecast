import { z } from "zod";
import type { PlanningBundle, PlanningProduct, PlanningVersion } from "./planning-results";

export const unitSources = ["Towell", "Cliente", "Pedidos"] as const;
const text = z.string().trim();
const optionalText = text.nullable();
const identityText = z.string();
const rowSchema = z.object({
  source: z.enum(unitSources), product_id: identityText.min(1), account: identityText.min(1), chain: identityText.min(1), format: identityText.min(1), barcode: identityText.min(1),
  model: identityText, size: identityText, color: identityText, origin_unit: optionalText, factor: z.number().finite().positive().nullable(), destination_unit: optionalText,
  valid_from: optionalText, valid_through: optionalText, evidence: optionalText, status: z.enum(["Pendiente", "Validado", "Rechazado"]),
  validated_by: optionalText, validated_on: optionalText,
});
export const unitCatalogSchema = z.object({ schema: z.literal("towell-unit-catalog-1"), base_version: text.min(1), base_hash: text.min(1), rows: z.array(rowSchema).min(1).max(20000) });
export type UnitCatalog = z.infer<typeof unitCatalogSchema>;
export type UnitRule = UnitCatalog["rows"][number];
const key = (r: Pick<UnitRule,"source"|"product_id">) => `${r.source}|${r.product_id}`;
function date(value: string | null, field: string) {
  if (!value || !/^20\d\d-\d\d-\d\d$/.test(value) || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0,10)!==value) throw new Error(`${field}: fecha ISO válida requerida`);
  return value;
}
const monthEnd = (month: string) => new Date(Date.UTC(Number(month.slice(0,4)),Number(month.slice(5,7)),0)).toISOString().slice(0,10);
/** Offline assertion by Planeación, not a server authorization or a Champion publication. */
export function validateUnitCatalog(input: unknown, version: PlanningVersion, today = new Date().toISOString().slice(0,10)): UnitCatalog {
  const catalog = unitCatalogSchema.parse(input);
  if (catalog.base_version!==version.id || catalog.base_hash!==version.hash) throw new Error("Catálogo de otra versión/base");
  const products = new Map(version.products.map(p=>[p.id,p]));
  const seen = new Set<string>(), covered = new Set<string>(), active = new Map<string,UnitRule[]>();
  for (const [index,r] of catalog.rows.entries()) {
    const p = products.get(r.product_id), prefix = `Fila ${index+6}`;
    if (!p || ["account","chain","format","barcode","model","size","color"].some(k=>r[k as keyof UnitRule]!==p[k as keyof PlanningProduct])) throw new Error(`${prefix}: identidad/fuente/producto incompatible`);
    covered.add(key(r));
    const identity = JSON.stringify([key(r),r.valid_from,r.valid_through]);
    if (seen.has(identity)) throw new Error(`${prefix}: vigencia duplicada`);
    seen.add(identity);
    if (r.valid_from || r.valid_through) {
      const start=date(r.valid_from,`${prefix} inicio`), end=date(r.valid_through,`${prefix} fin`);
      if(start>end || !start.endsWith("-01") || end!==monthEnd(end.slice(0,7))) throw new Error(`${prefix}: la fuente mensual requiere meses completos, sin prorrateo`);
    }
    if(r.validated_on) {date(r.validated_on,`${prefix} validación`);if(r.validated_on>today)throw new Error(`${prefix}: validación futura`);}
    if (r.status!=="Validado") continue;
    if (!r.origin_unit || /sin (equivalencia|identificar)|desconocid|pendiente/i.test(r.origin_unit) || !r.factor || r.destination_unit!=="piezas" || !r.evidence || !r.validated_by || !r.validated_on || !r.valid_from || !r.valid_through) throw new Error(`${prefix}: equivalencia validada sin unidad, factor, vigencia, evidencia o responsable`);
    if (/^(factor de empaque|empaque|propuesta|referencia de empaque)\b/i.test(r.evidence)) throw new Error(`${prefix}: una referencia de empaque/propuesta no acredita la unidad histórica`);
    active.set(key(r),[...(active.get(key(r))??[]),r]);
  }
  for(const p of version.products)for(const source of unitSources)if(!covered.has(key({source,product_id:p.id})))throw new Error("Catálogo incompleto: faltan productos/fuentes de la base");
  for(const rules of active.values()) {
    rules.sort((a,b)=>a.valid_from!.localeCompare(b.valid_from!));
    for(let i=1;i<rules.length;i++)if(rules[i].valid_from!<=rules[i-1].valid_through!)throw new Error("Vigencias validadas superpuestas: no se admite doble conteo");
  }
  return catalog;
}
export function unitRuleFor(catalog: UnitCatalog, source: string, productId: string, month: string) {
  return catalog.rows.find(r=>r.status==="Validado" && r.source===source && r.product_id===productId && r.valid_from!<=`${month}-01` && r.valid_through!>=monthEnd(month));
}
const sameUnit = (a: UnitRule | undefined,b: UnitRule | undefined) => !!a && !!b && a.origin_unit===b.origin_unit && a.factor===b.factor;
function multiply(value: number|null, factor: number) {if(value===null)return null;const result=value*factor;if(!Number.isFinite(result))throw new Error("Conversión fuera de rango numérico");return result;}
/** Derived, disposable view. Original timestamps, quantities and frozen bundle stay unchanged. */
export function unitView(bundle: PlanningBundle, catalog: UnitCatalog): PlanningBundle {
  validateUnitCatalog(catalog,bundle.versions.at(-1)!);
  const view=structuredClone(bundle);
  for(const v of view.versions)for(const p of v.products) {
    const latest=bundle.versions.at(-1)!.products.find(q=>q.id===p.id);
    const sameIdentity=latest && ["account","chain","format","barcode","model","size","color"].every(k=>p[k as keyof PlanningProduct]===latest[k as keyof PlanningProduct]);
    for(const [source,observations] of Object.entries(p.history))for(const [month,o] of Object.entries(observations)) {
      const r=sameIdentity?unitRuleFor(catalog,source,p.id,month):undefined;
      o.pieces=r?multiply(o.value,r.factor!):null;
      o.unit_evidence=r?.evidence??null;
    }
    const cutoffRule=unitRuleFor(catalog,"Towell",p.id,v.cutoff);
    const training=Object.entries(p.history.Towell??{}).filter(([m,o])=>o.value!==null && m>=(p.training_start??"0000-00") && m<=v.cutoff);
    const compatible=sameIdentity && training.length>0 && training.every(([m])=>sameUnit(cutoffRule,unitRuleFor(catalog,"Towell",p.id,m)));
    for(const point of p.projection) {
      const r=unitRuleFor(catalog,"Towell",p.id,point.period);
      const valid=compatible && sameUnit(cutoffRule,r);
      point.pieces=valid?multiply(point.value,r!.factor!):null;
    }
    // Per-period coverage is authoritative. Never infer global coverage from a scalar factor.
    p.pieces_factor=null;
  }
  return view;
}
export function unitCoverageRows(version: PlanningVersion,catalog: UnitCatalog|null,products=version.products) {
  const rows:(string|number|null)[][]=[];
  const future=Array.from({length:14},(_,i)=>new Date(Date.UTC(Number(version.cutoff.slice(0,4)),Number(version.cutoff.slice(5,7))+i,1)).toISOString().slice(0,7));
  for(const p of products)for(const source of unitSources) {
    const periods=[...new Set([...Object.keys(p.history[source]??{}),...(source==="Towell"?future:[])])].sort();
    for(const month of periods) {
      const point=p.projection.find(x=>source==="Towell"&&x.period===month);
      const obs=p.history[source]?.[month];
      const projected=month>version.cutoff && source==="Towell";
      const native=projected?point?.value??null:obs?.value??null;
      const pieces=projected?point?.pieces??null:obs?.pieces??null;
      const rule=catalog?unitRuleFor(catalog,source,p.id,month):undefined;
      const reason=native===null?"Sin cantidad / sin cobertura":!rule?"Pendiente de validación":pieces===null?"Unidad del entrenamiento/corte no acreditada o incompatible":"Equivalencia validada por Planeación";
      rows.push([p.id,source,month,projected?"Pronóstico provisional":"Histórico",native,rule?.origin_unit??"Desconocida",rule?.factor??null,pieces,pieces===null?"No":"Sí",reason,rule?.evidence??null,rule?.validated_by??null]);
    }
  }
  return rows;
}
