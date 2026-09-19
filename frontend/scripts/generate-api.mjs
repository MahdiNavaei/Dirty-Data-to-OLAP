import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(process.cwd(), "..");
const output = resolve(process.env.DDO_OPENAPI_OUTPUT ?? resolve(process.cwd(), "openapi.json"));
const generated = resolve(process.env.DDO_GENERATED_TYPES_OUTPUT ?? resolve(process.cwd(), "src/api/generated.ts"));
const pinnedPython = readFileSync(resolve(root, ".python-version"), "utf8").trim();
const localPython = process.platform === "win32" ? resolve(root, ".venv/Scripts/python.exe") : resolve(root, ".venv/bin/python");
const candidates = process.env.DDO_PYTHON ? [[process.env.DDO_PYTHON, []]] : [[localPython, []], ["python3", []], ["python", []]];

function run(command, args) {
  const result = spawnSync(command, args, { cwd: process.cwd(), stdio: "inherit", timeout: 360_000, windowsHide: true });
  if (result.error) {
    if (result.error.code === "ETIMEDOUT") console.error("OpenAPI generation timed out after 360 seconds.");
    return false;
  }
  if (result.status !== 0) process.exit(result.status ?? 1);
  return true;
}

let selected = false;
for (const [command, prefix] of candidates) {
  const probe = spawnSync(command, ["--version"], { stdio: "pipe", encoding: "utf8", timeout: 20_000, windowsHide: true });
  const version = `${probe.stdout ?? ""}${probe.stderr ?? ""}`.trim();
  if (!probe.error && probe.status === 0 && version.includes(`Python ${pinnedPython}`)) {
    selected = run(command, [...prefix, resolve(root, "tools/generate_step29_openapi.py"), "--output", output]);
    break;
  }
}
if (!selected) {
  console.error(`No Python ${pinnedPython} interpreter was found. Run the project-local bootstrap or set DDO_PYTHON to the pinned interpreter.`);
  process.exit(1);
}

if (!run(process.execPath, [resolve(process.cwd(), "node_modules/openapi-typescript/bin/cli.js"), output, "-o", generated])) process.exit(1);
