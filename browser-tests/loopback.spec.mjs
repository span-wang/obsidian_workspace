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

test("uses the current-vault graph as the default workbench without widening other scopes", async ({ page }) => {
  const firstVault = {
    vault_id: "vault-graph-first",
    path: "C:\\fixture\\First Graph Vault",
    authorization_status: "active",
    access_status: "available",
    is_current: true,
    index: { status: "stale", current_count: 2, stale_count: 1, failure_count: 0, pending_count: 0, failed_paths: [], stale_paths: ["notes/old.md"], semantic_status: "unavailable" }
  };
  const secondVault = { ...firstVault, vault_id: "vault-graph-second", path: "C:\\fixture\\Second Graph Vault", is_current: false };
  const primaryGraph = {
    vault_id: firstVault.vault_id,
    nodes: [
      { relative_path: "notes/one.md", title: "one", directory: "notes", tags: ["math"], source: "native" },
      { relative_path: "notes/two.md", title: "two", directory: "notes", tags: [], source: "derived" }
    ],
    edges: [
      { source_path: "notes/one.md", target_path: "notes/two.md", kind: "confirmed", status: "confirmed" },
      { source_path: "notes/two.md", target_path: "notes/one.md", kind: "candidate", status: "pending", review_item_id: "candidate-1", reason: "Shared evidence.", evidence: [{ relative_path: "notes/two.md", location: "line:1", source_locations: ["page:1"] }] }
    ],
    directories: ["notes"],
    tags: ["math"],
    index: firstVault.index
  };
  const secondGraph = { ...primaryGraph, vault_id: secondVault.vault_id, nodes: [{ relative_path: "notes/other.md", title: "other", directory: "notes", tags: [], source: "native" }], edges: [] };
  const refreshedGraph = {
    ...primaryGraph,
    index: { ...firstVault.index, status: "failed", failure_count: 1, failed_paths: ["notes/two.md"] }
  };
  const graphRequests = [];
  let graphRefreshSent = false;

  await page.route("**/api/vaults/vault-graph-first/graph**", async (route) => {
    graphRequests.push(route.request().url());
    const graph = route.request().url().includes("relationship_state=candidate")
      ? { ...primaryGraph, nodes: primaryGraph.nodes, edges: [primaryGraph.edges[1]] }
      : graphRefreshSent ? refreshedGraph : primaryGraph;
    await route.fulfill({ json: { graph } });
  });
  await page.route("**/api/vaults/vault-graph-second/graph**", async (route) => {
    graphRequests.push(route.request().url());
    const graph = route.request().url().includes("relationship_state=")
      ? { ...secondGraph, nodes: [], edges: [] }
      : secondGraph;
    await route.fulfill({ json: { graph } });
  });
  await page.route("**/api/vaults/vault-graph-second/current", async (route) => {
    await route.fulfill({ json: { vault: { ...secondVault, is_current: true } } });
  });
  await page.route("**/api/vaults/*/graph/events", async (route) => {
    const body = graphRefreshSent
      ? ": connected\n\n"
      : "event: graph-refresh\ndata: {\"reason\":\"changed\"}\n\n";
    graphRefreshSent = true;
    await route.fulfill({ contentType: "text/event-stream", body });
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [firstVault, secondVault] } });
  });

  await page.goto("/");
  await expect(page.getByLabel("图谱节点").getByRole("button", { name: /one/ })).toBeVisible();
  await expect.poll(() => graphRequests.filter((url) => url.includes("/api/vaults/vault-graph-first/graph") && !url.includes("/events")).length).toBeGreaterThanOrEqual(2);
  await expect(page.getByText("已确认（实线）：notes/one.md -> notes/two.md")).toBeVisible();
  await expect(page.getByText("失败对象：notes/two.md")).toBeVisible();
  const candidate = page.getByRole("button", { name: "候选（虚线）：notes/two.md -> notes/one.md" });
  await candidate.focus();
  await expect(candidate).toBeFocused();
  await page.getByLabel("按关系状态筛选图谱").selectOption("candidate");
  await expect(page.getByText("候选（虚线）：notes/two.md -> notes/one.md")).toBeVisible();
  await expect(graphRequests.at(-1)).toContain("relationship_state=candidate");
  await page.getByLabel("当前 vault").selectOption("vault-graph-second");
  await expect(page.getByRole("button", { name: /other/ })).toBeVisible();
  expect(graphRequests.at(-1)).not.toContain("relationship_state=");
  await expect(page.getByText("notes/one.md")).toHaveCount(0);
});

test("does not render a stale graph while a vault switch is completing", async ({ page }) => {
  const firstVault = {
    vault_id: "vault-race-first",
    path: "C:\\fixture\\First Race Vault",
    authorization_status: "active",
    access_status: "available",
    is_current: true,
    index: { status: "healthy", current_count: 1, stale_count: 0, failure_count: 0, pending_count: 0, failed_paths: [], stale_paths: [], semantic_status: "unavailable" }
  };
  const secondVault = { ...firstVault, vault_id: "vault-race-second", path: "C:\\fixture\\Second Race Vault", is_current: false };
  const firstGraph = {
    vault_id: firstVault.vault_id,
    nodes: [{ relative_path: "notes/first.md", title: "first", directory: "first-directory", tags: [], source: "native" }],
    edges: [],
    directories: ["first-directory"],
    tags: [],
    index: firstVault.index
  };
  const secondGraph = {
    ...firstGraph,
    vault_id: secondVault.vault_id,
    nodes: [{ relative_path: "notes/second.md", title: "second", directory: "second-directory", tags: [], source: "native" }],
    directories: ["second-directory"],
    index: secondVault.index
  };
  let releaseFirstGraph;
  let releaseSecondGraph;
  let releaseSwitch;

  await page.route("**/api/vaults/vault-race-first/graph**", async (route) => {
    await new Promise((resolve) => {
      releaseFirstGraph = async () => {
        await route.fulfill({ json: { graph: firstGraph } });
        resolve();
      };
    });
  });
  await page.route("**/api/vaults/vault-race-second/graph**", async (route) => {
    await new Promise((resolve) => {
      releaseSecondGraph = async () => {
        await route.fulfill({ json: { graph: secondGraph } });
        resolve();
      };
    });
  });
  await page.route("**/api/vaults/vault-race-second/current", async (route) => {
    await new Promise((resolve) => {
      releaseSwitch = async () => {
        await route.fulfill({ json: { vault: { ...secondVault, is_current: true } } });
        resolve();
      };
    });
  });
  await page.route("**/api/vaults/*/graph/events", async (route) => {
    await route.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" });
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [firstVault, secondVault] } });
  });

  await page.goto("/");
  await expect.poll(() => Boolean(releaseFirstGraph)).toBe(true);
  await page.getByLabel("当前 vault").selectOption(secondVault.vault_id);
  await expect.poll(() => Boolean(releaseSwitch)).toBe(true);
  await releaseFirstGraph();
  await releaseSwitch();
  await expect(page.getByLabel("当前 vault")).toHaveValue(secondVault.vault_id);
  await expect(page.getByLabel("图谱节点").getByRole("button", { name: "first" })).toHaveCount(0);
  await expect(page.getByLabel("按目录筛选图谱").getByRole("option", { name: "first-directory" })).toHaveCount(0);
  await expect.poll(() => Boolean(releaseSecondGraph)).toBe(true);
  await releaseSecondGraph();
  await expect(page.getByRole("button", { name: /second/ })).toBeVisible();
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

test("creates an import task from materials and shows its persistent scan snapshot", async ({ page }) => {
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
        }],
        candidate_link_proposals: [{
          review_item_id: "candidate-1",
          revision: 1,
          vault_id: "vault-import",
          source_item_id: 1,
          source_path: "platform/notes/source-new/01-chapter-one.md",
          target_item_id: 1,
          target_path: "platform/notes/source-new/02-chapter-two.md",
          reason: "两侧都包含可审计术语：algebra、equations。",
          confidence: 0.6,
          source_evidence: {
            relative_path: "platform/notes/source-new/01-chapter-one.md",
            block_location: "unit:0",
            excerpt: "Algebra equations preview.",
            source_locations: ["page:1"]
          },
          target_evidence: {
            relative_path: "platform/notes/source-new/02-chapter-two.md",
            block_location: "unit:3",
            excerpt: "Algebra equations practice.",
            source_locations: ["page:2"]
          },
          is_existing_note_change: false,
          requires_review: true,
          status: "required-check",
          decision: null,
          decision_reason: null,
          stale_reason: null
        }, {
          review_item_id: "candidate-stale",
          revision: 1,
          vault_id: "vault-import",
          source_item_id: 1,
          source_path: "platform/notes/previous-source.md",
          target_item_id: 1,
          target_path: "platform/notes/previous-target.md",
          reason: "两侧都包含可审计术语：algebra。",
          confidence: 0.6,
          source_evidence: {
            relative_path: "platform/notes/previous-source.md",
            block_location: "unit:0",
            excerpt: "Previous algebra evidence.",
            source_locations: ["page:1"]
          },
          target_evidence: {
            relative_path: "platform/notes/previous-target.md",
            block_location: "unit:1",
            excerpt: "Previous algebra target evidence.",
            source_locations: ["page:2"]
          },
          is_existing_note_change: false,
          requires_review: false,
          status: "stale",
          decision: null,
          decision_reason: null,
          stale_reason: "关联笔记提案已更新。"
        }]
      }
    });
  });
  await page.route("**/api/import-tasks/task-import/candidate-links/**", async (route) => {
    await route.fulfill({ json: { task } });
  });
  await page.route("**/api/import-tasks/task-import/events?after=4", async (route) => {
    await route.fulfill({ contentType: "text/event-stream", body: ": keep-alive\n\n" });
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [vault] } });
  });

  const eventSubscription = page.waitForRequest("**/api/import-tasks/task-import/events?after=4");
  await page.goto("/");
  await page.getByRole("link", { name: "资料", exact: true }).click();
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
  await expect(page.getByRole("heading", { name: "候选链接" })).toBeVisible();
  await expect(page.getByText("审核决定仅保存在应用私有状态，尚未写入 Markdown。")).toBeVisible();
  await expect(page.getByText("来源证据（unit:0）：Algebra equations preview.")).toBeVisible();
  await expect(page.getByText("已陈旧，需重新生成")).toBeVisible();
  await expect(page.getByText("陈旧原因：关联笔记提案已更新。")).toBeVisible();
  await expect(
    page.getByLabel("platform/notes/previous-source.md 到 platform/notes/previous-target.md 的候选链接决定理由")
  ).toBeDisabled();
  await expect(page.getByText("必须检查", { exact: true })).toHaveCount(2);
  const candidateReason = page.getByLabel(
    "platform/notes/source-new/01-chapter-one.md 到 platform/notes/source-new/02-chapter-two.md 的候选链接决定理由"
  );
  await candidateReason.focus();
  await expect(candidateReason).toBeFocused();
  await page.getByLabel("候选链接").locator("button.secondary-button:not([disabled])", { hasText: "接受" }).click();
  await expect(page.getByText("候选链接已接受，尚未写入 Markdown。")).toBeVisible();
  await expect(page.getByRole("button", { name: "接受标签" })).toBeVisible();
  await expect(page.getByLabel("分类建议").getByText("必须检查", { exact: true })).toBeVisible();
  const classificationFolder = page.getByLabel("资料项 1 的目标文件夹");
  await expect(classificationFolder).toHaveValue("platform/notes/unclassified");
  await expect(page.getByRole("button", { name: "接受高置信度建议" })).toBeVisible();
  const classificationStatus = page.getByText("没有可批量接受的高置信度建议。", { exact: true });
  await expect(classificationStatus).toBeVisible();
  await expect(classificationStatus).toHaveAttribute("role", "status");
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

test("filters commit review units and submits only the explicitly selectable units", async ({ page }) => {
  const vault = {
    vault_id: "vault-review",
    path: "C:\\fixture\\Review Vault",
    managed_root_relative_path: "platform",
    managed_root: "C:\\fixture\\Review Vault\\platform",
    source_directory: "C:\\fixture\\Review Vault\\platform\\sources",
    note_directory: "C:\\fixture\\Review Vault\\platform\\notes",
    authorization_status: "active",
    access_status: "available",
    index_status: "not-initialized",
    created_at: "2026-07-22T00:00:00+00:00",
    updated_at: "2026-07-22T00:00:00+00:00",
    is_current: true,
    recovery_actions: []
  };
  const task = {
    task_id: "task-review",
    vault_id: vault.vault_id,
    vault_label: "Review Vault",
    scope_label: "review.pdf",
    lifecycle: "waiting-for-review",
    phase: "waiting-for-review",
    current_item_label: null,
    counts: {
      discovered: 3,
      supported: 3,
      skipped: 0,
      unsupported: 0,
      failed: 0,
      new: 3,
      duplicate: 0,
      possible_version: 0,
      identity_failed: 0,
      parsed: 3,
      parse_failed: 0,
      ocr_completed: 3,
      ocr_failed: 0,
      confirmed_gaps: 1,
      required_check: 2,
      derived_notes: 3
    },
    recovery_actions: [],
    failure_reason: null,
    parent_task_id: null,
    created_at: "2026-07-22T00:00:00+00:00",
    updated_at: "2026-07-22T00:00:00+00:00"
  };
  const reviewSnapshot = {
    task_id: task.task_id,
    vault_id: vault.vault_id,
    digest: "a".repeat(64),
    source_hashes: [[1, "b".repeat(64)]],
    existing_file_hashes: [],
    review_items: [
      {
        review_item_id: "parse-blocked",
        unit_id: "source-blocked",
        object_type: "parse",
        risk: "blocking",
        status: "blocking",
        reason: "解析失败，不能提交。"
      },
      {
        review_item_id: "metadata-required",
        unit_id: "existing-note-required",
        object_type: "metadata",
        risk: "required-check",
        status: "pending",
        reason: "既有笔记修改需要明确确认。"
      }
    ],
    units: [
      {
        unit_id: "source-ready",
        source_item_id: 1,
        source_label: "review.pdf",
        kind: "source",
        eligibility_reason: null,
        confirmed_gaps: true,
        files: [
          { relative_path: "platform/sources/review.pdf", kind: "source", modifies_existing: false },
          { relative_path: "platform/notes/review.md", kind: "markdown", modifies_existing: false }
        ]
      },
      {
        unit_id: "source-retry",
        source_item_id: 2,
        source_label: "retry.pdf",
        kind: "source",
        eligibility_reason: null,
        confirmed_gaps: false,
        files: [{ relative_path: "platform/notes/retry.md", kind: "markdown", modifies_existing: false }]
      },
      {
        unit_id: "source-blocked",
        source_item_id: 3,
        source_label: "blocked.pdf",
        kind: "source",
        eligibility_reason: "解析失败，不能提交。",
        confirmed_gaps: false,
        files: [{ relative_path: "platform/notes/blocked.md", kind: "markdown", modifies_existing: false }]
      },
      {
        unit_id: "existing-note-required",
        source_item_id: 4,
        source_label: "existing.md",
        kind: "existing-note",
        eligibility_reason: "既有笔记修改需要明确确认。",
        confirmed_gaps: false,
        files: [{ relative_path: "existing.md", kind: "markdown", modifies_existing: true }]
      }
    ],
    created_at: "2026-07-22T00:00:00+00:00",
    stale_reasons: [],
    remaining_review_count: 2
  };
  let journals = [{ unit_id: "source-retry", status: "failed", reason: "模拟写入失败" }];
  let commitUnitIds = null;
  let refreshed = false;
  const detailPayload = () => ({
    task,
    items: [],
    note_proposals: [],
    classification_suggestions: [],
    metadata_tag_proposals: [],
    candidate_link_proposals: [],
    review_snapshot: reviewSnapshot,
    commit_journals: journals,
    event_cursor: 9
  });

  await page.route("**/api/import-tasks", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { tasks: [task] } });
      return;
    }
    await route.fallback();
  });
  await page.route("**/api/import-tasks/task-review**", async (route) => {
    const url = route.request().url();
    if (url.includes("/events")) {
      await route.fulfill({ contentType: "text/event-stream", body: ": keep-alive\n\n" });
      return;
    }
    if (url.endsWith("/review-snapshot") && route.request().method() === "POST") {
      refreshed = true;
      await route.fulfill({ json: { review_snapshot: reviewSnapshot } });
      return;
    }
    if (url.endsWith("/commit") && route.request().method() === "POST") {
      commitUnitIds = route.request().postDataJSON().unit_ids;
      journals = reviewSnapshot.units
        .filter((unit) => commitUnitIds.includes(unit.unit_id))
        .map((unit) => ({ unit_id: unit.unit_id, status: "committed", reason: null }));
      task.lifecycle = "complete";
      task.phase = "complete";
      await route.fulfill({ json: { task, commit_journals: journals } });
      return;
    }
    await route.fulfill({ json: detailPayload() });
  });
  await page.route("**/api/vaults", async (route) => {
    await route.fulfill({ json: { vaults: [vault] } });
  });

  await page.goto("/");
  await page.getByRole("link", { name: "任务", exact: true }).click();
  await page.getByRole("button", { name: /^review\.pdf/ }).click();

  const review = page.getByLabel("提交审核");
  await expect(review).toHaveAttribute("aria-live", "polite");
  await expect(page.getByText("仍有 2 个阻断或必须检查项。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "提交所选" })).toBeDisabled();
  await expect(page.getByLabel("选择提交单元 blocked.pdf")).toBeDisabled();
  await expect(page.getByLabel("选择提交单元 existing.md")).toBeDisabled();
  await expect(page.getByText("失败，可重试", { exact: true })).toBeVisible();
  await expect(page.getByText("恢复原因：模拟写入失败", { exact: true })).toBeVisible();

  await page.getByLabel("提交单元筛选").selectOption("existing-note");
  await expect(page.getByText("existing.md", { exact: true })).toBeVisible();
  await expect(page.getByText("review.pdf", { exact: true })).toHaveCount(0);
  await page.getByLabel("提交单元筛选").selectOption("all");
  await page.getByRole("button", { name: "全选可提交" }).click();
  await expect(page.getByLabel("选择提交单元 review.pdf")).toBeChecked();
  await expect(page.getByLabel("选择提交单元 retry.pdf")).toBeChecked();
  await expect(page.getByRole("button", { name: "提交所选" })).toBeEnabled();

  await page.getByRole("button", { name: "刷新快照" }).click();
  await expect.poll(() => refreshed).toBe(true);
  await expect(page.getByText("审核快照已刷新。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "提交所选" }).click();
  await expect.poll(() => commitUnitIds).toEqual(["source-ready", "source-retry"]);
  await expect(page.getByText("提交结果已记录。", { exact: true })).toBeVisible();
  await expect(page.getByText("已提交", { exact: true })).toHaveCount(2);
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
            ? [{ message_id: "message-1", role: "assistant", content: "会话详情已加载。" }]
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
  await expect(page.getByLabel("会话内容").getByText(/所用 vault：数学资料/)).toBeVisible();
  await expect(page.getByText("会话详情已加载。", { exact: true })).toBeVisible();
  await expect(page.getByText("notes/algebra.md", { exact: true })).toBeVisible();
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

test("requires a saved session context before composer actions and announces attachment changes", async ({ page }) => {
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
  const attachments = [];
  const taskSnapshots = [];
  const retrievalResults = [];
  let previewRequests = 0;
  const vaults = [
    { vault_id: "vault-a", display_name: "Session Vault A", managed_root_relative_path: "platform", authorization_status: "active", access_status: "available" },
    { vault_id: "vault-b", display_name: "Session Vault B", managed_root_relative_path: "platform", authorization_status: "active", access_status: "available" }
  ];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/health") return route.fulfill({ json: { service: "obsidian-personal-knowledge-platform" } });
    if (url.pathname === "/api/session") return route.fulfill({ json: { status: "ok" } });
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
      return route.fulfill({ json: { session, messages: [], task_states: [], citations: [], generation_results: [], attachments, task_snapshots: taskSnapshots, retrieval_results: retrievalResults } });
    }
    if (url.pathname === "/api/sessions/session-1/context" && request.method() === "PATCH") {
      const context = request.postDataJSON();
      session = {
        ...session,
        selected_vault_id: context.vault_id,
        selected_vault_label: context.vault_id === "vault-b" ? "Vault B" : "Vault A",
        selected_provider_id: context.provider_id,
        selected_provider_label: "Local",
        selected_model_id: context.model_id,
        selected_model_label: context.model_id,
        scope_kind: context.scope_kind,
        scope_path: context.scope_path
      };
      return route.fulfill({ json: { session } });
    }
    if (url.pathname === "/api/sessions/session-1/attachments/select") {
      return route.fulfill({ json: { selection_id: "selection-1", label: "handout.md" } });
    }
    if (url.pathname === "/api/sessions/session-1/attachments" && request.method() === "POST") {
      const attachment = { attachment_id: "attachment-1", filename: "handout.md", status: "available" };
      attachments.push(attachment);
      return route.fulfill({ json: { attachments: [attachment] } });
    }
    if (url.pathname === "/api/sessions/session-1/attachments/attachment-1" && request.method() === "DELETE") {
      attachments.splice(0, attachments.length);
      return route.fulfill({ json: { status: "removed" } });
    }
    if (url.pathname === "/api/sessions/session-1/task-preview" && request.method() === "POST") {
      previewRequests += 1;
      if (previewRequests === 2) {
        return route.fulfill({ json: { preview: {
          intent: "source-lookup", intent_source: "auto", vault_id: session.selected_vault_id,
          scope_kind: session.scope_kind, scope_path: session.scope_path,
          provider_id: session.selected_provider_id, model_id: session.selected_model_id,
          index_status: "provider-model-unavailable", index_updated_at: null,
          exclusion_summary: "无法读取排除项。", outbound_scope_summary: "尚未发送；恢复不可用对象后重新准备任务。",
          source_count: 0, source_digest: "a".repeat(64), source_sample: [], is_ready: false,
          blocking_reason: "所选 Provider/Model 不可用。", recovery_action: "选择已验证的 chat Model 后重试。"
        } } });
      }
      return route.fulfill({ json: { preview: {
        intent: "source-lookup", intent_source: "auto", vault_id: session.selected_vault_id,
        scope_kind: session.scope_kind, scope_path: session.scope_path,
        provider_id: session.selected_provider_id, model_id: session.selected_model_id,
        index_status: "healthy", index_updated_at: "2026-07-23T00:00:00+00:00",
        exclusion_summary: "无排除项。", outbound_scope_summary: "尚未发送；实际检索块将在执行前按任务快照申请或核验授权。",
        source_count: 0, source_digest: "a".repeat(64), source_sample: [], is_ready: true,
        blocking_reason: null, recovery_action: null
      } } });
    }
    if (url.pathname === "/api/sessions/session-1/tasks" && request.method() === "POST") {
      const { content } = request.postDataJSON();
      const snapshot = {
        snapshot_id: "snapshot-1", task_id: "task-1", intent: "source-lookup", status: "prepared",
        scope_kind: "vault", scope_path: null, source_count: 0, source_digest: "a".repeat(64),
        index_status: "healthy", outbound_scope_summary: "尚未发送", content
      };
      taskSnapshots.push(snapshot);
      return route.fulfill({ json: { snapshot } });
    }
    if (url.pathname === "/api/sessions/session-1/tasks/task-1/execute" && request.method() === "POST") {
      taskSnapshots[0].status = "completed";
      const result = {
        result_id: "result-1", task_id: "task-1", snapshot_id: "snapshot-1", status: "no-evidence",
        summary: "健康索引与有效范围内未找到可支持该请求的知识库证据。",
        recovery_action: "修改问题或范围后重新准备任务。",
        retrieval_duration_ms: 3, generation_duration_ms: 0, evidences: []
      };
      retrievalResults.push(result);
      return route.fulfill({ json: { result } });
    }
    return route.fallback();
  });

  await page.goto("/");
  await page.getByRole("link", { name: "会话", exact: true }).click();
  await expect(page.getByLabel("会话输入")).toBeVisible();
  await expect(page.getByText("所用 vault：Session Vault A", { exact: true })).toBeVisible();
  await expect(page.getByLabel("选择 vault").locator("option")).toHaveText(["选择 vault", "Session Vault A", "Session Vault B"]);

  await page.getByLabel("选择 vault").selectOption("vault-b");
  await page.getByLabel("选择 Model").selectOption(JSON.stringify(["provider-1", "chat-1"]));
  const composer = page.getByLabel("输入问题或继续创作");
  await composer.fill("第一行");
  await composer.press("Shift+Enter");
  await composer.pressSequentially("第二行");
  await expect(composer).toHaveValue("第一行\n第二行");
  await expect(page.getByRole("button", { name: "准备任务", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "添加附件", exact: true })).toBeDisabled();

  await page.getByRole("button", { name: "保存语境", exact: true }).click();
  await expect(page.getByText("会话语境已保存。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "准备任务", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "添加附件", exact: true }).click();
  await expect(page.getByText("附件已添加。", { exact: true })).toBeVisible();
  const removeAttachment = page.getByRole("button", { name: "移除附件 handout.md" });
  await removeAttachment.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("附件已移除。", { exact: true })).toBeVisible();
  await expect(removeAttachment).toHaveCount(0);

  await page.getByRole("button", { name: "准备任务", exact: true }).click();
  await expect(page.getByText("执行前范围：原文定位（自动识别）", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "固定快照", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "准备任务", exact: true }).click();
  await expect(page.getByText("所选 Provider/Model 不可用。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "固定快照", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "准备任务", exact: true }).click();
  await page.getByRole("button", { name: "固定快照", exact: true }).click();
  await expect(page.getByText("任务快照已固定，等待后续检索执行。", { exact: true })).toBeVisible();
  await expect(page.getByText("任务 原文定位：已准备", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "执行检索", exact: true }).click();
  await expect(page.getByLabel("未找到证据").getByText("健康索引与有效范围内未找到可支持该请求的知识库证据。", { exact: true })).toBeVisible();
  await expect(page.getByText("检索 3 ms；生成 0 ms（未调用 Model）", { exact: true })).toBeVisible();
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

test("keeps the newly selected session visible when an earlier execution finishes", async ({ page }) => {
  const session = (sessionId, title, vault) => ({
    session_id: sessionId,
    title,
    selected_vault_id: vault.toLowerCase().replace(" ", "-"),
    selected_vault_label: vault,
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
  });
  const first = session("session-a", "会话 A", "Vault A");
  const second = session("session-b", "会话 B", "Vault B");
  const detail = (item, content, snapshots = []) => ({
    session: item,
    messages: [{ message_id: `message-${item.session_id}`, role: "assistant", content }],
    task_states: [],
    citations: [],
    generation_results: [],
    attachments: [],
    task_snapshots: snapshots,
    retrieval_results: []
  });
  const firstSnapshot = {
    snapshot_id: "snapshot-a",
    task_id: "task-a",
    vault_id: "vault-a",
    intent: "source-lookup",
    status: "prepared",
    scope_kind: "vault",
    source_count: 1,
    source_digest: "a".repeat(64),
    index_status: "healthy",
    outbound_scope_summary: "尚未发送"
  };
  let releaseExecution;

  await page.route("**/api/sessions**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === "GET" && pathname === "/api/sessions") {
      return route.fulfill({ json: { sessions: [first, second], page: 1, page_size: 25, total: 2, total_pages: 1 } });
    }
    if (request.method() === "GET" && pathname === "/api/sessions/session-a") {
      return route.fulfill({ json: detail(first, "A 的内容", [firstSnapshot]) });
    }
    if (request.method() === "GET" && pathname === "/api/sessions/session-b") {
      return route.fulfill({ json: detail(second, "B 的内容") });
    }
    if (request.method() === "POST" && pathname === "/api/sessions/session-a/tasks/task-a/execute") {
      await new Promise((resolve) => {
        releaseExecution = async () => {
          await route.fulfill({ json: { result: {
            result_id: "result-a", task_id: "task-a", snapshot_id: "snapshot-a", status: "no-evidence",
            summary: "健康索引与有效范围内未找到可支持该请求的知识库证据。",
            recovery_action: "修改问题或范围后重新准备任务。",
            retrieval_duration_ms: 1, generation_duration_ms: 0, evidences: []
          } } });
          resolve();
        };
      });
      return;
    }
    return route.fallback();
  });

  await page.goto("/");
  await page.getByRole("link", { name: "会话", exact: true }).click();
  await expect(page.getByText("A 的内容", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "执行检索", exact: true }).click();
  await expect.poll(() => Boolean(releaseExecution)).toBe(true);
  await page.getByRole("button", { name: /会话 B/ }).click();
  await expect(page.getByText("B 的内容", { exact: true })).toBeVisible();
  await releaseExecution();

  await expect(page.getByText("B 的内容", { exact: true })).toBeVisible();
  await expect(page.getByText("A 的内容", { exact: true })).toHaveCount(0);
  await expect(page.getByText("检索已完成，证据已刷新。", { exact: true })).toHaveCount(0);
});
