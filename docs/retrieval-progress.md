# 检索改造开发进度

> 此文件由 `progress/` 下的结构化数据生成，请勿手工修改。

- 数据日期：2026-07-27
- 方案文档：`docs/retrieval-redesign.md`
- 任务总数：31
- 已完成：31
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
| `RET-05` 查询范围与枚举型汇总 | 让范围决定覆盖，完成可预览、可核验的全量汇总。 | 已完成 | 4/4 |
| `RET-06` Embedding 与混合检索 | 在明确出网边界后接入精确向量检索与独立通道融合。 | 已完成 | 4/4 |
| `RET-07` 受控增强 | 以独立开关验证 LLM 元数据、单元卡片和 rerank 的实际增益。 | 已完成 | 3/3 |
| `RET-08` 受控 rerank 接入 | 以逐任务授权、执行前重验和默认关闭开关，将已评测的点查 rerank 受控接入生产链路。 | 已完成 | 1/1 |
| `RET-09` 真实 Provider 测量 | 只以脱敏 fixture 在明确隐私与请求上限内测量真实 rerank Provider；费用按用户决定明确未计算，默认开关保持关闭。 | 已完成 | 2/2 |

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

## RET-06 Embedding 与混合检索

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-06-01` Embedding 出网授权与范围预览 | P0 | 已完成 | `RET-00-02`、`RET-05-02` | 未授权正文不出网，授权范围变化后旧授权失效。 | `uv run --directory apps/service pytest -p no:cacheprovider tests/unit/test_embedding_authorizations.py tests/unit/test_policies.py tests/unit/test_vault_api.py（23 passed）`、`uv run --directory apps/service ruff check domain/embedding_authorization.py application/embedding_authorizations.py application/policies.py api/main.py tests/unit/test_embedding_authorizations.py tests/unit/test_policies.py tests/unit/test_vault_api.py（All checks passed）`、`npm run unit（前端 22 passed；服务端 442 passed，3 skipped）`、`npm run lint（All checks passed）`、`$env:OBSIDIAN_PLATFORM_TEST_PORT = '6244'; npm run integration（11 passed，2 skipped）`、`npm run progress:build && npm run progress:check`、`git diff --check` |
| `RET-06-02` Embedding 客户端、指纹与缓存 | P0 | 已完成 | `RET-06-01` | 不同 endpoint/config revision 的同名模型绝不复用向量。 | `Provider adapter contract 测试`、`缓存命中与指纹隔离测试` |
| `RET-06-03` float32 向量存储、内存矩阵与健康状态 | P0 | 已完成 | `RET-06-02` | 只搜索 current 且 profile 匹配的向量，重建和模型切换后缓存一致。 | `精确 KNN 排序测试`、`内存缓存失效与覆盖率测试` |
| `RET-06-04` 独立三路召回与 RRF | P0 | 已完成 | `RET-04-03`、`RET-06-03` | 词法漏召回时向量可独立救回，任何通道都不作为另一通道的硬过滤器。 | `语义改写 recall@8`、`通道隔离回归测试` |

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

## 风险

| 风险 | 严重度 | 状态 | 影响 | 缓解措施 | 归属任务 |
| --- | --- | --- | --- | --- | --- |
| `RET-R001` DocumentGraph 随 import task 删除而失联 | 高 | 已关闭 | 派生文档索引无法稳定重建，graph_block_id 与原文 locator 失去长期核验能力。 | RET-01-01 已冻结最小不可变投影、稳定身份和索引侧读写合同；RET-01-02 已在同一 index.sqlite 事务中写入 projection 与派生文档；RET-01-03 已验证按 graph identity 读取投影、删除完成态 import task 后重建，以及 PDF/DOCX locator 核验。缺失或身份不匹配的投影会失败关闭。 | `RET-01-03` |
| `RET-R002` 索引正文批量出网扩大隐私边界 | 高 | 开放 | 教材和个人笔记正文可能按 vault 或目录批量发送给云端 Provider。 | RET-00-02 已确认按选定目录或 vault 批次、显式确认的 D-002。RET-06-01 已实现仅本机可见的 Provider/文件/块数预览、逐文件 never-send-cloud 排除、即使 always-allow 也逐批次确认，以及覆盖选定范围、文件集合、块序号和内容哈希的执行前核验。RET-06-02 已在每个 create_embeddings 网络批次前重验该快照；policy revision、Provider/model、范围、文件、块哈希或待发送 retrieval/contextual text 变化都会停止后续请求。Provider 配置 revision 在持锁调用前再次核验，Embedding 只允许 HTTPS endpoint，缓存命中不出网。用户已确认 RET-06-04 的点查查询可默认、无感发送；混合开关关闭时不出网，开启后只向已验证的默认 HTTPS embedding Provider 发送当前用户问题，不发送 vault 正文、索引块或绝对路径。2026-07-27 用户确认 index-metadata 默认批准；RET-07-01 已为每次执行冻结范围、重验 never-send-cloud、policy、Provider/model 与内容哈希，并保持与 index-embedding 独立。2026-07-27 用户确认 index-unit-card：RET-07-02 为 chat 摘要和卡片 Embedding 分别创建 pending 授权，均需确认；仅发送 current、可核验、非 stale、非 pending 且人工 accepted 的源块。每次 Provider 调用前重验 never-send-cloud、范围、来源哈希、policy 和 Provider revision；仅 HTTPS Provider 可用，配置或默认变更会清除卡片投影。RET-09-01 仅允许明确确认的 synthetic-deidentified rerank fixture；命令在读取 Provider 凭据和建立网络连接前要求确认、HTTPS、显式独立 rerank Provider/model 与最多两条请求，且不读取 vault 或会话数据。2026-07-27 已以 BAAI/bge-reranker-v2-m3 完成两条固定 fixture 的原生 /rerank 测量；报告不含正文、路径、endpoint、secret 或 token，费用按用户决定明确未计算，默认开关仍关闭。 | `RET-07-02` |
| `RET-R003` SQLite 新旧 schema 与派生索引不同步 | 高 | 已关闭 | 升级失败、stale FTS 行或向量孤儿会造成错误召回和难以恢复的索引状态。 | RET-02-01 已为富 IndexBlock 建立显式、可重试的结构列 migration，旧行以兼容默认值读取，失败注入会回滚富列且可在同一旧库重试；graph migration 也已独立事务化，避免后续 migration 失败留下无标记表。RET-02-02 已保持旧三字段与富字段同事务写入，并以 current-only、可重试回填补全哈希及可精确核验的 durable graph 结构；原始行的 legacy/rich 报告会拒绝静默覆盖不匹配或损坏投影。RET-02-03 已以 `OBSIDIAN_PLATFORM_RICH_BLOCK_READS` 保留 legacy 默认和显式 rich 切换；rich 模式遇到一致性问题会失败关闭，健康状态仅返回模式、状态和问题码。RET-03-03 已为块级规则元数据增加独立、可回滚的 migration，并将 document、block 与 metadata 写入保持在同一事务；filter_blocks 显式排除 stale、非 current、pending 与不允许路径。RET-04-01 已为 FTS5 与 map 完成可回滚 migration、eligible current 回填，以及 save、invalidate、rebuild 与失败回滚的同事务同步。RET-04-02 已以独立、可回滚内容回填 migration 将中英文词法文本同步到同一 FTS/map 生命周期；search_lexical 只读取 vault 内 current、verifiable、非 stale、非 pending 且由调用方允许的路径。RET-06-02 已为 embedding_cache 增加独立、可回滚 migration 与 float32 BLOB 校验；同一 profile locator 的多维度或损坏向量均失败关闭。RET-06-03 已以独立、可回滚的 current block vector migration 绑定完整 profile、块正文哈希、授权输入哈希和 float32 BLOB；文档保存、失效、回填、重建和关联状态转换均与向量删除和提交后矩阵 generation 失效同步。KNN、health 和写入均按 vault/current/可核验状态/profile/允许路径失败关闭，并拒绝损坏、零范数或输入哈希不匹配的向量。 | `RET-06-03` |
| `RET-R004` 目录规则只覆盖示例命名 | 中 | 已关闭 | 真实资料无法解析册次和单元，枚举型检索会返回 recoverable 或错误 scope。 | RET-00-02 已收集不可逆脱敏真实结构样本；RET-03-02 已以同一 fixture 固定严格的路径/标题归一化，并对根级、仅资料信号和仅位置标题分别返回 unknown 或 recoverable，绝不伪造 scope。RET-05-01 已让 QueryUnderstanding 直接消费同一合同，并将不完整或冲突范围保持为 recoverable，避免查询侧猜测。RET-05-02 已提供可编辑范围预览，显示文件、块、资料类型和缺口；用户确认的规范化范围已随私有快照保存。RET-05-03 已在范围快照完整时以 BlockFilter 全量枚举，不受点查 top-k 影响，并把标题规则分类与超预算项作为持久化的覆盖信息和明确缺口；仍须在更多已审阅、不可逆脱敏样本上扩展规则。 | `RET-05-03` |
| `RET-R005` 固定阈值导致误去重或漏召回 | 中 | 开放 | 0.92 相似度、top-k、RRF 参数和批预算若未经评测会直接固化样本偏差。 | RET-00-03 已建立脱敏合成 golden set，RET-00-04 已记录当前手写检索、Windows FTS5/BM25 与 float32 KNN 的原始基线。用户确认 RET-04-02 的词法最低门槛为 macro recall 0.6569、macro precision 0.2813；实现结果为 0.6875、0.302083。RET-04-03 已在同一 fixture 完成版本化 A/B：相对旧手写评分，FTS macro recall +0.030555555555555558、macro precision +0.020833333333333315，passesGate=true；生产 source-lookup 已仅使用 FTS，关闭本机开关时失败关闭，不回退旧打分。RET-05-04 只会按 block_content_sha256 做精确自动合并；标题词归一仅产生候选簇，duplicate precision/recall 已进入 golden 评测，模型窗口与批次数必须由调用方显式传入。RET-06-04 增加版本化脱敏语义改写 fixture：RRF k=20、60、100 的 macro recall@8 均为 1.0，保留 k=60；该小样本不校准真实 vault 的 top-k、RRF 或向量候选参数。RET-07-02 增加脱敏 unit-card fixture，冻结最低 card coverage=1.0、original citation recall=1.0、card-on 相对 card-off macro recall@8 gain=0.5；实际结果为 1.0、1.0、0.5。RET-07-03 新增 top-20 到 top-8 的脱敏 rerank A/B：脚本化 adapter 的 macro recall@8 +1.0、precision@8 +0.25、MRR@8 +1.0，但真实 Provider 延迟和费用尚未测量，passesGate=false，生产开关保持关闭。RET-09-01 将真实 Provider 测量限制为内容哈希校验通过的 synthetic-deidentified fixture，强制完整候选响应并只记录不含正文的质量与端到端延迟。2026-07-27 使用独立 BAAI/bge-reranker-v2-m3 执行两条 fixture：macro recall@8 +1.0、precision@8 +0.25、MRR@8 +1.0；p50=1014.314 ms、p95=1699.467 ms；质量门槛通过。用户明确不做价格或费用预算，费用明确未计算；该小样本仍不足以启用默认 rerank。仍须扩展已审阅、不可逆脱敏真实样本并由人工复核后决定是否改变默认。 | `RET-09-01` |
| `RET-R006` 同名 Embedding 模型错误复用缓存 | 中 | 开放 | 不同 Provider endpoint 或配置 revision 产生的不可比较向量可能被混用。 | RET-06-02 已实现 embedding_profile_fingerprint，固定绑定 provider ID、规范 endpoint、Provider.updated_at 配置 revision、model ID 和返回维度。缓存查询先以 locator 查找，再验证最终 profile 指纹；endpoint、revision、model 或 dimension 任一变化均不复用，且同一 locator 出现多维度时失败关闭。 | `RET-06-02` |
| `RET-R007` 向量常驻缓存失效不完整 | 中 | 已关闭 | 索引更新、模型切换或 policy revision 后仍可能搜索旧矩阵。 | RET-06-03 已按 vault/profile 维护本地 float32 矩阵与 vault generation；save_document、save_committed_unit、invalidate_current_path、backfill_current_blocks、pending association 解析和 save_block_vectors 仅在 SQLite 提交成功后递增 generation 并清除受影响矩阵。profile fingerprint 是缓存键的一部分，模型或 Provider 配置变化会加载独立矩阵；定向测试覆盖保存、失效、重建等价路径、新 profile、当前状态和跨 vault 隔离。 | `RET-06-03` |
| `RET-R008` LLM 元数据候选被错误采纳或驱动不可逆动作 | 高 | 开放 | 不可靠或恶意的 Provider 输出可能给索引块附加错误概念，进而误导后续知识卡片或人工决策。 | RET-07-01 已将模型输出限制为经过领域校验的 knowledge_kind 与 concept_key 候选，绑定 vault、块内容哈希、Provider/model 和置信度；低置信度或新概念一律标记为 required-check。候选不会自动合并、写回原文或改变范围、当前检索排序；只有带原因的本地人工接受决定才能改变审核状态。RET-07-02 只消费 accepted 且仍与当前块哈希匹配的候选；卡片 Provider 输出必须是 JSON，且只可完整复述已接受的 knowledge_kind/concept_key。源块、scope、审核决定或 Provider/profile 变化会使卡片失效；检索只将卡片命中展开为原始块证据，不把卡片正文作为 evidence。人工审核质量仍需持续监测。 | `RET-07-02` |
| `RET-R009` 点查 rerank 候选正文绕过外发授权 | 高 | 已关闭 | 若直接在 source-lookup 中启用云端 rerank，候选块正文、标题或标签可能在没有逐任务范围、内容哈希和 policy 重验的情况下发送给 Provider。 | RET-08-01 已在 source-lookup 生产链路建立 rerank-source-lookup 的逐任务授权快照与本机会话确认；预览和通用确认响应均不返回候选正文、路径、内容哈希或任务摘要。授权不可逆绑定查询哈希、候选顺序、待发送投影哈希、源块哈希、范围、policy revision 与独立 rerank Provider/model/config revision；每次实际调用前均重验这些条件、HTTPS 与 never-send-cloud。RET-09-02 已将生产重排目标独立为 rerank 模型类型与原生 POST /rerank，不能复用 chat/DeepSeek 模型。仅在默认关闭的 feature flag 开启且授权有效时，rerank 才位于 RRF 与邻域扩展之间；任何拒绝、失效、预检、Provider 或并发失败均保留本地 RRF，并记录不含正文的请求数和延迟。单元、API、集成与浏览器回归已覆盖授权前不执行、确认后执行及敏感字段不出现在 UI/请求体。RET-09-01 已记录固定脱敏 fixture 的真实 Provider 延迟与质量，但更多样本与人工复核完成前不改变默认关闭结论。 | `RET-08-01` |

## 技术债

当前无登记技术债。

## 最近日志

- 2026-07-27 `RET-09-01`：完成独立原生 Rerank 模型的受限真实 Provider 测量。结果：用户在前端配置并验证独立 Rerank 默认模型后，RET-09-01 仅对固定、内容哈希校验通过的 synthetic-deidentified fixture 发出两条原生 POST /rerank 请求。首次使用错误的相对 fixture 路径只在本地批准 fixture 校验处失败，未出网且未写入报告；修正后完成测量。报告不含 prompt、候选正文、响应正文、绝对路径、endpoint、secret 或授权 token：macro recall@8 +1.0、macro precision@8 +0.25、macro MRR@8 +1.0，quality gate=true；端到端 p50=1014.314 ms、p95=1699.467 ms。用户明确不做价格或费用预算，费用标记为未计算。报告的 passesGate 保持 false、生产 rerank 默认开关保持关闭，等待更多已审阅样本和人工复核；未新增技术债。 下一步：在更多已审阅、不可逆脱敏样本完成并人工复核质量和延迟前，rerank 默认保持关闭。
- 2026-07-27 `RET-09-02`：完成独立原生 Rerank 模型配置与受控接入。结果：新增独立 rerank 模型类型、默认选择和兼容 SQLite migration，保留既有 chat 与 embedding 配置。前端 Provider 设置显示 Rerank（重排）模型选择、模型类型和手动测试入口；生产重排、source-lookup 授权快照和 live fixture 都只解析该独立默认模型。原生合同固定为 POST /rerank 的 model、query、documents，完整、唯一、有限且位于 [0,1] 的 index/relevance_score 是唯一可接受响应；任何失败都保留本地 RRF。DeepSeek chat 模型不再可作为 rerank 目标。实现及自动化回归未调用真实 Provider；默认开关保持关闭，未新增技术债。 下一步：RET-09-01 仅以固定不可逆脱敏 fixture、显式出网确认和最多两条请求完成真实测量；默认开关继续关闭。
- 2026-07-27 `RET-09-01`：执行无预算的受限真实 Provider rerank 并复核流式最终内容边界。结果：用户已确认仅对版本化、不可逆脱敏 rerank fixture 进行受控真实 Provider 测量，并确认无需费用预算。live-eval 继续要求显式出网确认、HTTPS Provider/model、请求与输出 token 上限；价格、报价来源和费用预算可选且必须成组提供。仅接受固定路径和内容哈希均校验通过的 fixture，HTTPS 校验先于凭据读取，报告只能写入被忽略的 output/live-rerank/。首次调用在第一条 fixture 请求后因没有可用最终内容停止；随后适配器增加对 final message.content 的兼容，同时明确拒绝把 reasoning_content 当作最终答案。第二次、单请求重试确认 deepseek-v4-pro 只返回推理片段而没有最终 JSON。两次真实请求都只使用 fixture，不读取 vault、索引或会话数据；失败报告不包含 prompt、响应正文、endpoint、secret 或 token。由于没有完整 rerank 响应，未得到可用的延迟、usage 或质量测量；未提供价格时费用明确标记为未计算，默认 rerank 保持关闭。 下一步：这是错误使用 chat 模型的历史失败记录；后续已由独立原生 Rerank 模型配置和 RET-09-01 的受限测量取代，默认开关仍关闭。
- 2026-07-27 `RET-08-01`：完成点查 rerank 授权快照与受控接入。结果：为 source-lookup 的 RRF 前 20 个候选建立仅本机会话可见的 rerank-source-lookup 预览和逐任务显式确认。授权不可逆绑定查询、候选顺序、候选投影、源块哈希、范围、policy revision 与 chat Provider/model/config revision；执行前重新校验 never-send-cloud、HTTPS 和全部快照条件。默认关闭的开关开启后，rerank 仅位于 RRF 与邻域扩展之间；未确认、失效、阻断、预检、Provider 或并发失败均保留本地 RRF。授权响应、UI 和请求体不暴露候选正文、绝对路径、内容哈希或任务摘要；审计只记录授权 ID、状态、网络请求数和延迟。未新增技术债；真实 Provider 延迟与费用尚未测量，默认保持关闭。 下一步：在已审阅、不可逆脱敏的真实样本上测量真实 Provider 的延迟和费用；在质量、成本和隐私边界均重新满足门槛前，rerank 默认保持关闭。
- 2026-07-27 `RET-07-03`：完成点查 rerank A/B 评测与受控 adapter 合同。结果：新增 path-free RerankerPort、复用已验证 chat Model 的 HTTPS/revision adapter 和严格 JSON 校验；未知或重复候选 ID、非有限或越界 relevance、无序平分和非法响应均整体拒绝。新增 top-20 到 top-8 的不可逆脱敏 A/B fixture 与报告：脚本化本地 adapter 的 macro recall@8 +1.0、precision@8 +0.25、MRR@8 +1.0，fixture latency gate 通过。真实 Provider 延迟和费用没有测量，因此 passesGate=false，未接入生产 source-lookup，默认保持关闭；测试未调用真实 Provider，未新增技术债。 下一步：建立 rerank-source-lookup 的候选正文外发授权、内容哈希重验和真实 Provider 延迟/成本评测后，才评估生产点查启用。
- 2026-07-27 `RET-07-02`：完成单元卡片生成、失效与受控召回。结果：新增独立单元卡片投影、来源块引用、FTS 和向量 migration；仅聚合 current、可核验、非 stale、非 pending 且人工 accepted 的 metadata 候选。用户确认的 index-unit-card 以 chat 摘要和 Embedding 两份独立授权执行，每次网络调用前重验范围、never-send-cloud、来源哈希、policy 和 Provider revision。卡片输出只包含已审核概念，命中会展开为原始块证据而不作为 evidence 返回。源块、scope、审核决定和 Provider/default profile 变化均会失效卡片；feature flag 默认关闭。脱敏评测达到 coverage=1.0、原始引用召回=1.0、macro recall@8 增益=0.5。未新增技术债，测试未调用真实 Provider。 下一步：实施 RET-07-03：以独立 A/B 评测验证点查 rerank 的质量、延迟与成本。
- 2026-07-27 `RET-07-01`：完成 LLM 元数据抽取与本地审核。结果：新增独立的 index-metadata 授权与冻结批次：该 operation 按用户确认默认批准，但仍在每次调用前重验范围、never-send-cloud、policy revision、Provider/model 和块内容哈希。仅向已验证的 HTTPS chat Provider 发送批次内序号和正文；严格的完整 JSON 校验失败时不会部分写入。候选绑定 vault、文档、块序号和哈希；低置信度或新 concept_key 进入 required-check，并提供本机受保护的接受/排除与审核通过率报告。候选不会自动合并、改写源文件或改变检索排序。测试使用 fake Provider，未发出真实 Provider 请求。 下一步：实施 RET-07-02：仅消费已审核候选生成单元卡片，并在源块变化后使卡片失效。
- 2026-07-27 `RET-06-02`：完成 Embedding 批量客户端、配置指纹与跨 vault 缓存。结果：OpenAI-compatible client 现以受限批次调用 create_embeddings，并按响应 index 恢复原始顺序，拒绝缺失/重复 index、非有限值和不一致维度。Embedding profile fingerprint 固定绑定 provider ID、endpoint、Provider.updated_at 配置 revision、model ID 与维度；index.sqlite 新增可回滚 embedding_cache migration，使用 float32 BLOB，按 profile 和归一化输入缓存。缓存命中不出网；同一 locator 出现多维度、损坏向量、Provider 配置变更或非 HTTPS endpoint 均失败关闭。执行 API 仅在 RET-06-01 授权核验通过后运行，并在每个网络批次前重新读取授权快照；响应只含状态和计数，不含正文或向量。未写入 index_blocks、未建立向量检索表或内存矩阵，未新增技术债。 下一步：实施 RET-06-03：消费已验证的 profile/cache，持久化 current block vector，维护 vault/profile float32 矩阵和真实 semantic_status。
- 2026-07-27 `RET-06-04`：完成独立三路召回、RRF 与点查语义查询。结果：source-lookup 在受 policy 和冻结 manifest 限制的相同路径集上独立执行 BM25、heading_path 前缀和完整 profile 的本地 KNN，再用 RRF k=60 融合；任一通道都不向另一通道提供硬过滤。命中证据补入同文档相邻块，并保留 lexical、semantic、heading 或 neighborhood 渠道标记。OBSIDIAN_PLATFORM_HYBRID_RETRIEVAL 默认关闭；开启后使用已验证的默认 HTTPS embedding Provider 只发送当前用户点查问题。用户已确认该查询默认授权、无额外交互；不发送 vault 正文、索引块或绝对路径。版本化脱敏评测的两条语义改写均达到 recall@8=1.0，RRF k=20/60/100 同分，保留 k=60；未新增技术债。 下一步：实施 RET-07-01：在稳定的混合召回之上增加受控的 LLM 元数据抽取与审核。
- 2026-07-27 `RET-06-03`：完成 float32 向量存储、内存矩阵与健康状态。结果：index.sqlite 新增可回滚的 ret-06-03-index-block-vectors-v1 migration，以 current block、完整 embedding profile fingerprint、正文哈希、授权输入哈希和归一化 float32 BLOB 绑定向量。已授权批次在最终快照重验后将缓存命中和新建向量一次性绑定到仍为 current、可核验、非 stale、非 pending 的块。repository 按 vault/profile 缓存精确 KNN 矩阵，保存、失效、回填、关联状态转换和重建路径在提交成功后使矩阵 generation 失效；检索与健康检查拒绝跨 vault/profile、非允许路径、损坏、零向量或 rich 输入哈希不匹配的行。semantic_status 现返回 unavailable、partial、available 或 blocked，并报告最大单 profile 覆盖率。未新增 Provider 请求、授权操作或正文出网路径，未新增技术债。 下一步：实施 RET-06-04：以独立 vector、lexical 与 heading 候选集校准 RRF，并在不改变 D-002 授权边界的前提下接入点查路径。
