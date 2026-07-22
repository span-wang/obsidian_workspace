import assert from "node:assert/strict";
import test from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  App,
  ConversionReviewControls,
  HEALTH_ENDPOINT,
  IMPORT_DIRECTORY_SELECTION_ENDPOINT,
  IMPORT_FILES_SELECTION_ENDPOINT,
  ImportTaskCenter,
  IMPORT_TASK_EVENT_NAMES,
  IMPORT_TASKS_ENDPOINT,
  KnowledgeGraphWorkbench,
  LOCAL_SESSION_ENDPOINT,
  NAVIGATION_DESTINATIONS,
  PROVIDERS_ENDPOINT,
  SESSIONS_ENDPOINT,
  SessionManagement,
  TagManagement,
  VaultIndexStatus,
  VAULTS_ENDPOINT
} from "../src/app.js";

test("renders the five-destination local workbench shell", () => {
  const markup = renderToStaticMarkup(React.createElement(App));

  assert.deepEqual(
    NAVIGATION_DESTINATIONS.map((destination) => destination.label),
    ["工作台", "资料", "会话", "任务", "设置"]
  );
  assert.match(markup, /本机知识工作台/);
  assert.match(markup, /工作台/);
  assert.match(markup, /本机服务正在验证/);
  assert.match(markup, /正在加载 vault 授权。/);
});

test("uses relative same-origin endpoints for health and local session checks", () => {
  assert.equal(HEALTH_ENDPOINT, "/api/health");
  assert.equal(LOCAL_SESSION_ENDPOINT, "/api/session");
  assert.equal(VAULTS_ENDPOINT, "/api/vaults");
  assert.equal(PROVIDERS_ENDPOINT, "/api/providers");
  assert.equal(SESSIONS_ENDPOINT, "/api/sessions");
  assert.equal(IMPORT_TASKS_ENDPOINT, "/api/import-tasks");
  assert.equal(IMPORT_FILES_SELECTION_ENDPOINT, "/api/import-selections/files");
  assert.equal(IMPORT_DIRECTORY_SELECTION_ENDPOINT, "/api/import-selections/directory");
  assert.deepEqual(IMPORT_TASK_EVENT_NAMES, [
    "task-update",
    "scan-started",
    "scan-completed",
    "scan-failed",
    "scan-restarted",
    "parse-started",
    "parse-item-completed",
    "parse-item-failed",
    "parse-completed",
    "parse-failed",
    "parse-restarted",
    "conversion-started",
    "conversion-item-selected",
    "conversion-item-rejected",
    "conversion-completed",
    "conversion-failed",
    "conversion-profile-rejected",
    "source-changed",
    "ocr-started",
    "ocr-target-started",
    "ocr-target-completed",
    "ocr-target-failed",
    "ocr-attempt-failed",
    "ocr-not-required",
    "ocr-source-changed",
    "ocr-completed",
    "ocr-failed",
    "ocr-restarted",
    "derivation-started",
    "derivation-item-completed",
    "derivation-completed",
    "derivation-failed",
    "classification-generated",
    "classification-revised",
    "classification-accepted",
    "classification-excluded",
    "metadata-tags-generated",
    "metadata-tags-accepted",
    "metadata-tags-excluded",
    "candidate-links-generated",
    "candidate-links-accepted",
    "candidate-links-excluded",
    "review-snapshot-created",
    "review-snapshot-stale",
    "review-item-decided",
    "commit-started",
    "commit-prepared",
    "commit-unit-committed",
    "commit-unit-failed",
    "commit-partial-completed",
    "commit-partial-failed",
    "commit-completed",
    "indexing-completed"
  ]);
});

test("renders a bounded persistent session list without a composer", () => {
  const markup = renderToStaticMarkup(
    React.createElement(SessionManagement, {
      sessionPage: {
        sessions: [{
          session_id: "session-1",
          title: "代数复习",
          selected_vault_id: null,
          selected_vault_label: null,
          message_count: 0,
          updated_at: "2026-07-22T00:00:00+00:00"
        }],
        page: 1,
        page_size: 25,
        total: 26,
        total_pages: 2
      },
      filters: { query: "", sort: "updated_at", order: "desc" },
      isLoading: false,
      error: "",
      onLoad: () => {},
      onCreate: async () => ({}),
      onRename: async () => ({}),
      onExport: async () => {},
      onDelete: () => {}
    })
  );

  assert.match(markup, /新建会话/);
  assert.match(markup, /aria-label="搜索会话"/);
  assert.match(markup, /代数复习/);
  assert.match(markup, /所用 vault：未设置/);
  assert.match(markup, /上一页/);
  assert.match(markup, /下一页/);
  assert.match(markup, /第 1 \/ 2 页/);
  assert.doesNotMatch(markup, /session-composer/);
});

test("renders conversion retry and typed correction controls only for conversion review items", () => {
  const sharedProps = {
    lifecycle: "waiting-for-review",
    isActing: false,
    onDraftChange: () => {},
    onRetry: () => {},
    onCorrect: () => {}
  };
  const draft = {
    block_id: "block-1",
    kind: "paragraph",
    payload: '{"inline_runs":[{"kind":"text","text":"Corrected"}]}',
    retrieval_projection: "Corrected",
    reason: "Checked against the source."
  };
  const conversionMarkup = renderToStaticMarkup(React.createElement(ConversionReviewControls, {
    ...sharedProps,
    reviewItem: { object_type: "conversion", risk: "required-check", review_item_id: "conversion-1-graph-1-1" },
    draft,
    blocks: [{ block_id: "block-1", kind: "paragraph", locators: [{ type: "pdf-region", page: 1 }] }]
  }));
  const blockedMarkup = renderToStaticMarkup(React.createElement(ConversionReviewControls, {
    ...sharedProps,
    lifecycle: "running",
    reviewItem: { object_type: "conversion", risk: "blocking", review_item_id: "conversion-1-graph-1-1" },
    draft,
    blocks: []
  }));
  const parseMarkup = renderToStaticMarkup(React.createElement(ConversionReviewControls, {
    ...sharedProps,
    reviewItem: { object_type: "parse", risk: "required-check", review_item_id: "parse-1" },
    draft,
    blocks: []
  }));

  assert.match(conversionMarkup, /重试转换/);
  assert.match(conversionMarkup, /保存结构修正/);
  assert.match(conversionMarkup, /<option value="block-1" selected="">paragraph · 第 1 页<\/option>/);
  assert.doesNotMatch(conversionMarkup, /disabled=""/);
  assert.match(blockedMarkup, /重试转换<\/button>/);
  assert.match(blockedMarkup, /disabled=""/);
  assert.equal(parseMarkup, "");
});

test("keeps conversion correction disabled until its typed payload and reason are complete", () => {
  const markup = renderToStaticMarkup(React.createElement(ConversionReviewControls, {
    lifecycle: "waiting-for-review",
    isActing: false,
    reviewItem: { object_type: "conversion", risk: "required-check", review_item_id: "conversion-0-graph-1-1" },
    draft: { block_id: "block-1", kind: "paragraph", payload: "{not-json" },
    blocks: [],
    onDraftChange: () => {},
    onRetry: () => {},
    onCorrect: () => {}
  }));

  assert.match(markup, /修正内容必须是有效 JSON。/);
  assert.match(markup, /role="status"/);
  assert.match(markup, /保存结构修正<\/button>/);
  assert.match(markup, /disabled=""/);
});

test("shows all identity counts in task center rows", () => {
  const markup = renderToStaticMarkup(
    React.createElement(ImportTaskCenter, {
      tasks: [{
        task_id: "task-1",
        scope_label: "book.pdf",
        vault_label: "Vault",
        lifecycle: "queued",
        phase: "waiting-for-next-stage",
        recovery_actions: [],
        counts: {
          discovered: 3,
          new: 1,
          duplicate: 1,
          possible_version: 1,
          identity_failed: 1,
          parsed: 1,
          parse_failed: 0,
          required_check: 1,
          failed: 0
        }
      }],
      error: "",
      isLoading: false,
      selectedTaskId: null,
      onSelect: () => {},
      onTaskChanged: () => {},
      onTaskSnapshot: () => {},
      vault: null
    })
  );

  assert.match(markup, /可能版本 1/);
  assert.match(markup, /识别失败 1/);
  assert.match(markup, /已解析 1/);
  assert.match(markup, /待审核问题 1/);
});

test("offers an accessible deletion action only for non-running import tasks", () => {
  const task = {
    task_id: "task-1",
    scope_label: "book.pdf",
    vault_label: "Vault",
    lifecycle: "complete",
    phase: "completed",
    recovery_actions: [],
    counts: {
      discovered: 1,
      new: 1,
      duplicate: 0,
      possible_version: 0,
      identity_failed: 0,
      parsed: 1,
      parse_failed: 0,
      required_check: 0,
      failed: 0
    }
  };
  const markup = renderToStaticMarkup(
    React.createElement(ImportTaskCenter, {
      tasks: [task],
      error: "",
      isLoading: false,
      selectedTaskId: null,
      onSelect: () => {},
      onTaskChanged: () => {},
      onTaskDeleted: () => {},
      onTaskSnapshot: () => {},
      vault: null
    })
  );
  const runningMarkup = renderToStaticMarkup(
    React.createElement(ImportTaskCenter, {
      tasks: [{ ...task, lifecycle: "running", phase: "scanning" }],
      error: "",
      isLoading: false,
      selectedTaskId: null,
      onSelect: () => {},
      onTaskChanged: () => {},
      onTaskDeleted: () => {},
      onTaskSnapshot: () => {},
      vault: null
    })
  );

  assert.match(markup, /aria-label="删除任务 book\.pdf"/);
  assert.match(markup, />删除<\/button>/);
  assert.doesNotMatch(runningMarkup, /删除任务 book\.pdf/);
});

test("offers a private tag deletion flow without a target tag field", () => {
  const markup = renderToStaticMarkup(
    React.createElement(TagManagement, {
      vault: { vault_id: "vault-1" }
    })
  );

  assert.match(markup, /<option value="delete">删除<\/option>/);
  assert.match(markup, /标签变更先生成私有影响预览；实际 Markdown 写入仍需后续审核提交。/);
});

test("renders index health and explicit recovery controls without exposing content", () => {
  const markup = renderToStaticMarkup(
    React.createElement(VaultIndexStatus, {
      vault: {
        vault_id: "vault-1",
        index: {
          status: "stale",
          updated_at: "2026-07-22T00:00:00+00:00",
          current_count: 3,
          stale_count: 1,
          failure_count: 0,
          semantic_status: "unavailable",
          failed_paths: [],
          stale_paths: ["notes/old.md"],
          pending_count: 1,
          pending_paths: ["notes/replacement.md"]
        }
      },
      onUpdate: () => {}
    })
  );

  assert.match(markup, /索引健康度/);
  assert.match(markup, /失效证据：notes\/old.md/);
  assert.match(markup, /\? 状态：stale/);
  assert.match(markup, /待关联：notes\/replacement.md/);
  assert.match(markup, /确认重新关联/);
  assert.match(markup, /核对变更/);
  assert.match(markup, /重建索引/);
});

test("renders the current-vault graph controls with non-color relationship states", () => {
  const markup = renderToStaticMarkup(
    React.createElement(KnowledgeGraphWorkbench, {
      vaults: [],
      currentVault: null,
      isLoading: false,
      onAddVault: () => {},
      onUpdateVault: () => {}
    })
  );

  assert.match(markup, /添加 vault/);
  assert.match(markup, /知识图谱/);
});
