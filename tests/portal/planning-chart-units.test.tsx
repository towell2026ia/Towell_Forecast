import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PeriodsComparisonChart } from "../../components/forecast/periods-comparison-chart";
vi.mock("recharts",()=>{
  const Box=({children}:{children?:ReactNode})=><div>{children}</div>;
  return {ResponsiveContainer:Box,ComposedChart:Box,Bar:()=>null,Line:()=>null,CartesianGrid:()=>null,XAxis:()=>null,YAxis:()=>null,
    ReferenceArea:({label}:{label:{value:string}})=><div data-testid="forecast-area">{label.value}</div>,
    Tooltip:({content}:{content:(props:unknown)=>ReactNode})=><div data-testid="tooltip">{content({active:true,label:"2027-01",payload:[{}]})}</div>};
});
describe("planning chart year boundary",()=>{
  it("January uses its actual 2027 period rather than the 2026 cutoff year",()=>{
    render(<PeriodsComparisonChart points={[]} year={2026} planning={{unit:"Unidad de origen",provisional:true}}/>);
    expect(screen.getByTestId("tooltip").textContent).toBe("2027-01");
    expect(screen.getByTestId("forecast-area").textContent).toBe("Provisional");
  });
  it("labels an unpublished color plan Sin Champion",()=>{
    render(<PeriodsComparisonChart points={[]} year={2026} planning={{unit:"piezas",provisional:false}}/>);
    expect(screen.getByTestId("forecast-area").textContent).toBe("Sin Champion");
  });
});
