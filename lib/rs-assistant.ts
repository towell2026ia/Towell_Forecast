export type RsAssistantAction = "answer" | "research" | "research_status" | "forecast";
export type RsAssistantRequest = {
  chain_id: string;
  purchase_type: "RS";
  format: string;
  model: string;
  period: string;
  message: string;
  action: RsAssistantAction;
};

export type RsAssistantResponse = {
  message: string;
  uncertainty?: string;
  scope?: { chain_id: string; chain: string; format: string; model: string; presentation?: string; period: string; archive_id?: string; version_id?: string; generation?: number };
  metrics?: { by_month: RsMonth[]; inventory?: RsCoverage };
  quality?: { products_classified?: number; products_excluded_ambiguous?: number; temporal_certification?: boolean };
  forecast?: {
    status: "PROVISIONAL";
    engine: string;
    periods: { period: string; statistical: RsCandidate; ml: RsCandidate; ensemble_value: RsCandidate }[];
    retrospective?: Partial<Record<"statistical" | "ml", { wape: number | null; bias: number | null }>>;
  };
  research?: {
    status: string;
    summary?: string;
    sources?: { title: string; url: string }[];
    reason?: string;
    model?: string;
    started_at?: string;
    finished_at?: string | null;
    usage?: Partial<Record<RsResearchUsageField, number>>;
    evidence_scope?: "PUBLIC_CATEGORY_CONTEXT";
    source_publication_dates_verified?: boolean;
  };
};

const researchUsageFields = ["input_tokens", "output_tokens", "total_tokens", "web_search_calls"] as const;
type RsResearchUsageField = typeof researchUsageFields[number];
export const rsMetrics = { SALES: "Venta", ORDER: "Pedido", DELIVERY: "Entrega", FCST_TOWELL_HISTORY: "Fcst Towell", FCST_CLIENTE_HISTORY: "Fcst Cliente" } as const;
type RsCoverage = { pieces: string | null; products_with_data: number; products_expected: number };
type RsMonth = { period: string; fill_rate?: { ratio: string | null; paired_products: number; products_expected: number } } & Partial<Record<keyof typeof rsMetrics, RsCoverage>>;
type RsCandidate = { pieces: string | null; covered: number; eligible: number };

export type RsAssistantClient = (request: RsAssistantRequest) => Promise<RsAssistantResponse>;

export function safeResearchUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function assistantScopeIsValid(scope: Omit<RsAssistantRequest, "action" | "message">) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(scope.chain_id)
    && /^20\d{2}-(0[1-9]|1[0-2])$/.test(scope.period)
    && scope.purchase_type === "RS" && scope.format.length > 0 && scope.format.length <= 60
    && scope.model.length > 0 && scope.model.length <= 80 && !scope.model.startsWith("__");
}

const validPieces = (value: unknown) => value === null || typeof value === "string" && /^\d+(?:\.\d+)?$/.test(value);
const validCount = (value: number) => Number.isSafeInteger(value) && value >= 0;
const validCoverage = (item?: RsCoverage) => item && validPieces(item.pieces) && validCount(item.products_with_data) && validCount(item.products_expected) && item.products_with_data <= item.products_expected;
const validCandidate = (item?: RsCandidate) => item && validPieces(item.pieces) && validCount(item.covered) && validCount(item.eligible) && item.covered <= item.eligible;
const commercialKey = (value: string) => value.normalize("NFKD").replace(/\p{M}/gu, "").toUpperCase().trim().replace(/\s+/gu, " ");
const formatKey = (value: string) => commercialKey(value).replace(/[\s_-]+/gu, "");

function researchTimestamp(value: unknown) {
  if (typeof value !== "string") return undefined;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d{1,6})?(?:Z|\+00:00)$/.exec(value);
  if (!match) return undefined;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 19) === match[1] ? value : undefined;
}

function researchUsage(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const values = value as Record<string, unknown>;
  const usage: Partial<Record<RsResearchUsageField, number>> = {};
  for (const field of researchUsageFields) {
    const count = values[field];
    if (typeof count === "number" && validCount(count) && count <= 1_000_000) usage[field] = count;
  }
  return Object.keys(usage).length ? usage : undefined;
}

export function parseRsAssistantResponse(value: unknown, request: RsAssistantRequest): RsAssistantResponse {
  if (!value || typeof value !== "object") throw new Error("RS_ASSISTANT_INVALID_RESPONSE");
  const result = value as RsAssistantResponse;
  if (typeof result.message !== "string" || !result.message.trim() || result.scope?.chain_id !== request.chain_id || result.scope.period !== request.period
      || ![result.scope.chain, result.scope.format, result.scope.model].every(v => typeof v === "string" && v.trim())) throw new Error("RS_ASSISTANT_INVALID_RESPONSE");
  if (commercialKey(result.scope.model) !== commercialKey(request.model) || formatKey(result.scope.format) !== formatKey(request.format)
      || result.scope.archive_id !== undefined && result.scope.archive_id !== "UNIF-20060057ee8e4dfdbcd9") throw new Error("RS_ASSISTANT_INVALID_RESPONSE");
  const research = result.research;
  const startedAt = researchTimestamp(research?.started_at);
  let finishedAt = research?.finished_at === null ? null : researchTimestamp(research?.finished_at);
  if (startedAt && finishedAt && Date.parse(finishedAt) < Date.parse(startedAt)) finishedAt = undefined;
  const metricKeys = Object.keys(rsMetrics) as (keyof typeof rsMetrics)[];
  const metrics = result.metrics;
  if (!Array.isArray(metrics?.by_month) || metrics.by_month.some(row => !row || typeof row.period !== "string" || metricKeys.some(key => row[key] !== undefined && !validCoverage(row[key]))
      || row.fill_rate && (!validPieces(row.fill_rate.ratio) || !validCount(row.fill_rate.paired_products) || !validCount(row.fill_rate.products_expected) || row.fill_rate.paired_products > row.fill_rate.products_expected))
      || metrics.inventory && !validCoverage(metrics.inventory)) throw new Error("RS_ASSISTANT_INVALID_RESPONSE");
  const forecast = result.forecast;
  if (forecast && (forecast.status !== "PROVISIONAL" || typeof forecast.engine !== "string" || !Array.isArray(forecast.periods) || forecast.periods.some(row => !row || typeof row.period !== "string"
    || !validCandidate(row.statistical) || !validCandidate(row.ml) || !validCandidate(row.ensemble_value)))) throw new Error("RS_ASSISTANT_INVALID_RESPONSE");
  return {
    message: result.message,
    uncertainty: typeof result.uncertainty === "string" ? result.uncertainty : undefined,
    scope: result.scope, metrics, quality: result.quality, forecast,
    research: research && typeof research.status === "string" ? {
      status: research.status,
      summary: typeof research.summary === "string" ? research.summary : undefined,
      model: typeof research.model === "string" ? research.model : undefined,
      reason: typeof research.reason === "string" ? research.reason : undefined,
      started_at: startedAt,
      finished_at: finishedAt,
      usage: researchUsage(research.usage),
      evidence_scope: research.evidence_scope === "PUBLIC_CATEGORY_CONTEXT" ? research.evidence_scope : undefined,
      source_publication_dates_verified: typeof research.source_publication_dates_verified === "boolean" ? research.source_publication_dates_verified : undefined,
      sources: Array.isArray(research.sources) ? research.sources.flatMap(source => {
        if (!source || typeof source.title !== "string" || typeof source.url !== "string") return [];
        const url = safeResearchUrl(source.url);
        return url ? [{ title: source.title, url }] : [];
      }) : [],
    } : undefined,
  };
}
