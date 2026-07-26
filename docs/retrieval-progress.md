# 检索改造开发进度

> 此文件由 `progress/` 下的结构化数据生成，请勿手工修改。

- 数据日期：2026-07-26
- 方案文档：`docs/retrieval-redesign.md`
- 任务总数：28
- 已完成：17
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
| `RET-05` 查询范围与枚举型汇总 | 让范围决定覆盖，完成可预览、可核验的全量汇总。 | 未开始 | 0/4 |
| `RET-06` Embedding 与混合检索 | 在明确出网边界后接入精确向量检索与独立通道融合。 | 未开始 | 0/4 |
| `RET-07` 受控增强 | 以独立开关验证 LLM 元数据、单元卡片和 rerank 的实际增益。 | 未开始 | 0/3 |

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
| `RET-05-01` 共享查询范围解析 | P0 | 未开始 | `RET-03-02` | 同一册次、单元和资料类型在查询与索引侧归一一致。 | `查询理解参数化测试` |
| `RET-05-02` 范围预览 API 与前端确认 | P1 | 未开始 | `RET-05-01`、`RET-03-03` | 用户在执行前能看到并修正实际检索范围。 | `API contract 测试`、`前端 unit 与 Playwright 流程` |
| `RET-05-03` 枚举型全量过滤与分桶 | P0 | 未开始 | `RET-05-01`、`RET-03-03` | 枚举范围由元数据决定，不受相似度 top-k 截断。 | `单元汇总 coverage 测试`、`超预算 gaps 测试` |
| `RET-05-04` 原子条目去重与分层生成 | P0 | 未开始 | `RET-05-03` | 跨资料重复条目正确合并，每个合并结果保留全部来源，非重复项不误并。 | `duplicate precision/recall 评测`、`引用完整性测试` |

## RET-06 Embedding 与混合检索

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-06-01` Embedding 出网授权与范围预览 | P0 | 未开始 | `RET-00-02`、`RET-05-02` | 未授权正文不出网，授权范围变化后旧授权失效。 | `policy unit 测试`、`拒绝、撤销和 revision 集成测试` |
| `RET-06-02` Embedding 客户端、指纹与缓存 | P0 | 未开始 | `RET-06-01` | 不同 endpoint/config revision 的同名模型绝不复用向量。 | `Provider adapter contract 测试`、`缓存命中与指纹隔离测试` |
| `RET-06-03` float32 向量存储、内存矩阵与健康状态 | P0 | 未开始 | `RET-06-02` | 只搜索 current 且 profile 匹配的向量，重建和模型切换后缓存一致。 | `精确 KNN 排序测试`、`内存缓存失效与覆盖率测试` |
| `RET-06-04` 独立三路召回与 RRF | P0 | 未开始 | `RET-04-03`、`RET-06-03` | 词法漏召回时向量可独立救回，任何通道都不作为另一通道的硬过滤器。 | `语义改写 recall@8`、`通道隔离回归测试` |

## RET-07 受控增强

| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |
| --- | --- | --- | --- | --- | --- |
| `RET-07-01` LLM 元数据抽取与审核 | P1 | 未开始 | `RET-06-02`、`RET-03-03` | 低置信度和新概念进入审核，未审核数据不直接触发不可逆合并。 | `抽取合同测试`、`人工审核通过率报告` |
| `RET-07-02` 单元卡片生成与失效 | P2 | 未开始 | `RET-05-04`、`RET-06-03` | 卡片不替代原始证据，粗粒度查询增益达到评测阈值。 | `卡片覆盖率与引用回归` |
| `RET-07-03` 点查 rerank 增益验证 | P2 | 未开始 | `RET-06-04` | 只有质量增益覆盖延迟和成本代价时才进入默认点查路径。 | `A/B 评测报告` |

## 风险

| 风险 | 严重度 | 状态 | 影响 | 缓解措施 | 归属任务 |
| --- | --- | --- | --- | --- | --- |
| `RET-R001` DocumentGraph 随 import task 删除而失联 | 高 | 已关闭 | 派生文档索引无法稳定重建，graph_block_id 与原文 locator 失去长期核验能力。 | RET-01-01 已冻结最小不可变投影、稳定身份和索引侧读写合同；RET-01-02 已在同一 index.sqlite 事务中写入 projection 与派生文档；RET-01-03 已验证按 graph identity 读取投影、删除完成态 import task 后重建，以及 PDF/DOCX locator 核验。缺失或身份不匹配的投影会失败关闭。 | `RET-01-03` |
| `RET-R002` Embedding 批量出网扩大隐私边界 | 高 | 开放 | 教材和个人笔记正文可能按 vault 或目录批量发送给云端 Provider。 | RET-00-02 已确认按选定目录或 vault 批次、显式确认的 D-002；RET-06 必须实现范围预览、逐文件排除、revision 失效和执行前核验，未授权时保持词法检索。 | `RET-06-01` |
| `RET-R003` SQLite 新旧 schema 与派生索引不同步 | 高 | 开放 | 升级失败、stale FTS 行或向量孤儿会造成错误召回和难以恢复的索引状态。 | RET-02-01 已为富 IndexBlock 建立显式、可重试的结构列 migration，旧行以兼容默认值读取，失败注入会回滚富列且可在同一旧库重试；graph migration 也已独立事务化，避免后续 migration 失败留下无标记表。RET-02-02 已保持旧三字段与富字段同事务写入，并以 current-only、可重试回填补全哈希及可精确核验的 durable graph 结构；原始行的 legacy/rich 报告会拒绝静默覆盖不匹配或损坏投影。RET-02-03 已以 `OBSIDIAN_PLATFORM_RICH_BLOCK_READS` 保留 legacy 默认和显式 rich 切换；rich 模式遇到一致性问题会失败关闭，健康状态仅返回模式、状态和问题码。RET-03-03 已为块级规则元数据增加独立、可回滚的 migration，并将 document、block 与 metadata 写入保持在同一事务；filter_blocks 显式排除 stale、非 current、pending 与不允许路径。RET-04-01 已为 FTS5 与 map 完成可回滚 migration、eligible current 回填，以及 save、invalidate、rebuild 与失败回滚的同事务同步。RET-04-02 已以独立、可回滚内容回填 migration 将中英文词法文本同步到同一 FTS/map 生命周期；search_lexical 只读取 vault 内 current、verifiable、非 stale、非 pending 且由调用方允许的路径。RET-06-02 仍须将同一生命周期约束扩展至向量与缓存。 | `RET-06-02` |
| `RET-R004` 目录规则只覆盖示例命名 | 中 | 开放 | 真实资料无法解析册次和单元，枚举型检索会返回 recoverable 或错误 scope。 | RET-00-02 已收集不可逆脱敏真实结构样本；RET-03-02 已以同一 fixture 固定严格的路径/标题归一化，并对根级、仅资料信号和仅位置标题分别返回 unknown 或 recoverable，绝不伪造 scope。仍须在更多已审阅、不可逆脱敏样本上扩展规则；RET-05-01 消费同一合同解析查询范围。 | `RET-05-01` |
| `RET-R005` 固定阈值导致误去重或漏召回 | 中 | 开放 | 0.92 相似度、top-k、RRF 参数和批预算若未经评测会直接固化样本偏差。 | RET-00-03 已建立脱敏合成 golden set，RET-00-04 已记录当前手写检索、Windows FTS5/BM25 与 float32 KNN 的原始基线。用户确认 RET-04-02 的词法最低门槛为 macro recall 0.6569、macro precision 0.2813；实现结果为 0.6875、0.302083。RET-04-03 已在同一 fixture 完成版本化 A/B：相对旧手写评分，FTS macro recall +0.030555555555555558、macro precision +0.020833333333333315，passesGate=true；生产 source-lookup 已仅使用 FTS，关闭本机开关时失败关闭，不回退旧打分。该门槛不校准 top-k、RRF 或去重参数，RET-06-04 再校准 RRF 与混合候选参数。 | `RET-06-04` |
| `RET-R006` 同名 Embedding 模型错误复用缓存 | 中 | 开放 | 不同 Provider endpoint 或配置 revision 产生的不可比较向量可能被混用。 | RET-06 使用 embedding_profile_fingerprint 绑定 Provider、endpoint/config revision、模型和维度。 | `RET-06-02` |
| `RET-R007` 向量常驻缓存失效不完整 | 中 | 开放 | 索引更新、模型切换或 policy revision 后仍可能搜索旧矩阵。 | 按 vault/profile 建缓存 generation，save/invalidate/rebuild/model switch 全部触发原子失效并测试。 | `RET-06-03` |

## 技术债

当前无登记技术债。

## 最近日志

- 2026-07-26 `RET-04-03`：完成词法 A/B、会话切换与手写评分退役。结果：新增版本化 docs/ret-04-03-lexical-ab-v1.json：在同一 synthetic golden fixture 上，FTS 相对旧手写评分的 macro recall +0.030555555555555558、macro precision +0.020833333333333315，passesGate=true。source-lookup 在冻结快照、scope、policy 与 manifest 过滤后只调用 search_lexical，并保留证据身份、定位和引用合同；matched_channels 标记为 lexical。OBSIDIAN_PLATFORM_LEXICAL_RETRIEVAL 默认启用；关闭时失败关闭且不回退旧评分。生产 SessionService 已移除 _retrieval_score、_retrieval_terms 和 _semantic_similarity；历史实现仅保留在 benchmark helper 中以复现实验。浏览器 mock 同步至既有 execute/stream SSE 合同。 下一步：实施 RET-05-01：解析查询 scope；随后 RET-05-02 以 BlockFilter 实现枚举型全量汇总。
- 2026-07-26 `RET-04-02`：完成中英分词、heading 加权与 search_lexical。结果：新增纯领域中文分词器：使用 heading、tags、links 的本地域词典做最长匹配，未命中 CJK 退回 overlapping bigram；英文保留原文并由 FTS5 Porter tokenizer 归一。新增 LexicalQuery/BlockHit port 合同和 SQLite search_lexical，查询显式限制 vault、current、verifiable、stale、pending 与调用方允许路径，并以 heading 10 倍 BM25 权重排序。新增 RET-04-02 可回滚内容回填 migration，使既有 FTS 行重建 en/cjk/heading/tag 文本。用户确认的 synthetic golden 下，macro recall 0.6875、macro precision 0.302083，均超过 0.6569/0.2813 最低门槛。 下一步：实施 RET-04-03：完成旧手写检索与 FTS 的 A/B 报告、会话切换和退役判断。
- 2026-07-26 `RET-01-01`：定义耐久 graph projection 的领域合同与索引侧读写端口。结果：新增最小、不可变 projection；稳定身份为 vault、graph、revision、block，保留 source identity、selected attempt、block kind、reading order、locator、confidence 与 retrieval projection。合同序列化不保存 import task、原始 payload、artifact、issue 或 input snapshot。 下一步：完成 RET-00-03 与 RET-00-04 的评测闸门后，实施 RET-01-02 的 SQLite migration 与派生文档/投影原子写入。
- 2026-07-26 `RET-00-03`：建立版本化、脱敏的检索 golden set。结果：新增 18 个合成教学块与 24 条查询，覆盖点查、枚举、知识整理、重复簇和无 scope 的 recoverable 返回；fixture 校验器可对每条查询计算 recall、precision、scope coverage 与 duplicate precision，未读取或保存真实 vault 正文、路径、Provider 凭据或授权 token。 下一步：RET-00-04 基于该 fixture 运行当前手写检索基线与 Windows FTS5/BM25、float32 KNN smoke，并记录原始指标；不把本次合成集结果直接写成生产阈值。
- 2026-07-26 `RET-00-04`：运行当前手写检索基线、Windows FTS5/BM25 与 float32 KNN 能力 smoke。结果：新增一条命令可重复执行的评测工具与原始 JSON 报告。synthetic golden set 上的当前手写打分宏平均为 recall 0.6569444444444444、precision 0.28125、scope coverage 0.0、duplicate precision 0.125；scope 和重复聚合明确记录为当前实现不支持。SQLite 3.53.1 可创建 unicode61、porter unicode61 与 trigram FTS5 表，heading 权重 10 的 BM25 smoke 返回 greeting 教材块为首项。固定种子 20000 x 1024 float32 精确 KNN 的首轮中位延迟为 1477600 ns、矩阵 81920000 bytes、工作集 82004096 bytes，双轮中位延迟差 0.02524363833243097，排名完全一致。 下一步：实施 RET-01-02：在同一 index.sqlite 事务中完成耐久 graph projection migration 与派生文档/投影原子写入；FTS 和向量能力仅作为后续 RET-04、RET-06 的评测前置，不在本步改变生产检索。
- 2026-07-26 `RET-01-03`：实现 graph identity 投影读取、任务删除后重建与 locator 核验。结果：IndexingService 对带 graph provenance 的派生 Markdown 按 vault_id、graph_id、graph_revision 从 index.sqlite 读取耐久投影，并按 frontmatter locator 选择 retrievable block；首次原子提交使用同一提交内的投影，后续重建只读取耐久存储。投影缺失、source/attempt 身份不一致或 locator 不匹配时作业失败关闭，不退回 Markdown 切块。回归覆盖删除完成态 import task 后 PDF 与 DOCX locator 的投影正文和定位信息均可恢复。 下一步：实施 RET-02-01：在已验证的 projection 读取链路上扩展富 IndexBlock 与显式 schema migration。
- 2026-07-26 `RET-01-02`：实现耐久 graph projection migration 与提交期原子写入。结果：index.sqlite 新增版本化 graph projection migration、不可变 identity 读写和 block locator 持久化。带 selected DocumentGraph 的 source commit unit 会将派生 Markdown 索引文档与 projection 放在同一 SQLite 事务中；重复相同 projection 安全重试，不同内容复用相同 key 会拒绝。若该索引事务失败，提交服务恢复本单元的 vault 文件备份、记录 failed journal 并保留 retry-commit，而不会把笔记标为已提交。 下一步：实施 RET-01-03：以 graph identity 从 index.sqlite 读取 projection，覆盖删除完成态 import task 后的索引重建与 PDF/DOCX locator 引用核验。
- 2026-07-26 `RET-01-04`：新增投影重建验证测试面板。结果：新增本机会话保护的 durable projection 摘要接口，只返回 graph identity、块计数、PDF/DOCX locator 类型统计、PDF 页摘要、DOCX 部件计数和稳定 locator digest；不返回正文、来源路径、source identity 或转换工件。完成态任务详情中的测试面板按读取摘要、显式确认删除、重建索引、重读摘要顺序执行，并以 locator digest 与索引健康状态显示结果。 下一步：实施 RET-02-01：在已验证的 durable projection 读取链路上扩展富 IndexBlock 与显式 schema migration。
- 2026-07-26 `RET-02-01`：扩展富 IndexBlock 并实现可恢复的 schema migration。结果：IndexBlock 现持久化块正文哈希、类型、标题路径、locator、graph block identity、阅读顺序、置信度和检索加工字段，同时保持既有三参数构造调用兼容。派生 Markdown 会保留 durable graph projection 的结构字段；原生 Markdown 保持默认结构值。index.sqlite 新增带 migration ID 的 index_repository_migrations 和幂等结构列，旧行仍可原地读取且正文与文档 identity 不变。rich migration 失败会回滚，随后可在同一旧库重试；graph migration 也独立使用 savepoint，避免无标记半完成状态。 下一步：实施 RET-02-02：以富字段双写回填既有 current documents，并输出新旧正文、sequence 与 document identity 一致性报告。
- 2026-07-26 `RET-02-02`：实现结构双写、current-only 回填与新旧读取一致性报告。结果：index_blocks 继续在同一写入事务保留 sequence、location、text 与富字段。内部回填仅处理 is_current=1 行：原生块只补确定性哈希和默认结构；带完整 graph location、来源身份和正文均匹配的块从耐久 projection 恢复结构。报告按原始 SQLite 行独立校验 legacy 与 rich 读取，不包含正文；哈希、rich 正文、投影 provenance 或投影 payload 不一致时保持原行并返回稳定 issue。覆盖幂等、stale 行不变、带冒号 graph identity、损坏投影拒绝写入和写入失败整体回滚。 下一步：实施 RET-02-03：以 feature flag 切换富块读取，保留 legacy 回退并将一致性报告接入健康状态。
