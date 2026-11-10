import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SupabaseClient } from "@supabase/supabase-js";
import ForecastAssistant from "../../app/forecast-assistant";
import { PreviewReadError, RailwayPreviewClient } from "../../lib/forecast-preview";
import type { AssistantContext } from "../../app/assistant/assistant-service";
import { parseRsAssistantResponse, safeResearchUrl, type RsAssistantRequest, type RsAssistantResponse } from "../../lib/rs-assistant";

vi.mock("next/dynamic", () => ({ default: () => function Animation() { return <span>Animation</span>; } }));

const chain = "00000000-0000-0000-0000-000000000001";
const request: RsAssistantRequest = { chain_id: chain, format: "FORMATO", model: "MODELO DE PRUEBA", period: "2026-10", purchase_type: "RS", message: "Consulta de cobertura", action: "answer" };
const context: AssistantContext = { user: "viewer", role: "reader", chainId: chain, chain: "Cadena de prueba", screen: "Periodos", category: null, product: null, color: null, period: null, activeFilters: {}, periodRange: ["2026-10", "2026-10"], commercialModels: [request.model] };
function result(): RsAssistantResponse {
  return {
    message: "Datos del alcance consultado.",
    scope: { chain_id: chain, chain: "Cadena de prueba", format: request.format, model: request.model, period: request.period },
    quality: { products_classified: 2, temporal_certification: false },
    metrics: { by_month: [{ period: request.period, SALES: { pieces: "12.1234567890123456", products_with_data: 1, products_expected: 2 }, ORDER: { pieces: "20", products_with_data: 2, products_expected: 2 }, DELIVERY: { pieces: null, products_with_data: 0, products_expected: 2 }, fill_rate: { ratio: null, paired_products: 0, products_expected: 2 } }], inventory: { pieces: "0", products_with_data: 1, products_expected: 2 } },
    forecast: { status: "PROVISIONAL", engine: "test-engine", periods: [{ period: "2026-11", statistical: { pieces: "15.01", covered: 1, eligible: 2 }, ml: { pieces: null, covered: 0, eligible: 2 }, ensemble_value: { pieces: "14.12", covered: 1, eligible: 2 } }], retrospective: { statistical: { wape: 10, bias: -2 }, ml: { wape: null, bias: null } } },
    research: {
      status: "COMPLETED", summary: "Evidencia pública limitada.",
      started_at: "2026-11-10T20:05:12.123456+00:00", finished_at: "2026-11-10T20:07:15+00:00",
      usage: { input_tokens: 130, output_tokens: 55, total_tokens: 185, web_search_calls: 2 },
      evidence_scope: "PUBLIC_CATEGORY_CONTEXT", source_publication_dates_verified: false,
      sources: [{ title: "Fuente", url: "https://example.org/report" }, { title: "Unsafe", url: "javascript:alert(1)" }],
    },
  };
}
function panel(client: (body: RsAssistantRequest) => Promise<RsAssistantResponse>, ctx = context) {
  return <ForecastAssistant authorized uiEnabled apiEnabled={false} voiceEnabled={false} mode="local" context={ctx} rsAssistant={client}/>;
}
async function open() { await userEvent.click(screen.getByRole("button", { name: "Abrir Asistente FORECAST Towell" })); }

describe("Scoped RS assistant transport", () => {
  it("has no operational transport without an explicit valid Railway origin", () => {
    const auth = { auth: { getSession: vi.fn() } } as unknown as SupabaseClient;
    for (const origin of ["", "http://example.org", "https://example.org/path", "https://u:p@example.org"]) expect(new RailwayPreviewClient(auth, origin).rsAssistant).toBeUndefined();
    expect(auth.auth.getSession).not.toHaveBeenCalled();
  });
  it("sends only the explicit RS scope and question to Railway using the current user bearer", async () => {
    const fetcher = vi.fn(async () => Response.json(result()));
    vi.stubGlobal("fetch", fetcher);
    const auth = { auth: { getSession: vi.fn(async () => ({ data: { session: { access_token: "synthetic-user-token" } } })) } } as unknown as SupabaseClient;
    const client = new RailwayPreviewClient(auth, "https://railway.example.org");
    const response = await client.rsAssistant!(request);
    expect(fetcher).toHaveBeenCalledOnce();
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://railway.example.org/api/forecast/rs-assistant");
    expect(init).toMatchObject({ method: "POST", credentials: "omit", cache: "no-store", headers: expect.objectContaining({ Authorization: "Bearer synthetic-user-token" }) });
    expect(JSON.parse(String(init.body))).toEqual(request);
    expect(response.research?.sources).toEqual([{ title: "Fuente", url: "https://example.org/report" }]);
    expect(response.research).toMatchObject({
      started_at: result().research?.started_at, finished_at: result().research?.finished_at, usage: result().research?.usage,
      evidence_scope: "PUBLIC_CATEGORY_CONTEXT", source_publication_dates_verified: false,
    });
    vi.unstubAllGlobals();
  });
  it("refuses unauthenticated or mismatched results without a local fallback", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const auth = { auth: { getSession: vi.fn(async () => ({ data: { session: null } })) } } as unknown as SupabaseClient;
    await expect(new RailwayPreviewClient(auth, "https://railway.example.org").rsAssistant!(request)).rejects.toMatchObject({ code: "AUTH_REQUIRED" });
    expect(fetcher).not.toHaveBeenCalled();
    expect(() => parseRsAssistantResponse({ ...result(), scope: { ...result().scope, chain_id: "foreign" } }, request)).toThrow("RS_ASSISTANT_INVALID_RESPONSE");
    expect(safeResearchUrl("https://user:password@example.org")).toBeNull();
    vi.unstubAllGlobals();
  });
  it("accepts only the requested normalized model, format and authorized archive", () => {
    const original = result();
    expect(parseRsAssistantResponse({ ...original, scope: { ...original.scope, model: "módelo   de prueba", format: "FÓR-MA_TO", archive_id: "UNIF-20060057ee8e4dfdbcd9" } }, request).scope?.model).toBe("módelo   de prueba");
    for (const scope of [{ ...original.scope, model: "Otro modelo" }, { ...original.scope, format: "Otro formato" }, { ...original.scope, archive_id: "otra-version" }]) {
      expect(() => parseRsAssistantResponse({ ...original, scope }, request)).toThrow("RS_ASSISTANT_INVALID_RESPONSE");
    }
  });
  it("drops invalid research dates and usage without inventing missing values", () => {
    const original = result();
    const research = { ...original.research, started_at: "2026-02-30T20:05:12Z", finished_at: null, usage: { input_tokens: "130", output_tokens: -1, total_tokens: 1_000_001, web_search_calls: 0, extra: 3 }, evidence_scope: "OTHER", source_publication_dates_verified: "false" };
    expect(parseRsAssistantResponse({ ...original, research }, request).research).toMatchObject({ started_at: undefined, finished_at: null, usage: { web_search_calls: 0 }, evidence_scope: undefined, source_publication_dates_verified: undefined });
    expect(parseRsAssistantResponse({ ...original, research: { ...original.research, finished_at: "2026-11-10T20:04:00Z" } }, request).research?.finished_at).toBeUndefined();
  });
});

describe("Existing assistant panel with explicit RS actions", () => {
  it("does not call any operation on mount, opening, editing or scope changes", async () => {
    const client = vi.fn<(body: RsAssistantRequest) => Promise<RsAssistantResponse>>(async () => result());
    const { rerender } = render(panel(client, { ...context, chainId: null }));
    await open();
    expect(screen.getByText("Selecciona una cadena en los filtros del portal.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Enviar mensaje" }).hasAttribute("disabled")).toBe(true);
    rerender(panel(client, { ...context, commercialModels: ["Modelo A", "Modelo B"], periodRange: [null, null] }));
    await userEvent.type(screen.getByLabelText("Formato comercial"), request.format);
    await userEvent.selectOptions(screen.getByLabelText("Modelo"), "Modelo B");
    fireEvent.change(screen.getByLabelText("Mes"), { target: { value: request.period } });
    expect(client).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Calcular / consultar pronóstico RS" }).hasAttribute("disabled")).toBe(false);
  });
  it("renders exact pieces, coverage, retrospective metrics and only safe cited links after an explicit action", async () => {
    const client = vi.fn<(body: RsAssistantRequest) => Promise<RsAssistantResponse>>(async () => result());
    render(panel(client)); await open();
    await userEvent.type(screen.getByLabelText("Formato comercial"), request.format);
    await userEvent.click(screen.getByRole("button", { name: "Calcular / consultar pronóstico RS" }));
    await screen.findByText("Datos del alcance consultado.");
    expect(client).toHaveBeenCalledWith({ ...request, action: "forecast", message: "" });
    expect(screen.getByText(/12\.1234567890123456 piezas/)).toBeTruthy();
    expect(screen.getByRole("table", { name: "Pronóstico RS del asistente" }).textContent).toContain("15.01");
    expect(screen.getByText(/WAPE retrospectivo 10% · Bias retrospectivo -2%/)).toBeTruthy();
    const source = screen.getByRole("link", { name: "Fuente" }); expect(source.getAttribute("rel")).toBe("noopener noreferrer");
    expect(screen.queryByRole("link", { name: "Unsafe" })).toBeNull();
    expect(screen.getByText("Contexto público de la categoría.")).toBeTruthy();
    expect(screen.getByText("Fechas de publicación de las fuentes sin acreditar.")).toBeTruthy();
    const dates = screen.getByLabelText("Fechas de ejecución");
    expect(Array.from(dates.querySelectorAll("time")).map(time => time.dateTime)).toEqual([result().research?.started_at, result().research?.finished_at]);
    expect(within(screen.getByLabelText("Consumo de la investigación")).getByText("185")).toBeTruthy();
    expect(client).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Consultar estado de investigación" }));
    await waitFor(() => expect(client).toHaveBeenCalledTimes(2));
    expect(client.mock.calls[1][0].action).toBe("research_status");
  });
  it("shows a concrete inactive-data blocker, and leaves old-scope answers outside the new conversation", async () => {
    const client = vi.fn(async () => { throw new PreviewReadError("RS_CANDIDATE_NOT_ACTIVE"); });
    const { rerender } = render(panel(client)); await open();
    await userEvent.type(screen.getByLabelText("Formato comercial"), request.format);
    await userEvent.type(screen.getByLabelText("Mensaje para el asistente"), "Consultar inventario");
    await userEvent.click(screen.getByRole("button", { name: "Enviar mensaje" }));
    await screen.findByText(/La versión RS requerida todavía no está activa/);
    rerender(panel(client, { ...context, chainId: "00000000-0000-0000-0000-000000000002" }));
    expect(within(screen.getByLabelText("Conversación del asistente")).queryByText(/La versión RS requerida/)).toBeNull();
    expect((screen.getByLabelText("Formato comercial") as HTMLInputElement).value).toBe("");
    expect(client).toHaveBeenCalledOnce();
  });
});
