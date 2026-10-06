import type { SupabaseClient } from "@supabase/supabase-js";
import type { ManagedUser, UserMutation } from "./user-admin-contract";

export interface UserAdminClient {
  list(): Promise<ManagedUser[]>;
  invite(email: string, user: UserMutation): Promise<{ existing_auth_user: boolean; email_sent: boolean }>;
  update(targetId: string, user: UserMutation): Promise<void>;
  deactivate(targetId: string): Promise<void>;
  resend(targetId: string): Promise<void>;
}

export class SupabaseUserAdminClient implements UserAdminClient {
  constructor(private readonly client: SupabaseClient) {}
  private async invoke<T>(body: Record<string, unknown>): Promise<T> {
    const { data, error } = await this.client.functions.invoke("user-admin", { body });
    if (error || !data || typeof data !== "object" || "error" in data) throw new Error("user_management_unavailable");
    return data as T;
  }
  async list() {
    const result = await this.invoke<{ users: ManagedUser[] }>({ action: "list" });
    if (!Array.isArray(result.users)) throw new Error("user_management_unavailable");
    return result.users;
  }
  invite(email: string, user: UserMutation) {
    return this.invoke<{ existing_auth_user: boolean; email_sent: boolean }>({ action: "invite", email, user });
  }
  async update(targetId: string, user: UserMutation) {
    await this.invoke({ action: "update", target_id: targetId, user });
  }
  async deactivate(targetId: string) {
    await this.invoke({ action: "deactivate", target_id: targetId });
  }
  async resend(targetId: string) {
    await this.invoke({ action: "resend", target_id: targetId });
  }
}
