import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { PeriodsComparisonChart } from "../../components/forecast/periods-comparison-chart";
import { dashboardSeries, type DashboardPoint, type DashboardSeries } from "../../lib/periods-dashboard";
vi.mock("recharts",()=>{
  const Box=({children}:{children?:ReactNode})=><div>{children}</div>;
  return {ResponsiveContainer:Box,ComposedChart:({children}:{children?:ReactNode})=><div data-testid="composed-chart">{children}</div>,Bar:({dataKey}:{dataKey:string})=><div data-testid={`bar-${dataKey}`}/>,Line:({dataKey}:{dataKey:string})=><div data-testid={`line-${dataKey}`}/>,CartesianGrid:()=>null,XAxis:()=>null,YAxis:()=>null,
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
  it("keeps reported inventory in columns on the same composed chart and preserves its visibility control",async()=>{
    render(<PeriodsComparisonChart points={points} cutoff="2026-12"/>);
    expect(screen.getByTestId("bar-inventory")).toBeTruthy();
    expect(screen.queryByTestId("line-inventory")).toBeNull();
    expect(screen.getByTestId("bar-previous1")).toBeTruthy();
    expect(screen.getByTestId("line-actual")).toBeTruthy();
    await userEvent.click(screen.getByRole("checkbox",{name:"Inventario reportado"}));
    expect(screen.queryByTestId("bar-inventory")).toBeNull();
  });
  it("overlays Pedido inventory as a default visible reversible bar in the same chart",async()=>{
    render(<PeriodsComparisonChart points={points} cutoff={null} objective="Pedido"/>);
    const checkbox=screen.getByRole("checkbox",{name:"Inventario en tienda"}) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    expect(screen.getAllByTestId("composed-chart")).toHaveLength(1);
    expect(screen.getByTestId("bar-inventory")).toBeTruthy();
    expect(screen.queryByTestId("line-inventory")).toBeNull();
    expect(screen.getByTestId("line-order")).toBeTruthy();
    expect(screen.queryByRole("heading",{name:"Inventario en tienda"})).toBeNull();
    expect(screen.queryByRole("table",{name:"Inventario en tienda por mes"})).toBeNull();
    expect(screen.queryByText("Saldos y cobertura mensual")).toBeNull();
    await userEvent.click(checkbox);
    expect(checkbox.checked).toBe(false);
    expect(screen.queryByTestId("bar-inventory")).toBeNull();
    expect(screen.getByTestId("line-order")).toBeTruthy();
    await userEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);
    expect(screen.getByTestId("bar-inventory")).toBeTruthy();
    expect(screen.getAllByTestId("composed-chart")).toHaveLength(1);
  });
  it("reports an inventory query error without drawing stock while retaining the original Pedido chart",()=>{
    render(<PeriodsComparisonChart points={points} cutoff={null} objective="Pedido" inventoryError/>);
    const checkbox=screen.getByRole("checkbox",{name:"Inventario en tienda"}) as HTMLInputElement;
    expect(checkbox.checked).toBe(false); expect(checkbox.disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("Error de consulta de inventario");
    expect(screen.getAllByTestId("composed-chart")).toHaveLength(1);
    expect(screen.queryByTestId("bar-inventory")).toBeNull();
    expect(screen.getByTestId("line-order")).toBeTruthy();
    expect(screen.queryByText("Saldos y cobertura mensual")).toBeNull();
  });
});
