import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SupabaseClient } from "@supabase/supabase-js";
import { ForecastFiltersContext, UsersView } from "../../app/forecast-towell-app";
import { parseUserMutation, normalizedEmail } from "../../lib/supabase/user-admin-contract";
import { SupabaseUserAdminClient } from "../../lib/supabase/user-admin";
import { emptyFilters, type ForecastReadRepository, type Profile } from "../../lib/supabase/types";

const adminProfile: Profile = { id: "00000000-0000-4000-8000-000000000001", full_name: "Admin", global_role: "ADMIN", status: "ACTIVE" };
const chain = { id: "00000000-0000-4000-8000-000000000002", name: "Al Super", code: "AS", status: "ACTIVE", has_history: true, parentId: null, scopeType: null };
function mount(userAdmin: ForecastReadRepository["userAdmin"], profile = adminProfile) {
  const repository = { userAdmin, getVisibleChains: vi.fn(async () => [chain]), getProfiles: vi.fn(async () => [adminProfile]) } as unknown as ForecastReadRepository;
  render(<ForecastFiltersContext.Provider value={{ repository, profile, filters: emptyFilters, setFilters: vi.fn() }}><UsersView/></ForecastFiltersContext.Provider>);
}

describe("User management", () => {
  it("validates role, explicit chain access and safe email normalization", () => {
    expect(normalizedEmail("  Ana@Towell.com.mx ")).toBe("ana@towell.com.mx");
    expect(parseUserMutation({ full_name: "Ana", global_role: "VIEWER", status: "ACTIVE", grants: [] })).toBeNull();
    expect(parseUserMutation({ full_name: "Ana", global_role: "VIEWER", status: "ACTIVE", grants: [{ chain_id: chain.id, can_edit: true, can_import: false, can_run_forecast: false, can_approve: false }] })).toBeNull();
    expect(parseUserMutation({ full_name: "Ana", global_role: "ADMIN", status: "ACTIVE", grants: [] })?.global_role).toBe("ADMIN");
  });
  it("calls only the protected Supabase function using the current SDK session", async () => {
    const invoke = vi.fn(async () => ({ data: { users: [] }, error: null }));
    const client = new SupabaseUserAdminClient({ functions: { invoke } } as unknown as SupabaseClient);
    await client.list();
    expect(invoke).toHaveBeenCalledWith("user-admin", { body: { action: "list" } });
    expect(JSON.stringify(invoke.mock.calls)).not.toContain("service_role");
  });
  it("invites a VIEWER with explicit chain permission", async () => {
    const userAdmin = { list: vi.fn(async () => []), invite: vi.fn(async () => ({ existing_auth_user: false, email_sent: true })),
      update: vi.fn(), deactivate: vi.fn(), resend: vi.fn() };
    mount(userAdmin);
    await userEvent.click(await screen.findByRole("button", { name: "Agregar usuario" }));
    await userEvent.type(screen.getByLabelText("Nombre"), "Ana Towell");
    await userEvent.type(screen.getByLabelText("Correo"), "ana@towell.com.mx");
    await userEvent.click(screen.getByLabelText("Al Super"));
    await userEvent.click(screen.getByRole("button", { name: "Enviar invitación" }));
    await waitFor(() => expect(userAdmin.invite).toHaveBeenCalledWith("ana@towell.com.mx", {
      full_name: "Ana Towell", global_role: "VIEWER", status: "ACTIVE",
      grants: [{ chain_id: chain.id, can_edit: false, can_import: false, can_run_forecast: false, can_approve: false }],
    }));
  });
  it("does not expose management controls to a VIEWER", () => {
    mount(undefined, { ...adminProfile, global_role: "VIEWER" });
    expect(screen.queryByText("Usuarios y permisos")).toBeNull();
  });
});
