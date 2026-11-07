import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { PlanningPeriods } from "../../components/forecast/planning-periods";
import { addPlanningMonth, chartRows, filterPlanning, modelRows, nextWindow, noPlanningFilters, parsePlanning, piecesChartRows, planningSheets, type PlanningBundle, type PlanningProduct } from "../../lib/planning-results";
import { auditedPlanningSheets } from "../../lib/planning-source-audit";
vi.mock("../../components/forecast/periods-comparison-chart",()=>({PeriodsComparisonChart:()=> <div>Shared chart</div>}));
function product(id="A", model="Model"): PlanningProduct {
  return {id,account:"account",chain:"chain",format:"format",barcode:id,description:"Synthetic "+id,model,color:"BLUE",size:"size",commercial_code:"commercial",history:{Towell:{"2026-10":{value:11.125,original:11.125,cell:"Synthetic!A1",pack_factor:12,unit:"pieza",pieces:11.125,unit_evidence:"Synthetic specification"}},Pedidos:{},Cliente:{}},issues:[],champion:null,champion_reason:"Sin Champion",proposal:null,method:"Naive",projection:Array.from({length:14},(_,i)=>({period:addPlanningMonth("2026-10",i+1),horizon:i+1,value:20+i,pieces:20+i,reason:"Synthetic test"})),status:"PROVISIONAL",reason:"Provisional",unit:"pieza",pieces_factor:1,retrospective:{}};
}
function fixture(): PlanningBundle {return {schema:"towell-planning-1",versions:[{id:"V1",issued_at:"2026-11-05T12:00:00Z",cutoff:"2026-10",last_closed:"2026-10",closure_evidence:"Synthetic confirmation",last_quantity:"2026-10",code_sha:"test",engine:"test",policy:"test",original_id:"V1",previous_hash:null,hash:"test",audit:{},products:[product(),product("B","MODEL"),product("C","Second")],live:"No evaluable"}]};}
describe("private replenishment pilot",()=>{
  it("rolls upcoming months from any cut, including year boundaries",()=>{
    expect(nextWindow("2026-12",3)).toEqual(["2027-01","2027-02","2027-03"]);
    expect(nextWindow("2027-03",2)).toEqual(["2027-04","2027-05"]);
    expect(nextWindow("2027-03",3)).toEqual(["2027-04","2027-05","2027-06"]);
  });
  it("supports multiple/all models and dependent colors without case duplication",()=>{
    const v=fixture().versions[0];
    expect(filterPlanning(v,{...noPlanningFilters,models:["MODEL"]})).toHaveLength(2);
    expect(filterPlanning(v,{...noPlanningFilters,models:["MODEL","SECOND"],colors:["BLUE"]})).toHaveLength(3);
    expect(filterPlanning(v,noPlanningFilters)).toHaveLength(3);
  });
  it("never sums unknown units and exposes partial coverage and missing codes",()=>{
    const a=product(),b=product("B"),c=product("C");b.projection[0].pieces=null;b.pieces_factor=null;c.commercial_code=null;
    const rows=modelRows([a,b,c],["2026-11"]);
    expect(rows[0].months[0]).toEqual({period:"2026-11",value:20,covered:1});expect(rows[0].expected).toBe(2);
    expect(rows[1].months[0].value).toBeNull();
  });
  it("XLSX, table and chart use identical filtered projections and preserve original versions",()=>{
    const b=fixture(),v=b.versions[0];const sheets=planningSheets(b,v,{...noPlanningFilters,products:["A"]});
    expect(sheets.find(s=>s.name==="Pronostico_provisional")!.rows[0][12]).toBe(20);
    expect(chartRows([v.products[0]],v.cutoff,"provisional")[9].forecast).toBe(20);
    expect(chartRows([v.products[0]],v.cutoff,"color")[9].forecast).toBeNull();
    const second=structuredClone(v);second.id="V2";second.products[0].projection[2].value=99;second.products[0].projection[2].pieces=99;b.versions.push(second);
    const revisions=planningSheets(b,second).find(s=>s.name==="Revisiones_y_reales")!;
    expect(revisions.rows.find(r=>r[0]==="V2"&&r[3]==="A"&&r[4]==="2027-01")!.slice(6,9)).toEqual([22,99,null]);expect(v.products[0].projection[2].value).toBe(22);
  });
  it("rejects foreign/duplicate horizons and unproven piece conversions",()=>{
    const b=fixture();expect(parsePlanning(b).versions).toHaveLength(1);
    b.versions[0].products[0].projection[0].period="2026-10";expect(()=>parsePlanning(b)).toThrow();
    const bad=fixture();bad.versions[0].products[0].pieces_factor=null;expect(()=>parsePlanning(bad)).toThrow();
  });
  it("keeps different commercial codes and assorted variants separate with honest coverage",()=>{
    const a=product(),b=product("B"),c=product("C");b.commercial_code="other";c.color="VARIOS";c.pieces_factor=null;c.projection.forEach(p=>p.pieces=null);
    expect(modelRows([a,b,c],["2026-11"])).toHaveLength(2);
    expect(chartRows([a,b,c],"2026-10","model")[9].coverage.STATISTICAL).toEqual({value:40,covered:2,eligible:3});
    const v=fixture().versions[0];v.products=[a,b,c];c.size="SECOND";
    expect(filterPlanning(v,{...noPlanningFilters,sizes:["SECOND"]})).toEqual([c]);
    expect(c.color).toBe("VARIOS");
  });
  it("does not manufacture an original plan for a new revision product",()=>{
    const b=fixture(),v=structuredClone(b.versions[0]);v.id="V2";v.products.push(product("new"));b.versions.push(v);
    const rows=planningSheets(b,v).find(s=>s.name==="Revisiones_y_reales")!.rows;
    expect(rows.find(r=>r[0]==="V2"&&r[3]==="new")![6]).toBeNull();
    expect(planningSheets(b,v).find(s=>s.name==="Plan_anual_por_modelo")!.headers.slice(5,17)).toEqual(Array.from({length:12},(_,i)=>`2027-${String(i+1).padStart(2,"0")}`));
    v.cutoff="2027-02";
    expect(planningSheets(b,v).find(s=>s.name==="Plan_anual_por_modelo")!.headers[5]).toBe("2027-01");
    expect(planningSheets(b,v).find(s=>s.name==="Revisiones_y_reales")!.rows[0][4]).toBe("2026-11");
  });
  it("shows unadopted state and switches annual views without a network write",()=>{
    const fetcher=vi.spyOn(globalThis,"fetch");
    const bundle=fixture();bundle.pieces_confirmation={prd:"3A.1",unit:"PIEZAS",sources:["Towell","Cliente","Pedidos"],statement:"Synthetic unit confirmation",confirmed_by:"Synthetic reviewer",recorded_at:"2026-11-05T12:00:00Z",presentation_version:"TEST-PIECES",base_versions:bundle.versions.map(v=>({id:v.id,hash:v.hash})),source_file_sha256:"0".repeat(64)};
    render(<PlanningPeriods initialBundle={bundle}/>);
    expect(screen.getByText(/Origen: paquete privado V03 · PROVISIONAL/)).toBeTruthy();
    expect(screen.getByText("Estadístico por color · Plan anual provisional")).toBeTruthy();
    expect(screen.getByRole("table",{name:"Matriz de planeación"}).textContent).toContain("Sin Champion");
    expect(piecesChartRows([bundle.versions[0].products[0]],"2026-10","provisional")[9].forecast).toBe(20);
    const filtered={...noPlanningFilters,products:["A"]};
    const sheets=auditedPlanningSheets(bundle,bundle.versions[0],filtered);
    const annual=sheets.find(s=>s.name==="Plan_anual_por_color")!;
    expect(annual.rows).toHaveLength(1);
    expect(annual.rows[0][annual.headers.indexOf("2027-01")]).toBe(22);
    expect(sheets.find(s=>s.name==="Pronostico_provisional")!.rows[0][11]).toBe(piecesChartRows([bundle.versions[0].products[0]],"2026-10","provisional")[9].forecast);
    fireEvent.change(screen.getByLabelText("Vista de planeación"),{target:{value:"Provisionales"}});
    expect(screen.getByRole("table",{name:"Matriz de planeación"})).toBeTruthy();
    expect(screen.getByLabelText("Modelo / artículo–tamaño").querySelectorAll("option")).toHaveLength(2);
    fireEvent.change(screen.getByLabelText("Meses próximos"),{target:{value:"3"}});
    expect(screen.getByText(/2026-11 \/ 2026-12 \/ 2027-01/)).toBeTruthy();
    expect(fetcher).not.toHaveBeenCalled();fetcher.mockRestore();
  });
});
