const markerKey = "towell-password-recovery";
const recoveryWindowMs = 20 * 60 * 1000;

// Let the official SDK verify credentials. Never accept an arbitrary URL as
// identity, and never process callback credentials on the normal portal/login.
export function isRecoveryCallback(url: URL, params: Record<string, string>): boolean {
  return url.pathname === "/update-password" && (params.type === "recovery" || Boolean(params.error || params.error_code));
}
export function passwordRecoveryRedirect(origin: string): string {
  const url = new URL(origin);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && url.hostname === "localhost")) throw new Error("invalid_recovery_origin");
  return new URL("/update-password", url.origin).href;
}
export function clearRecoverySession() {
  try { window.sessionStorage.removeItem(markerKey); } catch { /* Storage can be disabled. */ }
}
export function rememberRecoverySession(userId: string) {
  try { window.sessionStorage.setItem(markerKey, JSON.stringify({ userId, expiresAt: Date.now() + recoveryWindowMs })); } catch { /* Current in-memory flow remains usable. */ }
}
export function recoveryUserId(): string | null {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(markerKey) ?? "null");
    if (typeof value?.userId === "string" && Number.isFinite(value.expiresAt) && value.expiresAt > Date.now() && value.expiresAt <= Date.now() + recoveryWindowMs) return value.userId;
  } catch { /* Invalid marker is not authority. */ }
  clearRecoverySession(); return null;
}
