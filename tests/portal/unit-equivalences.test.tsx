import { describe, expect, it, vi } from "vitest";
import { unitSources, unitView, validateUnitCatalog, unitCoverageRows, type UnitCatalog, type UnitRule } from "../../lib/unit-equivalences";
import { chartRows, modelRows, type PlanningBundle, type PlanningProduct } from "../../lib/planning-results";
vi.mock("../../components/forecast/periods-comparison-chart",()=>({PeriodsComparisonChart:()=> <div>Shared chart</div>}));

function fixture(): PlanningBundle {
  const obs=(value:number|null)=>({value,original:value,cell:"Synthetic!K1",pack_factor:99,unit:"Unidad desconocida",pieces:null,unit_evidence:null});
  const p:PlanningProduct={id:"SYNTHETIC|001",account:"SYNTHETIC",chain:"Synthetic chain",format:"Test",barcode:"001",model:"Model",size:"MB",color:"VARIOS",description:"Synthetic test only",commercial_code:"TEST",history:{Towell:{"2026-09":obs(0),"2026-10":obs(1.125)},Cliente:{"2026-10":obs(8)},Pedidos:{"2026-10":obs(null)}},issues:[],champion:null,champion_reason:"Sin Champion",proposal:null,method:"Naive",projection:[{period:"2026-11",horizon:1,value:1.125,pieces:null,reason:"Synthetic"},{period:"2026-12",horizon:2,value:null,pieces:null,reason:"Sin cobertura"}],status:"PROVISIONAL",reason:null,unit:"Desconocida",pieces_factor:null,retrospective:{},training_start:"2026-09"};
  return {schema:"towell-planning-1",versions:[{id:"TEST-V1",original_id:"TEST-V1",issued_at:"2026-11-05T12:00:00Z",cutoff:"2026-10",last_closed:"2026-10",last_quantity:"2026-10",closure_evidence:"Synthetic",code_sha:"test",engine:"test",policy:"test",previous_hash:null,hash:"test",audit:{},products:[p],live:"NO_EVALUABLE"}]};
}
function catalog(b=fixture()):UnitCatalog {
  const v=b.versions.at(-1)!;
  return {schema:"towell-unit-catalog-1",base_version:v.id,base_hash:v.hash,rows:v.products.flatMap(p=>unitSources.map(source=>({source,product_id:p.id,account:p.account,chain:p.chain,format:p.format,barcode:p.barcode,model:p.model,size:p.size,color:p.color,origin_unit:null,factor:null,destination_unit:"piezas",valid_from:null,valid_through:null,evidence:null,status:"Pendiente",validated_by:null,validated_on:null})))};
}
function approve(row:UnitRule,factor=2){Object.assign(row,{origin_unit:"paquete",factor,valid_from:"2026-09-01",valid_through:"2027-12-31",evidence:"Synthetic source/product specification DOC-1",status:"Validado",validated_by:"Synthetic planning reviewer",validated_on:"2026-11-05"});}
const check=(c:UnitCatalog,b=fixture())=>validateUnitCatalog(c,b.versions.at(-1)!,"2026-11-05");
describe("Fase 3A equivalences (synthetic evidence only)",()=>{
  it("retains all source identities pending without using package references",()=>{
    const b=fixture(),c=catalog(b),before=JSON.stringify(b);
    expect(check(c).rows).toHaveLength(3);
    const v=unitView(b,c).versions[0];expect(v.products[0].projection[0].pieces).toBeNull();expect(JSON.stringify(b)).toBe(before);
    expect(unitCoverageRows(v,c).find(r=>r[2]==="2026-11")![9]).toBe("Pendiente de validación");
  });
  it("converts independently by source, retaining decimals, zero, blank, assortment and frozen V01",()=>{
    const b=fixture(),c=catalog(b),before=JSON.stringify(b);approve(c.rows[0]);approve(c.rows[1],3);approve(c.rows[2],4);
    const v=unitView(b,c).versions[0],p=v.products[0];
    expect(p.projection[0].pieces).toBe(2.25);expect(p.projection[1].pieces).toBeNull();
    expect(p.history.Towell["2026-09"].pieces).toBe(0);expect(p.history.Cliente["2026-10"].pieces).toBe(24);expect(p.history.Pedidos["2026-10"].pieces).toBeNull();
    expect(p.color).toBe("VARIOS");expect(p.projection[0].value).toBe(1.125);expect(p.history.Towell["2026-10"].original).toBe(1.125);expect(p.champion).toBeNull();expect(JSON.stringify(b)).toBe(before);
    expect(chartRows([p],v.cutoff,"provisional",true)[9].forecast).toBe(2.25);expect(chartRows([p],v.cutoff,"provisional")[9].forecast).toBe(1.125);
    expect(modelRows([p],["2026-11"])[0].months[0]).toEqual({period:"2026-11",value:2.25,covered:1});
  });
  it.each([0,-1,Infinity,NaN])("rejects invalid factor %s",factor=>{const c=catalog();approve(c.rows[0],factor);expect(()=>check(c)).toThrow();});
  it("requires evidence, approval author, date and full month validity",()=>{
    for(const field of ["origin_unit","factor","evidence","validated_by","validated_on","valid_from","valid_through"] as const){const c=catalog();approve(c.rows[0]);c.rows[0][field]=null;expect(()=>check(c)).toThrow();}
    for(const change of [{valid_from:"2026-09-02"},{valid_through:"2027-12-30"},{valid_from:"2027-02-30"},{valid_from:"2028-01-01"},{validated_on:"2026-11-06"},{destination_unit:"cajas"},{evidence:"Factor de empaque de propuesta"}]){const c=catalog();approve(c.rows[0]);Object.assign(c.rows[0],change);expect(()=>check(c)).toThrow();}
  });
  it("rejects omissions, duplicate identities, foreign sources/products and stale base versions",()=>{
    const c=catalog();c.rows.pop();expect(()=>check(c)).toThrow(/incompleto/);
    const d=catalog();d.rows.push({...d.rows[0]});expect(()=>check(d)).toThrow(/duplicada/);
    for(const field of ["product_id","account","chain","format","barcode","model","size","color"] as const){const d=catalog();d.rows[0][field]="wrong";expect(()=>check(d)).toThrow(/incompatible/);}
    const e=catalog();e.base_hash="other";expect(()=>check(e)).toThrow(/base/);
    const f=catalog();Object.assign(f.rows[0],{source:"Proposal"});expect(()=>check(f)).toThrow();
  });
  it("rejects overlapping approvals; accepts adjoining full months without double counting",()=>{
    const c=catalog();approve(c.rows[0]);c.rows[0].valid_through="2026-10-31";
    const next={...c.rows[0],valid_from:"2026-10-01",valid_through:"2027-12-31"};c.rows.push(next);expect(()=>check(c)).toThrow(/superpuestas/);
    next.valid_from="2026-11-01";expect(check(c).rows).toHaveLength(4);expect(unitView(fixture(),c).versions[0].products[0].projection[0].pieces).toBe(2.25);
  });
  it("does not extrapolate missing, changed or expired units into frozen forecasts",()=>{
    for(const range of [{valid_from:"2026-10-01"},{valid_through:"2026-10-31"}]){const c=catalog();approve(c.rows[0]);Object.assign(c.rows[0],range);expect(unitView(fixture(),c).versions[0].products[0].projection[0].pieces).toBeNull();}
    const c=catalog();approve(c.rows[0]);c.rows[0].valid_through="2026-10-31";c.rows.push({...c.rows[0],factor:3,valid_from:"2026-11-01",valid_through:"2027-12-31"});expect(unitView(fixture(),c).versions[0].products[0].projection[0].pieces).toBeNull();
    c.rows[3].factor=2;c.rows[3].origin_unit="caja";expect(unitView(fixture(),c).versions[0].products[0].projection[0].pieces).toBeNull();
  });
  it("never applies rejected rules or earlier versions with incompatible identities",()=>{
    const b=fixture(),c=catalog(b);approve(c.rows[0]);c.rows[0].status="Rechazado";expect(unitView(b,c).versions[0].products[0].projection[0].pieces).toBeNull();
    c.rows[0].status="Validado";const previous=structuredClone(b.versions[0]);previous.products[0].barcode="different";b.versions.unshift(previous);expect(unitView(b,c).versions[0].products[0].projection[0].pieces).toBeNull();
  });
  it("rejects numeric overflow rather than showing infinite pieces",()=>{const b=fixture(),c=catalog(b);approve(c.rows[0],Number.MAX_VALUE);expect(()=>unitView(b,c)).toThrow(/rango/);});
  it("retains archived Fase 3A conversion as a detached function without network requests",()=>{
    const b=fixture(),c=catalog(b);approve(c.rows[0]);const fetcher=vi.spyOn(globalThis,"fetch");
    expect(unitView(b,c).versions[0].products[0].projection[0].pieces).toBe(2.25);
    expect(b.versions[0].products[0].projection[0].pieces).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();fetcher.mockRestore();
  });
});
