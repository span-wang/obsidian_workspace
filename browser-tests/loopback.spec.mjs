import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:net";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const serviceRoot = fileURLToPath(new URL("../apps/service/", import.meta.url));
const servicePython = join(serviceRoot, ".venv", "Scripts", "python.exe");
const serviceName = "obsidian-personal-knowledge-platform";
let service;

function assertLoopbackPortAvailable() {
  return new Promise((resolve, reject) => {
    const candidateServer = createServer();
    candidateServer.once("error", (error) => {
      reject(new Error(`Browser tests require an unused 127.0.0.1:6240: ${error.message}`));
    });
    candidateServer.listen(6240, "127.0.0.1", () => {
      candidateServer.close(resolve);
    });
  });
}

async function waitForHealth() {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch("http://127.0.0.1:6240/api/health", {
        signal: AbortSignal.timeout(500)
      });
      const payload = await response.json();
      if (service.exitCode !== null) throw new Error("Spawned service exited before health check.");
      if (response.ok && payload.service === serviceName) return;
    } catch {
      // The server has not bound the fixed endpoint yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Service did not become healthy within 10 seconds.");
}

test.beforeAll(async ({}, testInfo) => {
  await assertLoopbackPortAvailable();
  service = spawn(servicePython, ["-m", "api.main", "--no-browser"], {
    cwd: serviceRoot,
    stdio: "pipe",
    env: {
      ...process.env,
      OBSIDIAN_PLATFORM_DATA_DIR: testInfo.outputPath("app-data")
    }
  });
  await waitForHealth();
});

test.afterAll(async () => {
  if (service?.exitCode === null) {
    service.kill();
    await once(service, "exit");
  }
});

test("serves the workbench and its API requests from the fixed loopback origin", async ({ page }) => {
  const healthRequests = [];
  const sessionRequests = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/health")) healthRequests.push(request.url());
    if (request.url().endsWith("/api/session")) sessionRequests.push(request.url());
  });

  await page.goto("/");

  await expect(page).toHaveURL("http://127.0.0.1:6240/");
  await expect(page.getByRole("heading")).toHaveText("工作台");
  await expect(page.getByTestId("health-status")).toHaveText("本机服务可用");
  await expect(page.getByTestId("session-status")).toHaveText("本机会话已建立");
  await expect(page.getByRole("navigation").getByRole("link")).toHaveCount(5);
  expect(healthRequests).toEqual(["http://127.0.0.1:6240/api/health"]);
  expect(sessionRequests).toEqual(["http://127.0.0.1:6240/api/session"]);
});

test("uses a keyboard-accessible single navigation panel at narrow desktop widths", async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 800 });
  await page.goto("/");

  const menuButton = page.getByRole("button", { name: "打开导航" });
  await expect(menuButton).toBeVisible();
  await menuButton.focus();
  await menuButton.click();

  const navigationPanel = page.getByRole("dialog", { name: "主导航" });
  await expect(navigationPanel).toBeVisible();
  await expect(navigationPanel.getByRole("link", { name: "工作台" })).toBeFocused();
  await page.keyboard.press("Escape");

  await expect(navigationPanel).toBeHidden();
  await expect(menuButton).toBeFocused();
});
