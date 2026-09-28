import { execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const supplied = process.env.COMMIT_REF;
const sha = supplied || execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
if (!/^[0-9a-f]{40}$/.test(sha)) throw new Error("A full Git SHA is required for release provenance.");
const cli = fileURLToPath(new URL("../node_modules/next/dist/bin/next", import.meta.url));
const result = spawnSync(process.execPath, [cli, "build", "--webpack"], {
  stdio: "inherit", env: { ...process.env, NEXT_PUBLIC_GIT_SHA: sha },
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
