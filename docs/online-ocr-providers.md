# 在线 OCR Provider 设计

## 目标

为导入任务提供可选的在线文档解析，并为所有 PDF 提供独立的 Markdown 结构化管线选择。在线解析开关只决定 OCR 来源：打开并选择已验证 Provider 时，原始 PDF 及其文件名才会发送到 Provider；关闭时使用本机 PaddleOCR-VL。结构化模式决定选定 DocumentGraph 如何生成 Markdown，用户的选择会保存在浏览器中，直到再次手动变更。

## 范围与安全边界

- 支持 `paddleocr-official` 和 `mineru-official` 两个固定 Provider 类型；不复用 OpenAI-compatible 合同。
- 仅接受 PDF；DOCX、Markdown 和其他资料类型始终维持现有本地路径。
- 每个任务在创建时冻结 `online_parse_enabled`、`provider_id`、`provider_kind`、模型、原件上传策略版本和 `markdown_pipeline`。后续更改设置不会修改排队任务的 OCR 来源、结构化模式或外发目的地。
- `markdown_pipeline` 只适用于 PDF：`ai` 将选定 DocumentGraph 的确定性 Markdown 发送到既有 Markdown Provider；`local` 直接用 `render_document_graph()` 渲染，不调用 Markdown Provider。它与在线解析开关独立组合。
- 创建前检查 Vault outbound policy、Provider 已验证状态和凭据可用性。检查失败时不得上传。
- AI 结构化在派生 Markdown 前单独检查既有 Markdown outbound policy。本机 OCR + AI 结构化只外发派生 Markdown，不上传 PDF 原件；在线 OCR + AI 结构化同时受原件上传和 Markdown 外发策略约束。
- 远端 job/batch ID、状态和安全错误摘要可持久化，以支持服务重启后的继续轮询；不得持久化 token、绝对路径、原始 HTTP 响应或完整 Provider 日志。
- 取消仅停止本机等待和下载；不声称能够取消已经提交给云端的任务。在线 OCR 或 AI 结构化失败均进入可恢复失败，不静默回退到另一 Provider、另一 OCR 来源或本地结构化。
- 未持久化 `markdown_pipeline` 的历史 PDF 任务按原有行为兼容：有在线 OCR 选择时视为 `ai`，没有在线 OCR 选择时视为 `local`。

## 领域与 Port

新增不可变领域对象：

- `OnlineParseSelection`：任务级显式外发选择。
- `OnlineParseJob`：Provider 作业身份、状态、轮询时间和安全错误摘要。
- `OnlineDocumentParseResult`：经 adapter 归一化的 Markdown、页面定位和可下载工件引用。

`ports/online_document_parser.py` 定义最小合同：提交本地快照、查询单个作业、下载已完成结果。application 层只消费归一化结果并复用既有 DocumentGraph/转换工件进入主链路；adapter 负责 HTTP、上传、轮询和 Provider 专有响应。

## PaddleOCR 官方 API

使用官方 `paddleocr` SDK 的文档解析接口，模型固定为 `PaddleOCR-VL-1.6`：

1. 以 `PaddleOCRClient.submit_document_parsing(file_path=..., model="PaddleOCR-VL-1.6")` 提交本地快照。
2. 持久化返回的 `job_id`，通过 `get_status(job_id)` 分段轮询。
3. 作业完成后使用 `wait_document_parsing_result(job)` 或同等结果读取接口，下载 SDK 指向的资源并转换为稳定领域结果。

认证使用 AI Studio access token；默认 base URL 与超时由官方 SDK 控制，用户可在 Provider 设置中覆盖 HTTPS endpoint。连接测试只验证凭据和服务健康，不上传用户文件。

## MinerU 官方 API

使用 MinerU v4 的本地上传工作流：

1. `POST /api/v4/file-urls/batch`，请求带 `Authorization: Bearer <token>`，为任务快照申请上传 URL。
2. 将原始文件 PUT 到返回的签名 URL。上传完成即由 MinerU 自动创建解析任务。
3. 持久化 `batch_id` 与 file `data_id`，通过 `GET /api/v4/extract-results/batch/{batch_id}` 轮询。
4. 结果为 `done` 时下载 `full_zip_url`，读取 Markdown 和结构化 JSON，转换为稳定领域结果。

默认使用 MinerU `vlm` 模型；模型值由固定 allowlist 管理，首版不向任务 UI 暴露任意请求参数。

## 用户界面

在创建导入任务的表单内使用 Switch：

- 标签：`在线解析`；默认开启。
- 打开后显示可用 Provider 的 Select。没有已验证 Provider 时开关不可用，并在控件附近说明需要先完成设置与连接测试。
- 对 PDF 增加 `Markdown 结构化`分段控件：`AI 结构化（现有）` 与 `本地结构化（测试）`。其默认值为前者，并独立于在线解析开关保存和回填；四种 OCR/结构化组合均允许。
- 浏览器会保存上一次选择的已验证 Provider、在线解析开关和 PDF 结构化模式，并在进入任务、离开任务或刷新后回填；控件不会因页面生命周期自动变化，必须由用户手动切换。
- 开关打开时只展示一次外发范围说明：`将把所选原件与文件名发送至所选 Provider。`
- 选择 AI 结构化时展示 Markdown Provider 外发说明。创建后每个 PDF 任务详情显示“结构化：AI 结构化”或“结构化：本地结构化”；在线 OCR 任务另显示“在线解析：PaddleOCR-VL 1.6”或“在线解析：MinerU”及安全的远端状态摘要。不展示 token、URL、远端 job ID 或原始响应。非 PDF 任务不显示 PDF 结构化模式。

设置页将 Provider 按“在线解析”分组。PaddleOCR 与 MinerU 的名称、认证方式、固定模型和 endpoint 由类型决定；保存凭据和点击“连接测试”仍是两个独立操作。

## 验收与测试

- 两个 adapter 均通过伪造 HTTP/SDK 响应验证提交、轮询、完成、失败、超时与取消。
- 服务覆盖：四种 OCR/结构化组合、PDF-only 范围、Markdown outbound policy 拒绝、未验证在线 Provider 拒绝、任务级选择冻结、历史任务兼容、重启后轮询和失败不回退。
- UI 覆盖：无 Provider、在线 OCR 开关、PDF 结构化分段控件、持久化、任务详情模式标签与失败恢复，并在 1440x900、1024x768、390x844 验证键盘与无溢出。
