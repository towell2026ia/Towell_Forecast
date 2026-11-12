import type { ReactNode } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PeriodsComparisonChart } from "../../components/forecast/periods-comparison-chart";
import { addReportMonth } from "../../lib/forecast-reporting";
import { dashboardSeries, shortPeriod, type DashboardPoint } from "../../lib/periods-dashboard";

const chart = vi.hoisted(() => ({ data: [] as { fillRate: number | null }[], tooltipMonth: "Sep-26" }));
vi.mock("recharts", () => {
  const Box = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  return {
    ResponsiveContainer: Box,
    ComposedChart: ({ children, data }: { children?: ReactNode; data: { fillRate: number | null }[] }) => { chart.data = data; return <div data-testid="composed-chart">{children}</div>; },
    CartesianGrid: () => null, XAxis: () => null, ReferenceArea: () => null,
    YAxis: ({ yAxisId, orientation = "left", domain, label }: { yAxisId: string; orientation?: string; domain?: number[]; label?: { value: string } }) => <div data-testid={`axis-${yAxisId}`} data-orientation={orientation} data-domain={JSON.stringify(domain)}>{label?.value}</div>,
    Bar: ({ dataKey, yAxisId }: { dataKey: string; yAxisId: string }) => <div data-testid={`bar-${dataKey}`} data-axis={yAxisId}/>,
    Line: ({ dataKey, yAxisId, connectNulls }: { dataKey: string; yAxisId: string; connectNulls: boolean }) => <div data-testid={`line-${dataKey}`} data-axis={yAxisId} data-connect={String(connectNulls)}/>,
    Tooltip: ({ content }: { content: (props: unknown) => ReactNode }) => <div data-testid="monthly-tooltip">{content({ active: true, label: chart.tooltipMonth, payload: [{}] })}</div>,
  };
});
function points(): DashboardPoint[] {
  return Array.from({ length: 12 }, (_, index) => {
    const period = addReportMonth("2026-10", index - 8), horizon = index >= 9 ? index - 8 : null;
    return { period, month: shortPeriod(period), horizon,
      values: Object.fromEntries(Object.keys(dashboardSeries).map(key => [key, null])) as DashboardPoint["values"],
      coverage: Object.fromEntries(Object.keys(dashboardSeries).map(key => [key, { value: null, covered: 0, eligible: 11 }])) as DashboardPoint["coverage"],
      fillRate: { value: period === "2026-09" ? 91580 / 100000 * 100 : null, ordered: period === "2026-09" ? 100000 : null,
        delivered: period === "2026-09" ? 91580 : null, orderedExact: period === "2026-09" ? "100000" : null,
        deliveredExact: period === "2026-09" ? "91580" : null, covered: period === "2026-09" ? 11 : 0, eligible: 11 } };
  });
}
describe("Período monthly Fill Rate in the existing composed chart", () => {
  it("keeps all piece series on the left and only Fill Rate on the right in one chart", () => {
    render(<PeriodsComparisonChart points={points()} cutoff="2026-10" title="Período" showFillRate/>);
    expect(screen.getAllByTestId("composed-chart")).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Período" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Comparativo comercial 9+3" })).toBeNull();
    expect(screen.getByTestId("axis-pieces").getAttribute("data-orientation")).toBe("left");
    expect(screen.getByTestId("axis-fillRate").getAttribute("data-orientation")).toBe("right");
    expect(screen.getByTestId("axis-fillRate").getAttribute("data-domain")).toBe("[0,100]");
    for (const key of ["previous2", "previous1", "inventory"]) expect(screen.getByTestId(`bar-${key}`).getAttribute("data-axis")).toBe("pieces");
    for (const key of ["actual", "STATISTICAL", "ML", "ENSEMBLE", "TOWELL"]) expect(screen.getByTestId(`line-${key}`).getAttribute("data-axis")).toBe("pieces");
    expect(screen.getByTestId("line-fillRate").getAttribute("data-axis")).toBe("fillRate");
    expect(screen.getByTestId("line-fillRate").getAttribute("data-connect")).toBe("false");
    expect(chart.data).toHaveLength(12);
  });
  it("uses the same monthly percentage, comparable sums and coverage in tooltip and table", () => {
    render(<PeriodsComparisonChart points={points()} cutoff="2026-10" title="Período" showFillRate/>);
    const tooltip = screen.getByTestId("monthly-tooltip");
    expect(tooltip.textContent).toContain("91.58%");
    expect(tooltip.textContent).toContain("Pedido comparable: 100000 piezas");
    expect(tooltip.textContent).toContain("Entrega comparable: 91580 piezas");
    expect(tooltip.textContent).toContain("Cobertura 11/11 pares");
    const table = screen.getByRole("table", { name: "Datos de la gráfica Período" });
    const september = within(table).getByText("2026-09").closest("tr")!;
    expect(september.textContent).toContain("91.58%Cobertura 11/11 pares");
    const future = within(table).getByText("2026-11").closest("tr")!;
    expect(future.textContent).toContain("Sin datoCobertura 0/11 pares");
  });
  it("lets the original legend hide the line without losing inventory or the monthly table", async () => {
    render(<PeriodsComparisonChart points={points()} cutoff="2026-10" title="Período" showFillRate/>);
    await userEvent.click(screen.getByRole("checkbox", { name: "Fill Rate (%)" }));
    expect(screen.queryByTestId("line-fillRate")).toBeNull();
    expect(screen.getByTestId("bar-inventory")).toBeTruthy();
    expect(within(screen.getByRole("table", { name: "Datos de la gráfica Período" })).getByText("91.58%")).toBeTruthy();
    await userEvent.click(screen.getByRole("checkbox", { name: "Fill Rate (%)" }));
    expect(screen.getByTestId("line-fillRate")).toBeTruthy();
  });
  it("expands valid percentages above 100 and shows partial and zero rates without connecting absent future data", () => {
    const rows = points();
    rows[0].fillRate = { value: 125, ordered: 100, delivered: 125, orderedExact: "100", deliveredExact: "125", covered: 2, eligible: 11 };
    rows[1].fillRate = { value: 0, ordered: 100, delivered: 0, orderedExact: "100", deliveredExact: "0", covered: 1, eligible: 11 };
    rows[9].fillRate = { ...rows[0].fillRate, value: 999 }; // Defensive: future percentages never become chart points.
    chart.tooltipMonth = rows[0].month;
    render(<PeriodsComparisonChart points={rows} cutoff="2026-10" title="Período" showFillRate/>);
    expect(screen.getByTestId("axis-fillRate").getAttribute("data-domain")).toBe("[0,130]");
    expect(screen.getByTestId("monthly-tooltip").textContent).toContain("125.00%");
    expect(screen.getByTestId("monthly-tooltip").textContent).toContain("Cobertura parcial");
    const table = screen.getByRole("table", { name: "Datos de la gráfica Período" });
    expect(within(table).getByText(rows[1].period).closest("tr")!.textContent).toContain("0.00%");
    expect(chart.data[9].fillRate).toBeNull();
    chart.tooltipMonth = "Sep-26";
  });
  it("does not change other graph usages or add Fill Rate to private planning", () => {
    render(<PeriodsComparisonChart points={points()} cutoff="2026-10" planning={{ source: "Towell", unit: "PIEZAS", provisional: true }}/>);
    expect(screen.getByRole("heading", { name: "Comparativo comercial 9+3" })).toBeTruthy();
    expect(screen.queryByRole("checkbox", { name: "Fill Rate (%)" })).toBeNull();
    expect(screen.queryByTestId("axis-fillRate")).toBeNull();
  });
});
