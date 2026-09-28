export type AssistantStatus = "ready" | "processing" | "disabled" | "error";

export type AssistantRole = "manager" | "editor" | "reader";

export type AssistantContext = {
  user: string;
  role: AssistantRole;
  screen: string;
  activeFilters: Record<string, string | null>;
  chain: string | null;
  category: string | null;
  product: string | null;
  color: string | null;
  period: string | null;
  // Display context only, never an authorization claim accepted by a backend.
  globalRole?: "ADMIN" | "EDITOR" | "VIEWER";
  moduleId?: string;
  chainId?: string | null;
  categoryId?: string | null;
  productId?: string | null;
  periodRange?: [string | null, string | null];
  search?: string;
};

export type AssistantAction = {
  type: "navigate" | "apply_filter" | "open_record";
  label: string;
  payload: Record<string, string>;
};

export type AssistantResponse = {
  message: string;
  status: AssistantStatus;
  source: "mock" | "local" | "future-ai";
  actions: AssistantAction[];
  metadata: Record<string, string | number | boolean | null>;
  intent?: string | null;
  data?: Record<string, unknown>;
};

export type AssistantMode = "mock" | "local" | "future-ai";

export interface AssistantProvider {
  send(message: string, context: AssistantContext): Promise<AssistantResponse>;
}

export class MockProvider implements AssistantProvider {
  async send(_message: string, context: AssistantContext): Promise<AssistantResponse> {
    await new Promise((resolve) => window.setTimeout(resolve, 360));
    return {
      message: "El asistente está en preparación para la siguiente fase. No se envían datos ni se generan análisis automáticos.",
      status: "disabled",
      source: "mock",
      actions: [],
      metadata: { screen: context.screen, apiEnabled: false },
    };
  }
}

export class FutureAIProvider implements AssistantProvider {
  async send(): Promise<AssistantResponse> {
    throw new Error("FutureAIProvider no está habilitado en PRD 08A.");
  }
}

export class LocalAssistantProvider implements AssistantProvider {
  async send(message: string, context: AssistantContext): Promise<AssistantResponse> {
    const response = await fetch("/api/assistant/message", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message, context }),
    });
    if (!response.ok) {
      return {
        message: response.status === 403
          ? "No tienes permiso para consultar el asistente."
          : "El servicio local del asistente no está disponible en este momento.",
        status: "error", source: "local", actions: [], metadata: { apiEnabled: true },
      };
    }
    const result = await response.json() as {
      message: string; status: string; intent: string | null;
      data: Record<string, unknown>; metadata: Record<string, string | number | boolean | null>;
    };
    return {
      message: result.message,
      status: result.status === "success" || result.status === "unrecognized" ? "ready" : "error",
      source: "local", actions: [], metadata: result.metadata ?? {},
      intent: result.intent, data: result.data,
    };
  }
}

export async function sendAssistantMessage(
  message: string,
  context: AssistantContext,
  options: { mode: AssistantMode; apiEnabled: boolean },
): Promise<AssistantResponse> {
  if (!message.trim()) {
    return { message: "Escribe una pregunta para continuar.", status: "ready", source: "mock", actions: [], metadata: {} };
  }

  if (options.mode === "local" && !options.apiEnabled) {
    return { message: "El servicio local del asistente está deshabilitado.", status: "disabled",
      source: "local", actions: [], metadata: { apiEnabled: false } };
  }
  const provider: AssistantProvider = options.mode === "local"
    ? new LocalAssistantProvider()
    : options.mode === "future-ai" && options.apiEnabled
      ? new FutureAIProvider()
      : new MockProvider();

  return provider.send(message, context);
}
