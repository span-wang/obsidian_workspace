# Obsidian Personal Knowledge Platform

这是 Story 1.1 的可验证本机应用基础。它只提供 React/Vite 前端、FastAPI loopback 服务、依赖锁定和自动化测试入口；不包含 vault、Provider、任务、审核、索引或工作台业务功能。

## 环境基线

- Windows 11+
- Node.js 24.18.0 和 npm 11.8.0
- CPython 3.11.15，标准库 SQLite 3.45.1 或更高版本
- uv 0.11.29

## 初始化

在本目录执行：

```powershell
npm run preflight
npm ci --ignore-scripts
uv sync --project apps/service --all-groups
npx playwright install chromium
```

## 命令

```powershell
npm run preflight
npm run build
npm run lint
npm run unit
npm run integration
npm run browser-test
npm run test
```

`npm run test` 会依次运行前端单元测试、服务单元测试、前端构建、服务集成测试和 Chromium 同源测试。

## 本机启动

```powershell
npm run start
```

服务仅监听 `http://127.0.0.1:6240`。它不会改用其他端口、非回环地址或跨源 API。

`npm run start` 会先执行运行时前置检查和前端构建；若已有可验证的平台实例运行，则复用该实例并成功退出。

若 6240 已被其他进程占用，启动会输出 `Port 6240 is already in use.` 并退出。请先确认并停止占用该端口的进程，再重新执行启动命令。
