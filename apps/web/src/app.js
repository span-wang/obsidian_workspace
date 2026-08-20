import React from "react";
import {
  ChevronLeft,
  FileText,
  FolderOpen,
  LayoutDashboard,
  ListTodo,
  Menu,
  MessageCircle,
  Paperclip,
  RefreshCw,
  Search,
  Settings,
  SlidersHorizontal,
  Trash2,
  X
} from "lucide-react";

export const HEALTH_ENDPOINT = "/api/health";
export const LOCAL_SESSION_ENDPOINT = "/api/session";
export const VAULTS_ENDPOINT = "/api/vaults";
export const VAULT_DIRECTORY_PICKER_ENDPOINT = "/api/vaults/select-directory";
export const PROVIDERS_ENDPOINT = "/api/providers";
export const ONLINE_PARSE_PROVIDERS_ENDPOINT = "/api/online-parse-providers";
export const MARKDOWN_STRUCTURE_BUDGET_ENDPOINT = "/api/providers/markdown-structuring/budget";
export const SESSIONS_ENDPOINT = "/api/sessions";
export const RETRIEVAL_MODE_ENDPOINT = "/api/retrieval/mode";
export const WORKBENCH_OVERVIEW_ENDPOINT = "/api/workbench/overview";
export const IMPORT_TASKS_ENDPOINT = "/api/import-tasks";
export const IMPORT_FILES_SELECTION_ENDPOINT = "/api/import-selections/files";
export const IMPORT_UPLOAD_ENDPOINT = "/api/import-selections/uploads";
export const IMPORT_DIRECTORY_SELECTION_ENDPOINT = "/api/import-selections/directory";
export const ONLINE_PARSE_SELECTION_STORAGE_KEY = "obsidian-platform.online-parse-selection.v1";
export const ONLINE_PARSE_ENABLED_STORAGE_KEY = "obsidian-platform.online-parse-enabled.v1";
export const MARKDOWN_PIPELINE_STORAGE_KEY = "obsidian-platform.markdown-pipeline.v1";
export const IMPORT_TASK_EVENT_NAMES = [
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
];
export const NAVIGATION_DESTINATIONS = [
  { id: "workbench", label: "工作台", emptyState: "尚未选择 vault。" },
  { id: "materials", label: "资料", emptyState: "当前没有已授权的 vault。" },
  { id: "sessions", label: "会话", emptyState: "当前没有已保存的会话。" },
  { id: "tasks", label: "任务", emptyState: "当前没有任务。" },
  { id: "settings", label: "设置", emptyState: "当前没有可用设置。" }
];

const NAVIGATION_ICONS = {
  workbench: LayoutDashboard,
  materials: FolderOpen,
  sessions: MessageCircle,
  tasks: ListTodo,
  settings: Settings
};

const VAULT_SURFACES = new Set(["workbench", "materials"]);
const IMPORT_PROGRESS_PHASES = ["queued", "scanning", "converting", "parsing", "ocr", "deriving-markdown", "committing", "indexing"];

function browserStorage(storage) {
  if (storage) return storage;
  try {
    return globalThis.localStorage;
  } catch {
    return null;
  }
}

export function loadOnlineParseProviderId(storage) {
  try {
    const saved = browserStorage(storage)?.getItem(ONLINE_PARSE_SELECTION_STORAGE_KEY);
    const providerId = typeof saved === "string" ? JSON.parse(saved)?.providerId : "";
    return typeof providerId === "string" ? providerId.trim() : "";
  } catch {
    return "";
  }
}

export function saveOnlineParseProviderId(providerId, storage) {
  try {
    const target = browserStorage(storage);
    const selectedProviderId = typeof providerId === "string" ? providerId.trim() : "";
    if (!target) return;
    if (!selectedProviderId) {
      target.removeItem(ONLINE_PARSE_SELECTION_STORAGE_KEY);
      return;
    }
    target.setItem(ONLINE_PARSE_SELECTION_STORAGE_KEY, JSON.stringify({ providerId: selectedProviderId }));
  } catch {
    // Browser storage can be unavailable or blocked; selection remains usable for this page.
  }
}

export function loadOnlineParseEnabled(storage) {
  try {
    const saved = browserStorage(storage)?.getItem(ONLINE_PARSE_ENABLED_STORAGE_KEY);
    if (saved === "true") return true;
    if (saved === "false") return false;
    return true;
  } catch {
    return true;
  }
}

export function saveOnlineParseEnabled(enabled, storage) {
  try {
    const target = browserStorage(storage);
    if (!target) return;
    target.setItem(ONLINE_PARSE_ENABLED_STORAGE_KEY, enabled ? "true" : "false");
  } catch {
    // Browser storage can be unavailable or blocked; the choice remains usable for this page.
  }
}

export function loadMarkdownPipeline(storage) {
  try {
    const saved = browserStorage(storage)?.getItem(MARKDOWN_PIPELINE_STORAGE_KEY);
    return saved === "local" ? "local" : "ai";
  } catch {
    return "ai";
  }
}

export function saveMarkdownPipeline(pipeline, storage) {
  try {
    const target = browserStorage(storage);
    if (!target) return;
    target.setItem(MARKDOWN_PIPELINE_STORAGE_KEY, pipeline === "local" ? "local" : "ai");
  } catch {
    // Browser storage can be unavailable or blocked; the choice remains usable for this page.
  }
}

export function derivedMarkdownPreview(markdown) {
  const frontmatter = typeof markdown === "string"
    ? markdown.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/)
    : null;
  if (!frontmatter?.[1].includes("platform_provenance:")) return markdown;
  return markdown
    .slice(frontmatter[0].length)
    .replace(/^来源：\[\[[^\r\n\]]+\|原始资料\]\]\r?\n(?:\r?\n)?/m, "");
}

function nonEmptyText(value) {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function containsInternalReference(value) {
  return /(?:^graph:|#chunk:|(?:^|[\s:])[a-f0-9]{64}(?=$|[\s:#;])|(?:^|\s)(?:block|unit|paragraph|image|table|line|element[_ -]?path|region):\d+\b)/i.test(value);
}

export function userFacingEvidenceLocation(evidence = {}) {
  const rawLocation = nonEmptyText(evidence.location);
  const parsedHeading = /^heading:\s*([^;]+)/i.exec(rawLocation)?.[1]?.trim() || "";
  const parsedPage = /(?:^|;)\s*page:\s*(\d+)/i.exec(rawLocation)?.[1];
  const headingCandidate = nonEmptyText(evidence.heading) || parsedHeading;
  const heading = containsInternalReference(headingCandidate) ? "" : headingCandidate;
  const page = Number.isInteger(evidence.page) && evidence.page > 0
    ? evidence.page
    : parsedPage ? Number(parsedPage) : null;
  const structuredParts = [heading, page ? `第 ${page} 页` : ""].filter(Boolean);
  if (structuredParts.length) return structuredParts.join(" · ");
  if (!rawLocation) return "";
  return /^第\s*\d+\s*(?:章|节|页|部分)(?:\s*[-—].+)?$/.test(rawLocation) ? rawLocation : "";
}

export function userFacingEvidenceSource(evidence = {}) {
  const sourcePath = nonEmptyText(evidence.source_path ?? evidence.sourcePath);
  if (sourcePath) return `原始资料：${userFacingFileName(sourcePath)}`;
  const identityKind = evidence.identity_kind ?? evidence.identityKind;
  if (identityKind === "derived") return "来源类型：原始资料生成的笔记";
  if (identityKind === "native") return "来源类型：原生 Markdown";
  return "";
}

export function userFacingFileName(path) {
  const normalizedPath = nonEmptyText(path).replaceAll("\\", "/");
  return normalizedPath.split("/").filter(Boolean).at(-1) || "";
}

export function applicationEvidenceAnchorId(key) {
  // Keep fragment targets stable without exposing source identifiers in the DOM.
  let primary = 0x811c9dc5;
  let secondary = 0x9e3779b9;
  for (const character of String(key)) {
    const codePoint = character.codePointAt(0);
    primary = Math.imul(primary ^ codePoint, 0x01000193);
    secondary = Math.imul(secondary ^ codePoint, 0x85ebca6b);
  }
  return `application-evidence-${(primary >>> 0).toString(36)}-${(secondary >>> 0).toString(36)}`;
}

export async function copyPlainText(content, clipboard = globalThis.navigator?.clipboard) {
  const text = typeof content === "string" ? content.trim() : "";
  if (!text || typeof clipboard?.writeText !== "function") return false;
  await clipboard.writeText(text);
  return true;
}

export function userFacingSourceSample(source = {}) {
  return nonEmptyText(source.source_path) || nonEmptyText(source.relative_path);
}

export function userFacingImportLocation(value) {
  const rawLocation = nonEmptyText(value);
  if (!rawLocation) return "";
  const page = /(?:^|\s)(?:page|页)\s*:?\s*(\d+)/i.exec(rawLocation)?.[1]
    || /第\s*(\d+)\s*页/.exec(rawLocation)?.[1];
  if (page) return `第 ${page} 页`;
  if (/docx|ooxml|word\/document|paragraph|element[_ -]?path/i.test(rawLocation)) return "DOCX 内容";
  if (/^graph:|#chunk:|[a-f0-9]{64}|(?:^|\s)(?:block|unit):\d+/i.test(rawLocation)) return "";
  return rawLocation === "document" ? "文档内容" : "";
}

export function userFacingImportIssue(value) {
  const issue = nonEmptyText(value);
  if (!issue) return "";
  const friendlyIssue = issue
    .replace(/^graph:[\s\S]*?#chunk:\s*\d+\s*:\s*/i, "")
    .replace(/^(?:page\s*\d+|第\s*\d+\s*页)(?:\s+(?:box|table|paragraph|line)\s*:?\s*[\d,.-]+)?\s*:\s*/i, "")
    .replace(/^(?:docx|ooxml|word\/document)(?:[^:]*:\s*){1,2}/i, "")
    .replace(/^(?:block|unit|paragraph|image|table|line|element[_ -]?path|region)\s*:\s*[\d,.-]+(?:\s*\/\s*(?:row|cell|paragraph|line)\s*:\s*[\d,.-]+)*(?:\s*:\s*[\d,.-]+)*\s*:\s*/i, "");
  return containsInternalReference(friendlyIssue) ? "" : friendlyIssue;
}

export function ImportParserTag({ engine }) {
  if (!engine) return null;
  return React.createElement(
    "span",
    {
      className: "parser-tag",
      title: `解析器：${engine}`,
      "aria-label": `解析器：${engine}`
    },
    React.createElement("span", { className: "parser-tag-label" }, "解析器"),
    React.createElement("span", { className: "parser-tag-value" }, engine)
  );
}

function evidenceSummaryText(evidence) {
  return userFacingEvidenceLocation(evidence) || "来源详情";
}

function retrievalChannelText(channel) {
  return {
    keyword: "关键词",
    lexical: "关键词",
    semantic: "语义",
    heading: "标题",
    neighborhood: "相邻内容"
  }[channel] || "其他方式";
}

function importLifecycleText(lifecycle) {
  return {
    queued: "排队",
    running: "运行中",
    recoverable: "可恢复",
    failed: "失败",
    cancelled: "已取消",
    complete: "已完成",
    "completed-with-confirmed-gaps": "带已确认缺口完成"
  }[lifecycle] || lifecycle;
}

function importPhaseText(phase) {
  return {
    queued: "排队",
    scanning: "扫描",
    "waiting-for-next-stage": "等待后续处理",
    interrupted: "扫描已中断",
    parsing: "解析",
    converting: "保真转换",
    ocr: "OCR",
    "deriving-markdown": "生成笔记提案",
    committing: "提交",
    indexing: "索引",
    failed: "失败",
    cancelled: "已取消",
    complete: "完成",
    "completed-with-confirmed-gaps": "带已确认缺口完成"
  }[phase] || phase;
}

function importCategoryText(category) {
  return {
    supported: "支持",
    skipped: "跳过",
    unsupported: "不支持",
    failed: "失败"
  }[category] || category;
}

function importDocumentKindText(kind) {
  return {
    pdf: "PDF（电子/扫描待识别）",
    doc: "Word 97-2003",
    docx: "Word DOCX",
    docm: "Word DOCM",
    dotx: "Word 模板 DOTX",
    dotm: "Word 模板 DOTM",
    xls: "Excel 97-2003",
    xlsx: "Excel XLSX",
    xlsm: "Excel XLSM",
    xltx: "Excel 模板 XLTX",
    xltm: "Excel 模板 XLTM",
    markdown: "外部 Markdown"
  }[kind] || "未识别";
}

function importRecoveryActionText(action) {
  return {
    cancel: "取消",
    "restart-scan": "重新扫描",
    "restart-parse": "重新解析",
    "restart-conversion": "重新转换",
    "restart-derivation": "重新生成",
    "restart-ocr": "重新 OCR",
    "retry-commit": "重试提交",
    "create-new-task": "创建新任务"
  }[action] || action;
}

function importIdentityStatusText(status) {
  return {
    new: "新资料",
    duplicate: "重复资料",
    "identity-failed": "识别失败",
    "not-applicable": "不适用"
  }[status] || "待识别";
}

function importParseStatusText(status) {
  return {
    "not-applicable": "不适用",
    pending: "待解析",
    parsed: "已解析",
    "parse-failed": "解析失败"
  }[status] || "未解析";
}

function importConversionStatusText(status) {
  return {
    "not-applicable": "未转换",
    pending: "待转换",
    selected: "已选择完整转换图",
    rejected: "转换失败"
  }[status] || "转换未就绪";
}

function importOcrStatusText(status) {
  return {
    "not-applicable": "不适用",
    "not-required": "无需 OCR",
    "ocr-processing": "OCR 中",
    "ocr-completed": "OCR 完成",
    "ocr-failed": "OCR 失败",
    "required-check": "OCR 需重试",
    "completed-with-confirmed-gaps": "带已确认缺口完成"
  }[status] || "待 OCR";
}

function importOcrTargetStatusText(status) {
  return { processing: "处理中", completed: "已完成", failed: "失败" }[status] || status;
}

const DOCUMENT_BLOCK_KINDS = ["heading", "paragraph", "list", "table", "formula", "image", "caption", "code", "unresolved"];

export function conversionItemIdFromReviewItem(reviewItemId) {
  const matched = /^conversion-(\d+)(?:-|$)/.exec(reviewItemId || "");
  return matched ? Number(matched[1]) : null;
}

function conversionReviewHasGraphIssue(reviewItemId) {
  const parts = (reviewItemId || "").split("-");
  return parts.length >= 4 && /^\d+$/.test(parts[1]) && /^\d+$/.test(parts.at(-1));
}

export function conversionCorrectionDraft(draft = {}) {
  const blockId = draft.block_id?.trim();
  const kind = draft.kind || "paragraph";
  const retrievalProjection = draft.retrieval_projection?.trim();
  const reason = draft.reason?.trim();
  if (!blockId) return { error: "请提供要替换的转换块。" };
  if (!DOCUMENT_BLOCK_KINDS.includes(kind)) return { error: "请选择受支持的转换块类型。" };
  if (!draft.payload?.trim()) return { error: "请提供符合块类型的 JSON 内容。" };
  let payload;
  try {
    payload = JSON.parse(draft.payload);
  } catch {
    return { error: "修正内容必须是有效 JSON。" };
  }
  if (payload === null || Array.isArray(payload) || typeof payload !== "object") {
    return { error: "修正内容必须是 JSON 对象。" };
  }
  if (!retrievalProjection) return { error: "请提供检索投影。" };
  if (!reason) return { error: "请说明本次结构修正的理由。" };
  return { blockId, kind, payload, retrievalProjection, reason, error: "" };
}

function sourceLocatorText(locator) {
  if (locator.type === "pdf-region") return `第 ${locator.page} 页`;
  if (locator.type === "docx-ooxml") return "DOCX 内容";
  if (locator.type === "source-scope") return "原始资料";
  return locator.page ? `第 ${locator.page} 页` : "文档内容";
}

export function ConversionReviewControls({
  reviewItem,
  lifecycle,
  isActing,
  draft,
  blocks,
  onDraftChange,
  onRetry,
  onCorrect
}) {
  if (
    reviewItem.object_type !== "conversion"
    || !["required-check", "blocking"].includes(reviewItem.risk)
  ) return null;
  const itemId = conversionItemIdFromReviewItem(reviewItem.review_item_id);
  const canAct = lifecycle === "waiting-for-review" && !isActing && itemId !== null;
  const correction = conversionCorrectionDraft(draft);
  const retryTitle = canAct ? undefined : "转换重试只能在等待审核时执行。";
  const correctionTitle = !canAct ? retryTitle : correction.error || undefined;
  const availableBlocks = blocks || [];
  return React.createElement(
    "div",
    { className: "conversion-remediation-controls", "aria-label": "转换整改操作" },
    availableBlocks.length
      ? React.createElement(
          "select",
          {
            value: draft.block_id || "",
            disabled: !canAct,
            onChange: (event) => onDraftChange("block_id", event.target.value),
            "aria-label": "要修正的转换块"
          },
          React.createElement("option", { value: "" }, "选择转换块"),
          availableBlocks.map((block) => React.createElement(
            "option",
            { key: block.block_id, value: block.block_id },
            `${block.kind} · ${(block.locators || []).map(sourceLocatorText).join("、")}`
          ))
        )
      : React.createElement("input", {
          type: "text",
          value: draft.block_id || "",
          disabled: !canAct,
          onChange: (event) => onDraftChange("block_id", event.target.value),
          "aria-label": "要修正的转换块",
          placeholder: "转换块"
        }),
    React.createElement(
      "select",
      {
        value: draft.kind || "paragraph",
        disabled: !canAct,
        onChange: (event) => onDraftChange("kind", event.target.value),
        "aria-label": "修正后的块类型"
      },
      DOCUMENT_BLOCK_KINDS.map((kind) => React.createElement("option", { key: kind, value: kind }, kind))
    ),
    React.createElement("textarea", {
      value: draft.payload || "",
      disabled: !canAct,
      onChange: (event) => onDraftChange("payload", event.target.value),
      "aria-label": "修正后的块 JSON 内容",
      placeholder: "块 JSON"
    }),
    React.createElement("textarea", {
      value: draft.retrieval_projection || "",
      disabled: !canAct,
      onChange: (event) => onDraftChange("retrieval_projection", event.target.value),
      "aria-label": "修正后的检索投影",
      placeholder: "检索投影"
    }),
    React.createElement("input", {
      type: "text",
      value: draft.reason || "",
      disabled: !canAct,
      onChange: (event) => onDraftChange("reason", event.target.value),
      "aria-label": "结构修正理由",
      placeholder: "结构修正理由"
    }),
    React.createElement("button", {
      type: "button",
      className: "secondary-button",
      disabled: !canAct,
      title: retryTitle,
      onClick: onRetry
    }, "重试转换"),
    React.createElement("button", {
      type: "button",
      className: "secondary-button",
      disabled: !canAct || Boolean(correction.error),
      title: correctionTitle,
      onClick: onCorrect
    }, "保存结构修正"),
    correction.error
      ? React.createElement("span", { className: "row-note", role: "status" }, correction.error)
      : null
  );
}

function progressPhaseStatus(task, phase) {
  if (task.phase === phase) return "当前";
  if (["complete", "completed-with-confirmed-gaps"].includes(task.phase)) return "已完成";
  if (task.phase === "waiting-for-next-stage") {
    return ["queued", "scanning"].includes(phase) ? "已完成" : "未开始";
  }
  const activeIndex = IMPORT_PROGRESS_PHASES.indexOf(task.phase);
  const phaseIndex = IMPORT_PROGRESS_PHASES.indexOf(phase);
  if (activeIndex > phaseIndex) return "已完成";
  return "未开始";
}

function policyFor(vault) {
  return vault.policy || {
    outbound_mode: "always-allow",
    policy_revision: 1,
    rules: []
  };
}

function outboundModeText() {
  return "默认允许";
}

function ruleReason(kind) {
  if (kind === "completely-ignore") return "命中时阻止导入、索引、检索和外发";
  if (kind === "do-not-index") return "命中时阻止索引和私有检索";
  return "命中时绝不允许外发";
}

function rulePreviewStage(kind) {
  if (kind === "do-not-index") return "index";
  if (kind === "never-send-cloud") return "outbound";
  return "import";
}

function policyEndpoint(vaultId) {
  return `${VAULTS_ENDPOINT}/${vaultId}/policy`;
}

function requestJson(endpoint, options = {}) {
  const isFormData = typeof globalThis.FormData !== "undefined" && options.body instanceof globalThis.FormData;
  return fetch(endpoint, {
    ...options,
    headers: {
      ...(options.body && !isFormData ? { "Content-Type": "application/json" } : {}),
      ...options.headers
    }
  }).then(async (response) => {
    const payload = response.status === 204 ? {} : await response.json();
    if (!response.ok) {
      const message = nonEmptyText(payload?.message) || nonEmptyText(payload?.detail?.message);
      throw new Error(message || "请求未完成。");
    }
    return payload;
  });
}

export async function readServerSentEvents(response, handlers = {}) {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const message = nonEmptyText(payload?.message) || nonEmptyText(payload?.detail?.message);
    throw new Error(message || "请求未完成。");
  }
  if (!response.body?.getReader) throw new Error("流式响应不可用。");

  const reader = response.body.getReader();
  const decoder = new globalThis.TextDecoder();
  let buffer = "";
  const dispatch = (frame) => {
    const event = frame.split(/\r?\n/).find((line) => line.startsWith("event:"))?.slice(6).trim() || "message";
    const data = frame.split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) return;
    let payload;
    try {
      payload = JSON.parse(data);
    } catch {
      return;
    }
    if (event === "chunk") handlers.onChunk?.(payload);
    else if (event === "result") handlers.onResult?.(payload);
    else if (event === "error") handlers.onError?.(payload);
  };

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() || "";
    frames.forEach(dispatch);
    if (done) break;
  }
  if (buffer.trim()) dispatch(buffer);
}

function vaultName(vault) {
  return vault.display_name || vault.path?.replace(/\\/g, "/").split("/").at(-1) || "未命名知识库";
}

function sessionVaultName(session, vaults) {
  const vault = vaults.find((candidate) => candidate.vault_id === session.selected_vault_id);
  return vault ? vaultName(vault) : session.selected_vault_label || "未设置";
}

function statusText(vault) {
  if (vault.authorization_status === "inactive") return "已停用";
  if (vault.access_status !== "available") return "路径不可用";
  return "已授权";
}

function apiModeLabel(provider) {
  return provider.api_mode === "responses" ? "Responses API" : "Chat Completions";
}

function verifiedProviderModels(provider) {
  return (provider.models || []).filter((model) => model.is_discovered && model.verification?.ok);
}

function configuredProviderModels(provider) {
  return (provider.models || []).filter((model) => model.is_discovered && model.model_type);
}

function unconfiguredProviderModels(provider) {
  return (provider.models || []).filter((model) => model.is_discovered && !model.model_type);
}

function userFacingProviderReason(reason) {
  const normalized = nonEmptyText(reason);
  const exact = {
    "Not yet verified.": "尚未验证。",
    "Credential is unavailable.": "未能读取 API Key。请重新保存 Provider 凭据后重试。",
    "Model discovery could not be completed.": "模型发现失败。请检查服务地址、网络和 API Key。",
    "Provider health check could not be completed.": "服务健康检查失败。请检查服务地址、网络和 API Key。",
    "Provider request timed out.": "Provider 请求超时。请检查网络或稍后重试。",
    "Provider hostname could not be resolved.": "无法解析 Provider 域名。请检查服务地址和网络。",
    "Provider connection was refused.": "Provider 拒绝连接。请检查服务地址和服务状态。",
    "Provider TLS connection failed.": "Provider TLS 连接失败。请检查服务地址或证书后重试。",
    "Provider request could not be completed.": "Provider 请求未完成。请检查网络后重试。",
    "Choose a model type before testing the model.": "请先选择模型类型，再进行验证。",
    "Run Provider discovery before testing this model.": "请先测试 Provider，完成模型发现后再验证模型。",
    "The model must appear in the latest successful Provider discovery.": "该模型不在最近一次发现结果中。请先重新测试 Provider。"
  };
  if (exact[normalized]) return exact[normalized];
  const verification = /^(?:Chat|Embedding|Rerank) model verification could not be completed\.\s*(.*)$/.exec(normalized);
  if (verification) {
    const detail = nonEmptyText(verification[1]);
    return detail ? `模型验证失败：${userFacingProviderReason(detail)}` : "模型验证失败。请重试。";
  }
  const http = /^Provider request failed with HTTP (\d{3})\.$/.exec(normalized);
  if (http) {
    const status = http[1];
    const action = status === "401" || status === "403"
      ? "请检查 API Key 和模型权限。"
      : status === "404"
        ? "请检查服务地址和模型是否可用。"
        : "请稍后重试，或检查 Provider 服务状态。";
    return `Provider 返回 HTTP ${status}。${action}`;
  }
  return normalized || "未返回失败原因。";
}

function modelOptions(providers, modelType) {
  return providers.flatMap((provider) => (
    provider.verification.is_verified
      ? provider.models
        .filter((model) => model.model_type === modelType && model.verification.ok && model.is_discovered)
        .map((model) => ({ provider, model }))
      : []
  ));
}

function modelTypeLabel(modelType) {
  const labels = {
    chat: "对话/文本生成",
    embedding: "Embedding",
    rerank: "Rerank（重排）",
    markdown: "Markdown 结构化"
  };
  return labels[modelType] || modelType;
}

function sessionComposerContext(session) {
  return {
    vault_id: session?.selected_vault_id || "",
    scope_kind: session?.scope_kind || "vault",
    scope_path: session?.scope_path || "",
    provider_id: session?.selected_provider_id || "",
    model_id: session?.selected_model_id || ""
  };
}

export function conversationTurns(detail = {}) {
  const entries = [
    ...(detail.messages || []).map((value) => ({ kind: "message", value })),
    ...(detail.generation_results || []).map((value) => ({ kind: "generation", value })),
    ...(detail.retrieval_results || []).map((value) => ({ kind: "retrieval", value })),
    ...(detail.completeness_results || []).map((value) => ({ kind: "completeness", value })),
    ...(detail.knowledge_organization_results || []).map((value) => ({ kind: "organization", value })),
    ...(detail.deep_creation_results || []).map((value) => ({ kind: "deep-creation", value }))
  ]
    .sort((first, second) => (first.value.created_at || "").localeCompare(second.value.created_at || ""))
    .map((entry, index) => ({
      ...entry,
      key: `${entry.kind}:${entry.value.message_id || entry.value.result_id || entry.value.task_id || index}`
    }));

  const turns = [];
  entries.forEach((entry) => {
    const startsTurn = entry.kind === "message" && entry.value.role === "user";
    if (startsTurn || !turns.length) {
      turns.push({
        id: `session-turn-${entry.key}`,
        question: startsTurn ? nonEmptyText(entry.value.content) : "未命名问答",
        entries: []
      });
    }
    turns.at(-1).entries.push(entry);
  });
  return turns;
}

function IconButton({ icon: Icon, label, className = "icon-button", ...buttonProps }) {
  return React.createElement(
    "button",
    {
      ...buttonProps,
      className,
      title: buttonProps.title || label,
      "aria-label": label
    },
    React.createElement(Icon, { "aria-hidden": "true", size: 18, strokeWidth: 2 })
  );
}

function NavigationLinks({ activeDestination, firstLinkRef, onNavigate }) {
  return NAVIGATION_DESTINATIONS.map((destination, index) =>
    React.createElement(
      "a",
      {
        className: "navigation-link",
        href: `#${destination.id}`,
        key: destination.id,
        ref: index === 0 ? firstLinkRef : undefined,
        "aria-current": activeDestination === destination.id ? "page" : undefined,
        onClick: (event) => {
          event.preventDefault();
          onNavigate(destination.id);
        }
      },
      React.createElement(NAVIGATION_ICONS[destination.id], { "aria-hidden": "true", size: 18, strokeWidth: 2 }),
      React.createElement("span", { className: "navigation-link-label" }, destination.label)
    )
  );
}

function VaultForm({ vault, onCancel, onComplete }) {
  const [selectionId, setSelectionId] = React.useState("");
  const [selectionLabel, setSelectionLabel] = React.useState("");
  const [managedRoot, setManagedRoot] = React.useState(
    vault?.managed_root_relative_path || "platform"
  );
  const [status, setStatus] = React.useState("");
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const isRelinking = Boolean(vault);

  async function chooseDirectory() {
    setStatus("");
    setIsSubmitting(true);
    try {
      const response = await requestJson(VAULT_DIRECTORY_PICKER_ENDPOINT, { method: "POST" });
      if (response.selection_id) {
        setSelectionId(response.selection_id);
        setSelectionLabel(response.label || "已选择本机目录");
      }
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function submit(event) {
    event.preventDefault();
    if (!selectionId) return;
    setStatus("");
    setIsSubmitting(true);
    try {
      const endpoint = isRelinking ? `${VAULTS_ENDPOINT}/${vault.vault_id}/path` : VAULTS_ENDPOINT;
      const response = await requestJson(endpoint, {
        method: isRelinking ? "PUT" : "POST",
        body: JSON.stringify({ selection_id: selectionId, managed_root: managedRoot })
      });
      onComplete(response.vault);
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return React.createElement(
    "form",
    { className: "vault-form", onSubmit: submit, "aria-label": isRelinking ? "重新关联 vault" : "添加 vault" },
    React.createElement("h2", null, isRelinking ? "重新关联 vault" : "添加 vault"),
    React.createElement(
      "p",
      { className: "form-description" },
      "通过 Windows 本机路径选择器授权一个可访问的 Obsidian vault。"
    ),
    React.createElement(
      "div",
      { className: "form-row" },
      React.createElement("span", { className: "form-label" }, "本机路径"),
      React.createElement(
        "output",
        { className: "path-selection", "aria-live": "polite" },
        selectionLabel || "尚未选择路径"
      ),
      React.createElement(
        "button",
        { className: "secondary-button", type: "button", disabled: isSubmitting, onClick: chooseDirectory },
        "选择本机路径"
      )
    ),
    React.createElement(
      "label",
      { className: "form-row", htmlFor: "managed-root" },
      React.createElement("span", { className: "form-label" }, "受管根目录"),
      React.createElement("input", {
        id: "managed-root",
        value: managedRoot,
        required: true,
        disabled: isSubmitting,
        onChange: (event) => setManagedRoot(event.target.value)
      }),
      React.createElement("span", { className: "form-help" }, "源文件和派生笔记会分别存入其中的固定目录。")
    ),
    status
      ? React.createElement("p", { className: "form-error", role: "alert" }, status)
      : null,
    React.createElement(
      "div",
      { className: "form-actions" },
      React.createElement("button", { className: "secondary-button", type: "button", onClick: onCancel }, "取消"),
      React.createElement(
        "button",
        {
          className: "primary-button",
          type: "submit",
          disabled: !selectionId || !managedRoot || isSubmitting,
          "aria-describedby": selectionId ? undefined : "vault-path-required"
        },
        isRelinking ? "重新关联" : "授权 vault"
      )
    ),
    !selectionId
      ? React.createElement("p", { id: "vault-path-required", className: "form-help" }, "请先选择本机路径。")
      : null
  );
}

function ConfirmationPanel({ request, error, isSubmitting, onClose, onConfirm }) {
  const panelRef = React.useRef(null);

  React.useEffect(() => {
    panelRef.current?.querySelector("button")?.focus();
  }, []);

  function handleKeyDown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      if (request.kind !== "session-remove") onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const controls = [...panelRef.current.querySelectorAll("button:not([disabled])")];
    const first = controls[0];
    const last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  const isProviderRemoval = request.kind === "provider-remove";
  const isModelRemoval = request.kind === "provider-model-remove";
  const isSessionRemoval = request.kind === "session-remove";
  const isRemoval = request.kind === "remove" || isProviderRemoval;
  const targetName = isSessionRemoval
    ? request.target.title
    : isProviderRemoval ? request.target.name
      : isModelRemoval ? request.target.model_id
        : vaultName(request.target);
  return React.createElement(
    "div",
    { className: "confirmation-overlay" },
    React.createElement(
      "section",
      {
        className: "confirmation-panel",
        ref: panelRef,
        role: "dialog",
        "aria-modal": "true",
        "aria-labelledby": "confirmation-title",
        onKeyDown: handleKeyDown
      },
      React.createElement(
        "h2",
        { id: "confirmation-title" },
        isSessionRemoval ? `删除会话“${targetName}”？` : isProviderRemoval ? "删除 Provider" : isModelRemoval ? "删除模型" : isRemoval ? "移除 vault 授权" : "停用 vault"
      ),
      React.createElement(
        "p",
        null,
        isSessionRemoval
          ? "这会删除该会话的私有消息、范围、模型记录、任务状态、引用和结果。不会删除、移动或改写已审核写入 vault 的资料、笔记或标签。"
          : isProviderRemoval
          ? `将删除“${targetName}”的应用内配置、模型缓存和 Windows 凭据，并使关联外发记录失效。`
          : isModelRemoval
            ? `将删除“${targetName}”的模型类型、验证结果和本地缓存。若它是默认模型，对应默认设置也会被清除。`
          : isRemoval
            ? `将移除“${targetName}”的应用内授权与私有状态。不会删除、移动或改写 vault 中的文件。`
            : `将停止“${targetName}”的新写入依赖操作。现有 vault 文件、应用记录和本地结果会保留。`
      ),
      React.createElement(
        "div",
        { className: "form-actions" },
        error ? React.createElement("p", { className: "form-error", role: "alert" }, error) : null,
        React.createElement(
          "button",
          { className: "secondary-button", type: "button", disabled: isSubmitting, onClick: onClose },
          "取消"
        ),
        React.createElement(
          "button",
          { className: "danger-button", type: "button", disabled: isSubmitting, onClick: onConfirm },
          isSessionRemoval ? "删除会话" : isProviderRemoval ? "删除 Provider" : isModelRemoval ? "删除模型" : isRemoval ? "移除授权" : "停用"
        )
      )
    )
  );
}

export function SessionManagement({
  sessionPage,
  filters,
  isLoading,
  error,
  selectedSessionId,
  selectedDetail,
  isDetailLoading,
  detailError,
  onLoad,
  onSelect,
  onCreate,
  onRename,
  onExport,
  onDelete,
  vaults = [],
  providers = [],
  onPickAttachments,
  onRemoveAttachment,
  onRun,
  retrievalMode = { mode: "keyword", options: [] },
  retrievalModeLoading = false,
  onRetrievalModeChange = async () => retrievalMode,
  onLoadCompletenessCoverage,
  onEditGenerationResult,
  onReverifyGenerationResult
}) {
  const [query, setQuery] = React.useState(filters.query || "");
  const [editingSessionId, setEditingSessionId] = React.useState(null);
  const [editingTitle, setEditingTitle] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [retrievalModeError, setRetrievalModeError] = React.useState("");
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [isStreaming, setIsStreaming] = React.useState(false);
  const [streamingContent, setStreamingContent] = React.useState("");
  const [context, setContext] = React.useState({ vault_id: "", scope_kind: "vault", scope_path: "", provider_id: "", model_id: "" });
  const [message, setMessage] = React.useState("");
  const [taskIntent, setTaskIntent] = React.useState("auto");
  const [coveragePages, setCoveragePages] = React.useState({});
  const [editingGenerationResultId, setEditingGenerationResultId] = React.useState(null);
  const [editingGenerationContent, setEditingGenerationContent] = React.useState("");
  const [mobileSessionView, setMobileSessionView] = React.useState("history");
  const [isEvidenceSheetOpen, setEvidenceSheetOpen] = React.useState(false);
  const [activeEvidenceKey, setActiveEvidenceKey] = React.useState(null);
  const renameInputRef = React.useRef(null);
  const messageListRef = React.useRef(null);
  const evidenceSheetCloseButtonRef = React.useRef(null);
  const renderedSessionIdRef = React.useRef(null);
  const conversationSignatureRef = React.useRef("");
  const streamingOrdinalRef = React.useRef(null);
  const shouldStickToLatestRef = React.useRef(true);
  const [activeConversationTurnId, setActiveConversationTurnId] = React.useState(null);
  const page = sessionPage?.page || 1;
  const totalPages = sessionPage?.total_pages || 1;
  const sessions = sessionPage?.sessions || [];
  const activeDetail = selectedDetail?.session?.session_id === selectedSessionId
    ? selectedDetail
    : null;
  const selectedSession = activeDetail?.session
    || sessions.find((session) => session.session_id === selectedSessionId)
    || null;
  const retrievalResults = activeDetail?.retrieval_results || [];
  const completenessResults = activeDetail?.completeness_results || [];
  const knowledgeOrganizationResults = activeDetail?.knowledge_organization_results || [];
  const deepCreationResults = activeDetail?.deep_creation_results || [];
  const generationResults = activeDetail?.generation_results || [];
  const conversationTurnItems = conversationTurns(activeDetail || {});
  const conversationSignature = conversationTurnItems.map((turn) => turn.id).join("|");
  const snapshotsById = new Map((activeDetail?.task_snapshots || []).map((snapshot) => [snapshot.snapshot_id, snapshot]));
  const vaultsById = new Map(vaults.map((vault) => [vault.vault_id, vault]));
  const applicationEvidenceItemsByKey = new Map();

  React.useEffect(() => {
    setQuery(filters.query || "");
  }, [filters.query]);

  React.useEffect(() => {
    setCoveragePages({});
  }, [activeDetail]);

  React.useEffect(() => {
    if (editingSessionId) renameInputRef.current?.focus();
  }, [editingSessionId, selectedSession?.session_id]);

  React.useEffect(() => {
    if (!selectedSession) return;
    setContext(sessionComposerContext(selectedSession));
    setMessage("");
    setTaskIntent("auto");
    setIsStreaming(false);
    setStreamingContent("");
    streamingOrdinalRef.current = null;
  }, [selectedSession?.session_id]);

  React.useEffect(() => {
    if (!isEvidenceSheetOpen) return undefined;
    const focusTarget = () => {
      const target = activeEvidenceKey
        ? globalThis.document?.getElementById(`${applicationEvidenceAnchorId(activeEvidenceKey)}-sheet`)
        : evidenceSheetCloseButtonRef.current;
      (target || evidenceSheetCloseButtonRef.current)?.focus();
    };
    const frame = globalThis.requestAnimationFrame?.(focusTarget);
    return () => {
      if (frame !== undefined) globalThis.cancelAnimationFrame?.(frame);
    };
  }, [activeEvidenceKey, isEvidenceSheetOpen]);

  React.useLayoutEffect(() => {
    const messageList = messageListRef.current;
    if (!messageList || !activeDetail || !conversationTurnItems.length) return;
    const sessionId = activeDetail.session.session_id;
    const isNewSession = renderedSessionIdRef.current !== sessionId;
    const hasNewConversationContent = conversationSignatureRef.current !== conversationSignature;
    if (isNewSession || (hasNewConversationContent && shouldStickToLatestRef.current)) {
      messageList.scrollTop = messageList.scrollHeight;
      setActiveConversationTurnId(conversationTurnItems.at(-1).id);
    }
    renderedSessionIdRef.current = sessionId;
    conversationSignatureRef.current = conversationSignature;
  }, [activeDetail?.session?.session_id, conversationSignature, conversationTurnItems.length]);

  React.useLayoutEffect(() => {
    const messageList = messageListRef.current;
    if (!messageList || !isStreaming || !shouldStickToLatestRef.current) return;
    messageList.scrollTop = messageList.scrollHeight;
  }, [isStreaming, streamingContent]);

  React.useEffect(() => {
    const messageList = messageListRef.current;
    if (!messageList || !activeDetail || !conversationTurnItems.length || typeof globalThis.IntersectionObserver !== "function") return undefined;
    const observer = new globalThis.IntersectionObserver((entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((first, second) => first.boundingClientRect.top - second.boundingClientRect.top);
      if (visible.length) setActiveConversationTurnId(visible[0].target.id);
    }, { root: messageList, rootMargin: "-12% 0px -62%", threshold: 0 });
    conversationTurnItems.forEach((turn) => {
      const target = globalThis.document?.getElementById(turn.id);
      if (target) observer.observe(target);
    });
    return () => observer.disconnect();
  }, [activeDetail?.session?.session_id, conversationSignature]);

  function load(nextFilters) {
    setStatus("");
    onLoad(nextFilters);
  }

  function openRename(session) {
    setStatus("");
    setEditingSessionId(session.session_id);
    setEditingTitle(session.title);
  }

  async function createSession() {
    setStatus("");
    setIsSubmitting(true);
    try {
      const session = await onCreate();
      openRename(session);
      setMobileSessionView("conversation");
    } catch (requestError) {
      setStatus(requestError.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function saveRename(event, sessionId) {
    event.preventDefault();
    setStatus("");
    setIsSubmitting(true);
    try {
      await onRename(sessionId, editingTitle);
      setEditingSessionId(null);
    } catch (requestError) {
      setStatus(requestError.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function exportSession(session) {
    setStatus("");
    try {
      await onExport(session);
    } catch (requestError) {
      setStatus(requestError.message);
    }
  }

  async function copyContent(content) {
    try {
      const copied = await copyPlainText(content);
      setStatus(copied ? "已复制正文。" : "当前浏览器无法复制正文。");
    } catch (copyError) {
      setStatus(copyError.message || "复制正文失败。");
    }
  }

  function citationStatusText(status) {
    if (status === "stale") return "已失效";
    if (status === "pending-verification") return "待核验";
    if (status === "unsupported" || status === "invalid") return "无证据";
    return "有效";
  }

  function generationStatusText(result) {
    if (result.status === "pending-verification") return "内容待确认";
    if (result.status === "stale") return "内容需重新确认";
    if (result.status === "verifying") return "正在确认内容";
    if (result.status === "unsupported") return "当前内容不可用";
    return result.content_origin === "user-content" ? "我的内容" : "助手";
  }

  function messageRoleText(role) {
    if (role === "user") return "我的消息";
    if (role === "assistant") return "助手";
    return "系统";
  }

  function attachmentStatusText(attachmentStatus) {
    return {
      available: "可用",
      excluded: "被排除",
      "needs-import": "待解析（需先导入）"
    }[attachmentStatus] || "待解析";
  }

  const availableVaults = vaults.filter((vault) => (
    vault.authorization_status === "active" && vault.access_status === "available"
  ));
  const chatModels = providers.flatMap((provider) => (
    provider.verification?.is_verified
      ? (provider.models || []).filter((model) => (
        model.model_type === "chat" && model.is_discovered && model.verification?.ok
      )).map((model) => ({ provider, model }))
      : []
  ));
  const persistedContext = sessionComposerContext(selectedSession);
  const contextIsDirty = Object.keys(context).some((key) => context[key] !== persistedContext[key]);
  const canSend = Boolean(
    selectedSession && context.vault_id && context.provider_id && context.model_id && message.trim()
      && (context.scope_kind === "vault" || context.scope_path.trim())
  );

  async function pickAttachments() {
    if (!selectedSession || contextIsDirty) return;
    setStatus("");
    setIsSubmitting(true);
    try {
      await onPickAttachments(selectedSession.session_id);
      setStatus("附件已添加。");
    } catch (requestError) {
      setStatus(requestError.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function removeAttachment(attachmentId) {
    if (!selectedSession) return;
    setStatus("");
    setIsSubmitting(true);
    try {
      await onRemoveAttachment(selectedSession.session_id, attachmentId);
      setStatus("附件已移除。");
    } catch (requestError) {
      setStatus(requestError.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function changeRetrievalMode(mode) {
    if (!selectedSession || retrievalModeLoading) return;
    setRetrievalModeError("");
    try {
      await onRetrievalModeChange(mode);
    } catch (requestError) {
      setRetrievalModeError(requestError.message);
    }
  }

  function retrievalStatusText(result) {
    if (result.is_stale || result.snapshot_status === "invalidated") return "当前内容需要重新确认";
    return {
      completed: "内容已准备",
      "no-evidence": "当前范围内没有可回答的内容",
      excluded: "内容被排除",
      "index-unavailable": "当前内容暂时不可用",
      "provider-model-unavailable": "当前无法生成内容"
    }[result.status] || "当前无法处理请求";
  }

  function retrievalResultView(result) {
    const snapshot = snapshotsById.get(result.snapshot_id);
    const staleReason = result.invalidation_reason || snapshot?.invalidation_reason;
    const hasGeneratedAnswer = retrievalResultHasGeneratedAnswer(result);
    // The generated answer renders its own valid or re-verification status.
    if (result.status === "completed" && hasGeneratedAnswer) return null;
    const evidenceKeys = retrievalEvidenceKeys(result);
    const summary = result.status === "no-evidence"
      ? "当前范围内没有可回答的内容。"
      : result.status === "completed"
        ? /回答生成失败|未生成回答/.test(result.summary || "")
          ? directSummary(result.summary, "暂未生成可用回答。")
          : "暂未生成可用回答。"
        : directSummary(result.summary, retrievalStatusText(result));
    return React.createElement(
      "section",
      { className: "session-retrieval-result", key: result.result_id, "aria-label": retrievalStatusText(result) },
      React.createElement("p", { className: "session-message-role" }, retrievalStatusText(result)),
      summary ? contentWithEvidence(summary, evidenceKeys) : null,
      staleReason
        ? React.createElement("p", { className: "form-error" }, `需重新准备：${staleReason}`)
        : null,
      result.recovery_action
        ? React.createElement("p", { className: "form-error" }, `下一步：${result.recovery_action}`)
        : null,
    );
  }

  function applicationEvidenceLinks(keys) {
    const linkedKeys = [...new Set(keys || [])].filter((key) => applicationEvidenceItemsByKey.has(key));
    if (!linkedKeys.length) return null;
    return React.createElement(
      "sup",
      { className: "application-evidence-links", "data-copy-exclude": "true" },
      linkedKeys.map((key, index) => {
        const item = applicationEvidenceItemsByKey.get(key);
        const sourceLabel = userFacingFileName(item?.relativePath) || `第 ${index + 1} 条来源`;
        return React.createElement(
          "a",
          {
            className: "application-evidence-link",
            href: `#${applicationEvidenceAnchorId(key)}`,
            key,
            "aria-label": `查看来源 ${sourceLabel}`,
            title: "查看应用证据",
            "data-copy-exclude": "true",
            onClick: (event) => {
              event.preventDefault();
              focusApplicationEvidence(key);
            }
          },
          `[${index + 1}]`
        );
      })
    );
  }

  function focusApplicationEvidence(key) {
    if (globalThis.matchMedia?.("(max-width: 1099px)")?.matches) {
      setActiveEvidenceKey(key);
      setEvidenceSheetOpen(true);
      return;
    }
    const focusTarget = () => globalThis.document?.getElementById(applicationEvidenceAnchorId(key))?.focus();
    if (typeof globalThis.requestAnimationFrame === "function") {
      globalThis.requestAnimationFrame(focusTarget);
    } else {
      focusTarget();
    }
  }

  function contentWithEvidence(content, keys) {
    return React.createElement(
      "p",
      { className: "session-answer-content" },
      React.createElement("span", { className: "session-message-content" }, content),
      applicationEvidenceLinks(keys)
    );
  }

  function copyContentButton(content, label = "复制正文") {
    return React.createElement(
      "button",
      { className: "text-button session-copy-button", type: "button", onClick: () => copyContent(content) },
      label
    );
  }

  function conversationEntryView(entry) {
    if (entry.kind === "generation") return generationResultView(entry.value);
    if (entry.kind === "retrieval") return retrievalResultView(entry.value);
    if (entry.kind === "completeness") return completenessResultView(entry.value);
    if (entry.kind === "organization") return knowledgeOrganizationResultView(entry.value);
    if (entry.kind === "deep-creation") return deepCreationResultView(entry.value);
    return React.createElement(
      "article",
      { className: `session-message session-message-${entry.value.role}`, key: entry.value.message_id },
      React.createElement("p", { className: "session-message-role" }, messageRoleText(entry.value.role)),
      React.createElement("p", { className: "session-message-content" }, entry.value.content)
    );
  }

  function scrollToConversationTurn(turn) {
    const target = globalThis.document?.getElementById(turn.id);
    if (!target) return;
    shouldStickToLatestRef.current = false;
    setActiveConversationTurnId(turn.id);
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    target.focus({ preventScroll: true });
  }

  function updateConversationScroll(event) {
    const { clientHeight, scrollHeight, scrollTop } = event.currentTarget;
    shouldStickToLatestRef.current = scrollTop + clientHeight >= scrollHeight - 24;
  }

  function citationEvidenceKeysForResult(result) {
    const citations = activeDetail?.citations || [];
    const direct = citations.filter((citation) => citation.result_id === result.result_id);
    const related = direct.length
      ? direct
      : citations.filter((citation) => citation.snapshot_id && citation.snapshot_id === result.snapshot_id);
    return related.map((citation) => `citation:${citation.citation_id}`);
  }

  function retrievalEvidenceKeys(result) {
    const citations = activeDetail?.citations || [];
    const citedKeys = citations
      .filter((citation) => citationBelongsToRetrievalResult(citation, result))
      .map((citation) => `citation:${citation.citation_id}`);
    const uncitedKeys = (result.evidences || [])
      .filter((evidence) => !citations.some((citation) => (
        citationBelongsToRetrievalResult(citation, result)
        && citationMatchesEvidence(citation, evidence)
      )))
      .map((evidence) => retrievalEvidenceKey(result, evidence));
    return [...new Set([...citedKeys, ...uncitedKeys])];
  }

  function generationResultView(result) {
    const isEditing = editingGenerationResultId === result.result_id;
    const canVerify = ["pending-verification", "stale", "unsupported"].includes(result.status);
    const evidenceKeys = citationEvidenceKeysForResult(result);
    return React.createElement(
      "article",
      { className: "session-message session-generation-result", key: result.result_id },
      React.createElement("p", { className: "session-message-role" }, generationStatusText(result)),
      isEditing
        ? React.createElement("textarea", {
          className: "session-generation-editor",
          value: editingGenerationContent,
          "aria-label": "编辑回答",
          disabled: isSubmitting,
          onChange: (event) => setEditingGenerationContent(event.target.value)
        })
        : contentWithEvidence(result.content, evidenceKeys),
      React.createElement(
        "div",
        { className: "session-generation-actions" },
        isEditing
          ? React.createElement(
              React.Fragment,
              null,
              React.createElement("button", {
                className: "secondary-button", type: "button", disabled: isSubmitting,
                onClick: () => { setEditingGenerationResultId(null); setEditingGenerationContent(""); }
              }, "取消"),
              React.createElement("button", {
                className: "primary-button", type: "button", disabled: isSubmitting || !editingGenerationContent.trim(),
                onClick: async () => {
                  if (!selectedSession || !onEditGenerationResult) return;
                  setIsSubmitting(true);
                  setStatus("");
                  try {
                    await onEditGenerationResult(selectedSession.session_id, result.result_id, editingGenerationContent);
                    setEditingGenerationResultId(null);
                    setEditingGenerationContent("");
                    setStatus("内容已更新，需重新确认。");
                  } catch (requestError) {
                    setStatus(requestError.message);
                  } finally {
                    setIsSubmitting(false);
                  }
                }
              }, "保存并标为待核验")
            )
          : React.createElement(
              React.Fragment,
              null,
              copyContentButton(result.content),
              React.createElement("button", {
                className: "text-button", type: "button", disabled: isSubmitting || !result.snapshot_id,
                onClick: () => { setEditingGenerationResultId(result.result_id); setEditingGenerationContent(result.content); }
              }, "编辑回答")
            ),
        canVerify
          ? React.createElement("button", {
              className: "secondary-button", type: "button", disabled: isSubmitting || !result.snapshot_id,
              onClick: async () => {
                if (!selectedSession || !onReverifyGenerationResult) return;
                setIsSubmitting(true);
                setStatus("");
                try {
                  const verified = await onReverifyGenerationResult(selectedSession.session_id, result.result_id);
                    setStatus(verified.status === "valid" ? "内容已重新确认。" : "当前内容暂时无法确认，关联记录已更新。");
                } catch (requestError) {
                  setStatus(requestError.message);
                } finally {
                  setIsSubmitting(false);
                }
              }
            }, "重新确认")
          : null
      )
    );
  }

  function completenessResultView(result) {
    const coveragePage = coveragePages[result.result_id] || result;
    const statusText = {
      complete: "完整完成",
      "completed-with-confirmed-gaps": "带已确认缺口完成",
      recoverable: "存在可恢复缺口",
      failed: "完整性任务失败",
      "source-changed": "来源已变化",
      planned: "待处理",
      processed: "已处理",
      duplicate: "已合并重复项"
    }[result.status] || "完整性状态未知";
    const counts = coveragePage.coverage_counts || (coveragePage.coverage || []).reduce((total, item) => {
      total[item.status] = (total[item.status] || 0) + 1;
      return total;
    }, {});
    const evidenceKeys = completenessEvidenceKeys(result, coveragePage.coverage || []);
    const summary = directSummary(result.summary, statusText);
    return React.createElement(
      "section",
      { className: "session-retrieval-result completeness-result", key: result.result_id, "aria-label": statusText },
      React.createElement("p", { className: "session-message-role" }, statusText),
      contentWithEvidence(summary, evidenceKeys),
      React.createElement("div", { className: "progress-sequence", "aria-label": "覆盖进度" },
        React.createElement("span", null, `计划 ${counts.planned || 0} 项`),
        React.createElement("span", null, `已处理 ${counts.processed || 0} 项`),
        React.createElement("span", null, `重复合并 ${counts.duplicate || 0} 项`),
        React.createElement("span", null, `失败 ${counts.failed || 0} 项`),
        React.createElement("span", null, `排除 ${counts.excluded || 0} 项`),
        React.createElement("span", null, `未覆盖 ${counts.uncovered || 0} 项`)),
      result.invalidation_reason
        ? React.createElement("p", { className: "form-error" }, `来源已变化：${result.invalidation_reason}`)
        : null,
      result.recovery_action
        ? React.createElement("p", { className: "form-error" }, `下一步：${result.recovery_action}`)
        : null,
      copyContentButton(summary, "复制结论"),
      coveragePage.coverage_has_more && onLoadCompletenessCoverage
        ? React.createElement("button", {
            className: "secondary-button",
            type: "button",
            disabled: isSubmitting,
            onClick: async () => {
              if (!selectedSession) return;
              setIsSubmitting(true);
              try {
                const loaded = coveragePages[result.result_id] || result;
                const offset = (loaded.coverage_offset || 0) + (loaded.coverage || []).length;
                const nextPage = await onLoadCompletenessCoverage(
                  selectedSession.session_id, result.result_id, offset
                );
                setCoveragePages((current) => {
                  const currentPage = current[result.result_id] || result;
                  return {
                    ...current,
                    [result.result_id]: {
                      ...nextPage,
                      coverage_offset: currentPage.coverage_offset || 0,
                      coverage: [...(currentPage.coverage || []), ...(nextPage.coverage || [])]
                    }
                  };
                });
              } catch (requestError) {
                setStatus(requestError.message);
              } finally {
                setIsSubmitting(false);
              }
            }
          }, "加载更多")
        : null
    );
  }

  function knowledgeOrganizationSectionStatusText(status) {
    return {
      completed: "已完成",
      running: "正在生成",
      preparing: "正在准备",
      prepared: "已准备",
      failed: "失败",
      recoverable: "待恢复",
      planned: "已计划"
    }[status] || "已计划";
  }

  function knowledgeOrganizationResultView(result) {
    const statusText = {
      preparing: "正在生成整理",
      planned: "计划已准备",
      completed: "整理已完成",
      failed: "整理失败",
      recoverable: "计划待恢复",
      "source-changed": "来源已变化"
    }[result.status] || "计划状态未知";
    return React.createElement(
      "section",
      { className: "session-retrieval-result knowledge-organization-result", key: result.result_id, "aria-label": statusText },
      React.createElement("p", { className: "session-message-role" }, statusText),
      result.status === "completed" ? null : React.createElement(
        "p", { className: "session-retrieval-summary" }, directSummary(result.summary, statusText)
      ),
      React.createElement(
        "div",
        { className: "progress-sequence", "aria-label": "知识整理计划进度" },
        React.createElement("span", null, `计划 ${result.section_counts?.planned || 0} 段；已准备 ${result.section_counts?.prepared || 0} 段；已完成 ${result.section_counts?.completed || 0} 段；进行中 ${result.section_counts?.running || 0} 段`),
        React.createElement("span", null, `失败 ${result.section_counts?.failed || 0} 段；待恢复 ${result.section_counts?.recoverable || 0} 段`)
      ),
      result.invalidation_reason
        ? React.createElement("p", { className: "form-error" }, `需重新准备：${result.invalidation_reason}`)
        : null,
      result.recovery_action
        ? React.createElement("p", { className: "form-error" }, `下一步：${result.recovery_action}`)
        : null,
      (result.sections || []).map((section) => React.createElement(
        "section",
        { className: "organization-result-section", key: `organization-result:${result.result_id}:${section.ordinal}` },
        React.createElement("p", { className: "organization-plan-heading" }, `第 ${section.ordinal} 段：${knowledgeOrganizationSectionStatusText(section.status)}`),
        section.reason
          ? React.createElement("p", { className: "form-error" }, `原因：${section.reason}`)
          : null,
        (section.conclusions || []).length
          ? (section.conclusions || []).map((conclusion) => React.createElement(
              "div",
              { className: "organization-conclusion", key: `organization-conclusion:${section.ordinal}:${conclusion.ordinal}` },
              contentWithEvidence(
                conclusion.content,
                organizationConclusionEvidenceKeys(result, section, conclusion)
              ),
              copyContentButton(conclusion.content)
            ))
          : section.status === "completed"
            ? React.createElement("p", { className: "organization-plan-meta" }, "该段暂无可显示结论。")
            : null
      ))
    );
  }

  function deepCreationSectionStatusText(status) {
    return {
      completed: "已完成",
      preparing: "正在生成",
      running: "正在生成",
      failed: "失败",
      recoverable: "待恢复",
      planned: "已计划"
    }[status] || "已计划";
  }

  function deepCreationResultView(result) {
    const statusText = {
      preparing: "正在准备深度创作",
      completed: "深度创作已完成",
      failed: "深度创作失败",
      recoverable: "深度创作待恢复",
      "source-changed": "来源已变化"
    }[result.status] || "深度创作状态未知";
    const statusIcon = result.status === "completed" ? "✓"
      : result.status === "preparing" ? "?"
        : "!";
    return React.createElement(
      "section",
      { className: "session-retrieval-result deep-creation-result", key: result.result_id, "aria-label": statusText },
      React.createElement("p", { className: "status-marker deep-creation-status status-" + result.status }, statusIcon + " " + statusText),
      result.status === "completed" ? null : React.createElement(
        "p", { className: "session-retrieval-summary" }, directSummary(result.summary, statusText)
      ),
      result.invalidation_reason
        ? React.createElement("p", { className: "form-error" }, "需重新准备：" + result.invalidation_reason)
        : null,
      result.recovery_action
        ? React.createElement("p", { className: "form-error" }, "下一步：" + result.recovery_action)
        : null,
      (result.sections || []).map((section) => React.createElement(
        "section",
        { className: "organization-result-section deep-creation-result-section", key: "deep-result:" + result.result_id + ":" + section.ordinal },
        React.createElement("p", { className: "organization-plan-heading" }, "第 " + section.ordinal + " 段：" + deepCreationSectionStatusText(section.status)),
        section.reason
          ? React.createElement("p", { className: "form-error" }, "原因：" + section.reason)
          : null,
        ["completed", "running"].includes(section.status)
          ? React.createElement(
              React.Fragment,
              null,
              contentWithEvidence(section.content, deepCreationEvidenceKeys(result, section)),
              copyContentButton(section.content)
            )
          : null,
      ))
    );
  }

  async function sendTask(event) {
    event?.preventDefault();
    if (!canSend || !selectedSession || !onRun) return;
    setStatus("");
    setIsSubmitting(true);
    setIsStreaming(true);
    setStreamingContent("");
    streamingOrdinalRef.current = null;
    try {
      const execution = await onRun(selectedSession.session_id, {
        ...context,
        scope_path: context.scope_kind === "directory" ? context.scope_path : null,
        content: message,
        intent: taskIntent
      }, (chunk, ordinal) => {
        setStreamingContent((current) => {
          const separator = current && streamingOrdinalRef.current !== ordinal ? "\n\n" : "";
          streamingOrdinalRef.current = ordinal;
          return current + separator + chunk;
        });
      });
      if (execution?.isCurrent === false) return;
      const result = execution?.result || execution;
      setMessage("");
      setStatus(["completed", "complete"].includes(result.status) ? "已完成。" : result.summary);
    } catch (requestError) {
      setStatus(requestError.message);
    } finally {
      setIsStreaming(false);
      setStreamingContent("");
      streamingOrdinalRef.current = null;
      setIsSubmitting(false);
    }
  }

  function directSummary(summary, fallback) {
    const normalized = nonEmptyText(summary);
    return /知识库|检索|证据|引用|文件|位置|编号/.test(normalized) ? fallback : normalized || fallback;
  }

  function retrievalResultHasGeneratedAnswer(result) {
    return generationResults.some((item) => (
      item.result_id && result.task_id && item.task_id === result.task_id
    ) || (
      item.snapshot_id && result.snapshot_id && item.snapshot_id === result.snapshot_id
    ));
  }

  function evidenceRecordKey(kind, vaultId, evidence, suffix = "") {
    return [
      kind,
      vaultId || "",
      evidence.identity_kind || "",
      evidence.source_id || "",
      evidence.source_content_hash || "",
      evidence.content_sha256 || "",
      evidence.source_path || "",
      evidence.relative_path || "",
      evidence.location || "",
      evidence.excerpt || "",
      suffix
    ].join("|");
  }

  function resultVaultId(result) {
    return result.vault_id || snapshotsById.get(result.snapshot_id)?.vault_id || null;
  }

  function matchingRetrievalEvidence(citation) {
    const candidates = retrievalResults
      .filter((result) => !citation.snapshot_id || result.snapshot_id === citation.snapshot_id)
      .flatMap((result) => result.evidences || []);
    return candidates.find((evidence) => citationMatchesEvidence(citation, evidence)) || null;
  }

  function citationMatchesEvidence(citation, evidence) {
    return citation.relative_path === evidence.relative_path
      && (!citation.location || !evidence.location || evidence.location === citation.location)
      && (!citation.content_sha256 || !evidence.content_sha256 || evidence.content_sha256 === citation.content_sha256);
  }

  function citationBelongsToRetrievalResult(citation, result) {
    if (citation.snapshot_id && result.snapshot_id) return citation.snapshot_id === result.snapshot_id;
    const generationResult = generationResults.find((item) => item.result_id === citation.result_id);
    return Boolean(generationResult && (
      generationResult.task_id && result.task_id && generationResult.task_id === result.task_id
    ));
  }

  function citationEvidenceItem(citation) {
    const vault = citation.vault_id ? vaultsById.get(citation.vault_id) : null;
    const evidence = matchingRetrievalEvidence(citation);
    return {
      key: `citation:${citation.citation_id}`,
      kind: "applied-evidence",
      label: "回答应用",
      status: citation.status,
      statusText: citationStatusText(citation.status),
      vaultId: citation.vault_id,
      vaultLabel: vault?.display_name || "历史知识库不可用",
      relativePath: citation.relative_path,
      location: citation.location,
      identityKind: citation.identity_kind,
      contentSha256: citation.content_sha256,
      sourceId: citation.source_id,
      sourceContentHash: citation.source_content_hash,
      sourcePath: citation.source_path,
      heading: evidence?.heading,
      page: evidence?.page,
      excerpt: evidence?.excerpt,
      matchedChannels: evidence?.matched_channels,
      invalidationReason: citation.invalidation_reason,
      canOpenInObsidian: vault?.authorization_status === "active" && vault?.access_status === "available"
    };
  }

  function organizationEvidenceKey(vaultId, evidence, association) {
    return evidenceRecordKey("organization", vaultId, evidence, association);
  }

  function deepCreationEvidenceKey(vaultId, evidence, association) {
    return evidenceRecordKey("deep-creation", vaultId, evidence, association);
  }

  function completenessEvidenceKey(result, evidence) {
    return evidenceRecordKey("completeness", resultVaultId(result), evidence, `${result.result_id}:${evidence.ordinal}`);
  }

  function retrievalEvidenceKey(result, evidence) {
    return evidenceRecordKey("source-lookup", resultVaultId(result), evidence, `${result.result_id}:${evidence.ordinal}`);
  }

  function coverageStatusText(status) {
    return {
      planned: "待处理",
      processed: "已处理",
      duplicate: "已合并重复项",
      failed: "处理失败",
      excluded: "已排除",
      uncovered: "未覆盖"
    }[status] || "已记录";
  }

  function addAppliedEvidence(itemsByKey, key, evidence, context) {
    if (!evidence?.relative_path) return;
    const vault = context.vaultId ? vaultsById.get(context.vaultId) : null;
    const existing = itemsByKey.get(key);
    const usage = existing?.usage ? [...existing.usage] : [];
    if (!usage.includes(context.usage)) usage.push(context.usage);
    itemsByKey.set(key, {
      ...(existing || {}),
      key,
      kind: "applied-evidence",
      label: context.label,
      status: context.status || "valid",
      statusText: context.statusText || usage.join("；") || "有效",
      usage,
      vaultId: context.vaultId,
      vaultLabel: vault?.display_name || "历史知识库不可用",
      relativePath: evidence.relative_path,
      location: evidence.location,
      heading: evidence.heading,
      page: evidence.page,
      excerpt: evidence.excerpt,
      identityKind: evidence.identity_kind,
      contentSha256: evidence.content_sha256,
      sourceId: evidence.source_id,
      sourceContentHash: evidence.source_content_hash,
      sourcePath: evidence.source_path,
      sectionLabel: context.sectionLabel,
      resultLabel: existing?.resultLabel || context.resultLabel,
      reason: evidence.reason,
      duplicateOrdinal: evidence.evidence_ordinal,
      matchedChannels: evidence.matched_channels,
      canOpenInObsidian: vault?.authorization_status === "active" && vault?.access_status === "available"
    });
  }

  function organizationConclusionEvidenceKeys(result, section, conclusion) {
    const association = `result:${result.result_id}:section:${section.ordinal}:conclusion:${conclusion.ordinal}`;
    return (conclusion.evidence || []).map((evidence) => (
      organizationEvidenceKey(resultVaultId(result), evidence, association)
    ));
  }

  function retrievalEvidenceItems() {
    const itemsByKey = new Map();
    const citations = activeDetail?.citations || [];
    retrievalResults.forEach((result) => {
      const snapshot = snapshotsById.get(result.snapshot_id);
      const isStale = result.is_stale || result.snapshot_status === "invalidated" || snapshot?.status === "invalidated";
      (result.evidences || []).forEach((evidence) => {
        const isAlreadyCited = citations.some((citation) => (
          citationBelongsToRetrievalResult(citation, result)
          && citationMatchesEvidence(citation, evidence)
        ));
        if (isAlreadyCited) return;
        addAppliedEvidence(itemsByKey, retrievalEvidenceKey(result, evidence), evidence, {
          vaultId: resultVaultId(result),
          usage: "定位结果",
          label: "定位应用",
          status: isStale ? "stale" : "valid",
          statusText: isStale ? "已失效" : "有效",
          sectionLabel: isStale ? "当前内容需重新确认" : "定位结果",
          resultLabel: `结果 ${result.result_id}`
        });
      });
    });
    return [...itemsByKey.values()].sort((first, second) =>
      `${first.relativePath || ""}:${first.location || ""}`.localeCompare(`${second.relativePath || ""}:${second.location || ""}`)
    );
  }

  function organizationEvidenceItems() {
    const itemsByKey = new Map();
    (activeDetail?.task_snapshots || [])
      .filter((snapshot) => snapshot.intent === "knowledge-organization")
      .forEach((snapshot) => {
        (snapshot.knowledge_organization_plan?.sections || []).forEach((section) => {
          (section.evidence || []).forEach((evidence) => addAppliedEvidence(
            itemsByKey,
            organizationEvidenceKey(snapshot.vault_id, evidence, `snapshot:${snapshot.snapshot_id}:section:${section.ordinal}`),
            evidence,
            {
            vaultId: snapshot.vault_id,
            usage: "计划段",
            label: "整理应用",
            sectionLabel: `第 ${section.ordinal} 段计划`,
            resultLabel: `快照 ${snapshot.snapshot_id}`
            }
          ));
        });
      });
    (knowledgeOrganizationResults || []).forEach((result) => {
      (result.sections || []).forEach((section) => {
        (section.evidence || []).forEach((evidence) => addAppliedEvidence(
          itemsByKey,
          organizationEvidenceKey(resultVaultId(result), evidence, `result:${result.result_id}:section:${section.ordinal}`),
          evidence,
          {
            vaultId: resultVaultId(result),
            usage: "整理段",
            label: "整理应用",
            sectionLabel: `第 ${section.ordinal} 段`,
            resultLabel: `结果 ${result.result_id}`
          }
        ));
        (section.conclusions || []).forEach((conclusion) => {
          (conclusion.evidence || []).forEach((evidence) => addAppliedEvidence(
            itemsByKey,
            organizationEvidenceKey(
              resultVaultId(result),
              evidence,
              `result:${result.result_id}:section:${section.ordinal}:conclusion:${conclusion.ordinal}`
            ),
            evidence,
            {
              vaultId: resultVaultId(result),
              usage: "整理结论",
              label: "整理应用",
              sectionLabel: `第 ${section.ordinal} 段结论 ${conclusion.ordinal}`,
              resultLabel: `结果 ${result.result_id}`
            }
          ));
        });
      });
    });
    return [...itemsByKey.values()].sort((first, second) =>
      `${first.relativePath || ""}:${first.location || ""}`.localeCompare(`${second.relativePath || ""}:${second.location || ""}`)
    );
  }

  function deepCreationEvidenceForSection(result, section) {
    if ((section.local_evidence || []).length) return section.local_evidence;
    const snapshot = snapshotsById.get(result.snapshot_id);
    return (snapshot?.deep_creation_plan?.sections || []).find(
      (item) => item.ordinal === section.ordinal
    )?.local_evidence || [];
  }

  function deepCreationEvidenceKeys(result, section) {
    const vaultId = resultVaultId(result);
    const association = `result:${result.result_id}:section:${section.ordinal}`;
    return deepCreationEvidenceForSection(result, section).map(
      (evidence) => deepCreationEvidenceKey(vaultId, evidence, association)
    );
  }

  function deepCreationEvidenceItems() {
    const itemsByKey = new Map();
    (activeDetail?.task_snapshots || [])
      .filter((snapshot) => snapshot.intent === "deep-creation")
      .forEach((snapshot) => {
        (snapshot.deep_creation_plan?.sections || []).forEach((section) => {
          (section.local_evidence || []).forEach((evidence) => addAppliedEvidence(
            itemsByKey,
            deepCreationEvidenceKey(snapshot.vault_id, evidence, `snapshot:${snapshot.snapshot_id}:section:${section.ordinal}`),
            evidence,
            {
              vaultId: snapshot.vault_id,
              usage: "创作计划",
              label: "创作应用",
              sectionLabel: `第 ${section.ordinal} 段计划`,
              resultLabel: `快照 ${snapshot.snapshot_id}`
            }
          ));
        });
      });
    deepCreationResults.forEach((result) => {
      (result.sections || []).forEach((section) => {
        deepCreationEvidenceForSection(result, section).forEach((evidence) => addAppliedEvidence(
          itemsByKey,
          deepCreationEvidenceKey(
            resultVaultId(result),
            evidence,
            `result:${result.result_id}:section:${section.ordinal}`
          ),
          evidence,
          {
            vaultId: resultVaultId(result),
            usage: "创作内容",
            label: "创作应用",
            sectionLabel: `第 ${section.ordinal} 段`,
            resultLabel: `结果 ${result.result_id}`
          }
        ));
      });
    });
    return [...itemsByKey.values()].sort((first, second) =>
      `${first.relativePath || ""}:${first.location || ""}`.localeCompare(`${second.relativePath || ""}:${second.location || ""}`)
    );
  }

  function completenessEvidenceKeys(result, coverage) {
    return (coverage || [])
      .filter((item) => item.relative_path)
      .map((item) => completenessEvidenceKey(result, item));
  }

  function completenessEvidenceItems() {
    const itemsByKey = new Map();
    completenessResults.forEach((result) => {
      const coverage = coveragePages[result.result_id]?.coverage || result.coverage || [];
      coverage.forEach((item) => addAppliedEvidence(
        itemsByKey, completenessEvidenceKey(result, item), item, {
          vaultId: resultVaultId(result),
          usage: "覆盖检查",
          label: "覆盖应用",
          status: item.status || "valid",
          statusText: coverageStatusText(item.status),
          sectionLabel: `覆盖项 ${item.ordinal}`,
          resultLabel: `结果 ${result.result_id}`
        }
      ));
    });
    return [...itemsByKey.values()].sort((first, second) =>
      `${first.relativePath || ""}:${first.location || ""}`.localeCompare(`${second.relativePath || ""}:${second.location || ""}`)
    );
  }

  function evidencePanelItemView(item, anchorSuffix = "") {
    const statusClass = `session-citation-status status-${item.status || "valid"}`;
    const summary = evidenceSummaryText(item);
    const locationText = userFacingEvidenceLocation(item);
    const sourceText = userFacingEvidenceSource(item);
    return React.createElement(
      "article",
      {
        className: "session-citation",
        id: `${applicationEvidenceAnchorId(item.key)}${anchorSuffix}`,
        key: item.key,
        tabIndex: -1
      },
      React.createElement("p", { className: "session-citation-label" }, item.label),
      React.createElement("p", { className: "session-citation-path" }, userFacingFileName(item.relativePath) || "未标注来源"),
      React.createElement(
        "details",
        { className: "evidence-row citation-evidence-row" },
        React.createElement("summary", null, `${item.statusText} · ${summary}`),
        item.sectionLabel ? React.createElement("p", null, item.sectionLabel) : null,
        item.excerpt ? React.createElement("p", { className: "evidence-excerpt" }, item.excerpt) : null,
        locationText ? React.createElement("p", { className: "evidence-location" }, `位置：${locationText}`) : null,
        sourceText ? React.createElement("p", { className: "evidence-source" }, sourceText) : null,
        item.matchedChannels?.length
          ? React.createElement("p", { className: "evidence-source" }, `匹配方式：${item.matchedChannels.map(retrievalChannelText).join("、")}`)
          : null,
        item.reason ? React.createElement("p", { className: "form-error" }, item.reason) : null,
        item.duplicateOrdinal ? React.createElement("p", null, `与覆盖项 ${item.duplicateOrdinal} 合并。`) : null,
        item.invalidationReason ? React.createElement("p", { className: "form-error" }, item.invalidationReason) : null,
        item.canOpenInObsidian && item.vaultId
          ? React.createElement("a", {
              href: `${VAULTS_ENDPOINT}/${encodeURIComponent(item.vaultId)}/open?file=${encodeURIComponent(item.relativePath)}`,
              target: "_blank", rel: "noreferrer"
            }, "在 Obsidian 中打开")
          : null
      ),
      React.createElement("span", { className: statusClass }, item.statusText)
    );
  }

  if (isLoading && !sessions.length) {
    return React.createElement("p", { className: "empty-state", role: "status" }, "正在加载会话。");
  }

  const evidencePanelItems = [
    ...(activeDetail?.citations || []).map(citationEvidenceItem),
    ...retrievalEvidenceItems(),
    ...organizationEvidenceItems(),
    ...deepCreationEvidenceItems(),
    ...completenessEvidenceItems()
  ].sort((first, second) => `${first.relativePath || ""}:${first.location || ""}`.localeCompare(
    `${second.relativePath || ""}:${second.location || ""}`
  ));
  evidencePanelItems.forEach((item) => applicationEvidenceItemsByKey.set(item.key, item));

  return React.createElement(
    "section",
    { className: `session-management session-mobile-view-${mobileSessionView}`, "aria-label": "会话工作区" },
    React.createElement(
      "aside",
      { className: "session-history-pane", "aria-label": "会话历史" },
      React.createElement(
        "div",
        { className: "session-history-heading" },
        React.createElement("h2", null, "会话"),
        React.createElement("button", { className: "primary-button", type: "button", disabled: isSubmitting, onClick: createSession }, "新建会话")
      ),
      React.createElement(
        "form",
        {
          className: "session-search",
          onSubmit: (event) => {
            event.preventDefault();
            load({ ...filters, query: query.trim(), page: 1 });
          }
        },
        React.createElement("input", {
          value: query,
          onChange: (event) => setQuery(event.target.value),
          "aria-label": "搜索会话",
          placeholder: "搜索会话"
        }),
        React.createElement(IconButton, { icon: Search, label: "搜索会话", className: "icon-button session-search-button", type: "submit", disabled: isSubmitting })
      ),
      React.createElement(
        "label",
        { className: "session-sort" },
        React.createElement("span", null, "排序"),
        React.createElement(
          "select",
          {
            value: filters.sort,
            "aria-label": "会话排序",
            onChange: (event) => load({ ...filters, sort: event.target.value, page: 1 })
          },
          React.createElement("option", { value: "updated_at" }, "最近更新"),
          React.createElement("option", { value: "title" }, "标题"),
          React.createElement("option", { value: "vault" }, "所用 vault")
        )
      ),
      error || status
        ? React.createElement(
            "p",
            { className: error ? "form-error" : "status-line", role: error ? "alert" : "status" },
            error || status
          )
        : null,
      sessions.length
        ? React.createElement(
            "div",
            { className: "session-list", role: "list" },
            sessions.map((session) => React.createElement(
              "button",
              {
                className: "session-history-item",
                type: "button",
                key: session.session_id,
                "aria-pressed": selectedSession?.session_id === session.session_id,
                onClick: () => {
                  onSelect(session);
                  setMobileSessionView("conversation");
                }
              },
              React.createElement("strong", null, session.title),
              React.createElement("span", null, `所用 vault：${sessionVaultName(session, vaults)}`),
              React.createElement("span", null, `消息 ${session.message_count || 0} 条`)
            ))
          )
        : React.createElement("p", { className: "empty-state" }, "当前没有已保存的会话。"),
      React.createElement(
        "div",
        { className: "session-pagination", "aria-label": "会话分页" },
        React.createElement(
          "button",
          { className: "secondary-button", type: "button", disabled: isSubmitting || page <= 1, onClick: () => load({ ...filters, page: page - 1 }) },
          "上一页"
        ),
        React.createElement("span", { role: "status" }, `第 ${page} / ${totalPages} 页`),
        React.createElement(
          "button",
          { className: "secondary-button", type: "button", disabled: isSubmitting || page >= totalPages, onClick: () => load({ ...filters, page: page + 1 }) },
          "下一页"
        )
      )
    ),
    React.createElement(
      "section",
      { className: "session-conversation-pane", "aria-label": "会话内容" },
      React.createElement(
        "header",
        { className: "session-detail-heading" },
        React.createElement(IconButton, {
          icon: ChevronLeft,
          label: "返回会话列表",
          className: "icon-button session-mobile-back-button",
          type: "button",
          onClick: () => setMobileSessionView("history")
        }),
        editingSessionId === selectedSession?.session_id
          ? React.createElement(
              "form",
              { className: "session-rename", onSubmit: (event) => saveRename(event, selectedSession.session_id) },
              React.createElement("input", {
                ref: renameInputRef,
                value: editingTitle,
                onChange: (event) => setEditingTitle(event.target.value),
                "aria-label": `${selectedSession.title} 的会话标题`
              }),
              React.createElement("button", { className: "secondary-button", type: "button", disabled: isSubmitting, onClick: () => setEditingSessionId(null) }, "取消"),
              React.createElement("button", { className: "primary-button", type: "submit", disabled: isSubmitting }, "保存")
            )
          : React.createElement(
              React.Fragment,
              null,
              React.createElement(
                "div",
                { className: "session-detail-title" },
                React.createElement("p", { className: "section-label" }, "当前会话"),
                React.createElement("h2", null, selectedSession?.title || "选择一个会话")
              ),
              selectedSession
                ? React.createElement(
                    "div",
                    { className: "session-detail-actions" },
                    React.createElement(IconButton, {
                      icon: FileText,
                      label: "查看应用证据",
                      className: "icon-button session-mobile-evidence-button",
                      type: "button",
                      disabled: !evidencePanelItems.length,
                      onClick: () => {
                        setActiveEvidenceKey(null);
                        setEvidenceSheetOpen(true);
                      }
                    }),
                    React.createElement("button", { className: "text-button", type: "button", onClick: () => openRename(selectedSession) }, "重命名"),
                    React.createElement("button", { className: "text-button", type: "button", onClick: () => exportSession(selectedSession) }, "导出"),
                    React.createElement("button", { className: "text-button danger-text-button", type: "button", onClick: (event) => onDelete(selectedSession, event.currentTarget) }, "删除")
                  )
                : null
            )
      ),
      React.createElement(
        "div",
        { className: "session-conversation-body" },
        React.createElement(
          "div",
          { className: "session-message-list", ref: messageListRef, "aria-live": "polite", onScroll: updateConversationScroll },
          isDetailLoading && activeDetail
            ? React.createElement("p", { className: "session-detail-refresh-status", role: "status" }, "正在更新会话内容。")
            : null,
          detailError && activeDetail
            ? React.createElement("p", { className: "form-error", role: "alert" }, detailError)
            : null,
          conversationTurnItems.length
            ? conversationTurnItems.map((turn) => React.createElement(
                "section",
                { className: "session-turn", id: turn.id, key: turn.id, tabIndex: -1 },
                turn.entries.map(conversationEntryView)
              ))
            : isDetailLoading
              ? React.createElement("p", { className: "empty-state", role: "status" }, "正在加载会话内容。")
              : detailError
                ? React.createElement("p", { className: "form-error", role: "alert" }, detailError)
                : selectedSession
                  ? React.createElement("p", { className: "empty-state" }, "该会话尚无已保存的消息。")
                  : React.createElement("p", { className: "empty-state" }, "从左侧选择一个会话以查看内容。"),
          isStreaming
            ? React.createElement(
                "article",
                { className: "session-message session-message-streaming", "aria-live": "polite" },
                React.createElement("p", { className: "session-message-role" }, "助手 · 正在生成"),
                React.createElement(
                  "p",
                  { className: "session-message-content" },
                  streamingContent || "正在生成回答……"
                )
              )
            : null
        ),
        conversationTurnItems.length
          ? React.createElement(
              "nav",
              { className: "session-turn-navigator", "aria-label": "问答定位" },
              conversationTurnItems.map((turn, index) => React.createElement(
                "button",
                {
                  className: "session-turn-navigator-button",
                  type: "button",
                  key: turn.id,
                  "aria-label": `定位到第 ${index + 1} 轮：${turn.question || "未命名问答"}`,
                  "aria-current": activeConversationTurnId === turn.id ? "true" : undefined,
                  title: turn.question || "未命名问答",
                  onClick: () => scrollToConversationTurn(turn)
                }
              ))
            )
          : null
      ),
      selectedSession
        ? React.createElement(
            "form",
            { className: "session-composer", "aria-label": "会话输入", onSubmit: sendTask },
            React.createElement(
              "div",
              { className: "session-attachment-list", "aria-live": "polite", "aria-relevant": "additions removals text" },
              activeDetail?.attachments?.map((attachment) => React.createElement(
                "div",
                { className: "attachment-row", key: attachment.attachment_id },
                React.createElement("span", null,
                  React.createElement("strong", null, attachment.filename),
                  ` · ${attachmentStatusText(attachment.status)}`
                ),
                React.createElement("button", {
                  className: "text-button", type: "button", disabled: isSubmitting,
                  "aria-label": `移除附件 ${attachment.filename}`,
                  onClick: () => removeAttachment(attachment.attachment_id)
                }, "移除")
              ))
            ),
            React.createElement("textarea", {
              value: message,
              disabled: isSubmitting,
              "aria-label": "输入问题或继续创作",
              placeholder: "输入问题，或继续创作...",
              onChange: (event) => setMessage(event.target.value),
              onKeyDown: (event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  sendTask();
                }
              }
            }),
            React.createElement(
              "div", { className: "session-composer-controls" },
              React.createElement(
                "div",
                { className: "session-composer-primary-controls" },
                React.createElement(
                  "button",
                  {
                    className: "secondary-button composer-attachment-button",
                    type: "button",
                    disabled: isSubmitting || contextIsDirty || !selectedSession?.selected_vault_id,
                    onClick: pickAttachments
                  },
                  React.createElement(Paperclip, { size: 16, "aria-hidden": "true" }),
                  "添加附件"
                ),
              React.createElement(
                "fieldset",
                { className: "retrieval-mode-control", disabled: isSubmitting || retrievalModeLoading || !selectedSession },
                React.createElement("legend", null, "查找模式"),
                React.createElement(
                  "div",
                  { className: "retrieval-mode-options", role: "group", "aria-label": "检索模式" },
                  (retrievalMode.options?.length
                    ? retrievalMode.options
                    : [
                        { mode: "keyword", label: "仅关键词" },
                        { mode: "semantic", label: "仅语义" },
                        { mode: "hybrid", label: "关键词与语义混合" }
                      ]
                  ).map((option) => React.createElement(
                    "button",
                    {
                      className: `retrieval-mode-button${retrievalMode.mode === option.mode ? " is-active" : ""}`,
                      type: "button",
                      key: option.mode,
                      "aria-pressed": retrievalMode.mode === option.mode ? "true" : "false",
                      onClick: () => changeRetrievalMode(option.mode)
                    },
                    option.label
                  ))
                ),
                retrievalModeError
                  ? React.createElement("p", { className: "retrieval-mode-error", role: "alert" }, retrievalModeError)
                  : null
              )
              ),
              React.createElement(
                "details",
                { className: "session-context-settings" },
                React.createElement(
                  "summary",
                  null,
                  React.createElement(SlidersHorizontal, { size: 16, "aria-hidden": "true" }),
                  React.createElement("span", null, "上下文")
                ),
                React.createElement(
                  "div",
                  { className: "session-context-settings-panel" },
                  React.createElement("label", null, "资料库",
                    React.createElement("select", {
                      value: context.vault_id, disabled: isSubmitting, "aria-label": "选择 vault",
                      onChange: (event) => {
                        setContext({ vault_id: event.target.value, scope_kind: "vault", scope_path: "", provider_id: "", model_id: "" });
                      }
                    }, React.createElement("option", { value: "" }, "选择 vault"), availableVaults.map((vault) => React.createElement("option", { key: vault.vault_id, value: vault.vault_id }, vaultName(vault))))
                  ),
                  React.createElement("label", null, "资料范围",
                    React.createElement("select", {
                      value: context.scope_kind, disabled: isSubmitting || !context.vault_id, "aria-label": "选择资料范围",
                      onChange: (event) => {
                        setContext({ ...context, scope_kind: event.target.value, scope_path: "" });
                      }
                    }, React.createElement("option", { value: "vault" }, "整个 vault"), React.createElement("option", { value: "directory" }, "指定目录"))
                  ),
                  context.scope_kind === "directory"
                    ? React.createElement("label", { className: "session-context-path" }, "目录",
                      React.createElement("input", { value: context.scope_path, disabled: isSubmitting, "aria-label": "资料范围目录", placeholder: "vault 相对目录", onChange: (event) => {
                        setContext({ ...context, scope_path: event.target.value });
                      } })
                    )
                    : null,
                  React.createElement("label", null, "Model",
                    React.createElement("select", {
                      value: JSON.stringify([context.provider_id, context.model_id]), disabled: isSubmitting || !context.vault_id, "aria-label": "选择 Model",
                      onChange: (event) => {
                        const [providerId, modelId] = JSON.parse(event.target.value);
                        setContext({ ...context, provider_id: providerId || "", model_id: modelId || "" });
                      }
                    }, React.createElement("option", { value: JSON.stringify(["", ""]) }, "选择已验证的 chat Model"), chatModels.map(({ provider, model }) => React.createElement("option", { key: `${provider.provider_id}:${model.model_id}`, value: JSON.stringify([provider.provider_id, model.model_id]) }, `${provider.name} · ${model.model_id}`)))
                  ),
                  React.createElement("label", null, "任务类型",
                    React.createElement("select", {
                      value: taskIntent, disabled: isSubmitting, "aria-label": "选择任务类型",
                      onChange: (event) => setTaskIntent(event.target.value)
                    },
                    React.createElement("option", { value: "auto" }, "自动识别"),
                    React.createElement("option", { value: "source-lookup" }, "原文定位"),
                    React.createElement("option", { value: "completeness" }, "完整列举"),
                    React.createElement("option", { value: "knowledge-organization" }, "知识整理"),
                    React.createElement("option", { value: "deep-creation" }, "深度创作"))
                  )
                )
              ),
              React.createElement("button", { className: "primary-button", type: "submit", disabled: isSubmitting || !canSend }, "发送")
            )
          )
        : null
    ),
    React.createElement(
      "aside",
      { className: "session-evidence-pane", "aria-label": "应用证据" },
      React.createElement(
        "header",
        { className: "session-evidence-heading" },
        React.createElement("h2", { id: "application-evidence-heading" }, "应用证据"),
        React.createElement("span", null, evidencePanelItems.length)
      ),
      React.createElement(
        "div",
        { className: "session-citation-list" },
        isDetailLoading && !activeDetail
          ? React.createElement("p", { className: "empty-state", role: "status" }, "正在加载应用证据。")
          : evidencePanelItems.length
            ? evidencePanelItems.map((item) => evidencePanelItemView(item))
            : React.createElement("p", { className: "empty-state" }, selectedSession ? "当前会话暂无应用证据。" : "选择会话后将在此显示应用证据。")
      )
    ),
    isEvidenceSheetOpen
      ? React.createElement(
          "div",
          {
            className: "session-evidence-sheet-backdrop",
            onMouseDown: (event) => {
              if (event.target === event.currentTarget) setEvidenceSheetOpen(false);
            }
          },
          React.createElement(
            "section",
            {
              className: "session-evidence-sheet",
              role: "dialog",
              "aria-modal": "true",
              "aria-labelledby": "application-evidence-sheet-heading",
              onKeyDown: (event) => {
                if (event.key === "Escape") setEvidenceSheetOpen(false);
              }
            },
            React.createElement(
              "header",
              { className: "session-evidence-heading" },
              React.createElement("h2", { id: "application-evidence-sheet-heading" }, "应用证据"),
              React.createElement(
                "div",
                { className: "session-evidence-sheet-actions" },
                React.createElement("span", null, evidencePanelItems.length),
                React.createElement(IconButton, {
                  icon: X,
                  label: "关闭应用证据",
                  className: "icon-button",
                  type: "button",
                  ref: evidenceSheetCloseButtonRef,
                  onClick: () => setEvidenceSheetOpen(false)
                })
              )
            ),
            React.createElement(
              "div",
              { className: "session-citation-list" },
              evidencePanelItems.length
                ? evidencePanelItems.map((item) => evidencePanelItemView(item, "-sheet"))
                : React.createElement("p", { className: "empty-state" }, "当前会话暂无应用证据。")
            )
          )
        )
      : null
  );
}

function PolicyRuleForm({ vault, rule, onComplete, onCancel }) {
  const [kind, setKind] = React.useState(rule?.kind || "completely-ignore");
  const [relativePath, setRelativePath] = React.useState(rule?.relative_path || "");
  const [preview, setPreview] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const isEditing = Boolean(rule);

  async function previewRule() {
    if (!relativePath) return;
    setStatus("");
    try {
      const response = await requestJson(`${policyEndpoint(vault.vault_id)}/preview`, {
        method: "POST",
        body: JSON.stringify({
          source_path: relativePath,
          stage: rulePreviewStage(kind),
          candidate_kind: kind,
          candidate_relative_path: relativePath,
          replacing_rule_id: rule?.rule_id || null
        })
      });
      setPreview(response.preview.reason);
    } catch (error) {
      setStatus(error.message);
    }
  }

  async function submit(event) {
    event.preventDefault();
    setStatus("");
    setIsSubmitting(true);
    try {
      const endpoint = isEditing
        ? `${policyEndpoint(vault.vault_id)}/rules/${rule.rule_id}`
        : `${policyEndpoint(vault.vault_id)}/rules`;
      const response = await requestJson(endpoint, {
        method: isEditing ? "PUT" : "POST",
        body: JSON.stringify({ kind, relative_path: relativePath })
      });
      onComplete(response.rule);
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return React.createElement(
    "form",
    { className: "policy-rule-form", onSubmit: submit, "aria-label": isEditing ? "编辑排除规则" : "新增排除规则" },
    React.createElement(
      "label",
      { className: "form-row", htmlFor: "policy-rule-kind" },
      React.createElement("span", { className: "form-label" }, "规则类型"),
      React.createElement(
        "select",
        {
          id: "policy-rule-kind",
          value: kind,
          disabled: isSubmitting,
          onChange: (event) => setKind(event.target.value)
        },
        React.createElement("option", { value: "completely-ignore" }, "完全忽略"),
        React.createElement("option", { value: "do-not-index" }, "不建立索引"),
        React.createElement("option", { value: "never-send-cloud" }, "绝不发送到云端")
      )
    ),
    React.createElement(
      "label",
      { className: "form-row", htmlFor: "policy-rule-path" },
      React.createElement("span", { className: "form-label" }, "vault 相对路径"),
      React.createElement("input", {
        id: "policy-rule-path",
        value: relativePath,
        required: true,
        disabled: isSubmitting,
        onChange: (event) => setRelativePath(event.target.value)
      }),
      React.createElement("span", { className: "form-help" }, "目录规则覆盖其所有后代；路径不会离开当前 vault。")
    ),
    preview ? React.createElement("p", { className: "status-line", role: "status" }, `预览：${preview}`) : null,
    status ? React.createElement("p", { className: "form-error", role: "alert" }, status) : null,
    React.createElement(
      "div",
      { className: "form-actions" },
      React.createElement("button", { className: "secondary-button", type: "button", disabled: !relativePath || isSubmitting, onClick: previewRule }, "验证预览"),
      React.createElement("button", { className: "secondary-button", type: "button", disabled: isSubmitting, onClick: onCancel }, "取消"),
      React.createElement("button", { className: "primary-button", type: "submit", disabled: !relativePath || isSubmitting }, isEditing ? "保存规则" : "添加规则")
    )
  );
}

function VaultPolicyControls({ vault, onUpdate }) {
  const [ruleForm, setRuleForm] = React.useState(null);
  const [status, setStatus] = React.useState("");
  const [rulePreviews, setRulePreviews] = React.useState({});
  const policy = policyFor(vault);

  function updatePolicy(nextPolicy) {
    onUpdate({ ...vault, policy: nextPolicy });
  }

  async function removeRule(rule) {
    setStatus("");
    try {
      await requestJson(`${policyEndpoint(vault.vault_id)}/rules/${rule.rule_id}`, { method: "DELETE" });
      const response = await requestJson(policyEndpoint(vault.vault_id));
      updatePolicy(response.policy);
      setStatus("规则已删除。");
    } catch (error) {
      setStatus(error.message);
    }
  }

  async function previewRule(rule) {
    setStatus("");
    try {
      const response = await requestJson(`${policyEndpoint(vault.vault_id)}/preview`, {
        method: "POST",
        body: JSON.stringify({
          source_path: rule.relative_path,
          stage: rulePreviewStage(rule.kind)
        })
      });
      setRulePreviews((current) => ({ ...current, [rule.rule_id]: response.preview.reason }));
    } catch (error) {
      setStatus(error.message);
    }
  }

  async function completeRule() {
    const response = await requestJson(policyEndpoint(vault.vault_id));
    updatePolicy(response.policy);
    setRuleForm(null);
    setStatus("规则已保存。");
  }

  return React.createElement(
    "section",
    { className: "policy-controls", "aria-labelledby": "vault-policy-heading" },
    React.createElement("h3", { id: "vault-policy-heading" }, "资料排除规则"),
    React.createElement(
      "div",
      { className: "policy-summary", "aria-live": "polite" },
      `已验证 Provider 默认允许出网；策略修订 ${policy.policy_revision}。never-send-cloud 始终优先。`
    ),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null,
    React.createElement(
      "div",
      { className: "policy-heading-row" },
      React.createElement("p", { className: "section-label" }, "排除规则"),
      !ruleForm ? React.createElement("button", { className: "secondary-button", type: "button", onClick: () => setRuleForm({}) }, "添加规则") : null
    ),
    ruleForm
      ? React.createElement(PolicyRuleForm, {
          vault,
          rule: ruleForm.rule_id ? ruleForm : null,
          onComplete: completeRule,
          onCancel: () => setRuleForm(null)
        })
      : null,
    policy.rules.length === 0 && !ruleForm
      ? React.createElement("p", { className: "empty-state" }, "尚无排除规则。已验证 Provider 默认允许出网。")
      : null,
    policy.rules.map((rule) =>
      React.createElement(
        "div",
        { className: "section-row policy-rule-row", key: rule.rule_id },
        React.createElement("span", { className: "row-title" }, rule.kind),
        React.createElement("span", { className: "row-meta" }, rule.relative_path),
        React.createElement("span", { className: "row-note" }, ruleReason(rule.kind)),
        React.createElement(
          "span",
          { className: "rule-actions" },
          React.createElement("button", { className: "text-button", type: "button", onClick: () => previewRule(rule) }, "预览"),
          React.createElement("button", { className: "text-button", type: "button", onClick: () => setRuleForm(rule) }, "编辑"),
          React.createElement("button", { className: "text-button danger-text-button", type: "button", onClick: () => removeRule(rule) }, "删除")
        ),
        rulePreviews[rule.rule_id]
          ? React.createElement("span", { className: "rule-preview", role: "status" }, `预览：${rulePreviews[rule.rule_id]}`)
          : null
      )
    )
  );
}

export function VaultIndexStatus({ vault, onUpdate }) {
  const [status, setStatus] = React.useState("");
  const [isActing, setIsActing] = React.useState(false);
  const suppliedIndex = vault.index || {};
  const index = {
    status: suppliedIndex.status || vault.index_status || "not-initialized",
    updated_at: suppliedIndex.updated_at || null,
    current_count: suppliedIndex.current_count || 0,
    stale_count: suppliedIndex.stale_count || 0,
    failure_count: suppliedIndex.failure_count || 0,
    semantic_status: suppliedIndex.semantic_status || "unavailable",
    failed_paths: suppliedIndex.failed_paths || [],
    stale_paths: suppliedIndex.stale_paths || [],
    stale_details: suppliedIndex.stale_details || [],
    pending_count: suppliedIndex.pending_count || 0,
    pending_paths: suppliedIndex.pending_paths || []
  };
  const staleDetails = index.stale_details || [];
  const healthText = index.status === "not-initialized" ? "未初始化" : index.status;
  const statusIcon = index.status === "healthy" ? "✓" : index.status === "failed" ? "!" : "?";

  async function runIndexAction(action) {
    setIsActing(true);
    setStatus("");
    try {
      const response = await requestJson(`${VAULTS_ENDPOINT}/${vault.vault_id}/index/${action}`, {
        method: "POST"
      });
      onUpdate(response.vault);
      setStatus(action === "reconcile" ? "已核对 vault 变更。" : action === "retry" ? "已重试失败索引。" : "已重建私有索引。");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  async function resolveAssociation(relativePath, resolution) {
    setIsActing(true);
    setStatus("");
    try {
      const response = await requestJson(`${VAULTS_ENDPOINT}/${vault.vault_id}/index/associations`, {
        method: "POST",
        body: JSON.stringify({ relative_path: relativePath, resolution })
      });
      onUpdate(response.vault);
      setStatus("待关联项已记录审核处置。");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  return React.createElement(
    "section",
    { className: "index-health", "aria-label": "索引健康度" },
    React.createElement("h3", null, "索引健康度"),
    React.createElement("p", { className: `status-marker index-status index-status-${index.status}` }, `${statusIcon} 状态：${healthText}`),
    React.createElement("p", { className: "row-note" }, `已索引 ${index.current_count} 项；失效 ${index.stale_count} 项；待关联 ${index.pending_count || 0} 项；失败 ${index.failure_count} 项。`),
    React.createElement("p", { className: "row-note" }, index.updated_at ? `最近更新：${index.updated_at}` : "尚无成功的索引更新。"),
    React.createElement("p", { className: "row-note" }, index.semantic_status === "unavailable" ? "语义索引尚不可用，未向 Provider 发送内容。" : `语义索引：${index.semantic_status}`),
    index.failed_paths.length
      ? React.createElement("p", { className: "row-note status-danger" }, `失败对象：${index.failed_paths.join("、")}`)
      : null,
    staleDetails.length
      ? React.createElement("p", { className: "row-note" }, `失效证据：${staleDetails.join("、")}`)
      : index.stale_paths.length
        ? React.createElement("p", { className: "row-note" }, `失效证据：${index.stale_paths.join("、")}`)
      : null,
    (index.pending_paths || []).map((path) => React.createElement(
      "div",
      { className: "index-association-row", key: path },
      React.createElement("span", { className: "row-note" }, `待关联：${path}`),
      React.createElement("button", { className: "secondary-button", type: "button", disabled: isActing, onClick: () => resolveAssociation(path, "reassociate") }, "确认重新关联"),
      React.createElement("button", { className: "secondary-button", type: "button", disabled: isActing, onClick: () => resolveAssociation(path, "link-fixed") }, "确认链接已修复"),
      React.createElement("button", { className: "danger-button", type: "button", disabled: isActing, onClick: () => resolveAssociation(path, "confirm-delete") }, "确认删除")
    )),
    React.createElement(
      "div",
      { className: "detail-actions" },
      React.createElement("button", {
        className: "secondary-button",
        type: "button",
        disabled: isActing,
        onClick: () => runIndexAction("reconcile")
      }, "核对变更"),
      index.failure_count
        ? React.createElement("button", {
          className: "secondary-button",
          type: "button",
          disabled: isActing,
          onClick: () => runIndexAction("retry")
        }, "重试索引")
        : null,
      React.createElement("button", {
        className: "secondary-button",
        type: "button",
        disabled: isActing,
        onClick: () => runIndexAction("rebuild")
      }, "重建索引")
    ),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null
  );
}

function workbenchStateText(state) {
  return {
    healthy: "健康",
    running: "处理中",
    attention: "需处理",
    unavailable: "不可用",
    inactive: "已停用"
  }[state] || state;
}

function workbenchLifecycleText(lifecycle) {
  return {
    queued: "排队",
    running: "运行中",
    recoverable: "可恢复",
    failed: "失败",
    cancelled: "已取消",
    complete: "已完成",
    "completed-with-confirmed-gaps": "带缺口完成"
  }[lifecycle] || lifecycle;
}

function workbenchTimeText(value) {
  if (!value) return "暂无记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "暂无记录";
  return date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function WorkbenchMetric({ label, value, note, tone = "neutral", onClick }) {
  const content = React.createElement(
    React.Fragment,
    null,
    React.createElement("span", { className: "workbench-metric-label" }, label),
    React.createElement("strong", { className: "workbench-metric-value" }, value),
    note ? React.createElement("span", { className: "workbench-metric-note" }, note) : null
  );
  return onClick
    ? React.createElement("button", { className: `workbench-metric workbench-metric-${tone}`, type: "button", onClick }, content)
    : React.createElement("div", { className: `workbench-metric workbench-metric-${tone}` }, content);
}

function VaultOverviewDrawer({ vault, onClose, onNavigate, onRefresh }) {
  const [activeTab, setActiveTab] = React.useState("overview");
  const [tabData, setTabData] = React.useState({});
  const [loadingTab, setLoadingTab] = React.useState(false);
  const [tabError, setTabError] = React.useState("");
  const index = vault.index;
  const tabs = [
    ["overview", "概况"],
    ["index", "索引"],
    ["tasks", "资料任务"],
    ["sessions", "会话"],
    ["policy", "策略"]
  ];

  React.useEffect(() => {
    function closeOnEscape(event) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  React.useEffect(() => {
    if (!["sessions", "policy"].includes(activeTab) || tabData[activeTab]) return undefined;
    let active = true;
    setLoadingTab(true);
    setTabError("");
    const endpoint = activeTab === "sessions"
      ? `${SESSIONS_ENDPOINT}?vault_id=${encodeURIComponent(vault.vault_id)}&page=1&page_size=8&sort=updated_at&order=desc`
      : `${VAULTS_ENDPOINT}/${vault.vault_id}/policy`;
    requestJson(endpoint)
      .then((response) => {
        if (active) setTabData((current) => ({ ...current, [activeTab]: response }));
      })
      .catch((error) => {
        if (active) setTabError(error.message);
      })
      .finally(() => {
        if (active) setLoadingTab(false);
      });
    return () => { active = false; };
  }, [activeTab, tabData, vault.vault_id]);

  function reload() {
    setTabData({});
    setTabError("");
    onRefresh();
  }

  const sessions = tabData.sessions?.sessions || [];
  const policy = tabData.policy?.policy;

  return React.createElement(
    React.Fragment,
    null,
    React.createElement("button", { className: "workbench-drawer-scrim", type: "button", "aria-label": "关闭 Vault 详情", onClick: onClose }),
    React.createElement(
      "aside",
      { className: "workbench-drawer", "aria-label": `${vault.display_name}详情`, role: "dialog", "aria-modal": "true" },
      React.createElement(
        "header",
        { className: "workbench-drawer-header" },
        React.createElement(
          "div",
          null,
          React.createElement("h2", null, vault.display_name),
          React.createElement("p", { className: "workbench-drawer-subtitle" }, vault.is_current ? "当前 Vault" : "已授权 Vault")
        ),
        React.createElement(IconButton, { icon: X, label: "关闭 Vault 详情", className: "icon-button workbench-close-button", type: "button", onClick: onClose })
      ),
      React.createElement(
        "div",
        { className: "workbench-drawer-tabs", role: "tablist", "aria-label": "Vault 详情标签" },
        tabs.map(([id, label]) => React.createElement("button", {
          key: id,
          className: `workbench-drawer-tab${activeTab === id ? " is-active" : ""}`,
          type: "button",
          role: "tab",
          "aria-selected": activeTab === id,
          onClick: () => { setActiveTab(id); setTabError(""); }
        }, label))
      ),
      React.createElement(
        "div",
        { className: "workbench-drawer-body" },
        React.createElement(
          "div",
          { className: "workbench-drawer-status-line" },
          React.createElement("span", { className: `workbench-state workbench-state-${vault.state}` }, workbenchStateText(vault.state)),
          React.createElement(IconButton, { icon: RefreshCw, label: "刷新摘要", className: "icon-button workbench-refresh-button", type: "button", onClick: reload })
        ),
        tabError ? React.createElement("p", { className: "workbench-inline-error", role: "alert" }, tabError) : null,
        loadingTab
          ? React.createElement("p", { className: "workbench-loading", role: "status" }, "正在展开详情…")
          : null,
        activeTab === "overview"
          ? React.createElement(
            React.Fragment,
            null,
            React.createElement("div", { className: "workbench-drawer-lead" }, "这是一个只读运营摘要。正文仍保持在各自的工作区内。"),
            React.createElement("div", { className: "workbench-drawer-metrics" },
              React.createElement(WorkbenchMetric, { label: "可检索块", value: index?.current_count ?? "—", note: index ? `失效 ${index.stale_count}` : "索引不可用", tone: index?.stale_count ? "warning" : "neutral", onClick: () => setActiveTab("index") }),
              React.createElement(WorkbenchMetric, { label: "语义覆盖", value: index?.semantic_eligible_block_count ? `${Math.round(index.semantic_covered_block_count / index.semantic_eligible_block_count * 100)}%` : "—", note: index ? `${index.semantic_covered_block_count} / ${index.semantic_eligible_block_count}` : "尚无数据", tone: "accent", onClick: () => setActiveTab("index") }),
              React.createElement(WorkbenchMetric, { label: "任务", value: vault.tasks.total, note: vault.tasks.attention ? `需处理 ${vault.tasks.attention}` : `已完成 ${vault.tasks.completed}`, tone: vault.tasks.attention ? "warning" : "neutral", onClick: () => setActiveTab("tasks") }),
              React.createElement(WorkbenchMetric, { label: "会话", value: vault.sessions.total, note: vault.sessions.latest_at ? `最近 ${workbenchTimeText(vault.sessions.latest_at)}` : "暂无会话", tone: "neutral", onClick: () => setActiveTab("sessions") })
            ),
            React.createElement("div", { className: "workbench-drawer-actions" },
              React.createElement("button", { className: "primary-button", type: "button", onClick: () => onNavigate("materials") }, "打开资料"),
              React.createElement("button", { className: "secondary-button", type: "button", onClick: () => onNavigate("tasks") }, "查看任务"),
              React.createElement("button", { className: "secondary-button", type: "button", onClick: () => onNavigate("sessions") }, "进入会话")
            )
          )
          : activeTab === "index"
            ? React.createElement(
              "section",
              { className: "workbench-tab-section", "aria-label": "索引摘要" },
              React.createElement("h3", null, "索引与语义覆盖"),
              index
                ? React.createElement(
                  React.Fragment,
                  null,
                  React.createElement("div", { className: "workbench-stat-list" },
                    React.createElement("div", null, React.createElement("span", null, "当前块"), React.createElement("strong", null, index.current_count)),
                    React.createElement("div", null, React.createElement("span", null, "失效块"), React.createElement("strong", { className: index.stale_count ? "is-warning" : "" }, index.stale_count)),
                    React.createElement("div", null, React.createElement("span", null, "待关联"), React.createElement("strong", { className: index.pending_count ? "is-warning" : "" }, index.pending_count)),
                    React.createElement("div", null, React.createElement("span", null, "失败"), React.createElement("strong", { className: index.failure_count ? "is-danger" : "" }, index.failure_count))
                  ),
                  React.createElement("p", { className: "workbench-note" }, `语义状态：${index.semantic_status}；覆盖 ${index.semantic_covered_block_count} / ${index.semantic_eligible_block_count}。`),
                  React.createElement("button", { className: "secondary-button", type: "button", onClick: () => onNavigate("materials") }, "进入索引维护")
                )
                : React.createElement("p", { className: "workbench-empty" }, "当前 Vault 不可用，索引摘要暂不可读取。")
            )
            : activeTab === "tasks"
              ? React.createElement(
                "section",
                { className: "workbench-tab-section", "aria-label": "资料任务摘要" },
                React.createElement("h3", null, "资料任务"),
                React.createElement("div", { className: "workbench-stat-list" },
                  React.createElement("div", null, React.createElement("span", null, "总任务"), React.createElement("strong", null, vault.tasks.total)),
                  React.createElement("div", null, React.createElement("span", null, "运行中"), React.createElement("strong", null, vault.tasks.running)),
                  React.createElement("div", null, React.createElement("span", null, "需处理"), React.createElement("strong", { className: vault.tasks.attention ? "is-warning" : "" }, vault.tasks.attention)),
                  React.createElement("div", null, React.createElement("span", null, "已完成"), React.createElement("strong", null, vault.tasks.completed))
                ),
                React.createElement("p", { className: "workbench-note" }, vault.tasks.latest_at ? `最近任务更新：${workbenchTimeText(vault.tasks.latest_at)}` : "当前没有导入任务。"),
                React.createElement("button", { className: "primary-button", type: "button", onClick: () => onNavigate("tasks") }, "打开任务中心")
              )
              : activeTab === "sessions"
                  ? React.createElement(
                    "section",
                    { className: "workbench-tab-section", "aria-label": "会话摘要" },
                    React.createElement("h3", null, "会话活动"),
                    sessions.length
                      ? React.createElement("ul", { className: "workbench-mini-list" }, sessions.map((session) => React.createElement("li", { key: session.session_id }, React.createElement("strong", null, session.title), React.createElement("span", null, `消息 ${session.message_count || 0} · ${workbenchTimeText(session.last_activity_at || session.updated_at)}`))))
                      : React.createElement("p", { className: "workbench-empty" }, "当前 Vault 尚无会话记录。"),
                    React.createElement("button", { className: "primary-button", type: "button", onClick: () => onNavigate("sessions") }, "打开会话工作区")
                  )
                  : React.createElement(
                    "section",
                    { className: "workbench-tab-section", "aria-label": "Vault 策略摘要" },
                    React.createElement("h3", null, "外发与排除策略"),
                    policy
                      ? React.createElement(React.Fragment, null,
                        React.createElement("div", { className: "workbench-policy-state" }, React.createElement("strong", null, policy.outbound_mode === "always-allow" ? "默认允许出网" : policy.outbound_mode), React.createElement("span", null, `规则 ${policy.rules?.length || 0} 条`)),
                        React.createElement("p", { className: "workbench-note" }, "策略仅影响该 Vault 的导入、索引和外发边界，不会扩展到其他 Vault。"),
                        React.createElement("button", { className: "secondary-button", type: "button", onClick: () => onNavigate("settings") }, "打开策略设置")
                      )
                      : React.createElement("p", { className: "workbench-empty" }, "切换到此标签后读取策略摘要。")
                  )
      )
    )
  );
}

export function WorkbenchOverview({ overview, isLoading, error, selectedVaultId, onSelectVault, onRefresh, onNavigate }) {
  const [filter, setFilter] = React.useState("all");
  const vaults = overview?.vaults || [];
  const filteredVaults = vaults.filter((vault) => {
    if (filter === "attention") return ["attention", "unavailable", "inactive"].includes(vault.state);
    if (filter === "running") return vault.state === "running" || vault.tasks.running > 0;
    if (filter === "healthy") return vault.state === "healthy";
    return true;
  });
  const summary = {
    total: vaults.length,
    available: vaults.filter((vault) => vault.access_status === "available" && vault.authorization_status === "active").length,
    attention: vaults.filter((vault) => ["attention", "unavailable", "inactive"].includes(vault.state)).length,
    running: vaults.reduce((total, vault) => total + vault.tasks.running, 0),
    blocks: vaults.reduce((total, vault) => total + (vault.index?.current_count || 0), 0)
  };
  const selectedVault = vaults.find((vault) => vault.vault_id === selectedVaultId) || null;
  const attentionContent = overview?.attention?.length
    ? React.createElement("ul", { className: "workbench-activity-list" }, overview.attention.slice(0, 6).map((item, index) => React.createElement(
      "li",
      { key: `${item.kind}:${item.vault_id}:${item.task_id || index}` },
      React.createElement(
        "button",
        { className: "workbench-activity-button", type: "button", onClick: () => onSelectVault(item.vault_id) },
        React.createElement("span", { className: `workbench-activity-mark workbench-activity-mark-${item.status}` }),
        React.createElement("span", null, React.createElement("strong", null, item.title), React.createElement("small", null, `${item.vault_label} · ${item.detail}`)),
        React.createElement("span", { className: "workbench-activity-time" }, workbenchTimeText(item.updated_at))
      )
    )))
    : React.createElement("p", { className: "workbench-empty" }, "所有 Vault 暂无待处理异常。");
  const activityContent = overview?.activity?.length
    ? React.createElement("ul", { className: "workbench-activity-list" }, overview.activity.slice(0, 6).map((item, index) => React.createElement(
      "li",
      { key: `${item.kind}:${item.vault_id}:${item.updated_at}:${index}` },
      React.createElement(
        "button",
        { className: "workbench-activity-button", type: "button", onClick: () => onSelectVault(item.vault_id) },
        React.createElement("span", { className: "workbench-activity-mark workbench-activity-mark-neutral" }),
        React.createElement("span", null, React.createElement("strong", null, item.label), React.createElement("small", null, `${item.vault_label} · ${item.kind === "session" ? "会话活动" : workbenchLifecycleText(item.status)}`)),
        React.createElement("span", { className: "workbench-activity-time" }, workbenchTimeText(item.updated_at))
      )
    )))
    : React.createElement("p", { className: "workbench-empty" }, "尚无最近活动。");

  if (isLoading && !overview) {
    return React.createElement("section", { className: "workbench-overview", "aria-label": "全 Vault 工作台" }, React.createElement("div", { className: "workbench-loading-panel", role: "status" }, React.createElement("span", { className: "workbench-loading-mark" }), React.createElement("div", null, React.createElement("strong", null, "正在构建 Vault 全景"), React.createElement("p", null, "读取索引、任务与活动摘要…"))));
  }
  if (error && !overview) {
    return React.createElement("section", { className: "workbench-overview", "aria-label": "全 Vault 工作台" }, React.createElement("div", { className: "workbench-error-panel", role: "alert" }, React.createElement("strong", null, "工作台摘要暂不可用"), React.createElement("p", null, error), React.createElement("button", { className: "primary-button", type: "button", onClick: onRefresh }, "重新读取")));
  }

  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      "section",
      { className: "workbench-overview", "aria-label": "全 Vault 工作台" },
      React.createElement(
        "header",
        { className: "workbench-overview-heading" },
        React.createElement("span", { className: "workbench-refresh-time" }, overview?.updated_at ? `更新于 ${workbenchTimeText(overview.updated_at)}` : "等待读取"),
        React.createElement(IconButton, { icon: RefreshCw, label: "刷新工作台", className: "icon-button workbench-refresh-button", type: "button", onClick: onRefresh })
      ),
      React.createElement(
        "div",
        { className: "workbench-summary-strip", "aria-label": "全局摘要" },
        React.createElement(WorkbenchMetric, { label: "已授权 Vault", value: summary.total, note: `${summary.available} 个可用`, tone: "neutral" }),
        React.createElement(WorkbenchMetric, { label: "需要处理", value: summary.attention, note: "索引、任务或访问状态", tone: summary.attention ? "warning" : "neutral", onClick: () => setFilter("attention") }),
        React.createElement(WorkbenchMetric, { label: "运行中任务", value: summary.running, note: "保持上下文即可离开", tone: summary.running ? "accent" : "neutral", onClick: () => setFilter("running") }),
        React.createElement(WorkbenchMetric, { label: "可检索块", value: summary.blocks, note: "仅统计当前索引", tone: "neutral" })
      ),
      React.createElement(
        "div",
        { className: "workbench-filter-bar" },
        React.createElement("span", { className: "workbench-filter-label" }, "查看范围"),
        [
          ["all", "全部 Vault"],
          ["attention", "需处理"],
          ["running", "运行中"],
          ["healthy", "健康"]
        ].map(([id, label]) => React.createElement("button", { key: id, className: `workbench-filter${filter === id ? " is-active" : ""}`, type: "button", "aria-pressed": filter === id, onClick: () => setFilter(id) }, label)),
        React.createElement("span", { className: "workbench-filter-count" }, `${filteredVaults.length} / ${vaults.length}`)
      ),
      React.createElement(
        "section",
        { className: "workbench-vault-section", "aria-labelledby": "workbench-vault-heading" },
        React.createElement("div", { className: "workbench-section-heading" }, React.createElement("h3", { id: "workbench-vault-heading" }, "Vault")),
        filteredVaults.length
          ? React.createElement("div", { className: "workbench-vault-table" },
            React.createElement("div", { className: "workbench-vault-table-head", role: "row" }, React.createElement("span", null, "Vault"), React.createElement("span", null, "状态"), React.createElement("span", null, "索引"), React.createElement("span", null, "任务"), React.createElement("span", null, "活动")),
            filteredVaults.map((vault) => React.createElement("article", { className: `workbench-vault-row workbench-vault-row-${vault.state}`, key: vault.vault_id },
              React.createElement("button", { className: "workbench-vault-main", type: "button", onClick: () => onSelectVault(vault.vault_id) }, React.createElement("span", { className: "workbench-vault-name" }, vault.display_name), React.createElement("span", { className: "workbench-vault-meta" }, vault.is_current ? "当前工作上下文" : "已授权资料库")),
              React.createElement("button", { className: "workbench-vault-state-cell", type: "button", onClick: () => onSelectVault(vault.vault_id) }, React.createElement("span", { className: `workbench-state workbench-state-${vault.state}` }, workbenchStateText(vault.state)), React.createElement("small", null, vault.access_reason || "访问状态正常")),
              React.createElement("button", { className: "workbench-vault-data-cell", type: "button", onClick: () => onSelectVault(vault.vault_id) }, React.createElement("strong", null, vault.index?.current_count ?? "—"), React.createElement("small", null, vault.index ? `失效 ${vault.index.stale_count} · 待关联 ${vault.index.pending_count}` : "索引不可用")),
              React.createElement("button", { className: "workbench-vault-data-cell", type: "button", onClick: () => onSelectVault(vault.vault_id) }, React.createElement("strong", null, vault.tasks.total), React.createElement("small", null, vault.tasks.attention ? `需处理 ${vault.tasks.attention}` : `运行中 ${vault.tasks.running}`)),
              React.createElement("button", { className: "workbench-vault-data-cell", type: "button", onClick: () => onSelectVault(vault.vault_id) }, React.createElement("strong", null, vault.sessions.total), React.createElement("small", null, workbenchTimeText(vault.sessions.latest_at)))
            )))
          : React.createElement("p", { className: "workbench-empty" }, "当前筛选没有匹配的 Vault。"),
        null
      ),
      React.createElement(
        "div",
        { className: "workbench-lower-grid" },
        React.createElement("section", { className: "workbench-lower-section", "aria-labelledby": "workbench-attention-heading" }, React.createElement("div", { className: "workbench-section-heading" }, React.createElement("h3", { id: "workbench-attention-heading" }, "优先处理"), React.createElement("span", null, `${overview?.attention?.length || 0} 项`)), attentionContent),
        React.createElement("section", { className: "workbench-lower-section", "aria-labelledby": "workbench-activity-heading" }, React.createElement("div", { className: "workbench-section-heading" }, React.createElement("h3", { id: "workbench-activity-heading" }, "最近动态"), React.createElement("span", null, "按 Vault 汇总")), activityContent)
      )
    ),
    selectedVault ? React.createElement(VaultOverviewDrawer, { vault: selectedVault, onClose: () => onSelectVault(null), onNavigate, onRefresh }) : null
  );
}

function VaultDetail({ vault, onBack, onUpdate, onRelink, onConfirm }) {
  const [status, setStatus] = React.useState("");

  async function callAction(action) {
    setStatus("");
    try {
      const response = await requestJson(`${VAULTS_ENDPOINT}/${vault.vault_id}/${action}`, {
        method: "POST"
      });
      onUpdate(response.vault);
      setStatus(action === "reauthorize" ? "授权已重新验证。" : "当前 vault 已切换。");
    } catch (error) {
      setStatus(error.message);
    }
  }

  return React.createElement(
    "section",
    { className: "vault-detail", "aria-label": `${vaultName(vault)}详情` },
    React.createElement("button", { className: "back-button", type: "button", onClick: onBack }, "返回 vault 列表"),
    React.createElement("h2", null, vaultName(vault)),
    React.createElement(
      "p",
      { className: "status-line", "aria-live": "polite" },
      `状态：${statusText(vault)}${vault.is_current ? "；当前 vault" : ""}`
    ),
    React.createElement(
      "dl",
      { className: "vault-metadata" },
      React.createElement("dt", null, "路径"),
      React.createElement("dd", null, vault.path),
      React.createElement("dt", null, "写入权限"),
      React.createElement("dd", null, vault.access_status === "available" ? "可读写" : "不可用"),
      vault.access_reason
        ? React.createElement("dt", null, "访问问题")
        : null,
      vault.access_reason
        ? React.createElement("dd", null, vault.access_reason)
        : null,
      React.createElement("dt", null, "索引状态"),
      React.createElement("dd", null, vault.index?.status === "not-initialized" ? "未初始化" : vault.index?.status || vault.index_status),
      React.createElement("dt", null, "受管根目录"),
      React.createElement("dd", null, vault.managed_root),
      React.createElement("dt", null, "隔离边界"),
      React.createElement("dd", null, "文件、索引和操作状态仅属于此 vault。")
    ),
    React.createElement(VaultIndexStatus, { vault, onUpdate }),
    React.createElement(VaultPolicyControls, { vault, onUpdate }),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null,
    React.createElement(
      "div",
      { className: "detail-actions" },
      !vault.is_current && vault.authorization_status === "active" && vault.access_status === "available"
        ? React.createElement(
            "button",
            { className: "primary-button", type: "button", onClick: () => callAction("current") },
            "设为当前 vault"
          )
        : null,
      vault.recovery_actions.includes("reauthorize")
        ? React.createElement(
            "button",
            { className: "secondary-button", type: "button", onClick: () => callAction("reauthorize") },
            "重新授权"
          )
        : null,
      vault.recovery_actions.includes("relink")
        ? React.createElement(
            "button",
            { className: "secondary-button", type: "button", onClick: () => onRelink(vault) },
            "重新关联"
          )
        : null,
      vault.recovery_actions.includes("read-only")
        ? React.createElement(
            "button",
            { className: "secondary-button", type: "button", onClick: () => setStatus("正在只读查看 vault 详情。") },
            "只读查看"
          )
        : null,
      vault.authorization_status === "active"
        ? React.createElement(
            "button",
            { className: "secondary-button", type: "button", onClick: (event) => onConfirm("deactivate", vault, event.currentTarget) },
            "停用"
          )
        : null,
      React.createElement(
        "button",
        { className: "danger-button", type: "button", onClick: (event) => onConfirm("remove", vault, event.currentTarget) },
        "移除授权"
      )
    )
  );
}

function ProviderForm({ provider, onCancel, onComplete }) {
  const [name, setName] = React.useState(provider?.name || "");
  const [endpoint, setEndpoint] = React.useState(provider?.endpoint || "");
  const [apiMode, setApiMode] = React.useState(provider?.api_mode || "chat-completions");
  const [status, setStatus] = React.useState("");
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const secretRef = React.useRef(null);
  const isEditing = Boolean(provider);

  async function submit(event) {
    event.preventDefault();
    const secret = secretRef.current?.value || "";
    setStatus("");
    setIsSubmitting(true);
    try {
      const payload = { name, endpoint, api_mode: apiMode };
      if (secret) payload.secret = secret;
      const response = await requestJson(
        isEditing ? `${PROVIDERS_ENDPOINT}/${provider.provider_id}` : PROVIDERS_ENDPOINT,
        { method: isEditing ? "PUT" : "POST", body: JSON.stringify(payload) }
      );
      onComplete(response.provider);
    } catch (error) {
      setStatus(error.message);
    } finally {
      if (secretRef.current) secretRef.current.value = "";
      setIsSubmitting(false);
    }
  }

  return React.createElement(
    "form",
    { className: "provider-form", onSubmit: submit, "aria-label": isEditing ? "编辑 Provider" : "添加 Provider" },
    React.createElement("h2", null, isEditing ? "编辑 Provider" : "添加 Provider"),
    React.createElement("p", { className: "form-description" }, "支持 Chat Completions 和 Responses API。API Key 可选；本地服务留空时不会发送鉴权头。"),
    React.createElement(
      "label",
      { className: "form-row", htmlFor: "provider-name" },
      React.createElement("span", { className: "form-label" }, "名称"),
      React.createElement("input", { id: "provider-name", value: name, required: true, disabled: isSubmitting, onChange: (event) => setName(event.target.value) })
    ),
    React.createElement(
      "label",
      { className: "form-row", htmlFor: "provider-endpoint" },
      React.createElement("span", { className: "form-label" }, "服务地址"),
      React.createElement("input", { id: "provider-endpoint", type: "url", value: endpoint, required: true, disabled: isSubmitting, onChange: (event) => setEndpoint(event.target.value) }),
      React.createElement("span", { className: "form-help" }, "例如 https://service.example/v1")
    ),
    React.createElement(
      "label",
      { className: "form-row", htmlFor: "provider-api-mode" },
      React.createElement("span", { className: "form-label" }, "API 模式"),
      React.createElement(
        "select",
        { id: "provider-api-mode", value: apiMode, disabled: isSubmitting, onChange: (event) => setApiMode(event.target.value) },
        React.createElement("option", { value: "chat-completions" }, "Chat Completions"),
        React.createElement("option", { value: "responses" }, "Responses API")
      ),
      React.createElement("span", { className: "form-help" }, "Responses API 使用 /responses；Chat Completions 使用 /chat/completions。")
    ),
    React.createElement(
      "label",
      { className: "form-row", htmlFor: "provider-secret" },
      React.createElement("span", { className: "form-label" }, isEditing ? "替换 API Key（可选）" : "API Key（可选）"),
      React.createElement("input", { id: "provider-secret", type: "password", ref: secretRef, autoComplete: "new-password", disabled: isSubmitting }),
      React.createElement("span", { className: "form-help" }, isEditing ? "留空会保留当前 API Key。提交后此字段会立即清空。" : "本地服务可留空；提交后此字段会立即清空。")
    ),
    status ? React.createElement("p", { className: "form-error", role: "alert" }, status) : null,
    React.createElement(
      "div",
      { className: "form-actions" },
      React.createElement("button", { className: "secondary-button", type: "button", disabled: isSubmitting, onClick: onCancel }, "取消"),
      React.createElement("button", { className: "primary-button", type: "submit", disabled: !name || !endpoint || isSubmitting }, isEditing ? "保存 Provider" : "添加 Provider")
    )
  );
}

function ProviderStatusLights({ provider }) {
  const labels = {
    discovery: "模型发现",
    health: "服务健康"
  };
  return React.createElement(
    "div",
    { className: "provider-status-lights", "aria-live": "polite" },
    Object.entries(labels).map(([key, label]) => {
      const probe = provider.verification[key];
      const status = probe.ok ? "通过" : userFacingProviderReason(probe.reason);
      return React.createElement(
        "span",
        {
          key,
          className: probe.ok ? "provider-status-light is-ok" : "provider-status-light is-failed",
          role: "img",
          "aria-label": `${label}：${status}`,
          title: `${label}：${status}`
        },
        React.createElement("span", { className: "visually-hidden" }, `${label}：${status}`)
      );
    })
  );
}

function ModelDefaultSelector({ modelType, label, providers, modelDefault, onChange, onClear }) {
  modelDefault = modelDefault || {
    default: null,
    status: "unconfigured",
    reason: `尚未配置${modelTypeLabel(modelType)} Model。`
  };
  const options = modelOptions(providers, modelType);
  const selectedValue = modelDefault.default
    ? JSON.stringify([modelDefault.default.provider_id, modelDefault.default.model_id])
    : "";
  const selectedIsAvailable = options.some(({ provider, model }) => (
    JSON.stringify([provider.provider_id, model.model_id]) === selectedValue
  ));

  async function changeDefault(event) {
    const value = event.target.value;
    if (!value) {
      await onClear(modelType);
      return;
    }
    const [providerId, modelId] = JSON.parse(value);
    await onChange(modelType, providerId, modelId);
  }

  return React.createElement(
    "label",
    { className: "provider-default-row", htmlFor: `${modelType}-model-default` },
    React.createElement("span", { className: "visually-hidden" }, label),
    React.createElement(
      "select",
      { id: `${modelType}-model-default`, value: selectedValue, disabled: options.length === 0 && !selectedValue, onChange: changeDefault },
      React.createElement("option", { value: "" }, options.length === 0 ? "没有已验证的 Model" : `选择${label}`),
      selectedValue && !selectedIsAvailable
        ? React.createElement("option", { value: selectedValue }, `当前不可用：${modelDefault.default.model_id}`)
      : null,
      options.map(({ provider, model }) => React.createElement("option", { key: `${provider.provider_id}-${model.model_id}`, value: JSON.stringify([provider.provider_id, model.model_id]) }, `${provider.name} / ${model.model_id}`))
    ),
    modelDefault.reason ? React.createElement("span", { className: "provider-default-status" }, modelDefault.reason) : null
  );
}

function MarkdownStructureBudgetSettings() {
  const [budget, setBudget] = React.useState({
    minimum_tokens: 10000,
    target_tokens: 16000,
    maximum_tokens: 20000
  });
  const [isLoading, setIsLoading] = React.useState(true);
  const [isSaving, setIsSaving] = React.useState(false);
  const [status, setStatus] = React.useState("");

  React.useEffect(() => {
    let active = true;
    requestJson(MARKDOWN_STRUCTURE_BUDGET_ENDPOINT)
      .then((response) => {
        if (active) setBudget(response.budget);
      })
      .catch((error) => {
        if (active) setStatus(error.message);
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });
    return () => { active = false; };
  }, []);

  function updateBudget(field, value) {
    setBudget((current) => ({ ...current, [field]: Number(value) }));
  }

  async function saveBudget(event) {
    event.preventDefault();
    setStatus("");
    setIsSaving(true);
    try {
      const response = await requestJson(MARKDOWN_STRUCTURE_BUDGET_ENDPOINT, {
        method: "PUT",
        body: JSON.stringify(budget)
      });
      setBudget(response.budget);
      setStatus("Markdown 分块 Token 预算已保存，将用于后续导入。");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsSaving(false);
    }
  }

  return React.createElement(
    "section",
    { className: "markdown-budget-section", "aria-labelledby": "markdown-token-budget-heading" },
    React.createElement(
      "div",
      { className: "model-default-heading" },
      React.createElement("span", { className: "model-default-icon", "aria-hidden": "true" }, React.createElement(SlidersHorizontal, { size: 18, strokeWidth: 1.8 })),
      React.createElement(
        "div",
        null,
        React.createElement("h3", { id: "markdown-token-budget-heading" }, "Markdown 分块 Token 预算"),
        React.createElement("p", { className: "model-default-description" }, "结构安全分块会尽量接近目标值且不超过最大值。")
      )
    ),
    React.createElement(
      "form",
      { className: "markdown-budget-form", onSubmit: saveBudget, "aria-label": "Markdown 分块 Token 预算" },
      React.createElement(
        "div",
        { className: "token-budget-fields" },
        [["minimum_tokens", "最小 Token"], ["target_tokens", "目标 Token"], ["maximum_tokens", "最大 Token"]].map(([field, label]) => React.createElement(
          "label",
          { className: "token-budget-field", htmlFor: `markdown-budget-${field}`, key: field },
          React.createElement("span", { className: "form-label" }, label),
          React.createElement("input", {
            id: `markdown-budget-${field}`,
            type: "number",
            min: 1,
            max: 20000,
            step: 1000,
            value: budget[field],
            disabled: isLoading || isSaving,
            onChange: (event) => updateBudget(field, event.target.value)
          })
        ))
      ),
      React.createElement("button", { className: "secondary-button", type: "submit", disabled: isLoading || isSaving }, isSaving ? "保存中…" : "保存预算")
    ),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null
  );
}

function OnlineParseProviderSettings() {
  const [providers, setProviders] = React.useState([]);
  const [status, setStatus] = React.useState("");
  const [isLoading, setIsLoading] = React.useState(true);
  const [isSubmitting, setIsSubmitting] = React.useState(null);
  const secrets = React.useRef({});

  const load = React.useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await requestJson(ONLINE_PARSE_PROVIDERS_ENDPOINT);
      setProviders(response.providers || []);
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  React.useEffect(() => { void load(); }, [load]);

  async function save(provider) {
    const secretInput = secrets.current[provider.provider_id];
    const secret = secretInput?.value || "";
    setStatus("");
    setIsSubmitting(provider.provider_id);
    try {
      const response = await requestJson(`${ONLINE_PARSE_PROVIDERS_ENDPOINT}/${provider.provider_id}`, {
        method: "PUT",
        body: JSON.stringify({ endpoint: provider.endpoint || null, ...(secret ? { secret } : {}) })
      });
      setProviders((current) => current.map((item) => item.provider_id === provider.provider_id ? response.provider : item));
      if (secretInput) secretInput.value = "";
      setStatus(`${provider.name} 配置已保存，请执行连接测试。`);
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsSubmitting(null);
    }
  }

  async function test(provider) {
    setStatus("");
    setIsSubmitting(provider.provider_id);
    try {
      const response = await requestJson(`${ONLINE_PARSE_PROVIDERS_ENDPOINT}/${provider.provider_id}/test`, { method: "POST" });
      setProviders((current) => current.map((item) => item.provider_id === provider.provider_id ? response.provider : item));
      setStatus(response.provider.verified ? `${provider.name} 连接测试通过。` : `${provider.name} 连接测试未通过。`);
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsSubmitting(null);
    }
  }

  return React.createElement(
    "section",
    { className: "online-parse-provider-settings", "aria-labelledby": "online-parse-provider-heading" },
    React.createElement("h3", { id: "online-parse-provider-heading" }, "在线解析"),
    React.createElement("p", { className: "model-default-description" }, "仅用于创建时明确启用的 PDF 任务。保存凭据后需单独执行连接测试。"),
    isLoading ? React.createElement("p", { className: "empty-state", role: "status" }, "正在读取在线解析 Provider。") : null,
    providers.map((provider) => React.createElement(
      "div",
      { className: "online-parse-provider-row", key: provider.provider_id },
      React.createElement(
        "div",
        { className: "online-parse-provider-summary" },
        React.createElement("strong", null, provider.name),
        React.createElement("span", { className: provider.verified ? "provider-status-badge is-verified" : "provider-status-badge is-pending" }, provider.verified ? "已验证" : "待验证"),
        React.createElement("span", { className: "row-note" }, `固定模型：${provider.model}；凭据：${provider.credential_configured ? "已配置" : "未配置"}`)
      ),
      React.createElement(
        "label",
        { className: "form-row" },
        React.createElement("span", { className: "form-label" }, "服务地址"),
        React.createElement("input", {
          type: "url",
          value: provider.endpoint || "",
          placeholder: provider.uses_official_endpoint ? "官方默认地址" : "https://mineru.net",
          disabled: isSubmitting === provider.provider_id,
          onChange: (event) => setProviders((current) => current.map((item) => item.provider_id === provider.provider_id ? { ...item, endpoint: event.target.value || null } : item))
        })
      ),
      React.createElement(
        "label",
        { className: "form-row" },
        React.createElement("span", { className: "form-label" }, "替换 API Key（可选）"),
        React.createElement("input", {
          type: "password",
          autoComplete: "new-password",
          disabled: isSubmitting === provider.provider_id,
          ref: (element) => { secrets.current[provider.provider_id] = element; }
        })
      ),
      React.createElement(
        "div",
        { className: "detail-actions" },
        React.createElement("button", { className: "secondary-button", type: "button", disabled: isSubmitting === provider.provider_id, onClick: () => save(provider) }, "保存"),
        React.createElement("button", { className: "secondary-button", type: "button", disabled: isSubmitting === provider.provider_id || !provider.credential_configured, onClick: () => test(provider) }, "连接测试")
      )
    )),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null
  );
}

export function ProviderManagement({ providers, isLoading, modelDefaults, onOpenForm, onUpdate, onConfirm, onDefaultsChange }) {
  const [status, setStatus] = React.useState("");
  const [addingProviderId, setAddingProviderId] = React.useState(null);
  const [addModelId, setAddModelId] = React.useState("");
  const [addModelType, setAddModelType] = React.useState("");
  const [isAddingModel, setIsAddingModel] = React.useState(false);
  const [verifyingModelKey, setVerifyingModelKey] = React.useState(null);

  async function testProvider(provider) {
    setStatus("正在测试 Provider。");
    try {
      const response = await requestJson(`${PROVIDERS_ENDPOINT}/${provider.provider_id}/test`, { method: "POST" });
      onUpdate(response.provider);
      onDefaultsChange();
      setStatus(response.provider.verification.is_verified ? "Provider 发现和健康验证通过。" : "Provider 验证未通过；请查看各项原因。");
    } catch (error) {
      setStatus(userFacingProviderReason(error.message));
    }
  }

  function openModelPicker(provider) {
    const candidates = unconfiguredProviderModels(provider);
    const firstCandidate = candidates[0];
    setStatus("");
    setAddingProviderId((current) => current === provider.provider_id ? null : provider.provider_id);
    setAddModelId(firstCandidate?.model_id || "");
    setAddModelType(firstCandidate?.model_type || "");
  }

  function selectAddModel(modelId, provider) {
    const model = unconfiguredProviderModels(provider).find((candidate) => candidate.model_id === modelId);
    setAddModelId(modelId);
    setAddModelType(model?.model_type || "");
  }

  async function configureAndVerifyModel(providerId, modelId, modelType) {
    const configured = await requestJson(`${PROVIDERS_ENDPOINT}/${providerId}/models`, {
      method: "PUT",
      body: JSON.stringify({ model_id: modelId, model_type: modelType })
    });
    onUpdate(configured.provider);
    const tested = await requestJson(`${PROVIDERS_ENDPOINT}/${providerId}/models/test`, {
      method: "POST",
      body: JSON.stringify({ model_id: modelId })
    });
    onUpdate(tested.provider);
    await onDefaultsChange();
    return tested.provider;
  }

  async function reconfigureModel(providerId, modelId, modelType) {
    const modelKey = `${providerId}:${modelId}`;
    setStatus("正在验证模型。");
    setVerifyingModelKey(modelKey);
    try {
      const provider = await configureAndVerifyModel(providerId, modelId, modelType);
      setStatus(provider.models.find((model) => model.model_id === modelId)?.verification.ok
        ? "模型类型已更新并验证。"
        : userFacingProviderReason(provider.models.find((model) => model.model_id === modelId)?.verification.reason));
    } catch (error) {
      setStatus(userFacingProviderReason(error.message));
    } finally {
      setVerifyingModelKey(null);
    }
  }

  async function verifyModel(providerId, modelId) {
    const modelKey = `${providerId}:${modelId}`;
    setStatus("正在验证模型。");
    setVerifyingModelKey(modelKey);
    try {
      const response = await requestJson(`${PROVIDERS_ENDPOINT}/${providerId}/models/test`, {
        method: "POST",
        body: JSON.stringify({ model_id: modelId })
      });
      onUpdate(response.provider);
      await onDefaultsChange();
      setStatus(response.provider.models.find((model) => model.model_id === modelId)?.verification.ok
        ? "模型验证已完成。"
        : userFacingProviderReason(response.provider.models.find((model) => model.model_id === modelId)?.verification.reason));
    } catch (error) {
      setStatus(userFacingProviderReason(error.message));
    } finally {
      setVerifyingModelKey(null);
    }
  }

  async function addModel(provider) {
    if (!addModelId || !addModelType) {
      setStatus("请选择模型和模型类型。");
      return;
    }
    setStatus("正在验证模型。");
    setIsAddingModel(true);
    try {
      const testedProvider = await configureAndVerifyModel(provider.provider_id, addModelId, addModelType);
      setAddingProviderId(null);
      setStatus(testedProvider.models.find((model) => model.model_id === addModelId)?.verification.ok
        ? "模型已添加并验证。"
        : userFacingProviderReason(testedProvider.models.find((model) => model.model_id === addModelId)?.verification.reason));
    } catch (error) {
      setStatus(userFacingProviderReason(error.message));
    } finally {
      setIsAddingModel(false);
    }
  }

  async function changeDefault(modelType, providerId, modelId) {
    setStatus("");
    try {
      await requestJson(`${PROVIDERS_ENDPOINT}/defaults/${modelType}`, {
        method: "PUT",
        body: JSON.stringify({ provider_id: providerId, model_id: modelId })
      });
      await onDefaultsChange();
      setStatus(`${modelTypeLabel(modelType)}默认 Model 已更新。`);
    } catch (error) {
      setStatus(error.message);
    }
  }

  async function clearDefault(modelType) {
    setStatus("");
    try {
      await requestJson(`${PROVIDERS_ENDPOINT}/defaults/${modelType}`, { method: "DELETE" });
      await onDefaultsChange();
      setStatus(`${modelTypeLabel(modelType)}默认 Model 已清除。`);
    } catch (error) {
      setStatus(error.message);
    }
  }

  return React.createElement(
    "section",
    { className: "provider-management", "aria-labelledby": "provider-settings-heading" },
    React.createElement(
      "div",
      { className: "list-heading provider-list-heading" },
      React.createElement(
        "div",
        { className: "provider-list-title" },
        React.createElement("span", { className: "provider-list-icon", "aria-hidden": "true" }, React.createElement(Settings, { size: 18, strokeWidth: 1.8 })),
        React.createElement("div", null, React.createElement("h2", { id: "provider-settings-heading" }, "Provider"), React.createElement("p", { className: "provider-list-description" }, "管理连接、验证状态和可用模型。"))
      ),
      React.createElement("button", { className: "primary-button", type: "button", onClick: () => onOpenForm(null) }, "添加 Provider")
    ),
    React.createElement(
      "div",
      { className: "provider-model-settings", "aria-label": "默认模型设置" },
      [
        ["chat", "对话与文本", "用于解析、分类、标签和会话。", MessageCircle],
        ["embedding", "语义检索", "用于本地知识库的语义索引与检索。", Search],
        ["rerank", "候选重排", "默认关闭；启用后仅发送允许外发的候选。", RefreshCw],
        ["markdown", "Markdown 结构化", "用于导入任务的长文结构识别与安全分块。", FileText]
      ].map(([modelType, title, description, Icon]) => React.createElement(
        "section",
        { className: "model-default-section model-default-card", key: modelType, "aria-labelledby": `${modelType}-model-heading` },
        React.createElement(
          "div",
          { className: "model-default-heading" },
          React.createElement("span", { className: "model-default-icon", "aria-hidden": "true" }, React.createElement(Icon, { size: 18, strokeWidth: 1.8 })),
          React.createElement("div", null, React.createElement("h3", { id: `${modelType}-model-heading` }, title), React.createElement("p", { className: "model-default-description" }, description))
        ),
        React.createElement(ModelDefaultSelector, {
          modelType,
          label: `${title}默认模型`,
          providers,
          modelDefault: modelDefaults[modelType],
          onChange: changeDefault,
          onClear: clearDefault
        })
      ))
    ),
    React.createElement(MarkdownStructureBudgetSettings),
    React.createElement(OnlineParseProviderSettings),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null,
    isLoading
      ? React.createElement("p", { className: "empty-state", role: "status" }, "正在加载 Provider 配置。")
      : null,
    !isLoading && providers.length === 0
      ? React.createElement("p", { className: "empty-state" }, "尚无 Provider。添加后先完成发现和健康验证，再配置并验证每个模型。")
      : null,
    providers.map((provider) => {
      const verifiedModels = verifiedProviderModels(provider);
      const configuredModels = configuredProviderModels(provider);
      const candidateModels = unconfiguredProviderModels(provider);
      const isPickerOpen = addingProviderId === provider.provider_id;
      return React.createElement(
        "div",
        { className: "section-row provider-row", key: provider.provider_id },
        React.createElement(
          "div",
          { className: "provider-summary" },
          React.createElement(
            "div",
            { className: "provider-summary-heading" },
            React.createElement("span", { className: "row-title" }, provider.name),
            React.createElement(ProviderStatusLights, { provider })
          ),
          React.createElement("span", { className: "row-note" }, `${apiModeLabel(provider)}；${verifiedModels.length} 个已验证模型`)
        ),
        React.createElement(
          "div",
          { className: "rule-actions provider-actions" },
          React.createElement("button", { className: "text-button", type: "button", onClick: () => openModelPicker(provider), "aria-expanded": isPickerOpen }, "添加模型"),
          React.createElement(IconButton, { icon: RefreshCw, label: `测试 ${provider.name}`, type: "button", onClick: () => testProvider(provider) }),
          React.createElement(IconButton, { icon: Settings, label: `编辑 ${provider.name}`, type: "button", onClick: () => onOpenForm(provider) }),
          React.createElement(IconButton, { icon: X, label: `删除 ${provider.name}`, className: "icon-button provider-remove-button", type: "button", onClick: (event) => onConfirm("provider-remove", provider, event.currentTarget) })
        ),
        configuredModels.length
          ? configuredModels.map((model) => {
            const modelKey = `${provider.provider_id}:${model.model_id}`;
            const isVerifying = verifyingModelKey === modelKey;
            const isModelBusy = isAddingModel || Boolean(verifyingModelKey);
            return React.createElement(
              "div",
              { className: "provider-model-row", key: `${provider.provider_id}-${model.model_id}` },
              React.createElement(
                "div",
                { className: "provider-model-identity" },
                React.createElement("span", { className: "row-title" }, model.model_id),
                React.createElement(IconButton, {
                  icon: Trash2,
                  label: `删除模型 ${model.model_id}`,
                  className: "icon-button provider-model-remove-button",
                  type: "button",
                  disabled: isModelBusy,
                  onClick: (event) => onConfirm(
                    "provider-model-remove",
                    { provider_id: provider.provider_id, model_id: model.model_id },
                    event.currentTarget
                  )
                })
              ),
              React.createElement(
                "label",
                { className: "model-type-selector" },
                React.createElement("span", { className: "visually-hidden" }, `${model.model_id} 模型类型`),
                React.createElement(
                  "select",
                  { value: model.model_type || "", disabled: !provider.verification.is_verified || isModelBusy,
                    onChange: (event) => reconfigureModel(provider.provider_id, model.model_id, event.target.value) },
                  React.createElement("option", { value: "" }, "选择类型"),
                  React.createElement("option", { value: "chat" }, "对话/文本生成"),
                  React.createElement("option", { value: "embedding" }, "Embedding"),
                  React.createElement("option", { value: "rerank" }, "Rerank（重排）"),
                  React.createElement("option", { value: "markdown" }, "Markdown 结构化")
                )
              ),
              React.createElement(
                "span",
                { className: model.verification?.ok ? "provider-check provider-check-ok" : "provider-check provider-check-failed" },
                model.verification?.ok ? "已验证" : "验证失败"
              ),
              React.createElement(
                "button",
                {
                  className: "text-button",
                  type: "button",
                  disabled: !model.model_type || !provider.verification.is_verified || isModelBusy,
                  onClick: () => verifyModel(provider.provider_id, model.model_id)
                },
                isVerifying ? "验证中" : model.verification?.ok ? "测试模型" : "重试"
              ),
              !model.verification?.ok
                ? React.createElement("p", { className: "provider-model-reason", role: "status" }, `原因：${userFacingProviderReason(model.verification?.reason)}`)
                : null
            );
          })
          : React.createElement("p", { className: "provider-model-empty" }, "暂无已配置模型；点击“添加模型”从已发现模型中选择并验证。"),
        isPickerOpen
          ? React.createElement(
            "form",
            { className: "provider-model-add", onSubmit: (event) => { event.preventDefault(); addModel(provider); }, "aria-label": `${provider.name} 添加模型` },
            React.createElement("h3", null, "添加未验证模型"),
            !provider.verification.is_verified
              ? React.createElement("p", { className: "form-help" }, "请先完成 Provider 测试，获取可添加的模型列表。")
              : candidateModels.length === 0
                ? React.createElement("p", { className: "form-help" }, "暂无可添加的未验证模型；请重新测试 Provider 刷新模型列表。")
                : React.createElement(
                  React.Fragment,
                  null,
                  React.createElement(
                    "label",
                    { className: "model-type-selector", htmlFor: `${provider.provider_id}-add-model` },
                    React.createElement("span", { className: "form-label" }, "模型"),
                    React.createElement(
                      "select",
                      { id: `${provider.provider_id}-add-model`, value: addModelId, disabled: isAddingModel || Boolean(verifyingModelKey), onChange: (event) => selectAddModel(event.target.value, provider) },
                      candidateModels.map((model) => React.createElement("option", { key: model.model_id, value: model.model_id }, model.model_id))
                    )
                  ),
                  React.createElement(
                    "label",
                    { className: "model-type-selector", htmlFor: `${provider.provider_id}-add-model-type` },
                    React.createElement("span", { className: "form-label" }, "类型"),
                    React.createElement(
                      "select",
                      { id: `${provider.provider_id}-add-model-type`, value: addModelType, required: true, disabled: isAddingModel || Boolean(verifyingModelKey), onChange: (event) => setAddModelType(event.target.value) },
                      React.createElement("option", { value: "" }, "选择类型"),
                      React.createElement("option", { value: "chat" }, "对话/文本生成"),
                      React.createElement("option", { value: "embedding" }, "Embedding"),
                      React.createElement("option", { value: "rerank" }, "Rerank（重排）"),
                      React.createElement("option", { value: "markdown" }, "Markdown 结构化")
                    )
                  ),
                  React.createElement(
                    "div",
                    { className: "form-actions" },
                    React.createElement("button", { className: "secondary-button", type: "button", disabled: isAddingModel || Boolean(verifyingModelKey), onClick: () => setAddingProviderId(null) }, "取消"),
                    React.createElement("button", { className: "primary-button", type: "submit", disabled: isAddingModel || Boolean(verifyingModelKey) || !addModelId || !addModelType }, isAddingModel ? "验证中" : "添加并验证")
                  )
                )
            )
          : null
      );
    })
  );
}

function VaultManagement({ activeDestination, vaults, isLoading, selectedVault, onSelect, onOpenForm, onUpdate, onConfirm }) {
  if (isLoading) {
    return React.createElement("p", { className: "empty-state", role: "status" }, "正在加载 vault 授权。");
  }
  if (selectedVault) {
    return React.createElement(VaultDetail, {
      vault: selectedVault,
      onBack: () => onSelect(null),
      onUpdate,
      onRelink: onOpenForm,
      onConfirm
    });
  }
  if (vaults.length === 0) {
    return React.createElement(
      "section",
      { className: "workspace-section vault-empty", "aria-label": `${activeDestination} vault 状态` },
      React.createElement("p", { className: "section-label" }, "本机 vault"),
      React.createElement("p", { className: "empty-state" }, "尚未添加 vault。资料不会离开本机。"),
      React.createElement(
        "button",
        { className: "primary-button", type: "button", onClick: () => onOpenForm(null) },
        "添加 vault"
      )
    );
  }
  return React.createElement(
    "section",
    { className: "vault-list", "aria-label": "已授权 vault" },
    React.createElement(
      "div",
      { className: "list-heading" },
      React.createElement("p", { className: "section-label" }, "已授权 vault"),
      React.createElement("button", { className: "primary-button", type: "button", onClick: () => onOpenForm(null) }, "添加 vault")
    ),
    vaults.map((vault) =>
      React.createElement(
        "button",
        { className: "section-row vault-row", type: "button", key: vault.vault_id, onClick: () => onSelect(vault.vault_id) },
        React.createElement("span", { className: "row-title" }, vaultName(vault)),
        React.createElement("span", { className: "row-meta" }, vault.path),
        React.createElement("span", { className: "row-status" }, statusText(vault))
      )
    )
  );
}

function ImportTaskLauncher({ vault, onCreated }) {
  const [status, setStatus] = React.useState("");
  const [isSelecting, setIsSelecting] = React.useState(false);
  const [onlineParseEnabled, setOnlineParseEnabled] = React.useState(loadOnlineParseEnabled);
  const [onlineParseProviders, setOnlineParseProviders] = React.useState([]);
  const [onlineParseProviderId, setOnlineParseProviderId] = React.useState(loadOnlineParseProviderId);
  const [markdownPipeline, setMarkdownPipeline] = React.useState(loadMarkdownPipeline);
  const [onlineParseLoadError, setOnlineParseLoadError] = React.useState("");
  const uploadInputRef = React.useRef(null);
  const uploadDirectoryInputRef = React.useRef(null);
  const canImport = vault && vault.authorization_status === "active" && vault.access_status === "available";
  const verifiedOnlineParseProviders = onlineParseProviders.filter((provider) => provider.verified);
  const onlineParseActive = onlineParseEnabled
    && verifiedOnlineParseProviders.some((provider) => provider.provider_id === onlineParseProviderId);

  React.useEffect(() => {
    saveOnlineParseProviderId(onlineParseProviderId);
  }, [onlineParseProviderId]);

  React.useEffect(() => {
    saveMarkdownPipeline(markdownPipeline);
  }, [markdownPipeline]);

  React.useEffect(() => {
    let active = true;
    requestJson(ONLINE_PARSE_PROVIDERS_ENDPOINT)
      .then((response) => {
        if (!active) return;
        const providers = response.providers || [];
        setOnlineParseProviders(providers);
        setOnlineParseProviderId((current) => (
          providers.some((provider) => provider.verified && provider.provider_id === current) ? current : ""
        ));
      })
      .catch((error) => { if (active) setOnlineParseLoadError(error.message); });
    return () => { active = false; };
  }, []);

  async function createFromSelection(selection) {
    if (!selection.selection_id) {
      setStatus("未选择资料，未创建导入任务。");
      return;
    }
    const created = await requestJson(IMPORT_TASKS_ENDPOINT, {
      method: "POST",
      body: JSON.stringify({
        vault_id: vault.vault_id,
        selection_id: selection.selection_id,
        online_parse_enabled: onlineParseActive,
        online_parse_provider_id: onlineParseActive ? onlineParseProviderId : null,
        markdown_pipeline: markdownPipeline
      })
    });
    const tasks = created.tasks || (created.task ? [created.task] : []);
    if (tasks.length === 0) {
      setStatus("未创建导入任务。");
      return;
    }
    setStatus(tasks.length === 1 ? `已创建导入任务：${selection.label}。` : `已创建 ${tasks.length} 个导入任务。`);
    tasks.forEach(onCreated);
  }

  async function uploadAndCreate(event, kind = "files") {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    if (!canImport || files.length === 0) return;
    if (onlineParseActive && (kind !== "files" || files.some((file) => !file.name.toLowerCase().endsWith(".pdf")))) {
      setStatus("在线解析仅支持 PDF 文件；请关闭在线解析后导入其他格式或文件夹。");
      return;
    }
    setStatus(kind === "directory" ? "正在上传文件夹…" : `正在上传 ${files.length} 个文件…`);
    setIsSelecting(true);
    try {
      const formData = new globalThis.FormData();
      formData.append("kind", kind);
      files.forEach((file) => formData.append(
        "files", file, kind === "directory" ? file.webkitRelativePath || file.name : file.name
      ));
      const selection = await requestJson(IMPORT_UPLOAD_ENDPOINT, {
        method: "POST",
        body: formData
      });
      await createFromSelection(selection);
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsSelecting(false);
    }
  }

  return React.createElement(
    "section",
    { className: "workspace-section import-launcher", "aria-label": "创建导入任务" },
    React.createElement("p", { className: "section-label" }, "导入资料"),
    canImport
      ? React.createElement(
          React.Fragment,
          null,
          React.createElement("input", {
            ref: uploadInputRef,
            className: "visually-hidden",
            type: "file",
            multiple: true,
            "aria-label": "上传本机资料文件",
            onChange: uploadAndCreate
          }),
          React.createElement("input", {
            ref: uploadDirectoryInputRef,
            className: "visually-hidden",
            type: "file",
            multiple: true,
            webkitdirectory: "",
            "aria-label": "上传本机资料文件夹",
            onChange: (event) => uploadAndCreate(event, "directory")
          }),
          React.createElement(
            "div",
            { className: "detail-actions" },
            React.createElement(
              "button",
              {
                className: "primary-button",
                type: "button",
                disabled: isSelecting,
                onClick: () => uploadInputRef.current?.click()
              },
              "上传文件"
            ),
            React.createElement(
              "button",
              {
                className: "primary-button",
                type: "button",
                disabled: isSelecting,
                onClick: () => uploadDirectoryInputRef.current?.click()
              },
              "上传文件夹"
            ),
          )
          , React.createElement(
            "div",
            { className: "online-parse-control" },
            React.createElement(
              "div",
              { className: "online-parse-switch-row" },
              React.createElement("span", { className: "form-label", id: "online-parse-label" }, "在线解析"),
              React.createElement(
                "button",
                {
                  className: "form-switch",
                  type: "button",
                  role: "switch",
                  "aria-checked": onlineParseEnabled,
                  "aria-labelledby": "online-parse-label",
                  disabled: verifiedOnlineParseProviders.length === 0 || isSelecting,
                  onClick: () => setOnlineParseEnabled((current) => {
                    const next = !current;
                    saveOnlineParseEnabled(next);
                    return next;
                  })
                },
                React.createElement("span", { className: "form-switch-thumb", "aria-hidden": "true" })
              )
            ),
            onlineParseEnabled ? React.createElement(
              React.Fragment,
              null,
              React.createElement(
                "label",
                { className: "form-row", htmlFor: "online-parse-provider" },
                React.createElement("span", { className: "form-label" }, "解析 Provider"),
                React.createElement(
                  "select",
                  {
                    id: "online-parse-provider",
                    value: onlineParseProviderId,
                    disabled: isSelecting,
                    onChange: (event) => setOnlineParseProviderId(event.target.value)
                  },
                  React.createElement(
                    "option",
                    { value: "", disabled: true },
                    "请选择在线解析 Provider"
                  ),
                  verifiedOnlineParseProviders.map((provider) => React.createElement(
                    "option", { key: provider.provider_id, value: provider.provider_id }, `${provider.name} / ${provider.model}`
                  ))
                )
              ),
              onlineParseActive
                ? React.createElement("p", { className: "form-help" }, "将把所选 PDF 原件与文件名发送至所选 Provider。")
                : null
            ) : null,
            React.createElement(
              "div",
              { className: "markdown-pipeline-control", role: "group", "aria-labelledby": "markdown-pipeline-label" },
              React.createElement("span", { className: "form-label", id: "markdown-pipeline-label" }, "Markdown 结构化"),
              React.createElement(
                "div",
                { className: "segmented-control", role: "radiogroup", "aria-label": "PDF Markdown 结构化方式" },
                [
                  ["ai", "AI 结构化"],
                  ["local", "本地结构化"]
                ].map(([pipeline, label]) => React.createElement(
                  "button",
                  {
                    className: `segmented-control-option${markdownPipeline === pipeline ? " is-selected" : ""}`,
                    type: "button",
                    key: pipeline,
                    role: "radio",
                    "aria-checked": markdownPipeline === pipeline,
                    disabled: isSelecting,
                    onClick: () => setMarkdownPipeline(pipeline)
                  },
                  label
                ))
              ),
              markdownPipeline === "ai"
                ? React.createElement("p", { className: "form-help" }, "AI 结构化会将选定 DocumentGraph 的 Markdown 发送至已配置的 Markdown Provider。")
                : React.createElement("p", { className: "form-help" }, "本地结构化不调用 Markdown Provider。")
            ),
            verifiedOnlineParseProviders.length === 0
              ? React.createElement("p", { className: "form-help" }, onlineParseLoadError || "请先在设置中保存凭据并完成在线解析 Provider 的连接测试。")
              : null
          )
        )
      : React.createElement("p", { className: "empty-state" }, "请先授权并设为当前可用 vault，才能导入资料。"),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null
  );
}

function projectionSummaryText(projection) {
  const typeCounts = Object.entries(projection.locator_summary?.type_counts || {})
    .map(([type, count]) => `${{
      "pdf-region": "PDF 内容",
      "docx-ooxml": "DOCX 内容",
      "source-scope": "来源范围"
    }[type] || "其他内容"} ${count}`)
    .join("；");
  const pdfPages = projection.locator_summary?.pdf_pages || [];
  const docxPartCount = projection.locator_summary?.docx_part_count || 0;
  return `投影块 ${projection.block_count}；可检索块 ${projection.retrievable_block_count}；内容类型 ${typeCounts || "无"}；PDF 页 ${pdfPages.length ? pdfPages.join("、") : "无"}；DOCX 内容 ${docxPartCount} 处。`;
}

export function ProjectionRebuildVerificationPanel({ task, conversionGraphs, onTaskDeleted }) {
  const graphOptions = conversionGraphs.filter((graph) => (
    typeof graph.graph_id === "string" && Number.isInteger(graph.graph_revision)
  ));
  const [selectedGraphIndex, setSelectedGraphIndex] = React.useState(0);
  const [beforeProjection, setBeforeProjection] = React.useState(null);
  const [afterProjection, setAfterProjection] = React.useState(null);
  const [rebuildIndex, setRebuildIndex] = React.useState(null);
  const [confirmed, setConfirmed] = React.useState(false);
  const [status, setStatus] = React.useState("");
  const [isActing, setIsActing] = React.useState(false);
  const selectedGraph = graphOptions[selectedGraphIndex] || graphOptions[0];

  function summaryEndpoint(graph) {
    return `${VAULTS_ENDPOINT}/${encodeURIComponent(task.vault_id)}/graph-projections/${encodeURIComponent(graph.graph_id)}/${graph.graph_revision}`;
  }

  async function inspectProjection() {
    if (!selectedGraph || isActing) return;
    setStatus("");
    setIsActing(true);
    try {
      const response = await requestJson(summaryEndpoint(selectedGraph));
      setBeforeProjection(response.projection);
      setAfterProjection(null);
      setRebuildIndex(null);
      setConfirmed(false);
      setStatus("已读取删除前的耐久投影摘要。");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  async function verifyRebuild() {
    if (!selectedGraph || !beforeProjection || !confirmed || isActing) return;
    setStatus("");
    setIsActing(true);
    try {
      await onTaskDeleted(task.task_id, { keepSelected: true });
      const rebuilt = await requestJson(`${VAULTS_ENDPOINT}/${encodeURIComponent(task.vault_id)}/index/rebuild`, {
        method: "POST"
      });
      const response = await requestJson(summaryEndpoint(selectedGraph));
      setAfterProjection(response.projection);
      setRebuildIndex(rebuilt.index);
      const locatorMatches = response.projection.locator_digest === beforeProjection.locator_digest;
      const rebuildSucceeded = rebuilt.index?.status === "healthy";
      setStatus(
        locatorMatches && rebuildSucceeded
          ? "验证通过：任务已删除，索引重建成功，投影结构摘要保持一致。"
          : `验证未通过：索引状态 ${rebuilt.index?.status || "未知"}；结构摘要${locatorMatches ? "一致" : "不一致"}。`
      );
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  return React.createElement(
    "section",
    { className: "projection-rebuild-verification", "aria-label": "投影重建验证" },
    React.createElement("h3", null, "投影重建验证"),
    React.createElement("p", { className: "row-note" }, "只读取耐久投影的结构摘要和计数；不会显示正文或转换工件。"),
    graphOptions.length > 1
      ? React.createElement(
        "label",
        { className: "form-label" },
        "转换图谱",
        React.createElement(
          "select",
          {
            value: String(selectedGraphIndex),
            disabled: isActing || Boolean(afterProjection),
            onChange: (event) => setSelectedGraphIndex(Number(event.target.value)),
            "aria-label": "选择待验证的转换图谱"
          },
          graphOptions.map((graph, index) => React.createElement(
            "option",
            { key: `${graph.graph_id}:${graph.graph_revision}`, value: String(index) },
            `图谱 ${index + 1}`
          ))
        )
      )
      : null,
    React.createElement(
      "div",
      { className: "detail-actions" },
      React.createElement("button", {
        className: "secondary-button",
        type: "button",
        disabled: isActing || Boolean(afterProjection),
        onClick: inspectProjection
      }, beforeProjection ? "重新读取投影摘要" : "读取投影摘要"),
      beforeProjection && !afterProjection
        ? React.createElement(
          "label",
          { className: "projection-confirmation" },
          React.createElement("input", {
            type: "checkbox",
            checked: confirmed,
            disabled: isActing,
            onChange: (event) => setConfirmed(event.target.checked)
          }),
          "我确认删除此导入任务并执行索引重建验证"
        )
        : null,
      beforeProjection && !afterProjection
        ? React.createElement("button", {
          className: "primary-button",
          type: "button",
          disabled: isActing || !confirmed,
          onClick: verifyRebuild
        }, isActing ? "正在验证" : "删除并重建验证")
        : null
    ),
    beforeProjection
      ? React.createElement("p", { className: "row-note", "data-testid": "projection-before-summary" }, `删除前：${projectionSummaryText(beforeProjection)}`)
      : null,
    afterProjection
      ? React.createElement("p", { className: "row-note", "data-testid": "projection-after-summary" }, `重建后：${projectionSummaryText(afterProjection)}`)
      : null,
    rebuildIndex
      ? React.createElement("p", { className: "row-note" }, `重建索引状态：${rebuildIndex.status}。`)
      : null,
    status
      ? React.createElement("p", { className: `status-line${afterProjection && status.startsWith("验证未通过") ? " status-danger" : ""}`, role: "status" }, status)
      : null
  );
}

export function LegacyReviewImportTaskDetail({ taskId, onBack, onTaskChanged, onTaskDeleted, onTaskSnapshot }) {
  const [detail, setDetail] = React.useState(null);
  const [status, setStatus] = React.useState("");
  const [isActing, setIsActing] = React.useState(false);
  const [ocrDrafts, setOcrDrafts] = React.useState({});
  const [classificationDrafts, setClassificationDrafts] = React.useState({});
  const [reviewItemDrafts, setReviewItemDrafts] = React.useState({});
  const [conversionDrafts, setConversionDrafts] = React.useState({});
  const [splitSelections, setSplitSelections] = React.useState({});
  const [selectedCommitUnits, setSelectedCommitUnits] = React.useState({});
  const [commitFilter, setCommitFilter] = React.useState("all");
  const refreshTimerRef = React.useRef(null);

  const loadDetail = React.useCallback(async () => {
    try {
      const response = await requestJson(`${IMPORT_TASKS_ENDPOINT}/${taskId}`);
      setDetail(response);
      onTaskSnapshot(response.task);
      setStatus("");
      return response;
    } catch (error) {
      setStatus(error.message);
      return null;
    }
  }, [onTaskSnapshot, taskId]);

  const scheduleDetailRefresh = React.useCallback(() => {
    if (refreshTimerRef.current !== null) return;
    refreshTimerRef.current = window.setTimeout(() => {
      refreshTimerRef.current = null;
      void loadDetail();
    }, 250);
  }, [loadDetail]);

  React.useEffect(() => {
    let eventSource;
    let disposed = false;
    loadDetail().then((loaded) => {
      if (!loaded || disposed) return;
      eventSource = new window.EventSource(
        `${IMPORT_TASKS_ENDPOINT}/${taskId}/events?after=${loaded.event_cursor || 0}`
      );
      for (const eventName of IMPORT_TASK_EVENT_NAMES) {
        eventSource.addEventListener(eventName, scheduleDetailRefresh);
      }
    });
    return () => {
      disposed = true;
      eventSource?.close();
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
    };
  }, [loadDetail, scheduleDetailRefresh, taskId]);

  async function runAction(action) {
    if (isActing) return;
    setStatus("");
    setIsActing(true);
    try {
      const response = await requestJson(`${IMPORT_TASKS_ENDPOINT}/${taskId}/${action}`, { method: "POST" });
      onTaskChanged(response.task);
      if (response.task.task_id !== taskId) {
        onBack(response.task.task_id);
        return;
      }
      await loadDetail();
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  function updateOcrDraft(itemId, targetId, field, value) {
    const key = `${itemId}:${targetId}`;
    setOcrDrafts((current) => ({
      ...current,
      [key]: { ...(current[key] || {}), [field]: value }
    }));
  }

  function updateClassificationDraft(itemId, field, value) {
    setClassificationDrafts((current) => ({
      ...current,
      [itemId]: { ...(current[itemId] || {}), [field]: value }
    }));
  }

  function updateReviewItemDraft(reviewItemId, value) {
    setReviewItemDrafts((current) => ({ ...current, [reviewItemId]: value }));
  }

  function updateConversionDraft(reviewItemId, field, value) {
    setConversionDrafts((current) => ({
      ...current,
      [reviewItemId]: { ...(current[reviewItemId] || {}), [field]: value }
    }));
  }

  function updateSplitSelection(itemId, sequence, value) {
    setSplitSelections((current) => ({
      ...current,
      [`${itemId}:${sequence}`]: Number(value)
    }));
  }

  async function runOcrAction(itemId, targetId, action) {
    if (isActing) return;
    const draft = ocrDrafts[`${itemId}:${targetId}`] || {};
    if (action !== "retry" && !draft.reason?.trim()) {
      setStatus("请说明本次 OCR 决定的理由。");
      return;
    }
    if (action === "correct" && !draft.corrected_text?.trim()) {
      setStatus("请提供修正后的文本。");
      return;
    }
    setStatus("");
    setIsActing(true);
    try {
      const response = await requestJson(
        `${IMPORT_TASKS_ENDPOINT}/${taskId}/items/${itemId}/ocr/${encodeURIComponent(targetId)}/${action}`,
        {
          method: "POST",
          body: action === "retry" ? undefined : JSON.stringify({
            reason: draft.reason,
            corrected_text: action === "correct" ? draft.corrected_text : undefined
          })
        }
      );
      onTaskChanged(response.task);
      await loadDetail();
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  async function runNoteProposalAction(action, payload) {
    if (isActing) return;
    setStatus("");
    setIsActing(true);
    try {
      const response = await requestJson(`${IMPORT_TASKS_ENDPOINT}/${taskId}/note-proposals/${action}`, {
        method: "POST",
        body: JSON.stringify(payload)
      });
      onTaskChanged(response.task);
      await loadDetail();
      setStatus(action === "merge" ? "笔记提案已合并。" : "笔记提案已按所选边界拆分。");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  async function runClassificationAction(suggestion, action) {
    if (isActing) return;
    const draft = classificationDrafts[suggestion.item_id] || {};
    const reason = draft.reason?.trim();
    if ((action === "revise" || action === "excluded") && !reason) {
      setStatus("请说明本次分类决定的理由。");
      return;
    }
    setStatus("");
    setIsActing(true);
    try {
      const endpoint = action === "revise"
        ? `${IMPORT_TASKS_ENDPOINT}/${taskId}/classifications/${suggestion.item_id}/revise`
        : `${IMPORT_TASKS_ENDPOINT}/${taskId}/classifications/${suggestion.item_id}/decision`;
      const body = action === "revise"
        ? {
            domain: draft.domain ?? suggestion.domain,
            target_folder: draft.target_folder ?? suggestion.target_folder,
            filename: draft.filename ?? suggestion.filename,
            reason
          }
        : {
            decision: action,
            reason: reason || "Accepted from the import task detail."
          };
      const response = await requestJson(endpoint, { method: "POST", body: JSON.stringify(body) });
      onTaskChanged(response.task);
      await loadDetail();
      setStatus(
        action === "revise"
          ? "分类建议已修正。"
          : action === "accepted"
            ? "分类建议已接受。"
            : "分类建议已排除。"
      );
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  async function acceptHighConfidenceClassifications() {
    if (isActing) return;
    setStatus("");
    setIsActing(true);
    try {
      const response = await requestJson(
        `${IMPORT_TASKS_ENDPOINT}/${taskId}/classifications/accept-high-confidence`,
        { method: "POST", body: JSON.stringify({ reason: "Accepted from the import task detail." }) }
      );
      onTaskChanged(response.task);
      await loadDetail();
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  async function refreshReviewSnapshot() {
    if (isActing) return;
    setStatus("");
    setIsActing(true);
    try {
      await requestJson(`${IMPORT_TASKS_ENDPOINT}/${taskId}/review-snapshot`, { method: "POST" });
      await loadDetail();
      setStatus("审核快照已刷新。");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  async function runReviewItemAction(reviewItem, decision) {
    if (isActing) return;
    const reason = reviewItemDrafts[reviewItem.review_item_id]?.trim();
    if (!reason) {
      setStatus("请说明本次审核决定的理由。");
      return;
    }
    setStatus("");
    setIsActing(true);
    try {
      const response = await requestJson(
        `${IMPORT_TASKS_ENDPOINT}/${taskId}/review-items/${encodeURIComponent(reviewItem.review_item_id)}/decision`,
        { method: "POST", body: JSON.stringify({ decision, reason }) }
      );
      onTaskChanged(response.task);
      await loadDetail();
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  async function retryConversionReviewItem(reviewItem) {
    if (isActing) return;
    const itemId = conversionItemIdFromReviewItem(reviewItem.review_item_id);
    if (itemId === null) {
      setStatus("该转换审核项没有可重试的资料项。");
      return;
    }
    setStatus("");
    setIsActing(true);
    try {
      const response = await requestJson(
        `${IMPORT_TASKS_ENDPOINT}/${taskId}/conversion-items/${itemId}/retry`,
        { method: "POST" }
      );
      onTaskChanged(response.task);
      await loadDetail();
      setStatus("已提交转换重试。");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  async function correctConversionReviewBlock(reviewItem) {
    if (isActing) return;
    const itemId = conversionItemIdFromReviewItem(reviewItem.review_item_id);
    const correction = conversionCorrectionDraft(conversionDrafts[reviewItem.review_item_id] || {});
    if (itemId === null) {
      setStatus("该转换审核项没有可修正的资料项。");
      return;
    }
    if (correction.error) {
      setStatus(correction.error);
      return;
    }
    setStatus("");
    setIsActing(true);
    try {
      const response = await requestJson(
        `${IMPORT_TASKS_ENDPOINT}/${taskId}/conversion-items/${itemId}/blocks/${encodeURIComponent(correction.blockId)}/correct`,
        {
          method: "POST",
          body: JSON.stringify({
            kind: correction.kind,
            payload: correction.payload,
            retrieval_projection: correction.retrievalProjection,
            reason: correction.reason
          })
        }
      );
      onTaskChanged(response.task);
      await loadDetail();
      setStatus("结构修正已保存并重新生成提案。");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  async function runCommitReview(unitIds) {
    if (isActing || unitIds.length === 0) return;
    setStatus("");
    setIsActing(true);
    try {
      const response = await requestJson(`${IMPORT_TASKS_ENDPOINT}/${taskId}/commit`, {
        method: "POST",
        body: JSON.stringify({ unit_ids: unitIds })
      });
      onTaskChanged(response.task);
      await loadDetail();
      setStatus("提交结果已记录。");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  if (!detail) {
    return React.createElement(
      "section",
      { className: "workspace-section", "aria-label": "导入任务详情" },
      React.createElement("button", { className: "back-button", type: "button", onClick: () => onBack(null) }, "返回任务列表"),
      React.createElement("p", { className: "empty-state", role: "status" }, status || "正在读取任务快照。")
    );
  }

  const {
    task,
    items,
    note_proposals: noteProposals = [],
    classification_suggestions: classifications = [],
    conversion_graphs: conversionGraphs = [],
    review_snapshot: reviewSnapshot = null,
    commit_journals: commitJournals = [],
    index = null
  } = detail;
  const canCancel = task.lifecycle === "running";
  const canResume = task.recovery_actions.includes("restart-scan") || task.recovery_actions.includes("restart-conversion") || task.recovery_actions.includes("restart-parse") || task.recovery_actions.includes("restart-ocr") || task.recovery_actions.includes("restart-derivation") || task.recovery_actions.includes("retry-commit") || task.recovery_actions.includes("create-new-task");
  const canStartConversion = task.lifecycle === "queued" && task.phase === "waiting-for-next-stage";
  const canAdjustNoteProposals = task.lifecycle === "waiting-for-review" && !isActing;
  const canManageReviewItems = task.lifecycle === "waiting-for-review" && !isActing;
  const reviewItemControlReason = isActing
    ? "正在更新审核状态。"
    : task.lifecycle !== "waiting-for-review"
      ? "审核决定只能在等待审核时处理。"
      : "";
  const noteProposalActionReason = isActing
    ? "正在更新提案。"
    : task.lifecycle !== "waiting-for-review"
      ? "笔记边界只能在等待审核时调整。"
      : "";
  const canManageClassifications = task.lifecycle === "waiting-for-review" && !isActing;
  const hasHighConfidenceSuggestion = classifications.some(
    (suggestion) => !suggestion.decision && suggestion.status !== "required-check"
  );
  const classificationControlReason = isActing
    ? "正在更新分类建议。"
    : task.lifecycle !== "waiting-for-review"
      ? "分类建议只能在等待审核时处理。"
      : "";
  const classificationBatchReason = classificationControlReason || (
    !hasHighConfidenceSuggestion ? "没有可批量接受的高置信度建议。" : ""
  );
  const reviewItemsByUnit = new Map();
  for (const reviewItem of reviewSnapshot?.review_items || []) {
    const current = reviewItemsByUnit.get(reviewItem.unit_id) || [];
    current.push(reviewItem);
    reviewItemsByUnit.set(reviewItem.unit_id, current);
  }
  const conversionBlocksByItem = new Map(
    conversionGraphs.map((graph) => [graph.item_id, graph.blocks || []])
  );
  const canVerifyProjectionRebuild = ["complete", "completed-with-confirmed-gaps"].includes(task.lifecycle)
    && conversionGraphs.some((graph) => typeof graph.graph_id === "string" && Number.isInteger(graph.graph_revision));
  const unitRisk = (unit) => {
    const itemsForUnit = reviewItemsByUnit.get(unit.unit_id) || [];
    if (itemsForUnit.some((item) => item.risk === "blocking")) return "blocking";
    if (itemsForUnit.some((item) => item.risk === "required-check" && !["accepted", "revised", "excluded"].includes(item.status))) return "required-check";
    return "ordinary";
  };
  const filteredCommitUnits = (reviewSnapshot?.units || []).filter((unit) => (
    commitFilter === "all" || unitRisk(unit) === commitFilter || unit.kind === commitFilter
  ));
  const eligibleCommitUnits = (reviewSnapshot?.units || []).filter((unit) => !unit.eligibility_reason);
  const selectedCommitUnitIds = eligibleCommitUnits
    .filter((unit) => selectedCommitUnits[unit.unit_id])
    .map((unit) => unit.unit_id);
  const commitControlReason = !reviewSnapshot
    ? "正在等待审核快照。"
    : reviewSnapshot.stale_reasons?.length
      ? reviewSnapshot.stale_reasons.join("；")
      : isActing
        ? "正在更新审核或提交状态。"
        : selectedCommitUnitIds.length === 0
          ? reviewSnapshot.remaining_review_count
            ? `仍有 ${reviewSnapshot.remaining_review_count} 个阻断或必须检查项。`
            : "请先选择可提交单元。"
          : "";
  return React.createElement(
    "section",
    { className: "import-task-detail", "aria-label": "导入任务详情" },
    React.createElement("button", { className: "back-button", type: "button", onClick: () => onBack(null) }, "返回任务列表"),
    React.createElement("h2", null, "导入任务详情"),
    React.createElement("p", { className: "scope-summary" }, `目标 vault：${task.vault_label}；范围：${task.scope_label}`),
    task.markdown_pipeline
      ? React.createElement("p", { className: "row-note" }, `结构化：${task.markdown_pipeline === "ai" ? "AI 结构化" : "本地结构化"}`)
      : null,
    task.online_parse?.enabled
      ? React.createElement("p", { className: "row-note" }, `在线解析：${task.online_parse.provider_name} / ${task.online_parse.model}`)
      : null,
    index
      ? React.createElement(
        "p",
        { className: "row-note", role: "status" },
        `索引：${index.status}；已索引 ${index.current_count} 项；失效 ${index.stale_count} 项；失败 ${index.failure_count} 项。`
      )
      : null,
    React.createElement(
      "div",
      { className: "progress-sequence", "aria-live": "polite" },
      React.createElement("span", { className: "status-marker" }, `状态：${importLifecycleText(task.lifecycle)}`),
      React.createElement("span", null, `当前阶段：${importPhaseText(task.phase)}`),
      ...IMPORT_PROGRESS_PHASES.map((phase) => React.createElement(
        "span",
        { key: phase },
        `${importPhaseText(phase)}：${progressPhaseStatus(task, phase)}`
      )),
      React.createElement("span", null, `已发现 ${task.counts.discovered}`),
      React.createElement("span", null, `已支持 ${task.counts.supported}`),
      React.createElement("span", null, `跳过 ${task.counts.skipped}`),
      React.createElement("span", null, `不支持 ${task.counts.unsupported}`),
      React.createElement("span", null, `失败 ${task.counts.failed}`),
      React.createElement("span", null, `新资料 ${task.counts.new || 0}`),
      React.createElement("span", null, `重复资料 ${task.counts.duplicate || 0}`),
      React.createElement("span", null, `可能版本 ${task.counts.possible_version || 0}`),
      React.createElement("span", null, `识别失败 ${task.counts.identity_failed || 0}`),
      React.createElement("span", null, `已解析 ${task.counts.parsed || 0}`),
      React.createElement("span", null, `解析失败 ${task.counts.parse_failed || 0}`),
      React.createElement("span", null, `OCR 完成 ${task.counts.ocr_completed || 0}`),
      React.createElement("span", null, `OCR 失败 ${task.counts.ocr_failed || 0}`),
      React.createElement("span", null, `已确认缺口 ${task.counts.confirmed_gaps || 0}`),
      React.createElement("span", null, `已生成笔记 ${task.counts.derived_notes || 0}`),
      React.createElement("span", null, `待审核问题 ${task.counts.required_check || 0}`)
    ),
    task.current_item_label ? React.createElement("p", { className: "status-line" }, `当前文件：${task.current_item_label}`) : null,
    task.failure_reason ? React.createElement("p", { className: "status-line status-danger" }, `失败原因：${task.failure_reason}`) : null,
    React.createElement(
      "div",
      { className: "detail-actions" },
      canCancel
        ? React.createElement("button", { className: "secondary-button", type: "button", disabled: isActing, onClick: () => runAction("cancel") }, "取消")
        : null,
      canResume
        ? React.createElement(
            "button",
            { className: "primary-button", type: "button", disabled: isActing, onClick: () => runAction("resume") },
            task.lifecycle === "cancelled" ? "创建新任务" : task.recovery_actions.includes("restart-conversion") ? "重新转换" : task.recovery_actions.includes("restart-parse") ? "重新解析" : task.recovery_actions.includes("restart-ocr") ? "重新 OCR" : task.recovery_actions.includes("restart-derivation") ? "重新生成笔记" : task.recovery_actions.includes("retry-commit") ? "重试提交" : "重新扫描"
          )
        : null
      , canStartConversion
        ? React.createElement("button", { className: "primary-button", type: "button", disabled: isActing, onClick: () => runAction("convert") }, "开始保真转换")
        : null
    ),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null,
    canVerifyProjectionRebuild
      ? React.createElement(ProjectionRebuildVerificationPanel, {
        task,
        conversionGraphs,
        onTaskDeleted
      })
      : null,
    React.createElement(
      "section",
      { className: "commit-review-list", "aria-label": "提交审核", "aria-live": "polite" },
      React.createElement("h3", null, "提交审核"),
      reviewSnapshot
        ? React.createElement(
            React.Fragment,
            null,
            React.createElement(
              "p",
              { className: "scope-summary" },
              `已固定提交审核范围；目标 vault：${task.vault_label}；来源 ${reviewSnapshot.source_hashes.length}；受影响既有文件 ${reviewSnapshot.existing_file_hashes.length}`
            ),
            React.createElement(
              "div",
              { className: "review-summary" },
              React.createElement("span", { className: "status-marker" }, `剩余审核 ${reviewSnapshot.remaining_review_count}`),
              React.createElement("span", null, `新增 ${reviewSnapshot.units.filter((unit) => unit.kind === "source").length}`),
              React.createElement("span", null, `既有笔记变更 ${reviewSnapshot.units.filter((unit) => unit.kind === "existing-note").length}`),
              React.createElement("span", null, `异常 ${reviewSnapshot.units.filter((unit) => unit.kind === "unresolved").length}`),
              React.createElement("span", null, `跳过 ${reviewSnapshot.units.filter((unit) => unit.kind === "skipped").length}`),
              React.createElement("span", null, `确认缺口 ${reviewSnapshot.units.filter((unit) => unit.confirmed_gaps).length}`),
              React.createElement("span", null, `已提交 ${commitJournals.filter((journal) => journal.status === "committed").length}`),
              React.createElement("span", null, `失败 ${commitJournals.filter((journal) => journal.status === "failed").length}`)
            ),
            reviewSnapshot.stale_reasons?.length
              ? React.createElement("p", { className: "status-line status-danger", role: "status" }, `陈旧原因：${reviewSnapshot.stale_reasons.join("；")}`)
              : null,
            React.createElement(
              "div",
              { className: "detail-actions" },
              React.createElement("select", {
                value: commitFilter,
                onChange: (event) => setCommitFilter(event.target.value),
                "aria-label": "提交单元筛选"
              },
              React.createElement("option", { value: "all" }, "全部单元"),
              React.createElement("option", { value: "ordinary" }, "普通项"),
              React.createElement("option", { value: "required-check" }, "必须检查"),
              React.createElement("option", { value: "blocking" }, "阻断项"),
              React.createElement("option", { value: "source" }, "新资料"),
              React.createElement("option", { value: "existing-note" }, "既有笔记"),
              React.createElement("option", { value: "unresolved" }, "异常/未处理"),
              React.createElement("option", { value: "skipped" }, "跳过项")),
              React.createElement("button", {
                className: "secondary-button",
                type: "button",
                disabled: isActing || eligibleCommitUnits.length === 0,
                title: eligibleCommitUnits.length ? "选择所有当前可提交单元。" : "没有可提交单元。",
                onClick: () => setSelectedCommitUnits(Object.fromEntries(eligibleCommitUnits.map((unit) => [unit.unit_id, true])))
              }, "全选可提交"),
              React.createElement("button", {
                className: "secondary-button",
                type: "button",
                disabled: isActing,
                onClick: refreshReviewSnapshot
              }, "刷新快照"),
              React.createElement("button", {
                className: "primary-button",
                type: "button",
                disabled: Boolean(commitControlReason),
                title: commitControlReason || "提交已选择的原子单元。",
                "aria-describedby": commitControlReason ? "commit-control-reason" : undefined,
                onClick: () => runCommitReview(selectedCommitUnitIds)
              }, "提交所选")
            ),
            commitControlReason
              ? React.createElement("p", { id: "commit-control-reason", className: "status-line", role: "status" }, commitControlReason)
              : null,
            filteredCommitUnits.map((unit) => {
              const journal = [...commitJournals].reverse().find((item) => item.unit_id === unit.unit_id);
              const reason = unit.eligibility_reason;
              const unitStatus = journal?.status === "committed"
                ? "已提交"
                : journal?.status === "failed"
                  ? "失败，可重试"
                  : reason
                    ? `不可提交：${reason}`
                    : unit.kind === "unresolved"
                      ? "异常，需处理"
                    : unit.kind === "skipped"
                      ? "已跳过"
                    : unit.confirmed_gaps
                      ? "带已确认缺口完成"
                      : "可提交";
              return React.createElement(
                "div",
                { className: "section-row review-diff-row commit-unit-row", key: unit.unit_id },
                React.createElement("input", {
                  type: "checkbox",
                  checked: Boolean(selectedCommitUnits[unit.unit_id]),
                  disabled: Boolean(reason) || journal?.status === "committed" || isActing,
                  onChange: (event) => setSelectedCommitUnits((current) => ({ ...current, [unit.unit_id]: event.target.checked })),
                  "aria-label": `选择提交单元 ${unit.source_label}`
                }),
                React.createElement("span", { className: "row-title" }, unit.source_label),
                React.createElement("span", { className: "row-meta" }, unit.kind === "existing-note" ? "既有笔记独立单元" : unit.kind === "unresolved" ? "异常资料" : unit.kind === "skipped" ? "跳过资料" : "源文件原子单元"),
                React.createElement("span", { className: `row-status${reason ? " status-danger" : ""}` }, unitStatus),
                unit.confirmed_gaps ? React.createElement("span", { className: "row-note" }, "带已确认缺口完成") : null,
                journal?.reason ? React.createElement("span", { className: "row-note" }, `恢复原因：${journal.reason}`) : null,
                ...unit.files.map((file) => React.createElement(
                  "span",
                  { className: "row-note", key: `${unit.unit_id}:${file.relative_path}` },
                  `${file.kind === "source" ? "来源" : file.modifies_existing ? "修改" : "新增"}：${file.relative_path}`
                )),
                ...(reviewItemsByUnit.get(unit.unit_id) || [])
                  .filter((item) => (
                    ["parse", "existing-note"].includes(item.object_type) && item.risk === "required-check"
                  ) || (
                    item.object_type === "conversion" && ["required-check", "blocking"].includes(item.risk)
                  ))
                  .map((item) => React.createElement(
                    "div",
                    { className: "detail-actions", key: item.review_item_id },
                    React.createElement("span", { className: "row-note" }, `${item.object_type === "parse" ? "解析" : item.object_type === "conversion" ? "转换" : "既有笔记"}：${item.reason}`),
                    item.status === "pending"
                      ? React.createElement(
                          React.Fragment,
                          null,
                          item.object_type !== "conversion" || (
                            item.risk === "required-check" && conversionReviewHasGraphIssue(item.review_item_id)
                          )
                            ? React.createElement("input", {
                                type: "text",
                                value: reviewItemDrafts[item.review_item_id] || "",
                                disabled: isActing,
                                onChange: (event) => updateReviewItemDraft(item.review_item_id, event.target.value),
                                "aria-label": `${unit.source_label} 的审核决定理由`
                              })
                            : null,
                          item.object_type !== "conversion" || (
                            item.risk === "required-check" && conversionReviewHasGraphIssue(item.review_item_id)
                          )
                            ? React.createElement("button", { type: "button", disabled: !canManageReviewItems, title: reviewItemControlReason || undefined, onClick: () => runReviewItemAction(item, "accepted") }, "接受")
                            : null,
                          item.object_type !== "conversion"
                            ? React.createElement("button", { type: "button", disabled: !canManageReviewItems, title: reviewItemControlReason || undefined, onClick: () => runReviewItemAction(item, "revised") }, "确认修正")
                            : null,
                          item.object_type !== "conversion" || (
                            item.risk === "required-check" && conversionReviewHasGraphIssue(item.review_item_id)
                          )
                            ? React.createElement("button", { type: "button", disabled: !canManageReviewItems, title: reviewItemControlReason || undefined, onClick: () => runReviewItemAction(item, "excluded") }, "排除")
                            : null,
                          item.object_type === "conversion"
                            ? React.createElement(ConversionReviewControls, {
                                reviewItem: item,
                                lifecycle: task.lifecycle,
                                isActing,
                                draft: conversionDrafts[item.review_item_id] || {},
                                blocks: conversionBlocksByItem.get(conversionItemIdFromReviewItem(item.review_item_id)) || [],
                                onDraftChange: (field, value) => updateConversionDraft(item.review_item_id, field, value),
                                onRetry: () => retryConversionReviewItem(item),
                                onCorrect: () => correctConversionReviewBlock(item)
                              })
                            : null
                        )
                      : React.createElement("span", { className: "row-status" }, `已${item.status === "accepted" ? "接受" : item.status === "revised" ? "修正" : "排除"}`)
                  ))
              );
            })
          )
        : React.createElement("p", { className: "empty-state" }, "正在生成审核快照。")
    ),
    React.createElement("h3", null, "资料项"),
    items.length === 0
      ? React.createElement("p", { className: "empty-state" }, "尚未发现文件。")
      : React.createElement(
          "div",
          { className: "import-item-list" },
          items.map((item) => React.createElement(
            "div",
            { className: "section-row import-item-row", key: item.item_id },
            React.createElement("span", { className: "row-title" }, item.label),
            React.createElement("span", { className: "row-meta" }, importDocumentKindText(item.document_kind)),
            React.createElement(
              "span",
              { className: "row-status" },
              `${importCategoryText(item.category)} · ${importIdentityStatusText(item.identity_status)}${item.conversion_status && item.conversion_status !== "not-applicable" ? ` · ${importConversionStatusText(item.conversion_status)}` : ""} · ${importParseStatusText(item.parse_status)} · ${importOcrStatusText(item.ocr_status)}`
            ),
            item.parse_confidence !== null && item.parse_confidence !== undefined
              ? React.createElement("span", { className: "row-note" }, `解析置信度：${item.parse_confidence}`)
              : null,
            React.createElement(ImportParserTag, { engine: item.conversion_engine }),
            item.conversion_fallback_reason
              ? React.createElement("span", { className: "row-note" }, item.conversion_fallback_reason)
              : null,
            userFacingImportLocation(item.parse_locator_summary)
              ? React.createElement("span", { className: "row-note" }, `内容位置：${userFacingImportLocation(item.parse_locator_summary)}`)
              : null,
            item.parse_issue_count
              ? React.createElement("span", { className: "row-status status-danger" }, `待审核问题 ${item.parse_issue_count}`)
              : null,
            userFacingImportIssue(item.parse_issue_summary)
              ? React.createElement("span", { className: "row-note" }, userFacingImportIssue(item.parse_issue_summary))
              : null,
            item.ocr_confidence !== null && item.ocr_confidence !== undefined
              ? React.createElement("span", { className: "row-note" }, `OCR 置信度：${item.ocr_confidence}`)
              : null,
            userFacingImportLocation(item.ocr_locator_summary)
              ? React.createElement("span", { className: "row-note" }, `OCR 内容：${userFacingImportLocation(item.ocr_locator_summary)}`)
              : null,
            item.ocr_issue_count
              ? React.createElement("span", { className: "row-status status-danger" }, `OCR 待审核 ${item.ocr_issue_count}`)
              : null,
            userFacingImportIssue(item.ocr_issue_summary)
              ? React.createElement("span", { className: "row-note" }, userFacingImportIssue(item.ocr_issue_summary))
              : null,
            ...(item.ocr_targets || []).map((target) => {
              const draft = ocrDrafts[`${item.item_id}:${target.target_id}`] || {};
              const needsDecision = target.issue_count > 0 && !target.decision;
              return React.createElement(
                "div",
                { className: "ocr-target-actions", key: `${item.item_id}:${target.target_id}` },
                React.createElement("span", { className: "row-title" }, userFacingImportLocation(target.locator_summary) || "待审核内容"),
                React.createElement("span", { className: "row-meta" }, `${importOcrTargetStatusText(target.status)}${target.engine ? ` · ${target.engine}` : ""}`),
                target.confidence !== null && target.confidence !== undefined
                  ? React.createElement("span", { className: "row-note" }, `置信度：${target.confidence}`)
                  : null,
                target.decision
                  ? React.createElement("span", { className: "row-status" }, `${target.decision === "excluded" ? "已排除" : "已修正"}：${target.decision_reason}`)
                  : null,
                needsDecision
                  ? React.createElement(
                      React.Fragment,
                      null,
                      React.createElement("input", {
                        type: "text",
                        value: draft.reason || "",
                        onChange: (event) => updateOcrDraft(item.item_id, target.target_id, "reason", event.target.value),
                        "aria-label": `${userFacingImportLocation(target.locator_summary) || "待审核内容"} 的处理理由`,
                        placeholder: "处理理由"
                      }),
                      React.createElement("textarea", {
                        value: draft.corrected_text || "",
                        onChange: (event) => updateOcrDraft(item.item_id, target.target_id, "corrected_text", event.target.value),
                        "aria-label": `${userFacingImportLocation(target.locator_summary) || "待审核内容"} 的修正文本`,
                        placeholder: "修正文本"
                      }),
                      React.createElement("button", { type: "button", className: "secondary-button", disabled: isActing, onClick: () => runOcrAction(item.item_id, target.target_id, "retry") }, "重试此页"),
                      React.createElement("button", { type: "button", className: "secondary-button", disabled: isActing, onClick: () => runOcrAction(item.item_id, target.target_id, "correct") }, "保存修正"),
                      React.createElement("button", { type: "button", className: "secondary-button", disabled: isActing, onClick: () => runOcrAction(item.item_id, target.target_id, "exclude") }, "确认排除")
                    )
                  : null
              );
            }),
            item.version_suggestion
              ? React.createElement(
                  React.Fragment,
                  null,
                  React.createElement("span", { className: "row-status" }, "待审核确认"),
                  React.createElement(
                    "span",
                    { className: "row-note" },
                    `可能是已有资料的新版本：${item.version_suggestion.reason}`
                  )
                )
              : null,
            item.reason ? React.createElement("span", { className: "row-note" }, item.reason) : null
          ))
        ),
    React.createElement(
      "section",
      { className: "note-proposal-list", "aria-label": "派生 Markdown 提案" },
      React.createElement("h3", null, "Markdown 提案"),
      noteProposals.length === 0
        ? React.createElement("p", { className: "empty-state" }, "正在等待可预览的 Markdown 提案。")
        : noteProposals.map((proposal) => React.createElement(
            "div",
            { className: "note-proposal", key: `${proposal.kind}:${proposal.item_id}` },
            proposal.kind === "native"
              ? React.createElement(
                  React.Fragment,
                  null,
                  React.createElement("p", { className: "row-title" }, "原生 Markdown"),
                  React.createElement("p", { className: "row-note" }, `位置：${proposal.relative_path}`),
                  React.createElement("pre", { className: "markdown-preview" }, proposal.markdown)
                )
              : React.createElement(
                  React.Fragment,
                  null,
                  React.createElement("p", { className: "row-title" }, `派生笔记提案（版本 ${proposal.revision}）`),
                  proposal.risks?.length
                    ? React.createElement("p", { className: "row-status status-danger" }, `待审核范围：${proposal.risks.join("；")}`)
                    : null,
                  noteProposalActionReason
                    ? React.createElement("p", { className: "row-note" }, `边界调整不可用：${noteProposalActionReason}`)
                    : null,
                  React.createElement("pre", { className: "markdown-preview" }, derivedMarkdownPreview(proposal.index_note.markdown)),
                  proposal.notes.map((note, index) => {
                    const splitKey = `${proposal.item_id}:${note.sequence}`;
                    const safeBoundaries = note.safe_split_after_unit_indexes || [];
                    const selectedBoundary = splitSelections[splitKey] ?? safeBoundaries[0];
                    return React.createElement(
                      "div",
                      { className: "section-row note-proposal-row", key: note.note_id },
                      React.createElement("span", { className: "row-title" }, `${note.sequence}. ${note.title}`),
                      React.createElement("pre", { className: "markdown-preview" }, derivedMarkdownPreview(note.markdown)),
                      index < proposal.notes.length - 1
                        ? React.createElement("button", {
                            className: "secondary-button",
                            type: "button",
                            disabled: !canAdjustNoteProposals,
                            title: noteProposalActionReason || undefined,
                            onClick: () => runNoteProposalAction("merge", { item_id: proposal.item_id, before_sequence: note.sequence }),
                            "aria-label": `合并 ${note.title} 与下一篇笔记`
                          }, "与下一篇合并")
                        : null,
                      safeBoundaries.length
                        ? React.createElement(
                            React.Fragment,
                            null,
                            React.createElement(
                              "select",
                              {
                                "aria-label": `${note.title} 的安全拆分边界`,
                                disabled: !canAdjustNoteProposals,
                                value: selectedBoundary,
                                onChange: (event) => updateSplitSelection(proposal.item_id, note.sequence, event.target.value)
                              },
                              safeBoundaries.map((afterUnitIndex) => React.createElement(
                                "option",
                                { key: afterUnitIndex, value: afterUnitIndex },
                                `在第 ${afterUnitIndex + 1} 个单元后拆分`
                              ))
                            ),
                            React.createElement("button", {
                              className: "secondary-button",
                              type: "button",
                              disabled: !canAdjustNoteProposals,
                              title: noteProposalActionReason || undefined,
                              onClick: () => runNoteProposalAction("split", {
                                item_id: proposal.item_id,
                                sequence: note.sequence,
                                after_unit_index: selectedBoundary
                              }),
                              "aria-label": `在 ${note.title} 的安全边界拆分笔记`
                            }, "拆分")
                          )
                        : React.createElement("span", { className: "row-note" }, "没有可安全拆分的边界。")
                    );
                  })
                )
          ))
    )
    ,
    React.createElement(
      "section",
      { className: "classification-list", "aria-label": "分类建议" },
      React.createElement("h3", null, "分类建议"),
      classificationControlReason || classificationBatchReason
        ? React.createElement(
            "p",
            { className: "status-line", role: "status" },
            classificationControlReason || classificationBatchReason
          )
        : null,
      classifications.length === 0
        ? React.createElement("p", { className: "empty-state" }, "正在等待分类建议。")
        : React.createElement(
            React.Fragment,
            null,
            React.createElement(
              "div",
              { className: "detail-actions" },
              React.createElement("button", {
                className: "secondary-button",
                type: "button",
                disabled: !canManageClassifications || !hasHighConfidenceSuggestion,
                title: classificationBatchReason || "仅接受尚未决定的高置信度建议。",
                onClick: acceptHighConfidenceClassifications
              }, "接受高置信度建议")
            ),
            classifications.map((suggestion) => {
              const draft = classificationDrafts[suggestion.item_id] || {};
              const canDecide = canManageClassifications && !suggestion.decision;
              const decisionText = suggestion.decision === "accepted"
                ? "已接受"
                : suggestion.decision === "excluded"
                  ? "已排除"
                  : suggestion.decision === "revised"
                    ? "已修正"
                    : suggestion.status === "required-check"
                      ? "必须检查"
                      : "待确认";
              return React.createElement(
                "div",
                { className: "section-row review-diff-row classification-row", key: suggestion.item_id },
                React.createElement("span", { className: "row-title" }, `资料：${suggestion.filename || suggestion.domain}`),
                React.createElement("span", { className: "row-meta" }, `目标 vault：${suggestion.target_vault_label}`),
                React.createElement("span", { className: "row-note" }, `目标文件夹：${suggestion.target_folder}`),
                React.createElement("span", { className: "row-note" }, `文件名：${suggestion.filename}`),
                React.createElement("span", { className: "row-note" }, `置信度：${suggestion.confidence}`),
                React.createElement(
                  "span",
                  { className: `row-status${suggestion.status === "required-check" && !suggestion.decision ? " status-danger" : ""}` },
                  decisionText
                ),
                React.createElement("span", { className: "row-note" }, suggestion.reason),
                suggestion.decision_reason
                  ? React.createElement("span", { className: "row-note" }, `决定理由：${suggestion.decision_reason}`)
                  : null,
                !suggestion.decision
                  ? React.createElement(
                      "div",
                      { className: "classification-controls" },
                      React.createElement("input", {
                        type: "text",
                        value: draft.domain ?? suggestion.domain,
                        onChange: (event) => updateClassificationDraft(suggestion.item_id, "domain", event.target.value),
                        disabled: !canDecide,
                        "aria-label": `${suggestion.filename || "分类建议"} 的领域`
                      }),
                      React.createElement("input", {
                        type: "text",
                        value: draft.target_folder ?? suggestion.target_folder,
                        onChange: (event) => updateClassificationDraft(suggestion.item_id, "target_folder", event.target.value),
                        disabled: !canDecide,
                        "aria-label": `${suggestion.filename || "分类建议"} 的目标文件夹`
                      }),
                      React.createElement("input", {
                        type: "text",
                        value: draft.filename ?? suggestion.filename,
                        onChange: (event) => updateClassificationDraft(suggestion.item_id, "filename", event.target.value),
                        disabled: !canDecide,
                        "aria-label": `${suggestion.filename || "分类建议"} 的目标文件名`
                      }),
                      React.createElement("input", {
                        type: "text",
                        value: draft.reason || "",
                        onChange: (event) => updateClassificationDraft(suggestion.item_id, "reason", event.target.value),
                        disabled: !canDecide,
                        placeholder: "修正或排除理由",
                        "aria-label": `${suggestion.filename || "分类建议"} 的分类决定理由`
                      }),
                      React.createElement("button", {
                        className: "secondary-button",
                        type: "button",
                        disabled: !canDecide,
                        title: classificationControlReason || undefined,
                        onClick: () => runClassificationAction(suggestion, "accepted")
                      }, "接受"),
                      React.createElement("button", {
                        className: "secondary-button",
                        type: "button",
                        disabled: !canDecide,
                        title: classificationControlReason || undefined,
                        onClick: () => runClassificationAction(suggestion, "revise")
                      }, "保存修正"),
                      React.createElement("button", {
                        className: "secondary-button",
                        type: "button",
                        disabled: !canDecide,
                        title: classificationControlReason || undefined,
                        onClick: () => runClassificationAction(suggestion, "excluded")
                      }, "确认排除")
                    )
                  : null
              );
            })
          )
    )
  );
}

function sourceParseBlockKindText(kind) {
  return {
    heading: "标题",
    paragraph: "正文",
    list: "列表",
    table: "表格",
    formula: "公式",
    image: "图片",
    caption: "图片说明",
    code: "代码",
    unresolved: "待确认内容"
  }[kind] || "解析内容";
}

function importItemLabelById(items) {
  return new Map(items.map((item) => [item.item_id, item.label]));
}

export function SourceParseResults({ sourceParses = [], items = [] }) {
  const itemLabels = importItemLabelById(items);
  const visibleParses = sourceParses.filter((sourceParse) => sourceParse.blocks?.length);

  return React.createElement(
    "section",
    { className: "source-parse-list", "aria-label": "源解析内容" },
    React.createElement("h3", null, "源解析内容"),
    visibleParses.length === 0
      ? React.createElement("p", { className: "empty-state" }, "正在等待可查看的源解析内容。")
      : visibleParses.map((sourceParse) => React.createElement(
        "div",
        { className: "source-parse", key: sourceParse.item_id },
        React.createElement("p", { className: "row-title" }, itemLabels.get(sourceParse.item_id) || `资料项 ${sourceParse.item_id}`),
        sourceParse.blocks.map((block, index) => React.createElement(
          "div",
          { className: "source-parse-block", key: `${sourceParse.item_id}:${index}` },
          React.createElement("span", { className: "row-meta" }, `${sourceParseBlockKindText(block.kind)}${block.location ? ` · ${block.location}` : ""}`),
          React.createElement("pre", { className: "source-parse-content" }, block.content)
        ))
      ))
  );
}

export function AutomaticMarkdownResults({ noteProposals = [], items = [] }) {
  const itemLabels = importItemLabelById(items);
  return React.createElement(
    "section",
    { className: "note-proposal-list", "aria-label": "Markdown 结果" },
    React.createElement("h3", null, "Markdown 结果"),
    noteProposals.length === 0
      ? React.createElement("p", { className: "empty-state" }, "正在等待可查看的 Markdown 结果。")
      : noteProposals.map((proposal) => React.createElement(
        "div",
        { className: "note-proposal", key: `${proposal.kind}:${proposal.item_id}` },
        itemLabels.get(proposal.item_id)
          ? React.createElement("p", { className: "row-meta" }, itemLabels.get(proposal.item_id))
          : null,
        proposal.kind === "native"
          ? React.createElement(
            React.Fragment,
            null,
            React.createElement("p", { className: "row-title" }, "原生 Markdown"),
            React.createElement("p", { className: "row-note" }, `位置：${proposal.relative_path}`),
            React.createElement("pre", { className: "markdown-preview" }, proposal.markdown)
          )
          : React.createElement(
            React.Fragment,
            null,
            React.createElement("p", { className: "row-title" }, `结构化 Markdown（版本 ${proposal.revision}）`),
            proposal.index_note?.markdown
              ? React.createElement(
                React.Fragment,
                null,
                React.createElement("p", { className: "row-note" }, `索引：${proposal.index_note.relative_path}`),
                React.createElement("pre", { className: "markdown-preview" }, derivedMarkdownPreview(proposal.index_note.markdown))
              )
              : null,
            (proposal.notes || []).map((note) => React.createElement(
              "div",
              { className: "section-row note-proposal-row", key: note.note_id },
              React.createElement("span", { className: "row-title" }, `${note.sequence}. ${note.title}`),
              React.createElement("span", { className: "row-note" }, `位置：${note.relative_path}`),
              React.createElement("pre", { className: "markdown-preview" }, derivedMarkdownPreview(note.markdown))
            ))
          )
      ))
  );
}

export function ImportContentComparison({ items = [], sourceParses = [], noteProposals = [] }) {
  return React.createElement(
    "div",
    { className: "import-content-comparison" },
    React.createElement(SourceParseResults, { sourceParses, items }),
    React.createElement(AutomaticMarkdownResults, { noteProposals, items })
  );
}

function AutomaticImportTaskDetail({ taskId, onBack, onTaskChanged, onTaskDeleted, onTaskSnapshot }) {
  const [detail, setDetail] = React.useState(null);
  const [status, setStatus] = React.useState("");
  const [isActing, setIsActing] = React.useState(false);
  const refreshTimerRef = React.useRef(null);

  const loadDetail = React.useCallback(async () => {
    try {
      const response = await requestJson(`${IMPORT_TASKS_ENDPOINT}/${taskId}`);
      setDetail(response);
      onTaskSnapshot(response.task);
      setStatus("");
      return response;
    } catch (error) {
      setStatus(error.message);
      return null;
    }
  }, [onTaskSnapshot, taskId]);

  const scheduleDetailRefresh = React.useCallback(() => {
    if (refreshTimerRef.current !== null) return;
    refreshTimerRef.current = window.setTimeout(() => {
      refreshTimerRef.current = null;
      void loadDetail();
    }, 250);
  }, [loadDetail]);

  React.useEffect(() => {
    let eventSource;
    let disposed = false;
    loadDetail().then((loaded) => {
      if (!loaded || disposed) return;
      eventSource = new window.EventSource(
        `${IMPORT_TASKS_ENDPOINT}/${taskId}/events?after=${loaded.event_cursor || 0}`
      );
      for (const eventName of IMPORT_TASK_EVENT_NAMES) {
        eventSource.addEventListener(eventName, scheduleDetailRefresh);
      }
    });
    return () => {
      disposed = true;
      eventSource?.close();
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
    };
  }, [loadDetail, scheduleDetailRefresh, taskId]);

  async function runAction(action) {
    if (isActing) return;
    setStatus("");
    setIsActing(true);
    try {
      const response = await requestJson(`${IMPORT_TASKS_ENDPOINT}/${taskId}/${action}`, { method: "POST" });
      onTaskChanged(response.task);
      if (response.task.task_id !== taskId) {
        onBack(response.task.task_id);
        return;
      }
      await loadDetail();
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  if (!detail) {
    return React.createElement(
      "section",
      { className: "workspace-section", "aria-label": "导入任务详情" },
      React.createElement("button", { className: "back-button", type: "button", onClick: () => onBack(null) }, "返回任务列表"),
      React.createElement("p", { className: "empty-state", role: "status" }, status || "正在读取任务状态。")
    );
  }

  const {
    task,
    items = [],
    note_proposals: noteProposals = [],
    source_parses: sourceParses = [],
    classification_suggestions: classifications = [],
    conversion_graphs: conversionGraphs = [],
    commit_journals: commitJournals = [],
    index = null
  } = detail;
  const canCancel = task.lifecycle === "running";
  const canResume = task.lifecycle === "recoverable" || task.lifecycle === "cancelled";
  const canVerifyProjectionRebuild = ["complete", "completed-with-confirmed-gaps"].includes(task.lifecycle)
    && conversionGraphs.some((graph) => typeof graph.graph_id === "string" && Number.isInteger(graph.graph_revision));

  return React.createElement(
    "section",
    { className: "import-task-detail", "aria-label": "导入任务详情" },
    React.createElement("button", { className: "back-button", type: "button", onClick: () => onBack(null) }, "返回任务列表"),
    React.createElement("h2", null, "导入任务详情"),
    React.createElement("p", { className: "scope-summary" }, `目标 vault：${task.vault_label}；范围：${task.scope_label}`),
    task.markdown_pipeline
      ? React.createElement("p", { className: "row-note" }, `结构化：${task.markdown_pipeline === "ai" ? "AI 结构化" : "本地结构化"}`)
      : null,
    task.online_parse?.enabled
      ? React.createElement("p", { className: "row-note" }, `在线解析：${task.online_parse.provider_name} / ${task.online_parse.model}`)
      : null,
    React.createElement(
      "div",
      { className: "progress-sequence", "aria-live": "polite" },
      React.createElement("span", { className: "status-marker" }, `状态：${importLifecycleText(task.lifecycle)}`),
      React.createElement("span", null, `当前阶段：${importPhaseText(task.phase)}`),
      ...IMPORT_PROGRESS_PHASES.map((phase) => React.createElement(
        "span",
        { key: phase },
        `${importPhaseText(phase)}：${progressPhaseStatus(task, phase)}`
      )),
      React.createElement("span", null, `已发现 ${task.counts.discovered}`),
      React.createElement("span", null, `已解析 ${task.counts.parsed || 0}`),
      React.createElement("span", null, `已生成笔记 ${task.counts.derived_notes || 0}`),
      React.createElement("span", null, `异常 ${task.counts.failed + task.counts.parse_failed + task.counts.ocr_failed}`)
    ),
    index
      ? React.createElement("p", { className: "row-note", role: "status" }, `索引：${index.status}；已索引 ${index.current_count} 项；失败 ${index.failure_count} 项。`)
      : null,
    task.current_item_label ? React.createElement("p", { className: "status-line" }, `当前文件：${task.current_item_label}`) : null,
    task.failure_reason ? React.createElement("p", { className: "status-line status-danger" }, `失败原因：${task.failure_reason}`) : null,
    React.createElement(
      "div",
      { className: "detail-actions" },
      canCancel
        ? React.createElement("button", { className: "secondary-button", type: "button", disabled: isActing, onClick: () => runAction("cancel") }, "取消")
        : null,
      canResume
        ? React.createElement(
          "button",
          { className: "primary-button", type: "button", disabled: isActing, onClick: () => runAction("resume") },
          task.lifecycle === "cancelled" ? "创建新任务" : task.recovery_actions.includes("restart-conversion") ? "重新转换" : task.recovery_actions.includes("restart-parse") ? "重新解析" : task.recovery_actions.includes("restart-ocr") ? "重新 OCR" : task.recovery_actions.includes("restart-derivation") ? "重新生成笔记" : task.recovery_actions.includes("retry-commit") ? "重试提交" : "自动重试"
        )
        : null
    ),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null,
    React.createElement(
      "section",
      { className: "import-item-list", "aria-label": "资料项" },
      React.createElement("h3", null, "资料项"),
      items.length === 0
        ? React.createElement("p", { className: "empty-state" }, "正在扫描资料。")
        : items.map((item) => React.createElement(
          "div",
          { className: "section-row", key: item.item_id },
          React.createElement("span", { className: "row-title" }, item.label),
          React.createElement("span", { className: "row-meta" }, `${importDocumentKindText(item.document_kind)}；${importCategoryText(item.category)}`),
          React.createElement("span", { className: "row-note" }, `${importParseStatusText(item.parse_status)}；${importConversionStatusText(item.conversion_status)}；${importOcrStatusText(item.ocr_status)}`),
          React.createElement(ImportParserTag, { engine: item.conversion_engine }),
          userFacingImportIssue(item.parse_issue_summary || item.ocr_issue_summary)
            ? React.createElement("span", { className: "row-note" }, userFacingImportIssue(item.parse_issue_summary || item.ocr_issue_summary))
            : null
        ))
    ),
    React.createElement(ImportContentComparison, { items, sourceParses, noteProposals }),
    React.createElement(
      "section",
      { className: "classification-list", "aria-label": "分类建议" },
      React.createElement("h3", null, "分类建议"),
      classifications.length === 0
        ? React.createElement("p", { className: "empty-state" }, "暂无分类建议。")
        : classifications.map((suggestion) => React.createElement(
          "div",
          { className: "section-row", key: suggestion.item_id },
          React.createElement("span", { className: "row-title" }, suggestion.filename || suggestion.domain),
          React.createElement("span", { className: "row-meta" }, suggestion.domain),
          React.createElement("span", { className: "row-note" }, suggestion.reason)
        ))
    ),
    commitJournals.length
      ? React.createElement(
        "section",
        { className: "commit-journal-list", "aria-label": "提交记录" },
        React.createElement("h3", null, "提交记录"),
        commitJournals.map((journal, index) => React.createElement(
          "div",
          { className: "section-row", key: `${journal.unit_id}:${index}` },
          React.createElement("span", { className: "row-title" }, journal.source_label),
          React.createElement("span", { className: "row-meta" }, journal.status),
          journal.reason ? React.createElement("span", { className: "row-note" }, journal.reason) : null
        ))
      )
      : null,
    canVerifyProjectionRebuild
      ? React.createElement(ProjectionRebuildVerificationPanel, { task, conversionGraphs, onTaskDeleted })
      : null
  );
}

export function ImportTaskCenter({ tasks, error, isLoading, selectedTaskId, onSelect, onTaskChanged, onTaskDeleted, onTaskSnapshot, vault }) {
  const [deleteError, setDeleteError] = React.useState("");
  const [deletingTaskIds, setDeletingTaskIds] = React.useState(() => new Set());
  const [selectedTaskIds, setSelectedTaskIds] = React.useState(() => new Set());
  const [pageSize, setPageSize] = React.useState(10);
  const [page, setPage] = React.useState(1);
  const listRef = React.useRef(null);
  const vaultTasks = vault ? tasks.filter((task) => task.vault_id === vault.vault_id) : [];
  const totalPages = Math.max(1, Math.ceil(vaultTasks.length / pageSize));
  const currentPage = Math.max(1, Math.min(page, totalPages));
  const visibleTasks = vaultTasks.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const deletableVisibleTasks = visibleTasks.filter((task) => task.lifecycle !== "running");
  const selectedVisibleTasks = deletableVisibleTasks.filter((task) => selectedTaskIds.has(task.task_id));
  const isDeleting = deletingTaskIds.size > 0;

  React.useEffect(() => {
    setPage(1);
  }, [pageSize, vault?.vault_id]);

  React.useEffect(() => {
    setSelectedTaskIds(new Set());
  }, [pageSize, currentPage, vault?.vault_id]);

  React.useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  function toggleTaskSelection(taskId, checked) {
    setSelectedTaskIds((current) => {
      const next = new Set(current);
      if (checked) next.add(taskId);
      else next.delete(taskId);
      return next;
    });
  }

  function toggleVisibleTaskSelection(checked) {
    setSelectedTaskIds((current) => {
      const next = new Set(current);
      for (const task of deletableVisibleTasks) {
        if (checked) next.add(task.task_id);
        else next.delete(task.task_id);
      }
      return next;
    });
  }

  async function deleteTasks(selectedTasks) {
    if (isDeleting || selectedTasks.length === 0) return;
    const confirmed = window.confirm(
      selectedTasks.length === 1
        ? `删除任务“${selectedTasks[0].scope_label}”及其处理数据？\n\n这会删除该任务提交到 Vault 的 Markdown、源文件和资料附件；不会删除上传时的本地原件。`
        : `删除所选 ${selectedTasks.length} 个任务及其处理数据？\n\n这会删除每个任务提交到 Vault 的 Markdown、源文件和资料附件；不会删除上传时的本地原件。`
    );
    if (!confirmed) return;
    setDeleteError("");
    setDeletingTaskIds(new Set(selectedTasks.map((task) => task.task_id)));
    const failures = [];
    for (const task of selectedTasks) {
      try {
        await onTaskDeleted(task.task_id);
        setSelectedTaskIds((current) => {
          const next = new Set(current);
          next.delete(task.task_id);
          return next;
        });
      } catch (deleteFailure) {
        failures.push(`${task.scope_label}：${deleteFailure.message}`);
      } finally {
        setDeletingTaskIds((current) => {
          const next = new Set(current);
          next.delete(task.task_id);
          return next;
        });
      }
    }
    if (failures.length) setDeleteError(failures.join("；"));
    listRef.current?.focus();
  }

  if (selectedTaskId) {
    return React.createElement(AutomaticImportTaskDetail, {
      taskId: selectedTaskId,
      onBack: onSelect,
      onTaskChanged,
      onTaskDeleted,
      onTaskSnapshot
    });
  }
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(ImportTaskLauncher, { vault, onCreated: onTaskChanged }),
    React.createElement(
      "section",
      { className: "import-task-list", "aria-label": "导入任务列表", ref: listRef, tabIndex: -1 },
      React.createElement("p", { className: "section-label" }, "任务"),
      error ? React.createElement("p", { className: "status-line status-danger", role: "status" }, `无法读取导入任务：${error}`) : null,
      deleteError ? React.createElement("p", { className: "status-line status-danger", role: "status" }, `无法删除导入任务：${deleteError}`) : null,
      deletableVisibleTasks.length
        ? React.createElement(
            "div",
            { className: "import-task-bulk-actions" },
            React.createElement("input", {
              type: "checkbox",
              checked: selectedVisibleTasks.length === deletableVisibleTasks.length,
              ref: (element) => {
                if (element) element.indeterminate = selectedVisibleTasks.length > 0 && selectedVisibleTasks.length < deletableVisibleTasks.length;
              },
              disabled: isDeleting,
              onChange: (event) => toggleVisibleTaskSelection(event.target.checked),
              "aria-label": "全选当前页可删除任务"
            }),
            React.createElement("span", null, `已选择 ${selectedVisibleTasks.length} 项`),
            React.createElement(
              "button",
              {
                className: "text-button danger-text-button",
                type: "button",
                disabled: selectedVisibleTasks.length === 0 || isDeleting,
                onClick: () => deleteTasks(selectedVisibleTasks)
              },
              isDeleting ? "删除中" : "删除所选"
            )
          )
        : null,
      isLoading
        ? React.createElement("p", { className: "empty-state", role: "status" }, "正在读取任务快照。")
        : vaultTasks.length === 0 && !error
          ? React.createElement("p", { className: "empty-state" }, "当前没有导入任务。")
          : visibleTasks.map((task) => React.createElement(
              "div",
              { className: "section-row import-task-row", key: task.task_id },
              task.lifecycle !== "running"
                ? React.createElement("input", {
                    className: "import-task-select",
                    type: "checkbox",
                    checked: selectedTaskIds.has(task.task_id),
                    disabled: isDeleting,
                    onChange: (event) => toggleTaskSelection(task.task_id, event.target.checked),
                    "aria-label": `选择任务 ${task.scope_label}`
                  })
                : null,
              React.createElement(
                "button",
                { className: "import-task-open", type: "button", onClick: () => onSelect(task.task_id) },
                React.createElement("span", { className: "row-title" }, task.scope_label),
                React.createElement("span", { className: "row-meta" }, `目标：${task.vault_label}`),
                React.createElement("span", { className: "row-status" }, `${importLifecycleText(task.lifecycle)} · ${importPhaseText(task.phase)}`),
                React.createElement("span", { className: "row-note" }, task.recovery_actions.length
                  ? `恢复：${task.recovery_actions.map(importRecoveryActionText).join("、")}`
                  : `发现 ${task.counts.discovered}；新资料 ${task.counts.new || 0}；重复资料 ${task.counts.duplicate || 0}；可能版本 ${task.counts.possible_version || 0}；识别失败 ${task.counts.identity_failed || 0}；已解析 ${task.counts.parsed || 0}；解析失败 ${task.counts.parse_failed || 0}；待审核问题 ${task.counts.required_check || 0}；失败 ${task.counts.failed}`)
              ),
              task.lifecycle !== "running"
                ? React.createElement(
                    "button",
                    {
                      className: "text-button danger-text-button import-task-delete",
                      type: "button",
                      "aria-label": `删除任务 ${task.scope_label}`,
                      disabled: isDeleting,
                      onClick: () => deleteTasks([task])
                    },
                    deletingTaskIds.has(task.task_id) ? "删除中" : "删除"
                  )
                : null
            )),
      vaultTasks.length
        ? React.createElement(
          "div",
          { className: "import-task-pagination", "aria-label": "任务分页" },
          React.createElement(
            "label",
            { className: "import-task-page-size" },
            React.createElement("span", null, "每页显示"),
            React.createElement(
              "select",
              {
                value: pageSize,
                "aria-label": "每页任务数量",
                onChange: (event) => setPageSize(Number(event.target.value))
              },
              [10, 20, 50].map((size) => React.createElement("option", { key: size, value: size }, `${size} 条`))
            )
          ),
          React.createElement(
            "div",
            { className: "import-task-page-controls" },
            React.createElement(
              "button",
              { className: "secondary-button", type: "button", disabled: currentPage <= 1, onClick: () => setPage(currentPage - 1) },
              "上一页"
            ),
            React.createElement("span", { role: "status" }, `第 ${currentPage} / ${totalPages} 页`),
            React.createElement(
              "button",
              { className: "secondary-button", type: "button", disabled: currentPage >= totalPages, onClick: () => setPage(currentPage + 1) },
              "下一页"
            )
          )
        )
        : null
    )
  );
}

export function App() {
  const [activeDestination, setActiveDestination] = React.useState("workbench");
  const [healthStatus, setHealthStatus] = React.useState("本机服务正在验证");
  const [sessionStatus, setSessionStatus] = React.useState("本机会话正在建立");
  const [menuOpen, setMenuOpen] = React.useState(false);
  const [vaults, setVaults] = React.useState([]);
  const [vaultsLoading, setVaultsLoading] = React.useState(true);
  const [providers, setProviders] = React.useState([]);
  const [providersLoading, setProvidersLoading] = React.useState(true);
  const [tasks, setTasks] = React.useState([]);
  const [tasksLoading, setTasksLoading] = React.useState(true);
  const [tasksError, setTasksError] = React.useState("");
  const [workbenchOverview, setWorkbenchOverview] = React.useState(null);
  const [workbenchOverviewLoading, setWorkbenchOverviewLoading] = React.useState(true);
  const [workbenchOverviewError, setWorkbenchOverviewError] = React.useState("");
  const [selectedWorkbenchVaultId, setSelectedWorkbenchVaultId] = React.useState(null);
  const [sessionPage, setSessionPage] = React.useState({ sessions: [], page: 1, page_size: 25, total: 0, total_pages: 1 });
  const [sessionFilters, setSessionFilters] = React.useState({ query: "", sort: "updated_at", order: "desc", page: 1 });
  const [sessionsLoading, setSessionsLoading] = React.useState(true);
  const [sessionsError, setSessionsError] = React.useState("");
  const [selectedSessionId, setSelectedSessionId] = React.useState(null);
  const [selectedSessionDetail, setSelectedSessionDetail] = React.useState(null);
  const [sessionDetailLoading, setSessionDetailLoading] = React.useState(false);
  const [sessionDetailError, setSessionDetailError] = React.useState("");
  const [retrievalMode, setRetrievalMode] = React.useState({
    mode: "keyword",
    label: "仅关键词",
    options: [
      { mode: "keyword", label: "仅关键词" },
      { mode: "semantic", label: "仅语义" },
      { mode: "hybrid", label: "关键词与语义混合" }
    ]
  });
  const [retrievalModeLoading, setRetrievalModeLoading] = React.useState(true);
  const [modelDefaults, setModelDefaults] = React.useState({
    chat: { default: null, status: "unconfigured", reason: "正在加载对话/文本生成 Model。" },
    embedding: { default: null, status: "unconfigured", reason: "正在加载 Embedding Model。" },
    rerank: { default: null, status: "unconfigured", reason: "正在加载 Rerank（重排）Model。" },
    markdown: { default: null, status: "unconfigured", reason: "正在加载 Markdown 结构化 Model。" }
  });
  const [selectedVaultId, setSelectedVaultId] = React.useState(null);
  const [selectedTaskId, setSelectedTaskId] = React.useState(null);
  const [formVault, setFormVault] = React.useState(undefined);
  const [providerForm, setProviderForm] = React.useState(undefined);
  const [confirmationRequest, setConfirmationRequest] = React.useState(null);
  const [confirmationError, setConfirmationError] = React.useState("");
  const [confirmationSubmitting, setConfirmationSubmitting] = React.useState(false);
  const actionTriggerRef = React.useRef(null);
  const sessionListRequestRef = React.useRef(0);
  const sessionDetailRequestRef = React.useRef(0);
  const selectedSessionIdRef = React.useRef(null);
  const menuButtonRef = React.useRef(null);
  const firstMenuLinkRef = React.useRef(null);
  const menuPanelRef = React.useRef(null);

  const loadVaults = React.useCallback(() => {
    setVaultsLoading(true);
    return requestJson(VAULTS_ENDPOINT)
      .then((response) => setVaults(response.vaults))
      .catch(() => setVaults([]))
      .finally(() => setVaultsLoading(false));
  }, []);

  const loadProviders = React.useCallback(() => {
    setProvidersLoading(true);
    return requestJson(PROVIDERS_ENDPOINT)
      .then((response) => setProviders(response.providers))
      .catch(() => setProviders([]))
      .finally(() => setProvidersLoading(false));
  }, []);

  const loadTasks = React.useCallback(() => {
    setTasksLoading(true);
    setTasksError("");
    return requestJson(IMPORT_TASKS_ENDPOINT)
      .then((response) => setTasks(response.tasks))
      .catch((error) => setTasksError(error.message))
      .finally(() => setTasksLoading(false));
  }, []);

  const loadWorkbenchOverview = React.useCallback(() => {
    setWorkbenchOverviewLoading(true);
    setWorkbenchOverviewError("");
    return requestJson(WORKBENCH_OVERVIEW_ENDPOINT)
      .then((response) => setWorkbenchOverview(response))
      .catch((error) => setWorkbenchOverviewError(error.message))
      .finally(() => setWorkbenchOverviewLoading(false));
  }, []);

  const loadModelDefaults = React.useCallback(() => (
    requestJson(`${PROVIDERS_ENDPOINT}/defaults`)
      .then((response) => setModelDefaults(response))
      .catch((error) => setModelDefaults({
        chat: { default: null, status: "unavailable", reason: error.message },
        embedding: { default: null, status: "unavailable", reason: error.message },
        rerank: { default: null, status: "unavailable", reason: error.message }
      }))
  ), []);

  const loadRetrievalMode = React.useCallback(() => {
    setRetrievalModeLoading(true);
    return requestJson(RETRIEVAL_MODE_ENDPOINT)
      .then((response) => setRetrievalMode(response))
      .catch(() => undefined)
      .finally(() => setRetrievalModeLoading(false));
  }, []);

  async function changeRetrievalMode(mode) {
    const response = await requestJson(RETRIEVAL_MODE_ENDPOINT, {
      method: "POST",
      body: JSON.stringify({ mode })
    });
    setRetrievalMode(response);
    return response;
  }

  const loadSessionDetail = React.useCallback(async (sessionId) => {
    const requestId = ++sessionDetailRequestRef.current;
    if (!sessionId) {
      setSelectedSessionDetail(null);
      setSessionDetailError("");
      return;
    }
    setSessionDetailLoading(true);
    setSessionDetailError("");
    try {
      const detail = await requestJson(`${SESSIONS_ENDPOINT}/${sessionId}`);
      if (requestId === sessionDetailRequestRef.current) setSelectedSessionDetail(detail);
    } catch (requestError) {
      if (requestId === sessionDetailRequestRef.current) {
        setSelectedSessionDetail(null);
        setSessionDetailError(requestError.message);
      }
    } finally {
      if (requestId === sessionDetailRequestRef.current) setSessionDetailLoading(false);
    }
  }, []);

  const loadSessions = React.useCallback(async (nextFilters) => {
    const requestId = ++sessionListRequestRef.current;
    const requested = { query: "", sort: "updated_at", order: "desc", page: 1, ...nextFilters };
    const search = new window.URLSearchParams({
      query: requested.query,
      sort: requested.sort,
      order: requested.order,
      page: String(requested.page),
      page_size: "25"
    });
    setSessionsLoading(true);
    setSessionsError("");
    try {
      const response = await requestJson(`${SESSIONS_ENDPOINT}?${search}`);
      if (requestId !== sessionListRequestRef.current) return null;
      setSessionPage(response);
      setSessionFilters({ ...requested, page: response.page });
      setSelectedSessionId((current) => (
        response.sessions.some((session) => session.session_id === current)
          ? current
          : response.sessions[0]?.session_id || null
      ));
      return response;
    } catch (requestError) {
      if (requestId === sessionListRequestRef.current) setSessionsError(requestError.message);
      return null;
    } finally {
      if (requestId === sessionListRequestRef.current) setSessionsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    selectedSessionIdRef.current = selectedSessionId;
    loadSessionDetail(selectedSessionId);
  }, [loadSessionDetail, selectedSessionId]);

  React.useEffect(() => {
    fetch(HEALTH_ENDPOINT)
      .then((response) => {
        if (!response.ok) throw new Error("Health check failed.");
        return response.json();
      })
      .then(() => setHealthStatus("本机服务可用"))
      .catch(() => setHealthStatus("本机服务不可用"));

    fetch(LOCAL_SESSION_ENDPOINT)
      .then((response) => {
        if (!response.ok) throw new Error("Local session check failed.");
        return response.json();
      })
      .then(() => {
        setSessionStatus("本机会话已建立");
        return loadWorkbenchOverview();
      })
      .catch(() => {
        setSessionStatus("本机会话不可用");
        setVaultsLoading(false);
        setTasksLoading(false);
        setTasksError("本机会话不可用。");
        setWorkbenchOverviewLoading(false);
        setWorkbenchOverviewError("本机会话不可用。");
        setSessionsLoading(false);
        setSessionsError("本机会话不可用。");
      });
  }, [loadWorkbenchOverview]);

  React.useEffect(() => {
    if (sessionStatus !== "本机会话已建立" || activeDestination === "workbench") return;
    if (activeDestination === "materials") {
      void loadVaults();
      return;
    }
    if (activeDestination === "tasks") {
      void Promise.all([loadVaults(), loadTasks()]);
      return;
    }
    if (activeDestination === "sessions") {
      void Promise.all([
        loadVaults(),
        loadProviders(),
        loadModelDefaults(),
        loadRetrievalMode(),
        loadSessions()
      ]);
      return;
    }
    if (activeDestination === "settings") {
      void Promise.all([loadVaults(), loadProviders(), loadModelDefaults()]);
    }
  }, [
    activeDestination,
    loadModelDefaults,
    loadProviders,
    loadRetrievalMode,
    loadSessions,
    loadTasks,
    loadVaults,
    sessionStatus
  ]);

  React.useEffect(() => {
    if (menuOpen) firstMenuLinkRef.current?.focus();
  }, [menuOpen]);

  const activePage = NAVIGATION_DESTINATIONS.find(
    (destination) => destination.id === activeDestination
  );
  const selectedVault = vaults.find((vault) => vault.vault_id === selectedVaultId) || null;
  const currentVault = vaults.find((vault) => vault.is_current) || null;
  const contextVault = currentVault || workbenchOverview?.vaults?.find((vault) => vault.is_current) || null;
  const currentPolicy = currentVault ? policyFor(currentVault) : null;

  function closeMenu() {
    setMenuOpen(false);
    menuButtonRef.current?.focus();
  }

  function navigate(destinationId) {
    setActiveDestination(destinationId);
    setSelectedVaultId(null);
    setSelectedTaskId(null);
    setFormVault(undefined);
    setProviderForm(undefined);
    setConfirmationRequest(null);
    setConfirmationError("");
    setSelectedWorkbenchVaultId(null);
    if (menuOpen) closeMenu();
  }

  function handleMenuKeyDown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeMenu();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...menuPanelRef.current.querySelectorAll('a[href], button:not([disabled])')];
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function updateVault(vault) {
    setVaults((current) => current.map((item) => (
      item.vault_id === vault.vault_id
        ? vault
        : vault.is_current ? { ...item, is_current: false } : item
    )));
    loadWorkbenchOverview();
  }

  const syncTask = React.useCallback((task) => {
    setTasks((current) => {
      const remaining = current.filter((item) => item.task_id !== task.task_id);
      return [task, ...remaining];
    });
  }, []);

  const updateTask = React.useCallback((task) => {
    syncTask(task);
    setSelectedTaskId(task.task_id);
    setActiveDestination("tasks");
  }, [syncTask]);

  const deleteTask = React.useCallback(async (taskId, { keepSelected = false } = {}) => {
    setTasksError("");
    await requestJson(`${IMPORT_TASKS_ENDPOINT}/${taskId}`, { method: "DELETE" });
    setTasks((current) => current.filter((task) => task.task_id !== taskId));
    if (!keepSelected) setSelectedTaskId((current) => current === taskId ? null : current);
    await loadWorkbenchOverview();
  }, [loadWorkbenchOverview]);

  async function createPersistentSession() {
    const response = await requestJson(SESSIONS_ENDPOINT, {
      method: "POST",
      body: JSON.stringify({})
    });
    setSelectedSessionDetail({
      session: response.session,
      messages: [],
      task_states: [],
      citations: [],
      generation_results: []
    });
    await loadSessions({ query: "", sort: "updated_at", order: "desc", page: 1 });
    setSelectedSessionId(response.session.session_id);
    return response.session;
  }

  async function renamePersistentSession(sessionId, title) {
    const response = await requestJson(`${SESSIONS_ENDPOINT}/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify({ title })
    });
    await loadSessions(sessionFilters);
    setSelectedSessionDetail((current) => (
      current?.session.session_id === sessionId ? { ...current, session: response.session } : current
    ));
    return response.session;
  }

  async function pickPersistentSessionAttachments(sessionId) {
    const selection = await requestJson(`${SESSIONS_ENDPOINT}/${sessionId}/attachments/select`, { method: "POST" });
    if (!selection.selection_id) return;
    const response = await requestJson(`${SESSIONS_ENDPOINT}/${sessionId}/attachments`, {
      method: "POST",
      body: JSON.stringify({ selection_id: selection.selection_id })
    });
    setSelectedSessionDetail((current) => (
      current?.session.session_id === sessionId
        ? { ...current, attachments: [...(current.attachments || []), ...response.attachments] }
        : current
    ));
    await loadSessionDetail(sessionId);
  }

  async function removePersistentSessionAttachment(sessionId, attachmentId) {
    await requestJson(`${SESSIONS_ENDPOINT}/${sessionId}/attachments/${attachmentId}`, { method: "DELETE" });
    setSelectedSessionDetail((current) => (
      current?.session.session_id === sessionId
        ? { ...current, attachments: (current.attachments || []).filter((item) => item.attachment_id !== attachmentId) }
        : current
    ));
    await loadSessionDetail(sessionId);
  }

  async function runPersistentSessionTask(sessionId, command, onChunk) {
    const response = await fetch(`${SESSIONS_ENDPOINT}/${sessionId}/run/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command)
    });
    let result;
    await readServerSentEvents(response, {
      onChunk: (payload) => {
        if (typeof payload?.content === "string") onChunk?.(payload.content, payload.ordinal);
      },
      onResult: (payload) => {
        result = payload?.result;
      },
      onError: (payload) => {
        throw new Error(nonEmptyText(payload?.message) || "会话生成失败。");
      }
    });
    if (!result) throw new Error("流式响应未返回最终结果。");
    const isCurrent = selectedSessionIdRef.current === sessionId;
    if (isCurrent) await loadSessionDetail(sessionId);
    await loadSessions(sessionFilters);
    return { result, isCurrent };
  }

  async function editPersistentSessionGenerationResult(sessionId, resultId, content) {
    const response = await requestJson(`${SESSIONS_ENDPOINT}/${sessionId}/generation-results/${resultId}`, {
      method: "PATCH",
      body: JSON.stringify({ content, content_origin: "user-content" })
    });
    if (selectedSessionIdRef.current === sessionId) await loadSessionDetail(sessionId);
    await loadSessions(sessionFilters);
    return response.result;
  }

  async function reverifyPersistentSessionGenerationResult(sessionId, resultId) {
    const response = await requestJson(`${SESSIONS_ENDPOINT}/${sessionId}/generation-results/${resultId}/reverify`, {
      method: "POST",
      body: JSON.stringify({})
    });
    if (selectedSessionIdRef.current === sessionId) await loadSessionDetail(sessionId);
    await loadSessions(sessionFilters);
    return response.result;
  }

  async function loadPersistentCompletenessCoverage(sessionId, resultId, offset) {
    const search = new window.URLSearchParams({ offset: String(offset), limit: "100" });
    return requestJson(
      `${SESSIONS_ENDPOINT}/${sessionId}/completeness-results/${resultId}/coverage?${search}`
    );
  }

  async function exportPersistentSession(session) {
    const response = await fetch(`${SESSIONS_ENDPOINT}/${session.session_id}/export`);
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.message || "导出会话失败。");
    }
    const link = document.createElement("a");
    const objectUrl = window.URL.createObjectURL(await response.blob());
    link.href = objectUrl;
    link.download = "会话导出.json";
    link.click();
    window.URL.revokeObjectURL(objectUrl);
  }

  function completeVaultForm(vault) {
    setVaults((current) => {
      const withoutUpdated = current.filter((item) => item.vault_id !== vault.vault_id);
      return vault.is_current ? [vault, ...withoutUpdated.map((item) => ({ ...item, is_current: false }))] : [...withoutUpdated, vault];
    });
    setSelectedVaultId(vault.vault_id);
    setFormVault(undefined);
    loadWorkbenchOverview();
  }

  function openConfirmation(kind, target, trigger) {
    actionTriggerRef.current = trigger;
    setConfirmationError("");
    setConfirmationRequest({ kind, target });
  }

  function closeConfirmation() {
    setConfirmationRequest(null);
    setConfirmationError("");
    actionTriggerRef.current?.focus();
  }

  async function confirmAction() {
    const request = confirmationRequest;
    if (!request) return;
    setConfirmationSubmitting(true);
    try {
      if (request.kind === "provider-remove") {
        await requestJson(`${PROVIDERS_ENDPOINT}/${request.target.provider_id}`, { method: "DELETE" });
        setProviders((current) => current.filter((item) => item.provider_id !== request.target.provider_id));
        await loadModelDefaults();
      } else if (request.kind === "provider-model-remove") {
        const query = new window.URLSearchParams({ model_id: request.target.model_id });
        const response = await requestJson(
          `${PROVIDERS_ENDPOINT}/${request.target.provider_id}/models?${query}`,
          { method: "DELETE" }
        );
        setProviders((current) => current.map((item) => (
          item.provider_id === request.target.provider_id ? response.provider : item
        )));
        await loadModelDefaults();
      } else if (request.kind === "session-remove") {
        await requestJson(`${SESSIONS_ENDPOINT}/${request.target.session_id}`, { method: "DELETE" });
        const updated = await loadSessions(sessionFilters);
        if (updated && updated.sessions.length === 0 && updated.page > 1) {
          await loadSessions({ ...sessionFilters, page: updated.page - 1 });
        }
      } else if (request.kind === "remove") {
        await requestJson(`${VAULTS_ENDPOINT}/${request.target.vault_id}`, { method: "DELETE" });
        setVaults((current) => current.filter((item) => item.vault_id !== request.target.vault_id));
        setSelectedVaultId(null);
        await loadWorkbenchOverview();
      } else {
        const response = await requestJson(`${VAULTS_ENDPOINT}/${request.target.vault_id}/deactivate`, { method: "POST" });
        updateVault(response.vault);
      }
      closeConfirmation();
    } catch (error) {
      if (request.kind === "session-remove") {
        closeConfirmation();
        setSessionsError(error.message);
      } else {
        setConfirmationError(error.message);
      }
    } finally {
      setConfirmationSubmitting(false);
    }
  }

  let workspaceContent;
  if (formVault !== undefined) {
    workspaceContent = React.createElement(VaultForm, {
      vault: formVault,
      onCancel: () => setFormVault(undefined),
      onComplete: completeVaultForm
    });
  } else if (providerForm !== undefined) {
    workspaceContent = React.createElement(ProviderForm, {
      provider: providerForm,
      onCancel: () => setProviderForm(undefined),
      onComplete: async (provider) => {
        setProviders((current) => {
          const withoutUpdated = current.filter((item) => item.provider_id !== provider.provider_id);
          return [...withoutUpdated, provider];
        });
        setProviderForm(undefined);
        await loadModelDefaults();
      }
    });
  } else if (activeDestination === "settings") {
    workspaceContent = React.createElement(
      React.Fragment,
      null,
      React.createElement(ProviderManagement, {
        providers,
        isLoading: providersLoading,
        modelDefaults,
        onOpenForm: setProviderForm,
        onUpdate: (provider) => setProviders((current) => current.map((item) => item.provider_id === provider.provider_id ? provider : item)),
        onConfirm: openConfirmation,
        onDefaultsChange: loadModelDefaults
      }),
      React.createElement(VaultManagement, {
        activeDestination: activePage.label,
        vaults,
        isLoading: vaultsLoading,
        selectedVault,
        onSelect: setSelectedVaultId,
        onOpenForm: setFormVault,
        onUpdate: updateVault,
        onConfirm: openConfirmation
      })
    );
  } else if (activeDestination === "tasks") {
    workspaceContent = React.createElement(ImportTaskCenter, {
      tasks,
      error: tasksError,
      isLoading: tasksLoading,
      selectedTaskId,
      onSelect: setSelectedTaskId,
      onTaskChanged: updateTask,
      onTaskDeleted: deleteTask,
      onTaskSnapshot: syncTask,
      vault: currentVault
    });
  } else if (activeDestination === "sessions") {
    workspaceContent = React.createElement(SessionManagement, {
      sessionPage,
      filters: sessionFilters,
      isLoading: sessionsLoading,
      error: sessionsError,
      selectedSessionId,
      selectedDetail: selectedSessionDetail,
      isDetailLoading: sessionDetailLoading,
      detailError: sessionDetailError,
      onLoad: loadSessions,
      onSelect: (session) => {
        selectedSessionIdRef.current = session.session_id;
        setSelectedSessionId(session.session_id);
      },
      onCreate: createPersistentSession,
      onRename: renamePersistentSession,
      onExport: exportPersistentSession,
      onDelete: (session, trigger) => openConfirmation("session-remove", session, trigger),
      vaults,
      providers,
      onPickAttachments: pickPersistentSessionAttachments,
      onRemoveAttachment: removePersistentSessionAttachment,
      onRun: runPersistentSessionTask,
      retrievalMode,
      retrievalModeLoading,
      onRetrievalModeChange: changeRetrievalMode,
      onLoadCompletenessCoverage: loadPersistentCompletenessCoverage,
      onEditGenerationResult: editPersistentSessionGenerationResult,
      onReverifyGenerationResult: reverifyPersistentSessionGenerationResult
    });
  } else if (activeDestination === "workbench") {
    workspaceContent = React.createElement(WorkbenchOverview, {
      overview: workbenchOverview,
      isLoading: workbenchOverviewLoading,
      error: workbenchOverviewError,
      selectedVaultId: selectedWorkbenchVaultId,
      onSelectVault: setSelectedWorkbenchVaultId,
      onRefresh: loadWorkbenchOverview,
      onNavigate: navigate
    });
  } else if (VAULT_SURFACES.has(activeDestination)) {
    workspaceContent = React.createElement(
      React.Fragment,
      null,
      React.createElement(ImportTaskLauncher, { vault: currentVault, onCreated: updateTask }),
      React.createElement(VaultManagement, {
        activeDestination: activePage.label,
        vaults,
        isLoading: vaultsLoading,
        selectedVault,
        onSelect: setSelectedVaultId,
        onOpenForm: setFormVault,
        onUpdate: updateVault,
        onConfirm: openConfirmation
      })
    );
  } else {
    workspaceContent = React.createElement(
      "section",
      { className: "workspace-section", "aria-label": `${activePage.label}状态` },
      React.createElement("p", { className: "section-label" }, "当前状态"),
      React.createElement("p", { className: "empty-state" }, activePage.emptyState)
    );
  }

  const contextSession = activeDestination === "sessions"
    && selectedSessionDetail?.session?.session_id === selectedSessionId
    ? selectedSessionDetail.session
    : null;
  const contextSessionVault = contextSession
    ? vaults.find((vault) => vault.vault_id === contextSession.selected_vault_id)
    : null;
  const contextLocation = contextSession ? "会话" : "本机工作区";
  const contextVaultName = contextVault ? vaultName(contextVault) : null;
  const contextOutbound = contextSessionVault
    ? `外发：${outboundModeText(policyFor(contextSessionVault).outbound_mode)}`
    : currentPolicy ? `外发：${outboundModeText(currentPolicy.outbound_mode)}` : null;

  return React.createElement(
    "div",
    { className: "app-shell" },
    React.createElement(
      "aside",
      { className: "navigation-rail", "aria-label": "主导航" },
      React.createElement("div", { className: "brand" }, "本机知识工作台"),
      React.createElement(
        "nav",
        { "aria-label": "工作区目的地" },
        React.createElement(NavigationLinks, { activeDestination, onNavigate: navigate })
      ),
      React.createElement("p", { className: "rail-status" }, "仅限本机访问")
    ),
    React.createElement(
      "section",
      { className: `application-content${activeDestination === "sessions" ? " application-content-sessions" : ""}` },
      React.createElement(
        "header",
        { className: "context-bar" },
        React.createElement(IconButton, {
          icon: Menu,
          label: "打开导航",
          className: "icon-button menu-button",
          type: "button",
          ref: menuButtonRef,
          "aria-controls": "mobile-navigation-panel",
          "aria-expanded": menuOpen,
          onClick: () => setMenuOpen(true)
        }),
          React.createElement(
            "p",
            { className: "context-location" },
          contextLocation
        ),
        React.createElement(
          "div",
          { className: "context-actions" },
          contextVaultName
            ? React.createElement("span", { className: "context-vault-marker", "aria-label": `当前 Vault：${contextVaultName}` }, contextVaultName)
            : null,
          React.createElement(
            "div",
            { className: "context-statuses", "aria-live": "polite" },
            React.createElement("span", { "data-testid": "health-status" }, healthStatus),
            React.createElement("span", { "data-testid": "session-status" }, sessionStatus),
            contextOutbound
              ? React.createElement("span", { "data-testid": "outbound-status" }, contextOutbound)
              : null
          )
        )
      ),
      React.createElement(
        "main",
        {
          className: `workspace${activeDestination === "sessions" ? " session-workspace" : ""}`,
          ...(activeDestination === "sessions"
            ? { "aria-label": "会话工作区" }
            : { "aria-labelledby": "workspace-title" })
        },
        activeDestination === "sessions"
          ? workspaceContent
          : React.createElement(
              "div",
              { className: "workspace-inner" },
              React.createElement("h1", { id: "workspace-title" }, activePage.label),
              workspaceContent
            )
      )
    ),
    React.createElement(
      "div",
      { className: "navigation-overlay", hidden: !menuOpen },
      React.createElement(
        "aside",
        {
          className: "navigation-panel",
          id: "mobile-navigation-panel",
          ref: menuPanelRef,
          role: "dialog",
          "aria-label": "主导航",
          "aria-modal": "true",
          onKeyDown: handleMenuKeyDown
        },
        React.createElement("p", { className: "brand" }, "本机知识工作台"),
        React.createElement(
          "nav",
          { "aria-label": "工作区目的地" },
          React.createElement(NavigationLinks, {
            activeDestination,
            firstLinkRef: firstMenuLinkRef,
            onNavigate: navigate
          })
        ),
        React.createElement(IconButton, { icon: X, label: "关闭导航", className: "icon-button panel-close", type: "button", onClick: closeMenu })
      )
    ),
      confirmationRequest
        ? React.createElement(ConfirmationPanel, {
            request: confirmationRequest,
            error: confirmationError,
            isSubmitting: confirmationSubmitting,
            onClose: closeConfirmation,
            onConfirm: confirmAction
          })
      : null
  );
}
