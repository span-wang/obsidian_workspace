import assert from "node:assert/strict";
import test from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  App,
  HEALTH_ENDPOINT,
  IMPORT_DIRECTORY_SELECTION_ENDPOINT,
  IMPORT_FILES_SELECTION_ENDPOINT,
  ImportTaskCenter,
  IMPORT_TASKS_ENDPOINT,
  LOCAL_SESSION_ENDPOINT,
  NAVIGATION_DESTINATIONS,
  PROVIDERS_ENDPOINT,
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
  assert.equal(IMPORT_TASKS_ENDPOINT, "/api/import-tasks");
  assert.equal(IMPORT_FILES_SELECTION_ENDPOINT, "/api/import-selections/files");
  assert.equal(IMPORT_DIRECTORY_SELECTION_ENDPOINT, "/api/import-selections/directory");
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
