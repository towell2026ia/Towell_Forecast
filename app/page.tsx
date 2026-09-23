import ForecastTowellApp from "./forecast-towell-app";
import { getChatGPTUser } from "./chatgpt-auth";

export const dynamic = "force-dynamic";

export default async function Home() {
  const supabaseConfigured = Boolean(
    process.env.SUPABASE_URL && process.env.SUPABASE_SERVICE_ROLE_KEY,
  );
  const local = (process.env.APP_ENV ?? process.env.NODE_ENV) === "development";
  const user = local ? null : await getChatGPTUser();
  const managers = new Set((process.env.ASSISTANT_MANAGER_IDS ?? "").split(",").map((id) => id.trim()));
  const currentRole = local ? (process.env.CURRENT_USER_ROLE ?? "manager") :
    (user && managers.has(user.userId) ? "manager" : "reader");
  const backendConfigured = Boolean(process.env.PYTHON_ASSISTANT_URL &&
    process.env.ASSISTANT_API_TOKEN && process.env.ASSISTANT_MANAGER_IDS);
  const assistantConfig = {
    authorized: currentRole === "manager",
    uiEnabled: process.env.AI_ASSISTANT_UI_ENABLED !== "false",
    apiEnabled: process.env.AI_ASSISTANT_API_ENABLED !== "false" && (local || backendConfigured),
    voiceEnabled: false,
    mode: "local" as const,
  };
  return <ForecastTowellApp supabaseConfigured={supabaseConfigured} assistantConfig={assistantConfig} />;
}
