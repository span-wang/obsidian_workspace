import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const REQUIRED = {
  nodeVersion: "24.18.0",
  pythonVersion: "3.11.15",
  sqliteVersion: "3.45.1",
  uvVersion: "0.11.29"
};

function isAtLeast(actual, minimum) {
  const actualParts = actual.split(".").map(Number);
  const minimumParts = minimum.split(".").map(Number);

  for (let index = 0; index < minimumParts.length; index += 1) {
    if (actualParts[index] > minimumParts[index]) return true;
    if (actualParts[index] < minimumParts[index]) return false;
  }

  return true;
}

export function validateEnvironment(environment) {
  const errors = [];

  if (environment.nodeVersion !== REQUIRED.nodeVersion) {
    errors.push(
      `Node.js ${REQUIRED.nodeVersion} is required; found ${environment.nodeVersion}.`
    );
  }
  if (environment.pythonVersion !== REQUIRED.pythonVersion) {
    errors.push(
      `CPython ${REQUIRED.pythonVersion} is required; found ${environment.pythonVersion}.`
    );
  }
  if (!isAtLeast(environment.sqliteVersion, REQUIRED.sqliteVersion)) {
    errors.push(
      `SQLite ${REQUIRED.sqliteVersion} or newer is required; found ${environment.sqliteVersion}.`
    );
  }
  if (environment.uvVersion !== REQUIRED.uvVersion) {
    errors.push(`uv ${REQUIRED.uvVersion} is required; found ${environment.uvVersion}.`);
  }

  return errors;
}

function commandOutput(command, args) {
  return execFileSync(command, args, { encoding: "utf8" }).trim();
}

function inspectEnvironment() {
  const [pythonVersion, sqliteVersion] = commandOutput("python", [
    "-c",
    "import sqlite3, sys; print('.'.join(map(str, sys.version_info[:3]))); print(sqlite3.sqlite_version)"
  ]).split(/\r?\n/);

  return {
    nodeVersion: process.versions.node,
    pythonVersion,
    sqliteVersion,
    uvVersion: commandOutput("uv", ["--version"]).split(/\s+/)[1]
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const environment = inspectEnvironment();
  const errors = validateEnvironment(environment);

  if (errors.length > 0) {
    console.error(errors.join("\n"));
    process.exitCode = 1;
  } else {
    console.log("Runtime preflight passed.");
  }
}
