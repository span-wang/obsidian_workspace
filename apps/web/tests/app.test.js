import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  App,
  applicationEvidenceAnchorId,
  AutomaticMarkdownResults,
  conversationTurns,
  copyPlainText,
  ConversionReviewControls,
  derivedMarkdownPreview,
  HEALTH_ENDPOINT,
  IMPORT_DIRECTORY_SELECTION_ENDPOINT,
  IMPORT_FILES_SELECTION_ENDPOINT,
  loadMarkdownPipeline,
  loadOnlineParseEnabled,
  loadOnlineParseProviderId,
  MARKDOWN_STRUCTURE_BUDGET_ENDPOINT,
  MARKDOWN_PIPELINE_STORAGE_KEY,
  ONLINE_PARSE_ENABLED_STORAGE_KEY,
  ONLINE_PARSE_PROVIDERS_ENDPOINT,
  ONLINE_PARSE_SELECTION_STORAGE_KEY,
  IMPORT_UPLOAD_ENDPOINT,
  ImportTaskCenter,
  ImportParserTag,
  ImportContentComparison,
  IMPORT_TASK_EVENT_NAMES,
  IMPORT_TASKS_ENDPOINT,
  LOCAL_SESSION_ENDPOINT,
  NAVIGATION_DESTINATIONS,
  ProjectionRebuildVerificationPanel,
  ProviderManagement,
  PROVIDERS_ENDPOINT,
  readServerSentEvents,
  RETRIEVAL_MODE_ENDPOINT,
  saveOnlineParseEnabled,
  saveMarkdownPipeline,
  saveOnlineParseProviderId,
  SESSIONS_ENDPOINT,
  SessionManagement,
  WORKBENCH_OVERVIEW_ENDPOINT,
  userFacingEvidenceLocation,
  userFacingEvidenceSource,
  userFacingImportIssue,
  userFacingImportLocation,
  userFacingSourceSample,
  VaultIndexStatus,
  VAULTS_ENDPOINT,
  WorkbenchOverview
} from "../src/app.js";

function visibleText(markup) {
  return markup.replace(/<[^>]*>/g, "");
}

test("changes retrieval mode without writing session history status or reloading session data", () => {
  const testDirectory = dirname(fileURLToPath(import.meta.url));
  const source = readFileSync(resolve(testDirectory, "../src/app.js"), "utf8");
  const sessionComponentStart = source.indexOf("export function SessionManagement");
  const handlerStart = source.indexOf("async function changeRetrievalMode(mode)", sessionComponentStart);
  const handlerEnd = source.indexOf("function retrievalStatusText", handlerStart);
  const handler = source.slice(handlerStart, handlerEnd);
  const appHandlerStart = source.indexOf("async function changeRetrievalMode(mode)", handlerEnd);
  const appHandlerEnd = source.indexOf("const loadSessionDetail", appHandlerStart);
  const appHandler = source.slice(appHandlerStart, appHandlerEnd);

  assert.ok(handlerStart > sessionComponentStart);
  assert.doesNotMatch(handler, /setStatus\(/);
  assert.match(handler, /setRetrievalModeError\(requestError\.message\)/);
  assert.match(handler, /await onRetrievalModeChange\(mode\)/);
  assert.doesNotMatch(appHandler, /loadSessions|loadSessionDetail/);
});

test("formats source metadata for readers without exposing internal identity", () => {
  const internalLocation = "graph:745b58a99c49033e041e15dbef498fafb0a24a450ff32ac6d087c996ed60aec1:1:f50de8acf3da3b02d96907d440f394d268753f4b4409bcc2e631814fe9fb263c#chunk:1";

  assert.equal(userFacingEvidenceLocation({ heading: "第一单元", page: 3, location: internalLocation }), "第一单元 · 第 3 页");
  assert.equal(userFacingEvidenceLocation({ page: 3, location: internalLocation }), "第 3 页");
  assert.equal(userFacingEvidenceLocation({ location: internalLocation }), "");
  assert.equal(userFacingEvidenceLocation({ heading: internalLocation }), "");
  assert.equal(userFacingEvidenceLocation({ location: "chunk:12" }), "");
  assert.equal(userFacingEvidenceLocation({ location: "block:7" }), "");
  assert.equal(userFacingEvidenceLocation({ location: "word/document.xml:42" }), "");
  assert.equal(userFacingEvidenceLocation({ location: "line:42" }), "");
  assert.equal(userFacingEvidenceLocation({ location: "第 2 节" }), "第 2 节");
  assert.equal(userFacingEvidenceSource({ identity_kind: "derived", source_path: "sources/book.pdf" }), "原始资料：book.pdf");
  assert.equal(userFacingEvidenceSource({ identity_kind: "derived", source_path: "sources\\book.pdf" }), "原始资料：book.pdf");
  assert.equal(userFacingEvidenceSource({ identity_kind: "native" }), "来源类型：原生 Markdown");
  assert.equal(userFacingSourceSample({ source_path: "sources/book.pdf", relative_path: "notes/book.md" }), "sources/book.pdf");
  assert.equal(userFacingSourceSample({ relative_path: "notes/native.md" }), "notes/native.md");
  assert.equal(userFacingImportLocation("page 2 box:10,20,60,12"), "第 2 页");
  assert.equal(userFacingImportLocation("word/document.xml:42"), "DOCX 内容");
  assert.equal(userFacingImportLocation("graph:internal#chunk:2"), "");
  assert.equal(userFacingImportLocation("line:42"), "");
  assert.equal(userFacingImportLocation("region:body"), "");
  assert.equal(userFacingImportIssue("page 1 table:1: Table columns need review."), "Table columns need review.");
  assert.equal(userFacingImportIssue("word/document.xml:42: DOCX content needs review."), "DOCX content needs review.");
  assert.equal(userFacingImportIssue(`${internalLocation}: Internal locator needs review.`), "Internal locator needs review.");
  assert.equal(userFacingImportIssue("paragraph:1: Paragraph needs review."), "Paragraph needs review.");
  assert.equal(userFacingImportIssue("image:1: Image description needs review."), "Image description needs review.");
  assert.equal(userFacingImportIssue("table:1/row:1/cell:1: Table needs review."), "Table needs review.");
});

test("marks parsed import items with their selected parser", () => {
  const markup = renderToStaticMarkup(React.createElement(ImportParserTag, { engine: "paddleocr-vl-1.6" }));
  const emptyMarkup = renderToStaticMarkup(React.createElement(ImportParserTag, { engine: null }));

  assert.match(markup, /class="parser-tag"/);
  assert.match(markup, /aria-label="解析器：paddleocr-vl-1\.6"/);
  assert.match(markup, /解析器/);
  assert.match(markup, /paddleocr-vl-1\.6/);
  assert.equal(emptyMarkup, "");
});

test("hides derived-note provenance frontmatter from Markdown previews", () => {
  const markdown = [
    "---",
    "platform_provenance:",
    "  schema_version: 2",
    "  source_id: source-1",
    "---",
    "# Final Markdown",
    "",
    "来源：[[platform/sources/source-1.pdf|原始资料]]",
    "",
    "Only this content is shown."
  ].join("\n");

  assert.equal(derivedMarkdownPreview(markdown), "# Final Markdown\n\nOnly this content is shown.");
  assert.equal(derivedMarkdownPreview("# Native Markdown"), "# Native Markdown");
});

test("renders automatic Markdown results as read-only previews", () => {
  const markup = renderToStaticMarkup(React.createElement(AutomaticMarkdownResults, {
    noteProposals: [
      { kind: "native", item_id: 1, revision: 1, relative_path: "notes/native.md", markdown: "# Native result" },
      {
        kind: "derived",
        item_id: 2,
        revision: 3,
        index_note: { relative_path: "notes/book/index.md", markdown: "# Book index" },
        notes: [{ note_id: "note-1", sequence: 1, title: "Chapter One", relative_path: "notes/book/01.md", markdown: "# Chapter One\n\nParsed body" }]
      }
    ]
  }));

  assert.match(markup, /Markdown 结果/);
  assert.match(markup, /# Native result/);
  assert.match(markup, /# Book index/);
  assert.match(markup, /# Chapter One/);
  assert.match(markup, /Parsed body/);
  assert.doesNotMatch(markup, /提交审核|接受|保存修正/);
});

test("places source parsing beside the matching structured Markdown result", () => {
  const markup = renderToStaticMarkup(React.createElement(ImportContentComparison, {
    items: [{ item_id: 2, label: "book.pdf" }],
    sourceParses: [{
      item_id: 2,
      blocks: [
        { kind: "heading", location: "第 1 页", content: "第一章" },
        { kind: "paragraph", location: "第 1 页", content: "源解析正文。" }
      ]
    }],
    noteProposals: [{
      kind: "derived",
      item_id: 2,
      revision: 3,
      index_note: { relative_path: "notes/book/index.md", markdown: "# Book index" },
      notes: [{ note_id: "note-1", sequence: 1, title: "Chapter One", relative_path: "notes/book/01.md", markdown: "# 第一章\n\n结构化正文。" }]
    }]
  }));

  assert.match(markup, /class="import-content-comparison"/);
  assert.match(markup, /源解析内容/);
  assert.match(markup, /book\.pdf/);
  assert.match(markup, /第一章/);
  assert.match(markup, /源解析正文。/);
  assert.match(markup, /Markdown 结果/);
  assert.match(markup, /结构化正文。/);
});

test("copies only direct answer content without application-evidence markers", async () => {
  const writes = [];
  const clipboard = { writeText: async (value) => writes.push(value) };

  assert.equal(await copyPlainText("可直接使用的内容。\n", clipboard), true);
  assert.deepEqual(writes, ["可直接使用的内容。"]);
  assert.doesNotMatch(writes[0], /\[1\]/);
  assert.equal(await copyPlainText("", clipboard), false);
});

test("consumes fragmented session SSE chunks before the final result", async () => {
  const encoder = new globalThis.TextEncoder();
  const received = [];
  let result;
  const response = {
    ok: true,
    body: new globalThis.ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: chunk\ndata: {"ordinal":1,"content":"先到"}\n\n'));
        controller.enqueue(encoder.encode('event: chunk\ndata: {"ordinal":1,"content":"后到"}\n\nevent: result\ndata: {"result":{"status":"completed"}}\n\n'));
        controller.close();
      }
    })
  };

  await readServerSentEvents(response, {
    onChunk: (payload) => received.push(payload.content),
    onResult: (payload) => { result = payload.result; }
  });

  assert.deepEqual(received, ["先到", "后到"]);
  assert.deepEqual(result, { status: "completed" });
});

test("groups each user question with its following session output for navigation", () => {
  const turns = conversationTurns({
    messages: [
      { message_id: "question-1", role: "user", content: "先解释概念", created_at: "2026-08-04T01:00:00+00:00" },
      { message_id: "answer-1", role: "assistant", content: "概念解释", created_at: "2026-08-04T01:00:01+00:00" },
      { message_id: "question-2", role: "user", content: "再给一个例子", created_at: "2026-08-04T01:01:00+00:00" }
    ],
    generation_results: [
      { result_id: "generation-2", content: "例子说明", created_at: "2026-08-04T01:01:01+00:00" }
    ]
  });

  assert.equal(turns.length, 2);
  assert.equal(turns[0].question, "先解释概念");
  assert.deepEqual(turns[0].entries.map((entry) => entry.key), ["message:question-1", "message:answer-1"]);
  assert.equal(turns[1].question, "再给一个例子");
  assert.deepEqual(turns[1].entries.map((entry) => entry.key), ["message:question-2", "generation:generation-2"]);
});

test("renders the five-destination local workbench shell", () => {
  const markup = renderToStaticMarkup(React.createElement(App));

  assert.deepEqual(
    NAVIGATION_DESTINATIONS.map((destination) => destination.label),
    ["工作台", "资料", "会话", "任务", "设置"]
  );
  assert.match(markup, /本机知识工作台/);
  assert.match(markup, /工作台/);
  assert.match(markup, /本机服务正在验证/);
  assert.match(markup, /正在构建 Vault 全景/);
});

test("uses relative same-origin endpoints for health and local session checks", () => {
  assert.equal(HEALTH_ENDPOINT, "/api/health");
  assert.equal(LOCAL_SESSION_ENDPOINT, "/api/session");
  assert.equal(VAULTS_ENDPOINT, "/api/vaults");
  assert.equal(PROVIDERS_ENDPOINT, "/api/providers");
  assert.equal(MARKDOWN_STRUCTURE_BUDGET_ENDPOINT, "/api/providers/markdown-structuring/budget");
  assert.equal(ONLINE_PARSE_PROVIDERS_ENDPOINT, "/api/online-parse-providers");
  assert.equal(SESSIONS_ENDPOINT, "/api/sessions");
  assert.equal(RETRIEVAL_MODE_ENDPOINT, "/api/retrieval/mode");
  assert.equal(WORKBENCH_OVERVIEW_ENDPOINT, "/api/workbench/overview");
  assert.equal(IMPORT_TASKS_ENDPOINT, "/api/import-tasks");
  assert.equal(IMPORT_FILES_SELECTION_ENDPOINT, "/api/import-selections/files");
  assert.equal(IMPORT_UPLOAD_ENDPOINT, "/api/import-selections/uploads");
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

test("renders a dense all-vault panorama and second-level detail drawer", () => {
  const overview = {
    updated_at: "2026-08-07T09:30:00+00:00",
    vaults: [{
      vault_id: "vault-a",
      display_name: "研究资料",
      authorization_status: "active",
      access_status: "available",
      access_reason: null,
      is_current: true,
      updated_at: "2026-08-07T09:20:00+00:00",
      state: "attention",
      index: {
        status: "stale",
        updated_at: "2026-08-07T09:15:00+00:00",
        current_count: 42,
        stale_count: 2,
        pending_count: 1,
        failure_count: 0,
        semantic_status: "healthy",
        semantic_covered_block_count: 38,
        semantic_eligible_block_count: 42
      },
      tasks: { total: 3, running: 1, attention: 1, completed: 2, latest_at: "2026-08-07T09:18:00+00:00" },
      sessions: { total: 4, latest_at: "2026-08-07T09:19:00+00:00" }
    }],
    attention: [{ kind: "index", vault_id: "vault-a", vault_label: "研究资料", title: "索引需要处理", detail: "失效 2；待关联 1；失败 0。", status: "attention", updated_at: "2026-08-07T09:15:00+00:00", task_id: null }],
    activity: [{ kind: "session", vault_id: "vault-a", vault_label: "研究资料", label: "近期会话", status: "active", updated_at: "2026-08-07T09:19:00+00:00" }]
  };
  const markup = renderToStaticMarkup(React.createElement(WorkbenchOverview, {
    overview,
    isLoading: false,
    error: "",
    selectedVaultId: "vault-a",
    onSelectVault: () => {},
    onRefresh: () => {},
    onNavigate: () => {}
  }));

  assert.match(markup, /aria-label="刷新工作台"/);
  assert.doesNotMatch(markup, /所有 Vault，一处掌握|CONTROL DESK/);
  assert.match(markup, /研究资料/);
  assert.match(markup, /可检索块/);
  assert.match(markup, /优先处理/);
  assert.match(markup, /最近动态/);
  assert.match(markup, /概况/);
  assert.match(markup, /索引/);
  assert.match(markup, /资料任务/);
  assert.doesNotMatch(markup, /图谱/);
  assert.match(markup, /会话/);
  assert.match(markup, /策略/);
  assert.doesNotMatch(markup, /研究资料\/platform/);
});

test("groups model defaults and Provider actions into a scannable settings layout", () => {
  const markup = renderToStaticMarkup(React.createElement(ProviderManagement, {
    providers: [{
      provider_id: "provider-1",
      name: "Rerank Provider",
      endpoint: "https://rerank.example/v1",
      api_mode: "responses",
      credential_configured: false,
      verification: { is_verified: true, discovery: { ok: true }, health: { ok: true } },
      models: [{
        model_id: "rerank-1",
        model_type: "rerank",
        is_discovered: true,
        verification: { ok: true }
      }, {
        model_id: "failed-model",
        model_type: "chat",
        is_discovered: true,
        verification: { ok: false, reason: "Chat model verification could not be completed. Provider TLS connection failed." }
      }, {
        model_id: "unused-provider-model",
        model_type: null,
        is_discovered: true,
        verification: { ok: false, reason: "Not yet verified." }
      }]
    }],
    isLoading: false,
    modelDefaults: {
      chat: { default: null, status: "unconfigured", reason: null },
      embedding: { default: null, status: "unconfigured", reason: null },
      rerank: { default: null, status: "unconfigured", reason: null },
      markdown: { default: null, status: "unconfigured", reason: null }
    },
    onOpenForm: () => {},
    onUpdate: () => {},
    onConfirm: () => {},
    onDefaultsChange: async () => {}
  }));

  assert.match(markup, /class="provider-model-settings"/);
  assert.match(markup, /class="model-default-icon"/);
  assert.match(markup, /候选重排/);
  assert.match(markup, /候选重排默认模型/);
  assert.match(markup, /默认关闭；启用后仅发送允许外发的候选。/);
  assert.match(markup, /Rerank（重排）/);
  assert.match(markup, /failed-model/);
  assert.match(markup, /验证失败/);
  assert.match(markup, /原因：模型验证失败：Provider TLS 连接失败。请检查服务地址或证书后重试。/);
  assert.doesNotMatch(markup, /unused-provider-model/);
  assert.match(markup, /添加模型/);
  assert.match(markup, /Markdown 结构化/);
  assert.match(markup, /Markdown 结构化默认模型/);
  assert.match(markup, /Markdown 分块 Token 预算/);
  assert.match(markup, /在线解析/);
  assert.match(markup, /最小 Token/);
  assert.match(markup, /目标 Token/);
  assert.match(markup, /最大 Token/);
  assert.match(markup, /Rerank Provider \/ rerank-1/);
  assert.match(markup, /aria-label="模型发现：通过"/);
  assert.match(markup, /aria-label="服务健康：通过"/);
  assert.doesNotMatch(markup, /https:\/\/rerank\.example\/v1/);
  assert.doesNotMatch(markup, /API Key：未配置/);
  assert.match(markup, /Responses API/);
  assert.match(markup, /aria-label="测试 Rerank Provider"/);
  assert.match(markup, /aria-label="编辑 Rerank Provider"/);
  assert.match(markup, /aria-label="删除 Rerank Provider"/);
  assert.match(markup, /aria-label="删除模型 rerank-1"/);
  assert.match(markup, /aria-label="删除模型 failed-model"/);
  assert.match(markup, /class="provider-model-identity">[\s\S]*?rerank-1[\s\S]*?aria-label="删除模型 rerank-1"/);
});

test("reads FastAPI detail messages for provider validation failures", () => {
  const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "../src/app.js"), "utf8");

  assert.match(source, /payload\?\.detail\?\.message/);
});

test("renders a bounded three-pane session workspace with a context composer", () => {
  const session = {
    session_id: "session-1",
    title: "代数复习",
    selected_vault_id: "vault-1",
    selected_vault_label: "platform",
    message_count: 1,
    updated_at: "2026-07-22T00:00:00+00:00"
  };
  const markup = renderToStaticMarkup(
    React.createElement(SessionManagement, {
      sessionPage: {
        sessions: [session],
        page: 1,
        page_size: 25,
        total: 26,
        total_pages: 2
      },
      filters: { query: "", sort: "updated_at", order: "desc" },
      isLoading: false,
      error: "",
      selectedSessionId: "session-1",
      selectedDetail: {
        session,
        messages: [{ message_id: "message-1", role: "assistant", content: "先复习二次方程。" }],
        citations: [{
          citation_id: "citation-1",
          result_id: "answer-1",
          snapshot_id: "snapshot-1",
          vault_id: "vault-2",
          identity_kind: "derived",
          source_id: "source-1",
          source_content_hash: "b".repeat(64),
          content_sha256: "a".repeat(64),
          source_path: "sources/algebra.pdf",
          relative_path: "notes/algebra.md",
          location: "graph:745b58a99c49033e041e15dbef498fafb0a24a450ff32ac6d087c996ed60aec1:1:f50de8acf3da3b02d96907d440f394d268753f4b4409bcc2e631814fe9fb263c#chunk:1",
          status: "valid"
        }],
        generation_results: [{
          result_id: "answer-1",
          task_id: "task-1",
          snapshot_id: "snapshot-1",
          status: "valid",
          content: "二次方程可用求根公式求解。",
          content_origin: "model-judgement"
        }],
        task_snapshots: [{
          snapshot_id: "snapshot-1",
          task_id: "task-1",
          vault_id: "vault-2",
          intent: "source-lookup",
          status: "prepared",
          scope_kind: "vault",
          source_count: 1,
          source_digest: "a".repeat(64),
          index_status: "healthy",
          outbound_scope_summary: "尚未发送"
        }],
        retrieval_results: [{
          result_id: "result-1",
          task_id: "task-1",
          snapshot_id: "snapshot-1",
          status: "completed",
          summary: "已在已确认范围内找到 1 条知识库证据；已提交给所选 Model 生成回答。",
          recovery_action: null,
          retrieval_duration_ms: 12,
          generation_duration_ms: 0,
          source_independence_available: true,
          independent_source_count: 2,
          source_groups: [{
            vault_id: "vault-2",
            identity_kind: "derived",
            basis: "vault-source-id",
            source_id: "source-1",
            content_sha256: null,
            evidence_ordinals: [1, 2],
            relative_paths: ["notes/algebra.md", "notes/algebra-examples.md"]
          }, {
            vault_id: "vault-2",
            identity_kind: "native",
            basis: "vault-content-sha256",
            source_id: null,
            content_sha256: "c".repeat(64),
            evidence_ordinals: [3],
            relative_paths: ["notes/teacher-note.md"]
          }],
          evidences: [{
            ordinal: 1,
            identity_kind: "derived",
            relative_path: "notes/algebra.md",
            content_sha256: "a".repeat(64),
            source_id: "source-1",
            source_content_hash: "b".repeat(64),
            source_path: "sources/algebra.pdf",
            heading: "二次方程",
            location: "graph:745b58a99c49033e041e15dbef498fafb0a24a450ff32ac6d087c996ed60aec1:1:f50de8acf3da3b02d96907d440f394d268753f4b4409bcc2e631814fe9fb263c#chunk:1",
            page: 2,
            excerpt: "原始段落摘录。",
            matched_channels: ["keyword", "semantic"]
          }, {
            ordinal: 2,
            identity_kind: "derived",
            relative_path: "notes/algebra-examples.md",
            content_sha256: "d".repeat(64),
            source_id: "source-1",
            source_content_hash: "b".repeat(64),
            source_path: "sources/algebra.pdf",
            heading: "例题",
            location: "heading: 例题; page: 3",
            page: 3,
            excerpt: "同一来源的第二条证据。",
            matched_channels: ["keyword"]
          }, {
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
            excerpt: "另一个独立来源的说法。",
            matched_channels: ["keyword"]
          }]
        }]
      },
      isDetailLoading: true,
      detailError: "",
      onLoad: () => {},
      onSelect: () => {},
      onCreate: async () => ({}),
      onRename: async () => ({}),
      onExport: async () => {},
      onDelete: () => {},
      vaults: [
        { vault_id: "vault-1", display_name: "English", managed_root_relative_path: "platform", authorization_status: "active", access_status: "available" },
        { vault_id: "vault-2", display_name: "Mathematics", managed_root_relative_path: "platform", authorization_status: "active", access_status: "available" }
      ],
      providers: [{
        provider_id: "provider-1",
        name: "Local chat",
        verification: { is_verified: true },
        models: [{ model_id: "chat-1", model_type: "chat", is_discovered: true, verification: { ok: true } }]
      }],
      onPickAttachments: async () => {},
      onRemoveAttachment: async () => {},
      onRun: async () => ({})
    })
  );

  assert.match(markup, /新建会话/);
  assert.match(markup, /aria-label="搜索会话"/);
  assert.match(markup, /aria-label="会话历史"/);
  assert.match(markup, /aria-label="会话内容"/);
  assert.match(markup, /aria-label="返回会话列表"/);
  assert.match(markup, /aria-label="查看应用证据"/);
  assert.match(markup, /aria-label="问答定位"/);
  assert.match(markup, /class="session-turn-navigator-button"/);
  const conversationMarkup = markup.slice(
    markup.indexOf('class="session-conversation-pane"'),
    markup.indexOf('class="session-evidence-pane"')
  );
  const evidenceMarkup = markup.slice(markup.indexOf('class="session-evidence-pane"'));
  const evidenceId = applicationEvidenceAnchorId("citation:citation-1");
  const conversationText = visibleText(conversationMarkup);

  assert.match(markup, /aria-label="应用证据"/);
  assert.match(markup, /代数复习/);
  assert.match(markup, /所用 vault：English/);
  assert.doesNotMatch(markup, /所用 vault：platform/);
  assert.match(markup, /先复习二次方程。/);
  assert.match(conversationMarkup, /二次方程可用求根公式求解。/);
  assert.match(conversationMarkup, /正在更新会话内容。/);
  assert.match(conversationMarkup, /\[1\]/);
  assert.match(conversationMarkup, /aria-label="查看来源 algebra\.md"/);
  assert.doesNotMatch(conversationMarkup, /aria-label="查看来源 notes\/algebra\.md"/);
  assert.ok(conversationMarkup.includes(`href="#${evidenceId}"`));
  assert.match(conversationMarkup, /复制正文/);
  assert.doesNotMatch(conversationMarkup, /所用 vault：English|范围：整个 vault|Model：chat-1/);
  assert.doesNotMatch(conversationText, /原始段落摘录。|notes\/algebra\.md|知识库|检索|证据|引用/);
  assert.ok(evidenceMarkup.includes(`id="${evidenceId}"`));
  assert.match(evidenceMarkup, /algebra\.md/);
  assert.doesNotMatch(evidenceMarkup, /notes\/algebra\.md/);
  assert.match(evidenceMarkup, /原始段落摘录。/);
  assert.match(markup, /上一页/);
  assert.match(markup, /下一页/);
  assert.match(markup, /第 1 \/ 2 页/);
  assert.match(markup, /aria-label="会话输入"/);
  assert.match(markup, /aria-label="选择 vault"/);
  assert.match(markup, /<option value="vault-1">English<\/option>/);
  assert.match(markup, /<option value="vault-2">Mathematics<\/option>/);
  assert.doesNotMatch(markup, /<option value="vault-[12]">platform<\/option>/);
  assert.match(markup, /aria-label="选择 Model"/);
  assert.match(markup, /aria-label="选择任务类型"/);
  assert.match(markup, /自动识别/);
  assert.match(markup, /aria-label="输入问题或继续创作"/);
  assert.match(markup, /session-composer/);
  assert.match(markup, /仅关键词/);
  assert.match(markup, /仅语义/);
  assert.match(markup, /关键词与语义混合/);
  assert.match(markup, /发送/);
  assert.doesNotMatch(markup, /保存语境|准备任务|固定快照|任务快照状态|执行检索/);
  assert.match(evidenceMarkup, /二次方程 · 第 2 页/);
  assert.match(evidenceMarkup, /原始资料：algebra\.pdf/);
  assert.doesNotMatch(evidenceMarkup, /Source ID|源内容哈希|内容哈希|来源摘要|graph:/);
  assert.doesNotMatch(evidenceMarkup, /[a-f0-9]{64}/i);
  assert.match(evidenceMarkup, /在 Obsidian 中打开/);
  assert.doesNotMatch(evidenceMarkup, /知识库：|Mathematics/);
  assert.match(evidenceMarkup, /\/api\/vaults\/vault-2\/open\?file=notes%2Falgebra.md/);
});

test("renders completeness coverage with explicit gaps and stale source status", () => {
  const markup = renderToStaticMarkup(React.createElement(SessionManagement, {
    sessionPage: { sessions: [{ session_id: "session-1", title: "英语", message_count: 0 }], page: 1, page_size: 25, total: 1, total_pages: 1 },
    filters: { query: "", sort: "updated_at", order: "desc" }, isLoading: false, error: "", selectedSessionId: "session-1",
    selectedDetail: {
      session: { session_id: "session-1", title: "英语" }, messages: [], citations: [], retrieval_results: [],
      task_snapshots: [{ snapshot_id: "snapshot-1", task_id: "task-1", vault_id: "vault-1", intent: "completeness", status: "invalidated", scope_kind: "vault", source_count: 1, source_digest: "a".repeat(64), index_status: "healthy", outbound_scope_summary: "尚未发送", coverage: { planned_count: 1, excluded_count: 1, uncovered_count: 0 } }],
      completeness_results: [{ result_id: "result-1", snapshot_id: "snapshot-1", status: "source-changed", summary: "根据检索证据，来源已变化", recovery_action: "重新准备", invalidation_reason: "索引已变化", coverage_total: 3, coverage_has_more: true, coverage_counts: { planned: 1, processed: 1, duplicate: 0, failed: 0, excluded: 1, uncovered: 0 }, coverage: [{ ordinal: 1, status: "processed", relative_path: "notes/unit.md", content_sha256: "a".repeat(64), identity_kind: "native", heading: "Unit", location: "graph:graph-hash:1:block-hash#chunk:1", excerpt: "word" }, { ordinal: 2, status: "excluded", relative_path: "notes/excluded.md", content_sha256: "b".repeat(64), identity_kind: "native", heading: "Excluded", location: "graph:graph-hash:1:block-hash#chunk:2", reason: "内容被排除" }] }]
    }, vaults: [{ vault_id: "vault-1", display_name: "英语资料", authorization_status: "active", access_status: "available" }], onLoadCompletenessCoverage: async () => ({})
  }));

  const conversationMarkup = markup.slice(
    markup.indexOf('class="session-conversation-pane"'),
    markup.indexOf('class="session-evidence-pane"')
  );
  const evidenceMarkup = markup.slice(markup.indexOf('class="session-evidence-pane"'));
  const conversationText = visibleText(conversationMarkup);

  assert.match(conversationMarkup, /来源已变化/);
  assert.match(conversationText, /\[1\]\[2\]/);
  assert.match(conversationMarkup, /排除 1 项/);
  assert.match(conversationMarkup, /复制结论/);
  assert.match(conversationMarkup, /加载更多/);
  assert.doesNotMatch(conversationText, /根据检索证据|word|内容被排除|notes\/|知识库|检索|证据|引用/);
  assert.match(evidenceMarkup, /应用证据/);
  assert.match(evidenceMarkup, /unit\.md/);
  assert.doesNotMatch(evidenceMarkup, /notes\/unit\.md/);
  assert.match(evidenceMarkup, /word/);
  assert.match(evidenceMarkup, /内容被排除/);
  assert.doesNotMatch(evidenceMarkup, /graph:|内容哈希|[ab]{64}/);
});

test("renders an evidence-bound knowledge organization conclusion with expandable evidence", () => {
  const markup = renderToStaticMarkup(React.createElement(SessionManagement, {
    sessionPage: { sessions: [{ session_id: "session-1", title: "英语", message_count: 0 }], page: 1, page_size: 25, total: 1, total_pages: 1 },
    filters: { query: "", sort: "updated_at", order: "desc" }, isLoading: false, error: "", selectedSessionId: "session-1",
    selectedDetail: {
      session: { session_id: "session-1", title: "英语" }, messages: [], citations: [], retrieval_results: [], completeness_results: [],
      task_snapshots: [{
        snapshot_id: "snapshot-1", task_id: "task-1", vault_id: "vault-1", intent: "knowledge-organization", status: "completed", scope_kind: "directory", scope_path: "notes/unit", source_count: 1, source_digest: "a".repeat(64), index_status: "healthy", outbound_scope_summary: "仅使用已冻结的本地知识库证据；不会调用 Provider、Model 或互联网。",
        knowledge_organization_plan: { section_count: 1, local_evidence_only: true, sections: [{ ordinal: 1, title: "notes/unit", goal: "整理英语知识点", scope_path: "notes/unit", evidence_count: 1, evidence: [{ ordinal: 1, relative_path: "notes/unit/vocabulary.md", heading: "Vocabulary", location: "heading: Vocabulary", excerpt: "word evidence", identity_kind: "native", content_sha256: "a".repeat(64) }] }] }
      }],
      knowledge_organization_results: [{ result_id: "result-1", snapshot_id: "snapshot-1", vault_id: "vault-1", status: "completed", summary: "已按冻结证据生成 1 个知识整理计划段。", structure_kind: "outline", section_counts: { planned: 1, prepared: 0, running: 0, completed: 1, failed: 0, recoverable: 0 }, sections: [{ ordinal: 1, title: "notes/unit", goal: "整理英语知识点", scope_path: "notes/unit", status: "completed", independent_source_count: 1, evidence: [{ ordinal: 1, relative_path: "notes/unit/vocabulary.md", heading: "Vocabulary", location: "heading: Vocabulary", excerpt: "word evidence", identity_kind: "native", content_sha256: "a".repeat(64) }], conclusions: [{ ordinal: 1, content: "词汇要点。", evidence: [{ ordinal: 1, relative_path: "notes/unit/vocabulary.md", heading: "Vocabulary", location: "heading: Vocabulary", excerpt: "word evidence", identity_kind: "native", content_sha256: "a".repeat(64) }] }] }] }]
    },
    vaults: [{ vault_id: "vault-1", display_name: "英语资料", authorization_status: "active", access_status: "available" }]
  }));

  const conversationMarkup = markup.slice(
    markup.indexOf('class="session-conversation-pane"'),
    markup.indexOf('class="session-evidence-pane"')
  );
  const evidenceMarkup = markup.slice(markup.indexOf('class="session-evidence-pane"'));
  const conversationText = visibleText(conversationMarkup);

  assert.match(conversationMarkup, /词汇要点。/);
  assert.match(conversationMarkup, /\[1\]/);
  assert.match(conversationMarkup, /复制正文/);
  assert.match(conversationMarkup, /计划 1 段；已准备 0 段；已完成 1 段；进行中 0 段/);
  assert.doesNotMatch(conversationText, /已按冻结证据|notes\/unit\/vocabulary\.md|word evidence|内容哈希|知识库|检索|证据|引用/);
  assert.match(evidenceMarkup, /应用证据/);
  assert.match(evidenceMarkup, /整理应用/);
  assert.match(evidenceMarkup, /在 Obsidian 中打开/);
  assert.match(evidenceMarkup, /vocabulary\.md/);
  assert.doesNotMatch(evidenceMarkup, /notes\/unit\/vocabulary\.md/);
  assert.match(evidenceMarkup, /word evidence/);
  assert.doesNotMatch(evidenceMarkup, /内容哈希|Source ID|graph:|a{64}/);
});

test("renders deep creation as direct content with application evidence in the side pane", () => {
  const markup = renderToStaticMarkup(React.createElement(SessionManagement, {
    sessionPage: { sessions: [{ session_id: "session-1", title: "英语", message_count: 0 }], page: 1, page_size: 25, total: 1, total_pages: 1 },
    filters: { query: "", sort: "updated_at", order: "desc" }, isLoading: false, error: "", selectedSessionId: "session-1",
    selectedDetail: {
      session: { session_id: "session-1", title: "英语" },
      messages: [], citations: [], retrieval_results: [], completeness_results: [], knowledge_organization_results: [],
      task_snapshots: [{
        snapshot_id: "snapshot-1", task_id: "task-1", vault_id: "vault-1", intent: "deep-creation", status: "completed", scope_kind: "directory", scope_path: "notes/unit", source_count: 1, source_digest: "a".repeat(64), index_status: "healthy", outbound_scope_summary: "本次深度创作会发送冻结本地证据。",
        deep_creation_plan: { section_count: 1, local_evidence_count: 1, sections: [{ ordinal: 1, title: "notes/unit", goal: "写学习笔记", scope_path: "notes/unit", local_evidence_count: 1, local_evidence: [{ ordinal: 1, relative_path: "notes/unit/vocabulary.md", heading: "Vocabulary", location: "heading: Vocabulary", excerpt: "word evidence", identity_kind: "native", content_sha256: "a".repeat(64) }] }] }
      }],
      deep_creation_results: [{
        result_id: "result-1", task_id: "task-1", snapshot_id: "snapshot-1", vault_id: "vault-1", status: "completed", summary: "已按冻结证据和模型判断生成 1 个深度创作段。", section_counts: { planned: 1, completed: 1, failed: 0, recoverable: 0 },
        sections: [{ ordinal: 1, title: "notes/unit", goal: "写学习笔记", scope_path: "notes/unit", status: "completed", content: "深度创作段落。", model_judgement: "模型判断：保留未解决的不确定性。", local_evidence: [{ ordinal: 1, relative_path: "notes/unit/vocabulary.md", heading: "Vocabulary", location: "heading: Vocabulary", excerpt: "word evidence", identity_kind: "native", content_sha256: "a".repeat(64) }]}]
      }]
    },
    vaults: [{ vault_id: "vault-1", display_name: "英语资料", authorization_status: "active", access_status: "available" }]
  }));

  const conversationMarkup = markup.slice(
    markup.indexOf('class="session-conversation-pane"'),
    markup.indexOf('class="session-evidence-pane"')
  );
  const evidenceMarkup = markup.slice(markup.indexOf('class="session-evidence-pane"'));
  const conversationText = visibleText(conversationMarkup);

  assert.match(conversationMarkup, /深度创作已完成/);
  assert.match(conversationMarkup, /深度创作段落。/);
  assert.match(conversationMarkup, /\[1\]/);
  assert.match(conversationMarkup, /复制正文/);
  assert.doesNotMatch(conversationText, /word evidence|模型判断|notes\/unit\/vocabulary\.md|知识库|检索|证据|引用/);
  assert.match(evidenceMarkup, /应用证据/);
  assert.match(evidenceMarkup, /创作应用/);
  assert.match(evidenceMarkup, /vocabulary\.md/);
  assert.doesNotMatch(evidenceMarkup, /notes\/unit\/vocabulary\.md/);
  assert.match(evidenceMarkup, /word evidence/);
  assert.doesNotMatch(evidenceMarkup, /互联网证据/);
});

test("keeps reused evidence scoped to each organization and creation result", () => {
  const session = { session_id: "session-1", title: "英语", message_count: 0 };
  const evidence = {
    ordinal: 1,
    relative_path: "notes/unit/vocabulary.md",
    heading: "Vocabulary",
    location: "heading: Vocabulary",
    excerpt: "word evidence",
    identity_kind: "native",
    content_sha256: "a".repeat(64)
  };
  const markup = renderToStaticMarkup(React.createElement(SessionManagement, {
    sessionPage: { sessions: [session], page: 1, page_size: 25, total: 1, total_pages: 1 },
    filters: { query: "", sort: "updated_at", order: "desc" }, isLoading: false, error: "", selectedSessionId: "session-1",
    selectedDetail: {
      session, messages: [], citations: [], retrieval_results: [], completeness_results: [], task_snapshots: [],
      knowledge_organization_results: ["organization-a", "organization-b"].map((resultId, index) => ({
        result_id: resultId, vault_id: "vault-1", status: "completed", section_counts: {},
        sections: [{
          ordinal: 1, status: "completed", conclusions: [{
            ordinal: 1, content: `整理内容 ${index + 1}。`, evidence: [evidence]
          }]
        }]
      })),
      deep_creation_results: ["creation-a", "creation-b"].map((resultId, index) => ({
        result_id: resultId, vault_id: "vault-1", status: "completed", section_counts: {},
        sections: [{ ordinal: 1, status: "completed", content: `创作内容 ${index + 1}。`, local_evidence: [evidence] }]
      }))
    },
    vaults: [{ vault_id: "vault-1", display_name: "英语资料", authorization_status: "active", access_status: "available" }]
  }));

  const conversationMarkup = markup.slice(
    markup.indexOf('class="session-conversation-pane"'),
    markup.indexOf('class="session-evidence-pane"')
  );
  const evidenceMarkup = markup.slice(markup.indexOf('class="session-evidence-pane"'));
  const targetIds = [...conversationMarkup.matchAll(/href="#(application-evidence-[^"]+)"/g)].map((match) => match[1]);

  assert.equal(targetIds.length, 4);
  assert.equal(new Set(targetIds).size, 4);
  targetIds.forEach((targetId) => assert.ok(evidenceMarkup.includes(`id="${targetId}"`)));
  assert.equal((evidenceMarkup.match(/class="session-citation"/g) || []).length, 4);
});

test("renders restored knowledge-organization bindings and recoverable progress truthfully", () => {
  const markup = renderToStaticMarkup(React.createElement(SessionManagement, {
    sessionPage: { sessions: [{ session_id: "session-restore", title: "恢复整理", selected_vault_id: "vault-current", message_count: 0 }], page: 1, page_size: 25, total: 1, total_pages: 1 },
    filters: { query: "", sort: "updated_at", order: "desc" }, isLoading: false, error: "", selectedSessionId: "session-restore",
    selectedDetail: {
      session: { session_id: "session-restore", title: "恢复整理", selected_vault_id: "vault-current", scope_kind: "vault", selected_model_label: "chat-1" },
      messages: [], citations: [], retrieval_results: [], completeness_results: [],
      task_snapshots: [{
        snapshot_id: "snapshot-restore", task_id: "task-restore", vault_id: "vault-frozen", intent: "knowledge-organization", status: "recoverable", scope_kind: "directory", scope_path: "notes/unit", source_count: 2, source_digest: "s".repeat(64),
        index_status: "healthy", index_updated_at: "2026-07-23T00:00:00+00:00", index_digest: "i".repeat(64), policy_revision: 9, exclusion_summary: "排除规则 1 项：never-send-cloud: notes/private", outbound_scope_summary: "本次检索仅使用本地知识库证据。",
        knowledge_organization_plan: { section_count: 2, local_evidence_only: true, sections: [
          { ordinal: 1, title: "notes/unit", goal: "整理已完成主题", scope_path: "notes/unit", evidence_count: 1, evidence: [] },
          { ordinal: 2, title: "notes/review", goal: "整理待恢复主题", scope_path: "notes/review", evidence_count: 1, evidence: [] }
        ] },
        invalidation_reason: null
      }],
      knowledge_organization_results: [{
        result_id: "result-restore", snapshot_id: "snapshot-restore", status: "recoverable", summary: "本次生成被中断，已知段落已保留。", recovery_action: "恢复索引后重新发送。", local_evidence_only: true,
        section_counts: { planned: 2, prepared: 1, failed: 0, recoverable: 1 },
        sections: [
          { ordinal: 1, title: "notes/unit", goal: "整理已完成主题", scope_path: "notes/unit", status: "prepared", prepared_evidence_count: 1, evidence: [] },
          { ordinal: 2, title: "notes/review", goal: "整理待恢复主题", scope_path: "notes/review", status: "recoverable", prepared_evidence_count: 0, reason: "服务在准备此段前中断。", evidence: [] }
        ]
      }]
    },
    vaults: [
      { vault_id: "vault-current", display_name: "当前资料", authorization_status: "active", access_status: "available" },
      { vault_id: "vault-frozen", display_name: "冻结资料", authorization_status: "active", access_status: "available" }
    ]
  }));

  assert.match(markup, /计划待恢复/);
  assert.match(markup, /本次生成被中断，已知段落已保留。/);
  assert.match(markup, /下一步：恢复索引后重新发送。/);
  assert.match(markup, /第 1 段：已准备/);
  assert.match(markup, /第 2 段：待恢复/);
  assert.doesNotMatch(markup, /任务 知识整理|冻结知识库|已冻结资料范围|策略修订|准备任务/);
  assert.match(markup, /计划待恢复/);
  assert.match(markup, /计划 2 段；已准备 1 段；已完成 0 段；进行中 0 段/);
  assert.match(markup, /失败 0 段；待恢复 1 段/);
  assert.match(markup, /第 2 段：待恢复/);
  assert.match(markup, /服务在准备此段前中断。/);
});

test("keeps pending-answer citations in the application-evidence pane", () => {
  const session = { session_id: "session-1", title: "英语", message_count: 1 };
  const markup = renderToStaticMarkup(React.createElement(SessionManagement, {
    sessionPage: { sessions: [session], page: 1, page_size: 25, total: 1, total_pages: 1 },
    filters: { query: "", sort: "updated_at", order: "desc" }, isLoading: false, error: "", selectedSessionId: "session-1",
    selectedDetail: {
      session,
      messages: [{ message_id: "message-1", role: "user", content: "继续解释", created_at: "2026-07-23T00:00:00+00:00" }],
      citations: [{ citation_id: "citation-1", result_id: "answer-1", vault_id: "vault-history", relative_path: "notes/unit.md", location: "heading: Unit", status: "pending-verification", invalidation_reason: "段落内容已修改" }],
      generation_results: [{ result_id: "answer-1", status: "pending-verification", content: "已编辑的段落", content_origin: "user-content", snapshot_id: "snapshot-1", provider_id: "provider-history", model_id: "chat-1", vault_id: "vault-history", scope_kind: "directory", scope_path: "notes", context_summary: "用户约束：仅限本地。", created_at: "2026-07-23T00:00:01+00:00" }],
      task_snapshots: [], retrieval_results: [], completeness_results: []
    },
    vaults: [], providers: [], onEditGenerationResult: async () => ({}), onReverifyGenerationResult: async () => ({ status: "valid" })
  }));

  const conversationMarkup = markup.slice(
    markup.indexOf('class="session-conversation-pane"'),
    markup.indexOf('class="session-evidence-pane"')
  );
  const evidenceMarkup = markup.slice(markup.indexOf('class="session-evidence-pane"'));
  const conversationText = visibleText(conversationMarkup);

  assert.match(conversationMarkup, /内容待确认/);
  assert.match(conversationMarkup, /已编辑的段落/);
  assert.match(conversationMarkup, /\[1\]/);
  assert.match(conversationMarkup, /复制正文/);
  assert.match(conversationMarkup, /重新确认/);
  assert.doesNotMatch(conversationText, /引用待核验|范围：notes|Provider：provider-history|用户约束|vault-history|notes\/unit\.md|知识库|检索|证据|引用/);
  assert.match(evidenceMarkup, /应用证据/);
  assert.doesNotMatch(evidenceMarkup, /知识库：|历史知识库不可用/);
  assert.match(evidenceMarkup, /unit\.md/);
  assert.doesNotMatch(evidenceMarkup, /notes\/unit\.md/);
  assert.match(evidenceMarkup, /待核验/);
});

test("keeps uncited stale source-lookup evidence in the application-evidence pane", () => {
  const session = {
    session_id: "session-1",
    title: "当前会话",
    selected_vault_id: "vault-1",
    selected_vault_label: "当前 vault",
    message_count: 0,
    updated_at: "2026-07-23T00:00:00+00:00"
  };
  const markup = renderToStaticMarkup(
    React.createElement(SessionManagement, {
      sessionPage: { sessions: [session], page: 1, page_size: 25, total: 1, total_pages: 1 },
      filters: { query: "", sort: "updated_at", order: "desc" },
      isLoading: false,
      error: "",
      selectedSessionId: "session-1",
      selectedDetail: {
        session,
        messages: [],
        citations: [],
        task_snapshots: [{
          snapshot_id: "snapshot-old",
          task_id: "task-old",
          vault_id: "vault-2",
          intent: "source-lookup",
          status: "invalidated",
          scope_kind: "vault",
          source_count: 1,
          source_digest: "a".repeat(64),
          index_status: "healthy",
          outbound_scope_summary: "尚未发送",
          invalidation_reason: "来源已改变。"
        }],
        retrieval_results: [{
          result_id: "result-old",
          task_id: "task-old",
          snapshot_id: "snapshot-old",
          vault_id: "vault-2",
          snapshot_status: "invalidated",
          is_stale: true,
          invalidation_reason: "来源已改变。",
          status: "completed",
          summary: "已在已确认范围内找到 1 条本地知识库证据；未调用 Model。",
          recovery_action: null,
          retrieval_duration_ms: 12,
          generation_duration_ms: 0,
          evidences: [{
            ordinal: 1,
            identity_kind: "native",
            relative_path: "notes/evidence.md",
            content_sha256: "a".repeat(64),
            source_id: null,
            source_content_hash: null,
            source_path: null,
            heading: "证据",
            location: "heading: 证据",
            page: null,
            excerpt: "历史证据。",
            matched_channels: ["keyword"]
          }, {
            ordinal: 2,
            identity_kind: "derived",
            relative_path: "notes/other-evidence.md",
            content_sha256: "b".repeat(64),
            source_id: "other-source",
            source_content_hash: "c".repeat(64),
            source_path: "sources/other.pdf",
            heading: "另一份证据",
            location: "heading: 另一份证据",
            page: null,
            excerpt: "另一来源的历史证据。",
            matched_channels: ["keyword"]
          }]
        }]
      },
      isDetailLoading: false,
      detailError: "",
      onLoad: () => {},
      onSelect: () => {},
      onCreate: async () => ({}),
      onRename: async () => ({}),
      onExport: async () => {},
      onDelete: () => {},
      vaults: [
        { vault_id: "vault-1", display_name: "当前 vault", authorization_status: "active", access_status: "available" },
        { vault_id: "vault-2", display_name: "证据 vault", authorization_status: "active", access_status: "available" }
      ],
      providers: [],
      onUpdateContext: async () => {},
      onPickAttachments: async () => {},
      onRemoveAttachment: async () => {},
      onSendMessage: async () => {},
      onPreviewTask: async () => ({}),
      onCreateTask: async () => ({}),
      onExecuteTask: async () => ({})
    })
  );

  const conversationMarkup = markup.slice(
    markup.indexOf('class="session-conversation-pane"'),
    markup.indexOf('class="session-evidence-pane"')
  );
  const evidenceMarkup = markup.slice(markup.indexOf('class="session-evidence-pane"'));
  const conversationText = visibleText(conversationMarkup);

  assert.match(conversationMarkup, /当前内容需要重新确认/);
  assert.match(conversationMarkup, /需重新准备：来源已改变。/);
  assert.doesNotMatch(conversationText, /历史证据|notes\/evidence\.md|notes\/other-evidence\.md|知识库|检索|引用/);
  assert.match(evidenceMarkup, /应用证据/);
  assert.match(evidenceMarkup, /定位应用/);
  assert.match(evidenceMarkup, /已失效/);
  assert.doesNotMatch(evidenceMarkup, /知识库：|证据 vault/);
  assert.match(evidenceMarkup, /\/api\/vaults\/vault-2\/open\?file=notes%2Fevidence.md/);
  assert.match(evidenceMarkup, /历史证据。/);
  assert.match(evidenceMarkup, /other-evidence\.md/);
  assert.doesNotMatch(evidenceMarkup, /notes\/other-evidence\.md/);
});

test("keeps a stale generated answer instead of showing an empty source-lookup fallback", () => {
  const session = { session_id: "session-1", title: "当前会话", message_count: 0 };
  const markup = renderToStaticMarkup(React.createElement(SessionManagement, {
    sessionPage: { sessions: [session], page: 1, page_size: 25, total: 1, total_pages: 1 },
    filters: { query: "", sort: "updated_at", order: "desc" }, isLoading: false, error: "", selectedSessionId: "session-1",
    selectedDetail: {
      session,
      messages: [],
      citations: [{
        citation_id: "citation-1", result_id: "answer-old", snapshot_id: "snapshot-old", vault_id: "vault-1",
        relative_path: "notes/unit.md", location: "heading: Unit", identity_kind: "native", status: "stale"
      }],
      generation_results: [{
        result_id: "answer-old", task_id: "task-old", snapshot_id: "snapshot-old", vault_id: "vault-1",
        status: "stale", content: "保留的回答。", content_origin: "model-judgement"
      }],
      task_snapshots: [{
        snapshot_id: "snapshot-old", task_id: "task-old", vault_id: "vault-1", intent: "source-lookup",
        status: "invalidated", scope_kind: "vault", source_count: 1, source_digest: "a".repeat(64),
        index_status: "healthy", outbound_scope_summary: "尚未发送", invalidation_reason: "来源已改变。"
      }],
      retrieval_results: [{
        result_id: "result-old", task_id: "task-old", snapshot_id: "snapshot-old", vault_id: "vault-1",
        status: "completed", is_stale: true, invalidation_reason: "来源已改变。",
        summary: "已在已确认范围内找到 1 条本地知识库证据；已生成回答。", recovery_action: null,
        evidences: [{
          ordinal: 1, identity_kind: "native", relative_path: "notes/unit.md", content_sha256: "a".repeat(64),
          heading: "Unit", location: "heading: Unit", excerpt: "关联摘录。"
        }]
      }],
      completeness_results: []
    },
    vaults: [{ vault_id: "vault-1", display_name: "英语资料", authorization_status: "active", access_status: "available" }]
  }));

  const conversationMarkup = markup.slice(
    markup.indexOf('class="session-conversation-pane"'),
    markup.indexOf('class="session-evidence-pane"')
  );
  const evidenceMarkup = markup.slice(markup.indexOf('class="session-evidence-pane"'));
  const conversationText = visibleText(conversationMarkup);

  assert.match(conversationMarkup, /内容需重新确认/);
  assert.match(conversationMarkup, /保留的回答。/);
  assert.doesNotMatch(conversationMarkup, /暂未生成可用回答。/);
  assert.doesNotMatch(conversationText, /关联摘录。|notes\/unit\.md|知识库|检索|证据|引用/);
  assert.match(evidenceMarkup, /unit\.md/);
  assert.doesNotMatch(evidenceMarkup, /notes\/unit\.md/);
  assert.match(evidenceMarkup, /关联摘录。/);
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
        vault_id: "vault-1",
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
      onTaskDeleted: () => {},
      onTaskSnapshot: () => {},
      vault: { vault_id: "vault-1" }
    })
  );

  assert.match(markup, /可能版本 1/);
  assert.match(markup, /识别失败 1/);
  assert.match(markup, /已解析 1/);
  assert.match(markup, /待审核问题 1/);
});

test("offers browser file and folder uploads for an available vault", () => {
  const markup = renderToStaticMarkup(
    React.createElement(ImportTaskCenter, {
      tasks: [],
      error: "",
      isLoading: false,
      selectedTaskId: null,
      onSelect: () => {},
      onTaskChanged: () => {},
      onTaskDeleted: () => {},
      onTaskSnapshot: () => {},
      vault: {
        vault_id: "vault-1",
        display_name: "Import Vault",
        authorization_status: "active",
        access_status: "available"
      }
    })
  );

  assert.match(markup, /aria-label="上传本机资料文件"/);
  assert.match(markup, /aria-label="上传本机资料文件夹"/);
  assert.match(markup, /webkitdirectory=""/);
  assert.doesNotMatch(markup, /选择服务机文件/);
  assert.match(markup, />上传文件夹<\/button>/);
});

test("requires an explicit online parse Provider selection", () => {
  const testDirectory = dirname(fileURLToPath(import.meta.url));
  const source = readFileSync(resolve(testDirectory, "../src/app.js"), "utf8");
  const componentStart = source.indexOf("function ImportTaskLauncher");
  const componentEnd = source.indexOf("function projectionSummaryText", componentStart);
  const component = source.slice(componentStart, componentEnd);

  assert.ok(componentStart >= 0);
  assert.match(component, /const \[onlineParseEnabled, setOnlineParseEnabled\] = React\.useState\(loadOnlineParseEnabled\)/);
  assert.match(component, /React\.useState\(loadOnlineParseProviderId\)/);
  assert.match(component, /saveOnlineParseProviderId\(onlineParseProviderId\)/);
  assert.match(component, /saveOnlineParseEnabled\(next\)/);
  assert.match(component, /React\.useState\(loadMarkdownPipeline\)/);
  assert.match(component, /markdown_pipeline: markdownPipeline/);
  assert.match(component, /const onlineParseActive = onlineParseEnabled/);
  assert.match(component, /online_parse_enabled: onlineParseActive/);
  assert.match(component, /online_parse_provider_id: onlineParseActive \? onlineParseProviderId : null/);
  assert.match(component, /AI 结构化/);
  assert.match(component, /本地结构化/);
  assert.match(component, /verifiedOnlineParseProviders\.some/);
  assert.match(component, /请选择在线解析 Provider/);
  assert.doesNotMatch(component, /providers\.find\(\(provider\) => provider\.verified\)/);
});

test("defaults online parsing on and preserves the user's manual switch choice", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key)
  };

  assert.equal(loadOnlineParseEnabled(storage), true);

  saveOnlineParseEnabled(false, storage);
  assert.equal(values.get(ONLINE_PARSE_ENABLED_STORAGE_KEY), "false");
  assert.equal(loadOnlineParseEnabled(storage), false);

  saveOnlineParseEnabled(true, storage);
  assert.equal(values.get(ONLINE_PARSE_ENABLED_STORAGE_KEY), "true");
  assert.equal(loadOnlineParseEnabled(storage), true);

  values.set(ONLINE_PARSE_ENABLED_STORAGE_KEY, "unexpected");
  assert.equal(loadOnlineParseEnabled(storage), true);
});

test("persists the last selected online parse Provider without enabling online parsing", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key)
  };

  saveOnlineParseProviderId(" mineru-official ", storage);

  assert.equal(loadOnlineParseProviderId(storage), "mineru-official");
  assert.equal(values.get(ONLINE_PARSE_SELECTION_STORAGE_KEY), '{"providerId":"mineru-official"}');

  saveOnlineParseProviderId("", storage);
  assert.equal(loadOnlineParseProviderId(storage), "");
});

test("defaults PDF Markdown structuring to AI and preserves manual local mode", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value)
  };

  assert.equal(loadMarkdownPipeline(storage), "ai");
  saveMarkdownPipeline("local", storage);
  assert.equal(values.get(MARKDOWN_PIPELINE_STORAGE_KEY), "local");
  assert.equal(loadMarkdownPipeline(storage), "local");
  saveMarkdownPipeline("invalid", storage);
  assert.equal(loadMarkdownPipeline(storage), "ai");
});

test("offers an accessible deletion action only for non-running import tasks", () => {
  const task = {
    task_id: "task-1",
    vault_id: "vault-1",
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
      vault: { vault_id: "vault-1" }
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
      vault: { vault_id: "vault-1" }
    })
  );

  assert.match(markup, /aria-label="删除任务 book\.pdf"/);
  assert.match(markup, />删除<\/button>/);
  assert.doesNotMatch(runningMarkup, /删除任务 book\.pdf/);
});

test("offers current-page task selection and bulk deletion without selecting running tasks", () => {
  const task = (taskId, scopeLabel, lifecycle = "complete") => ({
    task_id: taskId,
    vault_id: "vault-1",
    scope_label: scopeLabel,
    vault_label: "Vault",
    lifecycle,
    phase: lifecycle === "running" ? "scanning" : "completed",
    recovery_actions: [],
    counts: { discovered: 1, new: 1, duplicate: 0, possible_version: 0, identity_failed: 0, parsed: 1, parse_failed: 0, required_check: 0, failed: 0 }
  });
  const markup = renderToStaticMarkup(
    React.createElement(ImportTaskCenter, {
      tasks: [task("task-1", "first.pdf"), task("task-2", "running.pdf", "running"), task("task-3", "third.pdf")],
      error: "",
      isLoading: false,
      selectedTaskId: null,
      onSelect: () => {},
      onTaskChanged: () => {},
      onTaskDeleted: () => {},
      onTaskSnapshot: () => {},
      vault: { vault_id: "vault-1" }
    })
  );

  assert.match(markup, /aria-label="全选当前页可删除任务"/);
  assert.match(markup, /aria-label="选择任务 first\.pdf"/);
  assert.match(markup, /aria-label="选择任务 third\.pdf"/);
  assert.doesNotMatch(markup, /aria-label="选择任务 running\.pdf"/);
  assert.match(markup, />删除所选<\/button>/);
});

test("filters import tasks to the current vault and paginates ten tasks by default", () => {
  const task = (index, vaultId = "vault-1") => ({
    task_id: `task-${index}`,
    vault_id: vaultId,
    scope_label: index === 13 ? "其他 vault 任务" : `当前任务 ${index}`,
    vault_label: vaultId === "vault-1" ? "当前 Vault" : "其他 Vault",
    lifecycle: "queued",
    phase: "waiting-for-next-stage",
    recovery_actions: [],
    counts: {
      discovered: 1,
      new: 1,
      duplicate: 0,
      possible_version: 0,
      identity_failed: 0,
      parsed: 0,
      parse_failed: 0,
      required_check: 0,
      failed: 0
    }
  });
  const markup = renderToStaticMarkup(
    React.createElement(ImportTaskCenter, {
      tasks: [...Array.from({ length: 12 }, (_, index) => task(index + 1)), task(13, "vault-2")],
      error: "",
      isLoading: false,
      selectedTaskId: null,
      onSelect: () => {},
      onTaskChanged: () => {},
      onTaskDeleted: () => {},
      onTaskSnapshot: () => {},
      vault: { vault_id: "vault-1" }
    })
  );

  assert.match(markup, /当前任务 10/);
  assert.doesNotMatch(markup, /当前任务 11/);
  assert.doesNotMatch(markup, /其他 vault 任务/);
  assert.match(markup, /aria-label="每页任务数量"/);
  assert.match(markup, /第 1 \/ 2 页/);
  assert.match(markup, /上一页/);
  assert.match(markup, /下一页/);
});

test("renders the projection rebuild verification panel without projection content", () => {
  const markup = renderToStaticMarkup(
    React.createElement(ProjectionRebuildVerificationPanel, {
      task: { task_id: "task-1", vault_id: "vault-1" },
      conversionGraphs: [{ item_id: 1, graph_id: "graph-1", graph_revision: 2, blocks: [] }],
      onTaskDeleted: () => {}
    })
  );

  assert.match(markup, /投影重建验证/);
  assert.match(markup, /读取投影摘要/);
  assert.doesNotMatch(markup, /删除并重建验证/);
  assert.doesNotMatch(markup, /retrieval_projection/);
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

test("renders a partial index summary without unmounting the workspace", () => {
  const markup = renderToStaticMarkup(
    React.createElement(VaultIndexStatus, {
      vault: {
        vault_id: "vault-1",
        index: { status: "not-initialized" }
      },
      onUpdate: () => {}
    })
  );

  assert.match(markup, /状态：未初始化/);
  assert.match(markup, /已索引 0 项；失效 0 项；待关联 0 项；失败 0 项。/);
});
