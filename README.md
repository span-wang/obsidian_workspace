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

服务确认开始监听后会打开默认浏览器中的同源本机工作台，并为该浏览器建立一个仅内存的随机本机会话。应用不提供远程登录或局域网/公网入口。自动化测试使用内部的 `--no-browser` 参数避免打开真实浏览器；日常 `npm run start` 不需要该参数。

`npm run start` 会先执行运行时前置检查和前端构建；若已有可验证的平台实例运行，则复用该实例并成功退出。

若 6240 已被其他进程占用，启动会输出 `Port 6240 is already in use.` 并退出。请先确认并停止占用该端口的进程，再重新执行启动命令。

## Cloudflare Tunnel

`obsidian.panspan.cloud` 使用独立的 `obsidian-panspan-cloud` Tunnel，所有配置和运行文件都位于 `cloudflare/`，不会复用 `C:\Users\panshimao\.cloudflared\config.yml` 或其他域名的配置。它仍然只将请求转发至 `http://127.0.0.1:6240`。

Tunnel 固定使用本机直连，不通过 Mihomo 或其他代理。Windows 需保留以下持久路由，使 Cloudflare Tunnel 边缘网段经物理网关 `192.168.1.1` 出站：

```powershell
route -p add 198.41.192.0 mask 255.255.255.0 192.168.1.1 metric 1 if 11
route -p add 198.41.200.0 mask 255.255.255.0 192.168.1.1 metric 1 if 11
```

上述命令需在管理员 PowerShell 中执行。启动脚本会清除当前进程的代理环境变量；不要为它设置 `HTTP_PROXY`、`HTTPS_PROXY` 或 `ALL_PROXY`。

首次部署时，在本目录执行以下命令创建专用凭据和 DNS 路由。凭据 JSON 不应提交到 Git：

```powershell
cloudflared tunnel create --credentials-file .\cloudflare\obsidian-panspan-cloud.json obsidian-panspan-cloud
cloudflared tunnel route dns obsidian-panspan-cloud obsidian.panspan.cloud
```

先启动本机工作台，再建立 Tunnel：

```powershell
npm run start
.\cloudflare\start-obsidian-tunnel.ps1
```

默认以前台运行，使用 `-Background` 可在后台运行并将 PID 与标准输出/错误日志写入 `cloudflare/`。停止后台 Tunnel 时，读取该 PID 后结束对应的 `cloudflared` 进程并删除 PID 文件。

此 Tunnel 不使用 Cloudflare Access。任何能够访问 `obsidian.panspan.cloud` 的人都可以打开工作台；不要将需要身份验证保护的数据或 Provider 凭据暴露在该实例中。
