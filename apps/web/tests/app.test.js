import assert from "node:assert/strict";
import test from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  App,
  ConversionReviewControls,
  derivedMarkdownPreview,
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
        citations: [{ citation_id: "citation-1", relative_path: "notes/algebra.md", location: "第 2 节", status: "valid" }],
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
          summary: "已在已确认范围内找到 1 条本地知识库证据；未调用 Model。",
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
            location: "heading: 二次方程; page: 2",
            page: 2,
            excerpt: "二次方程有两个根。",
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
      isDetailLoading: false,
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
      onUpdateContext: async () => {},
      onPickAttachments: async () => {},
      onRemoveAttachment: async () => {},
      onSendMessage: async () => {},
      onPreviewTask: async () => ({}),
      onCreateTask: async () => ({}),
      onExecuteTask: async () => ({})
    })
  );

  assert.match(markup, /新建会话/);
  assert.match(markup, /aria-label="搜索会话"/);
  assert.match(markup, /aria-label="会话历史"/);
  assert.match(markup, /aria-label="会话内容"/);
  assert.match(markup, /aria-label="引用证据"/);
  assert.match(markup, /代数复习/);
  assert.match(markup, /所用 vault：English/);
  assert.doesNotMatch(markup, /所用 vault：platform/);
  assert.match(markup, /先复习二次方程。/);
  assert.match(markup, /notes\/algebra\.md/);
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
  assert.match(markup, /执行检索/);
  assert.match(markup, /本地知识库证据/);
  assert.match(markup, /检索 12 ms；生成 0 ms（未调用 Model）/);
  assert.match(markup, /Source ID source-1/);
  assert.match(markup, /在 Obsidian 中打开/);
  assert.match(markup, /vault：Mathematics/);
  assert.match(markup, /\/api\/vaults\/vault-2\/open\?file=notes%2Falgebra.md/);
  assert.match(markup, /独立来源：2/);
  assert.match(markup, /系统不会自动合并、选择或判定哪一种说法正确。/);
  assert.match(markup, /同一来源中的 2 条证据只计为 1 个独立来源。/);
});

test("renders completeness coverage with explicit gaps and stale source status", () => {
  const markup = renderToStaticMarkup(React.createElement(SessionManagement, {
    sessionPage: { sessions: [{ session_id: "session-1", title: "英语", message_count: 0 }], page: 1, page_size: 25, total: 1, total_pages: 1 },
    filters: { query: "", sort: "updated_at", order: "desc" }, isLoading: false, error: "", selectedSessionId: "session-1",
    selectedDetail: {
      session: { session_id: "session-1", title: "英语" }, messages: [], citations: [], retrieval_results: [],
      task_snapshots: [{ snapshot_id: "snapshot-1", task_id: "task-1", vault_id: "vault-1", intent: "completeness", status: "invalidated", scope_kind: "vault", source_count: 1, source_digest: "a".repeat(64), index_status: "healthy", outbound_scope_summary: "尚未发送", coverage: { planned_count: 1, excluded_count: 1, uncovered_count: 0 } }],
      completeness_results: [{ result_id: "result-1", snapshot_id: "snapshot-1", status: "source-changed", summary: "来源已变化", recovery_action: "重新准备", invalidation_reason: "索引已变化", coverage_total: 3, coverage_has_more: true, coverage_counts: { planned: 1, processed: 1, duplicate: 0, failed: 0, excluded: 1, uncovered: 0 }, coverage: [{ ordinal: 1, status: "processed", relative_path: "notes/unit.md", content_sha256: "a".repeat(64), identity_kind: "native", location: "heading: Unit", excerpt: "word" }, { ordinal: 2, status: "excluded", relative_path: "notes/excluded.md", content_sha256: "b".repeat(64), identity_kind: "native", location: "heading: Excluded", reason: "内容被排除" }] }]
    }, vaults: [{ vault_id: "vault-1", display_name: "英语资料", authorization_status: "active", access_status: "available" }], onLoadCompletenessCoverage: async () => ({})
  }));

  assert.match(markup, /来源已变化/);
  assert.match(markup, /排除 1 项/);
  assert.match(markup, /内容被排除/);
  assert.match(markup, /加载更多覆盖项/);
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

  assert.match(markup, /知识整理计划/);
  assert.match(markup, /仅使用本地知识库中已冻结的证据/);
  assert.match(markup, /已按冻结证据生成 1 个知识整理计划段。/);
  assert.match(markup, /词汇要点。/);
  assert.match(markup, /独立来源：1/);
  assert.match(markup, /在 Obsidian 中打开/);
  assert.match(markup, /notes\/unit\/vocabulary\.md/);
  assert.match(markup, /word evidence/);
  assert.match(markup, /计划 1 段；已准备 0 段；已完成 1 段；进行中 0 段/);
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
        index_status: "healthy", index_updated_at: "2026-07-23T00:00:00+00:00", index_digest: "i".repeat(64), policy_revision: 9, exclusion_summary: "排除规则 1 项：never-send-cloud: notes/private", outbound_scope_summary: "仅使用已冻结的本地知识库证据；不会调用 Provider、Model 或互联网。",
        knowledge_organization_plan: { section_count: 2, local_evidence_only: true, sections: [
          { ordinal: 1, title: "notes/unit", goal: "整理已完成主题", scope_path: "notes/unit", evidence_count: 1, evidence: [] },
          { ordinal: 2, title: "notes/review", goal: "整理待恢复主题", scope_path: "notes/review", evidence_count: 1, evidence: [] }
        ] },
        invalidation_reason: null
      }],
      knowledge_organization_results: [{
        result_id: "result-restore", snapshot_id: "snapshot-restore", status: "recoverable", summary: "准备被中断，已知段落已保留。", recovery_action: "恢复索引后重新准备任务。", local_evidence_only: true,
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

  assert.match(markup, /任务 知识整理：待恢复/);
  assert.match(markup, /冻结 vault：冻结资料/);
  assert.match(markup, /来源：2 项；来源摘要：ssssssssssss/);
  assert.match(markup, /索引：healthy；版本：2026-07-23T00:00:00\+00:00；索引摘要：iiiiiiiiiiii/);
  assert.match(markup, /策略修订：9；排除项：排除规则 1 项：never-send-cloud: notes\/private/);
  assert.match(markup, /计划待恢复/);
  assert.match(markup, /计划 2 段；已准备 1 段；已完成 0 段；进行中 0 段/);
  assert.match(markup, /失败 0 段；待恢复 1 段/);
  assert.match(markup, /第 2 段：notes\/review/);
  assert.match(markup, /状态：待恢复/);
  assert.match(markup, /服务在准备此段前中断。/);
});

test("renders a pending citation paragraph with its historical scope and verification action", () => {
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

  assert.match(markup, /引用待核验/);
  assert.match(markup, /范围：notes/);
  assert.match(markup, /Provider：provider-history/);
  assert.match(markup, /vault：vault-history/);
  assert.match(markup, /重新检索核验/);
  assert.match(markup, /待核验/);
});

test("marks stale evidence and keeps its original vault identity", () => {
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

  assert.match(markup, /证据已失效/);
  assert.match(markup, /需重新准备：来源已改变。/);
  assert.match(markup, /vault：证据 vault/);
  assert.match(markup, /\/api\/vaults\/vault-2\/open\?file=notes%2Fevidence.md/);
  assert.match(markup, /历史证据：该历史结果未提供独立来源计算依据。/);
  assert.match(markup, /notes\/other-evidence.md/);
  assert.equal((markup.match(/source-comparison-group/g) || []).length, 2);
  assert.doesNotMatch(markup, /来源 1：/);
  assert.doesNotMatch(markup, /同一来源中的 2 条证据只计为 1 个独立来源。/);
  assert.doesNotMatch(markup, /aria-label="本地知识库证据"/);
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
