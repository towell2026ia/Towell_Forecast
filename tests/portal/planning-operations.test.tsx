import { describe,expect,it,vi } from "vitest";
import { fireEvent,render,screen } from "@testing-library/react";
import { PlanningPeriods } from "../../components/forecast/planning-periods";
import { piecesModelRows,piecesChartRows,addPlanningMonth,type PlanningBundle,type PlanningProduct } from "../../lib/planning-results";
vi.mock("../../components/forecast/periods-comparison-chart",()=>({PeriodsComparisonChart:()=> <div>Shared chart</div>}));
function fixture():PlanningBundle {
 const obs=(value:number|null)=>({value,original:value,cell:"Test!K1",pack_factor:99,unit:"Legacy unknown",pieces:null,unit_evidence:null});
 const p:PlanningProduct={id:"A|001",account:"A",chain:"Chain",format:"Scope",barcode:"001",description:"Model blue",model:"Model",size:"MB",color:"Blue",commercial_code:"CODE",history:{Towell:{"2026-09":obs(0),"2026-10":obs(5.25)},Cliente:{"2026-10":obs(30)},Pedidos:{"2026-10":obs(20)}},issues:[],champion:null,champion_reason:"Sin Champion",proposal:null,method:"Naive",projection:Array.from({length:14},(_,i)=>({period:addPlanningMonth("2026-10",i+1),horizon:i+1,value:i===13?null:10.125,pieces:null,reason:i===13?"Sin cobertura":"Provisional"})),status:"PROVISIONAL",reason:null,unit:"Legacy unknown",pieces_factor:null,retrospective:{}};
 const b:PlanningBundle={schema:"towell-planning-1",versions:[{id:"V1",original_id:"V1",issued_at:"2026-11-05T12:00:00Z",cutoff:"2026-10",last_closed:"2026-10",closure_evidence:"Synthetic",last_quantity:"2026-10",code_sha:"test",engine:"test",policy:"test",previous_hash:null,hash:"h1",audit:{},products:[p],live:"No evidence"}]};
 b.pieces_confirmation={prd:"3A.1",unit:"PIEZAS",sources:["Towell","Cliente","Pedidos"],statement:"Synthetic confirmation",confirmed_by:"Synthetic user",recorded_at:"2026-11-05T13:00:00Z",presentation_version:"V02-PIECES",base_versions:[{id:"V1",hash:"h1"}],source_file_sha256:"0".repeat(64)};return b;
}

import { operationalPlanningSheets,inventorySummary } from "../../lib/planning-operations";
import { planningXlsx } from "../../lib/planning-xlsx";
describe("PRD 3A.2 additive inventory structure",()=>{
 it("preserves frozen versions, independent series, and no-data inventory",()=>{
  const b=fixture(),before=JSON.stringify(b),v=b.versions[0],s=operationalPlanningSheets(b,v);
  expect(JSON.stringify(b)).toBe(before);expect(s).toHaveLength(17);
  const annual=s.find(s=>s.name==='Plan_anual_por_color')!;
  expect(annual.rows[0][annual.headers.indexOf('2027-01')]).toBe(10.125);
  expect(annual.rows[0][annual.headers.indexOf('2027-12')]).toBeNull();
  expect(annual.rows[0][annual.headers.indexOf('Champion')]).toBe('Sin Champion');
  const series=s.find(s=>s.name==='Series_comerciales')!,r=series.rows.find(r=>r[9]==='2026-10')!;
  expect(r.slice(10,17)).toEqual([null,null,20,5.25,null,30,'Sin Champion']);
  for(const name of ['Inventario_PV_color','Inventario_por_modelo']){
   const sheet=s.find(s=>s.name===name)!;expect(sheet.rows[0][sheet.headers.indexOf('Inventario PIEZAS')]).toBeNull();
   expect(sheet.rows[0][sheet.headers.indexOf('Fecha de corte')]).toBeNull();
  }
 });
 it("shares sums with chart and export for filters without a model forecast fit",()=>{
  const b=fixture(),v=b.versions[0],q=structuredClone(v.products[0]);q.id='A|002';q.barcode='002';v.products.push(q);
  const rows=piecesModelRows(v.products,['2027-01']),s=operationalPlanningSheets(b,v),model=s.find(s=>s.name==='Plan_anual_por_modelo')!;
  expect(model.rows).toHaveLength(1);expect(model.rows[0][model.headers.indexOf('2027-01')]).toBe(rows[0].months[0].value);
  expect(piecesChartRows(v.products,v.cutoff,'model')[11].forecast).toBe(rows[0].months[0].value);
  const filtered=operationalPlanningSheets(b,v,{scopes:[],models:[],colors:[],sizes:[],products:[q.id]});
  expect(filtered[0].rows).toHaveLength(1);expect(filtered[0].rows[0][0]).toBe(q.id);
 });
 it("requires one date, version and store set; missing is not zero",()=>{
  const r={productId:'A',store:'S1',date:'2026-10-31',version:'I1',pieces:0};
  expect(inventorySummary([r],['A','B'],['S1','S2'],r.date,r.version)).toEqual({pieces:0,covered:1,expected:4,status:'Parcial'});
  expect(inventorySummary([],['A'],['S1'],r.date,r.version).pieces).toBeNull();
  for(const changed of [{date:'2026-09-30'},{version:'I2'},{store:'S3'},{productId:'C'},{pieces:-1},{pieces:NaN}])expect(()=>inventorySummary([{...r,...changed}],['A'],['S1'],r.date,r.version)).toThrow();
  expect(()=>inventorySummary([r,r],['A'],['S1'],r.date,r.version)).toThrow(/duplicado/);
  expect(()=>inventorySummary([],['A','A'],['S1'],r.date,r.version)).toThrow();
  expect(()=>inventorySummary([],['A'],['S1'],'2026-02-30',r.version)).toThrow();
  expect(inventorySummary([{...r,pieces:0.125}],['A'],['S1'],r.date,r.version).pieces).toBe(0.125);
 });
 it("downloads an OOXML ZIP with numeric zero, blank null and literal formula-like strings",()=>{
  const bytes=planningXlsx([{name:'Test',title:'Test',note:'Local only',headers:['ID','Zero','Missing'],rows:[['001',0,null],['=WEBSERVICE(1)',1.125,null]]}]);
  const text=new TextDecoder().decode(bytes);
  expect(Array.from(bytes.slice(0,4))).toEqual([80,75,3,4]);expect(text).toContain('<v>0</v>');expect(text).toContain('>001</t>');expect(text).toContain('=WEBSERVICE(1)</t>');expect(text).not.toContain('<f>');
  expect(text).not.toContain('r="C5"');expect(text).toContain('<v>1.125</v>');
 });
 it("keeps existing controls and adds annual and inventory views plus download",()=>{
  render(<PlanningPeriods initialBundle={fixture()}/>);
  expect(screen.getByTestId('inventory-status').textContent).toContain('Sin dato');
  expect(screen.getByRole('button',{name:'Descargar XLSX de esta consulta'})).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Vista de planeación'),{target:{value:'Anual por color'}});
  expect(screen.getByRole('table').textContent).toContain('2027-12');
  fireEvent.change(screen.getByLabelText('Vista de planeación'),{target:{value:'Inventario por color'}});
  expect(screen.getByRole('table').textContent).toContain('Tienda');
 });
});
