import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { validateEnvironment } from "./preflight.mjs";

test("accepts the required Node, Python, SQLite, and uv versions", () => {
  assert.deepEqual(
    validateEnvironment({
      nodeVersion: "24.18.0",
      pythonVersion: "3.11.15",
      sqliteVersion: "3.53.1",
      uvVersion: "0.11.29"
    }),
    []
  );
});

test("reports every unsupported runtime", () => {
  assert.deepEqual(
    validateEnvironment({
      nodeVersion: "24.12.0",
      pythonVersion: "3.12.0",
      sqliteVersion: "3.44.0",
      uvVersion: "0.10.0"
    }),
    [
      "Node.js 24.18.0 is required; found 24.12.0.",
      "CPython 3.11.15 is required; found 3.12.0.",
      "SQLite 3.45.1 or newer is required; found 3.44.0.",
      "uv 0.11.29 is required; found 0.10.0."
    ]
  );
});

test("prints a clear result when run as a command", () => {
  const result = spawnSync(process.execPath, [fileURLToPath(new URL("./preflight.mjs", import.meta.url))], {
    encoding: "utf8"
  });

  assert.equal(result.status, 0);
  assert.match(result.stdout, /Runtime preflight passed\./);
});

test("runs the runtime check and web build before the documented start command", () => {
  const packageJson = JSON.parse(readFileSync(new URL("../package.json", import.meta.url)));

  assert.equal(packageJson.scripts.prestart, "npm run preflight && npm run build");
});

test("requires the exact CPython patch version used by the service environment", () => {
  const projectConfig = readFileSync(new URL("../apps/service/pyproject.toml", import.meta.url), "utf8");

  assert.match(projectConfig, /requires-python = "==3\.11\.15"/);
});
