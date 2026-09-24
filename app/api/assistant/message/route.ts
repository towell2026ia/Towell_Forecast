import { NextResponse } from "next/server";
import { createHmac, randomUUID } from "node:crypto";
import { getChatGPTUser } from "@/app/chatgpt-auth";

export async function POST(request: Request) {
  if (process.env.AI_ASSISTANT_API_ENABLED === "false") {
    return NextResponse.json({ error: "assistant_disabled" }, { status: 503 });
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  if (!body || typeof body !== "object" || typeof (body as { message?: unknown }).message !== "string" ||
      !(body as { message: string }).message.trim() || (body as { message: string }).message.length > 1000) {
    return NextResponse.json({ error: "invalid_message" }, { status: 400 });
  }
  const environment = process.env.APP_ENV ?? process.env.NODE_ENV;
  const local = environment === "development";
  const base = process.env.PYTHON_ASSISTANT_URL ?? "http://127.0.0.1:8000";
  const secret = process.env.ASSISTANT_API_TOKEN;
  let actorId = "local-manager";
  let token = secret;
  if (!local) {
    // Only a trusted hosting proxy may supply authenticated-user headers. The
    // browser's role, actor and context fields are never used for authority.
    const user = await getChatGPTUser();
    if (!user) return NextResponse.json({ error: "assistant_auth_required" }, { status: 401 });
    const managers = new Set((process.env.ASSISTANT_MANAGER_IDS ?? "").split(",").map((id) => id.trim()));
    if (!managers.has(user.userId)) return NextResponse.json({ error: "assistant_permission_denied" }, { status: 403 });
    if (!secret || secret.length < 32 || !process.env.PYTHON_ASSISTANT_URL) {
      return NextResponse.json({ error: "assistant_identity_not_configured" }, { status: 503 });
    }
    actorId = user.userId;
    const issued = Math.floor(Date.now() / 1000);
    const claims = { sub: actorId, user_id: actorId, session_id: randomUUID(),
      iss: `forecast-towell-frontend:${environment}`, aud: "forecast-towell-fastapi",
      iat: issued, exp: issued + 60, role: "manager",
      permissions: ["ADMIN", "EXECUTE", "READ"] };
    const encoded = Buffer.from(JSON.stringify(claims)).toString("base64url");
    const signature = createHmac("sha256", secret).update(encoded).digest("base64url");
    token = `${encoded}.${signature}`;
  }
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(new URL("/api/assistant/message", base), {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-actor-id": actorId,
          ...(token ? { "x-assistant-token": token } : {}),
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) return NextResponse.json({ error: "assistant_backend_unavailable" }, { status: response.status === 403 ? 403 : 503 });
      return NextResponse.json(await response.json());
    } finally {
      clearTimeout(timeout);
    }
  } catch {
    return NextResponse.json({ error: "python_assistant_unavailable" }, { status: 503 });
  }
}
