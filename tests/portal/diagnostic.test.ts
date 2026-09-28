import { describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import { SupabaseForecastReadRepository } from "../../lib/forecast-data";
import { controlledReadCode, parseReadDiagnostic, PortalReadError } from "../../lib/portal-read-diagnostic";

describe("Controlled read diagnostics, not identity or data logs", () => {
  it("retains HTTP status, table and safe PostgREST code, never upstream text", async () => {
    const upstream = { message: "password=private access_token=private", code: "42501", details: "Authorization: Bearer private", hint: "private" };
    const query = { select() { return this; }, order() { return this; }, limit() { return this; }, abortSignal() { return this; }, then(resolve: (r: unknown) => void) { resolve({ data: null, error: upstream, status: 403 }); } };
    const client = { from: vi.fn(() => query) } as unknown as SupabaseClient;
    const log = vi.spyOn(console, "info").mockImplementation(() => {}), callback = vi.fn();
    const repo = new SupabaseForecastReadRepository(client, "private-user", callback);
    await expect(repo.getCategories()).rejects.toBeInstanceOf(PortalReadError);
    expect(callback).toHaveBeenCalledWith({ endpoint: "getCategories", table: "categories", http_status: 403, code: "42501", operation: "select" });
    expect(JSON.stringify(log.mock.calls)).not.toMatch(/private|Bearer|password|Authorization/);
  });
  it("allowlists machine codes, never raw errors or arbitrary payload fields", () => {
    expect(controlledReadCode("PGRST100")).toBe("PGRST100");
    expect(controlledReadCode("Bearer private")).toBe("READ_FAILED");
    const d = { endpoint: "getPeriods", table: "portal_monthly_observations_current", http_status: 200, code: "INVALID_PERIOD", operation: "period_bounds" };
    expect(parseReadDiagnostic(d)).toEqual(d);
    for (const value of [{ ...d, user: "private" }, { ...d, endpoint: "/auth?token=private" }, { ...d, table: "auth.users" }, { ...d, code: "private" }, { ...d, http_status: NaN }, null, []]) expect(parseReadDiagnostic(value)).toBeNull();
  });
});
