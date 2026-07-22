import { spawn } from "node:child_process";
import { once } from "node:events";
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

  await expect(page).toHaveURL(`${baseUrl}/`);
  await expect(page.getByRole("heading")).toHaveText("工作台");
  await expect(page.getByTestId("health-status")).toHaveText("本机服务可用");
  await expect(page.getByTestId("session-status")).toHaveText("本机会话已建立");
  await expect(page.getByRole("navigation").getByRole("link")).toHaveCount(5);
  expect(healthRequests).toEqual([`${baseUrl}/api/health`]);
  expect(sessionRequests).toEqual([`${baseUrl}/api/session`]);
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

  await expect(page.getByText("本机 / 当前 vault：Second Vault")).toBeVisible();
  await page.getByRole("button", { name: "返回 vault 列表" }).click();
  await expect(page.locator(".vault-list .row-status").filter({ hasText: "当前" })).toHaveCount(1);
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

test("edits vault exclusions and keeps the outbound authorization state visible", async ({ page }) => {
  let policy = {
    outbound_mode: "ask-each-task",
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
    if (url.endsWith("/mode")) {
      const payload = route.request().postDataJSON();
      policy = { ...policy, outbound_mode: payload.outbound_mode, policy_revision: policy.policy_revision + 1 };
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
  await expect(page.getByRole("heading", { name: "资料排除与外发授权" })).toBeVisible();
  await expect(page.getByTestId("outbound-status")).toHaveText("外发：每次询问");

  await page.getByLabel("外发方式").selectOption("always-allow");
  await expect(page.getByTestId("outbound-status")).toHaveText("外发：始终允许");
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

test("creates an import task from the workbench and shows its persistent scan snapshot", async ({ page }) => {
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
    lifecycle: "waiting-for-review",
    phase: "waiting-for-review",
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
      required_check: 1,
      derived_notes: 1
    },
    recovery_actions: [],
    failure_reason: null,
    parent_task_id: null,
    created_at: "2026-07-21T00:00:00+00:00",
    updated_at: "2026-07-21T00:00:00+00:00"
  };

  await page.route("**/api/import-selections/files", async (route) => {
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
          parse_confidence: 0.72,
          parse_issue_count: 1,
          parse_locator_summary: "page 2",
          parse_issue_summary: "Table columns need review.",
          ocr_status: "required-check",
          ocr_confidence: 56,
          ocr_issue_count: 1,
          ocr_locator_summary: "page 2 box:10,20,60,12",
          ocr_issue_summary: "OCR confidence needs review.",
          ocr_targets: [{
            target_id: "page:2",
            label: "Page 2",
            locator_summary: "page 2",
            engine: "paddleocr-vl-1.6",
            status: "completed",
            confidence: 56,
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
          risks: ["Table columns need review."],
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
        metadata_tag_proposals: [{
          item_id: 1,
          revision: 1,
          vault_id: "vault-import",
          proposal_revision: 1,
          content_sha256: "b".repeat(64),
          source_type: "pdf",
          source_file: "book.pdf",
          ingested_at: "2026-07-22T00:00:00+00:00",
          processing_status: "waiting-for-review",
          domain: "unclassified",
          domain_confidence: 0.4,
          requires_review: true,
          decision: null,
          decision_reason: null,
          tags: [{
            name: "unclassified",
            confidence: 0.4,
            status: "required-check",
            is_new: true,
            document_paths: ["platform/notes/source-new/index.md"],
            note_paths: ["platform/notes/source-new/01-chapter-one.md"],
            reason: "New tag proposed from the private domain suggestion."
          }]
        }]
      }
    });
  });
  await page.route("**/api/import-tasks/task-import/events?after=4", async (route) => {
    await route.fulfill({ contentType: "text/event-stream", body: ": keep-alive\n\n" });
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [vault] } });
  });

  const eventSubscription = page.waitForRequest("**/api/import-tasks/task-import/events?after=4");
  await page.goto("/");
  await expect(page.getByText("目标 vault：Import Vault")).toBeVisible();
  await page.getByRole("button", { name: "选择文件", exact: true }).click();

  await expect(page.getByRole("heading", { name: "任务", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "导入任务 task-import" })).toBeVisible();
  await eventSubscription;
  await expect(page.getByText("当前阶段：等待审核")).toBeVisible();
  await expect(page.getByText("解析：已完成")).toBeVisible();
  await expect(page.getByText("新资料 1")).toBeVisible();
  await expect(page.getByText("可能版本 1")).toBeVisible();
  await expect(page.getByText("已解析 1")).toBeVisible();
  await expect(page.getByText("OCR 完成 1")).toBeVisible();
  await expect(page.getByText("支持 · 新资料 · 已解析 · OCR 待审核")).toBeVisible();
  await expect(page.getByLabel("Page 2 的处理理由")).toBeVisible();
  await expect(page.getByRole("button", { name: "重试此页" })).toBeVisible();
  await expect(page.locator(".progress-sequence").getByText("待审核问题 1")).toBeVisible();
  await expect(page.getByText("证据位置：page 2")).toBeVisible();
  await expect(page.getByText("Table columns need review.", { exact: true })).toBeVisible();
  await expect(page.getByText("待审核确认")).toBeVisible();
  await expect(page.getByText("PDF（电子/扫描待识别）")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Markdown 提案" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "分类建议" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "元数据与标签" })).toBeVisible();
  await expect(page.getByRole("button", { name: "接受标签" })).toBeVisible();
  await expect(page.getByText("必须检查", { exact: true })).toBeVisible();
  const classificationFolder = page.getByLabel("资料项 1 的目标文件夹");
  await expect(classificationFolder).toHaveValue("platform/notes/unclassified");
  await expect(page.getByRole("button", { name: "接受高置信度建议" })).toBeVisible();
  await expect(
    page.getByLabel("分类建议").getByRole("button", { name: "确认排除" })
  ).toBeVisible();
  await expect(page.getByText("派生笔记提案（版本 1）")).toBeVisible();
  await expect(page.getByText("来源：第 1 页")).toBeVisible();
  await expect(page.locator(".markdown-preview").nth(1)).toContainText("Preview text");
  const mergeButton = page.getByRole("button", { name: "合并 Chapter One 与下一篇笔记" });
  await expect(mergeButton).toBeVisible();
  await expect(page.getByLabel("Chapter One 的安全拆分边界")).toBeVisible();
  await expect(page.getByRole("button", { name: "在 Chapter One 的安全边界拆分笔记" })).toBeVisible();
  await mergeButton.focus();
  await expect(mergeButton).toBeFocused();
  await classificationFolder.focus();
  await expect(classificationFolder).toBeFocused();
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

test("configures a chat-only Provider without requiring an Embedding model", async ({ page }) => {
  let providers = [];
  let defaults = {
    chat: { default: null, status: "unconfigured", reason: "No chat Provider model is selected." },
    embedding: { default: null, status: "unconfigured", reason: "No embedding Provider model is selected." }
  };

  function unverifiedProvider(payload) {
    return {
      provider_id: "provider-test",
      name: payload.name,
      endpoint: payload.endpoint,
      transport: "openai-compatible",
      credential_configured: true,
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
        ...model, verification: { ok: true, reason: null }, verified_at: "2026-07-21T00:02:00+00:00"
      })) };
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
    if (method === "DELETE" && url.endsWith("/provider-test")) {
      providers = [];
      defaults.chat = { default: null, status: "unconfigured", reason: "No chat Provider model is selected." };
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
  const secret = page.getByLabel("凭据");
  await expect(secret).toHaveAttribute("type", "password");
  await page.getByLabel("名称").fill("Cloud AI");
  await page.getByLabel("服务地址").fill("https://provider.example/v1");
  await secret.fill("never-render-this");
  await page.getByRole("button", { name: "添加 Provider" }).last().click();

  await expect(page.getByText("Cloud AI")).toBeVisible();
  await expect(page.getByLabel("凭据")).toHaveCount(0);
  await expect(page.getByText("模型发现：Not yet verified.")).toBeVisible();
  await page.getByRole("button", { name: "测试" }).click();
  await expect(page.getByText("模型发现：通过")).toBeVisible();
  await expect(page.getByText("服务健康：通过")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Embedding 模型" })).toBeVisible();
  await page.getByLabel("model/chat::primary 模型类型").selectOption("chat");
  await page.getByRole("button", { name: "测试模型" }).click();
  await expect(page.getByText("模型验证已完成。")).toBeVisible();
  await page.getByLabel("全局对话/文本生成 Model").selectOption(JSON.stringify(["provider-test", "model/chat::primary"]));
  await expect(page.getByText("对话/文本生成默认 Model 已更新。")).toBeVisible();
  await page.getByLabel("全局对话/文本生成 Model").selectOption("");
  await expect(page.getByText("对话/文本生成默认 Model 已清除。")).toBeVisible();
  const deleteButton = page.getByRole("button", { name: "删除" });
  await deleteButton.focus();
  await deleteButton.click();
  await expect(page.getByRole("dialog", { name: "删除 Provider" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "删除 Provider" })).toBeHidden();
  await expect(deleteButton).toBeFocused();
});
