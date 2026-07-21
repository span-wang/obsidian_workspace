import assert from "node:assert/strict";
import test from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  App,
  HEALTH_ENDPOINT,
  LOCAL_SESSION_ENDPOINT,
  NAVIGATION_DESTINATIONS
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
  assert.doesNotMatch(markup, /添加 vault|导入资料|配置 Provider/);
});

test("uses relative same-origin endpoints for health and local session checks", () => {
  assert.equal(HEALTH_ENDPOINT, "/api/health");
  assert.equal(LOCAL_SESSION_ENDPOINT, "/api/session");
});
