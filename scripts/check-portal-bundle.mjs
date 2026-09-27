import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
const files = root => readdirSync(root, { withFileTypes: true }).flatMap(e => e.isDirectory() ? files(join(root, e.name)) : /\.js$/.test(e.name) ? [join(root, e.name)] : []);
const assets = files("dist/client");
if (!assets.length) throw new Error("Build client assets before checking exposure.");
const forbidden = /SUPABASE_SERVICE_ROLE_KEY|ASSISTANT_API_TOKEN|ASSISTANT_MANAGER_IDS|capture_operational_record/;
for (const path of assets) if (forbidden.test(readFileSync(path, "utf8"))) throw new Error(`Privileged browser reference: ${path}`);
console.log(`portal_browser_bundle: PASS (${assets.length} assets; privileged configuration references 0)`);
