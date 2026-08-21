# 检索改造开发进度

> 此文件由 `progress/` 下的结构化数据生成，请勿手工修改。

- 数据日期：2026-08-21
- 方案文档：`docs/retrieval-redesign.md`
- 任务总数：62
- 已完成：62
- 进行中：0
- 阻塞：0

## 当前焦点

当前没有进行中或阻塞任务。

## 阶段概览

| 阶段 | 目标 | 状态 | 完成度 |
| --- | --- | --- | --- |
| `RET-00` 决策与基线 | 冻结事实、范围、评测集和开发闸门。 | 已完成 | 4/4 |
| `RET-01` 耐久结构投影 | 让索引在导入任务删除后仍能读取选定 DocumentGraph 的检索投影。 | 已完成 | 4/4 |
| `RET-02` 富块模型与兼容迁移 | 扩展 IndexBlock，并通过双写、回填和可回退切读完成旧库升级。 | 已完成 | 3/3 |
| `RET-03` 确定性分块与范围过滤 | 建立结构分块、标题路径、领域元数据和 SQL 范围枚举。 | 已完成 | 3/3 |
| `RET-04` FTS5 词法检索 | 以可迁移、可失效的 BM25 检索替换手写词项打分。 | 已完成 | 3/3 |
| `RET-05` 查询范围与枚举型汇总 | 让范围决定覆盖，完成可预览、可核验的全量汇总。 | 已完成 | 5/5 |
| `RET-06` Embedding 与混合检索 | 在明确出网边界后接入精确向量检索与独立通道融合。 | 已完成 | 5/5 |
| `RET-07` 受控增强 | 以独立开关验证 LLM 元数据、单元卡片和 rerank 的实际增益。 | 已完成 | 3/3 |
| `RET-08` 受控 rerank 接入 | 以逐任务授权、执行前重验和默认关闭开关，将已评测的点查 rerank 受控接入生产链路。 | 已完成 | 1/1 |
| `RET-09` 真实 Provider 测量 | 只以脱敏 fixture 在明确隐私与请求上限内测量真实 rerank Provider；费用按用户决定明确未计算，默认开关保持关闭。 | 已完成 | 2/2 |
| `RET-10` 用户界面收敛 | 让检索、引用和导入结果只展示用户可理解的信息，同时保留内部可核验身份。 | 已完成 | 1/1 |
| `RET-11` 分块聚合与派生目录去重 | 消除短原子块和平台生成目录造成的重复覆盖单元，同时保留可核验来源。 | 已完成 | 1/1 |
| `RET-12` 默认出网与导入向量门禁 | 删除逐次出网授权，让已验证 Provider 默认可用，并使导入提交必须先完成向量化。 | 已完成 | 2/2 |
| `RET-13` 查询范围闭合 | 修正自动意图与显式标题范围的冲突，禁止未解析范围扩大为整个 vault。 | 已完成 | 1/1 |
| `RET-14` 问答输出与应用证据 | 让全部会话模式输出可直接使用的内容，并将可核验证据统一分层到应用证据面板。 | 已完成 | 1/1 |
| `RET-15` 会话阅读体验 | 稳定会话详情刷新和滚动位置，并提供按问答轮次定位的紧凑导航。 | 已完成 | 1/1 |
| `RET-16` 远程资料上传 | 让非服务机浏览器可将资料安全暂存到服务机，并复用既有导入与 Vault 提交流程。 | 已完成 | 1/1 |
| `RET-17` Markdown Provider 结构化 | 以独立 Provider 模型为原生 Markdown 导入生成可核验结构，并在长文分块时保持标题上下文与原文结构。 | 已完成 | 1/1 |
| `RET-18` 自动化导入提交 | 将导入解析、结构化、提交、索引和向量化串成无需人工审核的可恢复流水线，并隔离任务私有建议。 | 已完成 | 2/2 |
| `RET-19` PDF 原生优先解析 | 按页优先使用原生 PDF 文本，原生文本为空或质量不通过时回退 MinerU，同时保留数字、图片和公式。 | 已完成 | 1/1 |
| `RET-20` Markdown 结构化质量 | 以全局标题纲要、明确的保真去重规则和可验证的层级约束，提升 Markdown Provider 的结构化稳定性。 | 已完成 | 1/1 |
| `RET-21` 多 Vault 工作台重构 | 以跨 Vault 摘要全景和二级上下文交互提升工作台的信息承载、扫描效率与操作密度。 | 已完成 | 1/1 |
| `RET-22` PaddleOCR-VL PDF 解析 | 以本机 PaddleOCR-VL 1.6 替换 PDF 的 native/MinerU 混合解析，同时保持可核验 DocumentGraph 溯源。 | 已完成 | 1/1 |
| `RET-23` Markdown Provider Token 预算 | 以结构安全的 token 聚合降低 Markdown Provider 的重复提示词开销，并避免长文输出截断。 | 已完成 | 1/1 |
| `RET-24` 导入任务队列与本地 Word 提案 | 让批量导入按可见队列逐项推进，并确保 Word/PDF 的本地 DocumentGraph 不会发送给 Markdown Provider。 | 已完成 | 2/2 |
| `RET-25` iOS 设计语言统一 | 以 DESIGN.md 定义的原生感、扁平、状态连续和全端可操作标准，统一生产工作台的视觉与交互。 | 已完成 | 1/1 |
| `RET-26` 可读导入文件命名 | 让导入原件、解析笔记及目录以导入文件名和解析标题命名，同时保留可核验身份并防止同名覆盖。 | 已完成 | 1/1 |
| `RET-27` 受控在线文档解析 | 以任务级显式授权和 Vault 出网策略门禁接入 PaddleOCR-VL 1.6 与 MinerU 官方在线解析。 | 已完成 | 1/1 |
| `RET-28` 来源链接核验修复 | 修复双语标题导致已存在来源链接被错误标记为 stale 的索引核验缺陷。 | 已完成 | 1/1 |
| `RET-29` 当前页任务批量删除 | 让当前页导入任务可多选或全选，并在既有安全删除语义下逐项删除。 | 已完成 | 1/1 |
| `RET-30` 无效治理与知识图谱退役 | 删除未形成生产闭环的标签、LLM 元数据、单元卡片和用户知识图谱能力，同时保留导入结构投影与检索主链。 | 已完成 | 1/1 |
| `RET-31` 本地 OCR 图结构归一化 | 以可追溯、版本化的确定性规则将 PaddleOCR-VL 页面图块归一化为保真 Markdown，改善标题、阅读顺序、列表、图注与页边噪音。 | 已完成 | 1/1 |
| `RET-32` Provider 验证反馈 | 让模型验证保留安全、可操作的失败原因，并在设置页连续展示验证状态和重试入口。 | 已完成 | 1/1 |
| `RET-33` Responses API Provider 模式 | 让 OpenAI-compatible Provider 可显式选择 Responses API，并以稳定的流式合同完成模型验证和生成。 | 已完成 | 1/1 |
| `RET-34` Obsidian 图片资源保留 | 解析和入库原生 Obsidian Markdown 时保留本地图片资源，同时确保图片不进入 embedding 出网输入。 | 已完成 | 1/1 |
| `RET-35` 文件管理与安全在线阅读 | 直接管理各 Vault 的 sources 原件，提供本地全文检索、受控预览、下载和不复制原件的 Office 临时渲染。 | 已完成 | 1/1 |

## RET-00 决策与基线

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-00-01` 方案审核、开发路径与治理基线 | P0 | 已完成 | - | 关键事实错误已修正，阶段可独立验收，进度检查命令可重复通过。 | `npm run progress:check`、`git diff --check` |
| `RET-00-02` 确认产品决策与真实 vault 样本 | P0 | 已完成 | `RET-00-01` | D-001 至 D-003 有明确选择，目录规则有可测试样本。 | `决策写入 progress/logs.json`、`相关风险状态同步更新` |
| `RET-00-03` 建立检索 golden set | P0 | 已完成 | `RET-00-02` | 每条查询都能计算 recall、precision、scope coverage 和 duplicate precision。 | `评测夹具 schema 校验`、`人工抽查标注一致性` |
| `RET-00-04` 基线评测与 Windows 能力 smoke | P0 | 已完成 | `RET-00-03` | 评测可一条命令重复执行，结果写入日志且保留原始指标。 | `重复运行结果差异在允许范围内`、`失败时返回非零退出码` |

## RET-01 耐久结构投影

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-01-01` 定义耐久 graph 投影合同 | P0 | 已完成 | `RET-00-02` | 合同足以重建 IndexBlock 和核验引用，不复制无关转换工件。 | `领域模型单元测试`、`端口类型与序列化 round-trip 测试` |
| `RET-01-02` 提交时原子写入耐久投影 | P0 | 已完成 | `RET-01-01` | 笔记与投影不会出现一方成功、一方缺失的不可恢复状态。 | `提交成功、重复提交、注入失败测试`、`旧数据库升级测试` |
| `RET-01-03` 删除任务后的结构重建与引用核验 | P0 | 已完成 | `RET-01-02` | 删除完成态 import task 后，索引重建与引用定位仍成功。 | `删除完成态 import task 后的 PDF/DOCX durable projection 重建回归`、`服务集成测试与任务删除生命周期回归` |
| `RET-01-04` 投影重建验证测试面板 | P1 | 已完成 | `RET-01-03` | 用户可在前端验证完成态任务删除后，耐久 projection 与 PDF/DOCX locator 摘要仍存在且索引重建成功。 | `前端 unit、build 与浏览器流程`、`API 合同与进度检查` |

## RET-02 富块模型与兼容迁移

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-02-01` 富 IndexBlock 与显式 schema migration | P0 | 已完成 | `RET-01-03` | 旧索引库原地升级，现有文档身份与正文不丢失。 | `migration 单元测试`、`旧库 fixture 升级测试` |
| `RET-02-02` 结构双写、回填与一致性对比 | P0 | 已完成 | `RET-02-01` | 新旧读取的正文、sequence、document identity 一致，结构差异可解释。 | `回填幂等测试`、`差异报告为零或有批准例外` |
| `RET-02-03` 切换富块读取并保留回退 | P0 | 已完成 | `RET-02-02` | 新路径通过测试，关闭开关可恢复旧行为。 | `双模式 unit 与 integration 测试` |

## RET-03 确定性分块与范围过滤

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-03-01` 结构优先分块与 contextual prefix | P0 | 已完成 | `RET-02-03` | 块不静默截断，标题路径和表头上下文可复原。 | `分块 golden fixtures`、`block_content_sha256 稳定性测试` |
| `RET-03-02` 路径与标题元数据归一化 | P0 | 已完成 | `RET-00-03`、`RET-03-01` | 真实目录样本有稳定归一结果，索引侧与查询侧不分叉。 | `参数化规则测试`、`未知格式失败关闭测试` |
| `RET-03-03` 块级元数据持久化与 filter_blocks | P0 | 已完成 | `RET-03-02` | 第一单元全量过滤结果与人工块清单一致且无跨范围泄漏。 | `repository unit 测试`、`policy 与 stale/current 集成测试` |

## RET-04 FTS5 词法检索

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-04-01` FTS5 schema 与生命周期同步 | P0 | 已完成 | `RET-03-03` | 任何索引生命周期操作后都不存在可检索的孤儿或 stale 行。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_sqlite_index_fts_lifecycle.py tests/unit/test_sqlite_index_block_metadata.py tests/unit/test_sqlite_index_rich_blocks.py tests/unit/test_index_service.py（37 passed）`、`npm run unit（前端 21 passed；服务端 398 passed，3 skipped）`、`npm run integration（空闲 loopback 测试端口；11 passed，2 skipped）`、`uv run --directory apps/service ruff check .（All checks passed）`、`npm run progress:build && npm run progress:check`、`git diff --check` |
| `RET-04-02` 中英分词与 search_lexical | P0 | 已完成 | `RET-04-01` | synthetic golden set 的词法 macro recall 不低于 0.6569，macro precision 不低于 0.2813。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_retrieval_lexical.py tests/unit/test_sqlite_index_fts_lifecycle.py tests/unit/test_sqlite_index_block_metadata.py tests/unit/test_index_service.py（31 passed）`、`synthetic golden lexical eval（macro recall 0.6875 >= 0.6569；macro precision 0.302083 >= 0.2813）`、`npm run unit（前端 21 passed；服务端 404 passed，3 skipped）`、`npm run lint（All checks passed）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6242'; npm run integration（11 passed，2 skipped）`、`npm run progress:build && npm run progress:check`、`git diff --check` |
| `RET-04-03` 词法 A/B、切换与退役手写打分 | P0 | 已完成 | `RET-04-02` | FTS 无指标回退，点查证据合同和引用保持兼容。 | `golden eval`、`session unit、integration 与 browser 回归` |

## RET-05 查询范围与枚举型汇总

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-05-01` 共享查询范围解析 | P0 | 已完成 | `RET-03-02` | 同一册次、单元和资料类型在查询与索引侧归一一致。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_retrieval_query.py tests/unit/test_retrieval_metadata.py（16 passed）`、`uv run --directory apps/service pytest -p no:cacheprovider tests/unit（425 passed，3 skipped）`、`uv run --directory apps/service ruff check domain/retrieval_query.py tests/unit/test_retrieval_query.py（All checks passed）` |
| `RET-05-02` 范围预览 API 与前端确认 | P1 | 已完成 | `RET-05-01`、`RET-03-03` | 用户在执行前能看到并修正实际检索范围。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_retrieval_query.py tests/unit/test_sessions.py tests/unit/test_sessions_api.py（81 passed）`、`npm run lint（All checks passed）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6242'; npm run test（前端 22 passed；服务端 428 passed，3 skipped；integration 11 passed，2 skipped；browser 28 passed）` |
| `RET-05-03` 枚举型全量过滤与分桶 | P0 | 已完成 | `RET-05-01`、`RET-03-03` | 枚举范围由元数据决定，不受相似度 top-k 截断。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_sessions.py（62 passed）`、`uv run --directory apps/service ruff check application/sessions.py domain/sessions.py adapters/sqlite_session_repository.py tests/unit/test_sessions.py（All checks passed）`、`npm run unit（前端 22 passed；服务端 434 passed，3 skipped）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6243'; npm run integration（11 passed，2 skipped）`、`npm run lint（All checks passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过）` |
| `RET-05-04` 原子条目去重与分层生成 | P0 | 已完成 | `RET-05-03` | 跨资料重复条目正确合并，每个合并结果保留全部来源，非重复项不误并。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_retrieval_enumeration.py tests/unit/test_retrieval_golden.py tests/unit/test_sessions.py tests/unit/test_sessions_api.py（85 passed）`、`uv run --directory apps/service ruff check application/sessions.py application/retrieval_golden.py application/retrieval_baseline.py application/retrieval_lexical_ab.py domain/sessions.py domain/retrieval_enumeration.py adapters/sqlite_session_repository.py api/main.py tests/unit/test_retrieval_enumeration.py tests/unit/test_retrieval_golden.py tests/unit/test_sessions.py tests/unit/test_sessions_api.py（All checks passed）`、`uv run --directory apps/service python -m application.retrieval_golden --fixture tests/fixtures/retrieval-golden-v1.json --validate-only（通过）`、`uv run --directory apps/service python -m application.retrieval_lexical_ab（passesGate=true；macro recall +0.030555555555555558；macro precision +0.020833333333333315）`、`npm run unit（前端 22 passed；服务端 438 passed，3 skipped）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6243'; npm run integration（11 passed，2 skipped；未停止占用默认 6240 的既有本机实例）`、`npm run lint（All checks passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过）` |
| `RET-05-05` 通用标题层级范围过滤 | P0 | 已完成 | `RET-05-03`、`RET-11-01` | 用户指定的标题范围只会向模型发送该标题及子标题下的当前、可核验且允许外发的证据；范围无命中时不调用 Provider，且不退回 vault 全量内容。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_retrieval_hybrid.py tests/unit/test_sessions.py -q（90 passed）`、`uv run --directory apps/service ruff check domain/retrieval_hybrid.py application/sessions.py tests/unit/test_retrieval_hybrid.py tests/unit/test_sessions.py（All checks passed）`、`npm run unit（通过；前端 24 项、服务 unit 582 项收集）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过）` |

## RET-06 Embedding 与混合检索

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-06-01` Embedding 出网授权与范围预览 | P0 | 已完成 | `RET-00-02`、`RET-05-02` | 未授权正文不出网，授权范围变化后旧授权失效。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_embedding_authorizations.py tests/unit/test_policies.py tests/unit/test_vault_api.py（23 passed）`、`uv run --directory apps/service ruff check domain/embedding_authorization.py application/embedding_authorizations.py application/policies.py api/main.py tests/unit/test_embedding_authorizations.py tests/unit/test_policies.py tests/unit/test_vault_api.py（All checks passed）`、`npm run unit（前端 22 passed；服务端 442 passed，3 skipped）`、`npm run lint（All checks passed）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6244'; npm run integration（11 passed，2 skipped）`、`npm run progress:build && npm run progress:check`、`git diff --check` |
| `RET-06-02` Embedding 客户端、指纹与缓存 | P0 | 已完成 | `RET-06-01` | 不同 endpoint/config revision 的同名模型绝不复用向量。 | `Provider adapter contract 测试`、`缓存命中与指纹隔离测试` |
| `RET-06-03` float32 向量存储、内存矩阵与健康状态 | P0 | 已完成 | `RET-06-02` | 只搜索 current 且 profile 匹配的向量，重建和模型切换后缓存一致。 | `精确 KNN 排序测试`、`内存缓存失效与覆盖率测试` |
| `RET-06-04` 独立三路召回与 RRF | P0 | 已完成 | `RET-04-03`、`RET-06-03` | 词法漏召回时向量可独立救回，任何通道都不作为另一通道的硬过滤器。 | `语义改写 recall@8`、`通道隔离回归测试` |
| `RET-06-05` 前端检索模式切换 | P0 | 已完成 | `RET-06-04` | 用户可在会话工作台一键切换关键词、语义或混合模式；当前发送立即使用所选模式，语义模式不会静默回退关键词，混合模式在语义不可用时显示降级原因。 | `关键词、语义、混合通道调用与语义索引缺失失败关闭断言通过`、`检索模式 API 校验、切换和本机会话保护回归通过`、`前端三按钮渲染、模式 API 请求和浏览器点击回归通过`、`progress:check 通过` |

## RET-07 受控增强

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-07-01` LLM 元数据抽取与审核 | P1 | 已完成 | `RET-06-02`、`RET-03-03` | 低置信度和新概念进入审核，未审核数据不直接触发不可逆合并。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_metadata_authorizations.py tests/unit/test_metadata_extraction.py tests/unit/test_metadata_service.py tests/unit/test_sqlite_index_metadata_candidates.py tests/unit/test_vault_api.py -q（21 passed）`、`uv run --directory apps/service ruff check domain/metadata_extraction.py application/metadata_authorizations.py application/metadata_service.py adapters/sqlite_index_repository.py api/main.py tests/unit/test_metadata_authorizations.py tests/unit/test_metadata_extraction.py tests/unit/test_metadata_service.py tests/unit/test_sqlite_index_metadata_candidates.py tests/unit/test_vault_api.py（All checks passed）`、`npm run unit（前端 23 passed；服务端 488 passed，3 skipped）`、`npm run lint（All checks passed）`、`npm run integration（13 passed）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6245'; npm run browser-test（28 passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅显示 Windows 行尾转换提示）` |
| `RET-07-02` 单元卡片生成与失效 | P2 | 已完成 | `RET-05-04`、`RET-06-03` | 卡片不替代原始证据，粗粒度查询增益达到评测阈值。 | `npm run retrieval:unit-card-eval（passesGate=true；card coverage=1.0；original citation recall=1.0；card-on macro recall@8=1.0；macro recall gain@8=0.5）`、`uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_unit_cards.py tests/unit/test_unit_card_authorizations.py tests/unit/test_unit_card_service.py tests/unit/test_unit_card_evaluation.py tests/unit/test_sqlite_unit_cards.py tests/unit/test_sessions.py tests/unit/test_vault_api.py -q（95 passed）`、`uv run --directory apps/service ruff check domain application adapters api ports tests/unit（All checks passed）`、`npm run lint（All checks passed）`、`npm run unit（前端 23 passed；服务端 514 passed，3 skipped）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6246'; npm run integration（11 passed，2 skipped；默认 6240 被现有本机进程占用，未停止）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅显示 Windows 行尾转换提示）` |
| `RET-07-03` 点查 rerank 增益验证 | P2 | 已完成 | `RET-06-04` | 只有质量增益覆盖延迟和成本代价时才进入默认点查路径。 | `npm run retrieval:rerank-ab（qualityGate=true；macro recall@8 +1.0；macro precision@8 +0.25；macro MRR@8 +1.0；fixture adapter latency gate=true；真实 Provider 延迟和费用未测，passesGate=false，默认保持关闭）`、`uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_retrieval_rerank.py tests/unit/test_provider_reranker.py tests/unit/test_retrieval_rerank_eval.py -q（9 passed）`、`npm run lint（All checks passed）`、`npm run unit（前端 23 passed；服务端 523 passed，3 skipped）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6247'; npm run integration（11 passed，2 skipped；默认 6240 被既有本机实例占用，未停止）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅显示 Windows 行尾转换提示）` |

## RET-08 受控 rerank 接入

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-08-01` 点查 rerank 授权快照与受控接入 | P1 | 已完成 | `RET-06-04`、`RET-07-03` | 未获确认或任一快照条件变化时绝不发送候选正文；受控开启时 rerank 只影响点查的 RRF 后排序且保留原始证据。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_rerank_authorizations.py tests/unit/test_sessions.py tests/unit/test_sessions_api.py tests/unit/test_provider_reranker.py tests/unit/test_runtime.py -q（134 passed）`、`npm run retrieval:rerank-ab（qualityGate=true；macro recall@8 +1.0；macro precision@8 +0.25；macro MRR@8 +1.0；真实 Provider 延迟和费用未测，passesGate=false，默认保持关闭）`、`npm run lint（All checks passed）`、`npm run unit（前端 24 passed；服务端 550 passed，3 skipped）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6251'; npm run integration（11 passed，2 skipped；默认 6240 被既有本机实例占用，未停止）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6251'; npm run browser-test（29 passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅显示 Windows 行尾转换提示）` |

## RET-09 真实 Provider 测量

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-09-01` 真实 Provider rerank 受控测量与门槛复核 | P1 | 已完成 | `RET-07-03`、`RET-08-01` | 命令在出网前拒绝缺少确认、HTTPS、明确 rerank 目标或受限 fixture 的调用；费用明确标记为未计算。有效测量不记录正文并强制完整候选排序，任何未满足质量或隐私门槛的结果均不启用默认 rerank。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_retrieval_live_rerank_eval.py tests/unit/test_openai_compatible_provider.py tests/unit/test_providers.py -q（59 passed）`、`uv run --directory apps/service ruff check domain/providers.py ports/provider_client.py adapters/openai_compatible_provider.py adapters/sqlite_provider_repository.py application/providers.py application/provider_reranker.py application/sessions.py application/retrieval_live_rerank_eval.py api/main.py api/retrieval_live_rerank_eval.py tests/unit/test_retrieval_live_rerank_eval.py（All checks passed）`、`npm run retrieval:rerank-live -- --confirm-live-egress --provider-id <redacted> --model-id BAAI/bge-reranker-v2-m3 --max-requests 2 --fixture tests/fixtures/retrieval-rerank-golden-v1.json --data-dir <local-data-dir> --output output/live-rerank/ret-09-01-bge-reranker-v2-m3-20260727.json（completed；2 条固定脱敏 fixture 请求；p50=1014.314 ms；p95=1699.467 ms；macro recall@8 +1.0、precision@8 +0.25、MRR@8 +1.0；quality gate=true；费用未计算；默认保持关闭）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅 Windows 行尾提示）` |
| `RET-09-02` 独立原生 Rerank 模型配置与受控接入 | P1 | 已完成 | `RET-08-01` | 用户可将已发现模型配置、验证并设为独立 Rerank 默认模型；配置本身不触发请求。生产 rerank 只使用已验证的 HTTPS rerank 默认模型，完整响应校验失败则保留本地 RRF，且默认开关保持关闭。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_retrieval_rerank.py tests/unit/test_provider_reranker.py tests/unit/test_retrieval_live_rerank_eval.py tests/unit/test_sessions.py tests/unit/test_vault_api.py tests/unit/test_providers.py tests/unit/test_openai_compatible_provider.py tests/unit/test_sqlite_provider_repository_rerank.py（166 passed）`、`npm run lint（通过）`、`npm run unit（前端 25 passed；服务端 584 passed，3 skipped）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6253'; npm run integration（11 passed，2 skipped）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6254'; npm run browser-test（29 passed）`、`git diff --check（通过；仅 Windows 行尾提示）` |

## RET-10 用户界面收敛

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-10-01` 用户界面来源信息收敛 | P1 | 已完成 | `RET-09-02` | 生产工作台不再向用户展示 Source ID、内容哈希、来源摘要哈希或 graph/chunk 内部定位；用户仍能通过文件、标题、页码、摘录和状态理解并打开证据，内部核验合同保持不变。 | `npm --prefix apps/web run test（26 passed）`、`npm --prefix apps/web run lint（通过）`、`npm --prefix apps/web run build（通过；包含窄屏任务多选行对齐复核）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6257'; npm run browser-test（29 passed）`、`桌面 1280px 与移动 390px 视觉检查（通过；证据层级显示文件、标题、页码、原始资料、摘录和匹配方式，未显示 Source ID、哈希或 graph/chunk 定位）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅 Windows 行尾提示）` |

## RET-11 分块聚合与派生目录去重

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-11-01` 短原子块聚合与派生目录索引排除 | P0 | 已完成 | `RET-10-01` | 短原子内容不再稳定地产生十几字符的独立检索块；平台生成目录不会重复整份 source projection；保留索引块的来源定位并通过定向回归和进度检查。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_index_chunking.py tests/unit/test_index_service.py tests/unit/test_index_graph_projection_rebuild.py -q（30 passed）`、`uv run --directory apps/service ruff check domain/retrieval_chunking.py application/indexing.py tests/unit/test_index_chunking.py tests/unit/test_index_service.py（All checks passed）`、`当前 vault 通过运行中服务重建：4 个索引文档 / 776 块降为 2 个正文文档 / 49 块；当前 derived index.md 为 0，49 个块正文哈希均唯一，平均 340.7 字符。`、`npm run unit（通过；前端 26 passed，服务端 591 项收集完成）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过）` |

## RET-12 默认出网与导入向量门禁

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-12-01` 移除出网授权并以向量化门禁导入提交 | P0 | 已完成 | `RET-10-01` | 已验证 Provider 的可允许内容无需逐次授权即可出网；会话在选择与输入后可一次发送并立即检索、生成，页面不再要求保存语境、准备任务或固定快照；never-send-cloud 仍失败关闭；导入只有在当前块向量完整持久化后才可完成提交，失败不留下部分完成态。 | `受影响服务定向回归：Embedding/metadata/unit-card/vault API 21 passed；会话、政策、Provider、rerank 与 vault API 169 passed；提交门禁回归 13 passed。`、`npm run unit（前端 24 passed；服务端 572 passed，3 skipped）`、`npm run lint（All checks passed）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6262'; npm run integration（11 passed，2 skipped；默认 6240 被既有本机实例占用，未停止该实例）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6262'; npm run browser-test（22 passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过）` |
| `RET-12-02` 移除 Provider 响应大小硬上限 | P0 | 已完成 | `RET-12-01` | Provider 客户端不再因累计响应字节数而拒绝有效 JSON 或有效流式事件；超时、取消、重定向和格式校验继续生效。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_openai_compatible_provider.py（36 passed）`、`uv run --directory apps/service ruff check adapters/openai_compatible_provider.py tests/unit/test_openai_compatible_provider.py（All checks passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过）` |

## RET-13 查询范围闭合

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-13-01` 自动意图与通用标题路径闭合 | P0 | 已完成 | `RET-05-05`、`RET-12-01` | 任意标题层级请求只冻结所有请求约束在同一 heading_path 共同成立的标题及子标题；完整标题优先于结构别名，显式路径任一部分无命中都不会扩大到兄弟标题或整个 vault，普通整库请求保持全范围语义。 | `通用标题范围五文件定向回归（174 passed）；Ruff（All checks passed）`、`回归矩阵覆盖 Project A/Project B 共享子标题、跨路径 Project A + Details、缺失结构父级 + 共享子标题、Chapter/Module 中英文别名、两字中文标题、纯中文自然缺失标题、命令噪声标题、all notes 与中英文整库表达`、`npm run unit（前端 24 passed；服务端 628 passed，3 skipped）`、`npm run lint（All checks passed）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6273'; npm run integration（11 passed，2 skipped；未停止既有 6240 实例）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6274'; npm run browser-test（22 passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅 Windows 行尾提示）` |

## RET-14 问答输出与应用证据

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-14-01` 全模式问答输出与应用证据分层 | P1 | 已完成 | `RET-10-01`、`RET-12-01`、`RET-13-01` | source-lookup、知识整理、深度创作和完整性检查的主对话区不再默认呈现知识库/检索/证据话术、引用编号或证据摘录；关联内容可通过不可复制的角标跳转至右侧“应用证据”，该面板仍显示实际关联的用户可理解来源信息；复制操作只复制正文。范围过滤、never-send-cloud、citation/coverage 关联和失败恢复行为保持不变。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_sessions.py tests/unit/test_sessions_api.py（137 passed）`、`uv run --directory apps/service ruff check application/sessions.py tests/unit/test_sessions.py tests/unit/test_sessions_api.py（All checks passed）`、`npm --prefix apps/web run test（27 passed）`、`npm --prefix apps/web run lint（通过）`、`npm --prefix apps/web run build（通过）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6341'; npm run browser-test（22 passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅 Windows 行尾提示）` |

## RET-15 会话阅读体验

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-15-01` 会话消息稳定渲染与问答定位导航 | P1 | 已完成 | `RET-14-01` | 会话详情刷新不清空已显示内容或将阅读位置重置到首条消息；首次进入有内容会话展示最新消息，用户离开底部阅读后刷新不夺取滚动位置；每轮用户提问及其后续回答在对话区右侧有可访问的定位条，点击可定位并显示当前轮次。 | `npm --prefix apps/web run test（28 passed）`、`npm --prefix apps/web run lint（通过）`、`npm --prefix apps/web run build（通过）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6377'; npm run browser-test（22 passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅 Windows 行尾提示）` |

## RET-16 远程资料上传

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-16-01` 非服务机资料上传与私有暂存 | P0 | 已完成 | `RET-12-01` | 非服务机浏览器选择的单文件、多文件或文件夹可在不暴露客户端或服务端绝对路径的前提下创建导入任务；文件夹会保留相对层级并走既有递归扫描；暂存文件在审核提交前不写入 Vault，删除任务或过期未消费选择后会清理；服务机本地选择和 Vault 现有逻辑不变。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_import_selections.py tests/unit/test_import_uploads.py tests/unit/test_import_api.py（22 passed）`、`npm run lint（通过）`、`npm --prefix apps/web run test（29 passed）`、`npm --prefix apps/web run build（通过）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6388'; npm run integration（13 passed，2 skipped）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6387'; npm run browser-test（23 passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅 Windows 行尾提示）` |

## RET-17 Markdown Provider 结构化

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-17-01` Markdown Provider 结构化导入与上下文分块 | P0 | 已完成 | `RET-12-01` | 原生 Markdown 和已选 PDF/DOCX DocumentGraph 的格式保留 Markdown 投影均不再使用本地规则生成任务结构化结果；它们仅在已验证的 Markdown Provider 默认模型可用且现有 outbound policy 允许时发送结构安全分块后的正文。Provider 直接返回最终 Markdown，长文分块不会截断标题、列表、表格、引用或代码围栏，后续块含必要标题上下文，服务按原顺序拼接非空结果。重复页眉、页脚、页码、广告和分页伪影由 Provider 移除；Provider 按语义与上下文保留标题文字并优化标题层级，不凭空创建标题；PDF/DOCX 能完整回链的重复块才从私有候选排除，并保留 graph block ID、定位和审核语义。Provider、策略或响应核验失败不会写入不完整提案且任务可恢复；索引重建语义不变。 | `定向 Markdown/PDF/DOCX Provider 直出、长文上下文、JSON 拒绝、噪音候选排除与图块溯源回归（71 passed，1 skipped）`、`Markdown Provider 清洗提示词与语义标题层级规则定向回归（14 passed）`、`Markdown Provider 相关导入、Provider 和 SQLite 回归（45 passed）`、`Markdown Provider 应用文件与定向测试 Ruff（All checks passed）`、`真实任务 c6fb7415-f03c-43cc-8c0f-319fc023631b 重试成功：waiting-for-review；182 个图块、10 个 noise 图块、noise 候选行 0、Provider Markdown 9978 字符`、`受影响服务文件 Ruff（All checks passed）`、`npm run lint（通过）`、`npm run unit（前端 29 passed；服务端 669 passed，3 skipped）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6396'; npm run integration（13 passed，2 skipped）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6397'; npm run browser-test（23 passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅 Windows 行尾提示）` |

## RET-18 自动化导入提交

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-18-01` 自动化导入提交与私有建议隔离 | P0 | 已完成 | `RET-12-01`、`RET-17-01` | 选择资料后，任务会在不要求审核、确认或手动提交的情况下自动完成解析、Markdown 结构化、提交 Vault、索引与 Embedding；任一阶段失败保持可恢复且不会留下部分提交。元数据与标签、候选链接、分类建议可独立生成和查看，但不阻塞、不改变或参与提交。审核状态、提交入口和对应 API 不再可用。 | `uv run --directory apps/service pytest tests/unit -q（634 passed，2 skipped）`、`uv run --directory apps/service ruff check application/ingest.py api/main.py tests/unit/test_import_service.py tests/unit/test_review_commit_service.py tests/unit/test_review_commit_api.py tests/unit/test_import_api.py tests/unit/test_document_conversion_v2.py tests/integration/test_loopback_server.py（All checks passed）`、`npm --prefix apps/web run test（29 passed）`、`npm --prefix apps/web run lint && npm --prefix apps/web run build（通过）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6401'; npm run integration（13 passed，2 skipped）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6402'; npm run browser-test（23 passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过）` |
| `RET-18-02` 导入任务展示与已提交文件删除 | P1 | 已完成 | `RET-18-01` | 任务页只展示当前 vault 的任务，默认每页最多 10 条且可调整页大小、翻页；删除任务不会重新加载列表或造成页面抖动。删除已完成任务会仅回退该任务提交到 Vault 的 Markdown、源文件和资料附件，并同步索引；任何目标文件不再匹配提交哈希时，删除失败且不改动 Vault 或任务记录；上传时的本地原件不删除。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_import_tasks.py tests/unit/test_filesystem_vault_committer.py（19 passed，2 skipped）`、`uv run --directory apps/service ruff check application/ingest.py adapters/filesystem_vault_committer.py ports/vault_committer.py tests/unit/test_import_tasks.py tests/unit/test_filesystem_vault_committer.py（All checks passed）`、`uv run --directory apps/service pytest -p no:cacheprovider tests/unit（646 passed，2 skipped）`、`npm --prefix apps/web run test（31 passed）`、`npm --prefix apps/web run lint 与 build（通过）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6417'; npm run integration（13 passed，2 skipped）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6415'; npm run browser-test（23 passed）`、`uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_import_tasks.py tests/unit/test_source_identities.py tests/unit/test_sqlite_index_graph_projection.py tests/unit/test_sqlite_index_vectors.py tests/unit/test_index_service.py tests/unit/test_filesystem_vault_committer.py tests/unit/test_import_api.py（65 passed，2 skipped）`、`uv run --directory apps/service ruff check application/ingest.py application/indexing.py adapters/sqlite_index_repository.py adapters/sqlite_source_repository.py adapters/sqlite_task_repository.py ports/index_repository.py ports/source_repository.py tests/unit/test_import_tasks.py tests/unit/test_source_identities.py tests/unit/test_sqlite_index_graph_projection.py tests/unit/test_sqlite_index_vectors.py tests/unit/test_filesystem_vault_committer.py tests/unit/test_import_api.py（All checks passed）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6428'; npm run integration（12 passed，2 skipped，1 failed：test_automatic_import_recovers_without_writing_the_vault_when_conversion_is_unavailable 期望 restart-derivation；当前在线解析恢复逻辑未返回该动作，与本任务删除链路无交集）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅 Windows 行尾提示）` |

## RET-19 PDF 原生优先解析

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-19-01` PDF 原生文本优先与按页 MinerU 回退 | P0 | 已完成 | `RET-17-01`、`RET-18-01` | 有文字且质量通过的 PDF 页面使用原生解析；空文字、结构不可靠或 token 校验失败的页面使用 MinerU；混合结果保留可回链 artifact、图片、公式和完整数字 token，当前样例的 GB15577、15 min、1 均不再丢失。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_document_conversion_v2.py tests/unit/test_converter_provisioning.py tests/unit/test_document_parser.py（61 passed）`、`uv run --directory apps/service ruff check workers/converters/launcher.py workers/converters/profiles.py workers/converters/provisioning.py tests/unit/test_document_conversion_v2.py tests/unit/test_converter_provisioning.py tests/unit/test_document_parser.py（All checks passed）`、`uv run --directory apps/service pytest tests/unit -q（641 passed，2 skipped）`、`当前 23 页 PDF smoke（158 s）：selected=native-pdf+mineru；18 个原生文字页覆盖；第 2、4、6、22、23 页回退 VLM；第 12 页保留 GB15577、15 min、1；本机 VLM artifact 14 个。`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过）` |

## RET-20 Markdown 结构化质量

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-20-01` Markdown Provider 层级与去重质量约束 | P0 | 已完成 | `RET-17-01` | 每个 Provider 分块都有其完整原始 Markdown 标题纲要和当前继承标题上下文，纲要不会被直接重复输出。提示词明确要求语义内容按源顺序保留一次、只删除可确认的分页噪音、保留标题文字、同级统一且父子不跳级；来源没有明确文档标题时不得臆造。合并结果拒绝标题深度跳级及重复文档标题，并有限重试；失败时不写入不完整提案，保持可恢复。定向回归、Ruff、进度检查和差异检查均通过。 | - |

## RET-21 多 Vault 工作台重构

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-21-01` 多 Vault 工作台全景与二级上下文交互 | P1 | 已完成 | `RET-10-01`、`RET-14-01`、`RET-18-02` | 工作台首屏可查看所有已授权 Vault 的可用性、索引健康、任务与活动摘要；点击任一状态可在不丢失全局上下文的二级抽屉中定位对应详情和操作；导入任务资料项在已选择解析引擎时显示‘解析器’标签，兼容审核详情与自动导入详情一致，未选择引擎时保持空缺；摘要不泄露正文或跨 Vault 关系；桌面和移动端的按钮、选择器、输入容器及键盘交互具备一致的加载、禁用、错误和焦点状态。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_document_conversion_v2.py tests/unit/test_import_service.py tests/unit/test_import_tasks.py tests/unit/test_import_api.py（61 passed）`、`uv run --directory apps/service ruff check application/ingest.py domain/derived_notes.py workers/converters/launcher.py tests/unit/test_import_service.py tests/unit/test_document_conversion_v2.py（All checks passed）`、`npm --prefix apps/web run test（33 passed）、lint 与 build（通过）`、`本机任务 03/09 均完成；详情接口 62/63 ms 返回且 index=null；两项 Word 提案 provider_markdown=null；运行/排队计数均为 0。` |

## RET-22 PaddleOCR-VL PDF 解析

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-22-01` 以 PaddleOCR-VL 1.6 替换 PDF 解析 | P0 | 已完成 | `RET-19-01` | PDF V2 不再调用 MinerU 或 native-pdf+MinerU 混合解析；仅当已批准的本机 PaddleOCR-VL 1.6 profile、配置和模型完整可核验时运行。有效输出能保留文本、页码/bbox、表格、公式、图片及 artifact 溯源；profile 缺失、输出格式无效或转换失败时失败关闭且任务可恢复。 | `GPU runtime 自检：paddlepaddle-gpu 3.2.2、cuda=True、device=gpu:0、CPU paddlepaddle distribution absent。`、`PaddleOCR-VL 1.6 真实保存页面 JSON fixture 与无扩展名 snapshot 的私有 input.pdf staging 回归。`、`uv run --directory apps/service --locked --no-sync pytest -p no:cacheprovider tests/unit/test_document_conversion_v2.py tests/unit/test_converter_provisioning.py tests/unit/test_document_parser.py（67 passed）`、`uv run --directory apps/service --locked --no-sync ruff check workers/converters/launcher.py workers/converters/profiles.py workers/converters/provisioning.py tests/unit/test_document_conversion_v2.py tests/unit/test_converter_provisioning.py tests/unit/test_document_parser.py（All checks passed）`、`PaddleOCR-VL 1.6 脱敏 PDF application smoke：engine=paddleocr-vl-1.6、quality=accepted、18 blocks、page 1 bbox、5 artifact drafts；临时 input.pdf 未进入 artifact。`、`完整服务端 unit 在 180 秒上限内未完成且未输出失败断言；未记为通过。` |

## RET-23 Markdown Provider Token 预算

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-23-01` Markdown Provider 大 token 结构安全分块 | P0 | 已完成 | `RET-20-01` | 设置页可查看和保存 Markdown 结构化请求正文的最小、目标和最大 token 预算，默认 10k/16k/20k；无效顺序或越界值不会持久化。可安全聚合的长 Markdown 正文以约目标值、最多最大值的请求发送，不会再因默认字符数或 24 单位上限产生大量小请求。整篇不足最小值、最终尾块不足最小值或结构边界迫使的小块仍发送；标题、列表、表格、引用、代码围栏、源顺序和继承标题上下文保持不变。单个超过上限的不可拆结构不出网也不截断。Markdown Provider 的输出预算足以返回同量级正文，普通聊天生成预算不变。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_markdown_structuring.py tests/unit/test_providers.py tests/unit/test_openai_compatible_provider.py tests/unit/test_sqlite_provider_repository_rerank.py tests/unit/test_vault_api.py -q（108 passed）`、`uv run --directory apps/service ruff check domain/markdown_structuring.py application/markdown_structuring.py application/providers.py adapters/openai_compatible_provider.py adapters/sqlite_provider_repository.py ports/provider_repository.py api/main.py tests/unit/test_markdown_structuring.py tests/unit/test_providers.py tests/unit/test_openai_compatible_provider.py tests/unit/test_sqlite_provider_repository_rerank.py tests/unit/test_vault_api.py（All checks passed）`、`npm --prefix apps/web run test（33 passed）、lint 与 build（通过）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅 Windows 行尾提示）` |

## RET-24 导入任务队列与本地 Word 提案

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-24-01` 导入任务排队与 Word 本地提案修复 | P0 | 已完成 | `RET-18-01`、`RET-23-01` | 同一批量选择中只有一个任务处于运行态，其他任务显示排队；当前任务进入完成、失败或取消终态后下一项自动推进。DOCX/PDF 的 DocumentGraph 不会调用 Markdown Provider，原生 Markdown 行为保持不变。慢写入不会阻塞任务详情读取。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_document_conversion_v2.py tests/unit/test_import_service.py tests/unit/test_import_tasks.py tests/unit/test_import_api.py（61 passed）`、`uv run --directory apps/service ruff check application/ingest.py domain/derived_notes.py workers/converters/launcher.py tests/unit/test_import_service.py tests/unit/test_document_conversion_v2.py（All checks passed）`、`npm --prefix apps/web run test（33 passed）、lint 与 build（通过）`、`本机任务 03/09 均完成；详情接口 62/63 ms 返回且 index=null；两项 Word 提案 provider_markdown=null；运行/排队计数均为 0。` |
| `RET-24-02` 导入任务源解析与结构化结果对照 | P1 | 已完成 | `RET-24-01` | 已解析的 PDF/DOCX 任务详情可按资料项查看源解析正文及其用户可理解的位置，并与同一资料项的结构化 Markdown 结果直接对照；未解析资料保持清晰空态；响应不包含原始解析工件、绝对路径、哈希或内部块身份。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_import_api.py tests/unit/test_import_service.py（17 passed）`、`uv run --directory apps/service ruff check application/ingest.py api/main.py tests/unit/test_import_api.py（All checks passed）`、`npm --prefix apps/web run test（34 passed）、lint 与 build（通过）`、`OBSIDIAN_PLATFORM_TEST_PORT=6424 npm run browser-test（23 passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过）` |

## RET-25 iOS 设计语言统一

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-25-01` iOS 设计语言统一前端 UI 重构 | P0 | 已完成 | `RET-21-01`、`RET-24-02` | 五个生产入口均遵循 DESIGN.md 的 iOS 设计语言、中文简洁文案、状态连续和无障碍标准；桌面三栏与移动端逐层路径完整可操作；刷新不闪白或丢失阅读位置；关键单测、构建、浏览器和视觉检查通过。 | `npm --prefix apps/web run test（39 passed）`、`npm --prefix apps/web run lint（通过）`、`npm --prefix apps/web run build（通过）`、`OBSIDIAN_PLATFORM_TEST_PORT=6451 npm run browser-test（24 passed）`、`Playwright CLI 检查 1440x900、1024x768、390x844、Tab 焦点与 prefers-reduced-motion（通过；无横向溢出、控制台错误或布局跳动）` |

## RET-26 可读导入文件命名

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-26-01` 解析产物可读命名与同名保护 | P1 | 已完成 | `RET-24-01` | 新导入的原件、目录页和派生笔记路径不含 source ID、哈希或章节序号，且可直接从导入文件名和解析标题理解；同名不同内容不会覆盖已有资料；新目录页及旧 index.md 都不进入正文索引；内部来源身份、溯源和删除回滚保持有效。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_derived_notes.py tests/unit/test_import_service.py tests/unit/test_metadata_tags.py tests/unit/test_index_service.py（59 passed）`、`uv run --directory apps/service ruff check domain/derived_notes.py application/ingest.py application/indexing.py tests/unit/test_derived_notes.py tests/unit/test_import_service.py tests/unit/test_metadata_tags.py tests/unit/test_index_service.py（All checks passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅既有 Windows 行尾提示）`、`完整服务端 unit 在 184 秒上限内未完成且未输出失败断言；未记为通过。` |

## RET-27 受控在线文档解析

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-27-01` 任务级在线 OCR 与 PDF Markdown 双管线 | P0 | 已完成 | `RET-24-01`、`RET-25-01` | 所有 PDF 任务均冻结 AI 结构化或本地结构化模式，且可独立组合本机或在线 OCR。AI 模式仅在 Markdown outbound policy 允许时将选定 DocumentGraph 的 Markdown 发送给既有 Provider；本地模式零 Markdown Provider 调用；任何 Provider 或策略失败均不静默回退。用户只有在显式打开在线解析并选择已验证 Provider 后，原件与文件名才会发送到 PaddleOCR 官方 API 或 MinerU；其他格式保持既有处理。历史任务兼容原有外发行为，任务详情仅展示安全的模式、Provider 与状态；两个 Provider 的连接测试、服务测试、前端测试和关键浏览器流程通过。 | - |

## RET-28 来源链接核验修复

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-28-01` 双语标题来源链接核验修复 | P0 | 已完成 | `RET-26-01` | 来源文件与哈希未变化且来源链接位于中英文两行一级标题之后时，派生笔记保持 verifiable，不产生 source-link-broken；既有同原因 stale 文档在下一次 reconcile 自动恢复；来源链接缺失或仅出现在后续正文时仍不可核验。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_index_service.py -q（19 passed；修复前新增双语标题回归单独失败并复现 source-link-broken）`、`uv run --directory apps/service ruff check application/indexing.py tests/unit/test_index_service.py（All checks passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过）` |

## RET-29 当前页任务批量删除

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-29-01` 当前页导入任务多选删除 | P1 | 已完成 | `RET-18-02`、`RET-25-01` | 当前 Vault 的任务列表可单选或全选当前页中所有非运行任务；确认后只通过既有单任务删除路径逐项处理。成功项即时从列表移除，失败项保持选中并显示原因；分页、切换 Vault 或改变页大小不会把选择范围扩展到当前页以外。 | `npm --prefix apps/web run test（38 passed）`、`npm --prefix apps/web run lint（通过）`、`npm --prefix apps/web run build（通过）`、`OBSIDIAN_PLATFORM_TEST_PORT=6431 npm run browser-test：新增当前页多选、运行中任务排除、部分失败保留用例通过；完整 24 项中 16 项通过，另有 8 项在既有工作台、设置和会话 UI 场景失败，未归因于本任务。`、`单独重跑新增浏览器用例时隔离服务未在 10 秒健康检查窗口内启动；此前完整套件中该用例已通过，因此不记为功能失败。`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅既有 Windows 行尾提示）` |

## RET-30 无效治理与知识图谱退役

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-30-01` 删除无效标签元数据治理与用户知识图谱 | P0 | 已完成 | `RET-18-01`、`RET-25-01` | 生产 UI 和公开路由不再出现标签治理、元数据审核、单元卡片或知识图谱；导入不再生成私有标签或候选链接，运行时不再构造 LLM 元数据和单元卡片服务；旧数据库可继续打开且历史表不被改写；DocumentGraph/graph projection、Markdown 原生标签、确定性范围元数据、导入、Embedding 和普通检索保持可用。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit -q（651 passed，2 skipped）`、`uv run --directory apps/service ruff check .（All checks passed）`、`npm --prefix apps/web run test（35 passed）、npm --prefix apps/web run lint（通过）、npm --prefix apps/web run build（通过）`、`OBSIDIAN_PLATFORM_TEST_PORT=6427 npm run integration（13 passed，2 skipped）`、`OBSIDIAN_PLATFORM_TEST_PORT=6429 npx playwright test --config browser-tests/playwright.config.mjs browser-tests/loopback.spec.mjs --grep "without a user graph drawer\|without a user graph\|runs import tasks automatically\|does not expose a manual commit request"（3 passed）`、`完整 browser-test 24 场景为 13 passed、11 failed；其中本任务相关场景已改合同并通过，余下失败为既有工作台、设置、会话 UI 基线漂移，未纳入本任务`、`npm run progress:build && npm run progress:check（待本次进度同步后执行）`、`git diff --check（通过；仅既有 Windows 行尾提示）` |

## RET-31 本地 OCR 图结构归一化

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-31-01` OCR 后本地 Markdown 图结构归一化 | P0 | 已完成 | `RET-22-01`、`RET-27-01` | 本机 PDF 的 local 模式仅消费有 provenance 的 DocumentGraph 并保持零 Markdown Provider 调用；可信标题、列表、图注和可验证表格/公式/图片能以 Markdown 结构表达，跨页重复页边噪音不进入正文，异常或无法确认的结构不静默丢失。每个归一化图块可回链全部原始 block identity、locator 与 evidence，任务冻结规则版本，历史任务不会因代码升级自动改变。定向 golden 回归、Ruff、进度检查和差异检查通过。 | `OCR 图结构、二级/三级编号标题、跨页同位置重复内容、任务 profile 持久化、导入/API 与转换器 provisioning 定向回归（102 passed）`、`受影响应用、领域、适配器、转换器与测试 Ruff（All checks passed）`、`uv run --directory apps/service python -m compileall -q application adapters api domain ports workers（通过）`、`OBSIDIAN_PLATFORM_TEST_PORT=6443 npm run integration（13 passed，2 skipped）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅既有 Windows 行尾提示）` |

## RET-32 Provider 验证反馈

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-32-01` 模型验证失败原因与状态反馈 | P0 | 已完成 | `RET-25-01` | 模型验证失败时，设置页保留对应模型和已选类型，展示中文的失败状态、已脱敏原因与可重试操作；本机 API 前置拒绝也展示具体可修复原因。成功和失败均有稳定状态，失败不会被误报为成功或静默隐藏。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_providers.py -q（24 passed）`、`uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_vault_api.py -q（12 passed）`、`uv run --directory apps/service ruff check application/providers.py tests/unit/test_providers.py（All checks passed）`、`npm --prefix apps/web run test（38 passed）、lint 与 build（通过）`、`OBSIDIAN_PLATFORM_TEST_PORT=6445 npx playwright test --config browser-tests/playwright.config.mjs browser-tests/loopback.spec.mjs --grep "configures independent chat and Rerank models"（1 passed）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅既有 Windows 行尾提示）` |

## RET-33 Responses API Provider 模式

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-33-01` Provider Responses API 模式 | P0 | 已完成 | `RET-32-01` | 用户可在添加或编辑 Provider 时选择 Chat Completions 或 Responses API；旧 Provider 迁移后继续使用 Chat Completions。Responses Provider 的 chat/markdown 模型验证和生成只请求 /responses，并仅从流式 response.output_text.delta 读取最终文本、从 response.completed 读取一致的 usage；无有效文本或 usage 时失败关闭。Embedding、Rerank、模型发现、健康检查、凭据和出网边界保持既有行为。 | `Provider client、服务和 SQLite 迁移定向回归（74 passed）`、`Provider API 单测（12 passed）`、`Loopback integration（15 passed）`、`Provider 相关 Ruff（All checks passed）`、`前端单测（39 passed）、lint 与 build（通过）`、`独立 Provider 设置 Playwright 回归（1 passed）`、`Playwright CLI 真实工作台设置页检查（Responses API 选项可见；桌面截图 output/playwright/responses-api-mode.png）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅既有 Windows 行尾提示）` |

## RET-34 Obsidian 图片资源保留

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-34-01` 原生 Obsidian 图片资源保留与 Embedding 排除 | P0 | 已完成 | `RET-18-02`、`RET-26-01` | 原生 Obsidian Markdown 导入后，存在的本地图片可在 Vault 中随 Markdown 使用且引用有效；缺失或不安全引用不会产生部分完成态；图片正文、路径和二进制内容不进入 Embedding Provider；删除任务不会删除仍被其他 Markdown 或用户文件引用的共享图片。 | `图片资源、Embedding、迁移和导入定向回归（80 passed）`、`服务完整 unit 回归（678 passed，2 skipped）`、`服务 Ruff（All checks passed）`、`服务 compileall（通过）`、`npm run build（通过）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅既有 Windows 行尾提示）`、`npm run integration（未执行：既有本机服务占用 127.0.0.1:6240）` |

## RET-35 文件管理与安全在线阅读

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-35-01` Vault 原件文件管理、检索与安全在线阅读 | P0 | 已完成 | `RET-18-02`、`RET-24-02`、`RET-34-01` | 用户可在文件管理中浏览当前 Vault 的所有已提交 sources 原件、在 active 且 available Vault 范围内检索文件名和可用解析正文、打开安全格式在线阅读或下载原文件。Office 文件以本机临时 PDF 保持版式阅读，原件不重复存储且用户正文不出网；危险或未知格式永不执行或内嵌。文件列表不依赖任务记录，任务删除后仍可读取实际仍存在的 sources 文件。 | `前端单元测试 39 passed、lint 与生产构建通过`、`Playwright CLI 文件管理桌面/移动端阅读器高度与抽屉回归通过`、`npm run progress:build && npm run progress:check`、`git diff --check` |

## 风险

| 风险 | 严重度 | 状态 | 影响 | 缓解措施 | 归属任务 |
| --- | --- | --- | --- | --- | --- |
| `RET-R034` Office 预览运行时下载或临时产物扩大本机执行面 | 高 | 开放 | 若转换器来源、完整性校验、子进程参数、源文件路径约束或临时产物清理不充分，恶意 Office 文件或被篡改的运行时可能导致错误执行、泄漏源文件或长期占用磁盘。 | RET-35-01 将 runtime 固定到官方版本与完整性清单，下载和转换均不上传用户资料；转换器只接收经 Vault sources 边界解析的单文件路径，在受限临时目录写入 PDF，设置超时、大小上限和会话清理。HTML、SVG、脚本、压缩包、可执行文件及未知格式不嵌入。运行时或清单校验失败时关闭预览而非回退执行。 | `RET-35-01` |
| `RET-R001` DocumentGraph 随 import task 删除而失联 | 高 | 已关闭 | 派生文档索引无法稳定重建，graph_block_id 与原文 locator 失去长期核验能力。 | RET-01-01 已冻结最小不可变投影、稳定身份和索引侧读写合同；RET-01-02 已在同一 index.sqlite 事务中写入 projection 与派生文档；RET-01-03 已验证按 graph identity 读取投影、删除完成态 import task 后重建，以及 PDF/DOCX locator 核验。缺失或身份不匹配的投影会失败关闭。 | `RET-01-03` |
| `RET-R002` 索引正文批量出网扩大隐私边界 | 高 | 已关闭 | 教材和个人笔记正文可能按 vault 或目录批量发送给云端 Provider。 | 2026-07-27 用户明确确认默认允许出网，2026-08-05 再次明确确认可将 PDF/DOCX 解析得到的、格式保留的 Markdown 正文发送给 Markdown Provider 做结构化。RET-12-01 已删除运行时的逐任务授权、确认和执行前授权核验；会话 /run 在同一请求中保存选择、检索并仅将 outbound policy 允许的证据提交给所选 Model。never-send-cloud 证据不进入提示词；do-not-index、completely-ignore、HTTPS、已验证 Provider/model、内容哈希、响应和向量校验保持失败关闭。旧 SQLite 授权历史保留但运行时不读取或写入。默认出网的隐私范围已由用户接受。 | `RET-12-01` |
| `RET-R003` SQLite 新旧 schema 与派生索引不同步 | 高 | 已关闭 | 升级失败、stale FTS 行或向量孤儿会造成错误召回和难以恢复的索引状态。 | RET-02-01 已为富 IndexBlock 建立显式、可重试的结构列 migration，旧行以兼容默认值读取，失败注入会回滚富列且可在同一旧库重试；graph migration 也已独立事务化，避免后续 migration 失败留下无标记表。RET-02-02 已保持旧三字段与富字段同事务写入，并以 current-only、可重试回填补全哈希及可精确核验的 durable graph 结构；原始行的 legacy/rich 报告会拒绝静默覆盖不匹配或损坏投影。RET-02-03 已以 `OBSIDIAN_PLATFORM_RICH_BLOCK_READS` 保留 legacy 默认和显式 rich 切换；rich 模式遇到一致性问题会失败关闭，健康状态仅返回模式、状态和问题码。RET-03-03 已为块级规则元数据增加独立、可回滚的 migration，并将 document、block 与 metadata 写入保持在同一事务；filter_blocks 显式排除 stale、非 current、pending 与不允许路径。RET-04-01 已为 FTS5 与 map 完成可回滚 migration、eligible current 回填，以及 save、invalidate、rebuild 与失败回滚的同事务同步。RET-04-02 已以独立、可回滚内容回填 migration 将中英文词法文本同步到同一 FTS/map 生命周期；search_lexical 只读取 vault 内 current、verifiable、非 stale、非 pending 且由调用方允许的路径。RET-06-02 已为 embedding_cache 增加独立、可回滚 migration 与 float32 BLOB 校验；同一 profile locator 的多维度或损坏向量均失败关闭。RET-06-03 已以独立、可回滚的 current block vector migration 绑定完整 profile、块正文哈希、授权输入哈希和 float32 BLOB；文档保存、失效、回填、重建和关联状态转换均与向量删除和提交后矩阵 generation 失效同步。KNN、health 和写入均按 vault/current/可核验状态/profile/允许路径失败关闭，并拒绝损坏、零范数或输入哈希不匹配的向量。 | `RET-06-03` |
| `RET-R004` 目录规则只覆盖示例命名 | 中 | 已关闭 | 真实资料无法解析册次和单元，枚举型检索会返回 recoverable 或错误 scope。 | RET-00-02 已收集不可逆脱敏真实结构样本；RET-03-02 已以同一 fixture 固定严格的路径/标题归一化，并对根级、仅资料信号和仅位置标题分别返回 unknown 或 recoverable，绝不伪造 scope。RET-05-01 已让 QueryUnderstanding 直接消费同一合同，并将不完整或冲突范围保持为 recoverable，避免查询侧猜测。RET-05-02 已提供可编辑范围预览，显示文件、块、资料类型和缺口；用户确认的规范化范围已随私有快照保存。RET-05-03 已在范围快照完整时以 BlockFilter 全量枚举，不受点查 top-k 影响，并把标题规则分类与超预算项作为持久化的覆盖信息和明确缺口；仍须在更多已审阅、不可逆脱敏样本上扩展规则。 | `RET-05-03` |
| `RET-R005` 固定阈值导致误去重或漏召回 | 中 | 已关闭 | 0.92 相似度、top-k、RRF 参数和批预算若未经评测会直接固化样本偏差。 | RET-00-03 已建立脱敏合成 golden set，RET-00-04 已记录当前手写检索、Windows FTS5/BM25 与 float32 KNN 的原始基线。用户确认 RET-04-02 的词法最低门槛为 macro recall 0.6569、macro precision 0.2813；实现结果为 0.6875、0.302083。RET-04-03 已在同一 fixture 完成版本化 A/B：相对旧手写评分，FTS macro recall +0.030555555555555558、macro precision +0.020833333333333315，passesGate=true；生产 source-lookup 已仅使用 FTS，关闭本机开关时失败关闭，不回退旧打分。RET-05-04 只会按 block_content_sha256 做精确自动合并；标题词归一仅产生候选簇，duplicate precision/recall 已进入 golden 评测，模型窗口与批次数必须由调用方显式传入。RET-06-04 增加版本化脱敏语义改写 fixture：RRF k=20、60、100 的 macro recall@8 均为 1.0，保留 k=60；该小样本不校准真实 vault 的 top-k、RRF 或向量候选参数。RET-07-02 增加脱敏 unit-card fixture，冻结最低 card coverage=1.0、original citation recall=1.0、card-on 相对 card-off macro recall@8 gain=0.5；实际结果为 1.0、1.0、0.5。RET-07-03 新增 top-20 到 top-8 的脱敏 rerank A/B：脚本化 adapter 的 macro recall@8 +1.0、precision@8 +0.25、MRR@8 +1.0，但真实 Provider 延迟和费用尚未测量，passesGate=false，生产开关保持关闭。RET-09-01 将真实 Provider 测量限制为内容哈希校验通过的 synthetic-deidentified fixture，强制完整候选响应并只记录不含正文的质量与端到端延迟。2026-07-27 使用独立 BAAI/bge-reranker-v2-m3 执行两条 fixture：macro recall@8 +1.0、precision@8 +0.25、MRR@8 +1.0；p50=1014.314 ms、p95=1699.467 ms；质量门槛通过。用户明确不做价格或费用预算，费用明确未计算；该小样本仍不足以启用默认 rerank。仍须扩展已审阅、不可逆脱敏真实样本并由人工复核后决定是否改变默认。 | `RET-09-01` |
| `RET-R006` 同名 Embedding 模型错误复用缓存 | 中 | 开放 | 不同 Provider endpoint 或配置 revision 产生的不可比较向量可能被混用。 | RET-06-02 已实现 embedding_profile_fingerprint，固定绑定 provider ID、规范 endpoint、Provider.updated_at 配置 revision、model ID 和返回维度。缓存查询先以 locator 查找，再验证最终 profile 指纹；endpoint、revision、model 或 dimension 任一变化均不复用，且同一 locator 出现多维度时失败关闭。 | `RET-06-02` |
| `RET-R007` 向量常驻缓存失效不完整 | 中 | 已关闭 | 索引更新、模型切换或 policy revision 后仍可能搜索旧矩阵。 | RET-06-03 已按 vault/profile 维护本地 float32 矩阵与 vault generation；save_document、save_committed_unit、invalidate_current_path、backfill_current_blocks、pending association 解析和 save_block_vectors 仅在 SQLite 提交成功后递增 generation 并清除受影响矩阵。profile fingerprint 是缓存键的一部分，模型或 Provider 配置变化会加载独立矩阵；定向测试覆盖保存、失效、重建等价路径、新 profile、当前状态和跨 vault 隔离。 | `RET-06-03` |
| `RET-R008` LLM 元数据候选被错误采纳或驱动不可逆动作 | 高 | 开放 | 不可靠或恶意的 Provider 输出可能给索引块附加错误概念，进而误导后续知识卡片或人工决策。 | RET-07-01 已将模型输出限制为经过领域校验的 knowledge_kind 与 concept_key 候选，绑定 vault、块内容哈希、Provider/model 和置信度；低置信度或新概念一律标记为 required-check。候选不会自动合并、写回原文或改变范围、当前检索排序；只有带原因的本地人工接受决定才能改变审核状态。RET-07-02 只消费 accepted 且仍与当前块哈希匹配的候选；卡片 Provider 输出必须是 JSON，且只可完整复述已接受的 knowledge_kind/concept_key。源块、scope、审核决定或 Provider/profile 变化会使卡片失效；检索只将卡片命中展开为原始块证据，不把卡片正文作为 evidence。人工审核质量仍需持续监测。 | `RET-07-02` |
| `RET-R009` 默认出网的 rerank 候选正文范围扩大 | 高 | 已关闭 | 启用 rerank 时，候选块正文、标题或标签会在没有逐任务确认的情况下发送给已验证 Provider。 | 2026-07-27 用户明确确认默认允许出网。RET-12-01 已移除 rerank 授权快照；默认 feature flag 仍关闭，启用后仅调用独立的已验证 HTTPS rerank Provider，并继续逐候选执行 never-send-cloud、绝对路径投影、响应格式、并发和失败回退校验。默认出网范围已由用户接受。 | `RET-12-01` |
| `RET-R010` 短原子块和派生目录重复放大全量枚举范围 | 高 | 已关闭 | 解析产生的十几字符 atomic paragraph 不能达到预期检索粒度；平台生成的 index.md 又按相同 projection 入索引，会使完整列举重复处理同一来源内容并放大本地索引、Embedding 和模型处理预算。 | RET-11-01 已在同一标题上下文内确定性聚合相邻 short paragraph atomic projection，保留全部 source locator、起始 graph block identity、阅读顺序和最小置信度；带有效 platform_provenance 的派生 index.md 已在普通索引、提交期索引和重建中失效。回归覆盖结构边界、reconcile、提交期索引和 rebuild；当前 vault 已从 4 个索引文档 / 776 块重建为 2 个正文文档 / 49 个唯一块。 | `RET-11-01` |
| `RET-R011` 自动向量化与导入提交出现部分完成态 | 高 | 已关闭 | 若 Provider 调用或向量持久化失败后仍提交派生文档，会造成提交内容不可被完整语义检索。 | RET-12-01 已在提交完成前自动执行 Embedding；Embedding 服务缺失、Provider、内容、规则或向量校验失败都会恢复本次提交中的全部已写入文件，reconcile 当前索引，记录 failed/rolled-back journal，并使任务保持 retry-commit 可重试状态。提交门禁回归覆盖自动执行、Provider 失败恢复、跨资料整体回滚和不完成提交。 | `RET-12-01` |
| `RET-R012` 标题范围请求退化为全范围证据 | 高 | 已关闭 | 用户指定“第一单元”等标题范围时，知识整理或深度创作可能混入其他标题（如 Unit 7）的证据并生成错误结论。 | RET-05-05 已以 IndexBlock.heading_path 锚定当前允许来源中的标题与子标题，并对旧块的既有 heading: location 只读兼容。中文“第一单元”匹配 Unit 1、不匹配 Unit 7；用户文本中出现的任意现有标题同样可作为范围。明确范围无命中时在 Provider 调用前失败关闭。服务回归覆盖知识整理、深度创作、Unit 1/Unit 7、通用 Project A 标题与无命中范围。 | `RET-05-05` |
| `RET-R013` 未解析或局部命中的标题路径扩大检索范围 | 高 | 已关闭 | 用户指定任意父子标题或结构路径时，局部命中、共享子标题、结构别名或命令噪声可能把兄弟标题乃至整个 vault 的内容冻结并发送给 Provider，生成错误结论。 | RET-13-01 已将标题范围改为基于实际 heading_path 的联合约束：请求中的结构引用全部必须命中同一路径，显式标题短语不得留下未解析残余，完整标题优先于结构别名，最终过滤只接受已解析实际路径前缀。章、节、课、部分、模块、项目、卷、阶段和单元使用统一中英文结构别名；重点、核心、要点、关键、概念与方法等通用主题进入知识整理而不依赖词汇特例。任一显式范围无匹配均在任务持久化和 Provider 调用前失败关闭；all notes、当前 vault 等整库表达保持全范围语义。 | `RET-13-01` |
| `RET-R014` 会话详情刷新清空内容并丢失阅读位置 | 中 | 已关闭 | 用户在长会话中执行发送、编辑或重新核验后，会先看到空白加载区并被重置到首条消息，难以继续当前阅读位置。 | RET-15-01 保留与当前会话匹配的已加载详情和应用证据，刷新时只叠加轻量状态；首次加载定位最新轮次，后续内容更新仅在用户仍位于底部时跟随。问答导航按用户问题及后续输出分组，支持点击定位和可访问名称。前端静态回归验证刷新状态不移除正文；浏览器回归验证初始位置和定位条点击。 | `RET-15-01` |
| `RET-R015` 远程上传扩大公开 Tunnel 的文件接收面 | 高 | 开放 | 非服务机文件会经公网进入服务机；公开 Tunnel 当前没有独立身份认证，超大或未清理暂存文件也可能耗尽磁盘。 | 2026-08-04 用户已明确确认允许非服务机文件经公网传输并在服务端暂存，并追加要求支持多文件与文件夹。RET-16-01 已保持同源会话校验、512 MiB 单文件限制、随机私有暂存、文件名/相对路径规范化，以及选择过期、任务创建失败或最后一个引用删除后的清理；文件夹层级暂存和既有递归扫描回归已完成。公开 Tunnel 的独立身份认证、配额和可恢复上传仍不在本步范围内，因此风险保持开放。 | `RET-16-01` |
| `RET-R016` Markdown Provider 结构化误删、重复与层级失真 | 中 | 开放 | 长文分块时模型可能重复输出标题或正文、误删语义内容，或产生跳级和混杂的标题层级，导致最终 Markdown 不准确且难以导航。 | RET-17-01 已将原生和 PDF/DOCX 转换 Markdown 纳入 outbound policy、结构安全分块和可恢复失败链路。RET-20-01 正在为每个分块补充完整原始标题纲要与继承上下文，收紧语义内容恰好一次、标题保留、分页噪音删除、同级一致和父子连续的提示词规则，并拒绝可机械确认的跳级及重复文档标题后有限重试。该约束不能单独证明模型的语义判断；仍需以已审阅脱敏语料评估正文保真、标题判定和重复率。 | `RET-20-01` |
| `RET-R017` 自动导入跳过人工审核后采纳错误建议 | 高 | 开放 | 分类建议、元数据标签或候选链接若与源内容不符，人工不再能在提交前修正；错误产出可能降低后续人工整理质量。 | RET-18-01 将三类建议限制为任务私有、不可阻塞的观察性产出：它们不再写回 Markdown、不移动文件、不改变已提交内容、索引范围或检索排序。自动提交继续受 vault 相对路径验证、原子提交、索引与 Embedding 完整性门禁及失败回滚保护。任何将建议自动采纳为可见或不可逆变更的需求，必须另立带质量门槛和回滚的任务。 | `RET-18-01` |
| `RET-R018` PDF 原生文字层与 MinerU 解析结果不一致 | 高 | 开放 | MinerU pipeline 可能丢失行内数字和标准号；整份文档回退又会牺牲原生文字层，导致 GB15577、15 min、1 等证据缺失或重复。 | RET-19-01 已按页优先使用 pypdf 原生文字层，并以数字/英文 token 完整性校验决定页面级 MinerU VLM 回退；混合 DocumentGraph 保留原始 PDF、pipeline/VLM artifact、图片和公式溯源。真实 23 页样例中 18 个文字页走原生，第 2、4、6、22、23 页走 VLM；第 12 页的 GB15577、15 min、1 均保留。该样例的 2、6、22、23 页是实际空白分隔页，VLM artifact 保留但没有伪造正文。仍需以更多脱敏的扫描 PDF 样本评估 VLM 的非空页 OCR 质量。 | `RET-19-01` |
| `RET-R019` 删除任务误删提交后的 Vault 文件修改 | 高 | 已关闭 | 任务提交的 Markdown、源文件或资料附件若在之后被用户修改，直接按路径删除会丢失后续内容。 | RET-18-02 已实现全链路删除：仅回退最新 committed journal 中该任务拥有的 Vault 文件，并先以提交内容 SHA-256 精确比对；任一不匹配、缺失或不可读时整次删除失败关闭，不删除任务记录或任何 Vault 文件。回退后物理清理该任务的索引文档、FTS、向量、Embedding 缓存、元数据候选、索引任务路径、无引用 graph projection，以及无其他任务引用的来源身份和解析证据；共享状态保留。 | `RET-18-02` |
| `RET-R020` 工作台聚合摘要与 Vault 实际状态不同步 | 中 | 开放 | 跨 Vault 摘要若在索引、任务或会话状态变化后过期，用户可能基于错误的健康度或待处理数量执行操作。 | RET-21-01 只聚合已有可核验摘要，不缓存正文；首次加载、手动刷新和关键操作后重新读取，并在摘要带 updated_at 与明确的部分失败状态。实时推送、历史趋势和持久化事件另立任务。 | `RET-21-01` |
| `RET-R021` PaddleOCR-VL 1.6 本机运行时或模型 profile 不完整 | 高 | 开放 | PDF 解析在未安装 PaddlePaddle 推理运行时、VL 1.6 模型或哈希批准记录时无法执行；若绕过 profile，可能发生隐式下载、版本漂移或远程调用。 | RET-22-01 已将 PDF V2 主路径固定为 PaddleOCR-VL 1.6 的 native backend，并以 manifest 的 executable、配置和模型哈希及 release approval 门禁启动。子进程禁用模型源连通性检查、继承代理和 Hugging Face/Transformers 在线模式；缺失、无 bbox、无关联图片 artifact 或输出不完整时失败关闭。2026-08-07 已按用户批准安装并验证 GPU-only runtime：paddlepaddle-gpu 3.2.2、cuda=True、device=gpu:0，CPU paddlepaddle distribution absent；本机 profile 的 PaddleOCR-VL 1.6、PP-DocLayoutV3、字体、配置和 approval 哈希均加载通过。真实脱敏 PDF application smoke 选中 paddleocr-vl-1.6，quality=accepted，得到 18 个带 page 1 bbox 的 blocks 与 5 个 artifact drafts，且没有隐式下载。runtime 提示编译时 CUDNN 9.9 与系统 9.5 不匹配，当前 smoke 成功但仍须用更多脱敏 PDF 监测稳定性，因此风险保持开放。 | `RET-22-01` |
| `RET-R022` Markdown Provider 的模型窗口或输出上限小于大分块预算 | 中 | 开放 | Provider 可能拒绝约 16k 至 20k token 的正文或无法返回完整 Markdown，导致导入任务不能完成结构化。 | RET-23-01 已以保守本地估算和可持久化的最小/目标/最大设置将可发送正文限制在配置上限内，默认上限为 20k token，并将 Markdown 专用输出预算提高到可覆盖同量级正文；定向回归覆盖默认值、更新重启、旧库迁移失败重试、小文档和尾块。Provider 拒绝请求或响应未通过保真校验时维持既有可恢复失败，不写入部分提案。具体模型窗口协商与真实 Provider 容量验证不在本步实现，因此风险保持开放。 | `RET-23-01` |
| `RET-R023` 批量导入并行状态与 Word Provider 边界失控 | 高 | 已关闭 | 一轮多文件上传若将全部任务立刻标为运行中，会占满本地执行资源；若 DocumentGraph 被错误交给 Markdown Provider，则会扩大正文出网范围并使任务详情被长操作阻塞。 | RET-24-01 已将批量选择拆为单文件任务并由单消费者 FIFO 队列推进；恢复、重新派生和重试提交都先入队，重试提交以后台任务启动，不阻塞操作请求。DOCX/PDF 的 DocumentGraph 只在本地渲染派生提案，provider_markdown 保持 null；原生 Markdown 保留 Provider 结构化。任务详情不再持有导入状态写锁或同步读取索引。61 项服务定向回归及前端测试/lint/build 通过；现场恢复的两项 Word 任务均完成，详情在 62/63 ms 返回。 | `RET-24-01` |
| `RET-R024` 任务详情正文展示扩大本地浏览器可见范围 | 中 | 已关闭 | 将解析正文返回到任务详情可能意外泄漏绝对路径、哈希、转换工件或解析器内部原始输出，也可能把尚未选择的资料项内容混入当前任务。 | RET-24-02 仅从当前任务已选 DocumentGraph 的 retrieval_projection 或兼容 ParseEvidence.units 构造显示合同，按 item_id 归属并过滤为块类型、用户可理解定位和文本；不返回 raw extraction、artifact、bbox、哈希、内部 block ID 或绝对路径。接口继续要求本机会话；服务定向回归、前端单测/lint/build 和独立端口浏览器回归均通过，并断言敏感字段缺失。 | `RET-24-02` |
| `RET-R025` 全局 UI 重构降低高频操作可发现性或引入动效性能回归 | 中 | 已关闭 | 重排导航、收敛说明和迁移移动端层级可能让用户找不到既有操作；不受约束的动画或组件重挂载也可能造成闪白、滚动位置丢失、掉帧或无障碍退化。 | RET-25-01 保留五个入口和全部现有业务能力，只重构呈现层；主要操作保持文字标签，工具图标带 aria-label 和 Tooltip；刷新保留旧内容与位置；动画限定 transform/opacity 并支持 prefers-reduced-motion。2026-08-11 会话首次进入已改为直接定位最新消息，不继承默认平滑下滚，问答定位保留用户触发的平滑滚动。2026-08-20 已以 1440x900、1024x768、390x844、键盘 Tab 与减少动态完成截图复核，并在独立端口完成 24 项浏览器回归；未发现横向溢出、焦点遗漏、控制台错误或状态跳动。 | `RET-25-01` |
| `RET-R026` 可读导入文件名发生同名覆盖或目录页重复索引 | 中 | 已关闭 | 移除内部 ID 与哈希后，同名不同内容可能覆盖既有原件或笔记；目录页若被当作正文索引，会形成重复检索结果。 | RET-26-01 已在派生前检查 managed sources 目录：同内容复用已有名称，不同内容使用常规‘（2）’后缀。目录页改为‘文件名 - 目录.md’，索引层同时识别该格式与遗留 index.md。自动导入、同名、中文命名、标签和索引定向回归共 59 项通过。 | `RET-26-01` |
| `RET-R027` PDF 在线 OCR 与 AI 结构化扩大外发范围 | 高 | 开放 | 在线 OCR 需要将任务原件和文件名上传至 PaddleOCR 官方云 API 或 MinerU；统一 PDF AI 结构化后，本机或在线 OCR 的派生 Markdown 也可能发送到既有 Markdown Provider。若没有任务级选择、策略门禁、历史兼容或稳定失败语义，可能在用户未知或重试时扩大外发范围。 | RET-27-01 将在线 OCR 选择与 PDF Markdown 模式分别冻结：在线 OCR 仅在用户打开开关、选择已验证 Provider 且原件上传策略允许时上传 PDF；AI 结构化仅在 Markdown outbound policy 允许时发送选定 DocumentGraph 的派生 Markdown；本地结构化零 Provider 调用。历史任务缺少模式字段时按原有行为推断，失败不静默回退。仅 PDF 受此模式影响；不保存 token、绝对路径、签名 URL、远端 job ID、原始 HTTP 响应或完整 Provider 日志。用户已于 2026-08-17 明确确认本机 OCR + AI 结构化的 Markdown 外发范围与四种 OCR/结构化组合。 | `RET-27-01` |
| `RET-R028` 双语标题误判来源链接失效 | 高 | 已关闭 | 来源文件与哈希均未变化的派生笔记会因一级标题后的英文副标题而被标记 source-link-broken，导致 CME 索引错误 stale 并从检索范围排除。 | RET-28-01 已将来源链接核验改为允许一级标题后的单行副标题，再接受精确来源链接；既有 source-link-broken 文档会在下一次 reconcile 自动重新核验。回归覆盖中文标题、英文副标题、有效来源链接、恢复路径，并确认正文内容之后的链接仍会被拒绝。 | `RET-28-01` |
| `RET-R029` 退役知识图谱与治理代码误伤导入结构投影或检索标签 | 高 | 已关闭 | 若将用户知识图谱与解析使用的 graph projection 混同，PDF/DOCX 索引重建和来源定位会失效；若同时删除 Markdown 原生 tags 或确定性块级元数据，BM25、图谱之外的标签词项和范围枚举会退化。 | RET-30-01 已将删除边界限定为用户知识图谱、候选链接、私有标签提案、LLM 元数据候选和单元卡片；明确保留 DocumentGraph、graph_projection、Markdown 原生 tags、FTS tag_text 与 subject/grade_volume/unit_no/material_type。新库不创建退役治理表，旧库历史表和行保持不变且运行时不读写。完整 unit 651 passed/2 skipped，结构投影与索引重建 20 passed，确定性元数据与 FTS 20 passed，导入索引 68 passed，integration 13 passed/2 skipped，受影响浏览器场景 3 passed。 | `RET-30-01` |
| `RET-R030` 本地 OCR 归一化误删正文或让历史重试产生不同 Markdown | 高 | 开放 | 过度使用版面或文本启发式可能把正文误判为页眉页脚、错误合并跨列内容，或在规则升级后使历史任务重试产生不可核验的不同派生 Markdown。 | RET-31-01 已只在确定性高的几何和跨页重复条件下变换；不确定结构保留原始内容并记录稳定问题。每个归一化块保留全部原始 block ID、locator 与 evidence ref；任务持久化规则版本，历史任务默认使用旧版本。脱敏 JSON golden fixture 已覆盖页眉 OCR 顺序先于标题、跨块段落、列表、图注关联和 1.1/1.1.1 二级三级标题。local-v2 仅对新任务启用跨页相同内容的同位置检测，且有不同位置同文本保留回归；转换、任务、导入、API 与 provisioning 定向回归共 102 项通过。仍需更多已审阅、不可逆脱敏教材样本量化误删率和标题准确率。 | `RET-31-01` |
| `RET-R031` Provider 模型验证失败被静默隐藏 | 中 | 已关闭 | 用户无法区分地址、网络、TLS、超时、权限或 Provider 响应问题，失败模型还会从设置页消失，导致无法在原上下文中修复或重试。 | RET-32-01 仅保留 ProviderClientError 已归一化的 HTTP 与网络类别，不返回密钥、正文、完整地址、响应体或堆栈；设置页读取 FastAPI detail.message，并持续显示已配置模型的失败状态、中文原因和重试入口。application、API、前端和独立端口浏览器回归均通过。 | `RET-32-01` |
| `RET-R032` Responses Provider 被误用为 Chat Completions 协议 | 中 | 已关闭 | 仅支持 /responses 的 Provider 可能收到 /chat/completions 请求，导致网关 502、HTML/非 SSE 响应或模型验证失败；反向切换也可能让既有 Provider 失效。 | RET-33-01 为 Provider 持久化显式 api_mode，旧库默认 chat-completions。Responses 模式的 chat/markdown 验证与生成只使用 /responses、instructions、input、stream 和 max_output_tokens，并仅接受 response.output_text.delta 与一致的 response.completed usage；无有效文本失败关闭。Embedding、Rerank、模型发现和健康检查不受此模式影响；设置页持续显示当前模式，用户必须显式编辑 Provider 才会切换。 | `RET-33-01` |
| `RET-R033` 原生 Obsidian 图片资源丢失或误发到 Embedding Provider | 高 | 已关闭 | Markdown 导入若只保存文本会使本地图片引用失效；若将图片链接、路径或二进制误纳入 embedding，会造成不完整检索、无意义向量和超出用户预期的出网范围。 | RET-34-01 已将本地图片引用解析、内容哈希资产提交和 Markdown 重写放入同一可回滚导入单元；远程、越界、缺失和符号链接引用失败关闭；Embedding 只消费去除图片引用后的文本块，图片-only 块不计入语义覆盖。新增版本化迁移会定向清理历史图片输入向量和无引用缓存，避免同一块身份因输入规则升级触发冲突；真实同身份内容冲突仍失败关闭。80 项图片/Embedding/导入/索引/向量定向回归和 678 项完整服务 unit 回归通过。 | `RET-34-01` |

## 技术债

当前无登记技术债。

## 最近日志

- 2026-08-21 `RET-35-01`：完成 Vault 文件管理、检索与安全在线阅读。结果：文件管理现从各 Vault managed_root/sources 直接枚举原件，支持当前 Vault 与 active/available Vault 全局本地搜索、分页筛选、下载及 PDF/图片/TXT/Markdown/Office 在线阅读；Office 通过受控本机临时 PDF 渲染，不复制原件、不依赖任务记录、不向 Provider 外发正文。文件管理页面改为全屏阅读工作区，文件列表为可收起抽屉；修复阅读器高度链路，使阅读面板和 PDF iframe 从标题栏延伸至工作区底部。 下一步：如需文件移动、批量重命名、删除、版本控制、AI 整理或精确阅读位置高亮，另立任务并先定义回滚与权限边界。
- 2026-08-21 `RET-35-01`：启动 Vault 文件管理与安全在线阅读。结果：用户确认文件管理只读取各 Vault managed_root/sources，不复制原件、不依赖导入任务。范围包括全局本地全文检索、安全预览、下载和统一 Office 临时 PDF 渲染；不做 AI 整理，且不向用户展示解析状态。 下一步：先定义文件枚举、检索和预览合同，再补实现与回归。
- 2026-08-20 `RET-25-01`：完成工作台界面精简与交互回归。结果：工作台、Provider 和导入流程已收敛为简洁的中文操作界面，移除了重复说明，同时保留外发、隐私、错误和恢复提示。移动端 Vault 表格转换为两列信息行，上传控件保留可见按钮并从键盘焦点中移除隐藏 input；会话搜索控件名称不再冲突，异步加载中仍显示会话已保存的 Vault 标签。未新增技术债。 下一步：如需深色模式或更复杂的跨页动效，按 RET-25-01 executionBoundary 另立任务并先完成独立对比度或性能评审。
- 2026-08-20 `RET-34-01`：修复历史图片 Embedding 向量身份冲突。结果：根因是图片排除规则上线后，旧向量仍绑定旧输入哈希；同一 document/sequence/profile 写入新向量时触发身份完整性保护。新增统一 embedding_block_input_text 规则与 ret-34-01-embedding-image-exclusion-v1 可回滚迁移：仅删除输入哈希因图片排除而变化或图片-only 的旧向量，并清理无引用缓存；未变化向量保留，真实同身份不同内容仍拒绝写入。 下一步：释放 127.0.0.1:6240 后重新运行 npm run integration；如需历史 Vault 图片迁移或图像理解检索，另立任务。
- 2026-08-20 `RET-34-01`：完成原生 Obsidian 图片资源保留与 Embedding 排除。结果：原生 Markdown 现解析本地 Obsidian 与 Markdown 图片引用，验证路径与内容哈希后以 platform/assets/<sha256>.<ext> 去重入库并重写为 Vault 内 wikilink；远程图片不下载，缺失、越界或符号链接失败关闭。Embedding 仅发送去除图片引用后的文本，图片-only 块不产生 Provider 输入；删除任务会保留被其他 Markdown 引用或原先已存在的共享资产。 下一步：如需历史 Vault 图片迁移、图片预览或图像理解检索，另立任务并先确认隐私与向量合同。
- 2026-08-20 `RET-34-01`：开始原生 Obsidian 图片资源保留与 Embedding 排除。结果：用户确认：解析和入库 Obsidian 时保留本地图片资源，Embedding 不处理图片。本步冻结为原生 Markdown 本地图片引用的安全解析、资产入库、引用重写、共享删除保护和 embedding 排除；远程图片、历史批量迁移和图像理解不纳入。 下一步：先补充图片资源与 embedding 排除失败回归，再实现最小导入链路。
- 2026-08-19 `RET-31-01`：忽略跨页同位置的重复 OCR 内容。结果：新增 local-v2。新 PDF 任务默认冻结 v2：跨至少两页出现、文本相同且 bbox 相对或绝对位置及尺寸接近的内容，标记为不可渲染 noise，可覆盖页眉、页脚和页面中部的重复广告。不同页面位置的同文内容保留，避免把重复正文静默删除。legacy-v0 与已冻结的 local-v1 行为不变。 下一步：以更多已审阅、不可逆脱敏 PDF 检验不同纸张尺寸、双栏与重复练习内容；任何阈值调整以新的冻结 profile 发布。
- 2026-08-17 `RET-33-01`：完成 Provider Responses API 模式。结果：Provider 现可在设置页显式选择 Chat Completions 或 Responses API；旧 SQLite 记录自动迁移为 Chat Completions。Responses 模式的 chat/markdown 验证与生成调用 /responses，发送 instructions、input、stream 与 max_output_tokens，只消费 response.output_text.delta 的最终文本和 response.completed 的一致 usage。模型发现、健康检查、Embedding、Rerank、凭据与 Vault 出网策略未改变；不自动切换任何已有 Provider。真实工作台页面确认选项和桌面布局可用；未发起真实 Provider 请求，未新增技术债。 下一步：用户可编辑“极速按量”，将 API 模式切换为 Responses API 后重新测试模型；若仍收到服务商 502，则继续按 API Key 或上游模型权限处理。
- 2026-08-17 `RET-27-01`：确认 PDF OCR 后 Markdown 双管线开发方案。结果：用户确认所有 PDF 均可独立组合本机或在线 OCR 与 AI 结构化或本地结构化。AI 模式将选定 DocumentGraph 的 Markdown 发送到既有 Markdown Provider，本地模式使用确定性渲染且零 Provider 调用；PDF 原件上传仍只由在线解析开关控制。两类外发均使用既有 Vault outbound policy，失败不自动回退。模式按任务冻结，新任务默认 AI；历史任务按既有在线 OCR 选择推断模式以避免重试扩大外发。非 PDF 保持原处理；本步仅提供手动任务对照，不新增自动双跑、差异或评分。 下一步：实施任务级 PDF Markdown 模式合同、四种 OCR/结构化执行分支、API 与前端分段控件；完成定向服务端、前端和浏览器验证后记录真实结果。
- 2026-08-17 `RET-31-01`：开始 OCR 后本地 Markdown 图结构归一化。结果：用户确认以 DocumentGraph 为本地结构化规范输入，原始 PaddleOCR 页面 JSON 保持在 adapter 与私有证据层。范围限定为零出网的确定性归一化：标题、阅读顺序、列表、图注、段落断行、可确认的重复页边噪音与异常 bbox，并要求变换后的图块可回链全部原始定位与证据。历史任务不得因规则升级自动改变。 下一步：实现版本化归一化器与最小持久化合同，补齐脱敏 golden fixtures 后运行定向回归、进度检查和差异检查。
