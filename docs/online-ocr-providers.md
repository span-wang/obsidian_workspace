# 在线 OCR Provider 设计

## 目标

为导入任务提供可选的在线文档解析。默认仍使用本地解析；只有用户在创建该任务时明确打开“在线解析”并选择已验证 Provider，原始 PDF 及其文件名才会发送到 Provider。

## 范围与安全边界

- 支持 `paddleocr-official` 和 `mineru-official` 两个固定 Provider 类型；不复用 OpenAI-compatible 合同。
- 仅接受 PDF；DOCX、Markdown 和其他资料类型始终维持现有本地路径。
- 每个任务在创建时冻结 `online_parse_enabled`、`provider_id`、`provider_kind`、模型和策略版本。后续更改设置不会修改排队任务的外发目的地。
- 创建前检查 Vault outbound policy、Provider 已验证状态和凭据可用性。检查失败时不得上传。
- 远端 job/batch ID、状态和安全错误摘要可持久化，以支持服务重启后的继续轮询；不得持久化 token、绝对路径、原始 HTTP 响应或完整 Provider 日志。
- 取消仅停止本机等待和下载；不声称能够取消已经提交给云端的任务。Provider 失败进入可恢复失败，不静默回退到另一 Provider 或本地解析。

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

- 标签：`在线解析`；默认关闭。
- 打开后显示可用 Provider 的 Select。没有已验证 Provider 时开关不可用，并在控件附近说明需要先完成设置与连接测试。
- 浏览器会保存上一次选择的已验证 Provider，并在下次打开时回填；在线解析开关仍保持默认关闭，避免将新的 PDF 外发变成自动授权。
- 开关打开时只展示一次外发范围说明：`将把所选原件与文件名发送至所选 Provider。`
- 创建后任务详情显示“在线解析：PaddleOCR-VL 1.6”或“在线解析：MinerU”，以及安全的远端状态摘要；不展示 token、URL、远端 job ID 或原始响应。非 PDF 任务不显示在线解析选择。

设置页将 Provider 按“在线解析”分组。PaddleOCR 与 MinerU 的名称、认证方式、固定模型和 endpoint 由类型决定；保存凭据和点击“连接测试”仍是两个独立操作。

## 验收与测试

- 两个 adapter 均通过伪造 HTTP/SDK 响应验证提交、轮询、完成、失败、超时与取消。
- 服务覆盖：默认本地路径、policy 拒绝、未验证 Provider 拒绝、任务创建选择冻结、重启后轮询和失败不回退。
- UI 覆盖：默认关闭、无 Provider、打开后选择、失败恢复，并在 1440x900、1024x768、390x844 验证键盘与无溢出。
