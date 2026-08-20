import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdir, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const serviceRoot = fileURLToPath(new URL("../apps/service/", import.meta.url));
const servicePython = join(serviceRoot, ".venv", "Scripts", "python.exe");
const serviceName = "obsidian-personal-knowledge-platform";
const testPort = Number(process.env.OBSIDIAN_PLATFORM_TEST_PORT || "6240");
const baseUrl = `http://127.0.0.1:${testPort}`;
let service;

function resultEventStream(result) {
  return {
    contentType: "text/event-stream",
    body: `event: result\ndata: ${JSON.stringify({ result })}\n\n`
  };
}

function assertLoopbackPortAvailable() {
  return new Promise((resolve, reject) => {
    const candidateServer = createServer();
    candidateServer.once("error", (error) => {
      reject(new Error(`Browser tests require an unused 127.0.0.1:${testPort}: ${error.message}`));
    });
    candidateServer.listen(testPort, "127.0.0.1", () => {
      candidateServer.close(resolve);
    });
  });
}

async function waitForHealth() {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/api/health`, {
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
  service = spawn(servicePython, ["-m", "uvicorn", "api.main:create_app", "--factory", "--host", "127.0.0.1", "--port", String(testPort)], {
    cwd: serviceRoot,
    stdio: "pipe",
    env: {
      ...process.env,
      OBSIDIAN_PLATFORM_DATA_DIR: testInfo.outputPath("app-data"),
      OBSIDIAN_PLATFORM_RETRIEVAL_TEST_UI: "true"
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

  await expect(page).toHaveURL(`${baseUrl}/`);
  await expect(page.getByRole("heading")).toHaveText("工作台");
  await expect(page.getByTestId("health-status")).toHaveText("本机服务可用");
  await expect(page.getByTestId("session-status")).toHaveText("本机会话已建立");
  await expect(page.getByRole("navigation").getByRole("link")).toHaveCount(5);
  expect(healthRequests).toEqual([`${baseUrl}/api/health`]);
  expect(sessionRequests).toEqual([`${baseUrl}/api/session`]);
});

test("previews synthetic Markdown in the isolated retrieval chunking lab", async ({ page }) => {
  const previewRequests = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/_test/retrieval/chunk-preview")) {
      previewRequests.push(request.url());
    }
  });

  await page.goto("/_test/retrieval-chunking");

  await expect(page).toHaveURL(`${baseUrl}/_test/retrieval-chunking`);
  await expect(page.getByRole("heading", { name: "检索分块实验台" })).toBeVisible();
  await expect(page.getByText("不会读取 vault 或 SQLite，也不会访问外网；关闭服务开关后该页面不可用。")).toBeVisible();
  await page.getByLabel("Markdown 输入").fill("# 测试单元\n\n这是用于本机预览的合成段落。");
  await page.getByRole("button", { name: "生成分块预览" }).click();

  await expect(page.getByText(/已生成 \d+ 个分块。/)).toBeVisible();
  await expect(page.getByRole("article").first()).toBeVisible();
  await expect(page.getByLabel("分块结果")).toContainText("测试单元");
  expect(previewRequests).toEqual([`${baseUrl}/api/_test/retrieval/chunk-preview`]);
});

test("shows the all-vault overview without a user graph drawer", async ({ page }) => {
  const firstVault = {
    vault_id: "vault-graph-first",
    path: "C:\\fixture\\First Graph Vault",
    authorization_status: "active",
    access_status: "available",
    is_current: true,
    index: { status: "stale", current_count: 2, stale_count: 1, failure_count: 0, pending_count: 0, failed_paths: [], stale_paths: ["notes/old.md"], semantic_status: "unavailable" }
  };
  const secondVault = { ...firstVault, vault_id: "vault-graph-second", path: "C:\\fixture\\Second Graph Vault", is_current: false };
  const overview = {
    updated_at: "2026-08-07T09:30:00+00:00",
    vaults: [
      {
        vault_id: firstVault.vault_id,
        display_name: "First Graph Vault",
        authorization_status: "active",
        access_status: "available",
        access_reason: null,
        is_current: true,
        updated_at: "2026-08-07T09:20:00+00:00",
        state: "attention",
        index: { ...firstVault.index, updated_at: "2026-08-07T09:20:00+00:00", semantic_covered_block_count: 0, semantic_eligible_block_count: 0 },
        tasks: { total: 0, running: 0, attention: 0, completed: 0, latest_at: null },
        sessions: { total: 0, latest_at: null }
      },
      {
        vault_id: secondVault.vault_id,
        display_name: "Second Graph Vault",
        authorization_status: "active",
        access_status: "available",
        access_reason: null,
        is_current: false,
        updated_at: "2026-08-07T09:20:00+00:00",
        state: "healthy",
        index: { ...secondVault.index, updated_at: "2026-08-07T09:20:00+00:00", semantic_covered_block_count: 0, semantic_eligible_block_count: 0 },
        tasks: { total: 0, running: 0, attention: 0, completed: 0, latest_at: null },
        sessions: { total: 0, latest_at: null }
      }
    ],
    attention: [],
    activity: []
  };
  const workbenchRequests = [];
  const vaultRequests = [];

  await page.route("**/api/workbench/overview", async (route) => {
    workbenchRequests.push(route.request().url());
    await route.fulfill({ json: overview });
  });
  await page.route("**/api/vaults", async (route) => {
    vaultRequests.push(route.request().url());
    await route.fulfill({ json: { vaults: [firstVault, secondVault] } });
  });

  await page.goto("/");
  await expect(page.getByRole("region", { name: "Vault" })).toBeVisible();
  await expect(page.getByRole("button", { name: "First Graph Vault 当前工作上下文" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Second Graph Vault 已授权资料库" })).toBeVisible();
  expect(workbenchRequests).toEqual([`${baseUrl}/api/workbench/overview`]);
  expect(vaultRequests).toEqual([]);

  await page.getByRole("link", { name: "资料" }).click();
  await expect(page.getByRole("button", { name: "First Graph Vault" })).toBeVisible();
  expect(vaultRequests).toEqual([`${baseUrl}/api/vaults`]);

  await page.getByRole("link", { name: "工作台" }).click();

  await page.getByRole("button", { name: "First Graph Vault 当前工作上下文" }).click();
  const drawer = page.getByRole("dialog", { name: "First Graph Vault详情" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("tab", { name: "图谱" })).toHaveCount(0);

  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
});

test("does not expose a user graph when the selected drawer changes", async ({ page }) => {
  const firstVault = {
    vault_id: "vault-race-first",
    path: "C:\\fixture\\First Race Vault",
    authorization_status: "active",
    access_status: "available",
    is_current: true,
    index: { status: "healthy", current_count: 1, stale_count: 0, failure_count: 0, pending_count: 0, failed_paths: [], stale_paths: [], semantic_status: "unavailable" }
  };
  const secondVault = { ...firstVault, vault_id: "vault-race-second", path: "C:\\fixture\\Second Race Vault", is_current: false };
  const overview = {
    updated_at: "2026-08-07T09:30:00+00:00",
    vaults: [
      {
        vault_id: firstVault.vault_id,
        display_name: "First Race Vault",
        authorization_status: "active",
        access_status: "available",
        access_reason: null,
        is_current: true,
        updated_at: "2026-08-07T09:20:00+00:00",
        state: "healthy",
        index: { ...firstVault.index, updated_at: "2026-08-07T09:20:00+00:00", semantic_covered_block_count: 0, semantic_eligible_block_count: 0 },
        tasks: { total: 0, running: 0, attention: 0, completed: 0, latest_at: null },
        sessions: { total: 0, latest_at: null }
      },
      {
        vault_id: secondVault.vault_id,
        display_name: "Second Race Vault",
        authorization_status: "active",
        access_status: "available",
        access_reason: null,
        is_current: false,
        updated_at: "2026-08-07T09:20:00+00:00",
        state: "healthy",
        index: { ...secondVault.index, updated_at: "2026-08-07T09:20:00+00:00", semantic_covered_block_count: 0, semantic_eligible_block_count: 0 },
        tasks: { total: 0, running: 0, attention: 0, completed: 0, latest_at: null },
        sessions: { total: 0, latest_at: null }
      }
    ],
    attention: [],
    activity: []
  };
  await page.route("**/api/workbench/overview", async (route) => {
    await route.fulfill({ json: overview });
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [firstVault, secondVault] } });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "First Race Vault 当前工作上下文" }).click();
  const firstDrawer = page.getByRole("dialog", { name: "First Race Vault详情" });
  await expect(firstDrawer.getByRole("tab", { name: "图谱" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(firstDrawer).toBeHidden();

  await page.getByRole("button", { name: "Second Race Vault 已授权资料库" }).click();
  const secondDrawer = page.getByRole("dialog", { name: "Second Race Vault详情" });
  await expect(secondDrawer.getByRole("tab", { name: "图谱" })).toHaveCount(0);
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

test("adds a vault from the materials workspace and closes removal confirmation with Escape", async ({ page }) => {
  const vault = {
    vault_id: "vault-test",
    path: "C:\\fixture\\English Vault",
    managed_root_relative_path: "platform",
    managed_root: "C:\\fixture\\English Vault\\platform",
    source_directory: "C:\\fixture\\English Vault\\platform\\sources",
    note_directory: "C:\\fixture\\English Vault\\platform\\notes",
    authorization_status: "active",
    access_status: "available",
    index_status: "not-initialized",
    created_at: "2026-07-21T00:00:00+00:00",
    updated_at: "2026-07-21T00:00:00+00:00",
    is_current: true,
    recovery_actions: []
  };

  await page.route("**/api/vaults/select-directory", async (route) => {
    await route.fulfill({ json: { selection_id: "selection-test", label: "English Vault" } });
  });
  await page.route("**/api/vaults", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { vaults: [] } });
      return;
    }
    await route.fulfill({ json: { vault } });
  });

  await page.goto("/");
  await page.getByRole("link", { name: "资料" }).click();
  await page.getByRole("button", { name: "添加 vault" }).click();
  await page.getByRole("button", { name: "选择本机路径" }).click();
  await expect(page.getByText("English Vault")).toBeVisible();
  await page.getByRole("button", { name: "授权 vault" }).click();

  await expect(page.getByRole("heading", { name: "English Vault" })).toBeVisible();
  await page.getByRole("link", { name: "设置" }).click();
  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  await page.getByRole("button", { name: /English Vault/ }).click();
  const removeButton = page.getByRole("button", { name: "移除授权" });
  await removeButton.focus();
  await removeButton.click();
  await expect(page.getByRole("dialog", { name: "移除 vault 授权" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "移除 vault 授权" })).toBeHidden();
  await expect(removeButton).toBeFocused();
});

test("keeps exactly one current vault after switching", async ({ page }) => {
  const firstVault = {
    vault_id: "vault-first",
    path: "C:\\fixture\\First Vault",
    managed_root_relative_path: "platform",
    managed_root: "C:\\fixture\\First Vault\\platform",
    source_directory: "C:\\fixture\\First Vault\\platform\\sources",
    note_directory: "C:\\fixture\\First Vault\\platform\\notes",
    authorization_status: "active",
    access_status: "available",
    index_status: "not-initialized",
    created_at: "2026-07-21T00:00:00+00:00",
    updated_at: "2026-07-21T00:00:00+00:00",
    is_current: true,
    recovery_actions: []
  };
  const secondVault = {
    ...firstVault,
    vault_id: "vault-second",
    path: "C:\\fixture\\Second Vault",
    managed_root: "C:\\fixture\\Second Vault\\platform",
    source_directory: "C:\\fixture\\Second Vault\\platform\\sources",
    note_directory: "C:\\fixture\\Second Vault\\platform\\notes",
    is_current: false
  };

  await page.route("**/api/vaults/vault-second/current", async (route) => {
    await route.fulfill({ json: { vault: { ...secondVault, is_current: true } } });
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [firstVault, secondVault] } });
  });

  await page.goto("/");
  await page.getByRole("link", { name: "资料" }).click();
  await page.getByRole("button", { name: /Second Vault/ }).click();
  await page.getByRole("button", { name: "设为当前 vault" }).click();

  await expect(page.locator(".context-vault-marker")).toHaveText("Second Vault");
  await page.getByRole("button", { name: "返回 vault 列表" }).click();
  await expect(page.locator(".vault-list .row-status").filter({ hasText: "当前" })).toHaveCount(0);
});

test("cancels an open vault form when navigating to another workspace", async ({ page }) => {
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [] } });
  });

  await page.goto("/");
  await page.getByRole("link", { name: "资料" }).click();
  await page.getByRole("button", { name: "添加 vault" }).click();
  await expect(page.getByRole("form", { name: "添加 vault" })).toBeVisible();
  await page.getByRole("link", { name: "设置" }).click();

  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  await expect(page.getByRole("form", { name: "添加 vault" })).toBeHidden();
});

test("keeps a failed removal confirmation open and shows its error", async ({ page }) => {
  const vault = {
    vault_id: "vault-failure",
    path: "C:\\fixture\\Failure Vault",
    managed_root_relative_path: "platform",
    managed_root: "C:\\fixture\\Failure Vault\\platform",
    source_directory: "C:\\fixture\\Failure Vault\\platform\\sources",
    note_directory: "C:\\fixture\\Failure Vault\\platform\\notes",
    authorization_status: "active",
    access_status: "available",
    index_status: "not-initialized",
    created_at: "2026-07-21T00:00:00+00:00",
    updated_at: "2026-07-21T00:00:00+00:00",
    is_current: true,
    recovery_actions: []
  };

  await page.route("**/api/vaults/vault-failure", async (route) => {
    await route.fulfill({
      status: 500,
      json: {
        code: "vault_operation_failed",
        message: "移除授权未完成。",
        details: {},
        retryable: true
      }
    });
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [vault] } });
  });

  await page.goto("/");
  await page.getByRole("link", { name: "资料" }).click();
  await page.getByRole("button", { name: /Failure Vault/ }).click();
  await page.getByRole("button", { name: "移除授权" }).click();
  const dialog = page.getByRole("dialog", { name: "移除 vault 授权" });
  await dialog.getByRole("button", { name: "移除授权" }).click();

  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("alert")).toHaveText("移除授权未完成。");
});

test("edits vault exclusions under the default outbound policy", async ({ page }) => {
  let policy = {
    outbound_mode: "always-allow",
    policy_revision: 1,
    updated_at: "2026-07-21T00:00:00+00:00",
    rules: []
  };
  const vault = {
    vault_id: "vault-policy",
    path: "C:\\fixture\\Policy Vault",
    managed_root_relative_path: "platform",
    managed_root: "C:\\fixture\\Policy Vault\\platform",
    source_directory: "C:\\fixture\\Policy Vault\\platform\\sources",
    note_directory: "C:\\fixture\\Policy Vault\\platform\\notes",
    authorization_status: "active",
    access_status: "available",
    index_status: "not-initialized",
    created_at: "2026-07-21T00:00:00+00:00",
    updated_at: "2026-07-21T00:00:00+00:00",
    is_current: true,
    recovery_actions: [],
    get policy() {
      return policy;
    }
  };

  await page.route("**/api/vaults/vault-policy/policy**", async (route) => {
    const method = route.request().method();
    const url = route.request().url();
    if (method === "GET") {
      await route.fulfill({ json: { policy } });
      return;
    }
    if (url.endsWith("/preview")) {
      const payload = route.request().postDataJSON();
      const candidate = payload.candidate_kind
        ? { kind: payload.candidate_kind, relative_path: payload.candidate_relative_path }
        : policy.rules.find((rule) => rule.relative_path === payload.source_path);
      const blocked = candidate?.kind === "never-send-cloud"
        && payload.stage === "outbound"
        && payload.source_path.startsWith(candidate.relative_path);
      await route.fulfill({
        json: {
          preview: {
            allowed: !blocked,
            reason: blocked
              ? "Matched never-send-cloud rule; outbound processing is blocked."
              : "No matching rule blocks this stage."
          }
        }
      });
      return;
    }
    if (url.endsWith("/rules")) {
      const payload = route.request().postDataJSON();
      const rule = {
        rule_id: "rule-private",
        vault_id: vault.vault_id,
        kind: payload.kind,
        relative_path: payload.relative_path.replaceAll("\\", "/"),
        created_at: "2026-07-21T00:00:00+00:00",
        updated_at: "2026-07-21T00:00:00+00:00"
      };
      policy = { ...policy, policy_revision: policy.policy_revision + 1, rules: [...policy.rules, rule] };
      await route.fulfill({ json: { rule } });
      return;
    }
    await route.fallback();
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [vault] } });
  });

  await page.goto("/");
  await page.getByRole("link", { name: "资料" }).click();
  await page.getByRole("button", { name: /Policy Vault/ }).click();
  await expect(page.getByRole("heading", { name: "资料排除规则" })).toBeVisible();
  await expect(page.locator(".policy-summary")).toContainText("已验证 Provider 默认允许出网");
  await page.getByRole("button", { name: "添加规则" }).click();
  await page.getByLabel("规则类型").selectOption("never-send-cloud");
  await page.getByLabel("vault 相对路径").fill("private\\plans");
  await page.getByRole("button", { name: "验证预览" }).click();
  await expect(page.getByText(/预览：Matched never-send-cloud/)).toBeVisible();
  await page.getByRole("button", { name: "添加规则" }).last().click();
  await expect(page.getByText("private/plans")).toBeVisible();
  await page.getByRole("button", { name: "预览" }).click();
  await expect(page.getByText(/预览：Matched never-send-cloud/)).toBeVisible();
});

test("sends a browser-selected folder as relative upload paths", async ({ page }, testInfo) => {
  const folderPath = testInfo.outputPath("materials");
  await mkdir(join(folderPath, "chapter-1"), { recursive: true });
  await writeFile(join(folderPath, "chapter-1", "book.pdf"), "pdf");
  const vault = {
    vault_id: "vault-folder-upload",
    display_name: "Import Vault",
    authorization_status: "active",
    access_status: "available",
    is_current: true
  };
  const task = {
    task_id: "task-folder-upload",
    vault_id: vault.vault_id,
    vault_label: "Import Vault",
    scope_label: "materials",
    lifecycle: "queued",
    phase: "queued",
    recovery_actions: [],
    counts: {}
  };
  let uploadBody = "";
  let importTaskRequest = null;

  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [vault] } });
  });
  await page.route("**/api/import-selections/uploads", async (route) => {
    uploadBody = route.request().postDataBuffer()?.toString("latin1") || "";
    await route.fulfill({ json: { selection_id: "folder-selection", label: "materials" } });
  });
  await page.route("**/api/import-tasks", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { tasks: [] } });
      return;
    }
    importTaskRequest = route.request().postDataJSON();
    await route.fulfill({ json: { task } });
  });

  await page.goto("/");
  await page.getByRole("link", { name: "资料", exact: true }).click();
  const folderInput = page.getByLabel("上传本机资料文件夹");
  await expect(folderInput).toHaveAttribute("webkitdirectory", "");
  await folderInput.setInputFiles(folderPath);

  await expect.poll(() => uploadBody).toContain('name="kind"\r\n\r\ndirectory');
  expect(uploadBody).toContain('filename="materials/chapter-1/book.pdf"');
  await expect.poll(() => importTaskRequest).not.toBeNull();
  expect(importTaskRequest).toMatchObject({
    online_parse_enabled: false,
    online_parse_provider_id: null,
    markdown_pipeline: "ai"
  });
});

test("runs import tasks automatically and keeps suggestions read-only", async ({ page }) => {
  const vault = {
    vault_id: "vault-import",
    path: "C:\\fixture\\Import Vault",
    managed_root_relative_path: "platform",
    managed_root: "C:\\fixture\\Import Vault\\platform",
    source_directory: "C:\\fixture\\Import Vault\\platform\\sources",
    note_directory: "C:\\fixture\\Import Vault\\platform\\notes",
    authorization_status: "active",
    access_status: "available",
    index_status: "not-initialized",
    created_at: "2026-07-21T00:00:00+00:00",
    updated_at: "2026-07-21T00:00:00+00:00",
    is_current: true,
    recovery_actions: []
  };
  const task = {
    task_id: "task-import",
    vault_id: vault.vault_id,
    vault_label: "Import Vault",
    scope_label: "book.pdf",
    lifecycle: "complete",
    phase: "complete",
    current_item_label: null,
    counts: {
      discovered: 1,
      supported: 1,
      skipped: 0,
      unsupported: 0,
      failed: 0,
      new: 1,
      duplicate: 0,
      possible_version: 1,
      identity_failed: 0,
      parsed: 1,
      parse_failed: 0,
      ocr_completed: 1,
      ocr_failed: 0,
      confirmed_gaps: 0,
      required_check: 0,
      derived_notes: 1
    },
    recovery_actions: [],
    failure_reason: null,
    parent_task_id: null,
    markdown_pipeline: "local",
    online_parse: { enabled: false },
    created_at: "2026-07-21T00:00:00+00:00",
    updated_at: "2026-07-21T00:00:00+00:00"
  };

  await page.route("**/api/import-selections/uploads", async (route) => {
    await route.fulfill({ json: { selection_id: "import-selection", label: "book.pdf" } });
  });
  await page.route("**/api/import-tasks", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { tasks: [] } });
      return;
    }
    await route.fulfill({ json: { task } });
  });
  await page.route("**/api/import-tasks/task-import", async (route) => {
    await route.fulfill({
      json: {
        task,
        event_cursor: 4,
        items: [{
          item_id: 1,
          label: "book.pdf",
          category: "supported",
          document_kind: "pdf",
          reason: null,
          content_sha256: "b".repeat(64),
          source_id: "source-new",
          identity_status: "new",
          parse_status: "parsed",
          conversion_status: "selected",
          parse_confidence: 0.72,
          parse_issue_count: 1,
          parse_locator_summary: "page 2",
          parse_issue_summary: null,
          ocr_status: "ocr-completed",
          ocr_confidence: 0.99,
          ocr_issue_count: 0,
          ocr_locator_summary: null,
          ocr_issue_summary: null,
          ocr_targets: [{
            target_id: "page:2",
            label: "Page 2",
            locator_summary: "page 2",
            engine: "paddleocr-vl-1.6",
            status: "completed",
            confidence: 0.99,
            issue_count: 1,
            decision: null,
            decision_reason: null
          }],
          version_suggestion: {
            candidate_source_id: "source-old",
            previous_content_sha256: "a".repeat(64),
            reason: "同名文件的内容不同。",
            status: "required-check"
          }
        }],
        note_proposals: [{
          kind: "derived",
          item_id: 1,
          revision: 1,
          source_relative_path: "platform/sources/source-new-bbbbbbbbbbbbbbbb.pdf",
          risks: [],
          index_note: {
            relative_path: "platform/notes/source-new/index.md",
            markdown: "# Book index\n\n[[platform/notes/source-new/01-chapter-one|Chapter One]]"
          },
          notes: [{
            note_id: "note-1",
            title: "Chapter One",
            sequence: 1,
            relative_path: "platform/notes/source-new/01-chapter-one.md",
            source_locators: [{ page: 1 }],
            unit_indexes: [0, 1, 2],
            safe_split_after_unit_indexes: [1],
            markdown: "# Chapter One\n\nPreview text"
          }, {
            note_id: "note-2",
            title: "Chapter Two",
            sequence: 2,
            relative_path: "platform/notes/source-new/02-chapter-two.md",
            source_locators: [{ page: 2 }],
            unit_indexes: [3],
            markdown: "# Chapter Two\n\nMore preview text"
          }]
        }],
        source_parses: [{
          item_id: 1,
          blocks: [{
            kind: "heading",
            location: "第 1 页",
            content: "Chapter One"
          }, {
            kind: "paragraph",
            location: "第 1 页",
            content: "Source parsing preview text"
          }]
        }],
        classification_suggestions: [{
          item_id: 1,
          revision: 1,
          proposal_revision: 1,
          domain: "unclassified",
          target_vault_id: "vault-import",
          target_vault_label: "Import Vault",
          target_folder: "platform/notes/unclassified",
          filename: "book.pdf",
          confidence: 0.4,
          status: "required-check",
          decision: null,
          decision_reason: null,
          origin: "generated",
          reason: "No supported domain terms were found in the private proposal.",
          created_at: "2026-07-22T00:00:00+00:00",
          decided_at: null
        }],
        commit_journals: [{ unit_id: "source-new", source_label: "book.pdf", status: "committed", reason: null }]
      }
    });
  });
  await page.route("**/api/import-tasks/task-import/events?after=4", async (route) => {
    await route.fulfill({ contentType: "text/event-stream", body: ": keep-alive\n\n" });
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [vault] } });
  });

  await page.addInitScript(() => {
    localStorage.setItem("obsidian-platform.online-parse-enabled.v1", "false");
    localStorage.setItem("obsidian-platform.markdown-pipeline.v1", "local");
  });
  const eventSubscription = page.waitForRequest("**/api/import-tasks/task-import/events?after=4");
  await page.goto("/");
  await page.getByRole("link", { name: "资料", exact: true }).click();
  await page.getByLabel("上传本机资料文件", { exact: true }).setInputFiles({
    name: "book.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("pdf")
  });

  await expect(page.getByRole("heading", { name: "任务", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "导入任务详情" })).toBeVisible();
  await eventSubscription;
  await expect(page.getByText("结构化：本地结构化", { exact: true })).toBeVisible();
  await expect(page.getByText("状态：已完成")).toBeVisible();
  await expect(page.getByText("当前阶段：完成")).toBeVisible();
  await expect(page.getByText("解析：已完成")).toBeVisible();
  await expect(page.getByText("已发现 1")).toBeVisible();
  await expect(page.getByText("已解析 1")).toBeVisible();
  await expect(page.getByText("已解析；已选择完整转换图；OCR 完成", { exact: true })).toBeVisible();
  await expect(page.getByText("PDF（电子/扫描待识别）")).toBeVisible();
  const sourceParses = page.getByLabel("源解析内容");
  await expect(sourceParses.getByRole("heading", { name: "源解析内容" })).toBeVisible();
  await expect(sourceParses).toContainText("Source parsing preview text");
  const markdownResults = page.getByLabel("Markdown 结果");
  await expect(markdownResults.getByRole("heading", { name: "Markdown 结果" })).toBeVisible();
  await expect(markdownResults.locator("pre.markdown-preview").nth(0)).toContainText("# Book index");
  await expect(markdownResults.locator("pre.markdown-preview").nth(1)).toContainText("# Chapter One");
  await expect(markdownResults.locator("pre.markdown-preview").nth(2)).toContainText("# Chapter Two");
  await expect(page.getByRole("heading", { name: "分类建议" })).toBeVisible();
  await expect(page.getByText("No supported domain terms were found in the private proposal.")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("元数据与标签");
  await expect(page.locator("body")).not.toContainText("候选链接");
  await expect(page.getByText("接受标签", { exact: true })).toHaveCount(0);
  await expect(page.getByText("提交审核", { exact: true })).toHaveCount(0);
  await expect(page.getByText("提交所选", { exact: true })).toHaveCount(0);
  await expect(page.getByText("审核决定", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "接受高置信度建议" })).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("task-import");
  await expect(page.locator("body")).not.toContainText("unit:0");
  await expect(page.locator("body")).not.toContainText("page 2 box:10,20,60,12");
  await expect(page.locator("body")).not.toContainText("internal-block-id");
  await expect(page.locator(".commit-journal-list")).toBeVisible();
});

test("verifies a durable projection after deleting its completed import task", async ({ page }) => {
  const vault = {
    vault_id: "vault-projection",
    path: "C:\\fixture\\Projection Vault",
    managed_root_relative_path: "platform",
    managed_root: "C:\\fixture\\Projection Vault\\platform",
    source_directory: "C:\\fixture\\Projection Vault\\platform\\sources",
    note_directory: "C:\\fixture\\Projection Vault\\platform\\notes",
    authorization_status: "active",
    access_status: "available",
    index_status: "healthy",
    created_at: "2026-07-26T00:00:00+00:00",
    updated_at: "2026-07-26T00:00:00+00:00",
    is_current: true,
    recovery_actions: []
  };
  const task = {
    task_id: "task-projection",
    vault_id: vault.vault_id,
    vault_label: "Projection Vault",
    scope_label: "verified-book.pdf",
    lifecycle: "complete",
    phase: "complete",
    current_item_label: null,
    counts: {
      discovered: 1,
      supported: 1,
      skipped: 0,
      unsupported: 0,
      failed: 0,
      new: 1,
      duplicate: 0,
      possible_version: 0,
      identity_failed: 0,
      parsed: 1,
      parse_failed: 0,
      ocr_completed: 0,
      ocr_failed: 0,
      confirmed_gaps: 0,
      required_check: 0,
      derived_notes: 1
    },
    recovery_actions: [],
    failure_reason: null,
    parent_task_id: null,
    created_at: "2026-07-26T00:00:00+00:00",
    updated_at: "2026-07-26T00:00:00+00:00"
  };
  const projection = {
    vault_id: vault.vault_id,
    graph_id: "graph-projection",
    graph_revision: 7,
    block_count: 2,
    retrievable_block_count: 1,
    locator_summary: {
      type_counts: { "pdf-region": 1, "docx-ooxml": 1 },
      pdf_pages: [4],
      docx_part_count: 1
    },
    locator_digest: "d".repeat(64)
  };
  const detail = {
    task,
    items: [],
    note_proposals: [],
    classification_suggestions: [],
    conversion_graphs: [{ item_id: 1, graph_id: "graph-projection", graph_revision: 7, blocks: [] }],
    review_snapshot: null,
    commit_journals: [],
    index: { status: "healthy", current_count: 1, stale_count: 0, failure_count: 0 },
    event_cursor: 1
  };
  let deleted = false;
  let rebuilt = false;

  await page.route("**/api/vaults/vault-projection/graph**", async (route) => {
    if (route.request().url().includes("/events")) {
      await route.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" });
      return;
    }
    await route.fulfill({ json: { graph: { vault_id: vault.vault_id, nodes: [], edges: [], directories: [], tags: [], index: { status: "healthy", current_count: 0, stale_count: 0, failure_count: 0, pending_count: 0, failed_paths: [], stale_paths: [], semantic_status: "unavailable" } } } });
  });
  await page.route("**/api/vaults/vault-projection/graph-projections/graph-projection/7", async (route) => {
    await route.fulfill({ json: { projection } });
  });
  await page.route("**/api/vaults/vault-projection/index/rebuild", async (route) => {
    expect(deleted).toBe(true);
    rebuilt = true;
    await route.fulfill({ json: { vault, index: { status: "healthy" } } });
  });
  await page.route("**/api/import-tasks/task-projection/events?after=1", async (route) => {
    await route.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" });
  });
  await page.route("**/api/import-tasks/task-projection", async (route) => {
    if (route.request().method() === "DELETE") {
      deleted = true;
      await route.fulfill({ status: 204 });
      return;
    }
    await route.fulfill({ json: detail });
  });
  await page.route("**/api/import-tasks", async (route) => {
    await route.fulfill({ json: { tasks: deleted ? [] : [task] } });
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [vault] } });
  });

  await page.goto("/");
  await page.getByRole("link", { name: "任务", exact: true }).click();
  await page.locator(".import-task-open").click();
  await expect(page.getByRole("heading", { name: "投影重建验证" })).toBeVisible();
  await page.getByRole("button", { name: "读取投影摘要" }).click();
  await expect(page.getByTestId("projection-before-summary")).toContainText("PDF 页 4");
  const verifyButton = page.getByRole("button", { name: "删除并重建验证" });
  await expect(verifyButton).toBeDisabled();
  await page.getByRole("checkbox", { name: "我确认删除此导入任务并执行索引重建验证" }).check();
  await verifyButton.click();

  await expect.poll(() => deleted).toBe(true);
  await expect.poll(() => rebuilt).toBe(true);
  await expect(page.getByText("验证通过：任务已删除，索引重建成功，投影结构摘要保持一致。")).toBeVisible();
  await expect(page.getByTestId("projection-after-summary")).toContainText("DOCX 内容 1 处");
  await expect(page.getByRole("heading", { name: "导入任务详情" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("graph-projection");
  await expect(page.locator("body")).not.toContainText("docx-ooxml");
});

test("does not expose a manual commit request for an automatic import", async ({ page }) => {
  const task = {
    task_id: "task-automatic",
    vault_id: "vault-automatic",
    vault_label: "Automatic Vault",
    scope_label: "book.pdf",
    lifecycle: "complete",
    phase: "complete",
    current_item_label: null,
    counts: { discovered: 1, parsed: 1, derived_notes: 1, failed: 0, parse_failed: 0, ocr_failed: 0 },
    recovery_actions: [],
    failure_reason: null,
    parent_task_id: null,
    created_at: "2026-07-22T00:00:00+00:00",
    updated_at: "2026-07-22T00:00:00+00:00"
  };
  const vault = {
    vault_id: task.vault_id,
    path: "C:\\fixture\\Automatic Vault",
    managed_root_relative_path: "platform",
    managed_root: "C:\\fixture\\Automatic Vault\\platform",
    source_directory: "C:\\fixture\\Automatic Vault\\platform\\sources",
    note_directory: "C:\\fixture\\Automatic Vault\\platform\\notes",
    authorization_status: "active",
    access_status: "available",
    index_status: "healthy",
    created_at: task.created_at,
    updated_at: task.updated_at,
    is_current: true,
    recovery_actions: []
  };
  const detail = {
    task,
    items: [{ item_id: 1, label: "book.pdf", category: "supported", document_kind: "pdf", parse_status: "parsed", conversion_status: "selected", ocr_status: "ocr-completed" }],
    classification_suggestions: [{ item_id: 1, filename: "book.pdf", domain: "unclassified", reason: "只读分类观察。" }],
    commit_journals: [{ unit_id: "source-1", source_label: "book.pdf", status: "committed", reason: null }],
    index: { status: "healthy", current_count: 1, failure_count: 0 },
    event_cursor: 9
  };
  const requests = [];
  page.on("request", (request) => requests.push(request.url()));
  await page.route("**/api/import-tasks", async (route) => {
    await route.fulfill({ json: { tasks: [task] } });
  });
  await page.route("**/api/import-tasks/task-automatic", async (route) => {
    await route.fulfill({ json: detail });
  });
  await page.route("**/api/import-tasks/task-automatic/events?after=9", async (route) => {
    await route.fulfill({ contentType: "text/event-stream", body: ": keep-alive\n\n" });
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [vault] } });
  });

  await page.goto("/");
  await page.getByRole("link", { name: "任务", exact: true }).click();
  await page.getByRole("button", { name: /^book\.pdf/ }).click();
  await expect(page.getByText("状态：已完成")).toBeVisible();
  await expect(page.getByText("当前阶段：完成")).toBeVisible();
  await expect(page.getByText("只读分类观察。", { exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("只读链接候选");
  await expect(page.getByText("提交记录", { exact: true })).toBeVisible();
  await expect(page.getByText("提交审核", { exact: true })).toHaveCount(0);
  await expect(page.getByText("提交所选", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /接受|排除|保存修正/ })).toHaveCount(0);
  expect(requests.some((url) => url.endsWith("/commit"))).toBe(false);
});

test("shows a task loading failure instead of an empty task list", async ({ page }) => {
  await page.route("**/api/import-tasks", async (route) => {
    await route.fulfill({
      status: 503,
      json: { code: "task_unavailable", message: "Task storage is unavailable.", details: {}, retryable: true }
    });
  });

  await page.goto("/");
  await page.getByRole("link", { name: "任务", exact: true }).click();

  await expect(page.getByText("无法读取导入任务：Task storage is unavailable.")).toBeVisible();
  await expect(page.getByText("当前没有导入任务。")).toHaveCount(0);
});

test("deletes selected current-page tasks while retaining an item that fails safe deletion", async ({ page }) => {
  const vault = {
    vault_id: "vault-bulk-delete",
    display_name: "Bulk Delete Vault",
    authorization_status: "active",
    access_status: "available",
    is_current: true
  };
  const task = (taskId, scopeLabel, lifecycle = "complete") => ({
    task_id: taskId,
    vault_id: vault.vault_id,
    vault_label: vault.display_name,
    scope_label: scopeLabel,
    lifecycle,
    phase: lifecycle === "running" ? "scanning" : "completed",
    current_item_label: null,
    counts: { discovered: 1, new: 1, duplicate: 0, possible_version: 0, identity_failed: 0, parsed: 1, parse_failed: 0, required_check: 0, failed: 0 },
    recovery_actions: [],
    failure_reason: null,
    parent_task_id: null,
    created_at: "2026-08-12T00:00:00+00:00",
    updated_at: "2026-08-12T00:00:00+00:00"
  });
  const first = task("task-delete-first", "first.pdf");
  const running = task("task-delete-running", "running.pdf", "running");
  const third = task("task-delete-third", "third.pdf");
  const deletedTaskIds = [];

  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [vault] } });
  });
  await page.route("**/api/import-tasks", async (route) => {
    await route.fulfill({ json: { tasks: [first, running, third] } });
  });
  await page.route("**/api/import-tasks/*", async (route) => {
    const taskId = new URL(route.request().url()).pathname.split("/").at(-1);
    deletedTaskIds.push(taskId);
    if (taskId === third.task_id) {
      await route.fulfill({
        status: 409,
        json: { message: "Vault 文件已被修改。" }
      });
      return;
    }
    await route.fulfill({ status: 204 });
  });

  await page.goto("/");
  await page.getByRole("link", { name: "任务", exact: true }).click();
  const selectAll = page.getByRole("checkbox", { name: "全选当前页可删除任务" });
  await expect(selectAll).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "选择任务 running.pdf" })).toHaveCount(0);
  await selectAll.check();
  await expect(page.getByText("已选择 2 项", { exact: true })).toBeVisible();

  page.once("dialog", (dialog) => {
    expect(dialog.message()).toContain("删除所选 2 个任务");
    void dialog.accept();
  });
  await page.getByRole("button", { name: "删除所选", exact: true }).click();

  await expect.poll(() => deletedTaskIds).toEqual([first.task_id, third.task_id]);
  await expect(page.getByText("first.pdf", { exact: true })).toHaveCount(0);
  await expect(page.getByText("running.pdf", { exact: true })).toBeVisible();
  await expect(page.getByText("third.pdf", { exact: true })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "选择任务 third.pdf" })).toBeChecked();
  await expect(page.getByText("无法删除导入任务：third.pdf：Vault 文件已被修改。", { exact: true })).toBeVisible();
});

test("configures independent chat and Rerank models without requiring an Embedding model", async ({ page }) => {
  let providers = [];
  let modelTestShouldFail = false;
  let defaults = {
    chat: { default: null, status: "unconfigured", reason: "No chat Provider model is selected." },
    embedding: { default: null, status: "unconfigured", reason: "No embedding Provider model is selected." },
    rerank: { default: null, status: "unconfigured", reason: "No rerank Provider model is selected." }
  };

  function unverifiedProvider(payload) {
    return {
      provider_id: "provider-test",
      name: payload.name,
      endpoint: payload.endpoint,
      transport: "openai-compatible",
      api_mode: payload.api_mode,
      credential_configured: Boolean(payload.secret),
      verification: {
        discovery: { ok: false, reason: "Not yet verified." },
        health: { ok: false, reason: "Not yet verified." },
        is_verified: false
      },
      models: [],
      last_tested_at: null,
      created_at: "2026-07-21T00:00:00+00:00",
      updated_at: "2026-07-21T00:00:00+00:00"
    };
  }

  function discoveredProvider(provider) {
    return {
      ...provider,
      verification: {
        discovery: { ok: true, reason: null },
        health: { ok: true, reason: null },
        is_verified: true
      },
      models: [{
        model_id: "model/chat::primary",
        model_type: null,
        verification: { ok: false, reason: "Not yet verified." },
        is_discovered: true,
        verified_at: null
      }],
      last_tested_at: "2026-07-21T00:01:00+00:00"
    };
  }

  await page.route("**/api/providers**", async (route) => {
    const url = route.request().url();
    const method = route.request().method();
    if (method === "GET" && url.endsWith("/defaults")) {
      await route.fulfill({ json: defaults });
      return;
    }
    if (method === "GET" && url.endsWith("/api/providers")) {
      await route.fulfill({ json: { providers } });
      return;
    }
    if (method === "POST" && url.endsWith("/api/providers")) {
      const payload = route.request().postDataJSON();
      providers = [unverifiedProvider(payload)];
      await route.fulfill({ json: { provider: providers[0] } });
      return;
    }
    if (method === "POST" && url.endsWith("/provider-test/test")) {
      providers = [discoveredProvider(providers[0])];
      await route.fulfill({ json: { provider: providers[0] } });
      return;
    }
    if (method === "PUT" && url.endsWith("/models")) {
      const payload = route.request().postDataJSON();
      providers[0] = { ...providers[0], models: providers[0].models.map((model) => ({
        ...model, model_type: payload.model_type, verification: { ok: false, reason: "Not yet verified." }
      })) };
      await route.fulfill({ json: { provider: providers[0] } });
      return;
    }
    if (method === "POST" && url.endsWith("/models/test")) {
      providers[0] = { ...providers[0], models: providers[0].models.map((model) => ({
        ...model,
        verification: modelTestShouldFail
          ? { ok: false, reason: "Chat model verification could not be completed. Provider TLS connection failed." }
          : { ok: true, reason: null },
        verified_at: "2026-07-21T00:02:00+00:00"
      })) };
      await route.fulfill({ json: { provider: providers[0] } });
      return;
    }
    if (method === "DELETE" && url.includes("/provider-test/models?")) {
      const modelId = new URL(url).searchParams.get("model_id");
      providers[0] = {
        ...providers[0],
        models: providers[0].models.filter((model) => model.model_id !== modelId)
      };
      for (const [modelType, selection] of Object.entries(defaults)) {
        if (selection.default?.provider_id === "provider-test" && selection.default?.model_id === modelId) {
          defaults[modelType] = { default: null, status: "unconfigured", reason: `No ${modelType} Provider model is selected.` };
        }
      }
      await route.fulfill({ json: { provider: providers[0] } });
      return;
    }
    if (method === "PUT" && url.endsWith("/defaults/chat")) {
      const payload = route.request().postDataJSON();
      defaults.chat = { default: { ...payload, updated_at: "2026-07-21T00:03:00+00:00" }, status: "available", reason: null };
      await route.fulfill({ json: { default: defaults.chat.default } });
      return;
    }
    if (method === "DELETE" && url.endsWith("/defaults/chat")) {
      defaults.chat = { default: null, status: "unconfigured", reason: "No chat Provider model is selected." };
      await route.fulfill({ json: { status: "cleared" } });
      return;
    }
    if (method === "PUT" && url.endsWith("/defaults/rerank")) {
      const payload = route.request().postDataJSON();
      defaults.rerank = { default: { ...payload, updated_at: "2026-07-21T00:03:00+00:00" }, status: "available", reason: null };
      await route.fulfill({ json: { default: defaults.rerank.default } });
      return;
    }
    if (method === "DELETE" && url.endsWith("/defaults/rerank")) {
      defaults.rerank = { default: null, status: "unconfigured", reason: "No rerank Provider model is selected." };
      await route.fulfill({ json: { status: "cleared" } });
      return;
    }
    if (method === "DELETE" && url.endsWith("/provider-test")) {
      providers = [];
      defaults.chat = { default: null, status: "unconfigured", reason: "No chat Provider model is selected." };
      defaults.rerank = { default: null, status: "unconfigured", reason: "No rerank Provider model is selected." };
      await route.fulfill({ json: { status: "removed" } });
      return;
    }
    await route.fallback();
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [] } });
  });

  await page.goto("/");
  await page.getByRole("link", { name: "设置" }).click();
  await expect(page.getByRole("heading", { name: "Provider" })).toBeVisible();
  await page.getByRole("button", { name: "添加 Provider" }).click();
  const providerForm = page.locator(".provider-form");
  const secret = providerForm.getByLabel("API Key（可选）");
  await expect(secret).toHaveAttribute("type", "password");
  await providerForm.getByLabel("名称").fill("Local AI");
  await providerForm.getByLabel("服务地址").fill("http://127.0.0.1:11434/v1");
  await providerForm.getByLabel("API 模式").selectOption("responses");
  await providerForm.getByRole("button", { name: "添加 Provider" }).click();

  await expect(page.getByText("Local AI")).toBeVisible();
  await expect(page.getByText("API Key：未配置")).toHaveCount(0);
  await expect(page.getByText("Responses API")).toBeVisible();
  await expect(providerForm.getByLabel("API Key（可选）")).toHaveCount(0);
  await expect(page.getByRole("img", { name: "模型发现：尚未验证。" })).toBeVisible();
  await expect(page.getByRole("img", { name: "服务健康：尚未验证。" })).toBeVisible();
  await page.getByRole("button", { name: "测试 Local AI", exact: true }).click();
  await expect(page.getByRole("img", { name: "模型发现：通过" })).toBeVisible();
  await expect(page.getByRole("img", { name: "服务健康：通过" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "语义检索" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "候选重排" })).toBeVisible();
  await expect(page.getByText("model/chat::primary", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "添加模型" }).click();
  await page.getByLabel("类型").selectOption("chat");
  await page.getByRole("button", { name: "添加并验证" }).click();
  await expect(page.getByText("模型已添加并验证。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("model/chat::primary 模型类型")).toHaveValue("chat");
  await page.getByLabel("对话与文本默认模型").selectOption(JSON.stringify(["provider-test", "model/chat::primary"]));
  await expect(page.getByText("对话/文本生成默认 Model 已更新。")).toBeVisible();
  await page.getByLabel("对话与文本默认模型").selectOption("");
  await expect(page.getByText("对话/文本生成默认 Model 已清除。")).toBeVisible();
  await page.getByLabel("model/chat::primary 模型类型").selectOption("rerank");
  await expect(page.getByText("模型类型已更新并验证。", { exact: true })).toBeVisible();
  await page.getByLabel("候选重排默认模型").selectOption(JSON.stringify(["provider-test", "model/chat::primary"]));
  await expect(page.getByText("Rerank（重排）默认 Model 已更新。")).toBeVisible();
  modelTestShouldFail = true;
  await page.getByRole("button", { name: "测试模型" }).click();
  await expect(page.getByText("模型验证失败：Provider TLS 连接失败。请检查服务地址或证书后重试。", { exact: true })).toBeVisible();
  await expect(page.getByText("原因：模型验证失败：Provider TLS 连接失败。请检查服务地址或证书后重试。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("model/chat::primary 模型类型")).toHaveValue("rerank");
  await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
  await page.getByRole("button", { name: "删除模型 model/chat::primary" }).click();
  await expect(page.getByRole("dialog", { name: "删除模型" })).toBeVisible();
  await page.getByRole("dialog", { name: "删除模型" }).getByRole("button", { name: "删除模型" }).click();
  await expect(page.getByText("model/chat::primary", { exact: true })).toHaveCount(0);
  const deleteButton = page.getByRole("button", { name: "删除" });
  await deleteButton.focus();
  await deleteButton.click();
  await expect(page.getByRole("dialog", { name: "删除 Provider" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "删除 Provider" })).toBeHidden();
  await expect(deleteButton).toBeFocused();
});

test("manages bounded private sessions without inheriting a vault or closing deletion on Escape", async ({ page }) => {
  let nextSessionId = 27;
  let sessionDeleteAttempts = 0;
  const sessionListSorts = [];
  let sessions = Array.from({ length: 26 }, (_, index) => ({
    session_id: `session-${index + 1}`,
    title: `会话 ${index + 1}`,
    selected_vault_id: index === 0 ? "vault-math" : null,
    selected_vault_label: index === 0 ? "数学资料" : null,
    selected_provider_id: null,
    selected_provider_label: null,
    selected_model_id: null,
    selected_model_label: null,
    message_count: 0,
    created_at: `2026-07-22T00:${String(index).padStart(2, "0")}:00+00:00`,
    updated_at: `2026-07-22T00:${String(index).padStart(2, "0")}:00+00:00`,
    last_activity_at: `2026-07-22T00:${String(index).padStart(2, "0")}:00+00:00`
  }));

  function listPayload(url) {
    const query = url.searchParams.get("query") || "";
    const page = Number(url.searchParams.get("page") || "1");
    const pageSize = Number(url.searchParams.get("page_size") || "25");
    const matching = sessions.filter((session) => (
      `${session.title} ${session.selected_vault_label || ""}`.includes(query)
    ));
    return {
      sessions: matching.slice((page - 1) * pageSize, page * pageSize),
      page,
      page_size: pageSize,
      total: matching.length,
      total_pages: Math.max(1, Math.ceil(matching.length / pageSize))
    };
  }

  await page.route("**/api/sessions**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const parts = url.pathname.split("/").filter(Boolean);
    const sessionId = parts[2];
    if (method === "GET" && !sessionId) {
      sessionListSorts.push(url.searchParams.get("sort"));
      await route.fulfill({ json: listPayload(url) });
      return;
    }
    if (method === "POST") {
      const session = {
        session_id: `session-${nextSessionId++}`,
        title: "未命名会话",
        selected_vault_id: null,
        selected_vault_label: null,
        selected_provider_id: null,
        selected_provider_label: null,
        selected_model_id: null,
        selected_model_label: null,
        message_count: 0,
        created_at: "2026-07-22T01:00:00+00:00",
        updated_at: "2026-07-22T01:00:00+00:00",
        last_activity_at: "2026-07-22T01:00:00+00:00"
      };
      sessions = [session, ...sessions];
      await route.fulfill({ json: { session } });
      return;
    }
    const session = sessions.find((item) => item.session_id === sessionId);
    if (method === "GET" && session && parts.at(-1) !== "export") {
      await route.fulfill({
        json: {
          session,
          messages: sessionId === "session-1"
            ? [
                { message_id: "message-1", role: "user", content: "第一轮问题", created_at: "2026-07-22T00:00:00+00:00" },
                { message_id: "message-2", role: "assistant", content: "会话详情已加载。\n".repeat(48), created_at: "2026-07-22T00:00:01+00:00" },
                { message_id: "message-3", role: "user", content: "第二轮问题", created_at: "2026-07-22T00:00:02+00:00" },
                { message_id: "message-4", role: "assistant", content: "第二轮回答。", created_at: "2026-07-22T00:00:03+00:00" }
              ]
            : [],
          task_states: [],
          citations: sessionId === "session-1"
            ? [{ citation_id: "citation-1", relative_path: "notes/algebra.md", location: "第 2 节", status: "valid" }]
            : [],
          generation_results: []
        }
      });
      return;
    }
    if (method === "PATCH") {
      const { title } = request.postDataJSON();
      session.title = title;
      session.updated_at = "2026-07-22T01:01:00+00:00";
      await route.fulfill({ json: { session } });
      return;
    }
    if (method === "GET" && parts.at(-1) === "export") {
      await route.fulfill({
        contentType: "application/json",
        headers: { "Content-Disposition": `attachment; filename=\"${sessionId}.json\"` },
        body: JSON.stringify({ session })
      });
      return;
    }
    if (method === "DELETE") {
      sessionDeleteAttempts += 1;
      if (sessionDeleteAttempts === 1) {
        await route.fulfill({
          status: 500,
          json: { code: "session_operation_failed", message: "无法删除会话。", details: {}, retryable: true }
        });
        return;
      }
      sessions = sessions.filter((item) => item.session_id !== sessionId);
      await route.fulfill({ json: { status: "removed" } });
      return;
    }
    await route.fallback();
  });

  await page.goto("/");
  await page.getByRole("link", { name: "会话", exact: true }).click();

  await expect(page.getByRole("button", { name: /会话 1 所用 vault：数学资料/ })).toBeVisible();
  await expect(page.getByLabel("会话内容").locator(".session-detail-meta")).toHaveCount(0);
  await expect(page.getByText("会话详情已加载。")).toBeVisible();
  const messageList = page.locator(".session-message-list");
  const turnNavigator = page.getByLabel("问答定位");
  await expect(turnNavigator.getByRole("button")).toHaveCount(2);
  await expect(messageList).toHaveCSS("scroll-behavior", "auto");
  await expect.poll(() => messageList.evaluate((element) => element.scrollTop)).toBeGreaterThan(32);
  await turnNavigator.getByRole("button").first().click();
  await expect.poll(() => messageList.evaluate((element) => element.scrollTop)).toBeLessThan(32);
  await expect(page.getByText("algebra.md", { exact: true })).toBeVisible();
  await expect(page.getByText("第 1 / 2 页", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByRole("button", { name: /会话 26/ })).toBeVisible();

  await page.getByLabel("搜索会话").fill("会话 26");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByRole("button", { name: /会话 26/ })).toBeVisible();
  await expect(page.getByText("会话 1", { exact: true })).toHaveCount(0);

  await page.getByLabel("搜索会话").fill("");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page.getByLabel("会话排序").selectOption("vault");
  await expect.poll(() => sessionListSorts.at(-1)).toBe("vault");
  await page.getByRole("button", { name: "新建会话", exact: true }).click();
  await expect.poll(() => sessionListSorts.at(-1)).toBe("updated_at");
  const titleInput = page.getByLabel("未命名会话 的会话标题");
  await expect(titleInput).toBeFocused();
  await titleInput.fill("代数复习");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  const sessionRow = page.locator(".session-history-item", { hasText: "代数复习" });
  await expect(sessionRow).toContainText("所用 vault：未设置");

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出", exact: true }).click();
  await (await download).cancel();

  const deleteButton = page.getByRole("button", { name: "删除", exact: true });
  await deleteButton.focus();
  await deleteButton.click();
  const dialog = page.getByRole("dialog", { name: "删除会话“代数复习”？" });
  await expect(dialog).toContainText("不会删除、移动或改写已审核写入 vault 的资料、笔记或标签。", { exact: true });
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(deleteButton).toBeFocused();
  await deleteButton.click();
  await dialog.getByRole("button", { name: "删除会话", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(deleteButton).toBeFocused();
  await expect(page.getByText("无法删除会话。", { exact: true })).toBeVisible();
  await deleteButton.click();
  await dialog.getByRole("button", { name: "删除会话", exact: true }).click();
  await expect(page.getByText("代数复习", { exact: true })).toHaveCount(0);
});

test("keeps the latest session list result when an older search resolves last", async ({ page }) => {
  let releaseOlderSearch;
  const session = (sessionId, title) => ({
    session_id: sessionId,
    title,
    selected_vault_id: null,
    selected_vault_label: null,
    selected_provider_id: null,
    selected_provider_label: null,
    selected_model_id: null,
    selected_model_label: null,
    message_count: 0,
    created_at: "2026-07-22T00:00:00+00:00",
    updated_at: "2026-07-22T00:00:00+00:00",
    last_activity_at: "2026-07-22T00:00:00+00:00"
  });
  const pagePayload = (sessions) => ({
    sessions,
    page: 1,
    page_size: 25,
    total: sessions.length,
    total_pages: 1
  });

  await page.route("**/api/sessions**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const parts = url.pathname.split("/").filter(Boolean);
    if (request.method() === "GET" && !parts[2]) {
      const query = url.searchParams.get("query");
      if (query === "旧搜索") {
        await new Promise((resolve) => {
          releaseOlderSearch = async () => {
            await route.fulfill({ json: pagePayload([session("older", "旧搜索结果")]) });
            resolve();
          };
        });
        return;
      }
      await route.fulfill({
        json: pagePayload(
          query === "新搜索"
            ? [session("newer", "新搜索结果")]
            : [session("initial", "初始结果")]
        )
      });
      return;
    }
    if (request.method() === "GET" && parts[2]) {
      const detailSession = session(parts[2], parts[2] === "newer" ? "新搜索结果" : "旧搜索结果");
      await route.fulfill({
        json: { session: detailSession, messages: [], task_states: [], citations: [], generation_results: [] }
      });
      return;
    }
    await route.fallback();
  });

  await page.goto("/");
  await page.getByRole("link", { name: "会话", exact: true }).click();
  await page.getByLabel("搜索会话").fill("旧搜索");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect.poll(() => Boolean(releaseOlderSearch)).toBe(true);
  await page.getByLabel("搜索会话").fill("新搜索");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByRole("button", { name: /新搜索结果/ })).toBeVisible();

  await releaseOlderSearch();

  await expect(page.getByRole("button", { name: /新搜索结果/ })).toBeVisible();
  await expect(page.getByText("旧搜索结果", { exact: true })).toHaveCount(0);
});

test("submits the selected session context and question in one request", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(globalThis.navigator, "clipboard", {
      configurable: true,
      value: {
        writeText(value) {
          globalThis.__copiedSessionAnswer = value;
          return Promise.resolve();
        }
      }
    });
  });
  let session = {
    session_id: "session-1",
    title: "语境测试",
    selected_vault_id: "vault-a",
    selected_vault_label: "platform",
    selected_provider_id: "provider-1",
    selected_provider_label: "Local",
    selected_model_id: "chat-1",
    selected_model_label: "chat-1",
    scope_kind: "vault",
    scope_path: null,
    message_count: 0,
    created_at: "2026-07-23T00:00:00+00:00",
    updated_at: "2026-07-23T00:00:00+00:00",
    last_activity_at: "2026-07-23T00:00:00+00:00"
  };
  const messages = [];
  const citations = [];
  const generationResults = [];
  const retrievalResults = [];
  const runRequests = [];
  const retrievalModeRequests = [];
  const legacyRequests = [];
  const vaults = [
    { vault_id: "vault-a", display_name: "Session Vault A", managed_root_relative_path: "platform", authorization_status: "active", access_status: "available" },
    { vault_id: "vault-b", display_name: "Session Vault B", managed_root_relative_path: "platform", authorization_status: "active", access_status: "available" }
  ];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/health") return route.fulfill({ json: { service: "obsidian-personal-knowledge-platform" } });
    if (url.pathname === "/api/session") return route.fulfill({ json: { status: "ok" } });
    if (url.pathname === "/api/retrieval/mode") {
      if (request.method() === "POST") {
        const payload = request.postDataJSON();
        retrievalModeRequests.push(payload.mode);
        return route.fulfill({ json: {
          mode: payload.mode,
          label: payload.mode === "semantic" ? "仅语义" : "关键词与语义混合",
          options: [
            { mode: "keyword", label: "仅关键词" },
            { mode: "semantic", label: "仅语义" },
            { mode: "hybrid", label: "关键词与语义混合" }
          ]
        } });
      }
      return route.fulfill({ json: {
        mode: "keyword",
        label: "仅关键词",
        options: [
          { mode: "keyword", label: "仅关键词" },
          { mode: "semantic", label: "仅语义" },
          { mode: "hybrid", label: "关键词与语义混合" }
        ]
      } });
    }
    if (url.pathname === "/api/vaults") return route.fulfill({ json: { vaults } });
    if (url.pathname === "/api/providers/defaults") return route.fulfill({ json: { chat: {}, embedding: {} } });
    if (url.pathname === "/api/providers") {
      return route.fulfill({ json: { providers: [{
        provider_id: "provider-1",
        name: "Local",
        credential_configured: true,
        verification: { is_verified: true },
        models: [{ model_id: "chat-1", model_type: "chat", is_discovered: true, verification: { ok: true } }]
      }] } });
    }
    if (url.pathname === "/api/import-tasks") return route.fulfill({ json: { tasks: [] } });
    if (url.pathname === "/api/sessions" && request.method() === "GET") {
      return route.fulfill({ json: { sessions: [session], page: 1, page_size: 25, total: 1, total_pages: 1 } });
    }
    if (url.pathname === "/api/sessions/session-1" && request.method() === "GET") {
      return route.fulfill({ json: {
        session, messages, task_states: [], citations, generation_results: generationResults,
        attachments: [], task_snapshots: [], retrieval_results: retrievalResults
      } });
    }
    if (url.pathname === "/api/sessions/session-1/run/stream" && request.method() === "POST") {
      const command = request.postDataJSON();
      runRequests.push(command);
      session = {
        ...session,
        selected_vault_id: command.vault_id,
        selected_vault_label: command.vault_id === "vault-b" ? "Session Vault B" : "Session Vault A",
        selected_provider_id: command.provider_id,
        selected_provider_label: "Local",
        selected_model_id: command.model_id,
        selected_model_label: command.model_id,
        scope_kind: command.scope_kind,
        scope_path: command.scope_path,
        message_count: 1
      };
      messages.push({ message_id: "message-1", role: "user", content: command.content, created_at: "2026-07-23T00:00:01+00:00" });
      generationResults.push({
        result_id: "generation-1", status: "valid", content: "可直接使用的回答。",
        task_id: "task-1", snapshot_id: "snapshot-1", provider_id: command.provider_id,
        model_id: command.model_id, vault_id: command.vault_id, scope_kind: command.scope_kind,
        scope_path: command.scope_path, content_origin: "model-judgement", context_summary: "", created_at: "2026-07-23T00:00:02+00:00"
      });
      const result = {
        result_id: "result-1", task_id: "task-1", snapshot_id: "snapshot-1", vault_id: command.vault_id,
        status: "completed", summary: "已在已确认范围内找到 1 条知识库证据；已提交给所选 Model 生成回答。",
        recovery_action: null, retrieval_duration_ms: 3, generation_duration_ms: 2,
        evidences: [{ ordinal: 1, identity_kind: "native", relative_path: "notes/unit.md", heading: "Unit", location: "heading: Unit", excerpt: "可提交给模型的证据", matched_channels: ["lexical"] }]
      };
      citations.push({
        citation_id: "citation-1", result_id: "generation-1", snapshot_id: "snapshot-1",
        vault_id: command.vault_id, relative_path: "notes/unit.md", location: "heading: Unit",
        identity_kind: "native", status: "valid"
      });
      retrievalResults.push(result);
      return route.fulfill({
        contentType: "text/event-stream",
        body: [
          `event: chunk\ndata: ${JSON.stringify({ ordinal: 1, content: "可直接" })}\n\n`,
          `event: chunk\ndata: ${JSON.stringify({ ordinal: 1, content: "使用的回答。" })}\n\n`,
          `event: result\ndata: ${JSON.stringify({ result })}\n\n`
        ].join("")
      });
    }
    if (url.pathname.includes("/task-preview") || url.pathname.includes("/tasks")) {
      legacyRequests.push(url.pathname);
      return route.fulfill({ status: 500, json: { detail: "不应调用旧流程。" } });
    }
    return route.fallback();
  });

  await page.goto("/");
  await page.getByRole("link", { name: "会话", exact: true }).click();
  await expect(page.getByLabel("会话输入")).toBeVisible();
  await expect(page.getByText("所用 vault：Session Vault A", { exact: true })).toBeVisible();
  await expect(page.getByLabel("选择 vault").locator("option")).toHaveText(["选择 vault", "Session Vault A", "Session Vault B"]);

  await page.getByText("上下文", { exact: true }).click();
  await expect(page.getByLabel("选择 vault")).toBeVisible();
  await page.getByLabel("选择 vault").selectOption("vault-b");
  await page.getByLabel("选择 Model").selectOption(JSON.stringify(["provider-1", "chat-1"]));
  const composer = page.getByLabel("输入问题或继续创作");
  await composer.fill("第一行");
  await composer.press("Shift+Enter");
  await composer.pressSequentially("第二行");
  await expect(composer).toHaveValue("第一行\n第二行");
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeEnabled();
  await expect(page.getByText(/保存语境|准备任务|固定快照|执行检索/)).toHaveCount(0);
  await page.getByRole("button", { name: "发送", exact: true }).click();

  const conversation = page.getByLabel("会话内容");
  await expect(conversation.locator(".session-detail-meta")).toHaveCount(0);
  await expect(conversation).toContainText("可直接使用的回答。");
  await expect(conversation).toContainText("[1]");
  await expect(conversation).not.toContainText("可提交给模型的证据");
  await expect(page.getByRole("complementary", { name: "应用证据" })).toContainText("unit.md");
  await conversation.getByRole("link", { name: "查看来源 unit.md" }).click();
  await expect.poll(() => page.evaluate(() => document.activeElement?.id || "")).toMatch(/^application-evidence-/);
  await page.getByRole("button", { name: "复制正文", exact: true }).click();
  await expect.poll(() => page.evaluate(() => globalThis.__copiedSessionAnswer)).toBe("可直接使用的回答。");
  expect(runRequests).toEqual([{
    vault_id: "vault-b", scope_kind: "vault", scope_path: null, provider_id: "provider-1",
    model_id: "chat-1", content: "第一行\n第二行", intent: "auto"
  }]);
  expect(legacyRequests).toEqual([]);
});

test("renders keyboard-accessible paragraph editing and verification controls", async ({ page }) => {
  const session = {
    session_id: "session-citation",
    title: "引用核验",
    selected_vault_id: "vault-a",
    selected_vault_label: "Session Vault",
    selected_provider_id: "provider-1",
    selected_provider_label: "Local",
    selected_model_id: "chat-1",
    selected_model_label: "chat-1",
    scope_kind: "directory",
    scope_path: "notes",
    message_count: 1,
    created_at: "2026-07-23T00:00:00+00:00",
    updated_at: "2026-07-23T00:00:00+00:00",
    last_activity_at: "2026-07-23T00:00:00+00:00"
  };
  let answer = {
    result_id: "answer-1",
    status: "valid",
    content: "可追溯的本地证据摘要。",
    content_origin: "local-evidence",
    snapshot_id: "snapshot-1",
    provider_id: "provider-1",
    vault_id: "vault-a",
    model_id: "chat-1",
    scope_kind: "directory",
    scope_path: "notes",
    context_summary: "用户约束：仅限本地资料。当前范围：notes。未决问题：source-lookup。",
    created_at: "2026-07-23T00:00:01+00:00",
    updated_at: "2026-07-23T00:00:01+00:00"
  };
  let citation = {
    citation_id: "citation-1",
    result_id: "answer-1",
    vault_id: "vault-a",
    relative_path: "notes/unit.md",
    location: "heading: Unit",
    status: "valid",
    identity_kind: "native",
    content_sha256: "a".repeat(64),
    invalidation_reason: null
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/health") return route.fulfill({ json: { service: "obsidian-personal-knowledge-platform" } });
    if (url.pathname === "/api/session") return route.fulfill({ json: { status: "ok" } });
    if (url.pathname === "/api/vaults") return route.fulfill({ json: { vaults: [] } });
    if (url.pathname === "/api/providers/defaults") return route.fulfill({ json: { chat: {}, embedding: {} } });
    if (url.pathname === "/api/providers") return route.fulfill({ json: { providers: [] } });
    if (url.pathname === "/api/import-tasks") return route.fulfill({ json: { tasks: [] } });
    if (url.pathname === "/api/sessions" && request.method() === "GET") {
      return route.fulfill({ json: { sessions: [session], page: 1, page_size: 25, total: 1, total_pages: 1 } });
    }
    if (url.pathname === "/api/sessions/session-citation" && request.method() === "GET") {
      return route.fulfill({ json: {
        session,
        messages: [{ message_id: "message-1", role: "user", content: "继续核验", created_at: "2026-07-23T00:00:00+00:00" }],
        task_states: [], citations: [citation], generation_results: [answer], task_snapshots: []
      } });
    }
    if (url.pathname === "/api/sessions/session-citation/generation-results/answer-1" && request.method() === "PATCH") {
      const command = request.postDataJSON();
      answer = { ...answer, content: command.content, content_origin: command.content_origin, status: "pending-verification" };
      citation = { ...citation, status: "pending-verification", invalidation_reason: "段落内容已修改，需重新检索核验。" };
      return route.fulfill({ json: { result: answer } });
    }
    if (url.pathname === "/api/sessions/session-citation/generation-results/answer-1/reverify" && request.method() === "POST") {
      answer = { ...answer, status: "valid", content_origin: "user-content" };
      citation = { ...citation, status: "valid", invalidation_reason: null };
      return route.fulfill({ json: { result: answer } });
    }
    return route.fallback();
  });

  await page.goto("/");
  await page.getByRole("link", { name: "会话", exact: true }).click();
  const conversation = page.getByLabel("会话内容");
  await expect(conversation).toContainText("可追溯的本地证据摘要。");
  await expect(conversation).toContainText("[1]");
  await expect(conversation).not.toContainText("Provider：provider-1");
  await expect(conversation).not.toContainText("用户约束：仅限本地资料。");
  await expect(page.getByRole("complementary", { name: "应用证据" })).toContainText("unit.md");

  const editButton = page.getByRole("button", { name: "编辑回答", exact: true });
  await editButton.focus();
  await page.keyboard.press("Enter");
  await page.getByLabel("编辑回答").fill("已编辑的本地证据摘要。");
  await page.getByRole("button", { name: "保存并标为待核验", exact: true }).click();
  await expect(page.getByText("内容已更新，需重新确认。", { exact: true })).toBeVisible();
  await expect(page.getByText("待核验", { exact: true })).toBeVisible();

  const reverifyButton = page.getByRole("button", { name: "重新确认", exact: true });
  await reverifyButton.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("内容已重新确认。", { exact: true })).toBeVisible();
  await expect(page.getByText("有效", { exact: true })).toBeVisible();
});

test("moves uncited source-lookup material into the application-evidence pane", async ({ page }) => {
  const session = {
    session_id: "session-1",
    title: "来源对照",
    selected_vault_id: "vault-a",
    selected_vault_label: "Session Vault",
    selected_provider_id: "provider-1",
    selected_provider_label: "Local",
    selected_model_id: "chat-1",
    selected_model_label: "chat-1",
    scope_kind: "vault",
    scope_path: null,
    message_count: 1,
    created_at: "2026-07-23T00:00:00+00:00",
    updated_at: "2026-07-23T00:00:00+00:00",
    last_activity_at: "2026-07-23T00:00:00+00:00"
  };
  const retrievalResult = {
    result_id: "result-1",
    task_id: "task-1",
    snapshot_id: "snapshot-1",
    vault_id: "vault-a",
    status: "completed",
    summary: "已找到可对照的本地证据。",
    recovery_action: null,
    retrieval_duration_ms: 3,
    generation_duration_ms: 0,
    source_independence_available: true,
    independent_source_count: 2,
    source_groups: [
      {
        vault_id: "vault-a",
        identity_kind: "derived",
        basis: "vault-source-id",
        source_id: "source-lesson",
        content_sha256: null,
        evidence_ordinals: [1, 2],
        relative_paths: ["notes/lesson-a.md", "notes/lesson-b.md"]
      },
      {
        vault_id: "vault-a",
        identity_kind: "native",
        basis: "vault-content-sha256",
        source_id: null,
        content_sha256: "c".repeat(64),
        evidence_ordinals: [3],
        relative_paths: ["notes/teacher-note.md"]
      }
    ],
    evidences: [
      {
        ordinal: 1,
        identity_kind: "derived",
        relative_path: "notes/lesson-a.md",
        content_sha256: "a".repeat(64),
        source_id: "source-lesson",
        source_content_hash: "b".repeat(64),
        source_path: "sources/lesson.pdf",
        heading: "第一章",
        location: "heading: 第一章; page: 1",
        page: 1,
        excerpt: "同一教材的第一条证据。",
        matched_channels: ["keyword"]
      },
      {
        ordinal: 2,
        identity_kind: "derived",
        relative_path: "notes/lesson-b.md",
        content_sha256: "d".repeat(64),
        source_id: "source-lesson",
        source_content_hash: "b".repeat(64),
        source_path: "sources/lesson.pdf",
        heading: "第二章",
        location: "heading: 第二章; page: 2",
        page: 2,
        excerpt: "同一教材的第二条证据。",
        matched_channels: ["keyword"]
      },
      {
        ordinal: 3,
        identity_kind: "native",
        relative_path: "notes/teacher-note.md",
        content_sha256: "c".repeat(64),
        source_id: null,
        source_content_hash: null,
        source_path: null,
        heading: "教师笔记",
        location: "heading: 教师笔记",
        page: null,
        excerpt: "独立笔记提供另一种说法。",
        matched_channels: ["keyword"]
      }
    ]
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/health") return route.fulfill({ json: { service: "obsidian-personal-knowledge-platform" } });
    if (pathname === "/api/session") return route.fulfill({ json: { status: "ok" } });
    if (pathname === "/api/vaults") return route.fulfill({ json: { vaults: [{
      vault_id: "vault-a", display_name: "Session Vault", managed_root_relative_path: "platform",
      authorization_status: "active", access_status: "available"
    }] } });
    if (pathname === "/api/providers/defaults") return route.fulfill({ json: { chat: {}, embedding: {} } });
    if (pathname === "/api/providers") return route.fulfill({ json: { providers: [{
      provider_id: "provider-1", name: "Local", credential_configured: true,
      verification: { is_verified: true },
      models: [{ model_id: "chat-1", model_type: "chat", is_discovered: true, verification: { ok: true } }]
    }] } });
    if (pathname === "/api/import-tasks") return route.fulfill({ json: { tasks: [] } });
    if (pathname === "/api/sessions" && request.method() === "GET") {
      return route.fulfill({ json: { sessions: [session], page: 1, page_size: 25, total: 1, total_pages: 1 } });
    }
    if (pathname === "/api/sessions/session-1" && request.method() === "GET") {
      return route.fulfill({ json: {
        session,
        messages: [{ message_id: "message-1", role: "assistant", content: "请对照资料。" }],
        task_states: [], citations: [], generation_results: [], attachments: [], task_snapshots: [],
        retrieval_results: [retrievalResult]
      } });
    }
    return route.fallback();
  });

  await page.goto("/");
  await page.getByRole("link", { name: "会话", exact: true }).click();

  const conversation = page.getByLabel("会话内容");
  const evidencePane = page.getByRole("complementary", { name: "应用证据" });
  await expect(conversation).toContainText("暂未生成可用回答。");
  await expect(conversation).toContainText("[1]");
  await expect(conversation).not.toContainText("同一教材的第一条证据。");
  await expect(conversation).not.toContainText("独立来源：2");
  const evidenceRows = evidencePane.locator(".evidence-row");
  await expect(evidencePane.locator(".session-citation")).toHaveCount(3);
  await expect(evidenceRows).toHaveCount(3);
  await evidenceRows.first().locator("summary").click();
  await expect(evidencePane).not.toContainText("知识库：Session Vault");
  await expect(evidenceRows.first()).toContainText("原始资料：lesson.pdf");
  await expect(evidencePane).not.toContainText("notes/lesson-a.md");
  await expect(evidenceRows.first()).not.toContainText("source-lesson");
  await expect(evidenceRows.first()).not.toContainText("b".repeat(64));
});

test("shows completeness coverage gaps and stale sources without claiming completion", async ({ page }) => {
  const session = {
    session_id: "session-completeness", title: "完整性", selected_vault_id: "vault-a",
    selected_vault_label: "Session Vault", selected_provider_id: "provider-1",
    selected_provider_label: "Local", selected_model_id: "chat-1", selected_model_label: "chat-1",
    scope_kind: "vault", scope_path: null, message_count: 1,
    created_at: "2026-07-23T00:00:00+00:00", updated_at: "2026-07-23T00:00:00+00:00",
    last_activity_at: "2026-07-23T00:00:00+00:00"
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/health") return route.fulfill({ json: { service: serviceName } });
    if (pathname === "/api/session") return route.fulfill({ json: { status: "ok" } });
    if (pathname === "/api/vaults") return route.fulfill({ json: { vaults: [{
      vault_id: "vault-a", display_name: "Session Vault", managed_root_relative_path: "platform",
      authorization_status: "active", access_status: "available"
    }] } });
    if (pathname === "/api/providers/defaults") return route.fulfill({ json: { chat: {}, embedding: {} } });
    if (pathname === "/api/providers") return route.fulfill({ json: { providers: [] } });
    if (pathname === "/api/import-tasks") return route.fulfill({ json: { tasks: [] } });
    if (pathname === "/api/sessions" && request.method() === "GET") {
      return route.fulfill({ json: { sessions: [session], page: 1, page_size: 25, total: 1, total_pages: 1 } });
    }
    if (pathname === "/api/sessions/session-completeness" && request.method() === "GET") {
      return route.fulfill({ json: {
        session, messages: [], task_states: [], citations: [], generation_results: [], attachments: [], retrieval_results: [],
        task_snapshots: [{
          snapshot_id: "snapshot-completeness", task_id: "task-completeness", vault_id: "vault-a",
          intent: "completeness", status: "invalidated", scope_kind: "vault", source_count: 1,
          source_digest: "a".repeat(64), index_status: "healthy", outbound_scope_summary: "尚未发送",
          coverage: { planned_count: 1, excluded_count: 1, uncovered_count: 0 }, invalidation_reason: "来源内容已变化。"
        }],
        completeness_results: [{
          result_id: "result-completeness", task_id: "task-completeness", snapshot_id: "snapshot-completeness",
          vault_id: "vault-a", status: "source-changed", summary: "来源已变化，不能继续作为当前完整结果。",
          recovery_action: "重新准备任务。", invalidation_reason: "来源内容已变化。",
          coverage: [
            { ordinal: 1, status: "processed", identity_kind: "native", relative_path: "notes/unit.md", content_sha256: "a".repeat(64), location: "heading: Unit; page: 1", page: 1, excerpt: "first word" },
            { ordinal: 2, status: "excluded", identity_kind: "native", relative_path: "notes/excluded.md", content_sha256: "b".repeat(64), location: "heading: Excluded", page: null, excerpt: null, reason: "内容被当前排除规则确认排除。" }
          ]
        }]
      } });
    }
    return route.fallback();
  });

  await page.goto("/");
  await page.getByRole("link", { name: "会话", exact: true }).click();
  await expect(page.getByLabel("来源已变化")).toContainText("不能继续作为当前完整结果");
  await expect(page.getByText("排除 1 项", { exact: true })).toBeVisible();
  const excluded = page.getByRole("complementary", { name: "应用证据" })
    .locator(".session-citation")
    .filter({ hasText: "excluded.md" })
    .locator(".evidence-row");
  await excluded.locator("summary").click();
  await expect(excluded).toContainText("内容被当前排除规则确认排除。");
  await expect(page.getByText("完整完成", { exact: true })).toHaveCount(0);
});

test("keeps the current session detail when an earlier selection resolves last", async ({ page }) => {
  const session = (sessionId, title, vault) => ({
    session_id: sessionId,
    title,
    selected_vault_id: vault.toLowerCase(),
    selected_vault_label: vault,
    selected_provider_id: "provider-1",
    selected_provider_label: "Local",
    selected_model_id: "chat-1",
    selected_model_label: "chat-1",
    scope_kind: "vault",
    scope_path: null,
    message_count: 0,
    created_at: "2026-07-23T00:00:00+00:00",
    updated_at: "2026-07-23T00:00:00+00:00",
    last_activity_at: "2026-07-23T00:00:00+00:00"
  });
  const first = session("session-a", "会话 A", "Vault A");
  const second = session("session-b", "会话 B", "Vault B");
  let releaseFirstDetail;

  await page.route("**/api/sessions**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === "GET" && pathname === "/api/sessions") {
      return route.fulfill({ json: { sessions: [first, second], page: 1, page_size: 25, total: 2, total_pages: 1 } });
    }
    if (request.method() === "GET" && pathname === "/api/sessions/session-a") {
      await new Promise((resolve) => {
        releaseFirstDetail = async () => {
          await route.fulfill({ json: { session: first, messages: [{ message_id: "message-a", role: "assistant", content: "A 的内容" }], task_states: [], citations: [], generation_results: [], attachments: [] } });
          resolve();
        };
      });
      return;
    }
    if (request.method() === "GET" && pathname === "/api/sessions/session-b") {
      return route.fulfill({ json: { session: second, messages: [{ message_id: "message-b", role: "assistant", content: "B 的内容" }], task_states: [], citations: [], generation_results: [], attachments: [] } });
    }
    return route.fallback();
  });

  await page.goto("/");
  await page.getByRole("link", { name: "会话", exact: true }).click();
  await expect.poll(() => Boolean(releaseFirstDetail)).toBe(true);
  await page.getByRole("button", { name: /会话 B/ }).click();
  await expect(page.getByText("B 的内容", { exact: true })).toBeVisible();
  await releaseFirstDetail();

  await expect(page.getByText("B 的内容", { exact: true })).toBeVisible();
  await expect(page.getByText("A 的内容", { exact: true })).toHaveCount(0);
  await expect(page.locator(".context-location")).toContainText("Vault B");
});
