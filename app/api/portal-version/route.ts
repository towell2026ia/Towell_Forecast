export function GET() {
  // Public build provenance only. No auth/session/configuration dump.
  return Response.json({ git_sha: process.env.NEXT_PUBLIC_GIT_SHA ?? "unknown", site_url: process.env.NEXT_PUBLIC_SITE_URL ?? null }, { headers: { "Cache-Control": "no-store" } });
}
