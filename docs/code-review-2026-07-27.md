# 代码审查报告 — RET-00 ~ RET-09 检索改造
**日期：** 2026-07-27  
**范围：** 后端 + 前端全栈（109 个未提交变更，约 +21k/-12.5k 行）  
**审查维度：** 正确性与检索质量 · 可维护性与结构  
**证据强度约定：** ✅ 代码核实 · ⚠ 推断/需运行验证 · ❓ 逻辑疑问，需讨论

---

## 一、阻塞级（需修复后才能合并）

### B-1 Rerank eval 输出的 `cases` 数组为空，指标来源不可追溯
**文件：** `docs/ret-07-03-rerank-ab-v1.json`  
**证据强度：** ✅ 直接读取文件

`ret-07-03-rerank-ab-v1.json` 顶层 `"cases": []`（空数组），但 `reranked.macroMrrAt8 = 1.0`、`baseline.macroMrrAt8 = 0.0`。  
这意味着汇总指标无法追溯到任何具体案例的中间结果。AGENTS.md §6 要求"检索算法或阈值……必须运行版本化 golden eval 并记录指标对比"——记录格式不完整，无法满足该要求。

**更严重的问题：** `baseline.macroMrrAt8 = 0.0` 说明 fixture 里的 RRF 基线完全无法召回期望候选，这使得 `macroMrrGainAt8 = 1.0` 的"增益"没有有效基准。若 baseline 本来就无法召回，rerank 的增益数字对生产环境没有参考价值。

**修复建议：** 确认 eval 脚本是否正确将逐案例结果写入 `cases` 字段；同时补充 fixture 数据使 baseline 至少有合理的 MRR（>0），否则 gate 结论无法支撑 RET-08/RET-09 已通过的判定。

---

### B-2 Hybrid / Unit-card golden set 仅 2 cases，不具备统计代表性
**文件：** `apps/service/tests/fixtures/retrieval-hybrid-golden-v1.json`、`retrieval-rerank-golden-v1.json`、`unit-card-golden-v1.json`  
**证据强度：** ✅ 直接读取

三个 fixture 各 2 cases，全部是英语+数学合成数据（`provenance = synthetic-deidentified`），没有"七年级上册英语"这一真实 vault 场景。  
AGENTS.md §4.5："任何阈值、top-k……都必须有 golden set 依据，不把一次 benchmark 当作普遍结论"。  
2 cases 无法排除偶然性，`selectedRrfK = 60` 和 unit-card gate 门槛都是基于此推导出的。

**修复建议：** 在合并前补充至少 8–10 个 case，覆盖真实 vault 里的枚举型、点查型、跨资料类型场景。

---

## 二、需改正（建议在当前迭代内处理）

### M-1 查询 embedding 直接调用 `create_embeddings`，未经 outbound policy 授权
**文件：** `apps/service/application/sessions.py:2549`  
**证据强度：** ✅ 代码核实

```python
vectors = self.provider_service.create_embeddings(
    resolved.provider.provider_id,
    resolved.model.model_id,
    (query_text,),                # ← 用户输入的查询文本，无 policy check
    ...
)
```

索引 embedding（`EmbeddingAuthorizationService`）走了完整的 preview → request → check 授权链路；查询 embedding 直接出网，没有任何 outbound policy 检查。  
AGENTS.md §4.5："发送正文到 Provider 前必须通过 outbound policy"。  
虽然查询文本由用户主动输入而非知识库正文，但规范没有此区分，且用户在某些场景下可能在 query 中粘贴私密内容。  

**修复建议：** 在 `_semantic_candidates` 里加 outbound policy 预检（至少检查当前 vault 的 outbound_mode），或在 AGENTS.md §4.5 中明文豁免 query text，并在代码注释里标注理由。

---

### M-2 邻居块扩展未显式过滤 policy，依赖隐式文档级授权
**文件：** `apps/service/application/sessions.py:2626-2654`  
**证据强度：** ✅ 代码核实

`_expand_retrieval_neighborhoods` 取 `document.blocks` 里的 ±1 邻居块，而 `document` 已经通过 `_allowed_retrieval_documents` 的 retrieval policy 检查。逻辑上邻居块属于同一文档，因此 policy 结论继承，**不构成安全漏洞**。  
但以下场景存在隐患：如果某文档后续引入块级 policy（如 `block_kind = 'secret'`），邻居扩展会绕过该过滤。目前虽无块级 policy，但架构上缺少防护点。

**修复建议：** 在邻居扩展处加注释说明"邻居块沿用文档级 policy 授权，块级 policy 扩展时须在此处补充过滤"。

---

### M-3 `_rerank_absolute_path_pattern` 正则可能误拦合法候选
**文件：** `apps/service/application/sessions.py:95-104`  
**证据强度：** ⚠ 推断（需在真实 vault 数据上验证）

正则末分支：
```
/ [^\s/\"'`]+ (?:/[^\s/\"'`]+)*
```
该模式会匹配 Markdown 链接锚点（`/anchor`）、URL 路径段（`/api/v1`）、LaTeX 分数表达式（`a/b`）中的伪路径形式。  
对于英语教材中含有"读音标记 `/æ/`"或分数讲解的块，候选会被静默 block，导致这些块从 rerank 候选中消失，只剩 local RRF 排序，且前端没有任何提示。

**修复建议：** 在 rerank 被 block 时将 `blocked_candidate_count` 的原因一并记录（已有字段但没有 per-candidate 原因），或收窄正则，要求 Unix 路径至少有两段（`/a/b`）。

---

### M-4 `plan_map_reduce` 在 batch 超出预算时逻辑顺序有歧义
**文件：** `apps/service/domain/retrieval_enumeration.py:164-207`  
**证据强度：** ✅ 代码核实

```python
if current and current_tokens + item.token_estimate > available:
    if len(batches) >= budget.max_batch_count:
        uncovered.extend(current)   # ① 先将当前 batch flush 到 uncovered
        current, current_tokens = [], 0
        uncovered.append(item.ordinal)  # ② 再将新 item 也 uncovered
        continue
    batches.append(...)
    current, current_tokens = [], 0
if not current and len(batches) >= budget.max_batch_count:
    uncovered.append(item.ordinal)
    continue
```

路径 ①：当 `len(batches) >= max_batch_count` 时，`current` 里已经有几个 item，它们被 flush 到 `uncovered` 后，while 循环结束——这是正确的。  
但是：如果执行完 ① 之后 `continue`，下一个 item 在 `not current` 分支检查时 `len(batches) >= max_batch_count`，会直接 uncovered。这与 ① 里直接 `uncovered.append(item.ordinal)` 后 continue 的行为是等价的，不构成 bug。  
**问题是可读性**：两条 `uncovered` 路径交叉，代码不能一眼看出"batch 满了之后所有后续 item 都进 uncovered"。测试覆盖了 max_batch_count 场景，但逻辑值得用注释标注。

---

### M-5 `search_unit_cards_vector` 做全量扫描后过滤 allowed_paths，O(N×D) 无预过滤
**文件：** `apps/service/adapters/sqlite_index_repository.py:1532-1575`  
**证据强度：** ✅ 代码核实

SQL 查询先取出所有 `unit_card_vectors`（按 vault + profile 过滤），然后在 Python 侧对每行调用 `_resolve_unit_card_sources`（含子查询），最后过滤 `allowed_relative_paths`。  
对于个人知识库规模（数十张卡片），不构成性能问题。但如果 unit_card 数量增长（多学科多册次），每次检索都会全量加载所有卡片向量到内存。  

**修复建议：** 在 SQL 层加 `JOIN unit_card_sources` 过滤 `allowed_relative_paths`，或在 `_resolve_unit_card_sources` 里加 early-exit。当前规模可推迟，但需标注技术债。

---

## 三、可接受（代码基本正确，记录供后续参考）

### L-1 两套迁移元表并存：`index_schema_migrations` vs `index_repository_migrations`
**文件：** `apps/service/adapters/sqlite_index_repository.py`  
**证据强度：** ✅ 代码核实

最早的 graph projection 迁移（ret-01-02）写入 `index_schema_migrations`；后续所有迁移（ret-02 至 ret-07）写入 `index_repository_migrations`。两表并存，功能上不冲突，但历史查询时需要看两张表。建议在技术债中记录，并在未来合适时机迁移到统一表。

---

### L-2 `_apply_graph_projection_chunking_structure_migration` 的 ALTER TABLE 先于 migration_id 检查
**文件：** `apps/service/adapters/sqlite_index_repository.py:274-308`  
**证据强度：** ✅ 代码核实

```python
if "chunking_structure_json" not in columns:
    connection.execute("ALTER TABLE graph_projection_blocks ADD COLUMN chunking_structure_json TEXT")
existing = connection.execute("SELECT 1 FROM index_schema_migrations WHERE migration_id = ?", ...)
if existing is None:
    connection.execute("INSERT INTO index_schema_migrations ...")
```

列添加的幂等检查与 migration_id 的幂等检查是分开的：如果 ALTER TABLE 执行成功但进程在 INSERT migration_id 前崩溃，下次重启会再次尝试 ALTER TABLE（因列已存在会被跳过），再次 INSERT migration_id。最终结果正确，但两个幂等检查不在同一原子操作里。相比其他迁移（先检查 migration_id 再做 DDL），这里顺序是反的。不影响正确性，但不一致。

---

### L-3 `_is_coarse_unit_lookup` 检测逻辑过于简单
**文件：** `apps/service/application/sessions.py:2594-2596`  
**证据强度：** ✅ 代码核实

```python
return "unit" in normalized or "单元" in content
```

"how many units of energy..."、"community unit"、"unity of style" 等都会触发 unit card 检索路径，造成额外的向量查询开销，且可能把不相关的 unit card 插入 ranked 列表。  
当前规模影响有限，unit card 检索有 `seen_cards` 去重，且最终 evidence 上限是 8 条。可接受，但建议后续迭代收紧判断（结合 `_KNOWLEDGE_ORGANIZATION_MARKERS` 或 heading prefix 结果）。

---

### L-4 前端 `confirmPersistentKnowledgeOrganizationAuthorization` 无 deny 路径
**文件：** `apps/web/src/app.js`  
**证据强度：** ✅ 代码核实

前端对知识整理和深度创作授权只实现了 `approved: true` 的提交逻辑，没有 deny/cancel 的对应路由。用户若想拒绝授权，只能刷新会话或准备新任务。  
后端有 `confirm_knowledge_organization_authorization(..., approved: bool)` 完整支持，前端未调用。可接受（主流程不受影响），但用户体验有缺口。

---

### L-5 `sessions.py` 3329 行，execute_task 分支过多，长期维护风险
**文件：** `apps/service/application/sessions.py`  
**证据强度：** ✅ 代码核实

`execute_task` 方法内对 `intent ∈ {deep-creation, knowledge-organization, completeness, source-lookup}` 各走独立 if 分支，每个分支约 80-150 行，共约 400 行。状态机转换（prepared → waiting-authorization → completed/failed）在四条路径里各自重复实现。  
不是 bug，是结构问题，当前实现是一致的。建议将各 intent 执行逻辑提取为独立策略类，以便后续扩展新 intent 时不需要修改 `execute_task` 本体。

---

## 四、评测证据质量总结

| Eval 文件 | Case 数 | 数据来源 | 核心指标 | 结论 |
|---|---|---|---|---|
| ret-00-04-baseline-v1.json | 多 | 真实 vault | — | 基线已建立 ✅ |
| ret-04-03-lexical-ab-v1.json | 多 | 真实 vault | — | A/B 已完成 ✅ |
| ret-06-04-hybrid-eval-v1.json | 2 | 合成 | recall@8=1.0 | **样本不足** ⚠ |
| ret-07-02-unit-card-eval-v1.json | 2 | 合成 | gain@8=0.5，gate 通过 | **样本不足** ⚠ |
| ret-07-03-rerank-ab-v1.json | **0**（输出空） | 合成 | baseline=0.0 → gain=1.0 | **基准无效 + 输出缺失** 🔴 |

---

## 五、优先处理建议

1. **立即修复 B-1**：确认 rerank eval 脚本输出格式，补全 `cases` 数组，修复 baseline fixture 使基线 MRR > 0。
2. **立即修复 B-2**：在合并前为 hybrid/unit-card/rerank 三个 fixture 至少各补充 6 个真实 vault case。
3. **本迭代内处理 M-1**：明确查询 embedding 的 policy 立场，代码或规范选一个补充说明。
4. **本迭代内处理 M-3**：验证绝对路径正则在真实教材内容（含音标、分数）上的误报率，必要时收窄。
5. **技术债登记 L-1、L-5**：纳入现有 `progress/tech-debts.json`，不需要阻塞当前合并。
