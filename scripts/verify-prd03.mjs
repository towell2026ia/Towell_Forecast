import { readFile } from "node:fs/promises";

const [engine, migration, ui, runner, demo] = await Promise.all([
  readFile("services/statistical_engine/engine.py", "utf8"),
  readFile("supabase/legacy_migrations/202609180002_prd03_engine.sql", "utf8"),
  readFile("app/statistical-engine-view.tsx", "utf8"),
  readFile("services/statistical_engine/supabase_runner.py", "utf8"),
  readFile("app/data/forecast-demo.json", "utf8").then(JSON.parse),
]);
const failures = [];
for (const model of ["Naive","Naive estacional","Media móvil 3","Media móvil 6","Media móvil 12","Media móvil ponderada","SES","Holt","Holt-Winters","Tendencia lineal","Tendencia polinómica","Croston","SBA","TSB"]) if (!engine.includes(`"${model}"`)) failures.push(`missing model ${model}`);
for (const table of ["forecast_series","forecast_runs","forecast_results","forecast_model_results","backtest_windows","forecast_values","statistical_alerts"]) if (!migration.includes(`public.${table}`)) failures.push(`missing table ${table}`);
for (const term of ["Motor Estadístico","Real vs. forecast","Comparación de modelos","Horizonte oficial · 12 meses"]) if (!ui.includes(term)) failures.push(`missing UI ${term}`);
if (!migration.includes("guard_frozen_forecast")) failures.push("immutable version guard missing");
if (!migration.includes("forecast_monthly_observations")) failures.push("database source view missing");
if (!runner.includes("forecast_monthly_observations") || runner.toLowerCase().includes(".xlsx")) failures.push("runner must consume database, never Excel");
if (demo.series[0].forecast.length !== 12) failures.push("demo horizon is not 12 months");
if (!demo.series.some((row) => row.target === "Pedido" && row.status === "insufficient")) failures.push("Pedido insufficiency must be explicit");
if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
console.log(JSON.stringify({status:"PASS",models:14,series:demo.series.length,horizon:12,source:"database-ready"}));
