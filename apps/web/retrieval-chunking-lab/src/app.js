import React, { useState } from "react";

const h = React.createElement;

export const DEFAULT_MARKDOWN = `# 第一单元 认识新朋友

## 词汇

- name：名字
- class：班级
- friend：朋友

## 语法

be 动词会随主语变化：I am，you are，he is。

| 人称 | be 动词 | 示例 |
| --- | --- | --- |
| I | am | I am a student. |
| he | is | He is my friend. |`;

function previewErrorMessage(payload) {
  if (payload && typeof payload.message === "string" && payload.message.trim()) {
    return payload.message;
  }
  return "分块预览失败。请检查输入后重试。";
}

async function requestPreview(markdown) {
  const response = await fetch("/api/_test/retrieval/chunk-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ markdown })
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(previewErrorMessage(payload));
  }
  if (!payload || !Array.isArray(payload.chunks)) {
    throw new Error("服务未返回可用的分块结果。请重试。");
  }
  return payload.chunks;
}

export function formatHeadingPath(headingPath) {
  return headingPath.length ? headingPath.join(" / ") : "文档开头";
}

function chunkDetail(label, value, className = "") {
  return h("div", null, h("dt", null, label), h("dd", { className }, value));
}

export function ChunkPreviewResults({ chunks }) {
  if (chunks === null) {
    return h("p", { className: "lab-muted", role: "status" }, "尚未生成预览。");
  }
  if (chunks.length === 0) {
    return h("p", { className: "lab-muted", role: "status" }, "输入没有可分块的 Markdown 内容。");
  }
  return h(
    "section",
    { "aria-label": "分块结果", className: "chunk-results" },
    h("h2", null, "预览结果"),
    h("p", { role: "status" }, `已生成 ${chunks.length} 个分块。`),
    h(
      "ol",
      null,
      chunks.map((chunk) => h(
        "li",
        { key: `${chunk.sequence}-${chunk.block_content_sha256}` },
        h(
          "article",
          { className: "chunk-card" },
          h(
            "header",
            null,
            h("h3", null, `分块 ${chunk.sequence}`),
            h("span", null, chunk.block_kind)
          ),
          h(
            "dl",
            null,
            chunkDetail("标题路径", formatHeadingPath(chunk.heading_path)),
            chunkDetail("定位", chunk.location),
            chunkDetail("上下文前缀", chunk.contextual_prefix || "（无）"),
            chunkDetail("估算 token", chunk.token_estimate),
            chunkDetail("文本哈希", chunk.block_content_sha256, "lab-hash")
          ),
          h("pre", null, chunk.text)
        )
      ))
    )
  );
}

export function RetrievalChunkingLab() {
  const [markdown, setMarkdown] = useState(DEFAULT_MARKDOWN);
  const [chunks, setChunks] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    if (!markdown.trim()) {
      setChunks(null);
      setError("请输入 Markdown 后再预览。");
      return;
    }
    setIsLoading(true);
    setError("");
    try {
      setChunks(await requestPreview(markdown));
    } catch (previewError) {
      setError(previewError instanceof Error ? previewError.message : "分块预览失败。请重试。");
    } finally {
      setIsLoading(false);
    }
  }

  return h(
    "main",
    { className: "retrieval-chunking-lab" },
    h(
      "header",
      { className: "lab-intro" },
      h("p", { className: "lab-eyebrow" }, "仅限本机测试"),
      h("h1", null, "检索分块实验台"),
      h("p", null, "使用合成 Markdown 检查分块边界、标题路径和上下文前缀。"),
      h(
        "p",
        { className: "lab-muted" },
        "不会读取 vault 或 SQLite，也不会访问外网；关闭服务开关后该页面不可用。"
      )
    ),
    h(
      "form",
      { "aria-label": "Markdown 分块预览", onSubmit: handleSubmit },
      h("label", { htmlFor: "markdown-input" }, "Markdown 输入"),
      h("textarea", {
        id: "markdown-input",
        "aria-label": "Markdown 输入",
        value: markdown,
        onChange: (event) => setMarkdown(event.target.value),
        rows: 18,
        spellCheck: false
      }),
      h(
        "button",
        { type: "submit", disabled: isLoading || !markdown.trim() },
        isLoading ? "正在生成预览…" : "生成分块预览"
      )
    ),
    isLoading ? h("p", { role: "status" }, "正在预览分块…") : null,
    error ? h("p", { role: "alert" }, `${error} 可修改 Markdown 后重试。`) : null,
    h(ChunkPreviewResults, { chunks })
  );
}
