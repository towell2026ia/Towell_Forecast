import React from "react";
import { expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ForecastTowellApp, { ForecastFiltersContext } from "../../app/forecast-towell-app";
import type { ForecastReadRepository } from "../../lib/supabase/types";
import type { PreviewJob } from "../../lib/forecast-preview";
import { operationalContext } from "./operational-context-fixture";
import type { OperationalReadContext } from "../../lib/supabase/types";

// Observe the shared real app context independently of the engine's layout.
vi.mock("../../app/operational-engines-view", () => ({ ForecastEnginesView: () => {
  const context = React.useContext(ForecastFiltersContext);
  return <output data-testid="shared-preview">{context?.preview?.result?.job_id ?? context?.preview?.error ?? "EMPTY"}</output>;
} }));
vi.mock("../../app/forecast-assistant", () => ({ default: function SourceBoundAssistant() {
  const [saved, setSaved] = React.useState("EMPTY");
  return <><output data-testid="assistant-source-cache">{saved}</output><button onClick={() => setSaved("OLDER_SAVED_ANSWER")}>Save synthetic answer</button></>;
} }));

it("clears the previous official preview if refreshing against a changed source fails", async () => {
  let source = operationalContext;
  const latest = vi.fn(async () => {
    if (source.generation !== operationalContext.generation) throw new Error("official_source_changed");
    return { job_id: "SOURCE_A_RESULT", status: "READY_PREVIEW", mode: "RETROSPECTIVE_TRAINING", engine_version: "synthetic",
      scopes: [{ chain_id: "chain", status: "PREVIEW", source_context: source }] } as PreviewJob;
  });
  const repository = {
    openOperationalRead: async () => source,
    getVisibleChains: async () => [], getCategories: async () => [], getProducts: async () => [], getPeriods: async () => [],
    getHistoricalSummary: async () => ({ scopeCount: 0, productCount: 0, observationCount: 0, latestPeriod: null, byScope: [] }),
    previews: { latest },
  } as unknown as ForecastReadRepository;
  render(<ForecastTowellApp repository={repository} profile={{ id: "synthetic", full_name: "Synthetic", global_role: "VIEWER", status: "ACTIVE" }} onLogout={vi.fn()}/>);
  await waitFor(() => expect(latest).toHaveBeenCalled());
  await userEvent.click(screen.getByRole("button", { name: "Motores de Forecast" }));
  expect(await screen.findByText("SOURCE_A_RESULT")).toBeTruthy();
  const sourceACalls = latest.mock.calls.length;
  await userEvent.click(screen.getByRole("button", { name: "Inicio" }));
  await userEvent.click(screen.getByRole("button", { name: "Motores de Forecast" }));
  expect(screen.getByTestId("shared-preview").textContent).toBe("SOURCE_A_RESULT");
  expect(latest).toHaveBeenCalledTimes(sourceACalls);
  source = { ...operationalContext, source_hash: "b".repeat(64), generation: 3 };
  await userEvent.click(screen.getByRole("button", { name: "Inicio" }));
  await waitFor(() => expect(latest).toHaveBeenLastCalledWith(null, null, source));
  await userEvent.click(screen.getByRole("button", { name: "Motores de Forecast" }));
  await waitFor(() => expect(screen.getByTestId("shared-preview").textContent).toBe("DATA_READ_FAILED"));
  expect(screen.queryByText("SOURCE_A_RESULT")).toBeNull();
});

it("discards the assistant's previous-source answers when the authorized source changes or sales become unavailable", async () => {
  let source: OperationalReadContext = { ...operationalContext, sales_source: "TOWELL", inventory_source: "ESTADISTICA", sales_available: true,
    sales_contract: "towell-sales-v1", sales_file_sha256: "d".repeat(64), sales_sheet: "Towell", sales_training_cutoff: "2026-10", authorized_source_hash: operationalContext.source_hash };
  const repository = { openOperationalRead: async () => source, getVisibleChains: async () => [], getCategories: async () => [], getProducts: async () => [], getPeriods: async () => [],
    getHistoricalSummary: async () => ({ scopeCount: 0, productCount: 0, observationCount: 0, latestPeriod: null, byScope: [] }), previews: { latest: vi.fn(async () => null) } } as unknown as ForecastReadRepository;
  render(<ForecastTowellApp repository={repository} profile={{ id: "synthetic", full_name: "Synthetic", global_role: "VIEWER", status: "ACTIVE" }} onLogout={vi.fn()}/>);
  await screen.findByText(/Ventas: Towell/);
  expect(screen.getByText("Histórico autorizado · Solo lectura")).toBeTruthy();
  expect(screen.getByText(/Ventas autorizadas de Towell e inventario de Estadística/)).toBeTruthy();
  expect(screen.queryByText(/Histórico certificado/)).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Save synthetic answer" }));
  expect(screen.getByTestId("assistant-source-cache").textContent).toBe("OLDER_SAVED_ANSWER");
  await userEvent.click(screen.getByRole("button", { name: "Motores de Forecast" }));
  expect(screen.getByTestId("assistant-source-cache").textContent).toBe("OLDER_SAVED_ANSWER");
  await userEvent.click(screen.getByRole("button", { name: "Inicio" }));
  expect(screen.getByTestId("assistant-source-cache").textContent).toBe("OLDER_SAVED_ANSWER");
  source = { ...source, source_hash: "e".repeat(64), authorized_source_hash: "e".repeat(64), generation: 3, sales_available: false };
  await userEvent.click(screen.getByRole("button", { name: "Motores de Forecast" }));
  await waitFor(() => expect(screen.getByTestId("assistant-source-cache").textContent).toBe("EMPTY"));
  expect(screen.queryByText("OLDER_SAVED_ANSWER")).toBeNull();
  expect(repository.previews!.latest).toHaveBeenCalledTimes(1);
});
