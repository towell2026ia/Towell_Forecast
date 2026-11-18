import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PeriodsComparisonChart } from "../../components/forecast/periods-comparison-chart";
import { dashboardSeries, periodsDashboard, type DashboardPoint, type DashboardSeries } from "../../lib/periods-dashboard";
import { emptyReportChoice, type ReportSnapshot } from "../../lib/forecast-reporting";
import type { HistoricalRow, OperationalFulfillment, Product } from "../../lib/supabase/types";

const hover = vi.hoisted(() => ({ month: "Jul-26" }));
vi.mock("recharts", () => {
  const Box = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  type Curve = { dataKey: string; yAxisId: string; connectNulls?: boolean };
  return {
    ResponsiveContainer: Box,
    ComposedChart: ({ data, children }: { data: unknown[]; children?: ReactNode }) => <div data-testid="composed-chart"><output data-testid="chart-records">{JSON.stringify(data)}</output>{children}</div>,
    Bar: ({ dataKey, yAxisId }: Curve) => <div data-testid={`bar-${dataKey}`} data-axis={yAxisId}/>,
    Line: ({ dataKey, yAxisId, connectNulls }: Curve) => <div data-testid={`line-${dataKey}`} data-axis={yAxisId} data-connect-nulls={String(connectNulls)}/>,
    CartesianGrid: () => null,
    XAxis: () => null,
    YAxis: ({ yAxisId, orientation, domain, tickFormatter }: { yAxisId: string; orientation?: string; domain?: number[]; tickFormatter?: (n: number) => string }) => <div data-testid={`axis-${yAxisId}`} data-orientation={orientation} data-domain={JSON.stringify(domain)} data-zero={tickFormatter?.(0)}/>,
    ReferenceArea: ({ label }: { label: { value: string } }) => <div data-testid="forecast-area">{label.value}</div>,
    Tooltip: ({ content }: { content: (props: unknown) => ReactNode }) => <div data-testid="chart-tooltip">{content({ active: true, label: hover.month, payload: [] })}</div>,
  };
});

const products: Product[] = ["blue", "oxford"].map((id, i) => ({
  id, chain_id: "chain", category_id: null, product_code: `00${i}`, variant_code: null,
  description: `Synthetic towel ${id}`, identifiers: [[null, `000${i}`]],
  commercial: { product_id: id, chain_id: "chain", purchase_type: "RS", format: "BD", product_type: "TOALLA",
    model: "SYNTHETIC", color: id, size: "MEDIO BAÑO", status: "PENDING_CLASSIFICATION", source: "SYNTHETIC", reasons: [] },
}));
function snapshot(closed = false): ReportSnapshot {
  return { products, chains: [{ id: "chain", code: "SYNTHETIC::BD", name: "Synthetic BD", status: "ACTIVE", has_history: true, parentId: null, scopeType: "COMMERCIAL_UNIT" }],
    categories: [], calculations: [], horizons: [], decisions: [], live: [], provisionals: [], customers: [],
    capabilities: { vintage_persistence: false, champion_publication: false, official_publication: false },
    sourceContext: { source_hash: "d".repeat(64), authorized_source_hash: "d".repeat(64), generation: 4,
      first_period: "2024-01", last_period: "2026-10", forecast_contract: "pedido-towell-v1", objective: "Pedido", target_metric: "ORDER",
      target_sheet: "Pedidos", target_file_sha256: "e".repeat(64), unit: "PIEZAS", inventory_source: "ESTADISTICA",
      target_source_available: true, training_cutoff: closed ? "2026-09" : null, forecast_available: closed, forecast_block_code: closed ? null : "ORDER_CLOSURE_UNVERIFIED",
      fulfillment_source: "ESTADISTICA_BAASE", fulfillment_file_sha256: "f".repeat(64), fulfillment_available: true } };
}
function row(product: Product, period: string, ordered: number | null, delivered: number | null): HistoricalRow {
  return { chain_id: product.chain_id, product_id: product.id, product, chain: "Synthetic BD", period,
    SALES: 999, ORDER: ordered, DELIVERY: delivered, availability: "UNKNOWN",
    exact: { ORDER: ordered === null ? undefined : String(ordered), DELIVERY: delivered === null ? undefined : String(delivered) } };
}
function fulfillment(product: Product, period: string, ordered: string | null, delivered: string | null): OperationalFulfillment {
  return { chain_id: product.chain_id, product_id: product.id, period, format: "BD", upc: product.identifiers[0][1]!, purchase_type: "RS",
    order_text: ordered, delivery_text: delivered, source_reference: `BAASE!${product.id}/${period}`,
    order_source_reference: ordered === null ? null : "BAASE!ORDER", delivery_source_reference: delivered === null ? null : "BAASE!DELIVERY",
    source_sha256: "f".repeat(64), partial_period: period === "2026-10" };
}
function dashboard(closed = false) {
  const [blue, oxford] = products;
  return periodsDashboard(snapshot(closed), [
    row(blue, "2026-06", 10, null),
    row(blue, "2026-07", 100, null), row(oxford, "2026-07", 20, null),
    row(blue, "2026-08", 0, null),
    row(blue, "2026-09", 70, null), row(oxford, "2026-09", 30, null),
    row(blue, "2026-10", null, null), row(oxford, "2026-10", 50, null),
  ], emptyReportChoice, [null, null], [
    { chain_id: "chain", product_id: "blue", period: "2026-07", format: "BD", upc: "0000", purchase_type: "RS", quantity_text: "1000" },
    { chain_id: "chain", product_id: "blue", period: "2026-10", format: "BD", upc: "0000", purchase_type: "RS", quantity_text: "0.000" },
  ], [
    fulfillment(blue, "2026-06", "10", "12.5"),
    fulfillment(blue, "2026-07", "100", "0"), fulfillment(oxford, "2026-07", "20", null),
    fulfillment(blue, "2026-08", "0", "0"),
    fulfillment(blue, "2026-09", "70", "35"), fulfillment(oxford, "2026-09", "30", "30"),
    ...(closed ? [fulfillment(blue, "2026-10", "40", "30"), fulfillment(oxford, "2026-10", "10", "5")]
      : [fulfillment(blue, "2026-10", null, "12"), fulfillment(oxford, "2026-10", "50", null)]),
  ]);
}
type RecordRow = { period: string; order: number | null; fillRate: number | null; inventory: number | null };
const plotted = () => JSON.parse(screen.getByTestId("chart-records").textContent!) as RecordRow[];
const checked = (name: string) => (screen.getByRole("checkbox", { name }) as HTMLInputElement).checked;

describe("monthly Pedido and independent BAASE Fill Rate in the existing G3/G4 chart", () => {
  beforeEach(() => { hover.month = "Jul-26"; });

  it("shows ORDER, monthly Fill Rate and inventory by default without inventing a close or horizons", () => {
    const data = dashboard();
    expect(data.cutoff).toBeNull();
    expect(data.points.every(point => point.horizon === null)).toBe(true);
    expect(data.points.filter(point => point.outlook).map(point => point.period)).toEqual(["2026-11", "2026-12", "2027-01"]);
    render(<PeriodsComparisonChart points={data.points} cutoff={data.cutoff} objective="Pedido" showFillRate/>);
    expect(screen.getAllByTestId("composed-chart")).toHaveLength(1);
    expect(checked("Pedido histórico")).toBe(true);
    expect(checked("Fill Rate (%)")).toBe(true);
    expect(checked("Inventario en tienda")).toBe(true);
    const chart = screen.getByTestId("composed-chart");
    expect(within(chart).getByTestId("line-order").dataset.axis).toBe("pieces");
    expect(within(chart).getByTestId("bar-inventory").dataset.axis).toBe("pieces");
    expect(within(chart).getByTestId("line-fillRate").dataset.axis).toBe("fillRate");
    expect(screen.queryByTestId("line-inventory")).toBeNull();
    expect(screen.getByTestId("forecast-area").textContent).toBe("Pronóstico pendiente");
    expect(plotted().find(point => point.period === "2026-07")).toMatchObject({ order: 120, fillRate: 0, inventory: 1000 });
    expect(plotted().find(point => point.period === "2026-08")).toMatchObject({ order: 0, fillRate: null });
    expect(plotted().find(point => point.period === "2026-09")?.fillRate).toBe(65);
    expect(plotted().find(point => point.period === "2026-10")).toMatchObject({ order: 50, fillRate: null, inventory: 0 });
    expect(screen.getByTestId("line-fillRate").dataset.connectNulls).toBe("false");
  });

  it("uses paired quantities and actual coverage in the tooltip, retaining true 0% and Sin dato", () => {
    const data = dashboard();
    const chart = render(<PeriodsComparisonChart points={data.points} cutoff={null} objective="Pedido" showFillRate/>);
    const july = screen.getByTestId("chart-tooltip");
    expect(july.textContent).toContain("Fill Rate (%): 0.00%");
    expect(july.textContent).toContain("Pedido comparable BAASE: 100 piezas");
    expect(july.textContent).toContain("Entrega comparable BAASE: 0 piezas");
    expect(july.textContent).toContain("Cobertura 1/2 pares Pedido/Entrega");
    expect(july.textContent).toContain("Cobertura parcial");
    hover.month = "Ago-26";
    chart.rerender(<PeriodsComparisonChart points={data.points} cutoff={null} objective="Pedido" showFillRate/>);
    expect(screen.getByTestId("chart-tooltip").textContent).toContain("Fill Rate (%): Sin dato");
    expect(screen.getByTestId("chart-tooltip").textContent).toContain("Pedido comparable BAASE: 0 piezas");
    hover.month = "Oct-26";
    chart.rerender(<PeriodsComparisonChart points={data.points} cutoff={null} objective="Pedido" showFillRate/>);
    expect(screen.getByTestId("chart-tooltip").textContent).toContain("Cobertura 0/2 pares Pedido/Entrega");
    const rateAxis = screen.getByTestId("axis-fillRate");
    expect(rateAxis.dataset.orientation).toBe("right");
    expect(rateAxis.dataset.zero).toBe("0%");
    expect(JSON.parse(rateAxis.dataset.domain!)).toEqual([0, 130]);
  });

  it("allows independent series controls while keeping all series in one chart", async () => {
    const data = dashboard();
    render(<PeriodsComparisonChart points={data.points} cutoff={null} objective="Pedido" showFillRate/>);
    await userEvent.click(screen.getByRole("checkbox", { name: "Fill Rate (%)" }));
    expect(screen.queryByTestId("line-fillRate")).toBeNull();
    expect(screen.getByTestId("line-order")).toBeTruthy();
    expect(screen.getByTestId("bar-inventory")).toBeTruthy();
    await userEvent.click(screen.getByRole("checkbox", { name: "Fill Rate (%)" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Inventario en tienda" }));
    expect(screen.getByTestId("line-fillRate")).toBeTruthy();
    expect(screen.queryByTestId("bar-inventory")).toBeNull();
    expect(screen.getAllByTestId("composed-chart")).toHaveLength(1);
  });

  it("can overlay stored forecast families without projecting Fill Rate into future horizons", () => {
    const data = dashboard();
    const points: DashboardPoint[] = data.points.map((point, index) => {
      if (index < 9) return point;
      const values = Object.fromEntries((Object.keys(dashboardSeries) as DashboardSeries[]).map(key => [key, null])) as DashboardPoint["values"];
      Object.assign(values, { STATISTICAL: 40, ML: 30, ENSEMBLE: 37 });
      const coverage = Object.fromEntries((Object.keys(dashboardSeries) as DashboardSeries[]).map(key => [key, { value: values[key], covered: values[key] === null ? 0 : 2, eligible: 2 }])) as DashboardPoint["coverage"];
      return { ...point, outlook: false, horizon: index - 8, values, coverage, fillRate: { value: 999, ordered: 1, delivered: 9.99, orderedExact: "1", deliveredExact: "9.99", covered: 2, eligible: 2 } };
    });
    hover.month = "Nov-26";
    render(<PeriodsComparisonChart points={points} cutoff="2026-10" objective="Pedido" showFillRate/>);
    for (const family of ["STATISTICAL", "ML", "ENSEMBLE"] as const) {
      expect(screen.getByTestId(`line-${family}`).dataset.axis).toBe("pieces");
    }
    expect(screen.getByTestId("line-order")).toBeTruthy();
    expect(screen.getByTestId("line-fillRate")).toBeTruthy();
    expect(plotted().filter(point => point.period >= "2026-11").every(point => point.fillRate === null)).toBe(true);
    expect(screen.getByTestId("chart-tooltip").textContent).toContain("Fill Rate (%): Sin dato");
    expect(screen.getByTestId("chart-tooltip").textContent).toContain("Sin pares registrados");
    expect(JSON.parse(screen.getByTestId("axis-fillRate").dataset.domain!)).toEqual([0, 130]);
    expect(screen.getAllByTestId("composed-chart")).toHaveLength(1);
  });
  it("plots accredited partial October fulfillment during H1 without changing the September training close", () => {
    const data = dashboard(true);
    expect(data.cutoff).toBe("2026-09");
    expect(data.points.slice(9).map(point => [point.period, point.horizon])).toEqual([["2026-10", 1], ["2026-11", 2], ["2026-12", 3]]);
    expect(data.points[9].fillRate).toMatchObject({ value: 70, orderedExact: "50", deliveredExact: "35", covered: 2, eligible: 2,
      reported: true, partialPeriod: true, source: "ESTADISTICA_BAASE" });
    hover.month = "Oct-26";
    const chart = render(<PeriodsComparisonChart points={data.points} cutoff={data.cutoff} objective="Pedido" showFillRate/>);
    expect(data.points[9].partialOrder).toBe(true);
    expect(data.kpis.orders).toBe(230); // Captured October is visible, but remains outside the nine closed historical positions.
    expect(plotted().find(point => point.period === "2026-10")).toMatchObject({ order: 50, fillRate: 70, inventory: 0 });
    expect(screen.getByTestId("chart-tooltip").textContent).toContain("Oct-26 · H1");
    expect(screen.getByTestId("chart-tooltip").textContent).toContain("Pedido comparable BAASE: 50 piezas");
    expect(screen.getByTestId("chart-tooltip").textContent).toContain("Entrega comparable BAASE: 35 piezas");
    expect(screen.getByTestId("chart-tooltip").textContent).toContain("Mes parcial");
    hover.month = "Nov-26";
    chart.rerender(<PeriodsComparisonChart points={data.points} cutoff={data.cutoff} objective="Pedido" showFillRate/>);
    expect(plotted().find(point => point.period === "2026-11")?.fillRate).toBeNull();
    expect(screen.getByTestId("chart-tooltip").textContent).toContain("Sin pares registrados");
    expect(screen.getAllByTestId("composed-chart")).toHaveLength(1);
  });
});
