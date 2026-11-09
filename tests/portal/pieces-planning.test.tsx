import { describe,expect,it,vi } from "vitest";
import { fireEvent,render,screen } from "@testing-library/react";
import { PlanningPeriods } from "../../components/forecast/planning-periods";
import { auditedPlanningSheets } from "../../lib/planning-source-audit";
import { parsePiecesPlanning,piecesView,piecesModelRows,piecesChartRows,piecesPlanningSheets,piecesLiveMetric,addPlanningMonth,type PlanningBundle,type PlanningProduct } from "../../lib/planning-results";
vi.mock("../../components/forecast/periods-comparison-chart",()=>({PeriodsComparisonChart:()=> <div>Shared chart</div>}));
function fixture():PlanningBundle {
 const obs=(value:number|null)=>({value,original:value,cell:"Test!K1",pack_factor:99,unit:"Legacy unknown",pieces:null,unit_evidence:null});
 const p:PlanningProduct={id:"A|001",account:"A",chain:"Chain",format:"Scope",barcode:"001",description:"Model blue",model:"Model",size:"MB",color:"Blue",commercial_code:"CODE",history:{Towell:{"2026-09":obs(0),"2026-10":obs(5.25)},Cliente:{"2026-10":obs(30)},Pedidos:{"2026-10":obs(20)}},issues:[],champion:null,champion_reason:"Sin Champion",proposal:null,method:"Naive",projection:Array.from({length:14},(_,i)=>({period:addPlanningMonth("2026-10",i+1),horizon:i+1,value:i===13?null:10.125,pieces:null,reason:i===13?"Sin cobertura":"Provisional"})),status:"PROVISIONAL",reason:null,unit:"Legacy unknown",pieces_factor:null,retrospective:{}};
 const b:PlanningBundle={schema:"towell-planning-1",versions:[{id:"V1",original_id:"V1",issued_at:"2026-11-05T12:00:00Z",cutoff:"2026-10",last_closed:"2026-10",closure_evidence:"Synthetic",last_quantity:"2026-10",code_sha:"test",engine:"test",policy:"test",previous_hash:null,hash:"h1",audit:{},products:[p],live:"No evidence"}]};
 b.pieces_confirmation={prd:"3A.1",unit:"PIEZAS",sources:["Towell","Cliente","Pedidos"],statement:"Synthetic confirmation",confirmed_by:"Synthetic user",recorded_at:"2026-11-05T13:00:00Z",presentation_version:"V02-PIECES",base_versions:[{id:"V1",hash:"h1"}],source_file_sha256:"0".repeat(64)};return b;
}
describe("PRD 3A.1 pieces only",()=>{
 it("requires an explicit declaration tied to all frozen versions",()=>{
   const b=fixture();expect(parsePiecesPlanning(b).versions).toHaveLength(1);delete b.pieces_confirmation;expect(()=>parsePiecesPlanning(b)).toThrow(/confirmación/);
   const c=fixture();c.pieces_confirmation!.base_versions[0].hash="wrong";expect(()=>parsePiecesPlanning(c)).toThrow(/incompatible/);
 });
 it("labels the same amounts as pieces without reading package factors or modifying V01",()=>{
   const b=fixture(),before=JSON.stringify(b),p=piecesView(b).versions[0].products[0];
   expect(p.projection[0].pieces).toBe(10.125);expect(p.projection[13].pieces).toBeNull();expect(p.history.Towell['2026-09'].pieces).toBe(0);expect(p.history.Cliente['2026-10'].pieces).toBe(30);expect(p.history.Pedidos['2026-10'].pieces).toBe(20);expect(JSON.stringify(b)).toBe(before);
   b.versions[0].products[0].history.Towell['2026-10'].pack_factor=9999;expect(piecesView(b).versions[0].products[0].history.Towell['2026-10'].pieces).toBe(5.25);
 });
 it("keeps account, chain, format, size and code boundaries; case is not a new model",()=>{
   const p=fixture().versions[0].products[0],b=structuredClone(p);b.id="A|002";b.barcode="002";b.model="MODEL";b.color="Red";
   expect(piecesModelRows([p,b],["2026-11"])[0].months[0]).toEqual({period:"2026-11",value:20.25,covered:2});
   for(const field of ["account","chain","format","size","commercial_code"] as const){const q=structuredClone(b);q[field]="OTHER";expect(piecesModelRows([p,q],["2026-11"])).toHaveLength(2);}
   expect(()=>piecesModelRows([p,p],["2026-11"])).toThrow(/dos veces/);
 });
 it("isolates missing commercial codes without hiding their forecasts",()=>{
   const p=fixture().versions[0].products[0],q=structuredClone(p);p.commercial_code=null;q.commercial_code=null;q.id="B";
   const groups=piecesModelRows([p,q],["2026-11"]);expect(groups).toHaveLength(2);expect(groups.every(g=>g.months[0].value===10.125)).toBe(true);
 });
 it("never adds Towell, Cliente and Pedidos together; the graph selects one history",()=>{
   const p=fixture().versions[0].products[0];
   expect(piecesChartRows([p],"2026-10","provisional","Towell")[8].actual).toBe(5.25);
   expect(piecesChartRows([p],"2026-10","provisional","Pedidos")[8].actual).toBe(20);
   expect(piecesChartRows([p],"2026-10","provisional","Cliente")[8].actual).toBe(30);
   expect(piecesChartRows([p],"2026-10","color")[9].forecast).toBeNull();
 });
 it("uses one row per identity in each product/history view and no operational packaging columns",()=>{
   const b=fixture(),sheets=piecesPlanningSheets(b,b.versions[0]);expect(sheets).toHaveLength(13);
   for(const name of ['Productos','Historico_Towell','Historico_Cliente','Historico_Pedidos','Champion_por_color','Pronostico_provisional','Proximos_2_3_meses'])expect(sheets.find(s=>s.name===name)!.rows).toHaveLength(1);
   expect(JSON.stringify(sheets)).not.toMatch(/empaque|paquetes|factor|Legacy unknown|equivalencia/i);
   const forecast=sheets.find(s=>s.name==='Pronostico_provisional')!;expect(forecast.rows[0][forecast.headers.indexOf('2026-11')]).toBe(10.125);
   expect(piecesChartRows(b.versions[0].products,'2026-10','provisional')[9].forecast).toBe(10.125);
 });
 it("does not turn a November issuance into a prior November forecast",()=>{
   const b=fixture(),v=b.versions[0],p=v.products[0];v.last_closed='2026-11';p.history.Towell['2026-11']={...p.history.Towell['2026-10'],value:12};
   expect(piecesLiveMetric(b,v,p.id,'Towell').pairs).toBe(0);
 });
 it("evaluates LIVE per source using only the latest eligible issuance, not all revisions",()=>{
   const b=fixture(),v=b.versions[0],p=v.products[0];v.issued_at='2026-10-30T12:00:00Z';v.last_closed='2026-11';
   p.history.Towell['2026-11']={...p.history.Towell['2026-10'],value:20};p.history.Pedidos['2026-11']={...p.history.Pedidos['2026-10'],value:40};
   const older=structuredClone(v);older.id='older';older.issued_at='2026-10-20T12:00:00Z';older.products[0].projection[0].value=99;b.versions.unshift(older);
   expect(piecesLiveMetric(b,v,p.id,'Towell')).toMatchObject({pairs:1,value:49.375});
   expect(piecesLiveMetric(b,v,p.id,'Pedidos')).toMatchObject({pairs:1,value:74.6875});
   v.last_closed='2026-10';expect(piecesLiveMetric(b,v,p.id,'Towell').pairs).toBe(0);
 });
 it("preserves the original amount and all issued reviews independently",()=>{
   const b=fixture(),r=structuredClone(b.versions[0]);r.id='V2';r.issued_at='2027-01-01T00:00:00Z';r.products[0].projection[2].value=44;b.versions.push(r);
   const s=piecesPlanningSheets(b,r).find(s=>s.name==='Revisiones_y_reales')!;
   expect(s.rows.find(row=>row[0]==='V2'&&row[4]==='2027-01')!.slice(5,7)).toEqual([10.125,44]);
   expect(b.versions[0].products[0].projection[2].value).toBe(10.125);
 });
 it("removes the equivalence workflow, shows three separate series and no Champion adoption",()=>{
   const fetcher=vi.spyOn(globalThis,'fetch');render(<PlanningPeriods initialBundle={fixture()}/>);
   expect(screen.queryByLabelText('Importar equivalencias validadas')).toBeNull();expect(screen.queryByLabelText('Unidad de la gráfica')).toBeNull();
   expect(screen.getByTestId('unit-summary').textContent).toContain('PIEZAS');
   fireEvent.change(screen.getByLabelText('Vista de planeación'),{target:{value:'Histórico'}});fireEvent.change(screen.getByLabelText('Serie histórica'),{target:{value:'Pedidos'}});
   expect(screen.getByText('Pedido real · PIEZAS')).toBeTruthy();expect(screen.getByRole('table').querySelectorAll('tbody tr')).toHaveLength(1);
   fireEvent.change(screen.getByLabelText('Vista de planeación'),{target:{value:'Por color'}});expect(screen.getByText('Sin Champion',{selector:'td'})).toBeTruthy();expect(fetcher).not.toHaveBeenCalled();fetcher.mockRestore();
 });
 it("consults and exports the independent provisional ORDER version with exact variant sums",()=>{
   const b=fixture(),v=b.versions[0],p=v.products[0],q=structuredClone(p);
   b.pieces_confirmation!.prd="3A.9";
   expect(()=>parsePiecesPlanning(b)).toThrow(/Objetivo y fuente/);
   v.objective="ORDER";v.training_source="Pedidos";b.pieces_confirmation!.presentation_version="V04";
   q.id="A|002";q.barcode="002";q.color="Red";p.projection[0].value=0.1;q.projection[0].value=0.2;
   v.products.push(q);
   const bundle=parsePiecesPlanning(b),sheets=auditedPlanningSheets(bundle,bundle.versions[0]);
   const model=sheets.find(s=>s.name==="Plan_anual_por_modelo")!;
   expect(model.rows[0][model.headers.indexOf("2026-11")]).toBe(0.3);
   expect(piecesChartRows(v.products,v.cutoff,"model","Pedidos",true)[9].forecast).toBe(0.3);
   expect(model.rows[0]).toContain("TOTAL PARCIAL");
   const commercial=sheets.find(s=>s.name==="Series_comerciales")!;
   expect(commercial.headers.slice(10,16)).toEqual(["Forecast de Pedido real","Fcst Towell histórico","Fcst Cliente recibido","Pedido real","Venta real","Entrega"]);
   render(<PlanningPeriods initialBundle={bundle}/>);
   expect(screen.getByLabelText<HTMLSelectElement>("Serie histórica").value).toBe("Pedidos");
   expect(screen.getByText(/Origen: paquete privado V04 · Forecast de Pedido real/)).toBeTruthy();
 });
});
