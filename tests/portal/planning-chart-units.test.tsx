import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PeriodsComparisonChart } from "../../components/forecast/periods-comparison-chart";
import { dashboardSeries, type DashboardPoint, type DashboardSeries } from "../../lib/periods-dashboard";
vi.mock("recharts",()=>{
  const Box=({children}:{children?:ReactNode})=><div>{children}</div>;
  return {ResponsiveContainer:Box,ComposedChart:Box,Bar:()=>null,Line:()=>null,CartesianGrid:()=>null,XAxis:()=>null,YAxis:()=>null,
    ReferenceArea:({label}:{label:{value:string}})=><div data-testid="forecast-area">{label.value}</div>,
    Tooltip:({content}:{content:(props:unknown)=>ReactNode})=><div data-testid="tooltip">{content({active:true,label:"2027-01",payload:[{}]})}</div>};
});
const points: DashboardPoint[] = Array.from({ length: 12 }, (_, index) => {
  const period = new Date(Date.UTC(2026, index + 3, 1)).toISOString().slice(0, 7);
  const series = Object.keys(dashboardSeries) as DashboardSeries[];
  return {
    month: period, period, horizon: index >= 9 ? index - 8 : null,
    values: Object.fromEntries(series.map(key => [key, null])) as DashboardPoint["values"],
    coverage: Object.fromEntries(series.map(key => [key, { value: null, covered: 0, eligible: 1 }])) as DashboardPoint["coverage"],
  };
});
describe("planning chart year boundary",()=>{
  it("January uses its actual 2027 period rather than the 2026 cutoff year",()=>{
    render(<PeriodsComparisonChart points={points} cutoff="2026-12" planning={{unit:"Unidad de origen",provisional:true,source:"Towell"}}/>);
    expect(screen.getByTestId("tooltip").querySelector("strong")?.textContent).toBe("2027-01 · H1");
    expect(screen.getByTestId("forecast-area").textContent).toBe("Provisional");
  });
  it("labels an unpublished color plan Sin Champion",()=>{
    render(<PeriodsComparisonChart points={points} cutoff="2026-12" planning={{unit:"piezas",provisional:false,source:"Towell"}}/>);
    expect(screen.getByTestId("forecast-area").textContent).toBe("Sin Champion");
  });
});
