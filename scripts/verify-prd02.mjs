import { readFile } from "node:fs/promises";

const [sql, ui, importer] = await Promise.all([
  readFile("supabase/legacy_migrations/202609180001_prd02_base.sql", "utf8"),
  readFile("app/forecast-towell-app.tsx", "utf8"),
  readFile("scripts/migrate-prd01-to-supabase.mjs", "utf8"),
]);

const requiredTables = [
  "organizations","chains","formats","products","product_identifiers","product_variants","periods","users","roles",
  "orders","order_lines","sales","deliveries","customer_forecasts","source_files","source_sheets","migration_batches",
  "migration_records","migration_issues","record_versions","approval_records","audit_log","domain_events",
  "forecast_runs","forecast_versions","forecast_values",
];
const requiredModules = ["Inicio","Centro de captura","Histórico","Periodos","Calidad de datos","Usuarios","Auditoría"];
const failures = [];
for (const table of requiredTables) if (!sql.includes(`public.${table}`)) failures.push(`missing table ${table}`);
for (const moduleName of requiredModules) if (!ui.includes(moduleName)) failures.push(`missing module ${moduleName}`);
for (const form of ["Registrar pedido","Registrar venta","Registrar entrega","Registrar Fcst Cliente"]) if (!ui.includes(form)) failures.push(`missing form ${form}`);
if (!sql.includes("delivery_exceeds_pending_balance")) failures.push("delivery balance rule missing");
if (!sql.includes("period_not_editable")) failures.push("period state rule missing");
if (!sql.includes("domain_events")) failures.push("event storage missing");
if (!importer.includes("inventory_files.csv") || importer.toLowerCase().includes("downloads")) failures.push("importer must use PRD01 outputs only");
if (failures.length) { console.error(failures.join("\n")); process.exit(1); }
console.log(JSON.stringify({ status:"PASS", tables:requiredTables.length, modules:requiredModules.length, independentForms:4 }));
