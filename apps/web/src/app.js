import React from "react";

export const HEALTH_ENDPOINT = "/api/health";
export const LOCAL_SESSION_ENDPOINT = "/api/session";
export const VAULTS_ENDPOINT = "/api/vaults";
export const VAULT_DIRECTORY_PICKER_ENDPOINT = "/api/vaults/select-directory";
export const PROVIDERS_ENDPOINT = "/api/providers";
export const SESSIONS_ENDPOINT = "/api/sessions";
export const IMPORT_TASKS_ENDPOINT = "/api/import-tasks";
export const IMPORT_FILES_SELECTION_ENDPOINT = "/api/import-selections/files";
export const IMPORT_DIRECTORY_SELECTION_ENDPOINT = "/api/import-selections/directory";
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
];
export const NAVIGATION_DESTINATIONS = [
  { id: "workbench", label: "工作台", emptyState: "尚未选择 vault。" },
  { id: "materials", label: "资料", emptyState: "当前没有已授权的 vault。" },
  { id: "sessions", label: "会话", emptyState: "当前没有已保存的会话。" },
  { id: "tasks", label: "任务", emptyState: "当前没有任务。" },
  { id: "settings", label: "设置", emptyState: "当前没有可用设置。" }
];

const VAULT_SURFACES = new Set(["workbench", "materials"]);
const IMPORT_PROGRESS_PHASES = ["queued", "scanning", "converting", "parsing", "ocr", "deriving-markdown", "waiting-for-review", "committing", "indexing"];

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
    "waiting-for-review": "等待审核",
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
    docx: "DOCX",
    markdown: "外部 Markdown"
  }[kind] || "未识别";
}

function importRecoveryActionText(action) {
  return {
    cancel: "取消",
    "restart-scan": "重新扫描",
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
    rejected: "转换需审核"
  }[status] || "转换未就绪";
}

function importOcrStatusText(status) {
  return {
    "not-applicable": "不适用",
    "not-required": "无需 OCR",
    "ocr-processing": "OCR 中",
    "ocr-completed": "OCR 完成",
    "ocr-failed": "OCR 失败",
    "required-check": "OCR 待审核",
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
  if (!blockId) return { error: "请提供要替换的转换块 ID。" };
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
  if (locator.type === "docx-ooxml") return `DOCX ${locator.element_path}`;
  if (locator.type === "source-scope") return `来源范围：${locator.scope}`;
  return locator.page ? `第 ${locator.page} 页` : locator.docx_location || locator.region || "未定位";
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
            "aria-label": "要修正的转换块 ID"
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
          "aria-label": "要修正的转换块 ID",
          placeholder: "转换块 ID"
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
    outbound_mode: "ask-each-task",
    policy_revision: 1,
    rules: []
  };
}

function outboundModeText(mode) {
  return mode === "always-allow" ? "始终允许" : "每次询问";
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
  return fetch(endpoint, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers
    }
  }).then(async (response) => {
    const payload = response.status === 204 ? {} : await response.json();
    if (!response.ok) throw new Error(payload.message || "请求未完成。");
    return payload;
  });
}

function vaultName(vault) {
  return vault.path.replace(/\\/g, "/").split("/").at(-1) || vault.path;
}

function statusText(vault) {
  if (vault.authorization_status === "inactive") return "已停用";
  if (vault.access_status !== "available") return "路径不可用";
  return "已授权";
}

function providerStatus(provider) {
  if (provider.verification.is_verified) return "已验证";
  const failed = Object.values(provider.verification).find(
    (probe) => probe && typeof probe === "object" && probe.ok === false && probe.reason !== "Not yet verified."
  );
  return failed?.reason || "等待验证";
}

function modelOptions(providers, modelType) {
  return providers.flatMap((provider) => (
    provider.verification.is_verified && provider.credential_configured
      ? provider.models
        .filter((model) => model.model_type === modelType && model.verification.ok && model.is_discovered)
        .map((model) => ({ provider, model }))
      : []
  ));
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
      destination.label
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
  const isSessionRemoval = request.kind === "session-remove";
  const isRemoval = request.kind === "remove" || isProviderRemoval;
  const targetName = isSessionRemoval
    ? request.target.title
    : isProviderRemoval ? request.target.name : vaultName(request.target);
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
        isSessionRemoval ? `删除会话“${targetName}”？` : isProviderRemoval ? "删除 Provider" : isRemoval ? "移除 vault 授权" : "停用 vault"
      ),
      React.createElement(
        "p",
        null,
        isSessionRemoval
          ? "这会删除该会话的私有消息、范围、模型记录、任务状态、引用和结果。不会删除、移动或改写已审核写入 vault 的资料、笔记或标签。"
          : isProviderRemoval
          ? `将删除“${targetName}”的应用内配置、模型缓存和 Windows 凭据，并使关联外发授权失效。`
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
          isSessionRemoval ? "删除会话" : isProviderRemoval ? "删除 Provider" : isRemoval ? "移除授权" : "停用"
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
  onLoad,
  onCreate,
  onRename,
  onExport,
  onDelete
}) {
  const [query, setQuery] = React.useState(filters.query || "");
  const [editingSessionId, setEditingSessionId] = React.useState(null);
  const [editingTitle, setEditingTitle] = React.useState("");
  const [status, setStatus] = React.useState("");
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const renameInputRef = React.useRef(null);
  const page = sessionPage?.page || 1;
  const totalPages = sessionPage?.total_pages || 1;
  const sessions = sessionPage?.sessions || [];

  React.useEffect(() => {
    setQuery(filters.query || "");
  }, [filters.query]);

  React.useEffect(() => {
    if (editingSessionId) renameInputRef.current?.focus();
  }, [editingSessionId]);

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

  if (isLoading && !sessions.length) {
    return React.createElement("p", { className: "empty-state", role: "status" }, "正在加载会话。");
  }

  return React.createElement(
    "section",
    { className: "workspace-section session-management", "aria-label": "持久会话" },
    React.createElement("p", { className: "section-label" }, "持久会话"),
    React.createElement(
      "div",
      { className: "session-toolbar" },
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
          placeholder: "按标题或所用 vault 搜索"
        }),
        React.createElement("button", { className: "secondary-button", type: "submit", disabled: isSubmitting }, "搜索")
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
      React.createElement(
        "button",
        { className: "primary-button", type: "button", disabled: isSubmitting, onClick: createSession },
        "新建会话"
      )
    ),
    error || status
      ? React.createElement("p", { className: "form-error", role: "alert" }, error || status)
      : null,
    sessions.length
      ? React.createElement(
          "div",
          { className: "session-list" },
          sessions.map((session) => React.createElement(
            "article",
            { className: "section-row session-row", key: session.session_id },
            editingSessionId === session.session_id
              ? React.createElement(
                  "form",
                  { className: "session-rename", onSubmit: (event) => saveRename(event, session.session_id) },
                  React.createElement("input", {
                    ref: renameInputRef,
                    value: editingTitle,
                    onChange: (event) => setEditingTitle(event.target.value),
                    "aria-label": `${session.title} 的会话标题`
                  }),
                  React.createElement("button", { className: "secondary-button", type: "button", disabled: isSubmitting, onClick: () => setEditingSessionId(null) }, "取消"),
                  React.createElement("button", { className: "primary-button", type: "submit", disabled: isSubmitting }, "保存")
                )
              : React.createElement(
                  React.Fragment,
                  null,
                  React.createElement(
                    "div",
                    { className: "session-row-summary" },
                    React.createElement("strong", null, session.title),
                    React.createElement("span", null, `所用 vault：${session.selected_vault_label || "未设置"}`),
                    React.createElement("span", null, `消息 ${session.message_count || 0} 条`)
                  ),
                  React.createElement(
                    "div",
                    { className: "session-row-actions" },
                    React.createElement("button", { className: "text-button", type: "button", onClick: () => openRename(session) }, "重命名"),
                    React.createElement("button", { className: "text-button", type: "button", onClick: () => exportSession(session) }, "导出"),
                    React.createElement(
                      "button",
                      { className: "text-button danger-text-button", type: "button", onClick: (event) => onDelete(session, event.currentTarget) },
                      "删除"
                    )
                  )
                )
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

  async function changeMode(event) {
    const outboundMode = event.target.value;
    setStatus("");
    try {
      const response = await requestJson(`${policyEndpoint(vault.vault_id)}/mode`, {
        method: "PUT",
        body: JSON.stringify({ outbound_mode: outboundMode })
      });
      updatePolicy(response.policy);
      setStatus(outboundMode === "always-allow" ? "外发已设为始终允许。规则仍会阻止受限内容。" : "外发已改为每次询问；未执行授权已失效。");
    } catch (error) {
      setStatus(error.message);
    }
  }

  async function removeRule(rule) {
    setStatus("");
    try {
      await requestJson(`${policyEndpoint(vault.vault_id)}/rules/${rule.rule_id}`, { method: "DELETE" });
      const response = await requestJson(policyEndpoint(vault.vault_id));
      updatePolicy(response.policy);
      setStatus("规则已删除；未执行授权已失效。");
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
    setStatus("规则已保存；未执行授权已失效。");
  }

  return React.createElement(
    "section",
    { className: "policy-controls", "aria-labelledby": "vault-policy-heading" },
    React.createElement("h3", { id: "vault-policy-heading" }, "资料排除与外发授权"),
    React.createElement(
      "label",
      { className: "policy-mode-row", htmlFor: "outbound-mode" },
      React.createElement("span", { className: "form-label" }, "外发方式"),
      React.createElement(
        "select",
        { id: "outbound-mode", value: policy.outbound_mode, onChange: changeMode },
        React.createElement("option", { value: "ask-each-task" }, "每次询问"),
        React.createElement("option", { value: "always-allow" }, "始终允许")
      ),
      React.createElement("span", { className: "form-help" }, "同时适用于 cloud Model 与 web search；绝不发送到云端的规则始终优先。")
    ),
    React.createElement(
      "div",
      { className: "policy-summary", "aria-live": "polite" },
      `当前：${outboundModeText(policy.outbound_mode)}；策略修订 ${policy.policy_revision}。`
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
      ? React.createElement("p", { className: "empty-state" }, "尚无排除规则。默认外发方式为每次询问。")
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

export function TagManagement({ vault }) {
  const [tags, setTags] = React.useState([]);
  const [search, setSearch] = React.useState("");
  const [newTag, setNewTag] = React.useState("");
  const [operation, setOperation] = React.useState("rename");
  const [sourceTag, setSourceTag] = React.useState("");
  const [targetTag, setTargetTag] = React.useState("");
  const [preview, setPreview] = React.useState(null);
  const [status, setStatus] = React.useState("");
  const [isActing, setIsActing] = React.useState(false);

  const loadTags = React.useCallback(async (term = search) => {
    setIsActing(true);
    try {
      const response = await requestJson(`${VAULTS_ENDPOINT}/${vault.vault_id}/tags?search=${encodeURIComponent(term)}`);
      setTags(response.tags);
      setStatus("");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }, [search, vault.vault_id]);

  React.useEffect(() => {
    void loadTags("");
  }, [loadTags]);

  async function createTag() {
    if (!newTag.trim() || isActing) return;
    setIsActing(true);
    try {
      const response = await requestJson(`${VAULTS_ENDPOINT}/${vault.vault_id}/tags`, {
        method: "POST",
        body: JSON.stringify({ name: newTag })
      });
      setTags((current) => [...current, response.tag].sort((left, right) => left.name.localeCompare(right.name)));
      setNewTag("");
      setStatus("标签已加入应用私有目录；尚未写入任何 Markdown。 ");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  async function previewChange() {
    if (!sourceTag.trim() || ((operation === "rename" || operation === "merge") && !targetTag.trim()) || isActing) return;
    setIsActing(true);
    try {
      const response = await requestJson(`${VAULTS_ENDPOINT}/${vault.vault_id}/tags/change-preview`, {
        method: "POST",
        body: JSON.stringify({
          operation,
          source_tag: sourceTag,
          target_tag: operation === "deactivate" || operation === "delete" ? null : targetTag
        })
      });
      setPreview(response.preview);
      setStatus(response.preview.conflicts.length ? "预览包含冲突；请先修正后再进入后续审核。" : "已生成私有影响预览；不会写入 vault。 ");
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  async function applyChange() {
    if (!preview || preview.is_stale || preview.conflicts.length || isActing) return;
    setIsActing(true);
    try {
      await requestJson(`${VAULTS_ENDPOINT}/${vault.vault_id}/tags/change`, {
        method: "POST",
        body: JSON.stringify({
          operation: preview.operation,
          source_tag: preview.source_tag,
          target_tag: preview.target_tag,
          catalog_revision: preview.catalog_revision,
          proposal_versions: preview.proposal_versions
        })
      });
      await loadTags("");
      setPreview(null);
      setStatus(
        preview.operation === "delete"
          ? "标签及其私有提案引用已移除；不会修改 vault Markdown。"
          : "已更新应用私有标签提案；所有受影响 Markdown 仍需后续审核提交。 "
      );
    } catch (error) {
      setStatus(error.message);
    } finally {
      setIsActing(false);
    }
  }

  return React.createElement(
    "section",
    { className: "tag-management", "aria-labelledby": `tag-management-${vault.vault_id}` },
    React.createElement("h3", { id: `tag-management-${vault.vault_id}` }, "标签管理"),
    React.createElement("p", { className: "row-note" }, "标签变更先生成私有影响预览；实际 Markdown 写入仍需后续审核提交。"),
    React.createElement(
      "div",
      { className: "tag-controls" },
      React.createElement("input", {
        type: "search",
        value: search,
        onChange: (event) => setSearch(event.target.value),
        placeholder: "搜索标签",
        "aria-label": "搜索 vault 标签"
      }),
      React.createElement("button", { className: "secondary-button", type: "button", disabled: isActing, onClick: () => loadTags() }, "搜索"),
      React.createElement("input", {
        type: "text",
        value: newTag,
        onChange: (event) => setNewTag(event.target.value),
        placeholder: "新标签",
        "aria-label": "新建 vault 标签"
      }),
      React.createElement("button", { className: "secondary-button", type: "button", disabled: isActing || !newTag.trim(), onClick: createTag }, "新建标签")
    ),
    React.createElement(
      "div",
      { className: "tag-controls tag-change-controls" },
      React.createElement(
        "select",
        {
          value: operation,
          onChange: (event) => {
            setOperation(event.target.value);
            setPreview(null);
            setStatus("");
          },
          "aria-label": "标签变更类型"
        },
        React.createElement("option", { value: "rename" }, "重命名"),
        React.createElement("option", { value: "merge" }, "合并"),
        React.createElement("option", { value: "deactivate" }, "停用"),
        React.createElement("option", { value: "delete" }, "删除")
      ),
      React.createElement("input", {
        type: "text",
        value: sourceTag,
        onChange: (event) => {
          setSourceTag(event.target.value);
          setPreview(null);
          setStatus("");
        },
        placeholder: "当前标签",
        "aria-label": "当前标签"
      }),
      operation !== "deactivate" && operation !== "delete"
        ? React.createElement("input", {
            type: "text",
            value: targetTag,
            onChange: (event) => {
              setTargetTag(event.target.value);
              setPreview(null);
              setStatus("");
            },
            placeholder: "目标标签",
            "aria-label": "目标标签"
          })
        : null,
      React.createElement("button", { className: "secondary-button", type: "button", disabled: isActing, onClick: previewChange }, "检查标签影响")
    ),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null,
    tags.length === 0
      ? React.createElement("p", { className: "empty-state" }, "当前没有匹配的私有标签目录记录。")
      : React.createElement(
          "div",
          { className: "tag-list", "aria-label": "vault 标签目录" },
          tags.map((tag) => React.createElement(
            "div",
            { className: "section-row tag-row", key: tag.name },
            React.createElement("span", { className: "row-title" }, tag.name),
            React.createElement("span", { className: "row-note" }, `使用数：${tag.usage_count}`),
            React.createElement("span", { className: "row-status" }, tag.status === "active" ? "可用" : "已停用")
          ))
        ),
    preview
      ? React.createElement(
          "div",
          { className: "tag-impact-preview", "aria-label": "标签变更影响预览" },
          React.createElement("p", { className: preview.is_stale || preview.conflicts.length ? "row-status status-danger" : "row-status" }, preview.is_stale ? `预览已陈旧：${preview.stale_reason}` : `受影响 Markdown：${preview.affected_paths.length}`),
          preview.conflicts.map((conflict) => React.createElement("p", { className: "row-note", key: conflict }, conflict)),
          preview.affected_paths.map((path) => React.createElement("p", { className: "row-note", key: path }, path)),
          React.createElement("button", {
            className: "secondary-button",
            type: "button",
            disabled: isActing || preview.is_stale || preview.conflicts.length > 0,
            title: preview.is_stale ? preview.stale_reason : preview.conflicts[0],
            onClick: applyChange
          }, preview.operation === "delete" ? "确认删除标签" : "确认标签变更")
        )
      : null
  );
}

export function VaultIndexStatus({ vault, onUpdate }) {
  const [status, setStatus] = React.useState("");
  const [isActing, setIsActing] = React.useState(false);
  const index = vault.index || {
    status: vault.index_status || "not-initialized",
    updated_at: null,
    current_count: 0,
    stale_count: 0,
    failure_count: 0,
    semantic_status: "unavailable",
    failed_paths: [],
    stale_paths: [],
    stale_details: [],
    pending_count: 0,
    pending_paths: []
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

export function KnowledgeGraphWorkbench({ vaults, currentVault, isLoading, onAddVault, onUpdateVault }) {
  const [graph, setGraph] = React.useState(null);
  const [status, setStatus] = React.useState("");
  const [directory, setDirectory] = React.useState("");
  const [tag, setTag] = React.useState("");
  const [source, setSource] = React.useState("");
  const [relationshipState, setRelationshipState] = React.useState("");
  const [selected, setSelected] = React.useState(null);
  const [refreshRevision, setRefreshRevision] = React.useState(0);
  const graphRequestRevision = React.useRef(0);
  const vaultSwitchRevision = React.useRef(0);

  React.useEffect(() => {
    const requestRevision = ++graphRequestRevision.current;
    if (!currentVault) {
      setGraph(null);
      setSelected(null);
      return undefined;
    }
    const parameters = new window.URLSearchParams();
    if (directory) parameters.set("directory", directory);
    if (tag) parameters.set("tag", tag);
    if (source) parameters.set("source", source);
    if (relationshipState) parameters.set("relationship_state", relationshipState);
    let active = true;
    setGraph(null);
    setSelected(null);
    setStatus("正在读取当前 vault 图谱。");
    requestJson(`${VAULTS_ENDPOINT}/${currentVault.vault_id}/graph?${parameters}`)
      .then((response) => {
        if (
          !active
          || requestRevision !== graphRequestRevision.current
          || response.graph.vault_id !== currentVault.vault_id
        ) return;
        setGraph(response.graph);
        setStatus(response.graph.index.status === "healthy" ? "图谱已更新。" : "图谱包含不完整的索引状态。");
      })
      .catch((error) => {
        if (!active || requestRevision !== graphRequestRevision.current) return;
        setGraph(null);
        setStatus(error.message);
      });
    return () => { active = false; };
  }, [currentVault?.vault_id, directory, tag, source, relationshipState, refreshRevision]);

  React.useEffect(() => {
    if (!currentVault) return undefined;
    const eventSource = new window.EventSource(`${VAULTS_ENDPOINT}/${currentVault.vault_id}/graph/events`);
    eventSource.addEventListener("graph-refresh", () => {
      setStatus("图谱状态已变化，正在刷新。");
      setRefreshRevision((current) => current + 1);
    });
    return () => eventSource.close();
  }, [currentVault?.vault_id]);

  if (isLoading) {
    return React.createElement("p", { className: "empty-state", role: "status" }, "正在加载 vault 授权。");
  }
  if (!currentVault) {
    return React.createElement(
      "section",
      { className: "workspace-section graph-empty", "aria-label": "知识图谱" },
      React.createElement("p", { className: "section-label" }, "当前 vault 图谱"),
      React.createElement("p", { className: "empty-state" }, "添加 vault 后即可浏览已提交的知识图谱。"),
      React.createElement("button", { className: "primary-button", type: "button", onClick: onAddVault }, "添加 vault")
    );
  }

  async function selectVault(event) {
    const vaultId = event.target.value;
    if (!vaultId || vaultId === currentVault.vault_id) return;
    const switchRevision = ++vaultSwitchRevision.current;
    setGraph(null);
    setSelected(null);
    setStatus("正在切换当前 vault 并替换图谱。");
    try {
      const response = await requestJson(`${VAULTS_ENDPOINT}/${vaultId}/current`, { method: "POST" });
      if (switchRevision !== vaultSwitchRevision.current) return;
      setDirectory("");
      setTag("");
      setSource("");
      setRelationshipState("");
      onUpdateVault(response.vault);
    } catch (error) {
      if (switchRevision !== vaultSwitchRevision.current) return;
      setStatus(error.message);
    }
  }

  function clearFilters() {
    setDirectory("");
    setTag("");
    setSource("");
    setRelationshipState("");
  }

  function updateIndexVault(vault) {
    onUpdateVault(vault);
    setStatus("索引状态已更新，正在刷新图谱。");
    setRefreshRevision((current) => current + 1);
  }

  const currentGraph = graph?.vault_id === currentVault.vault_id ? graph : null;
  const index = currentGraph?.index || currentVault.index || { status: "not-initialized" };
  const indexSummary = index.status === "healthy"
    ? "索引健康，图谱只显示当前可验证的已提交知识。"
    : `索引状态：${index.status}；此图谱不是当前完整知识视图。`;
  const selectedNode = selected?.type === "node" ? currentGraph?.nodes.find((node) => node.relative_path === selected.path) : null;
  const selectedEdge = selected?.type === "edge" ? currentGraph?.edges[selected.index] : null;

  return React.createElement(
    "section",
    { className: "knowledge-graph-workbench", "aria-label": "知识图谱" },
    React.createElement("p", { className: "scope-summary" }, `当前 vault：${vaultName(currentVault)}；图谱筛选不会改变会话、任务或检索范围。`),
    React.createElement(
      "div",
      { className: "graph-filter-bar" },
      React.createElement(
        "label",
        null,
        "当前 vault",
        React.createElement("select", { "aria-label": "当前 vault", value: currentVault.vault_id, onChange: selectVault }, vaults.map((vault) => React.createElement("option", { key: vault.vault_id, value: vault.vault_id }, vaultName(vault))))
      ),
      React.createElement(
        "label",
        null,
        "目录",
        React.createElement("select", { "aria-label": "按目录筛选图谱", value: directory, onChange: (event) => setDirectory(event.target.value) }, [React.createElement("option", { key: "all", value: "" }, "全部目录"), ...(currentGraph?.directories || []).map((value) => React.createElement("option", { key: value, value }, value))])
      ),
      React.createElement(
        "label",
        null,
        "标签",
        React.createElement("select", { "aria-label": "按标签筛选图谱", value: tag, onChange: (event) => setTag(event.target.value) }, [React.createElement("option", { key: "all", value: "" }, "全部标签"), ...(currentGraph?.tags || []).map((value) => React.createElement("option", { key: value, value }, value))])
      ),
      React.createElement(
        "label",
        null,
        "来源",
        React.createElement("select", { "aria-label": "按来源筛选图谱", value: source, onChange: (event) => setSource(event.target.value) }, React.createElement("option", { value: "" }, "全部来源"), React.createElement("option", { value: "native" }, "原生 Markdown"), React.createElement("option", { value: "derived" }, "派生资料"))
      ),
      React.createElement(
        "label",
        null,
        "关系状态",
        React.createElement("select", { "aria-label": "按关系状态筛选图谱", value: relationshipState, onChange: (event) => setRelationshipState(event.target.value) }, React.createElement("option", { value: "" }, "全部关系"), React.createElement("option", { value: "confirmed" }, "已确认"), React.createElement("option", { value: "candidate" }, "候选"))
      ),
      React.createElement("button", { className: "secondary-button", type: "button", onClick: clearFilters }, "清除筛选")
    ),
    React.createElement("p", { className: "graph-status", role: "status", "aria-live": "polite" }, status),
    React.createElement("p", { className: `status-marker graph-index-${index.status}` }, indexSummary),
    React.createElement(VaultIndexStatus, { vault: { ...currentVault, index }, onUpdate: updateIndexVault }),
    currentGraph && !currentGraph.nodes.length
      ? React.createElement("p", { className: "empty-state" }, "没有符合当前筛选的图谱节点。")
      : null,
    currentGraph
      ? React.createElement(
        "div",
        { className: "knowledge-graph" },
        React.createElement(
          "section",
          { "aria-label": "图谱节点" },
          React.createElement("h2", null, "节点"),
          React.createElement("p", { className: "row-note" }, "节点均为当前 vault 中已提交、可验证的 Markdown。"),
          React.createElement("ul", { className: "graph-node-list" }, currentGraph.nodes.map((node) => React.createElement("li", { key: node.relative_path }, React.createElement("button", { className: "graph-node", type: "button", "aria-expanded": selectedNode?.relative_path === node.relative_path, onClick: () => setSelected({ type: "node", path: node.relative_path }) }, node.title, React.createElement("span", null, `目录：${node.directory}；来源：${node.source === "native" ? "原生" : "派生"}`))))),
        ),
        React.createElement(
          "section",
          { "aria-label": "图谱关系" },
          React.createElement("h2", null, "关系"),
          React.createElement("p", { className: "row-note" }, "已确认：实线；候选：虚线。候选关系尚未写入 vault。"),
          React.createElement("ul", { className: "graph-edge-list" }, currentGraph.edges.map((edge, indexValue) => React.createElement("li", { key: `${edge.kind}:${edge.source_path}:${edge.target_path}:${edge.review_item_id || ""}` }, React.createElement("button", { className: `graph-edge graph-edge-${edge.kind}`, type: "button", "aria-expanded": selectedEdge === edge, onClick: () => setSelected({ type: "edge", index: indexValue }) }, `${edge.kind === "confirmed" ? "已确认（实线）" : "候选（虚线）"}：${edge.source_path} -> ${edge.target_path}`))))
        )
      )
      : null,
    selectedNode ? React.createElement("section", { className: "graph-detail", "aria-label": "节点详情", tabIndex: -1 }, React.createElement("h2", null, selectedNode.title), React.createElement("p", null, `路径：${selectedNode.relative_path}`), React.createElement("p", null, `标签：${selectedNode.tags.join("、") || "无"}`)) : null,
    selectedEdge ? React.createElement("section", { className: "graph-detail", "aria-label": "关系详情", tabIndex: -1 }, React.createElement("h2", null, selectedEdge.kind === "confirmed" ? "已确认关系" : "候选关系"), React.createElement("p", null, `${selectedEdge.source_path} -> ${selectedEdge.target_path}`), selectedEdge.kind === "candidate" ? React.createElement(React.Fragment, null, React.createElement("p", null, `审核项：${selectedEdge.review_item_id}；状态：${selectedEdge.status}`), React.createElement("p", null, `理由：${selectedEdge.reason}`), React.createElement("p", null, `证据位置：${selectedEdge.evidence.map((evidence) => `${evidence.relative_path} ${evidence.location}`).join("；")}`)) : null) : null
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
      React.createElement("dd", null, "文件、标签、候选链接、索引和操作状态仅属于此 vault。")
    ),
    React.createElement(VaultIndexStatus, { vault, onUpdate }),
    React.createElement(VaultPolicyControls, { vault, onUpdate }),
    React.createElement(TagManagement, { vault }),
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
  const [status, setStatus] = React.useState("");
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const secretRef = React.useRef(null);
  const isEditing = Boolean(provider);

  async function submit(event) {
    event.preventDefault();
    const secret = secretRef.current?.value || "";
    if (!isEditing && !secret) {
      setStatus("请输入 Provider 凭据。");
      return;
    }
    setStatus("");
    setIsSubmitting(true);
    try {
      const payload = { name, endpoint };
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
    React.createElement("p", { className: "form-description" }, "仅支持 OpenAI-compatible 服务。凭据只保存到 Windows Credential Manager。"),
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
      { className: "form-row", htmlFor: "provider-secret" },
      React.createElement("span", { className: "form-label" }, isEditing ? "替换凭据（可选）" : "凭据"),
      React.createElement("input", { id: "provider-secret", type: "password", ref: secretRef, autoComplete: "new-password", disabled: isSubmitting }),
      React.createElement("span", { className: "form-help" }, isEditing ? "留空会保留当前凭据。提交后此字段会立即清空。" : "提交后此字段会立即清空。")
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

function ProviderVerification({ provider }) {
  const labels = {
    discovery: "模型发现",
    health: "服务健康"
  };
  return React.createElement(
    "div",
    { className: "provider-verification", "aria-live": "polite" },
    Object.entries(labels).map(([key, label]) => {
      const probe = provider.verification[key];
      return React.createElement(
        "p",
        { key, className: probe.ok ? "provider-check provider-check-ok" : "provider-check provider-check-failed" },
        `${label}：${probe.ok ? "通过" : probe.reason || "未验证"}`
      );
    })
  );
}

function ModelDefaultSelector({ modelType, label, description, providers, modelDefault, onChange, onClear }) {
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
    React.createElement("span", { className: "form-label" }, label),
    React.createElement(
      "select",
      { id: `${modelType}-model-default`, value: selectedValue, disabled: options.length === 0 && !selectedValue, onChange: changeDefault },
      React.createElement("option", { value: "" }, options.length === 0 ? "没有已验证的 Model" : `选择${label}`),
      selectedValue && !selectedIsAvailable
        ? React.createElement("option", { value: selectedValue }, `当前不可用：${modelDefault.default.model_id}`)
        : null,
      options.map(({ provider, model }) => React.createElement("option", { key: `${provider.provider_id}-${model.model_id}`, value: JSON.stringify([provider.provider_id, model.model_id]) }, `${provider.name} / ${model.model_id}`))
    ),
    React.createElement("span", { className: "form-help" }, modelDefault.reason || description)
  );
}

function ProviderManagement({ providers, isLoading, modelDefaults, onOpenForm, onUpdate, onConfirm, onDefaultsChange }) {
  const [status, setStatus] = React.useState("");

  async function testProvider(provider) {
    setStatus("");
    try {
      const response = await requestJson(`${PROVIDERS_ENDPOINT}/${provider.provider_id}/test`, { method: "POST" });
      onUpdate(response.provider);
      onDefaultsChange();
      setStatus(response.provider.verification.is_verified ? "Provider 发现和健康验证通过。" : "Provider 验证未通过；请查看各项原因。");
    } catch (error) {
      setStatus(error.message);
    }
  }

  async function configureModel(providerId, modelId, modelType) {
    setStatus("");
    try {
      const response = await requestJson(`${PROVIDERS_ENDPOINT}/${providerId}/models`, {
        method: "PUT",
        body: JSON.stringify({ model_id: modelId, model_type: modelType })
      });
      onUpdate(response.provider);
      await onDefaultsChange();
      setStatus("模型类型已更新；请单独验证该模型。");
    } catch (error) {
      setStatus(error.message);
    }
  }

  async function testModel(providerId, modelId) {
    setStatus("");
    try {
      const response = await requestJson(`${PROVIDERS_ENDPOINT}/${providerId}/models/test`, {
        method: "POST",
        body: JSON.stringify({ model_id: modelId })
      });
      onUpdate(response.provider);
      await onDefaultsChange();
      setStatus("模型验证已完成。");
    } catch (error) {
      setStatus(error.message);
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
      setStatus(`${modelType === "chat" ? "对话/文本生成" : "Embedding"}默认 Model 已更新。`);
    } catch (error) {
      setStatus(error.message);
    }
  }

  async function clearDefault(modelType) {
    setStatus("");
    try {
      await requestJson(`${PROVIDERS_ENDPOINT}/defaults/${modelType}`, { method: "DELETE" });
      await onDefaultsChange();
      setStatus(`${modelType === "chat" ? "对话/文本生成" : "Embedding"}默认 Model 已清除。`);
    } catch (error) {
      setStatus(error.message);
    }
  }

  return React.createElement(
    "section",
    { className: "provider-management", "aria-labelledby": "provider-settings-heading" },
    React.createElement(
      "div",
      { className: "list-heading" },
      React.createElement("div", null, React.createElement("p", { className: "section-label" }, "Provider 与模型"), React.createElement("h2", { id: "provider-settings-heading" }, "Provider")),
      React.createElement("button", { className: "primary-button", type: "button", onClick: () => onOpenForm(null) }, "添加 Provider")
    ),
    React.createElement(
      "div",
      { className: "model-default-section", "aria-labelledby": "chat-model-heading" },
      React.createElement("h3", { id: "chat-model-heading" }, "对话/文本生成模型"),
      React.createElement(ModelDefaultSelector, {
        modelType: "chat",
        label: "全局对话/文本生成 Model",
        description: "用于解析、分类、标签、链接建议和会话。",
        providers,
        modelDefault: modelDefaults.chat,
        onChange: changeDefault,
        onClear: clearDefault
      })
    ),
    React.createElement(
      "div",
      { className: "model-default-section", "aria-labelledby": "embedding-model-heading" },
      React.createElement("h3", { id: "embedding-model-heading" }, "Embedding 模型"),
      React.createElement(ModelDefaultSelector, {
        modelType: "embedding",
        label: "全局 Embedding Model",
        description: "用于未来私有语义索引与检索。",
        providers,
        modelDefault: modelDefaults.embedding,
        onChange: changeDefault,
        onClear: clearDefault
      })
    ),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null,
    isLoading
      ? React.createElement("p", { className: "empty-state", role: "status" }, "正在加载 Provider 配置。")
      : null,
    !isLoading && providers.length === 0
      ? React.createElement("p", { className: "empty-state" }, "尚无 Provider。添加后先完成发现和健康验证，再配置并验证每个模型。")
      : null,
    providers.map((provider) =>
      React.createElement(
        "div",
        { className: "section-row provider-row", key: provider.provider_id },
        React.createElement("div", { className: "provider-summary" }, React.createElement("span", { className: "row-title" }, provider.name), React.createElement("span", { className: "row-meta" }, provider.endpoint), React.createElement("span", { className: "row-note" }, `凭据：${provider.credential_configured ? "已配置" : "不可用"}；${providerStatus(provider)}`)),
        React.createElement(
          "div",
          { className: "rule-actions" },
          React.createElement("button", { className: "text-button", type: "button", onClick: () => testProvider(provider) }, "测试"),
          React.createElement("button", { className: "text-button", type: "button", onClick: () => onOpenForm(provider) }, "编辑"),
          React.createElement("button", { className: "text-button danger-text-button", type: "button", onClick: (event) => onConfirm("provider-remove", provider, event.currentTarget) }, "删除")
        ),
        React.createElement(ProviderVerification, { provider }),
        provider.models.map((model) => React.createElement(
          "div",
          { className: "provider-model-row", key: `${provider.provider_id}-${model.model_id}` },
          React.createElement("span", { className: "row-title" }, model.model_id),
          React.createElement(
            "label",
            { className: "model-type-selector" },
            React.createElement("span", { className: "visually-hidden" }, `${model.model_id} 模型类型`),
            React.createElement(
              "select",
              { value: model.model_type || "", disabled: !model.is_discovered || !provider.verification.is_verified,
                onChange: (event) => configureModel(provider.provider_id, model.model_id, event.target.value) },
              React.createElement("option", { value: "" }, "选择类型"),
              React.createElement("option", { value: "chat" }, "对话/文本生成"),
              React.createElement("option", { value: "embedding" }, "Embedding")
            )
          ),
          React.createElement("span", { className: model.verification.ok ? "provider-check provider-check-ok" : "provider-check provider-check-failed" }, model.verification.ok ? "已验证" : model.verification.reason || "未验证"),
          React.createElement("button", { className: "text-button", type: "button", disabled: !model.model_type || !model.is_discovered || !provider.verification.is_verified, onClick: () => testModel(provider.provider_id, model.model_id) }, "测试模型")
        ))
      )
    )
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
        React.createElement("span", { className: "row-status" }, `${statusText(vault)}${vault.is_current ? " · 当前" : ""}`)
      )
    )
  );
}

function ImportTaskLauncher({ vault, onCreated }) {
  const [status, setStatus] = React.useState("");
  const [isSelecting, setIsSelecting] = React.useState(false);
  const canImport = vault && vault.authorization_status === "active" && vault.access_status === "available";

  async function selectAndCreate(kind) {
    if (!canImport) return;
    setStatus("");
    setIsSelecting(true);
    try {
      const selection = await requestJson(
        kind === "directory" ? IMPORT_DIRECTORY_SELECTION_ENDPOINT : IMPORT_FILES_SELECTION_ENDPOINT,
        kind === "directory" ? { method: "POST" } : {
          method: "POST",
          body: JSON.stringify({ multiple: kind === "multiple" })
        }
      );
      if (!selection.selection_id) {
        setStatus("未选择资料，未创建导入任务。");
        return;
      }
      const created = await requestJson(IMPORT_TASKS_ENDPOINT, {
        method: "POST",
        body: JSON.stringify({ vault_id: vault.vault_id, selection_id: selection.selection_id })
      });
      setStatus(`已创建导入任务：${selection.label}。`);
      onCreated(created.task);
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
          React.createElement("p", { className: "scope-summary" }, `目标 vault：${vaultName(vault)}`),
          React.createElement(
            "div",
            { className: "detail-actions" },
            React.createElement(
              "button",
              { className: "primary-button", type: "button", disabled: isSelecting, onClick: () => selectAndCreate("single") },
              "选择文件"
            ),
            React.createElement(
              "button",
              { className: "secondary-button", type: "button", disabled: isSelecting, onClick: () => selectAndCreate("multiple") },
              "选择多个文件"
            ),
            React.createElement(
              "button",
              { className: "secondary-button", type: "button", disabled: isSelecting, onClick: () => selectAndCreate("directory") },
              "选择文件夹"
            )
          )
        )
      : React.createElement("p", { className: "empty-state" }, "请先授权并设为当前可用 vault，才能导入资料。"),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null
  );
}

function ImportTaskDetail({ taskId, onBack, onTaskChanged, onTaskSnapshot }) {
  const [detail, setDetail] = React.useState(null);
  const [status, setStatus] = React.useState("");
  const [isActing, setIsActing] = React.useState(false);
  const [ocrDrafts, setOcrDrafts] = React.useState({});
  const [classificationDrafts, setClassificationDrafts] = React.useState({});
  const [metadataTagDrafts, setMetadataTagDrafts] = React.useState({});
  const [candidateLinkDrafts, setCandidateLinkDrafts] = React.useState({});
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

  function updateMetadataTagDraft(itemId, value) {
    setMetadataTagDrafts((current) => ({ ...current, [itemId]: value }));
  }

  function updateCandidateLinkDraft(reviewItemId, value) {
    setCandidateLinkDrafts((current) => ({ ...current, [reviewItemId]: value }));
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

  async function runMetadataTagAction(proposal, decision) {
    if (isActing) return;
    const reason = metadataTagDrafts[proposal.item_id]?.trim();
    if (decision === "excluded" && !reason) {
      setStatus("请说明排除元数据与标签提案的理由。 ");
      return;
    }
    setStatus("");
    setIsActing(true);
    try {
      const response = await requestJson(
        `${IMPORT_TASKS_ENDPOINT}/${taskId}/metadata-tags/${proposal.item_id}/decision`,
        {
          method: "POST",
          body: JSON.stringify({
            decision,
            reason: reason || "Accepted from the import task detail."
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

  async function runCandidateLinkAction(proposal, decision) {
    if (isActing) return;
    const reason = candidateLinkDrafts[proposal.review_item_id]?.trim();
    if (decision === "excluded" && !reason) {
      setStatus("请说明排除候选链接的理由。 ");
      return;
    }
    setStatus("");
    setIsActing(true);
    try {
      const response = await requestJson(
        `${IMPORT_TASKS_ENDPOINT}/${taskId}/candidate-links/${encodeURIComponent(proposal.review_item_id)}/decision`,
        {
          method: "POST",
          body: JSON.stringify({
            decision,
            reason: reason || "Accepted from the import task detail."
          })
        }
      );
      onTaskChanged(response.task);
      await loadDetail();
      setStatus(decision === "accepted" ? "候选链接已接受，尚未写入 Markdown。" : "候选链接已排除。");
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
    metadata_tag_proposals: metadataTagProposals = [],
    candidate_link_proposals: candidateLinkProposals = [],
    conversion_graphs: conversionGraphs = [],
    review_snapshot: reviewSnapshot = null,
    commit_journals: commitJournals = [],
    index = null
  } = detail;
  const canCancel = task.lifecycle === "running";
  const canResume = task.recovery_actions.includes("restart-scan") || task.recovery_actions.includes("restart-parse") || task.recovery_actions.includes("restart-ocr") || task.recovery_actions.includes("restart-derivation") || task.recovery_actions.includes("create-new-task");
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
  const metadataTagControlReason = isActing
    ? "正在更新元数据与标签提案。"
    : task.lifecycle !== "waiting-for-review"
      ? "元数据与标签提案只能在等待审核时处理。"
      : "";
  const candidateLinkControlReason = isActing
    ? "正在更新候选链接提案。"
    : task.lifecycle !== "waiting-for-review"
      ? "候选链接只能在等待审核时处理。"
      : "";
  const reviewItemsByUnit = new Map();
  for (const reviewItem of reviewSnapshot?.review_items || []) {
    const current = reviewItemsByUnit.get(reviewItem.unit_id) || [];
    current.push(reviewItem);
    reviewItemsByUnit.set(reviewItem.unit_id, current);
  }
  const conversionBlocksByItem = new Map(
    conversionGraphs.map((graph) => [graph.item_id, graph.blocks || []])
  );
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
    React.createElement("h2", null, `导入任务 ${task.task_id}`),
    React.createElement("p", { className: "scope-summary" }, `目标 vault：${task.vault_label}；范围：${task.scope_label}`),
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
            task.lifecycle === "cancelled" ? "创建新任务" : task.recovery_actions.includes("restart-parse") ? "重新解析" : task.recovery_actions.includes("restart-ocr") ? "重新 OCR" : task.recovery_actions.includes("restart-derivation") ? "重新生成笔记" : "重新扫描"
          )
        : null
      , canStartConversion
        ? React.createElement("button", { className: "primary-button", type: "button", disabled: isActing, onClick: () => runAction("convert") }, "开始保真转换")
        : null
    ),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null,
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
              `快照 ${reviewSnapshot.digest.slice(0, 12)}；目标 vault：${task.vault_label}；来源 ${reviewSnapshot.source_hashes.length}；受影响既有文件 ${reviewSnapshot.existing_file_hashes.length}`
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
            item.source_id ? React.createElement("span", { className: "row-note" }, `来源：${item.source_id}`) : null,
            item.content_sha256 ? React.createElement("span", { className: "row-note" }, `哈希：${item.content_sha256}`) : null,
            item.parse_confidence !== null && item.parse_confidence !== undefined
              ? React.createElement("span", { className: "row-note" }, `解析置信度：${item.parse_confidence}`)
              : null,
            item.conversion_engine
              ? React.createElement("span", { className: "row-note" }, `转换器：${item.conversion_engine}`)
              : null,
            item.conversion_fallback_reason
              ? React.createElement("span", { className: "row-note" }, item.conversion_fallback_reason)
              : null,
            item.parse_locator_summary
              ? React.createElement("span", { className: "row-note" }, `证据位置：${item.parse_locator_summary}`)
              : null,
            item.parse_issue_count
              ? React.createElement("span", { className: "row-status status-danger" }, `待审核问题 ${item.parse_issue_count}`)
              : null,
            item.parse_issue_summary
              ? React.createElement("span", { className: "row-note" }, item.parse_issue_summary)
              : null,
            item.ocr_confidence !== null && item.ocr_confidence !== undefined
              ? React.createElement("span", { className: "row-note" }, `OCR 置信度：${item.ocr_confidence}`)
              : null,
            item.ocr_locator_summary
              ? React.createElement("span", { className: "row-note" }, `OCR 位置：${item.ocr_locator_summary}`)
              : null,
            item.ocr_issue_count
              ? React.createElement("span", { className: "row-status status-danger" }, `OCR 待审核 ${item.ocr_issue_count}`)
              : null,
            item.ocr_issue_summary
              ? React.createElement("span", { className: "row-note" }, item.ocr_issue_summary)
              : null,
            ...(item.ocr_targets || []).map((target) => {
              const draft = ocrDrafts[`${item.item_id}:${target.target_id}`] || {};
              const needsDecision = target.issue_count > 0 && !target.decision;
              return React.createElement(
                "div",
                { className: "ocr-target-actions", key: `${item.item_id}:${target.target_id}` },
                React.createElement("span", { className: "row-title" }, target.label),
                React.createElement("span", { className: "row-meta" }, `${target.locator_summary} · ${importOcrTargetStatusText(target.status)}${target.engine ? ` · ${target.engine}` : ""}`),
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
                        "aria-label": `${target.label} 的处理理由`,
                        placeholder: "处理理由"
                      }),
                      React.createElement("textarea", {
                        value: draft.corrected_text || "",
                        onChange: (event) => updateOcrDraft(item.item_id, target.target_id, "corrected_text", event.target.value),
                        "aria-label": `${target.label} 的修正文本`,
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
                    `候选来源：${item.version_suggestion.candidate_source_id}；旧哈希：${item.version_suggestion.previous_content_sha256}；${item.version_suggestion.reason}`
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
                  React.createElement("p", { className: "row-note" }, `计划源位置：${proposal.source_relative_path}`),
                  proposal.risks?.length
                    ? React.createElement("p", { className: "row-status status-danger" }, `待审核范围：${proposal.risks.join("；")}`)
                    : null,
                  noteProposalActionReason
                    ? React.createElement("p", { className: "row-note" }, `边界调整不可用：${noteProposalActionReason}`)
                    : null,
                  React.createElement("pre", { className: "markdown-preview" }, proposal.index_note.markdown),
                  proposal.notes.map((note, index) => {
                    const splitKey = `${proposal.item_id}:${note.sequence}`;
                    const safeBoundaries = note.safe_split_after_unit_indexes || [];
                    const selectedBoundary = splitSelections[splitKey] ?? safeBoundaries[0];
                    return React.createElement(
                      "div",
                      { className: "section-row note-proposal-row", key: note.note_id },
                      React.createElement("span", { className: "row-title" }, `${note.sequence}. ${note.title}`),
                      React.createElement("span", { className: "row-note" }, `位置：${note.relative_path}`),
                      React.createElement("span", { className: "row-note" }, `来源：${note.source_locators.map(sourceLocatorText).join("、")}`),
                      note.provenance_verifiable === false
                        ? React.createElement("span", { className: "row-status status-danger" }, `来源信息不可验证：${note.provenance_reason || "schema 不受支持。"}`)
                        : null,
                      React.createElement("pre", { className: "markdown-preview" }, note.markdown),
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
      { className: "metadata-tag-list", "aria-label": "元数据与标签" },
      React.createElement("h3", null, "元数据与标签"),
      metadataTagProposals.length === 0
        ? React.createElement("p", { className: "empty-state" }, "正在等待元数据与标签治理提案。")
        : metadataTagProposals.map((proposal) => {
            const canDecide = task.lifecycle === "waiting-for-review" && !isActing && !proposal.decision;
            const decisionText = proposal.decision === "accepted"
              ? "已接受"
              : proposal.decision === "excluded"
                ? "已排除"
                : proposal.requires_review
                  ? "标签必须检查"
                  : "待确认";
            return React.createElement(
              "div",
              { className: "section-row review-diff-row metadata-tag-row", key: proposal.item_id },
              React.createElement("span", { className: "row-title" }, `资料项 ${proposal.item_id}：${proposal.source_file}`),
              React.createElement("span", { className: "row-meta" }, `来源类型：${proposal.source_type}；处理状态：${proposal.processing_status}`),
              React.createElement("span", { className: "row-note" }, `内容哈希：${proposal.content_sha256}`),
              React.createElement("span", { className: "row-note" }, `领域：${proposal.domain}；置信度：${proposal.domain_confidence}`),
              React.createElement(
                "span",
                { className: `row-status${proposal.requires_review && !proposal.decision ? " status-danger" : ""}` },
                decisionText
              ),
              proposal.tags.map((tag) => React.createElement(
                "span",
                { className: "row-note", key: tag.name },
                `标签：${tag.name}${tag.is_new ? "（新建，待审核）" : "（复用）"}；文档 ${tag.document_paths.length}，笔记 ${tag.note_paths.length}；置信度：${tag.confidence}`
              )),
              proposal.decision_reason
                ? React.createElement("span", { className: "row-note" }, `决定理由：${proposal.decision_reason}`)
                : null,
              !proposal.decision
                ? React.createElement(
                    "div",
                    { className: "classification-controls" },
                    React.createElement("input", {
                      type: "text",
                      value: metadataTagDrafts[proposal.item_id] || "",
                      onChange: (event) => updateMetadataTagDraft(proposal.item_id, event.target.value),
                      disabled: !canDecide,
                      placeholder: "排除理由",
                      "aria-label": `资料项 ${proposal.item_id} 的元数据与标签决定理由`
                    }),
                    React.createElement("button", {
                      className: "secondary-button",
                      type: "button",
                      disabled: !canDecide,
                      title: metadataTagControlReason || undefined,
                      onClick: () => runMetadataTagAction(proposal, "accepted")
                    }, "接受标签"),
                    React.createElement("button", {
                      className: "secondary-button",
                      type: "button",
                      disabled: !canDecide,
                      title: metadataTagControlReason || undefined,
                      onClick: () => runMetadataTagAction(proposal, "excluded")
                    }, "排除标签")
                  )
                : null
            );
          })
    )
    ,
    React.createElement(
      "section",
      { className: "candidate-link-list", "aria-label": "候选链接" },
      React.createElement("h3", null, "候选链接"),
      React.createElement("p", { className: "row-note" }, "审核决定仅保存在应用私有状态，尚未写入 Markdown。"),
      candidateLinkProposals.length === 0
        ? React.createElement("p", { className: "empty-state" }, "尚未发现有充分证据的候选链接。")
        : candidateLinkProposals.map((proposal) => {
            const isStale = proposal.status === "stale";
            const canDecide = task.lifecycle === "waiting-for-review" && !isActing && !proposal.decision && !isStale;
            const decisionText = isStale
              ? "已陈旧，需重新生成"
              : proposal.decision === "accepted"
              ? "已接受，待后续提交"
              : proposal.decision === "excluded"
                ? "已排除"
                : proposal.requires_review
                  ? "必须检查"
                  : "待确认";
            const decisionControlReason = isStale
              ? proposal.stale_reason || "候选链接已陈旧，不能再做决定。"
              : candidateLinkControlReason;
            const controlReasonId = `candidate-link-control-reason-${proposal.review_item_id}`;
            return React.createElement(
              "div",
              { className: "section-row review-diff-row candidate-link-row", key: proposal.review_item_id },
              React.createElement("span", { className: "row-title" }, `${proposal.source_path} -> ${proposal.target_path}`),
              React.createElement("span", { className: "row-note" }, `关系理由：${proposal.reason}`),
              React.createElement("span", { className: "row-note" }, `来源证据（${proposal.source_evidence.block_location}）：${proposal.source_evidence.excerpt}`),
              React.createElement("span", { className: "row-note" }, `目标证据（${proposal.target_evidence.block_location}）：${proposal.target_evidence.excerpt}`),
              React.createElement("span", { className: "row-note" }, `置信度：${proposal.confidence}`),
              proposal.is_existing_note_change
                ? React.createElement("span", { className: "row-status" }, "既有笔记独立变更")
                : null,
              React.createElement(
                "span",
                { className: `row-status${proposal.requires_review && !proposal.decision ? " status-danger" : ""}` },
                decisionText
              ),
              proposal.decision_reason
                ? React.createElement("span", { className: "row-note" }, `决定理由：${proposal.decision_reason}`)
                : null,
              isStale
                ? React.createElement("span", { className: "row-note" }, `陈旧原因：${proposal.stale_reason}`)
                : null,
              !proposal.decision
                ? React.createElement(
                    "div",
                    { className: "classification-controls" },
                    decisionControlReason
                      ? React.createElement("span", { id: controlReasonId, className: "row-note", role: "status" }, decisionControlReason)
                      : null,
                    React.createElement("input", {
                      type: "text",
                      value: candidateLinkDrafts[proposal.review_item_id] || "",
                      onChange: (event) => updateCandidateLinkDraft(proposal.review_item_id, event.target.value),
                      disabled: !canDecide,
                      placeholder: "审核理由（排除时必填）",
                      "aria-label": `${proposal.source_path} 到 ${proposal.target_path} 的候选链接决定理由`,
                      "aria-describedby": decisionControlReason ? controlReasonId : undefined
                    }),
                    React.createElement("button", {
                      className: "secondary-button",
                      type: "button",
                      disabled: !canDecide,
                      title: decisionControlReason || undefined,
                      "aria-describedby": decisionControlReason ? controlReasonId : undefined,
                      onClick: () => runCandidateLinkAction(proposal, "accepted")
                    }, "接受"),
                    React.createElement("button", {
                      className: "secondary-button",
                      type: "button",
                      disabled: !canDecide,
                      title: decisionControlReason || undefined,
                      "aria-describedby": decisionControlReason ? controlReasonId : undefined,
                      onClick: () => runCandidateLinkAction(proposal, "excluded")
                    }, "确认排除")
                  )
                : null
            );
          })
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
                React.createElement("span", { className: "row-title" }, `资料项 ${suggestion.item_id}：${suggestion.domain}`),
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
                        "aria-label": `资料项 ${suggestion.item_id} 的领域`
                      }),
                      React.createElement("input", {
                        type: "text",
                        value: draft.target_folder ?? suggestion.target_folder,
                        onChange: (event) => updateClassificationDraft(suggestion.item_id, "target_folder", event.target.value),
                        disabled: !canDecide,
                        "aria-label": `资料项 ${suggestion.item_id} 的目标文件夹`
                      }),
                      React.createElement("input", {
                        type: "text",
                        value: draft.filename ?? suggestion.filename,
                        onChange: (event) => updateClassificationDraft(suggestion.item_id, "filename", event.target.value),
                        disabled: !canDecide,
                        "aria-label": `资料项 ${suggestion.item_id} 的目标文件名`
                      }),
                      React.createElement("input", {
                        type: "text",
                        value: draft.reason || "",
                        onChange: (event) => updateClassificationDraft(suggestion.item_id, "reason", event.target.value),
                        disabled: !canDecide,
                        placeholder: "修正或排除理由",
                        "aria-label": `资料项 ${suggestion.item_id} 的分类决定理由`
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

export function ImportTaskCenter({ tasks, error, isLoading, selectedTaskId, onSelect, onTaskChanged, onTaskDeleted, onTaskSnapshot, vault }) {
  const [deleteError, setDeleteError] = React.useState("");
  const [deletingTaskId, setDeletingTaskId] = React.useState(null);
  const listRef = React.useRef(null);

  async function deleteTask(task) {
    if (deletingTaskId) return;
    const confirmed = window.confirm(
      `删除任务“${task.scope_label}”及其未提交的处理数据？\n\n不会删除 vault 中已存在或已提交的文件。`
    );
    if (!confirmed) return;
    setDeleteError("");
    setDeletingTaskId(task.task_id);
    try {
      await onTaskDeleted(task.task_id);
      listRef.current?.focus();
    } catch (deleteFailure) {
      setDeleteError(deleteFailure.message);
    } finally {
      setDeletingTaskId(null);
    }
  }

  if (selectedTaskId) {
    return React.createElement(ImportTaskDetail, {
      taskId: selectedTaskId,
      onBack: onSelect,
      onTaskChanged,
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
      isLoading
        ? React.createElement("p", { className: "empty-state", role: "status" }, "正在读取任务快照。")
        : tasks.length === 0 && !error
          ? React.createElement("p", { className: "empty-state" }, "当前没有导入任务。")
          : tasks.map((task) => React.createElement(
              "div",
              { className: "section-row import-task-row", key: task.task_id },
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
                      disabled: deletingTaskId !== null,
                      onClick: () => deleteTask(task)
                    },
                    deletingTaskId === task.task_id ? "删除中" : "删除"
                  )
                : null
            ))
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
  const [sessionPage, setSessionPage] = React.useState({ sessions: [], page: 1, page_size: 25, total: 0, total_pages: 1 });
  const [sessionFilters, setSessionFilters] = React.useState({ query: "", sort: "updated_at", order: "desc", page: 1 });
  const [sessionsLoading, setSessionsLoading] = React.useState(true);
  const [sessionsError, setSessionsError] = React.useState("");
  const [modelDefaults, setModelDefaults] = React.useState({
    chat: { default: null, status: "unconfigured", reason: "正在加载对话/文本生成 Model。" },
    embedding: { default: null, status: "unconfigured", reason: "正在加载 Embedding Model。" }
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

  const loadModelDefaults = React.useCallback(() => (
    requestJson(`${PROVIDERS_ENDPOINT}/defaults`)
      .then((response) => setModelDefaults(response))
      .catch((error) => setModelDefaults({
        chat: { default: null, status: "unavailable", reason: error.message },
        embedding: { default: null, status: "unavailable", reason: error.message }
      }))
  ), []);

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
      return response;
    } catch (requestError) {
      if (requestId === sessionListRequestRef.current) setSessionsError(requestError.message);
      return null;
    } finally {
      if (requestId === sessionListRequestRef.current) setSessionsLoading(false);
    }
  }, []);

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
        return Promise.all([loadVaults(), loadProviders(), loadModelDefaults(), loadTasks(), loadSessions()]);
      })
      .catch(() => {
        setSessionStatus("本机会话不可用");
        setVaultsLoading(false);
        setTasksLoading(false);
        setTasksError("本机会话不可用。");
        setSessionsLoading(false);
        setSessionsError("本机会话不可用。");
      });
  }, [loadModelDefaults, loadProviders, loadSessions, loadTasks, loadVaults]);

  React.useEffect(() => {
    if (menuOpen) firstMenuLinkRef.current?.focus();
  }, [menuOpen]);

  const activePage = NAVIGATION_DESTINATIONS.find(
    (destination) => destination.id === activeDestination
  );
  const selectedVault = vaults.find((vault) => vault.vault_id === selectedVaultId) || null;
  const currentVault = vaults.find((vault) => vault.is_current) || null;
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

  const deleteTask = React.useCallback(async (taskId) => {
    setTasksError("");
    try {
      await requestJson(`${IMPORT_TASKS_ENDPOINT}/${taskId}`, { method: "DELETE" });
      setSelectedTaskId((current) => current === taskId ? null : current);
    } finally {
      await loadTasks();
    }
  }, [loadTasks]);

  async function createPersistentSession() {
    const response = await requestJson(SESSIONS_ENDPOINT, {
      method: "POST",
      body: JSON.stringify({})
    });
    await loadSessions({ query: "", sort: "updated_at", order: "desc", page: 1 });
    return response.session;
  }

  async function renamePersistentSession(sessionId, title) {
    const response = await requestJson(`${SESSIONS_ENDPOINT}/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify({ title })
    });
    await loadSessions(sessionFilters);
    return response.session;
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
    link.download = `session-${session.session_id}.json`;
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
      onLoad: loadSessions,
      onCreate: createPersistentSession,
      onRename: renamePersistentSession,
      onExport: exportPersistentSession,
      onDelete: (session, trigger) => openConfirmation("session-remove", session, trigger)
    });
  } else if (activeDestination === "workbench") {
    workspaceContent = React.createElement(KnowledgeGraphWorkbench, {
      vaults,
      currentVault,
      isLoading: vaultsLoading,
      onAddVault: () => setFormVault(null),
      onUpdateVault: updateVault
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
      { className: "application-content" },
      React.createElement(
        "header",
        { className: "context-bar" },
        React.createElement(
          "button",
          {
            className: "menu-button",
            type: "button",
            ref: menuButtonRef,
            title: "打开导航",
            "aria-label": "打开导航",
            "aria-controls": "mobile-navigation-panel",
            "aria-expanded": menuOpen,
            onClick: () => setMenuOpen(true)
          },
          "☰"
        ),
          React.createElement(
            "p",
            { className: "context-location" },
          currentVault ? `本机 / 当前 vault：${vaultName(currentVault)}` : "本机 / 当前工作区"
        ),
        React.createElement(
          "div",
          { className: "context-statuses", "aria-live": "polite" },
          React.createElement("span", { "data-testid": "health-status" }, healthStatus),
          React.createElement("span", { "data-testid": "session-status" }, sessionStatus),
          currentPolicy
            ? React.createElement("span", { "data-testid": "outbound-status" }, `外发：${outboundModeText(currentPolicy.outbound_mode)}`)
            : null
        )
      ),
      React.createElement(
        "main",
        { className: "workspace", "aria-labelledby": "workspace-title" },
        React.createElement(
          "div",
          { className: "workspace-inner" },
          React.createElement("p", { className: "eyebrow" }, "本机工作区"),
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
        React.createElement("button", { className: "panel-close", type: "button", onClick: closeMenu }, "关闭")
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
