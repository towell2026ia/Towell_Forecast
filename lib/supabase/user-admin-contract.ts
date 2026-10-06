import type { Profile, Role } from "./types";

export type ChainGrant = {
  chain_id: string;
  can_edit: boolean;
  can_import: boolean;
  can_run_forecast: boolean;
  can_approve: boolean;
};
export type ManagedUser = {
  id: string;
  email: string;
  email_confirmed: boolean;
  invited: boolean;
  last_sign_in_at: string | null;
  profile: Profile | null;
  grants: ChainGrant[];
};
export type UserMutation = {
  full_name: string;
  global_role: Role;
  status: "ACTIVE" | "INACTIVE";
  grants: ChainGrant[];
};

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export function normalizedEmail(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const email = value.trim().toLowerCase();
  return email.length <= 254 && emailPattern.test(email) ? email : null;
}
export function validUserId(value: unknown): value is string {
  return typeof value === "string" && uuid.test(value);
}
export function parseUserMutation(value: unknown): UserMutation | null {
  if (!value || typeof value !== "object") return null;
  const input = value as Record<string, unknown>;
  const name = typeof input.full_name === "string" ? input.full_name.trim() : "";
  const role = input.global_role;
  const status = input.status;
  if (name.length < 2 || name.length > 150 || !["ADMIN", "EDITOR", "VIEWER"].includes(String(role)) ||
      !["ACTIVE", "INACTIVE"].includes(String(status)) || !Array.isArray(input.grants) || input.grants.length > 1000) return null;
  if ((role === "ADMIN" || status === "INACTIVE") && input.grants.length !== 0) return null;
  if (role !== "ADMIN" && status === "ACTIVE" && input.grants.length === 0) return null;
  const seen = new Set<string>();
  const grants: ChainGrant[] = [];
  for (const raw of input.grants) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const grant = raw as Record<string, unknown>;
    if (Object.keys(grant).sort().join() !== "can_approve,can_edit,can_import,can_run_forecast,chain_id" || !validUserId(grant.chain_id)) return null;
    const chainId = grant.chain_id.toLowerCase();
    if (seen.has(chainId) || [grant.can_edit, grant.can_import, grant.can_run_forecast, grant.can_approve].some(flag => typeof flag !== "boolean")) return null;
    if (role === "VIEWER" && (grant.can_edit || grant.can_import || grant.can_run_forecast || grant.can_approve)) return null;
    seen.add(chainId);
    grants.push({ chain_id: chainId, can_edit: grant.can_edit as boolean, can_import: grant.can_import as boolean,
      can_run_forecast: grant.can_run_forecast as boolean, can_approve: grant.can_approve as boolean });
  }
  return { full_name: name, global_role: role as Role, status: status as "ACTIVE" | "INACTIVE", grants };
}
