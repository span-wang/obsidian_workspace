# 检索改造开发进度

> 此文件由 `progress/` 下的结构化数据生成，请勿手工修改。

- 数据日期：2026-08-05
- 方案文档：`docs/retrieval-redesign.md`
- 任务总数：44
- 已完成：44
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
| `RET-18` 自动化导入提交 | 将导入解析、结构化、提交、索引和向量化串成无需人工审核的可恢复流水线，并隔离任务私有建议。 | 已完成 | 1/1 |
| `RET-19` PDF 原生优先解析 | 按页优先使用原生 PDF 文本，原生文本为空或质量不通过时回退 MinerU，同时保留数字、图片和公式。 | 已完成 | 1/1 |

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
| `RET-10-01` 用户界面来源信息收敛 | P1 | 已完成 | `RET-09-02` | 生产工作台不再向用户展示 Source ID、内容哈希、来源摘要哈希或 graph/chunk 内部定位；用户仍能通过文件、标题、页码、摘录和状态理解并打开证据，内部核验合同保持不变。 | `npm --prefix apps/web run test（26 passed）`、`npm --prefix apps/web run lint（通过）`、`npm --prefix apps/web run build（通过）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6257'; npm run browser-test（29 passed）`、`桌面 1280px 与移动 390px 视觉检查（通过；证据层级显示文件、标题、页码、原始资料、摘录和匹配方式，未显示 Source ID、哈希或 graph/chunk 定位）`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过；仅 Windows 行尾提示）` |

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

## RET-19 PDF 原生优先解析

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-19-01` PDF 原生文本优先与按页 MinerU 回退 | P0 | 已完成 | `RET-17-01`、`RET-18-01` | 有文字且质量通过的 PDF 页面使用原生解析；空文字、结构不可靠或 token 校验失败的页面使用 MinerU；混合结果保留可回链 artifact、图片、公式和完整数字 token，当前样例的 GB15577、15 min、1 均不再丢失。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_document_conversion_v2.py tests/unit/test_converter_provisioning.py tests/unit/test_document_parser.py（61 passed）`、`uv run --directory apps/service ruff check workers/converters/launcher.py workers/converters/profiles.py workers/converters/provisioning.py tests/unit/test_document_conversion_v2.py tests/unit/test_converter_provisioning.py tests/unit/test_document_parser.py（All checks passed）`、`uv run --directory apps/service pytest tests/unit -q（641 passed，2 skipped）`、`当前 23 页 PDF smoke（158 s）：selected=native-pdf+mineru；18 个原生文字页覆盖；第 2、4、6、22、23 页回退 VLM；第 12 页保留 GB15577、15 min、1；本机 VLM artifact 14 个。`、`npm run progress:build && npm run progress:check（通过）`、`git diff --check（通过）` |

## 风险

| 风险 | 严重度 | 状态 | 影响 | 缓解措施 | 归属任务 |
| --- | --- | --- | --- | --- | --- |
| `RET-R001` DocumentGraph 随 import task 删除而失联 | 高 | 已关闭 | 派生文档索引无法稳定重建，graph_block_id 与原文 locator 失去长期核验能力。 | RET-01-01 已冻结最小不可变投影、稳定身份和索引侧读写合同；RET-01-02 已在同一 index.sqlite 事务中写入 projection 与派生文档；RET-01-03 已验证按 graph identity 读取投影、删除完成态 import task 后重建，以及 PDF/DOCX locator 核验。缺失或身份不匹配的投影会失败关闭。 | `RET-01-03` |
| `RET-R002` 索引正文批量出网扩大隐私边界 | 高 | 已关闭 | 教材和个人笔记正文可能按 vault 或目录批量发送给云端 Provider。 | 2026-07-27 用户明确确认默认允许出网，2026-08-05 再次明确确认可将 PDF/DOCX 解析得到的、格式保留的 Markdown 正文发送给 Markdown Provider 做结构化。RET-12-01 已删除运行时的逐任务授权、确认和执行前授权核验；会话 /run 在同一请求中保存选择、检索并仅将 outbound policy 允许的证据提交给所选 Model。never-send-cloud 证据不进入提示词；do-not-index、completely-ignore、HTTPS、已验证 Provider/model、内容哈希、响应和向量校验保持失败关闭。旧 SQLite 授权历史保留但运行时不读取或写入。默认出网的隐私范围已由用户接受。 | `RET-12-01` |
| `RET-R003` SQLite 新旧 schema 与派生索引不同步 | 高 | 已关闭 | 升级失败、stale FTS 行或向量孤儿会造成错误召回和难以恢复的索引状态。 | RET-02-01 已为富 IndexBlock 建立显式、可重试的结构列 migration，旧行以兼容默认值读取，失败注入会回滚富列且可在同一旧库重试；graph migration 也已独立事务化，避免后续 migration 失败留下无标记表。RET-02-02 已保持旧三字段与富字段同事务写入，并以 current-only、可重试回填补全哈希及可精确核验的 durable graph 结构；原始行的 legacy/rich 报告会拒绝静默覆盖不匹配或损坏投影。RET-02-03 已以 `OBSIDIAN_PLATFORM_RICH_BLOCK_READS` 保留 legacy 默认和显式 rich 切换；rich 模式遇到一致性问题会失败关闭，健康状态仅返回模式、状态和问题码。RET-03-03 已为块级规则元数据增加独立、可回滚的 migration，并将 document、block 与 metadata 写入保持在同一事务；filter_blocks 显式排除 stale、非 current、pending 与不允许路径。RET-04-01 已为 FTS5 与 map 完成可回滚 migration、eligible current 回填，以及 save、invalidate、rebuild 与失败回滚的同事务同步。RET-04-02 已以独立、可回滚内容回填 migration 将中英文词法文本同步到同一 FTS/map 生命周期；search_lexical 只读取 vault 内 current、verifiable、非 stale、非 pending 且由调用方允许的路径。RET-06-02 已为 embedding_cache 增加独立、可回滚 migration 与 float32 BLOB 校验；同一 profile locator 的多维度或损坏向量均失败关闭。RET-06-03 已以独立、可回滚的 current block vector migration 绑定完整 profile、块正文哈希、授权输入哈希和 float32 BLOB；文档保存、失效、回填、重建和关联状态转换均与向量删除和提交后矩阵 generation 失效同步。KNN、health 和写入均按 vault/current/可核验状态/profile/允许路径失败关闭，并拒绝损坏、零范数或输入哈希不匹配的向量。 | `RET-06-03` |
| `RET-R004` 目录规则只覆盖示例命名 | 中 | 已关闭 | 真实资料无法解析册次和单元，枚举型检索会返回 recoverable 或错误 scope。 | RET-00-02 已收集不可逆脱敏真实结构样本；RET-03-02 已以同一 fixture 固定严格的路径/标题归一化，并对根级、仅资料信号和仅位置标题分别返回 unknown 或 recoverable，绝不伪造 scope。RET-05-01 已让 QueryUnderstanding 直接消费同一合同，并将不完整或冲突范围保持为 recoverable，避免查询侧猜测。RET-05-02 已提供可编辑范围预览，显示文件、块、资料类型和缺口；用户确认的规范化范围已随私有快照保存。RET-05-03 已在范围快照完整时以 BlockFilter 全量枚举，不受点查 top-k 影响，并把标题规则分类与超预算项作为持久化的覆盖信息和明确缺口；仍须在更多已审阅、不可逆脱敏样本上扩展规则。 | `RET-05-03` |
| `RET-R005` 固定阈值导致误去重或漏召回 | 中 | 开放 | 0.92 相似度、top-k、RRF 参数和批预算若未经评测会直接固化样本偏差。 | RET-00-03 已建立脱敏合成 golden set，RET-00-04 已记录当前手写检索、Windows FTS5/BM25 与 float32 KNN 的原始基线。用户确认 RET-04-02 的词法最低门槛为 macro recall 0.6569、macro precision 0.2813；实现结果为 0.6875、0.302083。RET-04-03 已在同一 fixture 完成版本化 A/B：相对旧手写评分，FTS macro recall +0.030555555555555558、macro precision +0.020833333333333315，passesGate=true；生产 source-lookup 已仅使用 FTS，关闭本机开关时失败关闭，不回退旧打分。RET-05-04 只会按 block_content_sha256 做精确自动合并；标题词归一仅产生候选簇，duplicate precision/recall 已进入 golden 评测，模型窗口与批次数必须由调用方显式传入。RET-06-04 增加版本化脱敏语义改写 fixture：RRF k=20、60、100 的 macro recall@8 均为 1.0，保留 k=60；该小样本不校准真实 vault 的 top-k、RRF 或向量候选参数。RET-07-02 增加脱敏 unit-card fixture，冻结最低 card coverage=1.0、original citation recall=1.0、card-on 相对 card-off macro recall@8 gain=0.5；实际结果为 1.0、1.0、0.5。RET-07-03 新增 top-20 到 top-8 的脱敏 rerank A/B：脚本化 adapter 的 macro recall@8 +1.0、precision@8 +0.25、MRR@8 +1.0，但真实 Provider 延迟和费用尚未测量，passesGate=false，生产开关保持关闭。RET-09-01 将真实 Provider 测量限制为内容哈希校验通过的 synthetic-deidentified fixture，强制完整候选响应并只记录不含正文的质量与端到端延迟。2026-07-27 使用独立 BAAI/bge-reranker-v2-m3 执行两条 fixture：macro recall@8 +1.0、precision@8 +0.25、MRR@8 +1.0；p50=1014.314 ms、p95=1699.467 ms；质量门槛通过。用户明确不做价格或费用预算，费用明确未计算；该小样本仍不足以启用默认 rerank。仍须扩展已审阅、不可逆脱敏真实样本并由人工复核后决定是否改变默认。 | `RET-09-01` |
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
| `RET-R016` - | 中 | 开放 | - | RET-17-01 在发送原生和 PDF/DOCX 转换 Markdown 前复用 vault outbound policy，命中 never-send-cloud 或 completely-ignore 时不调用 Provider；Provider 直接返回最终 Markdown，服务拒绝 JSON、空响应和不保留任何源内容的响应，并按结构安全分块拼接。标题、列表、表格、引用和代码围栏不跨 Provider 分块，后续块携带继承标题上下文。仅重复出现且不承载正文的页眉、页脚、页码、广告等分页伪影可由 Provider 移除；Provider 依据语义和上下文保留标题文字并优化标题层级，不凭空创建标题。PDF/DOCX 只有能回链的重复图块才从私有候选排除，原始文件、图块 ID、定位和审核语义仍保留。模型发生正常 Markdown 重排时保留原图块用于审核与候选溯源，最终 Provider Markdown 用于生成提案；Provider 或响应核验失败会保持任务可恢复，不写入不完整提案。真实 PDF 任务已验证 182 个图块、10 个 noise 图块和 0 个 noise 候选行；语义质量仍需后续脱敏样本评估，因此风险保持开放。 | `RET-17-01` |
| `RET-R017` 自动导入跳过人工审核后采纳错误建议 | 高 | 开放 | 分类建议、元数据标签或候选链接若与源内容不符，人工不再能在提交前修正；错误产出可能降低后续人工整理质量。 | RET-18-01 将三类建议限制为任务私有、不可阻塞的观察性产出：它们不再写回 Markdown、不移动文件、不改变已提交内容、索引范围或检索排序。自动提交继续受 vault 相对路径验证、原子提交、索引与 Embedding 完整性门禁及失败回滚保护。任何将建议自动采纳为可见或不可逆变更的需求，必须另立带质量门槛和回滚的任务。 | `RET-18-01` |
| `RET-R018` PDF 原生文字层与 MinerU 解析结果不一致 | 高 | 开放 | MinerU pipeline 可能丢失行内数字和标准号；整份文档回退又会牺牲原生文字层，导致 GB15577、15 min、1 等证据缺失或重复。 | RET-19-01 已按页优先使用 pypdf 原生文字层，并以数字/英文 token 完整性校验决定页面级 MinerU VLM 回退；混合 DocumentGraph 保留原始 PDF、pipeline/VLM artifact、图片和公式溯源。真实 23 页样例中 18 个文字页走原生，第 2、4、6、22、23 页走 VLM；第 12 页的 GB15577、15 min、1 均保留。该样例的 2、6、22、23 页是实际空白分隔页，VLM artifact 保留但没有伪造正文。仍需以更多脱敏的扫描 PDF 样本评估 VLM 的非空页 OCR 质量。 | `RET-19-01` |

## 技术债

当前无登记技术债。

## 最近日志

- 2026-08-05 `RET-18-01`：完成自动化导入提交与私有建议隔离。结果：导入任务现从扫描自动串行执行解析、OCR/转换、Markdown 结构化、Vault 提交、索引和 Embedding；提交前不再创建或等待审核快照，失败保留 restart-* 或 retry-commit 可恢复状态并复用原子写入、索引和向量回滚。分类建议、元数据与标签、候选链接分别作为任务私有只读观察产出，不写回 Markdown、不移动文件、不改变索引或检索排序。服务端移除审核/提交/解析/转换手工路由，前端任务详情只保留状态、建议、提交记录、取消和自动重试。 下一步：如需将三类建议自动采纳为可见知识图谱、文件移动或 Markdown 修改，另立带质量门槛、回滚和用户可见变更的任务。
- 2026-08-05 `RET-18-01`：开始自动化导入提交与私有建议隔离。结果：用户明确确认移除导入审核步骤和审核 API/UI。任务将自动完成解析、Markdown 结构化、Vault 提交、索引与 Embedding；元数据与标签、候选链接和分类建议仅保留为互相隔离的任务私有产出，不参与提交。保留既有路径验证、事务回滚、向量化门禁和可恢复失败语义。 下一步：改造导入状态机、移除审核 API/UI，并以自动提交与失败回滚回归验证。
- 2026-08-05 `RET-17-01`：开始 PDF/DOCX Markdown Provider 结构化扩展。结果：用户明确要求 PDF 与 DOCX 解析后的原格式 Markdown 也必须提交给 Markdown Provider 做结构化，并要求移除重复页眉、页脚等噪音。用户同时明确确认该解析正文可按现有 Provider 出网边界发送。实施会保留 DocumentGraph、原始文件、图块 ID、页码/OOXML locator 和人工审核语义；Provider 仅返回经严格核验的结构分类，整块可回链的 noise 才从私有候选排除。 下一步：先补 PDF/DOCX Provider 输入、长文上下文、页眉页脚 noise、图块溯源和失败恢复回归，再做最小应用层接线。
- 2026-08-05 `RET-17-01`：完成 Markdown Provider 结构化导入、PDF/DOCX 投影与上下文分块。结果：Provider 配置现有独立的 markdown 模型类型、验证、默认选择和可回滚 SQLite 升级。原生 .md 与已选 PDF/DOCX DocumentGraph 的格式保留 Markdown 投影都会先按 vault outbound policy 拦截 never-send-cloud 或 completely-ignore，再把结构安全的源单位发送给该模型，而不是把本地规则结果作为任务结构；后续长文分块携带当前有效的标题层级，不重复正文，并同时限制 24,000 字符与 64 个源单位以保证分类 JSON 可完整返回。提示词要求模型将每个重复且不承载正文的页眉、页脚、页码等分页伪影标为 noise；PDF/DOCX 仅在该 noise 可完整回链单个 DocumentGraph 块时从私有候选排除，原始文件、图块 ID、定位和审核语义不变。模型响应仍须逐单位、顺序一致地覆盖所有源单位；兼容但校验 chunk_id 回显，拒绝任何其他顶层字段。策略、Provider、配置 revision 或响应核验失败会使导入任务保持 restart-derivation 可恢复状态。默认出网边界不变；RET-R016 继续跟踪模型语义分类质量，未新增技术债。 下一步：如需让 vault 重建消费 Provider 结构块，先设计 durable projection 合同；同时以已审阅的脱敏长 Markdown 样本衡量 RET-R016 的模型分类质量。
- 2026-08-05 `RET-17-01`：修正为 Markdown Provider 直出最终 Markdown并完成真实任务验证。结果：按用户确认移除 JSON blocks 协议：Provider 现在直接返回最终 Markdown，服务按结构安全分块发送并拼接，不再根据 JSON 做本地结构化。真实任务 c6fb7415-f03c-43cc-8c0f-319fc023631b 重启后进入 waiting-for-review；解析图含 182 个图块，Provider Markdown 9978 字符，10 个重复分页噪音图块保留 graph ID，私有候选命中 noise 的行数为 0。Provider 的 Markdown 重排不再误判为协议失败，唯一图块仍保留原始图块溯源与审核语义。 下一步：继续以已审阅、不可逆脱敏 Markdown 样本评估 Provider 的正文保留和噪音识别质量；如需让 vault 重建消费 Provider Markdown，另立 durable projection 任务。
- 2026-08-05 `RET-17-01`：优化 Markdown Provider 清洗与标题层级提示词。结果：按用户确认将提示词扩展为删除重复页眉、页脚、运行标题、页码、广告和 OCR 噪音，并要求 Provider 根据标题语义、编号、相邻内容和继承标题上下文重建 Markdown 标题层级；正文措辞、顺序和结构保持不变，歧义内容保留。 下一步：继续以已审阅、不可逆脱敏 Markdown 样本评估噪音识别和标题层级质量；如需让 vault 重建消费 Provider Markdown，另立 durable projection 任务。
- 2026-08-05 `RET-19-01`：开始 PDF 原生优先与按页 MinerU 回退。结果：用户确认 PDF 按页优先使用原生解析，原生文字为空或质量不通过时回退 MinerU；本步将以数字/英文 token 完整性校验保护 GB15577、15 min、1，并保留图片、公式和两种解析 artifact。 下一步：先补失败测试，再实现页面级 native/MinerU 合并并用当前 PDF 样例验证。
- 2026-08-05 `RET-19-01`：完成 PDF 原生优先与按页 MinerU 回退。结果：PDF 现先按页读取原生文字层并比较数字/英文 token 计数；原生文字为空、读取失败或完整性失败时，保留默认 pipeline 产物并按相邻页范围调用已批准的本机 MinerU VLM。混合图保留原始 PDF、pipeline/VLM artifact、图片 asset、公式和页面级 fallback 标记；空白分隔页不会被伪造成正文。已将本机 profile 绑定 VLM 缓存目录和 hash，保持离线运行。 下一步：以更多已审阅、脱敏的扫描 PDF 衡量 VLM 非空页的文字、公式和表格质量，再决定是否调整页级质量阈值或默认 backend。
- 2026-08-05 `RET-06-05`：完成前端检索模式切换。结果：会话工作台新增仅关键词、仅语义和关键词与语义混合三种即时模式。模式通过本机会话保护 API 切换；仅关键词只调用词法通道，仅语义只调用向量通道并在语义索引或 Embedding Provider 不可用时失败关闭，混合模式保留词法、向量和标题 RRF 并在语义不可用时显示本地降级。 下一步：如需跨服务重启保存模式或缓存重复查询的 Embedding，另立运行时配置和查询向量缓存任务。
- 2026-08-04 `RET-15-01`：完成会话消息稳定渲染与问答定位导航。结果：会话详情刷新不再用空白加载态替换已显示消息或应用证据。首次打开有内容的会话会定位到最新轮次；用户向上阅读后，后续刷新仅保留当前位置，不会回到首条消息。对话区右侧新增按用户提问及其后续输出分组的紧凑定位条，悬停可查看问题摘要，点击可平滑定位并显示当前轮次。未修改会话 API、SQLite schema、Provider、检索、证据关联或任务执行语义；无新增技术债。 下一步：如会话规模需要窗口化加载或跨设备恢复阅读位置，另立性能与持久化任务。
