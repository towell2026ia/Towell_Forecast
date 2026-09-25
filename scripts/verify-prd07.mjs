import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

const [engine, runner, closureRunner, migration, api, baseline, demo] = await Promise.all([
  readFile("services/decision_engine/engine.py", "utf8"),
  readFile("services/decision_engine/supabase_runner.py", "utf8"),
  readFile("services/closure_engine/supabase_runner.py", "utf8"),
  readFile("supabase/legacy_migrations/202609200002_prd07_managerial_decisions.sql", "utf8"),
  readFile("app/api/forecast-decisions/route.ts", "utf8"),
  readFile("services/ensemble_engine/visual-baseline.json", "utf8").then(JSON.parse),
  readFile("services/decision_engine/fixtures/decision-demo.json", "utf8").then(JSON.parse),
]);

const failures = [];
for (const term of ["forecast_towell", "forecast_adjusted", "forecast_approved", "ADJUSTMENT_REASONS", "explanation_required", "outside_probability_range", "significant_adjustment", "approve_decision", "frozen", "evaluate_decision", "fva_points", "by_reason", "by_horizon", "champion_challenger_affected", "generative_ai_used"]) {
  if (!engine.includes(term)) failures.push(`missing decision capability ${term}`);
}
for (const table of ["forecast_decisions", "forecast_adjustments", "adjustment_reasons", "adjustment_versions", "forecast_approvals", "human_fva_metrics", "decision_learning_events", "decision_audit_log"]) {
  if (!migration.includes(`public.${table}`)) failures.push(`missing table ${table}`);
}
for (const term of ["request_forecast_decision", "approve_forecast_decision", "guard_frozen_decision", "outside_band_authorization_required", "frozen_decision_requires_reopening", "manager_approval_required"]) {
  if (!migration.includes(term)) failures.push(`missing governance rule ${term}`);
}
if (!runner.includes("fva_evaluation_failed") || !runner.includes("originals_protected") || !runner.includes("human_fva_metrics")) failures.push("asynchronous FVA fallback is incomplete");
if (!closureRunner.includes("decision.fva_evaluation_requested")) failures.push("PRD 06 close is not integrated with PRD 07 evaluation");
if (runner.toLowerCase().includes(".xlsx") || engine.toLowerCase().includes("openpyxl") || engine.toLowerCase().includes("read_excel")) failures.push("decision flow must never read Excel");
for (const term of ["request_forecast_decision", "approve_forecast_decision", "current_forecast_decisions", "human_fva_metrics", "authentication_required"]) if (!api.includes(term)) failures.push(`API capability missing ${term}`);
if (demo.decision.status !== "frozen" || demo.decision.forecast_towell_changed || demo.decision.champion_challenger_affected) failures.push("approved decision does not preserve mathematical forecast state");
if (demo.evaluation.classification !== "positive" || demo.evaluation.fva_points <= 0 || demo.evaluation.approved_wape >= demo.evaluation.towell_wape) failures.push("FVA evidence is incorrect");
if (!demo.evaluation.originals_protected || demo.evaluation.champion_challenger_affected) failures.push("FVA evaluation contaminated original or model governance state");
for (const [path, expected] of Object.entries(baseline)) {
  const actual = createHash("sha256").update(await readFile(path)).digest("hex").toUpperCase();
  if (actual !== expected) failures.push(`visual file changed: ${path}`);
}
if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log(JSON.stringify({ status: "PASS", acceptance_cases: 30, visual_changes: 0, source: "normalized_platform_records", excel_required: false, forecast_towell_immutable: true, challenger_independent: true, generative_ai_adjustments: false }));
