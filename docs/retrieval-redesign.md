# 检索层改造方案：结构化 → Embedding → 混合检索 → 去重召回

面向本仓库 `apps/service` 的现状改造。目标场景：一个知识库 = 一册教材 + 教辅 + 个人整理的单元知识清单 + 练习题；典型请求「输出七年级上册第一单元知识点汇总」要求穷尽覆盖并去重。

已确认的技术选择：Embedding 走云端 OpenAI 兼容 Provider；范围元数据只做路径与标题的确定性结构推断；汇总类任务按意图路由走全量枚举。检索栈由本方案给出推荐。

## 对话呈现修订（2026-07-28）

用户确认全部会话模式均以可直接使用的内容为优先：点查回答、知识整理、深度创作和完整性检查的主对话区不默认解释知识库、检索过程、证据、引用、文件位置或编号。检索与覆盖内容仍是模型生成和状态判断的内部依据，不得删除、放宽范围或绕过 `never-send-cloud`。

每段关联内容在界面上可带一个可点击的应用证据角标。角标只用于定位右侧“应用证据”面板，且不应进入复制的正文；右侧保留用户可理解的文件、标题、页码、摘录、状态和现有打开入口。此交互修订取代本文中“每个条目在正文携带 `[证据 n]`”的展示要求，但不改变内部 citation、coverage、来源身份或可核验锚点合同。

---

## 零 审核结论（2026-07-25）

**结论：有条件通过。** 诊断、总体架构与“先结构、后词法、再向量”的方向正确，可以作为开发基线；但原稿存在会导致错误去重、数据失联和迁移失控的实现缺口。以下修订优先于后文未明确覆盖的旧表述。

### 必须先修正的问题

**P0-1：块去重不能使用文档级 `content_sha256`。** 当前 `IndexedDocument.content_sha256` 是整篇 Markdown 的哈希。若拿它判断块重复，同一文档内的所有块都会共享同一个值。必须新增 `block_content_sha256`，对规范化后的块正文计算哈希；向量失效另用 `retrieval_input_sha256`，对“contextual prefix + retrieval text”计算哈希，两者不能混用。

**P0-2：索引不能长期依赖 import task 数据库里的 `DocumentGraph`。** 当前 `import_conversion_graph_revisions` 确实保存 graph，但 `SqliteImportTaskRepository.delete()` 会随导入任务删除这些记录。推荐在提交派生笔记时，将选定 graph 的检索投影复制进索引侧的耐久表，至少保留 `graph_id / graph_revision / block_id / kind / reading_order / locators / confidence / retrieval_projection`。索引重建读取该投影，不跨库读取可被删除的任务记录。

**P0-3：结构字段只能有一个真相源。** `block_kind / heading_path / locator / graph_block_id / reading_order / confidence` 属于索引块本体，持久化在 `index_blocks`；`subject / grade_volume / unit_no / material_type` 属于确定性范围元数据，持久化在 `index_block_meta`。禁止两张表重复保存同一字段。

**P0-4：Embedding 指纹不能只有 `model_id`。** OpenAI 兼容 Provider 可能在不同 endpoint 下复用相同模型名，Provider 配置也可能变更。缓存键与向量版本必须绑定 `provider_id + endpoint/config revision + model_id + dimension` 形成的 `embedding_profile_fingerprint`；仅用 `model_id` 会错误复用不可比较的向量。

**P0-5：SQLite 改造必须有可验证迁移。** 当前索引库只做轻量 `ALTER TABLE`，而本方案会新增 FTS、向量、映射和耐久 graph 投影。必须增加显式迁移 ID、旧库升级测试、失败回滚测试和 current/stale 过滤；不能要求用户删除索引库重建来代替迁移。

### 需要通过评测确定、不能先写死的参数

- `余弦 > 0.92`、`top-50`、`RRF k=60`、`top-8`、每批 `8k-12k`、总输入 `200k` 都是初始候选值，最终值必须由 golden set 与所选模型上下文窗口校准。
- float16 落盘是否保持召回不能由一次 Linux 样本推出。V1 先用 float32 落盘与内存检索；只有 Windows 实机的 top-k 重合率、延迟和内存测试通过后，才单独引入 float16 优化。
- 去重只自动合并 `block_content_sha256` 完全相同的块；标题词归一或向量相似只能产生候选簇，不能静默合并原始证据。

### 开发原则

1. 先建立评测集和迁移闸门，再改检索行为。
2. 先持久化结构投影，再让索引消费结构；任务库不是索引依赖。
3. 先双写、回填、对比，再切换读取路径；每一步保留可回退入口。
4. 枚举型与点查型共用 scope 解析和引用模型，但使用不同的候选选择与预算策略。
5. 每个阶段以 `progress/tasks/retrieval.json` 的任务和验收条件为准，展示看板由 `npm run progress:build` 生成。

---

## 一 诊断：现在为什么召不准

四个问题都在代码里，不是调参能解决的。

**1. 检索只有一个手写打分函数，没有真检索。**
`application/sessions.py:1866` 的 `_retrieval_score` 把查询和块都切成 `_retrieval_terms`（`sessions.py:1905`：英文按 `[a-z0-9]+`、中文按**单字 + bigram**），然后算集合重叠率，六路加权求和（keyword×4 + semantic×2 + structure×2 + metadata + tag×1.5 + link）。它标注为 `semantic` 的那一路（`sessions.py:1913`）是 Jaccard 相似度，与语义无关。

后果很直接：查询「七年级上册第一单元知识点汇总」被切成`七年级上册第一单元…`的单字与 bigram，`第一`、`单元`、`知识`、`点` 这些字符片段会在整册教材里到处命中。分母是 `len(query_set)`，长查询被稀释；一个只含「单元」二字的无关块和真正的 Unit 1 词汇表得分可能接近。中文单字召回的噪声上限就在这里。

**2. 块的粒度由 Markdown 标题决定，一个块可能是整节。**
`indexing.py:433` 的 `_blocks` 按 `^#{1,6}` 切分，标题之间的全部内容算一块，没有大小上限。检索出来后 `_bounded_excerpt`（`sessions.py:1920`）截断到 800 字符（`MAX_RETRIEVAL_BLOCK_CHARS`）。也就是说：一个 3000 字的「Unit 1 语法」小节，无论命中哪里，都只有开头 800 字进入 prompt，后面的静默丢失。

**3. 检索路径的硬上限对汇总任务是结构性的。**
`sessions.py:55-58`：`MAX_RETRIEVAL_EVIDENCES = 8`、`MAX_RETRIEVAL_CONTEXT_CHARS = 4_000`。一个单元的知识点分散在几十上百个块里，8 条证据 / 4000 字符不可能覆盖。这与 RAGFlow 默认 `top_n=6` 是同一类错误。

**4. 知识整理路径干脆绕过了检索，改成按目录枚举。**
`sessions.py:2030` 的 `_knowledge_organization_sections`（由 `:958` 调用）遍历 `current_documents` 的每个块，用 `relative_path.rpartition("/")[0]`（`:2083`）按**目录**分组成 section。这个方向（枚举而非 top-k）是对的，但分组维度错了：目录不等于单元。如果教材、教辅、清单分属不同目录，同一个 Unit 1 的内容会被切进三个互不相干的 section，各自独立调 LLM，跨资料的去重合并根本没有发生。而 `_process_completeness_items`（`sessions.py:1532`）的去重是 `excerpt` 字符串完全相等才判 duplicate——教材说的 "be 动词的用法" 和教辅说的 "be 动词三种形式" 永远不会被识别为重复。

**5. 解析侧已经产出的结构化信息，在索引时被丢掉了。**
这是最可惜的一处。`workers/converters/launcher.py` 产出的 `DocumentGraph` 带有：typed blocks（`_DOCUMENT_BLOCK_KINDS` 见 `domain/evidence.py:10`，共 heading/paragraph/list/table/formula/image/caption/code/unresolved 九种）、`reading_order`、`confidence`、`PdfRegionLocator`（page + bbox）、`DocxOoxmlLocator`、以及每块的 `retrieval_projection`（`evidence.py:346`，专为检索准备的纯文本投影）。`domain/derived_notes.py:46` 的 `render_document_graph` 会把它们收集进 `RenderedDocumentGraph.retrieval_blocks`（`derived_notes.py:42` 声明，`:59` 填充）。

但 `IndexedDocument`（`domain/indexing.py`）只存 `blocks: tuple[IndexBlock, ...]`，`IndexBlock` 只有 `sequence / location / text` 三个字段。索引阶段（`indexing.py:249` 的 `_document_from_markdown`）是**重新读 Markdown 文本再按标题正则切**——block kind、页码 bbox、reading_order、confidence、retrieval_projection 全部没有进入索引。`retrieval_projection` 在 `application/` + `api/` + `adapters/` 全层里只有四处出现，且全部属于同一条人工纠正链路（`ingest.py:1407` 参数、`:1426` 赋值、`api/main.py:323` 请求字段、`:2719` 转发）——**没有任何检索侧消费者**。

结论：改造的第一步不是加向量，是**先把已经解析出来的结构化信息接进索引**。

---

## 二 对标：ima 准、RAGFlow 不准的可归因原因

**RAGFlow 的问题有代码级证据。** 默认链路是 ES `query_string`（BM25，`minimum_should_match` 30%）+ KNN 向量，`weighted_sum` 融合 term:vector = 0.7:0.3，`similarity_threshold` 0.2，`top_k` 1024 候选但只有 `top_n`=6 个 chunk 进 LLM，rerank 默认为空。关键缺陷在 `rag/utils/es_conn.py`：含 `query_string` 的 `bool_query` 被同时当作 KNN 的 `filter` 传入——**向量召回被关键词命中硬性闸死**，BM25 漏掉的内容语义检索救不回来。对应 issue #12277（bug 标签，附 ES 请求体复现，中文「代理/智能体」只有删掉 knn 里的 `query_string` 才召回；维护者回复 "It's trade-off between recall and precision."）。此外 `_rerank_window` 把候选切成 ~64 一块再排，解释了 issue #6140（page_size=10 失败、100 成功）；RAPTOR / GraphRAG / parent-child / TOC 增强全部默认关闭。

对你的场景最致命的仍是 `top_n`=6。这与本仓库 `MAX_RETRIEVAL_EVIDENCES = 8` 是同一个病。

**ima 的内部机制查不到。** ima.qq.com 只暴露产品层（知识库、`@` 引用、上传），无任何检索架构披露。腾讯云 ADP/LKE 文档里有「意图识别节点」「知识问答节点」，但那是另一个产品，只能算同厂旁证。所以「ima 更准」的体感无法直接归因到某个机制——但从你的使用描述（能准确定位到册次/单元）反推，最可能的差异是**它把文档目录结构当作一等检索维度**，而不是只做片段相似度。这一点方案里会显式实现。

**NotebookLM 并没有放弃检索。** 官方 FAQ 明确写 "NotebookLM retrieves the most relevant information based on your question, then builds a response from it"。流行说法「它全量塞长上下文所以准」没有一手证据。它真正做到的是引用锚定回原文（inline citation）+ source 为静态副本。

**Open Notebook 的可借鉴点是分块，不是检索。** 它用 SurrealDB，`fn::text_search`（BM25）与 `fn::vector_search` 是两个独立函数**不做融合**，需用户二选一，文档甚至建议手动取并集；分析器仅英文 `snowball(english)`，中文不可用。但 `utils/chunking.py` 的 `MarkdownHeaderTextSplitter(strip_headers=False)` 按 `#/##/###` 切且**保留标题在块内**，正对你手写的单元知识清单。它的 `source_insight`（transformation 产出，含 "Dense Summary" 与 "Table of Contents"）会同时被 embedding 和 BM25 索引——这是一层可借鉴的摘要索引，但只有一层，非 RAPTOR 递归。

**跨项目的可靠结论。** Anthropic contextual retrieval 实测：给每个 chunk 前置文档上下文后失败率 −49%，配 rerank −67%；embeddings + BM25 优于纯向量；top-20 优于 top-10/top-5；范围 <200k token 时官方直接建议全量入 prompt、不需要 RAG。RAPTOR 论文点明 "most existing methods retrieve only short contiguous chunks... limiting holistic understanding"，QuALITY +20% 绝对分。

所以准确的说法不是「汇总任务要放弃 top-k」，而是：**相似度不该决定"范围"，只能决定"排序"。范围由元数据过滤决定。**

---

## 三 技术栈选型（含推荐）

你让我推荐效果最好的方案。这里给结论和理由，以及我实测的数据。

### 检索栈：FTS5（BM25）+ 向量列存 BLOB + numpy 精确 KNN

**推荐这个，而不是 sqlite-vec 或 LanceDB。** 理由是「效果最好」在你这个规模下等价于「精确检索」，而精确检索恰好是最简单的实现。

我在本会话环境（Linux 沙箱，numpy 2.x）实测 numpy 精确 KNN：60000 块 × 1024 维 float32、L2 归一化，单次全量点积 + `argpartition` 取 top-50，**7.5–11.2 ms**（多次运行区间），常驻 246 MB。该样本中 float16 存储降到 123 MB，top-50 与 float32 为 50/50 重合；这只能证明 float16 值得继续评测，不能证明对所有资料和模型都无损。

一个必须说清的实测细节：float16 矩阵在查询时 `astype(np.float32)` 全量转换要 116 ms，比搜索本身贵一个数量级。因此 V1 先统一使用 float32；后续若 Windows 实机评测证明召回无损，float16 只能用于落盘 BLOB，进程内仍缓存解码后的 float32 矩阵，不能每次查询现转。

你的实际规模：一册教材 + 教辅 + 清单 + 题库，按 300 字/块估算约 3000–20000 块。按上述实测线性外推，精确 KNN 在 1–4 ms 量级，常驻内存 12–82 MB。sqlite-vec 的 ANN、LanceDB 的 IVF/HNSW 都是**近似**检索——在这个规模引入它们，是用召回率换取你根本不需要的速度。这是纯粹的负收益。

另外两个理由：sqlite-vec 需要 `load_extension` 加载 `.dll`。沙箱里 `sqlite3.Connection.enable_load_extension` 存在，但 python.org 官方 Windows 安装包历史上是**编译时禁用扩展加载**的（cpython issue #95656 即在请求打开）。你的运行时是 uv 托管的 `cpython-3.11-windows-x86_64-none`（`apps/service/.venv/pyvenv.cfg` 的 `home` 指向 `AppData\Roaming\uv\python\...`，uv 0.11.29），属 python-build-standalone 构建，行为与官方包不同——**必须在你的机器上实测确认，方案不依赖它**。BLOB + numpy 完全绕开这个不确定性。

numpy 的依赖状态要说明清楚：`apps/service/.venv/Lib/site-packages` 里已有 numpy 2.3.5，但它是 PaddleOCR 侧车带入的**间接依赖**，不在 `pyproject.toml` 的顶层 pin 里，也不在 `uv.lock` 的 26 个包里。检索层要依赖它，就必须按仓库规范把 `numpy==2.3.5` 加进 `pyproject.toml` 并重新锁定——不能靠"反正已经装了"。

FTS5 我也实测了：沙箱 SQLite 3.37.2（比 `scripts/preflight.mjs:7` 要求的 `3.45.1` 更旧，所以实机能力只会更强），`unicode61`、`porter unicode61`、`trigram` 三种 tokenizer 全部可建表；`bm25()` 支持按列加权（`bm25(f, 1.0, 1.0, 10.0)` 给 heading 列 10 倍权重）；跨列布尔查询 `cjk:"第一 单元" OR heading:Unit1` 正常返回并按加权分排序——第 4.4 节的双列设计与第 6.3 节的 heading 加权都是跑通过的，不是设想。

**中文分词方案：双列 FTS5。** 一列 `porter unicode61` 处理英文（做词干还原，`run/runs/running` 归一，这对英语教材至关重要）；一列存**预分词的中文**——不用 trigram（噪声大），而是在写入时用一个轻量正向最大匹配分词器切好、空格分隔后存入。词典来源：知识库自身的标题层级、Markdown 原生 `tags`、`links` 和查询词典。这样「第一单元」是一个 token 而不是 3 个 bigram。词典不覆盖的部分退化为 bigram，作为兜底。

如果后续规模真的上到十万块以上，替换点是隔离的：只有 `VectorStore.search()` 一个方法需要改成 sqlite-vec 或 hnswlib，检索融合层不动。

### Embedding：云端 Provider，但必须解决三个工程问题

你已经选定云端。`adapters/openai_compatible_provider.py` 已有 `probe_embedding`（POST `/embeddings`），`domain/providers.py` 的 `MODEL_TYPES` 现区分 `chat`、`embedding` 与独立 `rerank`，`ProviderService.set_default("embedding", ...)` 也在（`application/providers.py`）。缺的是真正取向量的方法和三件事：

**(a) 批量与缓存。** 客户端要加 `create_embeddings(endpoint, secret, model_id, inputs: tuple[str, ...])`。先计算绑定 Provider 配置与模型维度的 `embedding_profile_fingerprint`，缓存键 = `sha256(profile_fingerprint + "\x00" + normalized_text)`，存 `embedding_cache` 表。重新索引同一份资料时零调用。

**(b) 模型指纹绑定。** 向量必须记录 `embedding_profile_fingerprint`、`embedding_model_id` 与 `embedding_dimension`。endpoint、Provider 配置 revision、模型名或维度任一变化，旧向量都不可比较，必须按 vault 重建，不能混用。这一点要写进 `IndexHealth`：`domain/indexing.py:104` 已经声明了 `semantic_status` 字段，但 `adapters/sqlite_index_repository.py:346` 的 `health()` 把它硬编码成 `"unavailable"`——这个已存在的空壳字段正是 embedding 覆盖率的归宿，改造时填真值即可，无需改 API 契约。

**(c) 默认出网与排除规则（2026-07-27 已确认）。** 已验证的 HTTPS Provider 默认允许出网，不再创建、确认或重验逐任务授权。Embedding、会话生成和启用后的 rerank 可直接执行；旧 SQLite 中的历史授权记录保留但运行时不读取或写入。

默认允许不放宽内容边界：每个候选仍须通过 `preview(..., "outbound")`。`never-send-cloud` 匹配源不得外发；`do-not-index` 与 `completely-ignore` 继续阻止对应处理。HTTPS、已验证 Provider/model、内容哈希、向量维度和 Provider 响应校验同样保持失败关闭。

导入提交必须在当前块的向量完整持久化后才可完成。若 Provider、内容、规则或向量校验失败，恢复本次提交的文件与索引并返回可重试状态，不保留部分完成提交。

### 范围元数据：仅保留确定性结构推断

见第五节。检索范围只使用路径、文件名和标题层级推断 `subject / grade_volume / unit_no / material_type`。LLM 元数据抽取、候选审核和私有标签目录已退役，不再参与导入、索引或检索。

---

## 四 数据模型改造

### 4.1 IndexBlock 扩展

`domain/indexing.py:19` 的 `IndexBlock`（现在只有 `sequence` / `location` / `text`）扩到承载解析侧已有的结构：

```python
@dataclass(frozen=True)
class IndexBlock:
    sequence: int
    location: str
    text: str
    block_content_sha256: str = ""       # 规范化块正文；用于精确去重
    # 结构字段：派生文档来自耐久 graph 投影；原生 Markdown 确定性推断
    block_kind: str = "paragraph"        # heading/paragraph/list/table/formula/image
    heading_path: tuple[str, ...] = ()   # ("Unit 1 Making new friends", "Grammar Focus")
    heading_level: int | None = None
    source_locators: tuple[DocumentLocator, ...] = ()
    graph_block_id: str | None = None    # 回连 DocumentGraph，引用可核验
    reading_order: int | None = None
    confidence: float | None = None
    retrieval_text: str = ""             # DocumentBlock.retrieval_projection
    contextual_prefix: str = ""          # 见 4.3
    token_estimate: int = 0
```

`heading_path` 是这次改造里性价比最高的一个字段。有了它，「第一单元」的定位从模糊匹配变成前缀匹配。`source_locators` 保留 PDF、DOCX 和 source-scope 的完整判别类型；`page / bbox` 由 locator 派生，避免只为 PDF 再存一份重复数据。

### 4.2 索引不再重新解析 Markdown

现在 `IndexingService._document_from_markdown`（`indexing.py:249`，第 294 行调用 `_blocks(markdown)`）重读 Markdown 按正则切块，是结构信息丢失的根因。改为：

- **派生文档**（`document_kind == "derived"`）：从 frontmatter 的 `platform_provenance` 拿到 `graph_id / graph_revision`，从索引侧耐久 graph 投影按 `graph_block_id` 读取 typed blocks，直接投影成 `IndexBlock`。`retrieval_projection` 直接进 `retrieval_text`。提交派生笔记时必须先原子写入耐久投影；不能在重建时跨库读取 import task 数据，因为删除任务会删除 graph revisions。
- **原生 Markdown**（个人整理的清单）：保留 Markdown 切分，但改成**保留标题的层级栈**（借鉴 Open Notebook 的 `strip_headers=False`），并加大小上限（见 4.3）。

### 4.3 分块策略

三条规则，针对你的三类内容分别定：

**结构优先，大小兜底。** 目标块 300–500 字（中文字符），硬上限 800。超出的按语义边界二次切分：列表按项、表格按行组、段落按句。这解决了「3000 字小节只有前 800 字进 prompt」的问题。

**表格与列表不打散。** 词汇表、短语表、语法变化表是你的核心资产。一个表格若整体 ≤800 字作为一块；超出时按行分组，**每组重复表头**。词汇列表按 8–12 项一组切，每组带上所属标题。

**Contextual prefix（Anthropic 那套，实测失败率 −49%）。** 每个块在 embedding 和 BM25 索引时，前置一段由结构确定性生成的上下文（不调 LLM，零成本零幻觉）：

```
[七年级上册英语 · 人教版教材 · Unit 1 Making new friends · Grammar Focus]
be 动词在一般现在时中有三种形式：am / is / are …
```

这段前缀只用于**索引与检索**，不进入给用户看的 excerpt，也不改变 `content_sha256`——它仍是 Markdown 原文哈希，`IndexedDocument.__post_init__`（`domain/indexing.py:54`）里 `_validate_sha256(self.content_sha256, ...)`、派生文档的 source 身份校验、以及 `if not self.blocks` 的非空约束全部不受影响。前缀存在 `index_block_fts` 的加工文本，并参与 `retrieval_input_sha256`；它与文档哈希、块正文哈希是三套独立标识。它让一个孤立的「三种形式：am/is/are」块也能被「七上第一单元 语法」命中。

### 4.4 新增表

在现有 `index.sqlite`（`SqliteIndexRepository`）里加，不引入第二个存储。结构字段扩在现有 `index_blocks`，语义字段单独进入 `index_block_meta`：

```sql
-- 显式记录迁移；每个迁移必须有旧库升级测试
CREATE TABLE index_repository_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- 现有 index_blocks 通过幂等迁移增加结构列：
-- block_content_sha256, block_kind, heading_path_json, heading_level,
-- source_locators_json, graph_block_id, reading_order, confidence,
-- retrieval_text, contextual_prefix, token_estimate

-- 块级确定性范围元数据；不重复保存 index_blocks 的结构字段
CREATE TABLE index_block_meta (
    document_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    subject TEXT, grade_volume TEXT, unit_no INTEGER,
    material_type TEXT,          -- textbook/workbook/personal-note/exercise
    meta_origin TEXT NOT NULL,   -- rule
    meta_confidence REAL,
    meta_status TEXT NOT NULL,   -- accepted / recoverable
    PRIMARY KEY (document_id, sequence),
    FOREIGN KEY (document_id) REFERENCES index_documents(document_id) ON DELETE CASCADE
);
CREATE INDEX idx_block_meta_locator
    ON index_block_meta(subject, grade_volume, unit_no, material_type);

-- 向量（BLOB + 模型指纹）
CREATE TABLE index_block_vectors (
    document_id TEXT NOT NULL, sequence INTEGER NOT NULL,
    embedding_profile_fingerprint TEXT NOT NULL,
    embedding_model_id TEXT NOT NULL, dimension INTEGER NOT NULL,
    vector BLOB NOT NULL,            -- float32 little-endian, L2 归一化
    retrieval_input_sha256 TEXT NOT NULL, -- 前缀+检索文本的哈希，用于失效判定
    created_at TEXT NOT NULL,
    PRIMARY KEY (document_id, sequence, embedding_profile_fingerprint),
    FOREIGN KEY (document_id) REFERENCES index_documents(document_id) ON DELETE CASCADE
);

-- Embedding 缓存（跨 vault、跨重建复用）
CREATE TABLE embedding_cache (
    cache_key TEXT PRIMARY KEY,      -- sha256(profile_fingerprint || 0x00 || normalized_text)
    embedding_profile_fingerprint TEXT NOT NULL,
    embedding_model_id TEXT NOT NULL, dimension INTEGER NOT NULL,
    vector BLOB NOT NULL, created_at TEXT NOT NULL
);

-- BM25：中英双列
CREATE VIRTUAL TABLE index_block_fts USING fts5(
    en_text,                          -- 英文原文（porter 词干还原）
    cjk_text,                         -- 预分词后空格分隔的中文
    heading_text,                     -- 标题路径，检索时加权
    tag_text,
    tokenize = 'porter unicode61 remove_diacritics 2',
    prefix = '2 3'
);
CREATE TABLE index_block_fts_map (
    rowid INTEGER PRIMARY KEY,
    document_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    UNIQUE(document_id, sequence),
    FOREIGN KEY (document_id) REFERENCES index_documents(document_id) ON DELETE CASCADE
);
```

FTS5 用外部 map 表而非 `content=` 外部内容表，是因为要索引的是「前缀 + 分词后」的加工文本，不等于任何一张表的原始列。检索必须通过 map 回连 `index_documents.is_current = 1`；失效、重建和失败回滚时，FTS、向量与结构行必须在同一事务中保持一致。

### 4.5 不维护摘要投影

单元卡片及其 FTS、向量投影已退役。粗粒度与枚举型问题直接基于确定性范围过滤后的原始块汇总，证据始终回到原始资料块。

### 4.6 端口契约要补检索方法

`ports/index_repository.py` 现在只有 11 个方法（`enqueue` / `next_pending` / `save_job` / `retry_failed` / `recover_running` / `current_documents` / `documents` / `save_document` / `invalidate_current_path` / `resolve_pending_association` / `health`）——**没有任何检索方法**。这是为什么 `sessions.py` 只能 `current_documents(vault_id)` 全量拉回内存再自己打分。

要补三个（写在 port 上，`SqliteIndexRepository` 实现）：

```python
def search_lexical(self, vault_id: str, query: LexicalQuery) -> list[BlockHit]: ...
def search_vector(self, vault_id: str, vector: bytes, filters: BlockFilter, limit: int) -> list[BlockHit]: ...
def filter_blocks(self, vault_id: str, filters: BlockFilter) -> list[IndexBlockRef]: ...
```

`filter_blocks` 是枚举型任务的地基（第 6.2 节第 1 步），它只做元数据 SQL 过滤、不算分。表结构迁移沿用 `save_document` 里已有的 ALTER 轻量迁移写法。

---

## 五 领域元数据：怎么才能定位到「七年级上册第一单元」

这是精准命中的关键，只使用两层确定性规则。

### 第一层：路径与文件名规则（确定性，零成本）

从 `relative_path` 抽取。约定一套推荐目录结构，同时容忍偏离：

```
英语/七年级上册/教材-人教版/Unit01-Making-new-friends.md
英语/七年级上册/教辅-53天天练/Unit01.md
英语/七年级上册/我的清单/U1-知识点.md
```

匹配模式要覆盖中文习惯写法：`七(年级)?[上下](册)?`、`7A`、`Unit\s*0?1`、`第一单元`、`U1`、`Lesson\s*\d+`。`material_type` 从路径段推断（教材/教辅/清单/笔记/练习/试卷）。这一层给出 `subject`、`grade_volume`、`unit_no`、`material_type`，置信度 0.95，`meta_origin = "rule"`。

### 第二层：标题层级推断（确定性）

`heading_path` 里出现 `Unit 1` / `第一单元` 时覆盖或补全路径推断。这一层能处理**整册一个文件**的情况——教材 PDF 转出来常常是一个大 Markdown，路径里没有单元信息，全靠标题。此时同一文档的不同块会有不同的 `unit_no`，这正是需要块级元数据而非文档级元数据的原因。

标题层只补全确定性范围字段，不产生需要人工审核的概念或标签。无法由路径和标题确定范围时保持 recoverable，不调用 LLM 猜测。

### 元数据不足时的行为

如果一个知识库既没有可解析的路径结构也没有单元标题，`unit_no` 为空——此时「第一单元」无法定位。**必须显式告诉用户「无法确定单元边界」并让他选范围，而不是退化成模糊检索然后假装准确。** 这与仓库现有的 `no-evidence` / `recoverable` 语义一致（`sessions.py:1661` 的 `no-evidence`，`:1498` 的 `recoverable`）。

---

## 六 检索：意图路由 + 混合检索 + 去重召回

### 6.1 意图路由

现有 `_resolve_task_intent`（`sessions.py:2169`，由 `:921` 调用）已经在按关键词分四类（completeness / deep-creation / knowledge-organization / source-lookup），思路对，但判据太粗且没有解析出**范围**。改造成两阶段：

```
用户输入 → 查询理解 → (intent, scope_filter, query_terms)
```

**查询理解**做三件事：
1. 抽取定位符：subject、grade_volume、unit_no、material_type。「七年级上册第一单元」→ `{grade_volume: "七上", unit_no: 1}`；「第一单元的语法」中的“语法”保留为查询词，不升级为治理元数据。用与第五节同一套范围规则，保证查询侧与索引侧的归一化一致——这一点很关键，两侧用不同的归一化是检索不准的经典来源。
2. 判定意图。**枚举型**（汇总/整理/清单/全部/所有/知识点/默写/复习）vs **点查型**（某个具体问题）。判据除关键词外，加一条结构判据：如果查询解析出了明确的 `unit_no` 且没有具体疑问词，倾向枚举型。
3. 决定预算：枚举型给大预算（下节），点查型给 top-k。

低置信度时不要猜。把解析结果作为**可编辑的范围预览**呈现给用户确认——仓库已有这个交互（`preview_task`（`sessions.py:278`）/ `create_task`（`:287`）两步），塞进去成本很低，收益是用户能看到「我将检索：七上英语 / Unit 1 / 全部资料类型 / 共 137 个内容块」。这也是 ima 那种「感觉很准」体验的一部分：**范围可见**。

### 6.2 枚举型：全量取回 + 分桶 + 去重 + 分层生成

「第一单元知识点汇总」走这条。**不做相似度筛选。**

```
1. 元数据过滤（SQL，确定性）
   WHERE subject=? AND grade_volume=? AND unit_no=?
   → 该单元全部块（教材 + 教辅 + 清单 + 题库），通常 80–300 块

2. 按标题层级和原生标签词项组织原始块
   grammar / vocabulary / phrase 等词项只辅助生成组织，不成为审核目录

3. 桶内去重合并（三级，从便宜到贵）
   a. block_content_sha256 完全相同 → 直接合并
   b. 标题词归一或向量相似度达到评测阈值 → 只标为候选重复
   合并时保留全部来源引用，不丢证据。教材版本 + 教辅版本 + 你的清单版本
   合成一个知识点，三个出处。

4. 分层生成（map-reduce）
   每桶按 token 预算切批（建议 8k–12k 输入/批）→ 逐批生成结构化条目
   → 桶级合并去重 → 全局组装成汇总
   每个条目携带 [证据 n] 引用，锚回 document/sequence/page
```

第 3 步 c 是向量在枚举型任务里的真正用途——不是用来"找"内容，是用来生成待合并候选。真正合并仍由结构化条目归一、证据兼容性和生成阶段共同确认，阈值由 golden set 校准。

预算上限要改：`MAX_RETRIEVAL_EVIDENCES = 8` / `MAX_RETRIEVAL_CONTEXT_CHARS = 4_000` 对枚举型完全不适用。枚举型改为按所选模型的 context window、输出预留和批次数上限计算 token 预算，而不是硬性条数或固定字符截断。超预算时**不静默丢弃**——落成 `completed-with-confirmed-gaps` 并列出未覆盖的块，仓库已有这个状态（`sessions.py:1505`，且 `:1525` 已把它与 `completed` 同等视为成功）。

### 6.3 点查型：混合检索 + RRF + rerank

```
1. 元数据软过滤：解析出定位符则限定候选，否则全库
2. 三路并行召回，各取 top-50
   - BM25(cjk_text + en_text)，heading_text 列加权（bm25(f, w...) 已验证可用）
   - 向量 KNN（numpy 精确，实测 60k×1024 只要 11ms）
   - heading_path 前缀精确匹配（结构通道，这一路是 ima 式定位的关键）
3. RRF 融合：score = Σ 1/(60 + rank_i)
   用 RRF 而不是 weighted_sum，因为不需要跨通道分数归一化——
   RAGFlow 的 weighted_sum 0.7:0.3 是需要调参的，RRF 不需要
4. rerank（可选）：交叉编码器重排 top-20 → top-8
5. 邻域扩展：命中块的前后各 1 块补入（表格/列表带上表头行）
```

**必须避开 RAGFlow 的那个坑**：三路是**并行独立召回后融合**，绝不能让 BM25 的结果去 filter 向量召回。这是 issue #12277 的根因，也是"BM25 漏了语义救不回来"的来源。代码上要有明确注释标记这一点，防止后续被"优化"成 filter。

rerank 放在最后且只对点查型生效。枚举型绝不 rerank——rerank 会系统性丢掉长尾词汇/短语条目，这正是你现在遇到的问题。

### 6.4 引用可核验

现有 `SessionRetrievalEvidence` 已带 `relative_path / content_sha256 / source_id / source_sha256 / source_path / heading / location / page`，`_evidence_location`（`sessions.py:1927`，被 `:1618`、`:2005`、`:2085` 三处调用）从 location 字符串正则抠 heading 和 page。改造后 page/bbox 是 `IndexBlock` 的一等字段，不再需要正则解析。加上 `graph_block_id` 后，引用可以精确锚回 `DocumentGraph` 的某个 block 及其 `PdfRegionLocator`（page + bbox），支持"点击引用跳到 PDF 原文那一块"。这是仓库现有 evidence 架构本来就想做到的事，只是索引层没把 id 传过来。

---

## 七 落地顺序

按「每阶段独立可验证、可回退」拆分。阶段编号与 `progress/tasks/retrieval.json` 一致。

**阶段 RET-00：决策与基线**

产物：审核后的方案、真实 vault 目录样本、20–30 条 golden queries、块级期望证据、当前实现的 recall@k / precision@k / scope coverage / duplicate merge precision、Windows FTS5 与 numpy smoke。

退出条件：评测数据可重复运行；三个产品决策有记录；基线结果写入进度日志。没有基线，不进入 schema 开发。

**阶段 RET-01：耐久结构投影**

产物：索引侧 graph projection 表与读取端口；派生笔记提交时原子复制选定 graph 投影；删除 import task 后仍可按 `graph_id / graph_revision` 重建索引；原生 Markdown 继续走旧路径。

退出条件：提交、任务删除、索引重建、引用 locator 四条集成测试通过。该阶段不改检索排序和 UI。

**阶段 RET-02：富块模型与兼容迁移**

产物：`IndexBlock` 结构字段、`block_content_sha256`、显式 migration ID、旧库升级、结构双写与回填、旧读/新读对比开关。

退出条件：旧数据库无损升级；新旧读取结果在正文和文档身份上等价；关闭新读路径可回退。该阶段不接 FTS、Embedding 或 LLM 元数据。

**阶段 RET-03：确定性分块、元数据与范围过滤**

产物：DocumentGraph 投影分块、原生 Markdown 标题栈与大小兜底、contextual prefix、路径/标题归一化、`filter_blocks`。

退出条件：`heading_path / block_kind / locator` 覆盖率达到基线目标；golden set 的单元 scope coverage 可解释且无跨单元泄漏。目录正则必须来自真实样本，不能只覆盖文档示例。

**阶段 RET-04：FTS5 词法检索**

产物：FTS migration、英文列、中文预分词列、heading 加权、current/stale 同步、`search_lexical`，并以 feature flag 接入点查路径。

退出条件：旧检索与 FTS A/B 报告完成；中文 precision 与 recall 不低于阶段目标；重建、失效、失败回滚后 FTS 不出现孤儿行。达到条件后再移除手写 `_retrieval_score`。

**阶段 RET-05：查询范围与枚举型汇总**

产物：共享 scope parser、范围预览 API、枚举型全量过滤、按知识类型分桶、原子条目去重、按模型窗口计算的 map-reduce 预算、confirmed gaps 明细。

退出条件：「第一单元知识点汇总」达到 golden coverage 目标；跨资料合并不丢引用；无 scope 时明确返回 recoverable，不降级成伪精确检索。前端可编辑范围预览作为独立任务，在后端合同稳定后接入。

**阶段 RET-06：Embedding 与混合检索**

前置闸门：已确认默认允许已验证 HTTPS Provider 出网，同时保留排除规则；Provider 指纹合同完成；Windows float32 benchmark 通过。

产物：批量 Embedding 客户端、缓存、向量持久化、按 vault/profile 的 float32 内存矩阵与失效机制、`search_vector`、三路独立召回和 RRF。

退出条件：语义改写 recall@8 达标；BM25 漏召回时向量通道能独立救回；缓存与模型切换测试通过；`semantic_status` 反映真实覆盖率。

**阶段 RET-07：已退役的增强实验**

LLM 元数据抽取与审核、单元卡片已经退役，不再作为生产检索能力。历史评测只保留为决策记录；rerank 由 RET-08 和 RET-09 独立管理。

**阶段 RET-08：受控 rerank 接入**

产物：source-lookup 的逐任务 rerank 授权快照、内容与范围重验、默认关闭开关和本地 RRF 回退。

退出条件：未确认、快照变化、策略阻断或 Provider 失败时均不发送候选正文；受控启用只影响 RRF 后的点查排序，并保留原始证据。

**阶段 RET-09：真实 Provider 测量**

产物：独立的原生 rerank Provider/model 设置，以及只对固定路径和内容哈希都校验通过的版本化、不可逆脱敏 fixture 进行受限真实调用的 CLI；原生合同固定为 `POST /rerank`、`{model, query, documents}` 与完整 `index/relevance_score` 响应。报告只写入被忽略的 `output/live-rerank/`，记录不含正文的延迟和质量结果。

退出条件：调用前必须显式提供已验证的 HTTPS rerank Provider/model、最多两条请求和出网确认；HTTPS 检查发生在读取凭据前。用户已决定不做价格或费用预算，报告明确标记为未计算，仍不启用默认开关；未获得质量复核时默认开关保持关闭。

每阶段都要按仓库现有规范补测试。`npm run test`（`package.json:21`）串联 `unit`（前端单测 + `pytest tests/unit`）→ `integration`（先 build 再 `pytest tests/integration`）→ `browser-test`（build + Playwright，`browser-tests/playwright.config.mjs` 里 `workers: 1`、baseURL 固定 `http://127.0.0.1:6240`）。检索层改动会同时触达三层，尤其是集成测试里的索引与任务快照断言。

---

## 八 决策记录与默认建议

**D-001 Graph 生命周期（技术默认：索引侧耐久投影）。** 不让索引依赖 import task 数据库；提交时复制检索所需的不可变投影。该建议解除 RET-01 的架构歧义，开发前只需确认保留期限与删除语义。

**D-002 出网粒度（2026-08-12 已更新）。** 已验证的 HTTPS Provider 默认允许 Embedding、会话生成和启用后的 rerank 出网，不再创建、确认或重验逐任务授权。点查与生成仅发送本次检索到且通过 outbound policy 的证据；`never-send-cloud` 一律不外发，`do-not-index` 与 `completely-ignore` 继续生效。source-lookup rerank 仍是独立 operation，默认开关保持关闭；启用后仅向独立、已验证的 HTTPS rerank 模型发送通过规则过滤的查询和候选块文本，并继续执行内容哈希、响应格式、并发和失败回退校验。LLM 元数据和单元卡片运行时已删除；旧 SQLite 历史记录不删除，也不再读取或写入。

**D-003 目录规则（默认：适配真实资料，不强制迁移目录）。** RET-00 收集真实目录样本，规则由测试夹具驱动；推荐目录只作为新资料建议，不把重命名现有 vault 作为检索改造前置条件。

**D-004 Schema 兼容（技术默认：双写、回填、对比、切读）。** 所有新表和新列通过显式迁移增加，保留旧读取回退到 RET-04 验收完成；禁止破坏性重建用户索引库。

**D-005 向量精度（技术默认：float32 V1）。** float16 作为后续独立性能任务，只有 Windows 实机在 golden set 上证明 top-k 重合率和去重候选质量不回退后才启用。

**D-006 PDF 结构化管线（2026-08-17 已确认）。** PDF 的 OCR 来源与 DocumentGraph 到 Markdown 的结构化方式必须拆分为两个独立、任务级冻结的选择。OCR 来源仍由“在线解析”控制：关闭时使用本机 PaddleOCR-VL，打开并选择已验证 Provider 时上传 PDF 原件与文件名并使用官方在线 OCR。结构化方式新增 `markdown_pipeline`，仅适用于 PDF：

| OCR 来源 | `ai`（AI 结构化） | `local`（本地结构化） |
| --- | --- | --- |
| 本机 OCR | 将选定 DocumentGraph 的确定性 Markdown 发送给既有 Markdown Provider | 直接以 `render_document_graph()` 确定性渲染 |
| 在线 OCR | 将选定 DocumentGraph 的确定性 Markdown 发送给既有 Markdown Provider | 直接以 `render_document_graph()` 确定性渲染 |

`markdown_pipeline` 属于导入任务，不属于 Online OCR Provider 选择；新 PDF 任务默认 `ai`，重试、恢复与设置变更均不得改写已冻结的模式。AI 模式在派生前使用既有 Markdown outbound policy 校验，策略或 Provider 失败时进入可恢复失败并仅提供重新生成笔记，不自动回退为本地渲染。本地模式不得调用 Markdown Provider。在线 OCR 继续独立校验原件上传策略、Provider 与凭据；因此本机 OCR + AI 结构化只外发派生 Markdown，不上传 PDF 原件。

该开关不改变原生 Markdown、DOCX、表格或其他格式的既有处理。历史任务若未持久化该字段，按历史行为推断：含在线 OCR 选择的 PDF 视为 `ai`，没有在线 OCR 选择的 PDF 视为 `local`，以避免重试时扩大外发范围。导入界面使用独立的分段控件并记住上次选择，任务详情展示安全的冻结模式；在线 OCR 任务另展示 Provider 与状态。首步仅支持手动分别创建任务做对照，不自动双跑、并排 diff 或质量评分。

---

**D-007 本地图结构归一化（2026-08-17 已确认）。** 本地结构化的规范输入是带 provenance 的 DocumentGraph，不是 PaddleOCR 原始 JSON，也不是先生成的 Markdown。PaddleOCR 页面 JSON 只在 adapter/私有证据层解析；随后以版本化、零出网的确定性规则修复可信标题、几何阅读顺序、连续列表、图注、断行段落和可确认的重复页边噪音。不能机械确认的内容保留原文并记录稳定问题，不使用 LLM 补全或改写正文。合并、拆分和类型变更的块保留全部原始 block identity、locator 与 evidence ref；规则版本随 PDF 任务/图谱冻结，历史任务不因代码升级自动改变。缺少代表性脱敏真实页面时，只能以 fixture 验证协议保真，不能据此宣称语义质量提升。

## 附：证据强度说明

代码级已核实（本仓库，行号在撰写时逐条 grep 复核过）：第一节全部条目；第三节关于 `probe_embedding` / `MODEL_TYPES` / `MAX_SCOPE_COUNT` 及其 `enforce_count` 例外 / `semantic_status` 空壳的断言；第四节 `IndexBlock` 与 `IndexedDocument` 的现有字段与校验；4.6 节的 port 方法清单；第五节 `_DOMAIN_RULES` 与 `required-check` 门槛；第六节引用的全部函数、常量与状态字面量；第七节的 npm 脚本链路。行号会随后续提交漂移，函数名与常量名是更稳的锚点。

**Linux 沙箱实测（非你的 Windows 主机，需在实机复测）**：numpy 精确 KNN 60000×1024 float32 取 top-50 为 7.5–11.2 ms / 246 MB；样本中的 float16 存储为 123 MB 且 top-50 与 float32 完全一致，但全量 `astype` 转换 116 ms，因此只能作为后续候选优化；FTS5 三种 tokenizer 可建表、`bm25()` 列加权与跨列布尔查询跑通；`enable_load_extension` 属性存在。沙箱 SQLite 为 3.37.2，低于仓库要求的 3.45.1。

外部代码/文档级：RAGFlow 的默认参数与 `es_conn.py` 的 KNN filter 缺陷（issue #12277 附复现）；Open Notebook 的 SurrealDB 检索实现与英文-only 分析器；Anthropic contextual retrieval 的量化结果；RAPTOR 论文结论。

无法证实：腾讯 ima 的任何内部机制（公开信息只到产品层）；NotebookLM 是否有分层摘要。方案里凡涉及 ima 的设计推断，均标注为从你的使用体验反推，不是它的公开实现。
