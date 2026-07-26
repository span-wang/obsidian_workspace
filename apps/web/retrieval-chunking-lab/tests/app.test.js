import assert from "node:assert/strict";
import test from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  ChunkPreviewResults,
  DEFAULT_MARKDOWN,
  RetrievalChunkingLab,
  formatHeadingPath
} from "../src/app.js";

test("provides only synthetic Markdown as the initial lab input", () => {
  const markup = renderToStaticMarkup(React.createElement(RetrievalChunkingLab));

  assert.match(DEFAULT_MARKDOWN, /第一单元 认识新朋友/);
  assert.match(markup, /检索分块实验台/);
  assert.match(markup, /不会读取 vault 或 SQLite，也不会访问外网/);
  assert.match(markup, /生成分块预览/);
  assert.match(markup, /尚未生成预览/);
});

test("renders chunk structure without importing the production workbench", () => {
  const markup = renderToStaticMarkup(React.createElement(ChunkPreviewResults, {
    chunks: [{
      sequence: 1,
      block_kind: "paragraph",
      location: "line:3",
      heading_path: ["第一单元", "语法"],
      heading_level: 2,
      contextual_prefix: "第一单元 / 语法",
      text: "I am a student.",
      retrieval_text: "I am a student.",
      token_estimate: 5,
      block_content_sha256: "a".repeat(64)
    }]
  }));

  assert.equal(formatHeadingPath([]), "文档开头");
  assert.equal(formatHeadingPath(["第一单元", "语法"]), "第一单元 / 语法");
  assert.match(markup, /已生成 1 个分块/);
  assert.match(markup, /标题路径/);
  assert.match(markup, /第一单元 \/ 语法/);
  assert.match(markup, /上下文前缀/);
  assert.match(markup, /I am a student/);
  assert.match(markup, /文本哈希/);
});
