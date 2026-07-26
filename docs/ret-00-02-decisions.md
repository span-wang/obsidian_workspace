# RET-00-02：产品决策与真实 vault 样本

记录日期：2026-07-25。此文件只记录决策、已脱敏结构和可复核证据；不含正文、真实标题、vault 名称、绝对路径、vault ID、Provider 凭据或可逆映射。

## D-001：耐久 graph 投影与删除语义（已确认）

选择：已提交且仍被索引或引用的派生笔记，在索引库保留最小、不可变的 graph projection。完整 `DocumentGraph`、转换工件和任务私有候选仍归 import task 所有。

投影的稳定身份为 `(vault_id, graph_id, graph_revision, block_id)`；路径不是主键。RET-01 至少保留 `kind`、`reading_order`、`locators`、`confidence`、`retrieval_projection`、source identity 和 selected attempt，避免复制无关的转换工件。

| 事件 | 确认语义 |
| --- | --- |
| 删除完成态 import task | 只删除任务库中的完整 graph、工件和临时提案；不得删除索引库投影。 |
| 同一 graph 的新 revision | 追加新 revision，不原地修改旧投影。 |
| 派生笔记重命名或移动 | 重绑定索引文档路径；不复制或删除投影。 |
| 来源移动、派生笔记内容失效 | 标记索引文档 stale；保留投影和引用核验所需 locator。 |
| 派生笔记删除 | 先使索引文档失效；仅当所有关联派生笔记已删除且没有 current index reference 时，才允许后续受控 GC。 |
| 投影或索引写入失败 | 同一索引数据库事务回滚，不能留下孤儿投影或没有投影的已提交索引文档。 |

现状依据：完整 graph 现在仅保存在 `tasks.sqlite3.import_conversion_graph_revisions`，而 `SqliteImportTaskRepository.delete()` 会删除它；索引库目前只保存 Markdown 切分的 document/block，无法使用 graph identity 重建或核验 locator。具体 schema、端口、事务和删除后重建测试归 RET-01-01 至 RET-01-03，风险 `RET-R001` 仍保持开放，直至实现验证完成。

## D-002：Embedding 出网边界（已确认）

当前没有“索引块到 Embedding”的执行链：Provider 仅在模型测试时向 `/embeddings` 发送固定 `ping`，索引没有向量、缓存、profile fingerprint 或向量检索。现有正文出网仅发生在聊天生成，且由调用方在生成前检查授权；Provider 客户端本身不会强制 policy。

提案是最小权限的、一次索引批次一次授权：

- `index-embedding` 与未来的 `index-metadata` 必须是两个独立 operation、两个独立授权；后者不能借用前者。
- 默认只允许用户选定目录；只有用户在预览中明确选择时才允许整个 vault。每批次冻结精确文件集合和块集合，预览显示 Provider、模型、文件数、块数，以及会发送的内容类别（contextual prefix 与检索正文）。
- `never-send-cloud` 逐文件重算并优先于批次授权；新增文件、范围变化、policy revision、Provider endpoint/config revision 或模型变化都使旧预览和授权失效。执行前再次核验，执行中若失效则停止后续请求。
- 当前 `always-allow` 不能自动视为对正文 Embedding 的同意；`index-embedding` 仍需明确批准。云端 Provider 只允许 HTTPS；本机 loopback Provider 如需支持，另立明确的本机边界。
- 未获批准、预览不一致或 scope 无法枚举时，保持词法检索并返回可恢复状态，绝不静默降级为正文出网。

用户已接受上述产品边界。该决定允许 RET-06 实现该授权模型，但不批准任何具体索引批次：每次实际执行仍必须冻结范围、展示 Provider/模型/文件数/块数，并取得该批次的运行时授权。`RET-R002` 因实现和回归尚未完成而保持开放。

## D-003：目录规则与样本（已确认）

选择：不要求用户为检索改造重命名现有 vault。`RET-03-02` 从版本化、不可逆脱敏 fixture 建规则；不能推断 subject、grade 或 unit 时必须失败关闭并要求范围，而不是伪造精确 scope。

本次只读采集当前已授权本机 vault 的相对 Markdown 路径和当前索引位置；没有读取或保存 Markdown 正文。样本显示两种真实形态：根级 native Markdown，以及四级的 derived Markdown 路径。当前索引位置只有 `line:` 形态，且其中没有可保留的 grade、unit 或 subject 信号。因此后续 normalizer 必须覆盖“路径/标题没有可解析 scope”的可恢复结果。

具体 fixture 和脱敏协议见 `docs/fixtures/ret-00-02-vault-directory-samples.json`。`RET-R004` 仍保持开放，直到 RET-03 的参数化规则在更多已审阅样本上通过。

## 已完成与后续

本任务没有修改 Provider、授权策略、数据库 schema 或真实 vault 内容，也没有发起任何 Provider 请求。D-001 至 D-003 已明确，RET-00-02 完成；随后由 RET-01-01 开始实现 graph projection 合同。
