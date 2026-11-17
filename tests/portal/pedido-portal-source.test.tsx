import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ForecastTowellApp from "../../app/forecast-towell-app";
import type { PreviewClient } from "../../lib/forecast-preview";
import type { ReportSnapshot } from "../../lib/forecast-reporting";
import type { ForecastReadRepository, OperationalReadContext, Product } from "../../lib/supabase/types";

// Render the real portal and its shared preview owner. A panel-only context
// fixture cannot detect an error manufactured by ForecastTowellApp's effect.
const unclosed: OperationalReadContext = {
  source_hash: "a".repeat(64), authorized_source_hash: "a".repeat(64), generation: 3,
  first_period: "2024-01", last_period: "2026-10", forecast_contract: "pedido-towell-v1",
  objective: "Pedido", target_metric: "ORDER", target_sheet: "Pedidos", target_file_sha256: "b".repeat(64),
  training_cutoff: null, target_source_available: true, forecast_available: false,
  forecast_block_code: "ORDER_CLOSURE_UNVERIFIED", forecast_unavailable_reason: "Sin cálculo: cierre de Pedido no acreditado",
  unit: "PIEZAS", inventory_source: "ESTADISTICA",
};
const profile = { id: "synthetic-admin", full_name: "Synthetic", global_role: "ADMIN", status: "ACTIVE" } as const;
const chains = [1, 2].map(n => ({ id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
  code: `CHAIN-${n}`, name: `Synthetic chain ${n}`, status: "ACTIVE", has_history: true,
  parentId: null, scopeType: "COMMERCIAL_UNIT" }));
const products: Product[] = chains.map((chain, n) => ({ id: `11111111-1111-4111-8111-${String(n + 1).padStart(12, "0")}`,
  chain_id: chain.id, category_id: null, product_code: `SYNTHETIC-${n}`, description: `Synthetic variant ${n}`, variant_code: null,
  identifiers: [], commercial: { chain_id: chain.id, product_id: `11111111-1111-4111-8111-${String(n + 1).padStart(12, "0")}`,
    purchase_type: "RS", model: "SYNTHETIC DESIGN", color: `SYNTHETIC COLOUR ${n}`, size: "MB", product_type: "TOALLA",
    status: "PENDING_CLASSIFICATION", source: "SYNTHETIC_TEST", reasons: ["NOT_APPROVED"] } }));

function fixture(source: OperationalReadContext) {
  const previews = { readOnly: true, latest: vi.fn(async () => null), create: vi.fn(), createSelection: vi.fn(),
    vintageCapabilities: vi.fn(async () => ({ vintage_persistence: false, official_publication: false,
      champion_publication: false, forecast_calculation_history: true, forecast_selection: false, local_bounded_preview: true }))
  } as unknown as PreviewClient;
  const snapshot: ReportSnapshot = { products, chains, categories: [], calculations: [], horizons: [], decisions: [], live: [],
    provisionals: [], customers: [], sourceContext: source,
    capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false } };
  const repository: ForecastReadRepository = {
    previews, openOperationalRead: vi.fn(async () => source), getCurrentProfile: vi.fn(async () => profile),
    getVisibleChains: vi.fn(async () => chains), getCommercialCatalog: vi.fn(async () => products.map(p => p.commercial!)),
    getProducts: vi.fn(async chainId => products.filter(p => !chainId || p.chain_id === chainId)),
    getCategories: vi.fn(async () => []), getPeriods: vi.fn(async () => ["2026-10"]),
    getReportPeriods: vi.fn(async () => ["2026-10"]), getReportSnapshot: vi.fn(async () => snapshot),
    getReportHistory: vi.fn(async () => []), getReportInventory: vi.fn(async () => []),
    getHistoricalSummary: vi.fn(async () => ({ scopeCount: 2, productCount: 2, observationCount: 2, latestPeriod: "2026-10", byScope: [] })),
    getHistoricalObservations: vi.fn(async () => ({ rows: [], next: null, observationCount: 0 })),
    getForecastHistory: vi.fn(async () => source.target_source_available ? [{ period: "2026-10", sale: 17, order: 31.25, delivery: null }] : []),
    getCustomerForecast: vi.fn(async () => []), getProfiles: vi.fn(async () => [profile]), dispose: vi.fn(),
  };
  return repository;
}

async function openModelEngines(repository: ForecastReadRepository) {
  render(<ForecastTowellApp profile={profile} repository={repository} onLogout={vi.fn()}/>);
  await screen.findByText(/2 productos coinciden/);
  await userEvent.click(screen.getByLabelText("Modelo"));
  await userEvent.click(screen.getByRole("button", { name: "Limpiar selección" }));
  const group = within(screen.getByRole("group", { name: "Modelos disponibles" }));
  await userEvent.click(group.getByRole("checkbox", { name: /SYNTHETIC DESIGN/ }));
  await userEvent.click(screen.getByRole("button", { name: "Motores de Forecast" }));
  await screen.findByText(/Consulta almacenada · 2 productos/);
  await waitFor(() => expect(repository.getReportSnapshot).toHaveBeenCalled());
}

describe("Pedido source availability through the complete portal", () => {
  it("keeps unclosed history readable without manufacturing a shared preview source error", async () => {
    const repository = fixture(unclosed);
    await openModelEngines(repository);
    await waitFor(() => expect(repository.getForecastHistory).toHaveBeenCalledWith(
      expect.objectContaining({ chainId: null, commercialModels: ["SYNTHETIC DESIGN"] }), unclosed));
    expect(screen.getAllByText(/El cierre de Pedidos aún no está acreditado/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/No fue posible completar la consulta operacional/)).toBeNull();
    expect(screen.queryByText(/ORDER_SOURCE_UNAVAILABLE/)).toBeNull();
    expect((screen.getByRole("button", { name: "Calcular vista previa" }) as HTMLButtonElement).disabled).toBe(true);
    expect(repository.previews!.latest).not.toHaveBeenCalled();
    expect(repository.previews!.create).not.toHaveBeenCalled();
    expect(repository.previews!.createSelection).not.toHaveBeenCalled();
  });

  it("still reports a truly missing master and never borrows a cached result", async () => {
    // Keep the same unknown-close marker: actual availability, not that marker
    // alone, must decide whether a missing-file error may be suppressed.
    const missing = { ...unclosed, target_source_available: false,
      forecast_unavailable_reason: "Sin cálculo: fuente de pedidos no disponible" };
    const repository = fixture(missing);
    await openModelEngines(repository);
    await screen.findByText(/No fue posible completar la consulta operacional: ORDER_SOURCE_UNAVAILABLE/);
    expect((screen.getByRole("button", { name: "Calcular vista previa" }) as HTMLButtonElement).disabled).toBe(true);
    expect(repository.previews!.latest).not.toHaveBeenCalled();
    expect(repository.previews!.create).not.toHaveBeenCalled();
    expect(repository.previews!.createSelection).not.toHaveBeenCalled();
  });
});
