import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ForecastTowellApp from "../../app/forecast-towell-app";
import ForecastAssistant, { visualAssistantNotice } from "../../app/forecast-assistant";
import type { AssistantContext } from "../../app/assistant/assistant-service";
import type { ForecastReadRepository, Profile } from "../../lib/supabase/types";
import { readFileSync } from "node:fs";

vi.mock("next/dynamic", () => ({ default: () => function Animation({ onError }: { onError: () => void }) { return <span data-testid="lottie" onClick={event => { event.stopPropagation(); onError(); }}>Animation fixture</span>; } }));
const chain = { id: "chain-new", code: "NEW", name: "Cadena desde catálogo", status: "ACTIVE", has_history: true, parentId: null, scopeType: "PARENT_CHAIN" };
const profile: Profile = { id: "fixture-user", full_name: "Usuario", global_role: "ADMIN", status: "ACTIVE" };
const product = { id: "product-new", chain_id: chain.id, category_id: "category-new", product_code: "ITEM", variant_code: null, description: "Producto nuevo", identifiers: [] };
function fixture(): ForecastReadRepository {
  return { getCurrentProfile: vi.fn(async () => profile), getVisibleChains: vi.fn(async () => [chain]), getCategories: vi.fn(async () => [{ id: "category-new", chain_id: chain.id, name: "Categoría nueva" }]), getProducts: vi.fn(async () => [product]), getPeriods: vi.fn(async () => ["2026-07"]), getHistoricalSummary: vi.fn(async () => ({ scopeCount: 1, productCount: 1, observationCount: 3, latestPeriod: "2026-07", byScope: [{ chain, observations: 3 }] })), getHistoricalObservations: vi.fn(async () => ({ rows: [], next: null, observationCount: 0 })), getProfiles: vi.fn(async () => [profile]), dispose: vi.fn() };
}
const context: AssistantContext = { user: profile.id, role: "manager", globalRole: "ADMIN", screen: "Inicio", chain: null, category: null, product: null, color: null, period: null, activeFilters: {}, periodRange: [null, null] };
async function pick(label: string, value: string) {
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  await userEvent.click(await screen.findByRole("option", { name: value }));
}
const launcher = () => screen.getByRole("button", { name: "Abrir Asistente FORECAST Towell" });

describe("Global authenticated assistant, visual-only phase", () => {
  it("mounts one floating launcher throughout all eight modules while API is OFF", async () => {
    render(<ForecastTowellApp profile={profile} repository={fixture()} onLogout={vi.fn()}/>);
    const original = launcher(); expect(original.className).toMatch(/fixed bottom-5 right-5/);
    for (const moduleName of ["Inicio", "Histórico", "Motores de Forecast", "Calidad de datos", "Periodos", "Centro de captura", "Usuarios", "Auditoría"]) {
      await userEvent.click(screen.getByRole("button", { name: moduleName }));
      expect(launcher()).toBe(original);
    }
    expect(document.body.textContent).not.toMatch(/FENDI|Walmart|consulta local/);
  });
  it("uses actual selected scope, role, module and all filters in the panel", async () => {
    render(<ForecastTowellApp profile={profile} repository={fixture()} onLogout={vi.fn()}/>);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Cadena / unidad comercial" }).hasAttribute("disabled")).toBe(false));
    await pick("Cadena / unidad comercial", chain.name);
    await pick("Categoría", "Categoría nueva");
    await pick("Producto", "Producto nuevo · ITEM");
    await pick("Periodo desde", "2026-07");
    await pick("Periodo hasta", "2026-07");
    await userEvent.type(screen.getByLabelText("Buscar producto, ITEM, UPC o variante"), "ITEM");
    await pick("Producto", "Producto nuevo · ITEM");
    await userEvent.click(screen.getByRole("button", { name: "Histórico" }));
    await userEvent.click(launcher());
    const panel = screen.getByRole("dialog");
    expect(panel.textContent).toContain(chain.name); expect(panel.textContent).toContain("ADMIN");
    expect(panel.textContent).toContain("Histórico"); expect(panel.textContent).toContain("category-new");
    expect(panel.textContent).toContain("product-new"); expect(panel.textContent).toContain("2026-07 → 2026-07");
    expect(panel.textContent).toContain("ITEM"); expect(panel.textContent).toContain(visualAssistantNotice);
    expect(panel.textContent).not.toMatch(/FENDI|Walmart|consulta local/);
    await userEvent.click(within(panel).getByRole("button", { name: "Close" }));
    await userEvent.click(screen.getByRole("button", { name: "Inicio" }));
    await userEvent.click(launcher());
    expect(screen.getByRole("dialog").textContent).toContain("Módulo: Inicio");
    expect(screen.getByRole("dialog").textContent).toContain(chain.name);
  });
  it("visual panel makes zero assistant API and microphone requests", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const getUserMedia = vi.fn(); Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
    render(<ForecastAssistant authorized uiEnabled apiEnabled={false} voiceEnabled={false} mode="local" context={context}/>);
    await userEvent.click(launcher());
    expect(screen.getByText(visualAssistantNotice)).toBeTruthy();
    const input = screen.getByRole("textbox", { name: "Mensaje para el asistente" });
    expect(input.hasAttribute("disabled")).toBe(true);
    fireEvent.change(input, { target: { value: "run forecast" } }); fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("button", { name: "Enviar mensaje" }).hasAttribute("disabled")).toBe(true);
    expect(screen.queryByRole("button", { name: /voz|micrófono/i })).toBeNull();
    expect(fetcher).not.toHaveBeenCalled(); expect(getUserMedia).not.toHaveBeenCalled(); vi.unstubAllGlobals();
  });
  it("fallback retains the launcher and panel when Lottie fails", async () => {
    render(<ForecastAssistant authorized uiEnabled apiEnabled={false} voiceEnabled={false} mode="local" context={context}/>);
    fireEvent.click(screen.getByTestId("lottie"));
    expect(launcher().textContent).toBe("FT"); await userEvent.click(launcher());
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
  it("launcher is removed together with the authenticated shell on logout", async () => {
    function AuthHarness() {
      const [active, setActive] = React.useState(true);
      return active ? <ForecastTowellApp profile={profile} repository={fixture()} onLogout={async () => { setActive(false); }}/> : <div>Login fixture</div>;
    }
    render(<AuthHarness/>); expect(launcher()).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /Cerrar sesión/ }));
    expect(screen.getByText("Login fixture")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Abrir Asistente FORECAST Towell" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("cannot show the assistant outside authorized UI", () => {
    const { rerender } = render(<ForecastAssistant authorized={false} uiEnabled apiEnabled={false} voiceEnabled={false} mode="local" context={context}/>);
    expect(screen.queryByRole("button")).toBeNull();
    rerender(<ForecastAssistant authorized uiEnabled={false} apiEnabled={false} voiceEnabled={false} mode="local" context={context}/>);
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("runtime assistant sources have no pilot hardcode or microphone permission API", () => {
    for (const path of ["app/forecast-assistant.tsx", "app/forecast-towell-app.tsx", "app/assistant/assistant-service.ts"]) expect(readFileSync(path, "utf8")).not.toMatch(/FENDI|Walmart|consulta local|getUserMedia|requestPermission/);
    const animation = JSON.parse(readFileSync("public/lottie/forecast-assistant.json", "utf8"));
    expect(animation.layers.length).toBeGreaterThan(0);
  });
});
