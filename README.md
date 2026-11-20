# 交付实施助手（Delivery Copilot）

面向自动化项目交付实施的文档 agent。把合同、技术协议、会议纪要、现场日报、问题清单上传到项目工作区，
用对话提问：范围判断、进展追溯、验收风险。每条事实都带可点击的原文引用，agent 的检索步骤在界面上可见。

文档：[使用指南](docs/user-guide.md) · [技术文档](docs/architecture.md) · [宣传页](docs/promo.html)

## 功能（MVP）

- **项目工作区**：每个项目独立的文档库和会话。
- **领域包**：行业相关的部分（身份、结论标签和判定标准、判断规则、示例问题、评估集）放在
  `backend/domains/*.yaml`，新建项目时选择。内置“自动化交付”“软件实施”“售后服务”三个领域，加新领域只需写一份 YAML。
- **文档解析**：PDF（按页）、Word（按标题分节，含表格）、Excel（按行组，保留表头）、Markdown（按标题层级）。
  每个片段带位置信息（章节 / 页码 / 工作表行号），用于引用。
- **混合检索**：jieba 分词 BM25，配置 embedding 后与向量检索用 RRF 融合。
- **LangGraph agent**：LLM 节点与工具节点循环，可多次换关键词检索；会话状态由 SQLite checkpointer 持久化。
- **范围判断**：依据合同和技术协议条款给出结论（自动化交付为“范围内 / 范围外 / 存在歧义”），并给出澄清和变更建议。
- **多语言**：界面可切换中文 / English。回答默认用提问的语言，用户在 prompt 里指定了就用指定的语言；
  文档可以是中文或英文，检索词按文档的语言写，引用的原文与回答语言不同时附译文。
  结论是与语言无关的代码，界面按界面语言显示成标签。中文问英文文档、英文问中文文档都评估过。
- **验收就绪度检查**：从合同和技术协议抽出验收标准，并行逐条到日报、问题清单里找证据，给出“已满足 / 有风险 / 无证据”和引用（LangGraph Send + 结构化输出）。
- **MCP 服务**：项目文档库作为 MCP 工具（列项目、检索、问答），Claude Code、Cursor 等可直接调用。
- **调用追踪**：每次运行的耗时、token、工具调用和结论记录在本地；可选接入 LangSmith 查看完整调用链。
- **Web UI**：流式输出，工具调用以可展开的步骤卡片展示，引用 `[c12]` 点击后在右侧显示原文。

## 架构

```text
frontend (React + Vite)          backend (FastAPI)
  对话 / 步骤卡片 / 引用面板  ──SSE──▶  /api/conversations/{id}/chat
  文档上传                    ──────▶  documents.py  解析 + 分块
                                         retrieval.py  BM25 (+ 向量, RRF)
                                         agent.py      LangGraph: agent ⇄ tools
                                         domains.py    领域包 → 系统提示词、结论标签
                                         SQLite: 项目/文档/片段；checkpoints.db: 会话状态
```

## 运行

需要 [uv](https://docs.astral.sh/uv/) 和 Node.js 20+（pnpm）。

```bash
./start.sh          # http://localhost:8000
```

首次运行会创建 `backend/.env`，填入 `LLM_API_KEY` 后再运行一次。脚本会按需安装依赖、在前端代码有改动时重新构建。
`./start.sh dev` 是开发模式（后端自动重载 + 前端热更新，http://localhost:5173）；换端口用 `PORT=9000 ./start.sh`。

LLM 使用任一 OpenAI 兼容接口（DeepSeek、OpenAI 等），在 `.env` 中切换 `LLM_BASE_URL` / `LLM_MODEL`；
可选填 `EMBEDDING_*` 启用向量检索。

## MCP

```bash
claude mcp add delivery-copilot -- uv --directory "$(pwd)/backend" run python -m app.mcp_server
```

提供 `list_projects`、`list_documents`、`search_documents`、`ask` 四个只读工具，读取与 Web 端相同的数据。

## 示例数据

`sample_data/generate.py` 生成一个虚构的“转向节机加工自动化上下料产线”项目：合同、技术协议（docx）、
通讯点表和问题清单（xlsx）、两次会议纪要和三份现场日报（md）。公司、人员和数字均为虚构。

```bash
cd backend && uv run python ../sample_data/generate.py
```

文档中埋了几个典型的交付问题，可以直接用来提问：

- 甲方在评审会提出毛坯读码追溯（技术协议 3.8 明确排除）。
- 甲方口头要求 HMI 增加班次产量报表（技术协议未列 HMI 画面清单，存在歧义）。
- 甲方压缩空气压力不达标、机床信号改造延期（甲方责任，影响工期认定）。
- 换型时间 26 分钟，超过 20 分钟的要求（SAT 风险）。

## 测试

```bash
cd backend && uv run pytest
```

测试使用脚本化的假模型覆盖解析、检索、上传、流式对话和历史恢复，不调用外部 API。

## 评估

评估集挂在领域包下（领域 YAML 的 `eval` 字段），`--domain` 指定领域，默认 `automation_delivery`。
它的评估集 `backend/eval/cases.yaml`：45 条基于示例项目的客户变更请求，每条标注范围结论
（范围内 / 范围外 / 存在歧义）、判断依据以及证据所在的文档和原文。
覆盖追溯、MES、HMI、机械、性能、视觉、安全、电气、甲方责任、培训、资料、质保、服务、布局等类别。
标签规则写在文件开头。英文评估集 `cases_en.yaml` 是同一批用例的英文版，基于同一项目的英文文档
（`sample_data/project_a_en`，由 `generate_en.py` 生成），用于对比两种语言。

当前结果（DeepSeek，混合检索，各 3 次，标签尚未复核）：

| 提问 · 文档 | 准确率 | 回答语言一致 | 编造引用 |
|---|---|---|---|
| 中文 · 中文 | 0.933 | 100% | 0 |
| 英文 · 英文 | 0.919 | 100% | 0 |
| 中文 · 英文 | 0.926 | 100% | 0 |
| 英文 · 中文 | 0.919 | 100% | 0 |

售后领域（`--domain after_sales`，24 条）：0.986（标签口径经业务负责人复核后多轮修订，初版 0.792；规则是在这批用例上调出来的，数字偏乐观，过程见技术文档 12.9）。

多语言的设计过程、实测问题和参考文献见[技术文档 12.5 节](docs/architecture.md#125-多语言中文--英文)。

```bash
cd backend
uv run python -m eval.run --name deepseek-hybrid --repeats 3                    # 默认 DeepSeek + 混合检索
uv run python -m eval.run --name deepseek-bm25 --retrieval bm25 --repeats 3
uv run python -m eval.run --name gpt54mini-hybrid --provider openai --repeats 3
uv run python -m eval.run --name en-en --lang en --repeats 3                     # 英文评估集
uv run python -m eval.run --name zh-en --lang zh --docs-lang en --repeats 3     # 中文提问、英文文档
uv run python -m eval.compare                                                   # 汇总对比表
```

指标：

| 指标 | 含义 |
|---|---|
| 准确率 / Macro-F1 | 回答开头的结论代码与标注一致的比例；重复多次取均值 |
| 证据检索率 | 标注证据所在片段被 agent 检索到的比例 |
| 证据引用率 | 标注证据所在片段被回答引用的比例 |
| 无效引用率 | 回答引用了本次没有检索到的片段编号（编造引用）的比例 |
| 回答语言一致 | 回答语言（去掉引用的原文后判断）与提问语言一致的比例 |
| 工具调用 / 耗时 / token | 每条用例的平均成本 |

标签复核流程：`uv run python -m eval.review export --results <run>` 导出 Excel（含模型预测供参考），
填写“复核标签”“复核依据”两列后 `uv run python -m eval.review import` 写回。

## 路线图

- [x] 评估集：基于示例项目的变更请求，人工标注范围结论，测准确率与引用正确率
- [x] 领域包：行业相关配置与代码分离，内置自动化交付、软件实施两个领域
- [x] 多语言：界面中英切换，回答语言由用户决定，跨语言检索与引用翻译，英文示例项目与四种语言组合的评估
- [x] 验收就绪度检查、MCP 服务、调用追踪（本地 + 可选 LangSmith）
- [x] 售后领域：示例项目与 24 条评估用例
- [ ] 变更单 / 回复邮件草稿生成，写入前人工审批（LangGraph interrupt）
- [ ] 现场故障排查助手：日志解析与故障模式匹配工具
- [ ] 中英文档混在同一项目时，按语言均衡检索（每种语言各取 k 条再合并），并做混合语言的评估集
- [ ] 软件实施领域的示例项目和评估集
- [ ] 英文 README 和英文演示视频
- [ ] 标签由项目经理逐条复核；扩充更多项目和更难的用例（多文档冲突、口头约定与书面冲突）
- [ ] 分块大小、rerank 的对比实验
- [ ] 项目 wiki：agent 维护的项目档案与决策日志
- [ ] 周报、变更单草稿生成
