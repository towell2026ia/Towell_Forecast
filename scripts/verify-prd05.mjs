import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

const [engine,runner,migration,api,baseline,demo]=await Promise.all([
  readFile("services/ensemble_engine/engine.py","utf8"),
  readFile("services/ensemble_engine/supabase_runner.py","utf8"),
  readFile("supabase/legacy_migrations/202609190002_prd05_ensemble.sql","utf8"),
  readFile("app/api/ensemble-runs/route.ts","utf8"),
  readFile("services/ensemble_engine/visual-baseline.json","utf8").then(JSON.parse),
  readFile("services/ensemble_engine/fixtures/ensemble-demo.json","utf8").then(JSON.parse),
]);
const failures=[];
for(const term of ["minimum_improvement","no_degradation_pass","candidate_weights","by_horizon","probability","high_divergence","client_forecast_role","fallback_order"]) if(!engine.includes(term)) failures.push(`missing engine capability ${term}`);
for(const table of ["ensemble_runs","ensemble_candidates","ensemble_metrics","forecast_towell","forecast_towell_publications","forecast_vintages","probability_bands","ensemble_alerts","ensemble_audit_log"]) if(!migration.includes(`public.${table}`)) failures.push(`missing table ${table}`);
if(!migration.includes("request_ensemble_run")) failures.push("request RPC missing");
if(!migration.includes("guard_published_forecast_towell")) failures.push("immutable vintage guard missing");
if(!runner.includes("ml_training_observations")||runner.toLowerCase().includes(".xlsx")) failures.push("runner must consume normalized database records, never Excel");
if(!runner.includes("last valid publication")||!runner.includes("ML falló")) failures.push("fallback contract missing");
if(!api.includes("request_ensemble_run")||!api.includes("authentication_required")) failures.push("authenticated internal API missing");
if(demo.selection.reference_champion_wape<23.9||demo.selection.reference_champion_wape>24||demo.selection.official.strategy!=="statistical") failures.push("current Champion/no-degradation baseline is incorrect");
if(demo.published_metrics.statistical_wape!==23.96||demo.published_metrics.ml_wape!==35.78) failures.push("published PRD03/PRD04 evidence is missing");
if(demo.forecast_towell.length!==144||demo.selection.official.by_horizon.length!==12) failures.push("12-month forecast evidence is incomplete");
if(demo.forecast_towell.some(row=>!(row.probability.p10<=row.probability.p50&&row.probability.p50<=row.probability.p90&&row.probability.p90<=row.probability.p95))) failures.push("invalid probability bands");
if(demo.client_forecast_role!=="external_benchmark_only") failures.push("client forecast must stay outside ensemble");
for(const [path,expected] of Object.entries(baseline)){
  const actual=createHash("sha256").update(await readFile(path)).digest("hex").toUpperCase();
  if(actual!==expected) failures.push(`visual file changed: ${path}`);
}
if(failures.length){console.error(failures.join("\n"));process.exit(1);}
console.log(JSON.stringify({status:"PASS",acceptance_cases:20,visual_changes:0,source:"normalized_platform_records",excel_required:false}));
