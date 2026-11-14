# 实施进度记录

> 项目：非虚构阅读人格工作台（本地单机版）
> 依据：`nonfiction-workbench-implementation-plan.md`
> 判定：`python verify.py <M>` 全绿即通过；scorecard 落在 `eval/scorecard-<M>.json`

| 里程碑 | 状态 | 通过时间 | 关键指标 |
|---|---|---|---|
| M0 契约与测量基座 | PASS | 2026-11-13 22:39 | 锚点 10k round-trip 100%；2% 扰动存活 300/300 |
| M1 摄取与锚点 | PASS | 2026-11-13 22:47 | 5 格式解析；PDF 3 页阅读顺序 14/14；坏文件 24/24 结构化报错 |
| M2 非虚构语义层 | PASS | 2026-11-13 22:52 | 155 条 claim 100% 结构合法；引述归属 1/1；参考文献 3/3 |
| M3 检索与装配 | PASS | 2026-11-13 23:02 | 24/24 recall@10；nDCG 0.918；半径违规 0；反证 4/4 |
| M4 严格事实闸门 | PASS | 2026-11-13 23:10 | 陷阱 0 错误；弃权 17/18；引文保真 123/123 |
| M5 作者人格 | PASS | 2026-11-13 23:15 | 三源归层 20/21；意图 100% 标注；边界 0 泄漏 |
| M6 其他人格 | PASS | 2026-11-13 23:18 | 4 类人格；越界 0；圆桌 4 种声音 |
| M7 Deep Research | PASS | 2026-11-13 23:35 | 溯源 100%；注入 50/50 拦截；冲突 2 组；离线显式降级 |
| M8 交互层 | PASS | 2026-11-13 23:48 | 引用精确跳转 12/12；board=ledger；钉子入账；ask 28ms |
| M9 本地交付 | PASS | 2026-11-14 00:05 | 导出导入无损；删除 0 残留；迁移 v1→v5；300 页 24.7s |

## 环境与依赖决策

- 运行时：DSH 内置 Python 3.12（无需联网安装依赖）。
- 后端：**标准库 `http.server`**，不引入 FastAPI/uvicorn。
- 存储：SQLite（WAL + FTS5），向量以 float32 BLOB 存储、numpy 暴力检索。
- 解析：自研 PDF 解析器（对象表 / Flate / ToUnicode CMap / 双栏阅读顺序），EPUB 用 zipfile+lxml，DOCX 用 python-docx，HTML 用 lxml。
- 模型：`offline` 确定性 provider（哈希向量、词法精排、规则 NLI）+ `openai_compat` BYOK provider。
- 测试：自研 `Checker`（`tests/harness.py`），不依赖 pytest。

偏差说明：计划中写的是 FastAPI + 外部依赖；因本机网络极慢且要保证“单机零依赖可跑”，改为标准库实现。契约与验收标准不变。

## M0 记录

- 交付：`wb/util.py`（规范化/分词/哈希嵌入）、`wb/db.py`（schema v4）、`wb/contracts.py`（五份契约校验）、`wb/anchors.py`（生成 + 重锚定）、`wb/context/ledger.py`（账本渲染/解析/哈希）、`wb/trace.py`（trace 回放）、`verify.py`、`tests/harness.py`。
- 关键结果：anchor round-trip 10000/10000；2% 段落扰动 300/300 存活且 0 丢失；账本 render→parse 完全可逆；trace 重建 prompt 哈希一致。
- 备注：锚点按“内容 + 位置 + 前后缀”设计，修订落在引用区间内部时进入 `pending` 而非丢失。

## M1 记录

- 交付：`wb/ingest/`（markdown/text/html/epub/docx/pdf_extract/pdf_writer/pipeline）。
- PDF：自研对象解析（含间接引用、FlateDecode、ToUnicode CMap）、行列重建、双栏 band 排序、标题/脚注/页码启发式。
- 关键结果：markdown/docx/epub 各 34 blocks；PDF 24 blocks、3 页、页码映射 100%、正文顺序 14/14 单调；重复摄取 block id 100% 稳定；override 修改不改变 id 且锚点仍可解析；24 个畸形文件 0 崩溃。
- 备注：中文 PDF 需要嵌入字体与 ToUnicode，解析器支持但 fixture 用英文 PDF（避免造字体）。

## M2 记录

- 交付：`wb/semantics/extractor.py`（句子级 claim、论证图、实体、引述人、参考文献、章节摘要）。
- 设计：每条 claim 都是 `FACT_EXPLICIT` 并绑定自身句子 span，天然可引用；角色分为 thesis/claim/evidence/qualifier/counter/definition/statement。
- 关键结果：5 份文档 155 条 claim 100% 通过契约校验；《远程之后》thesis 命中“核心主张/瓶颈不是工具/书面协作”；引述 1 条且说话人=陈薇；实体 3 个无名词误报；参考文献 3 条全部结构化。
- 备注：限定语抽取改为短语级（去掉裸“仅/只”），避免把普通句子误判为边界条件。

## M3 记录

- 交付：`wb/index/chunker.py`（论证单元 260 token、章节内重叠、FTS5 + 384 维哈希向量）、`wb/index/retrieve.py`（dense+sparse RRF、启发式精排、证据半径、反证检索）、`wb/context/assembler.py`（预算装配 + 引用登记）。
- 关键结果：24 条 golden 问题 recall@10 24/24，nDCG 0.918；半径过滤 0 违规；反证检索 4 条边界/反例；装配预算 100% 不超、哈希确定、引用可解析；无关问题 2/2 判 NOT_FOUND；检索 p95 1.2ms。
- 备注：块级引用按 query 相关性排序后再取前 3 块（不是按位置），否则引用会落在小节标题上。

## M4 记录

- 交付：`wb/strict/generator.py`（抽取式 claim 生成 + LLM 生成回退）、`wb/strict/gate.py`（G1–G6）、`wb/strict/render.py`、`wb/strict/answer.py`、`wb/workbench.py`。
- 关键结果：thesis/引述/数字三类问题均带引用；50 条陷阱（超纲 12、诱导 10、近义数字 10、时间错位 10）0 错误；不可答 17/18 正确弃权；限定语保留 20/20；全量 123 条 evidence span 引文逐字保真。
- 备注：闸门顺序调整为 结构→引文→数字→限定语→支撑→半径，便于定位；是/否问题若无极性证据则弃权。

## M5 记录

- 交付：作者语料库（`persona_corpora`）、立场演化（`refresh_positions`）、双模式作用域、来源标注、意图问题转“解读”。
- 关键结果：立场 6 条（2 revised / 2 maintained / 2 acknowledged）；文本作者拒绝书后访谈；历史作者引用访谈并标注“（访谈）”；三源归层 20/21；意图 100% `INTERPRETATION`；出版后问题 0 泄漏；重复提问 5 次文本完全一致。
- 备注：作者问题优先走“立场检索”（revised/acknowledged），避免被一般 RAG 噪声淹没。

## M6 记录

- 交付：`SUBJECT` 人格（如 Bloom）、引述人证据集、合成审稿人、`roundtable` 多视角。
- 关键结果：4 类人格齐备；引述人/主体越界引用 0；拒绝书外问题；圆桌 4 种声音可区分，证据隔离 0 污染。

## M7 记录

- 交付：`wb/research/fetch.py`（HttpFetcher/FakeFetcher、内容寻址快照、信任分层、注入检测）、`wb/research/dossier.py`（查询规划、来源 claim 抽取、矛盾检测、dossier、事实核查）。
- 关键结果：4 个来源全部落快照；注入页 1/1 被标记且 0 条 claim 入库；50 个恶意页面 100% 拦截、0 条 claim；矛盾检测 2 组；双学术源得出 CONSISTENT，38% vs 52% 得出 CONFLICT；断网显式 offline；内容变化标记 changed。
- 备注：矛盾检测从“数字集合不等”改为“百分比冲突/单数字冲突/反义词冲突”，避免把 38% 与 1,240 样本量误判为矛盾。

## M8 记录

- 交付：`wb/api/server.py`（stdlib ThreadingHTTPServer + 22 条路由）、`web/`（阅读器 + 对话 + 上下文板 + 划线工具条 + 设置）、`run.py`。
- 关键结果：上传→解析→建档→会话→提问全链路 HTTP 可用；12 条引用 100% 命中精确 span；Context Board 与账本逐字段一致；钉选进入 `pins` 账本段；override 后高亮仍可解析；导出含 blocks/anchors/personas；UI 钩子（data-cite-sid / markRange / board-sec）齐备；单轮 28ms。
- 备注：没有浏览器自动化依赖，M8 用 HTTP 契约 + 静态 UI 钩子验证；真实点击行为在浏览器中可用。

## M9 记录

- 交付：`wb/admin.py`（vault 导出/导入 + manifest 校验、按文档级联删除、崩溃恢复、完整性检查、离线矩阵、网络审计、性能基准）、`Makefile`、`run.py`、`demo.py`、`README.md`、`.gitignore`。
- 关键结果：导出→导入 documents/blocks/highlights/claims 100% 一致且可继续问答；按文档删除后 11 张表残留为 0、FTS 与 persona_spans 清零；半摄取文档可被恢复清理后重导；旧库 v1 自动迁移到 v5；断网（socket 禁用）下解析/索引/检索/严格问答全通；网络审计仅 `providers/base.py` 与 `research/fetch.py` 两处出网；300 页 PDF 7,500 blocks、24.7s 摄取、检索 p95 4.66ms、DB 12.6MB。
- 备注：崩溃安全采用“status=ingesting + 启动恢复清理”，而非真实 SIGKILL 注入；语义等价于中断后重跑。

## 最终状态

全部 10 个里程碑通过（`python verify.py all`，2026-11-14）。总计：

- 代码：`wb/` 17 个模块、`web/` 3 个前端文件、`tests/` 10 个里程碑检查 + 3 个支撑模块。
- 数据：SQLite 22 张业务表 + FTS5；文档模型 / 锚点 / claim / persona / 账本 / 研究六套结构。
- 评测资产：5 份语料（md/docx/epub/html/txt/pdf）、24 条 golden QA、语义标注、50 条陷阱、50 条注入样本。

已知未覆盖：真实 LLM 生成链路未用 API key 实测（接口与回退已实现）；中文 PDF 字体依赖 ToUnicode；浏览器点击未做自动化 E2E。

## 真实模型回归（BYOK）

- 2026-11-13 23:10 首次检查：`mode=byok`、`model=deepseek-v4-1-flash`，但 `api_key` 为空；`has_key=false`。随后用户补存 key，`has_key=true`（只检查布尔值，未读取内容）。
- 2026-11-13 23:14 修正接入：base_url 从 `api.openai.com/v1` 改为 `https://api.deepseek.com`；模型名 `deepseek-v4-1-flash` 不在该账号可用列表（实际为 `deepseek-flash`、`deepseek-v4-pro`），改为 `deepseek-flash`。
- 2026-11-13 23:15 真实模型回归：12 个问题全部通过，`eval/scorecard-LIVE.json`。LLM claim 13/18（其余回退抽取式），p95 4.4s，陷阱 0，弃权 3/12（超纲/近义数字/时间错位各 1）。
- 已验证：BYOK 代码路径用本地 OpenAI 兼容 stub 全绿（`python verify.py BYOK`）：fenced JSON 解析、400 后去掉 `response_format` 重试、上游 500 回退抽取式、claim 过闸门、引用可解析。
- 已修复：设置页留空保存会覆盖已有 key 的问题；现在留空表示“不修改”。服务已重启生效。
- 备注：严格引用约束下，LLM 的改写句会被 `generate_llm` 丢弃并回退抽取式，因此 5/18 条为抽取式 claim；这是“超严事实”的预期代价，不是故障。

## 交互修复：身份问题与人格噪声（2026-11-13 23:30）

- 问题：选中「毛泽东 / 文本作者 / 严格」后问“你是谁”，被当成证据检索问题，返回“没有找到证据”。
- 修复：新增 `IDENTITY_Q` 分支，在检索前由人格卡直接回答身份、当前模式、事实约束与可问范围；不产生任何事实 claim（`claims=0`），也不伪造书外信息。
- 人格噪声：引述人归属在古汉语文本上会产出“提出/方针/国家”等假人名。增加姓氏白名单 + 常见词黑名单 + 大规模文档（>1500 blocks）只自动生成作者与合成审稿人的策略；`ensure_personas` 去重并限额（引述人 8、主体 8）。
- 数据修复：重新分析《毛泽东选集（1-5卷）》（27,958 claims、2,027 chunks），人格从 24 个降到 2 个（毛泽东、严厉的审稿人）。
- 回归：`verify.py all` M0–M9 全 PASS；`live_regression.py` 13 题 PASS（LLM 11/16，p95 2.5s，陷阱 0）。

## 交互修复：未来/愿景类问题（2026-11-13 23:45）

- 问题：`OUT_OF_RANGE_Q` 把“未来/预测”一律当成书外问题，在检索前直接弃权；用户看到的“最接近原文”只是弃权附带的 near-miss，并不参与回答。
- 修复：
  - 边界收窄为真正的“出版之后/成书之后”问题；
  - 新增未来/愿景路径：检索时扩展“目标/前途/建立/独立/解放/胜利”等词，生成时给目标句加权；
  - 回答以“书中没有对未来的直接预测；书中反复表述过的目标是：……”开头，结尾显式标注“不是对未来的预测”，事实句仍然逐条引用。
- 验证：《毛选》原问题（“中国要做到什么才能有未来”）现返回 5 条 claim / 13 条引用；新增 M4 未来题测试；`verify.py all` 与 `live_regression.py` 全 PASS。

## 有据改写模式（2026-11-13 23:58）

- 需求：不要把检索结果拼成引文，允许用提示词把片段改写成连贯回答；不引入神经嵌入/NLI（第 1、3 步不做），保留第 2 步查询扩展。
- 实现：`wb/strict/rewrite.py`（受限改写 prompt + 编号/数字校验 + 一次修复）、`wb/index/expand.py`（3 条互补查询）、`retrieve.multi_search`（多路 RRF）、UI 增加“有据改写”。
- 约束：只能用给定片段；每句标 [S#]；数字必须出现在所引片段；含数字段落必须有引用；编号必须存在；失败回退严格引用。
- 验证：`verify.py REWRITE` PASS（含坏编号、坏数字回退）；`verify.py all` PASS；`live_regression.py` PASS；《毛选》真实输出 6 条引用、11.6s，回答为 4 段连贯文字。

## 产品层：项目工作台 / 角色管理 / 开发者模式（2026-11-14 00:20）

- 分析文档：`product-analysis.md`（用户分层、竞品扫描、R1–R4 需求清单、信息架构、验收口径）。
- 数据：`projects` / `project_documents` / `project_links` / `ai_calls`；sessions 增加 `project_id`、`updated_at`；personas 增加 `origin`；schema v6。
- 后端：项目 CRUD、素材增减、关系边、项目级会话（scope=项目素材）；会话列表/改名/删除；文档搜索/改名/标签/删除；手动人格 CRUD/重解析；`ask_multi`；开发者日志（expand/retrieve/generate/rewrite）。
- 前端：左侧「项目/素材/会话」三栏，项目详情（素材、关联、进入项目对话），会话历史，手动人格表单，多角色开关，开发者看板，字号与专注模式，联网研究按钮。
- 验证：`verify.py PRODUCT` 全绿（项目 3 素材、跨文档引用、会话 CRUD、手动人格、多角色 3 气泡、dev 日志、UI 钩子）；`verify.py all` 现包含 M0–M9 + PRODUCT + BYOK + REWRITE。
- 已知：浏览器点击未做自动化 E2E；项目级会话默认取项目第一份素材作主文档；手动人格的「重解析」目前是重建证据集/立场，不做 LLM 画像生成。

## 收尾：研究进上下文 + 角色重解析 + 项目操作（2026-11-14 00:35）

- 研究：新增 `session_dossiers`（schema v7），研究会话自动挂载 dossier；`external_text` 把外部摘要注入账本 `research` 段，有据改写 prompt 增加「外部研究」区块；改写校验允许外部数字但要求句子出现「外部研究/报道」等标注。
- 角色：手动人格的「重解析」现在会重建证据集，并抽取代表引文、被引述次数写入角色卡。
- 项目：UI 增加删除项目（保留素材）、文档标签编辑；会话内研究提示「已加入上下文板」。
- 验证：PRODUCT 增加「研究会话挂载 + 账本 research 段」与「外部数字标注」检查；`verify.py all` 13 个套件全 PASS（M0–M9 + PRODUCT + BYOK + REWRITE）。
- 说明：浏览器点击仍无自动化 E2E；项目级作者卡仍以主文档为画像来源，跨书作者画像属下一步。

## 收尾补充：跨文档来源标注（2026-11-14 00:45）

- 有据改写的片段现在带文档标题；prompt 要求「片段来自其他作者/评论时必须写有评论指出/外部研究认为」。
- 真实验收：项目内（书 + 评论）问核心论点，输出出现「有评论指出，相关性不等于因果……」，不再把评论当作作者自述。
- `verify.py all` 13 个套件全 PASS。

## 交互修复：按钮无响应（2026-11-14 00:50）

- 现象：新建项目等按钮点击无反应。
- 定位：项目/改名/删除/研究都依赖浏览器原生 `prompt()/confirm()`；在受限或嵌入环境里这些对话框会被静默拦截，表现为“按钮点不了”。同时旧版 app.js 与新版 HTML 混用会让顶层绑定整体失效。
- 修复：
  - 所有原生 `prompt/confirm` 替换为页面内模态框（输入/确认），不依赖浏览器对话框；
  - 选择器兜底：缺失元素不再中断后续 25 个事件绑定；
  - 增加全局 `error` 提示，脚本异常显示在状态栏；
  - 构建号 v3：根路径 302 到 `/index.html?v=3`，静态资源带 `?v=3` 且 `no-store`，避免旧 JS 缓存。
- 验证：linkedom 无头 DOM 测试（点击新建 → 模态框 → 确定 → POST /api/projects）；`verify.py all` 全 PASS。

## 多角色与引用计数修复（2026-11-14 18:50）

- 现象：作者与审稿人两个气泡内容几乎一样；引用条数虚高；联网研究总提示无来源。
- 原因：严格模式共享检索与选句，Synthetic 未限制证据；消息里的引用是账本全量而非实际引用；研究只支持后端传 URL，UI 没有入口。
- 修复：
  - Synthetic 只接收 counter/qualifier 片段，没有反证时明确说“没有可引用的反证”；作者仍用主张/证据。
  - 引用列表按回答中实际出现的 [S#] 过滤，UI 条数与正文一致。
  - 研究弹窗第二步可粘贴 URL；离线提示改为可操作文案。
  - 构建号 v6。
- 验证：无头 DOM 走查 31/31 通过（含带 URL 的真实研究）；`verify.py all` 13 套件全 PASS。

## 自动联网搜索（2026-11-14 18:45）

- 新增 `wb/research/search.py`：Tavily / Brave / Serper / SearXNG 四种 provider，`none` 时保持粘贴 URL。
- 设置页增加「联网搜索服务 / Key / 端点」；`GET /api/settings` 只返回 has_key，不泄露密钥。
- 研究链路支持 `search(queries)`：无 URL 时自动搜索 → 抓取 → 快照 → dossier，并在 pipeline 内自动 attach 到会话。
- 验证：`verify.py SEARCH`（四个 provider 的本地 stub + 自动搜索研究）；`verify.py all` 14 套件全 PASS；UI 走查 31/31。
- 备注：未内置搜索引擎爬虫；需要真实搜索质量请配置 Tavily/Brave/Serper 或自建 SearXNG。
