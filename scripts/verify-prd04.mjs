import { readFile } from "node:fs/promises";

const [engine, runner, migration, ui, dashboard, demo] = await Promise.all([
  readFile("services/ml_engine/engine.py", "utf8"),
  readFile("services/ml_engine/supabase_runner.py", "utf8"),
  readFile("supabase/legacy_migrations/202609190001_prd04_ml_engine.sql", "utf8"),
  readFile("app/ml-engine-view.tsx", "utf8"),
  readFile("app/executive-dashboard.tsx", "utf8"),
  readFile("app/data/ml-demo.json", "utf8").then(JSON.parse),
]);
const failures = [];
for (const model of ["Regresión Lineal Global", "Random Forest Global", "Gradient Boosting Global", "XGBoost Global", "LightGBM Global"]) if (!engine.includes(model)) failures.push(`missing model ${model}`);
for (const feature of ["for lag in (1, 2, 3, 6, 12)", "for window in (3, 6, 12)", "meses desde última venta", "porcentaje con movimiento"]) if (!engine.includes(feature)) failures.push(`missing feature ${feature}`);
for (const table of ["ml_training_runs", "ml_model_versions", "ml_horizon_metrics", "ml_predictions", "ml_feature_importance", "ml_alerts", "ml_series_state", "ml_training_log"]) if (!migration.includes(`public.${table}`)) failures.push(`missing table ${table}`);
for (const copy of ["Motor Machine Learning", "Champion protegido", "WAPE por horizonte", "Factores relevantes", "Champion vs. Challenger", "Forecast ML · 12 meses"]) if (!ui.includes(copy)) failures.push(`missing UI ${copy}`);
if (!migration.includes("ml_one_champion_per_target")) failures.push("single Champion protection missing");
if (!migration.includes("guard_published_ml_version")) failures.push("published model immutability missing");
if (!migration.includes("request_ml_training")) failures.push("training request RPC missing");
if (!runner.includes("ml_training_observations") || runner.toLowerCase().includes(".xlsx")) failures.push("runner must consume the database, never Excel");
if (!runner.includes('initial_state = "challenger" if current else "evaluating"')) failures.push("Champion/Challenger workflow missing");
if (!runner.includes("run_id[:8]")) failures.push("unique operational version labels missing");
if (!dashboard.includes("Comparar motores") || !dashboard.includes("Motor ML")) failures.push("executive comparison missing");
if (demo.forecast.length < 2 || demo.forecast.some((row) => row.forecast.length !== 12)) failures.push("all demo forecasts must cover 12 months");
if (demo.champion.status !== "Champion" || demo.challenger.status !== "Challenger") failures.push("Champion/Challenger demo missing");
if (!demo.feature_importance.length || demo.champion.by_horizon.length !== 12) failures.push("model evidence incomplete");
if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
console.log(JSON.stringify({status:"PASS",models:5,series:demo.forecast.length,horizon:12,source:"database-ready",excel_required:false}));
