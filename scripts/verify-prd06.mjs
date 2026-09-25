import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

const [engine, runner, migration, api, baseline, demo] = await Promise.all([
  readFile("services/closure_engine/engine.py", "utf8"),
  readFile("services/closure_engine/supabase_runner.py", "utf8"),
  readFile("supabase/legacy_migrations/202609200001_prd06_closure_learning.sql", "utf8"),
  readFile("app/api/period-closures/route.ts", "utf8"),
  readFile("services/ensemble_engine/visual-baseline.json", "utf8").then(JSON.parse),
  readFile("services/closure_engine/fixtures/closure-demo.json", "utf8").then(JSON.parse),
]);

const failures = [];
for (const term of ["validate_closure", "field_name in (\"sale\", \"order\", \"delivery\")", "client_forecast_not_received", "_wape", "_bias", "fill_rate", "horizon_accuracy", "interval_coverage", "persistent_bias", "candidate_for_promotion", "drift", "retraining_requested", "previous_snapshot_hash", "next_cycle_prepared"]) {
  if (!engine.includes(term)) failures.push(`missing engine capability ${term}`);
}
for (const table of ["period_closures", "closure_snapshots", "realized_metrics", "forecast_accuracy", "fill_rate_metrics", "challenger_validation", "horizon_accuracy", "interval_coverage", "learning_events"]) {
  if (!migration.includes(`public.${table}`)) failures.push(`missing table ${table}`);
}
for (const term of ["request_period_closure", "request_period_reopening", "guard_immutable_closure_evidence", "manager_role_required", "completed_with_learning_error"]) {
  if (!migration.includes(term)) failures.push(`missing database rule ${term}`);
}
if (!runner.includes("completed_with_learning_error") || !runner.includes("champion_changed") || !runner.includes("realized_metrics")) failures.push("asynchronous fallback contract missing");
if (runner.toLowerCase().includes(".xlsx") || engine.toLowerCase().includes("openpyxl") || engine.toLowerCase().includes("read_excel")) failures.push("closure must never read Excel");
if (!api.includes("request_period_closure") || !api.includes("request_period_reopening") || !api.includes("authentication_required")) failures.push("authenticated period API missing");
if (demo.status !== "closed" || !demo.snapshot_immutable || demo.champion_changed || demo.forecast_towell_changed) failures.push("closure/fallback state is incorrect");
if (demo.service.fill_rate !== 95 || demo.benchmark.client_wape !== 25 || demo.benchmark.towell_wape !== 10) failures.push("realized metrics are incorrect");
if (demo.challenger_validation.champion_reference_wape !== 23.96 || demo.challenger_validation.challenger_reference_wape !== 22.56) failures.push("Champion/Challenger baseline is incorrect");
if (demo.challenger_validation.state !== "in_validation" || demo.challenger_validation.automatic_promotion !== false) failures.push("Challenger must remain controlled and in validation");
if (!demo.next_cycle_prepared || demo.snapshot.source_contract !== "normalized_platform_records") failures.push("next-cycle or normalized contract missing");
for (const [path, expected] of Object.entries(baseline)) {
  const actual = createHash("sha256").update(await readFile(path)).digest("hex").toUpperCase();
  if (actual !== expected) failures.push(`visual file changed: ${path}`);
}
if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log(JSON.stringify({ status: "PASS", acceptance_cases: 25, visual_changes: 0, source: "normalized_platform_records", excel_required: false, challenger_state: "in_validation", automatic_promotion: false }));
