import { afterEach, describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { RailwayPreviewClient } from "../../lib/forecast-preview";
import type { RsAssistantRequest } from "../../lib/rs-assistant";
import { operationalContext } from "./operational-context-fixture";

const local = "http://127.0.0.1:8000";
const chain = "00000000-0000-0000-0000-000000000001";
const request: RsAssistantRequest = { chain_id: chain, format: "BD", model: "MODEL", period: "2026-10", purchase_type: "RS", action: "answer", message: "Cobertura" };
function environment(mode: string, enabled?: string) {
  vi.stubEnv("NODE_ENV", mode);
  vi.stubEnv("NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED", enabled);
}
function auth(token: string | null = "synthetic-local-access") {
  return { rpc: () => ({ abortSignal: async () => ({ data: [operationalContext], error: null }) }),
    auth: { getSession: async () => ({ data: { session: token ? { access_token: token } : null } }) } } as unknown as SupabaseClient;
}
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("Local forecast API boundary", () => {
  it.each([local, local + "/"])("reuses authenticated endpoint contracts at %s only when explicitly enabled", async origin => {
    environment("development", "true");
    const fetcher = vi.fn(async (url: string) => {
      if (url.endsWith("/api/ready")) return Response.json({ forecast_calculation_history: true });
      if (url.includes("quality-gates")) return Response.json({ chain_id: chain, dataset_hash: "synthetic", data_quality: {}, forecast_quality: {}, service_level: {} });
      if (url.includes("commercial-catalog")) return Response.json({ products: [], persisted: false, source_context: operationalContext });
      if (url.includes("/calculations")) return Response.json({ calculations: [] });
      if (url.endsWith("/vintages/candidates")) return Response.json({ vintage_id: "v1" });
      if (url.includes("/vintages?")) return Response.json({ chain_id: chain, vintages: [] });
      if (url.endsWith("/vintages/v1")) return Response.json({ vintage: { id: "v1", chain_id: chain }, run: { chain_id: chain }, horizons: [], gates: [], models: [] });
      if (url.endsWith("/rs-assistant")) return Response.json({ message: "Datos del alcance", scope: { chain_id: chain, chain: "Synthetic", format: request.format,
        model: request.model, period: request.period, ...operationalContext }, metrics: { by_month: [] } });
      return Response.json({ job_id: "synthetic-job", status: "READY_PREVIEW", scopes: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const client = new RailwayPreviewClient(auth(), origin);
    expect((await client.status("synthetic-job")).job_id).toBe("synthetic-job");
    expect((await client.qualityGates(chain)).dataset_hash).toBe("synthetic");
    expect(await client.vintages(chain)).toEqual([]);
    expect((await client.vintageDetail("v1")).vintage.id).toBe("v1");
    expect((await client.vintageCapabilities()).forecast_calculation_history).toBe(true);
    expect(await client.commercialCatalog(operationalContext)).toEqual([]);
    expect(await client.calculations(chain, "synthetic-product")).toEqual([]);
    expect(await client.createCandidate(chain, "synthetic-preview")).toBe("v1");
    expect((await client.rsAssistant!(request)).message).toBe("Datos del alcance");
    for (const [url, options] of fetcher.mock.calls as unknown as [string, RequestInit][]) {
      expect(new URL(url).origin).toBe(local);
      expect(options.credentials).toBe("omit");
      if (!url.endsWith("/api/ready")) expect(options.headers).toHaveProperty("Authorization", "Bearer synthetic-local-access");
    }
    client.dispose();
  });

  it.each([
    ["production", "true"], ["test", "true"], ["development", undefined], ["development", "false"], ["development", "TRUE"],
  ])("rejects local HTTP before network in mode %s with flag %s", async (mode, flag) => {
    environment(mode!, flag);
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const client = new RailwayPreviewClient(auth(), local);
    expect(client.rsAssistant).toBeUndefined();
    for (const read of [() => client.status("job"), () => client.vintageCapabilities(), () => client.calculations(chain, "p")])
      await expect(read()).rejects.toThrow("PREVIEW_CONFIGURATION_REQUIRED");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    "http://backend.example", "http://localhost:8000", "http://127.0.0.2:8000", "http://127.0.0.1:8001", "http://127.1:8000",
    "http://2130706433:8000", "http://user:password@127.0.0.1:8000", local + "/path", local + "?remote=1", local + "#remote",
    "https://user:password@backend.example", "https://backend.example/path", "https://backend.example?remote=1",
  ])("retains host and URL protections in development: %s", async origin => {
    environment("development", "true");
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const client = new RailwayPreviewClient(auth(), origin);
    expect(client.rsAssistant).toBeUndefined();
    for (const read of [() => client.status("job"), () => client.qualityGates(chain), () => client.vintages(chain), () => client.vintageDetail("v1"),
      () => client.vintageCapabilities(), () => client.calculations(chain, "p"), () => client.createCandidate(chain, "preview")])
      await expect(read()).rejects.toThrow("PREVIEW_CONFIGURATION_REQUIRED");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("still requires an authenticated session for local business data", async () => {
    environment("development", "true");
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const client = new RailwayPreviewClient(auth(null), local);
    for (const read of [() => client.status("job"), () => client.calculations(chain, "p"), () => client.rsAssistant!(request)])
      await expect(read()).rejects.toThrow("AUTH_REQUIRED");
    expect(fetcher).not.toHaveBeenCalled();
  });
});
