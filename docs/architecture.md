# 交付实施助手 技术文档

本文说明系统的整体结构、各模块的实现方式、模块之间的调用关系，以及主要的设计取舍。
面向需要阅读、修改或扩展代码的开发者。

---

## 1. 总览

```text
┌──────────────────────── frontend (React + Vite) ────────────────────────┐
│  App.tsx  项目/会话/文档侧栏 · 对话区 · 引用面板                            │
│  api.ts   REST 请求 · streamChat() 解析 SSE                                │
└───────────────┬──────────────────────────────────────┬───────────────────┘
                │ REST (JSON / multipart)              │ SSE (text/event-stream)
┌───────────────▼──────────────────────────────────────▼───────────────────┐
│ backend/app/main.py   FastAPI 路由 · 生命周期 · SSE 事件转换                 │
│      │ 上传                          │ 对话                                 │
│      ▼                               ▼                                     │
│ documents.py  解析 + 分块        agent.py  LangGraph: agent ⇄ tools        │
│      │                               │ search_documents                    │
│      │ 写入片段                       ▼                                     │
│      │                          retrieval.py  BM25 (+ 向量) → RRF 融合      │
│      ▼                               │ 读取片段                              │
│ db.py  SQLite: 项目/文档/片段/会话 ◀──┘                                      │
│ domains.py  领域包：domains/*.yaml → 系统提示词、结论标签、示例问题、评估集     │
│ config.py  .env → Settings（被所有模块共用）                                 │
│ LangGraph AsyncSqliteSaver: 会话消息状态（checkpoints.db）                    │
└──────────────────────────────────────────────────────────────────────────┘
        ▲ 复用 documents / retrieval / agent / db
┌───────┴──────────────── backend/eval ────────────────────────────────────┐
│ run.py  批量跑用例并打分 · compare.py  汇总对比 · review.py  Excel 复核标签   │
└──────────────────────────────────────────────────────────────────────────┘
```

| 层 | 文件 | 行数 | 职责 |
|---|---|---|---|
| 配置 | `backend/app/config.py` | ~30 | 从 `.env` 读取模型、向量、数据目录等配置 |
| 存储 | `backend/app/db.py` | ~65 | SQLite 表结构与连接封装 |
| 文档 | `backend/app/documents.py` | ~135 | 四种格式的解析器与分块 |
| 检索 | `backend/app/retrieval.py` | ~105 | 分词、BM25、向量、RRF 融合、按项目缓存索引 |
| 领域包 | `backend/app/domains.py`、`backend/domains/*.yaml` | ~150 | 读取领域配置（中英两种语言），生成系统提示词和结论解析 |
| 语言 | `backend/app/i18n.py` | ~55 | 判断文本语言、解析 Accept-Language、接口消息的中英文 |
| Agent | `backend/app/agent.py` | ~55 | 工具定义、LangGraph 图（提示词由领域包提供） |
| 验收就绪度 | `backend/app/readiness.py` | ~200 | 抽取验收标准，并行逐条核查证据（LangGraph Send + 结构化输出） |
| 追踪 | `backend/app/tracing.py` | ~90 | 每次运行的耗时、token、工具调用、结论写入 `runs` 表；可选 LangSmith |
| MCP 服务 | `backend/app/mcp_server.py` | ~140 | 把项目文档库作为 MCP 工具提供给 Claude、Cursor 等客户端 |
| 接口 | `backend/app/main.py` | ~310 | REST 路由、SSE 流式对话、消息格式转换 |
| 前端 | `frontend/src/api.ts`、`App.tsx` | ~315 | 接口调用、SSE 解析、界面 |
| 评估 | `backend/eval/*.py`、`cases.yaml` | ~340 | 评估集、跑分、对比、标签复核 |

**依赖方向**（箭头表示“依赖于”）：

```text
main ──▶ agent ──▶ retrieval ──▶ db
  │        ├──────────────────▶ db
  │        └──▶ domains
  ├──▶ domains
  ├──▶ documents
  ├──▶ retrieval (Embedder, Retriever)
  └──▶ config ◀── retrieval
eval/run ──▶ agent, domains, retrieval, documents, db, config（不经过 main，不走 HTTP）
```

`documents` 不依赖任何其他模块；`agent` 不知道 HTTP 的存在；`main` 是唯一把各部分装配起来的地方。
因此评估脚本可以直接复用核心模块，测试也可以把大模型替换成假模型。

---

## 2. 两条核心数据流

### 2.1 上传文档

```text
浏览器 FormData(files[])
  → POST /api/projects/{id}/documents                      main.upload_documents
      for 每个文件:
        1. 检查扩展名 ∈ documents.SUPPORTED
        2. 保存原件 data/uploads/{project_id}/{随机前缀}_{文件名}
        3. documents.parse(path)   → [Section(location, text)]
        4. documents.chunk(...)    → [Section]（每段 ≤ 600 字）
        5. 若配置了 embedding: Embedder.embed(location + text) → float32 向量
        6. INSERT documents 一行 + chunks 多行（向量以 BLOB 存储）
        失败 → 删除原件，在结果中返回该文件的错误，继续处理下一个
      retriever.invalidate(project_id)                      使该项目的索引缓存失效
  ← [{filename, id, n_chunks} | {filename, error}]
```

### 2.2 对话

```text
浏览器 streamChat(conversation_id, message)
  → POST /api/conversations/{id}/chat                       main.chat
      若会话标题仍为“新会话”，用消息前 30 字作为标题
      graph.astream({messages: [HumanMessage]},
                    config={thread_id: 会话ID, project_id: 项目ID, recursion_limit: 16},
                    stream_mode=['messages', 'updates'])
        ┌─▶ agent 节点: LLM(系统提示词 + 历史消息)
        │      ├─ 有 tool_calls → tools 节点
        │      │     search_documents(query) → Retriever.search(project_id, query)
        │      │     返回 (给 LLM 的文本, 给前端的片段列表 artifact)
        └──────┘
               └─ 无 tool_calls → 结束
      流式事件转换：
        messages 模式中 agent 节点的文本增量    → event: text
        updates 模式中 agent 节点的 tool_calls  → event: tool_call
        updates 模式中 tools 节点的 ToolMessage → event: tool_result（含 sources）
        正常结束 → event: done；异常 → event: error
  ← text/event-stream
```

会话的全部消息（含工具调用和工具结果）由 LangGraph checkpointer 按 `thread_id` 持久化。
刷新页面时，`GET /api/conversations/{id}/messages` 从 checkpointer 读回状态，并转换成与流式事件相同的结构。

---

## 3. 配置模块 `config.py`

基于 `pydantic-settings`，按字段名读取 `.env` 和环境变量（不区分大小写）。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `llm_base_url` / `llm_api_key` / `llm_model` | DeepSeek / 空 / `deepseek-chat` | 对话模型，任意 OpenAI 兼容接口 |
| `llm_temperature` | 0.2 | 偏低，减少判断结论的随机性 |
| `openai_api_key` | 空 | 仅评估脚本的 `--provider openai` 使用 |
| `embedding_base_url` / `embedding_api_key` / `embedding_model` | 空 | 留空则关闭向量检索 |
| `data_dir` | `data` | SQLite 和上传文件目录（相对于启动目录） |
| `top_k` | 6 | 每次检索返回的片段数 |

`get_settings()` 用 `lru_cache` 做成单例。测试中直接构造 `Settings(_env_file=None, ...)` 传给 `create_app`，
不读取开发者本机的 `.env`。

---

## 4. 存储模块 `db.py`

一个 SQLite 文件 `data/app.db`，四张表：

```text
projects(id, name, domain, created_at)
   │1
   ├──∞ documents(id, project_id, filename, n_chunks, created_at)
   │       │1
   │       └──∞ chunks(id, document_id, project_id, ordinal, location, text, embedding BLOB)
   └──∞ conversations(id TEXT, project_id, title, created_at)
```

- `chunks.id` 就是界面和回答里的引用编号：`[c12]` 指 `chunks.id = 12`。全局自增，跨文档唯一。
- `chunks.project_id` 是冗余字段，便于按项目一次查出全部片段构建索引，不需要连表过滤。
- 外键开启 `ON DELETE CASCADE`：删除文档时，其片段一起删除。
- `projects.domain` 是领域包的 ID（默认 `automation_delivery`）。旧数据库在启动时自动补上这一列。
- 会话表只存元数据（标题、所属项目）。消息内容存在 LangGraph 的 `checkpoints.db` 中，两者通过会话 ID 关联。

`Database` 类每次操作新建一个连接（`connect()` 上下文管理器，退出时提交）。
SQLite 的连接开销很小，这样做可以避免跨线程共享连接的问题：FastAPI 会在线程池中执行同步路由。

---

## 5. 文档模块 `documents.py`

### 5.1 统一的中间表示

```python
@dataclass
class Section:
    location: str   # 人可读的位置，直接用于引用展示
    text: str
```

所有解析器都输出 `list[Section]`。分块、检索、引用都只依赖这个结构，与原始格式无关。
新增一种格式，只需要再写一个 `_parse_xxx` 并加进 `SUPPORTED`。

### 5.2 各格式解析器

| 函数 | 实现要点 |
|---|---|
| `_parse_pdf` | `pypdf` 逐页抽取文字，位置为“第 N 页”；空页跳过 |
| `_parse_docx` | 按文档顺序遍历 body 的子元素（段落和表格交错），而不是分别读取 `paragraphs` 和 `tables`，否则表格会和所在章节脱节。遇到 `Heading*` / `标题*` / `Title` 样式的段落就开始新的 Section。表格每行用 ` \| ` 连接，并去掉合并单元格产生的相邻重复内容 |
| `_parse_xlsx` | 第一行作表头，之后每 5 行一组，每行写成“表头：值；表头：值”。这样即使切到中间的行，检索时也能看到字段名。位置记为“工作表「名称」第 a-b 行”（Excel 行号） |
| `_parse_markdown` | 维护标题栈，位置是标题路径，如“方案评审会纪要 > 新增需求讨论” |

### 5.3 分块 `chunk(sections, max_chars=600, overlap=120)`

- 短于 600 字的 Section 原样保留，一个章节就是一个片段。
- 长 Section 按**行**切分，尽量不把一句话、一行表格切断：
  - 逐行累加，超过上限就输出一块。
  - 上一块的最后一行如果不长于 120 字，会重复放到下一块开头，保留跨块上下文。
  - 单行本身超过 600 字时，用滑动窗口切开（步长 480，窗口之间重叠 120 字）。
- 同一 Section 切出的块都继承它的 `location`。

600 字是经验值，大致对应合同中的一到两个条款。分块大小是评估中计划做的消融变量（`eval.run --chunk-size`）。

---

## 6. 检索模块 `retrieval.py`

### 6.1 分词

`jieba.lcut_for_search`（搜索引擎模式，会把长词再切出子词，如“压缩空气”→“压缩、空气、压缩空气”），
转小写，去掉纯标点和一组中文停用词。

### 6.2 索引与缓存

`Retriever` 对每个项目缓存一个 `_ProjectIndex`：

- `BM25Okapi`：语料是每个片段的 `location + text`，章节标题也参与匹配。
- `vectors`：片段的向量矩阵（已归一化）。只有当所有片段都有向量时才启用，避免混入未向量化的片段。

索引在第一次检索时构建；上传或删除文档时调用 `invalidate(project_id)` 丢弃缓存。
单个项目通常只有几百到几千个片段，重建索引只需毫秒级时间，因此不做增量更新。

### 6.3 检索与融合

```text
search(project_id, query, top_k):
  tokens = tokenize(query)
  ranking_1 = BM25 得分 > 0 的片段，按得分降序
  ranking_2 = 向量余弦相似度前 50（仅在启用 embedding 时）
  RRF: score(d) = Σ 1 / (60 + rank_i(d) + 1)，每个排名列表取前 50
  返回融合得分前 top_k 的 Hit(chunk_id, document_id, filename, location, text, score)
```

**为什么用 RRF 而不是加权求和：** BM25 得分和余弦相似度的量纲不同，直接相加需要调权重。
RRF 只看名次，没有可调参数（k=60 是文献中的常用值），在这种小规模场景下最稳妥。

**向量的计算方式：** 入库时对 `location + "\n" + text` 计算向量，查询时对问题原文计算向量。
`Embedder` 封装 `langchain_openai.OpenAIEmbeddings`，并关闭了 `check_embedding_ctx_length`
（该检查依赖 OpenAI 的 tiktoken 分词，对其他兼容服务不适用）。

---

## 7. Agent 模块 `agent.py`

### 7.1 图结构

```text
START → agent ──tools_condition──▶ tools ─┐
          ▲                               │
          └───────────────────────────────┘
          └──(没有工具调用)──▶ END
```

- 状态使用 LangGraph 内置的 `MessagesState`（一个按追加方式合并的消息列表）。
- `agent` 节点：按 `configurable.domain` 取出领域包，生成系统提示词放在历史消息前面，调用 `llm.bind_tools(tools)`。系统提示词不写入状态，
  所以修改提示词对历史会话立即生效，也不会在 checkpoint 中重复存储。
- `tools` 节点：LangGraph 的 `ToolNode`，并发执行同一轮的多个工具调用（DeepSeek 常在一轮里发起三四次检索）。
- `recursion_limit=16`（在 main 中设置）限制最多约 8 轮“检索—思考”，防止死循环。

这个结构等价于 ReAct agent。之所以用 `StateGraph` 显式搭建，而没有用预制的 agent 函数，
是为了后续方便插入节点，例如写操作前的人工审批（`interrupt`）、答案自检、意图路由。

### 7.2 工具

| 工具 | 输入 | 给 LLM 的内容 | artifact（给前端和评估） |
|---|---|---|---|
| `search_documents` | `query: str` | 每个片段格式为 `[c{id}] 《文件名》 位置\n正文` | `Hit` 字典列表 |
| `list_documents` | 无 | 文件名和上传时间列表 | 无 |

两点实现细节：

- **项目隔离**：工具通过注入的 `RunnableConfig` 读取 `configurable.project_id`，LLM 看不到也改不了这个参数，
  因此不会检索到其他项目的文档。工具在 `build_tools` 中以闭包方式绑定 `db` 和 `retriever`，整个应用只构建一次图。
- **`response_format='content_and_artifact'`**：工具同时返回两份数据。文本给 LLM 阅读；结构化的片段列表
  存在 `ToolMessage.artifact` 中，随 checkpoint 持久化。前端用它展示步骤卡片和引用原文，
  评估脚本用它计算“证据检索率”和“无效引用”。这样就不用解析给 LLM 的文本来还原片段信息。

### 7.3 系统提示词与领域包 `domains.py`

系统提示词分成两部分：

- **通用部分**（`domains.TEMPLATES`，中英各一份，所有领域共用）：涉及项目事实先检索、可多次换关键词；
  只依据检索结果回答，每条事实标注 `[c编号]`；检索不到就说明未找到；文档冲突时指出来；
  回答第一行写结论代码 `[[verdict:<代码>]]`；回答语言、文档语言的规则见 12.5。
  还注入了当天日期，让模型能判断“最近一份日报是两个月前的”这类时效问题。
- **领域部分**（`backend/domains/<id>.yaml`）：

| 字段 | 作用 | 自动化交付的取值 |
|---|---|---|
| `role` | 身份，“你是……” | 自动化项目交付实施助手，服务于项目经理和实施工程师 |
| `judgment` | 什么时候要给结论 | 判断客户需求是否属于合同范围 |
| `basis` | 结论引用哪些文件 | 合同、技术协议或已书面确认的会议纪要 |
| `labels` | 结论代码 → 显示名和判定标准（2~5 个）；代码与语言无关 | `in_scope` 范围内 / `out_of_scope` 范围外 / `ambiguous` 存在歧义 |
| `rules` | 额外的判断规则，追加在提示词末尾 | 无 |
| `examples` | 界面空白页上的示例问题 | 3 条 |
| `eval` | 评估集和评估用文档目录 | `eval/cases.yaml`、`sample_data/project_a` |
| `en` | 英文版本，没写的字段沿用中文（`eval` 除外） | 英文名称、标签显示名、示例问题；英文评估集 `cases_en.yaml` |

`Domain` 对象提供：`system_prompt(today, 文档语言, 提问语言)` 拼出完整提示词；`parse_verdict(answer)` 取出结论代码；
`code_for(name)` 把评估集里的标签名换成代码；`public()` 是给前端的字段。

领域在**建项目时确定**，存在 `projects.domain` 里。`main.py` 对话时把它放进 `configurable.domain`，
agent 节点按它取提示词；评估脚本用 `--domain` 指定。图只构建一次，不同领域的项目共用同一个图。

目前有两个领域包：

- `automation_delivery`（自动化交付）：有中文和英文评估集。
- `software_impl`（ToB 软件实施）：标签为范围内 / 需求变更 / 待澄清，并带 4 条领域规则
  （配置可实现算范围内、UAT 后的新需求算变更、上线缺陷不算变更、口头同意要补签）。还没有评估集。

### 7.4 模型

`main.default_llm` 使用 `ChatOpenAI(base_url=...)`，所以任何 OpenAI 兼容接口都能接入。
`streaming=True` 让 LangGraph 的 `messages` 流模式拿到逐 token 输出。
`build_graph` 只要求模型实现 `bind_tools` 和 `ainvoke`，测试中的 `ScriptedModel` 就是借此替换真实模型的。

---

## 8. 评估模块 backend/eval

### 8.1 评估集 `cases.yaml`

评估集挂在领域包下：领域 YAML 的 `eval.cases` 指向用例文件，`eval.documents` 指向这些用例所依据的项目文档。
`automation_delivery` 的评估集是 45 条客户变更请求，基于 `sample_data/project_a`。每条包含：

```yaml
- id: S07
  request: 甲方刘洋要在 HMI 上加一个按班次统计的产量报表页面。
  label: 存在歧义                     # 范围内 / 范围外 / 存在歧义
  category: HMI
  evidence:                          # 判断依据应来自的文档及其中的原文（逐字出现）
    - {doc: 技术协议, contains: HMI 触摸屏 1 台}
  rationale: ...
  reviewed: false                    # 是否已由项目经理复核
```

`evidence` 让评估不仅能看结论对不对，还能看依据找得对不对。

### 8.2 跑分 `run.py`

```text
1. 按 --domain 读取领域包，检查用例里的标签都属于该领域
   新建临时 SQLite，按指定 chunk_size 解析并入库领域包指定的文档（复用 documents / Retriever / Embedder）
2. build_graph(指定模型, ..., InMemorySaver())，每条用例一个独立的 thread
3. asyncio + Semaphore 并发执行（默认 8 路），可设重复次数
4. 对每条结果计算：
     pred                 回答中的结论代码 [[verdict:<代码>]]，换回标签名
     evidence_retrieved   evidence 中被任一检索片段（ToolMessage.artifact）包含的比例
     evidence_cited       evidence 中被任一引用片段（回答里的 [cN]）包含的比例
     invalid_citations    回答引用了、但本次没有检索到的片段编号（模型编造的引用）
     tool_calls / latency / tokens（取自 AIMessage.usage_metadata）
5. 汇总：准确率（各次重复的均值和每次的值）、各标签的 P/R/F1、Macro-F1、混淆矩阵、未解析数
6. 写入 eval/results/<name>/cases.jsonl 和 summary.json
```

可调参数：`--domain`（默认 `automation_delivery`）、`--lang {zh,en}`（领域的哪个语言版本的评估集）、`--provider {deepseek,openai}`、`--model`、`--retrieval {bm25,hybrid}`、`--top-k`、
`--chunk-size`、`--repeats`、`--concurrency`、`--ids`。

### 8.3 对比与复核

- `compare.py`：读取多个 `summary.json`，输出 Markdown 对比表。
- `review.py export`：导出 Excel，包含需求、草稿标签、依据、证据和模型预测（例如“范围外×3”），
  复核列带下拉校验。`import` 把复核结果写回 YAML，并标记 `reviewed: true`；保留文件头部的标签规则注释。

### 8.4 当前结果与结论（标签尚未复核）

| 配置 | 准确率 | Macro-F1 | 证据检索率 | 无效引用率 |
|---|---|---|---|---|
| DeepSeek + 混合检索（9 次） | 0.874（单次 0.84–0.89） | 0.85 | 97% | 0 |
| DeepSeek + BM25（3 次） | 0.859 | 0.83 | 100% | 0 |
| GPT-5.4-mini + 混合检索（3 次） | 0.726 | 0.73 | 95% | 0 |

- 文档规模小，检索不是瓶颈，两种检索方式的差异在波动范围之内。
- 主要的分歧在“文档未提及”的需求上：DeepSeek 倾向判为范围外，GPT-5.4-mini 倾向判为存在歧义。
  这首先是一个标注规则问题，需要由项目经理确定后重新评测。

---

## 9. 接口模块 `main.py`

### 9.1 装配：`create_app(settings=None, llm=None)`

应用工厂负责组装 `Database`、`Embedder`（可选）、`Retriever`，并在 `lifespan` 中：

1. 打开 `AsyncSqliteSaver`（`data/checkpoints.db`），它的生命周期与应用一致；
2. 用传入的 `llm` 或 `default_llm(settings)` 构建图，存入 `app.state.graph`；
3. 没有配置 API key 时，`graph` 为 `None`，其余功能照常可用，对话接口返回一个 error 事件。

使用工厂而不是模块级的 `app`，是为了让测试和评估能注入临时目录和假模型。
启动命令因此是 `uvicorn app.main:create_app --factory`，由根目录的 `start.sh` 封装：检查 `.env` 和端口，
按需安装前端依赖、在前端代码比 `dist` 新时重新构建，再启动 uvicorn；`dev` 参数同时启动 Vite 并把后端端口通过 `API_PORT` 传给它的代理。

### 9.2 路由

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/domains` | 领域包列表：名称、说明、结论标签、示例问题（按 `Accept-Language` 返回中文或英文） |
| GET / POST | `/api/projects` | 项目列表 / 新建（`{name, domain}`，`domain` 缺省为 `automation_delivery`，未知领域返回 400） |
| GET / POST | `/api/projects/{id}/documents` | 文档列表 / 上传（multipart，`files` 可多个） |
| DELETE | `/api/documents/{id}` | 删除文档及其片段 |
| GET | `/api/projects/{id}/search?q=` | 直接调用检索，用于调试检索效果 |
| POST | `/api/projects/{id}/readiness` | 验收就绪度检查：每条验收标准的状态、发现和引用，见 12.6 |
| GET | `/api/projects/{id}/runs?limit=` | 运行记录与汇总（问答的平均耗时、token、工具调用、结论分布；按来源分组），见 12.8 |
| GET / POST | `/api/projects/{id}/conversations` | 会话列表 / 新建 |
| GET | `/api/conversations/{id}/messages` | 从 checkpointer 读取历史，转换成界面结构 |
| POST | `/api/conversations/{id}/chat` | 流式对话（SSE） |
| GET | `/*` | 如果存在 `frontend/dist`，返回前端页面（`/api/*` 除外） |

### 9.3 SSE 事件协议

| event | data | 来源 |
|---|---|---|
| `text` | `{text}` | `messages` 流模式中 agent 节点的增量文本 |
| `tool_call` | `{id, name, args}` | `updates` 流模式中 agent 节点输出的 `tool_calls` |
| `tool_result` | `{id, name, sources[], content}` | `updates` 流模式中 tools 节点的 `ToolMessage`；`sources` 来自 artifact，没有 sources 时 `content` 为工具文本 |
| `error` | `{message}` | 未配置模型，或执行中出现异常 |
| `done` | `{}` | 正常结束 |

同时订阅两种流模式，是因为 `messages` 模式适合逐字输出文本，但拿不到完整的工具调用参数；
`updates` 模式在节点执行完后给出完整消息，适合输出工具调用和工具结果。

### 9.4 统一的消息结构

流式事件和历史消息被转换成同一种结构，前端只需一套渲染逻辑：

```ts
Message = { role: 'user' | 'assistant', parts: Part[] }
Part    = text | tool_call | tool_result | error
```

`to_ui_messages` 把 checkpoint 中的 `Human / AI / Tool` 消息序列分组：每条用户消息开启一轮；
后续的 AI 文本、工具调用和工具结果按出现顺序并入同一个 assistant 消息。

---

## 10. 前端 `frontend/src`

### 10.1 `api.ts`

- 类型定义：`Domain`、`Project`、`Doc`、`Conversation`、`Source`、`Part`、`Message`。
- `api.*`：对 REST 接口的简单封装，错误时抛出带后端 `detail` 的 Error。
- `streamChat`：浏览器原生的 `EventSource` 只支持 GET，这里需要 POST，所以用
  `fetch` + `TextDecoderStream` 读取响应体，按空行切分事件块，解析 `event:` / `data:`，
  再把每个事件作为 `Part` 回调出去。它同时兼容 `\r\n` 换行，并忽略心跳注释。

### 10.2 `App.tsx`

| 组件 | 职责 |
|---|---|
| `App` | 全局状态：领域、项目、文档、会话、消息、当前引用、是否在生成中；处理上传、删除、发送 |
| `NewProject` | 新建项目表单：项目名称 + 领域下拉框，下方显示所选领域的说明和结论标签 |
| `AssistantParts` | 按顺序渲染一个 assistant 消息的 parts，并按 `id` 把 tool_call 和 tool_result 配对 |
| `Step` | 可折叠的步骤卡片：工具名、检索词、结果数，展开后显示各片段的出处 |
| `Answer` | Markdown 渲染（`react-markdown` + GFM 表格）。先把 `[c12]` 替换成链接 `#cite-12`，再在自定义的 `a` 渲染器里把它换成引用按钮 |
| `Composer` | 输入框。Enter 发送，并通过 `isComposing` 判断避免中文输入法选词时误发 |
| `Empty` | 空状态：没有文档时提示上传，有文档时给出当前领域的示例问题 |

**流式更新：** 发送时先追加一条用户消息和一条空的 assistant 消息，之后每收到一个 Part 就更新最后一条消息。
连续的 text 合并到同一个 part；工具事件作为新的 part 追加。

**引用解析：** 用 `useMemo` 从当前会话所有 `tool_result.sources` 中建立 `chunk_id → Source` 映射。
点击引用后在右侧面板显示原文。由于用的是 artifact 中的数据，不需要额外请求。

**状态同步：** 新建会话后会切换 `conversationId`，此时如果去拉取历史，会覆盖正在流式写入的本地消息；
因此 `busy` 为真时跳过拉取。

---

## 11. 测试 `backend/tests/test_app.py`

| 测试 | 覆盖内容 |
|---|---|
| `test_parse_all_sample_formats` | 四种格式都能解析，片段长度不超过上限，docx 章节和 xlsx 表头、行号正确 |
| `test_upload_search_and_chat` | 批量上传（含不支持的格式）、检索命中、完整的 SSE 事件顺序、引用编号与检索结果一致、历史消息结构、会话标题 |
| `test_delete_document_removes_it_from_search` | 删除文档后索引缓存失效 |
| `test_chat_without_llm_reports_configuration_error` | 未配置 key 时返回 error 事件 |
| `test_domain_packs` / `test_projects_carry_their_domain` | 领域包加载、提示词、结论代码解析、各评估集标签合法；项目按领域取提示词 |
| `test_language_detection` / `test_english_documents_get_english_locations` / `test_prompt_follows_ui_language_and_names_document_languages` | 语言判断、英文分词和工具说明、英文引用位置、提示词语言与文档语言提示、接口消息本地化 |
| `test_old_database_is_migrated` | 旧数据库补列（领域、文档语言）并从片段回填文档语言 |
| `test_mcp_server_tools` | 用 MCP 内存客户端调用四个工具：只读标注、结构化结果、错误信息透传 |
| `test_runs_are_traced` | 问答写入运行记录；LangSmith 环境变量的开启逻辑 |
| `test_readiness_check` / `test_structured_output_repairs_bare_passage_ids` | 就绪度检查的并行流程、引用过滤（只保留给模型看过的片段）、`[c26]` 写进 JSON 的修复 |

`ScriptedModel` 是一个只实现了 `_generate` 的 `BaseChatModel`：第一轮发起一次检索，第二轮引用第一个检索结果作答。
测试不需要网络，也不消耗 API 费用。运行命令：`cd backend && uv run pytest`。

---

## 12. 设计取舍

| 决策 | 选择 | 原因 | 代价 |
|---|---|---|---|
| 向量库 | SQLite BLOB + numpy 暴力计算相似度 | 单项目片段数在千级以内，暴力计算只需毫秒；少一个需要运维的服务 | 片段数到十万级时需要换成 pgvector 或 FAISS |
| 中文检索 | jieba + BM25 为主，向量可选 | 合同条款、型号、点位地址这类精确术语，关键词检索更可靠；DeepSeek 没有 embedding 接口 | 同义改写的召回依赖向量检索 |
| 引用粒度 | 片段编号，而不是句子级 | 实现简单，原文面板足够核对 | 一个片段较长时，需要人自己找到对应的句子 |
| 会话存储 | LangGraph checkpointer | 工具调用、artifact 自动持久化，以后加人工审批（interrupt）可直接用 | 消息格式由 LangGraph 决定，需要 `to_ui_messages` 转换 |
| 结论格式 | 回答第一行写与语言无关的结论代码 `[[verdict:<代码>]]` | 界面和评估共用，不受回答语言影响，不需要额外的结构化输出调用 | 模型偶尔不写代码（实测 DeepSeek 四种语言组合 540 次中 1 次） |
| 前端 | React，只引入 Markdown 相关依赖 | 界面简单，不需要组件库 | 样式需要手写 |

---

## 12.5 多语言（中文 / 英文）

### 12.5.1 设计：把四件事分开

| 问题 | 谁决定 | 实现 |
|---|---|---|
| 界面文字、接口提示 | 用户在界面切换（首次跟随浏览器） | 前端 `i18n.ts` 字典；每个请求带 `Accept-Language`，后端 `i18n.t()` 按它返回消息和领域包 |
| 提示词用哪种语言写 | 界面语言 | `main.py` 把界面语言放进 `configurable.lang`，`DomainPack.get(lang)` 取对应版本；工具说明也随之切换 |
| **回答用哪种语言** | **用户**：默认跟提问的语言，用户在 prompt 里指定了就照指定的 | 提示词第 6 条写明规则；第 8 条由代码写上“用户最新的提问是用 X 写的”作为默认值 |
| 检索词用哪种语言 | 文档的语言 | 上传时识别每份文档的语言存入 `documents.lang`；提示词第 7 条写明“本项目文档的语言：9 份英文，检索词要用文档的语言” |

另外两条贯穿始终：

- **结论与语言无关**：回答第一行写结论代码 `[[verdict:out_of_scope]]`，前端按界面语言显示成彩色标签
  （范围外 / Out of scope），评估按代码打分。不管回答是中文、英文还是用户要求的其他语言，解析都只认这一种格式。
- **原文不翻译，引用时附译文**：检索和引用面板始终是文档原文；回答里引用的原文和回答语言不同时，
  先给原文，再在括号里给译文。这样核对依据时看的是原文，不会被翻译改写。

其他配套：英文文档的引用位置用英文（`documents.parse_document` 先识别语言，再用对应的标签解析，如
“Page 3”“Sheet 'Issue Log' rows 2-6”）；分词同时处理中英文（jieba 切中文；英文去停用词、轻量词干还原，
reports / reported → report）；`detect_language` 数汉字和拉丁字母，一个汉字按三个字母算。

### 12.5.2 怎么得到这个设计的（实测）

1. **第一版：按提问语言切换整套提示词。** 能用，但用户没法用 prompt 改回答语言（中文提示词写死了“用中文回答”）；
   结论行每种语言一种写法（“结论：范围外” / “Verdict: Out of scope”），解析要分语言处理。
2. **工具说明会把检索词带偏。** 英文提问、英文文档，但工具说明是中文时，70% 的检索词是中文，
   纯 BM25 每题要检索约 5.7 次。在工具说明里写“用文档的语言检索”无效；改为在提示词里直接告诉模型文档是什么语言后，
   中文提问检索英文文档时，模型会主动用英文关键词（如 “QR code reading before robot picking blank”）。
3. **只写“用提问的语言回答”不够。** 英文提问、中文文档时，135 个回答中有 107 个用了中文（回答语言一致率 0.207）：
   检索到的中文片段把回答语言拉了过去。这与文献中的 “language confusion” 一致。在提示词末尾由代码写明
   “用户最新的提问是用英文写的：用英文回答，除非用户要求用其他语言”后，一致率升到 1.0；
   用户明确要求（“请用英文回答” / “Please answer in Chinese”）时仍按用户要求。

### 12.5.3 评估：四种语言组合

英文评估集 `cases_en.yaml` 是 45 条用例的英文版（编号、标签、类别相同），基于同一项目的英文文档 `project_a_en`
（`sample_data/generate_en.py` 生成），每条证据原文都在英文文档中逐字出现（生成时校验过）。
`eval.run --lang <提问语言> --docs-lang <文档语言>` 可以组合出跨语言测试：提问和标签取自前者，文档和证据取自后者。
新增指标“回答语言一致”：去掉引号里的原文后判断回答语言，与提问语言比较。

| DeepSeek，混合检索，各 3 次 | 准确率 | Macro-F1 | 存在歧义召回 | 证据检索率 | 无效引用率 | 回答语言一致 | 每题检索次数 |
|---|---|---|---|---|---|---|---|
| 中文问 · 中文文档 | 0.933 | 0.92 | 0.82 | 99% | 0 | 100% | 2.8 |
| 英文问 · 英文文档 | 0.919 | 0.90 | 0.76 | 98% | 0 | 100% | 3.4 |
| 中文问 · 英文文档 | 0.926 | 0.92 | 0.82 | 100% | 0 | 100% | 3.3 |
| 英文问 · 中文文档 | 0.919 | 0.91 | 0.76 | 99% | 0 | 100% | 3.1 |
| 参考：改造前，中文问 · 中文文档（9 次） | 0.874 | 0.85 | 0.60 | 97% | 0 | — | 2.6 |

- 四种组合相差不到 0.015，在单次波动（约 ±0.02）之内：跨语言没有明显损失。这和文献“跨语言主要损失在检索”的结论并不矛盾——
  我们的文档量小，并且检索词按文档语言写、还有多语言向量兜底，检索率都在 98% 以上。
- 中文从 0.874 升到 0.933，主要来自“存在歧义”的召回（0.60 → 0.82）。这一版同时改了结论格式、文档语言提示和回答语言规则，
  没有逐项消融，不能确定是哪一项带来的。
- 错的题仍是同一批：S12（第五台机床覆盖）、S16（ZX-A02 节拍）、S39（随机备件），属于标注口径问题。

### 12.5.4 与业界做法的对照

| 业界做法 | 来源 | 本项目 |
|---|---|---|
| 跨语言 RAG 的主要损失在检索（提问与文档语言不同时检索最多降约 42%） | [1] | 检索词按文档语言写，混合检索兜底；实测检索率均 ≥ 98% |
| 查询翻译：把查询翻译成文档语言再检索 | [1][4][5] | 由 agent 按提示词直接用文档语言写检索词，不需要单独的翻译调用 |
| 多语言向量优先于翻译流水线 | [3] | 混合检索中的 `text-embedding-3-small` 本身是多语言的 |
| 混合语料按语言均衡检索（每种语言各取 k 条） | [1] | 未做，见路线图；目前示例项目都是单一语言 |
| 检索结果翻译成统一语言再生成（CrossRAG），回答语言更一致 | [2] | 不预先翻译，回答时引用原文并附译文；原文面板保持原文，便于核对 |
| 模型会答错语言（language confusion），复杂提示和高温度会加重 | [6][7][8] | 由代码写明本轮提问语言作为默认值；温度 0.2；评估中监控“回答语言一致” |
| 线上监控回答语言与用户语言是否一致 | [9] | 评估指标 `answer_language_match` |

参考资料：

1. *The Cross-Lingual Cost: Retrieval Biases in RAG over Arabic-English Corpora*. arXiv:2507.07543. <https://arxiv.org/abs/2507.07543>
2. *Multilingual Retrieval-Augmented Generation*（tRAG / MultiRAG / CrossRAG 对比）. arXiv:2504.03616. <https://arxiv.org/abs/2504.03616>；
   相关比较（问题翻译 vs 多语言检索）另见 EACL 2026 Findings：<https://aclanthology.org/2026.findings-eacl.35>
3. *What Drives Cross-lingual Ranking? Retrieval Approaches with Multilingual Language Models*. arXiv:2511.19324. <https://arxiv.org/abs/2511.19324>
4. *CLIRudit*（BM25 / SPLADE 加文档翻译的跨语言检索）. arXiv:2504.16264. <https://arxiv.org/abs/2504.16264>
5. *NeuCLIRBench: A Modern Evaluation Collection for Monolingual, Cross-Language, and Multilingual Information Retrieval*. arXiv:2511.14758. <https://arxiv.org/abs/2511.14758>
6. Marchisio 等. *Understanding and Mitigating Language Confusion in LLMs*. arXiv:2406.20052. <https://arxiv.org/abs/2406.20052>
7. 语码转换场景下的输出语言判断（OLA 基准）. ACL 2026. <https://aclanthology.org/2026.acl-long.2162.pdf>
8. 对话中途切换语言时的回答语言保持. MME 2026. <https://aclanthology.org/2026.mme-main.13.pdf>
9. Datadog LLM Observability: Language Mismatch 评估. <https://docs.datadoghq.com/llm_observability/investigate/evaluations/language_mismatch/>

---

## 12.6 验收就绪度检查 `readiness.py`

```text
collect ──Send×N──▶ check ──▶ END        （N = 抽取出的验收标准数，最多 12，并行执行）
```

- **collect**：按文档语言用几组固定检索词（验收标准、SAT 条件、性能指标、参数“不大于/不小于”、培训资料）检索，
  把片段交给模型，用结构化输出（`Criteria`）列出终验收时设备和交付必须满足、可以核查的标准；
  提示中明确排除付款、工期、违约等合同条款。标准保留文档原文的措辞和语言，像引用原文一样。
- **check**：对每条标准，再检索“标准原文”和“标准 + 实测/结果/问题”，由模型给出 `met / at_risk / no_evidence`、
  一句话发现（用界面语言）和引用；只保留模型实际看过的片段编号。
- 结果按“有风险 → 无证据 → 已满足”排序，前端以表格展示，引用可点击查看原文。一次约 12–15 秒、约 2 万 token。

实测中遇到并解决的三个问题：

| 问题 | 原因 | 处理 |
|---|---|---|
| 最关键的风险（换型 26 分钟）没被列出 | “换型时间不大于 20 分钟”在技术协议的参数表里，检索词没覆盖 | 增加“参数 要求 不大于 不小于”检索词 |
| 部分标准判断为空 | DeepSeek 在流式模式下做结构化输出时返回空的函数调用 | 就绪度检查使用非流式的模型副本 |
| 偶发 JSON 解析失败 | 模型把片段标签 `[c26]` 照搬进 JSON，写成 `[c26, c12]` | 字段说明写明“整数，不带 c”；解析失败时去掉 `c` 前缀再解析，仍失败才重试 |

## 12.7 MCP 服务 `mcp_server.py`

用 MCP Python SDK（2.x，`MCPServer`）以 stdio 方式提供四个只读工具，与 Web 端读同一个数据库：

| 工具 | 作用 |
|---|---|
| `list_projects` | 项目、领域、文档数 |
| `list_documents(project_id)` | 文件名、语言、片段数 |
| `search_documents(project_id, query, top_k)` | 混合检索，返回片段原文和位置 |
| `ask(project_id, question, lang)` | 运行完整 agent，返回回答、结论（代码和名称）、被引用的片段（结构化输出 `Answer`） |

工具都标注了 `readOnlyHint`；错误用 `ToolError` 抛出，客户端能看到具体原因（如“No project with id 99”）。
在 Claude Code 中使用：`claude mcp add delivery-copilot -- uv --directory <仓库>/backend run python -m app.mcp_server`。

## 12.8 调用追踪 `tracing.py`

- **本地**：每次问答（Web、MCP）和每次就绪度检查都在 `runs` 表写一行：来源、领域、语言、问题、结论代码、
  工具调用数、输入/输出 token、耗时、错误。`GET /api/projects/{id}/runs` 返回汇总，侧栏底部显示
  “N 次问答 · 平均 X 秒 · Y tokens/次”。流式回答的 token 用量需要 `ChatOpenAI(stream_usage=True)`；
  就绪度检查的 token 用 `UsageMetadataCallbackHandler` 汇总并行的多次调用。
- **LangSmith（可选）**：`.env` 中填 `LANGSMITH_API_KEY` 即开启，LangChain 会上报每次 LLM 和工具调用的完整链路；
  每次运行带有 `project_id / domain / lang / source` 元数据，便于筛选。未配置时不发送任何数据。

## 12.9 售后领域 `domains/after_sales.yaml`

第三个领域包，判断售后请求是**免费保修 / 收费服务 / 需诊断确认**，并要求回答给出响应级别（P1/P2/P3）、
响应和到场时限、处理团队；合同与售后服务协议冲突时以较新的协议为准。

示例项目 `sample_data/after_sales_a`（`generate_after_sales.py` 生成）是华衡产线终验收之后进入质保期的状态，
8 份文档：合同、终验收证书、售后服务协议、备件价格清单、操作维护规范、售后工单记录、维修报告、巡检记录。
评估集 `eval/cases_after_sales.yaml` 共 24 条（收费 13、免费 6、需诊断 5），证据原文均逐字核对过。

| 运行（DeepSeek，混合检索，3 次） | 准确率 | Macro-F1 | 证据引用率 | 无效引用率 |
|---|---|---|---|---|
| v1：初版标签定义 | 0.792 | 0.69 | 94% | 0 |
| v2：明确“先修后查”口径 | 0.944 | 0.92 | 96% | 0 |
| v3：碰撞一律需诊断（写过头） | 0.875 | 0.88 | 97% | 0 |
| v4：原因未查明的碰撞需诊断（业务负责人复核 A06） | 0.944 | 0.94 | 98% | 0 |
| v5：无碰撞、误操作迹象的部件故障按免费保修（业务负责人复核 A01） | 0.986 | 0.98 | 99% | 0 |

- v1 的错误几乎全是“免费保修”被判为“需诊断确认”：初版把“故障原因还不能确定”也算作需诊断，与免费保修重叠。
  v2 改为“先修后查”：质保期内乙方部件故障默认先免费修复，只有已有具体迹象指向除外情形时才判需诊断。
- v2 下 A06（SRVO-050 碰撞报警、原因未查明）被判为免费保修。业务负责人复核：碰撞原因未查明时，责任归属和是否在保
  都定不了，也就无法确定是甲方购买还是乙方发备品，必须先诊断。v3 据此加了碰撞规则但写成“碰撞一律需诊断”，
  把原因已经明确的 A05（甲方改了门信号延时）、A18（操作工违规进围栏）也判成了需诊断；v4 收窄为“原因未查明的碰撞”，
  并要求需诊断时说明查明后的两种处理（乙方原因且在保：发备品；甲方原因或超保：甲方购买）。v4 中 A06 三次全对。
- v4 中 A01（伺服电机烧毁、原因未明）3 次有 2 次被判需诊断。业务负责人复核：没有碰撞和误操作迹象时，部件自身故障
  基本就是免费保修。v5 在规则里补了具体例子（电机烧毁、传感器失效、触摸屏失灵、电源模块损坏），72 次中只错 1 次（A09）。
- **注意**：v2–v5 都是在同一批 24 条用例上看过结果后修改的规则，0.986 明显偏乐观，只能说明规则和标签已经一致，
  不能代表对新问题的泛化效果。下一步应由业务负责人另写一批没参与调规则的用例做验证。

---

## 13. 扩展指南

| 想做的事 | 修改位置 |
|---|---|
| 支持新的语言 | `i18n.py` 加语言代码和接口消息；`domains.py` 加该语言的模板和结论词；`agent.py` 的 `TOOL_DESCRIPTIONS`；`documents.py` 的 `LOCATIONS`；前端 `i18n.ts` 加字典；`detect_language` 需要能识别它；领域包 YAML 加对应的块 |
| 支持新的领域 | 在 `backend/domains/` 加一个 YAML（字段见 7.3），重启即可在新建项目时选择。要评估时再准备示例文档和用例，填上 `eval` 字段，用 `eval.run --domain <id>` 跑分 |
| 支持新的文件格式 | `documents.py`：加 `_parse_xxx`，在 `parse()` 中分派，并加入 `SUPPORTED` |
| 新增工具（如更新问题清单） | `agent.py` 的 `build_tools`。需要展示结构化结果时用 `content_and_artifact`；在 `App.tsx` 的 `TOOL_LABELS` 中加上中文名 |
| 写操作前人工确认 | 在 `agent.py` 的图中加审批节点并调用 `interrupt()`；`main.py` 增加恢复接口（`Command(resume=...)`）；前端为对应事件渲染“确认 / 拒绝”按钮 |
| 加 rerank | `Retriever.search` 在 RRF 之后，对前 N 条调用 rerank 模型重新排序 |
| 换向量库 | 替换 `_ProjectIndex.vectors` 的构建和相似度计算，`search` 的接口保持不变 |
| 新的评估维度 | `cases.yaml` 加字段，在 `run.py` 的 `run_case` 中计算，`summarize` 中汇总，`compare.py` 的 `COLUMNS` 中展示 |
| 多用户 | 增加用户表，`projects` 关联用户，路由加鉴权依赖；`thread_id` 已经按会话隔离 |

---

## 14. 已知限制

- 扫描版 PDF 没有文字层，需要接入 OCR。
- 文档解析在上传请求内同步完成，大文件会阻塞这次请求；文件多时应改为后台任务。
- 检索索引缓存在进程内存中，多进程部署时需要改为共享的失效机制。
- 没有鉴权，服务只应在本机或内网使用。
- 界面只支持中文和英文；回答可以是用户要求的任何语言，但只评估过中英文。中英文档混在同一项目时的检索效果没有评估过。
- 评估集只基于一个虚构项目，标签还在复核中；结论的泛化性有待用更多真实项目验证。
