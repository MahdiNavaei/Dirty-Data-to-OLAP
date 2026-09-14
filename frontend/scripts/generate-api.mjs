import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const root = resolve(process.cwd(), "..");
const output = resolve(process.cwd(), "openapi.json");
const generated = resolve(process.cwd(), "src/api/generated.ts");
const candidates = process.platform === "win32" ? ["python"] : ["python3", "python"];

function run(command, args) {
  const result = spawnSync(command, args, { cwd: process.cwd(), stdio: "inherit" });
  if (result.error) return false;
  if (result.status !== 0) process.exit(result.status ?? 1);
  return true;
}

let selected = false;
for (const command of candidates) {
  const probe = spawnSync(command, ["--version"], { stdio: "ignore" });
  if (!probe.error && probe.status === 0) {
    selected = run(command, [resolve(root, "tools/generate_step29_openapi.py"), "--output", output]);
    break;
  }
}
if (!selected) {
  console.error("No supported Python interpreter was found for OpenAPI generation.");
  process.exit(1);
}

if (!run(process.execPath, [resolve(process.cwd(), "node_modules/openapi-typescript/bin/cli.js"), output, "-o", generated])) process.exit(1);
