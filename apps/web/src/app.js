import React from "react";

export const HEALTH_ENDPOINT = "/api/health";
export const LOCAL_SESSION_ENDPOINT = "/api/session";
export const VAULTS_ENDPOINT = "/api/vaults";
export const VAULT_DIRECTORY_PICKER_ENDPOINT = "/api/vaults/select-directory";
export const PROVIDERS_ENDPOINT = "/api/providers";
export const IMPORT_TASKS_ENDPOINT = "/api/import-tasks";
export const IMPORT_FILES_SELECTION_ENDPOINT = "/api/import-selections/files";
export const IMPORT_DIRECTORY_SELECTION_ENDPOINT = "/api/import-selections/directory";
export const NAVIGATION_DESTINATIONS = [
  { id: "workbench", label: "工作台", emptyState: "尚未选择 vault。" },
  { id: "materials", label: "资料", emptyState: "当前没有已授权的 vault。" },
  { id: "sessions", label: "会话", emptyState: "当前没有已保存的会话。" },
  { id: "tasks", label: "任务", emptyState: "当前没有任务。" },
  { id: "settings", label: "设置", emptyState: "当前没有可用设置。" }
];

const VAULT_SURFACES = new Set(["workbench", "materials"]);
const IMPORT_PROGRESS_PHASES = ["queued", "scanning", "parsing", "ocr", "waiting-for-review", "committing", "indexing"];

function importLifecycleText(lifecycle) {
  return {
    queued: "排队",
    running: "运行中",
    recoverable: "可恢复",
    failed: "失败",
    cancelled: "已取消",
    complete: "已完成"
  }[lifecycle] || lifecycle;
}

function importPhaseText(phase) {
  return {
    queued: "排队",
    scanning: "扫描",
    "waiting-for-next-stage": "等待后续处理",
    interrupted: "扫描已中断",
    parsing: "解析",
    ocr: "OCR",
    "waiting-for-review": "等待审核",
    committing: "提交",
    indexing: "索引",
    failed: "失败",
    cancelled: "已取消",
    complete: "完成"
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
    const payload = await response.json();
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
      onClose();
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
  const isRemoval = request.kind === "remove" || isProviderRemoval;
  const targetName = isProviderRemoval ? request.target.name : vaultName(request.target);
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
        isProviderRemoval ? "删除 Provider" : isRemoval ? "移除 vault 授权" : "停用 vault"
      ),
      React.createElement(
        "p",
        null,
        isProviderRemoval
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
          isProviderRemoval ? "删除 Provider" : isRemoval ? "移除授权" : "停用"
        )
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
      React.createElement("dd", null, vault.index_status === "not-initialized" ? "未初始化" : vault.index_status),
      React.createElement("dt", null, "受管根目录"),
      React.createElement("dd", null, vault.managed_root),
      React.createElement("dt", null, "隔离边界"),
      React.createElement("dd", null, "文件、标签、候选链接、索引和操作状态仅属于此 vault。")
    ),
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
      eventSource.addEventListener("task-update", scheduleDetailRefresh);
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
      React.createElement("p", { className: "empty-state", role: "status" }, status || "正在读取任务快照。")
    );
  }

  const { task, items } = detail;
  const canCancel = task.lifecycle === "running";
  const canResume = task.recovery_actions.includes("restart-scan") || task.recovery_actions.includes("restart-parse") || task.recovery_actions.includes("create-new-task");
  return React.createElement(
    "section",
    { className: "import-task-detail", "aria-label": "导入任务详情" },
    React.createElement("button", { className: "back-button", type: "button", onClick: () => onBack(null) }, "返回任务列表"),
    React.createElement("h2", null, `导入任务 ${task.task_id}`),
    React.createElement("p", { className: "scope-summary" }, `目标 vault：${task.vault_label}；范围：${task.scope_label}`),
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
            task.lifecycle === "cancelled" ? "创建新任务" : task.recovery_actions.includes("restart-parse") ? "重新解析" : "重新扫描"
          )
        : null
    ),
    status ? React.createElement("p", { className: "status-line", role: "status" }, status) : null,
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
              `${importCategoryText(item.category)} · ${importIdentityStatusText(item.identity_status)} · ${importParseStatusText(item.parse_status)}`
            ),
            item.source_id ? React.createElement("span", { className: "row-note" }, `来源：${item.source_id}`) : null,
            item.content_sha256 ? React.createElement("span", { className: "row-note" }, `哈希：${item.content_sha256}`) : null,
            item.parse_confidence !== null && item.parse_confidence !== undefined
              ? React.createElement("span", { className: "row-note" }, `解析置信度：${item.parse_confidence}`)
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
        )
  );
}

export function ImportTaskCenter({ tasks, error, isLoading, selectedTaskId, onSelect, onTaskChanged, onTaskSnapshot, vault }) {
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
      { className: "import-task-list", "aria-label": "导入任务列表" },
      React.createElement("p", { className: "section-label" }, "任务"),
      error ? React.createElement("p", { className: "status-line status-danger", role: "status" }, `无法读取导入任务：${error}`) : null,
      isLoading
        ? React.createElement("p", { className: "empty-state", role: "status" }, "正在读取任务快照。")
        : tasks.length === 0 && !error
          ? React.createElement("p", { className: "empty-state" }, "当前没有导入任务。")
          : tasks.map((task) => React.createElement(
              "button",
              { className: "section-row import-task-row", type: "button", key: task.task_id, onClick: () => onSelect(task.task_id) },
              React.createElement("span", { className: "row-title" }, task.scope_label),
              React.createElement("span", { className: "row-meta" }, `目标：${task.vault_label}`),
              React.createElement("span", { className: "row-status" }, `${importLifecycleText(task.lifecycle)} · ${importPhaseText(task.phase)}`),
              React.createElement("span", { className: "row-note" }, task.recovery_actions.length
                ? `恢复：${task.recovery_actions.map(importRecoveryActionText).join("、")}`
                : `发现 ${task.counts.discovered}；新资料 ${task.counts.new || 0}；重复资料 ${task.counts.duplicate || 0}；可能版本 ${task.counts.possible_version || 0}；识别失败 ${task.counts.identity_failed || 0}；已解析 ${task.counts.parsed || 0}；解析失败 ${task.counts.parse_failed || 0}；待审核问题 ${task.counts.required_check || 0}；失败 ${task.counts.failed}`)
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
        return Promise.all([loadVaults(), loadProviders(), loadModelDefaults(), loadTasks()]);
      })
      .catch(() => {
        setSessionStatus("本机会话不可用");
        setVaultsLoading(false);
        setTasksLoading(false);
        setTasksError("本机会话不可用。");
      });
  }, [loadModelDefaults, loadProviders, loadTasks, loadVaults]);

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
      setConfirmationError(error.message);
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
      onTaskSnapshot: syncTask,
      vault: currentVault
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
