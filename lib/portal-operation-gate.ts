// E1 is read-only. No identity header or browser role can enable these endpoints.
export function disabledPortalOperation(): Response {
  return Response.json({ error: "operation_disabled", message: "Operación no habilitada en esta fase." }, { status: 403, headers: { "Cache-Control": "no-store" } });
}
