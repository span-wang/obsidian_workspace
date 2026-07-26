# AGENT 协作与代码规范

## 1. 适用范围

本文件适用于整个仓库。目标是让每次开发都有明确任务、边界、验证和进度记录，同时保持当前 FastAPI、React、SQLite 和本机 loopback 架构不被无意扩大。

开始工作前，先阅读：

1. `README.md`
2. 与任务直接相关的 `docs/` 方案
3. `progress/tasks/*.json` 中对应任务
4. `progress/risks.json` 中关联风险

## 2. 进度真相源

以下文件是唯一可手工修改的进度真相源，在修改前必须先让用户确认方案，确认后才可写入：

- `progress/config.json`
- `progress/tasks/*.json`
- `progress/risks.json`
- `progress/tech-debts.json`
- `progress/logs.json`

`docs/retrieval-progress.md` 是自动生成的展示文件，禁止手工修改。

```powershell
npm run progress:build
npm run progress:check
```

只有同时满足以下条件，任务才能标记为 `已完成`：

- 请求范围内的产物已经落地；
- 任务卡中的验收条件已经满足；
- 对应验证已经运行并记录真实结果；
- 风险、技术债和日志已经同步；
- `npm run progress:check` 通过。

任务开始实施时，在任务卡中记录 `executionBoundary`，至少包含：

- `inScope`：本步纳入；
- `outOfScope`：本步明确不纳入；
- `followup`：后续归属任务；
- `verification`：本步验证命令或人工检查。

涉及出网隐私边界、破坏性数据迁移、公开 API 不兼容变化时，必须先取得用户明确确认。普通的可回退内部实现按已确认任务边界推进。

## 3. 架构边界

后端遵守现有分层：

- `domain/`：不可变领域对象、状态和校验；不做文件、SQLite、HTTP 或 Provider I/O。
- `application/`：用例编排、策略和事务边界；依赖 port，不直接写 SQL。
- `ports/`：最小稳定合同；不要泄漏 SQLite row、HTTP response 或 UI 结构。
- `adapters/`：SQLite、文件系统、Provider 等具体实现。
- `api/`：HTTP 输入输出映射与错误翻译；不承载业务决策。
- `workers/`：解析、OCR、转换等长任务实现；产物必须通过领域合同进入主链路。

前端保持现有 React/Vite 单页应用结构。界面文案、按钮、状态和错误默认使用中文；专有名词、协议字段和代码标识可保留英文。

跨层改动先定义领域对象和 port，再实现 adapter 与调用方。不要让 `application/` 通过具体 SQLite 类型访问存储，也不要让 UI 复制后端业务判定。

## 4. 编码规则

### 4.1 通用

1. 只修改任务边界需要的文件，不顺手重构无关代码。
2. 优先复用现有模式；单一调用点不新增抽象，除非能消除真实复杂度。
3. 命名表达业务含义，避免 `data`、`info`、`handler2` 等模糊名称。
4. 注释解释“为什么”和约束，不复述代码。复杂隐私、迁移、并发和检索融合逻辑应有短注释；注释语言沿用所在文件。
5. 不吞异常、不伪造成功、不静默截断证据。可恢复失败必须返回稳定状态与下一步动作。
6. 不记录 Provider secret、正文全文、用户绝对路径或授权 token；测试夹具必须脱敏。

### 4.2 Python

- 运行时固定为 Python `3.11.15`，格式与静态检查遵守 `apps/service/pyproject.toml`，行长上限 100。
- 领域值优先使用带校验的 `@dataclass(frozen=True)`，保持现有不可变模型风格。
- 新依赖必须写入 `pyproject.toml` 并更新 `uv.lock`，禁止依赖虚拟环境里偶然存在的间接包。
- 类型注解覆盖公开函数、port 和跨层数据结构；不要用无约束 `dict` 代替稳定合同。
- 时间、ID、路径、SHA-256 和状态值继续复用已有领域校验与 helper。

### 4.3 JavaScript 与 React

- 遵守现有 ESLint 配置和函数组件模式，不为单个功能引入新的状态库或 UI 框架。
- 网络请求、异步状态和错误提示必须覆盖 loading、empty、success、recoverable、failed。
- 控件保持键盘可用和可访问标签；紧凑工作台优先，不添加营销式布局或无功能装饰。
- UI 合同变化同时更新前端单测；关键用户流程补 Playwright。

### 4.4 SQLite 与迁移

- schema 变化必须幂等，并使用显式 migration ID；不得把“删除数据库重建”作为升级方案。
- 写入主记录、FTS、向量、映射或 graph 投影时，明确同一事务内的一致性边界。
- 新查询必须显式处理 `vault_id`、`is_current`、stale、policy 和 scope，防止跨 vault 或过期数据泄漏。
- 每个迁移至少覆盖：空库、新库、旧库升级、重复初始化；关键迁移还要覆盖失败回滚。

### 4.5 检索与 AI

- 元数据过滤决定范围，相似度只决定点查排序或重复候选；枚举型任务不得被 top-k 静默截断。
- BM25、向量和结构通道独立召回后融合，禁止让一个通道成为另一个通道的硬过滤器。
- 任何阈值、top-k、token 预算或量化策略都必须有 golden set 依据，不把一次 benchmark 当作普遍结论。
- Embedding 缓存与向量必须绑定完整 `embedding_profile_fingerprint`；模型或 Provider 配置变化后不能混用。
- 发送正文到 Provider 前必须通过 outbound policy 和用户已确认的授权范围。

## 5. 标准开发流程

1. 在 `progress/tasks/*.json` 定位任务；没有任务卡时先补卡。
2. 检查依赖和开放风险，写清本步执行边界，将状态更新为 `进行中`。
3. 先写能复现现状或失败的测试，再做最小实现。
4. 运行与改动最接近的定向测试，修复后再扩大验证范围。
5. 达到验收条件后更新任务、风险、技术债和日志。
6. 运行 `npm run progress:build` 和 `npm run progress:check`。
7. 提交前运行 `git diff --check`，并报告未运行的测试与剩余风险。

## 6. 验证矩阵

按改动范围选择最低充分验证：

```powershell
npm run lint
npm run unit
npm run integration
npm run browser-test
npm run test
```

- 纯文档或进度数据：`npm run progress:check`、`git diff --check`。
- Python 领域、应用或 adapter：相关 `pytest` 定向测试 + `ruff check`；共享合同改动再跑全部 unit。
- API、SQLite 生命周期或跨层合同：unit + integration。
- 前端状态或交互：前端 unit + build；关键流程再跑 browser test。
- 检索算法或阈值：除自动测试外，必须运行版本化 golden eval 并记录指标对比。

测试失败不能通过降低断言、扩大容差、删除样本或跳过测试来掩盖。若测试本身错误，先说明证据再修正测试。

## 7. 工作区与提交纪律

- 工作区可能包含用户未提交改动。修改前先看 `git status` 和相关 diff，不覆盖、不回退无关变化。
- 不提交运行日志、临时数据库、截图、Provider 凭据或本机绝对路径，除非任务明确要求受控测试资产。
- 每个提交只包含一个可独立验收的任务或紧密相关的小步；提交信息引用任务 ID。
- 未达到验收条件时保持 `进行中`，不要为了展示进度提前标记完成。
