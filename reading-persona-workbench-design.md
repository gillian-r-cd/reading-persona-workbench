# 阅读角色工作台（Reading Persona Workbench）
## 需求解构、技术架构与初版实施方案

> v0.1 ｜ 2026-11-13

---

## 0. 一句话判断

这个产品的本质**不是"能聊天的电子书"**，而是：

> **以文本为唯一事实源、以角色为人格表达层、以联网研究为外部证据层，并把"当前上下文"变成用户可看、可编辑的一等对象的阅读工作台。**

三个子系统在技术上互相正交，只靠两样东西粘合：**稳定锚点（stable anchor）** 和 **引用（citation）**。如果这两个东西没设计对，后面所有能力——划线问答、角色扮演、RAG、Deep Research——都会各自为战，最后拼不起来。

因此真正的工程量分布大致是：

| 部分 | 占比 | 说明 |
|---|---|---|
| 文档解析与锚点体系 | 25% | 最脏、最容易被低估，但决定天花板 |
| 上下文管理与检索装配 | 30% | 产品的核心竞争力所在 |
| 角色建模与人格渲染 | 20% | 体验差异化的来源 |
| Deep Research 与研究材料治理 | 15% | 工程可控，风险在"污染"而非"搜不到" |
| 评测、可观测、调试工具 | 10% | 决定能不能持续迭代 |

---

## 1. 需求翻译：把产品语言转成系统语言

用户说的是四件事，工程上是四个对象模型：

| 用户语言 | 系统语言 | 真正要解决的问题 |
|---|---|---|
| "左边看素材" | 一个**可寻址的文档结构树** | 每个段落/句子/对白要有稳定 ID 和偏移量，重解析后不丢 |
| "右边和角色对话" | 一个**人格渲染层**，输入是证据，输出是第一人称话语 | 角色不能知道它不该知道的事，也不能说成通用助手 |
| "解析出角色的上下文" | 一个**角色知识图谱 + 角色卡** | 角色是文档的"视图"，不是文档的附属字段 |
| "联网做 Deep Research" | 一个**隔离的外部证据语料库** | 外部信息不能直接倒进人格 prompt |
| "划线然后问" | 一个**锚点对象（anchor）+ 上下文钉选（pin）** | 选区本身信息不足，需要自动扩窗 + 可回滚 |
| "上下文管理系统" | **预算化的上下文装配器** | 不是"塞进窗口"，是"按优先级组装并记账" |

### 关键抽象：Persona = (Voice, Knowledge Scope, Stance)

不要把它写死成"小说人物"。任何有"声音"和"视角"的实体都是角色：

- 小说/传记里的**人物**
- 书的**作者**（作者视角 ≠ 人物视角，知识边界完全不同）
- 论文的**作者**、被引用的**研究者**
- 商业文章里的**创始人、受访高管、案例主角、分析师**
- 甚至**合成角色**："一个挑剔的审稿人"、"2019 年的行业分析师"

这个泛化很重要，因为用户明确说了书籍、论文、商业文章三类素材。系统里角色的作用域应该是 `(documents[], narrative_position)`，而不是绑定在单本书上——作者角色天然跨书。

---

## 2. 要让它成立，必须先满足的 7 个条件

这 7 条是"必要条件"，缺一条产品就会在某个环节塌掉。

### C1. 文本必须可寻址（Addressability）

- 每个 block（段落/标题/对白/脚注/图注）有稳定 ID；每个 span 有 `(block_id, start, end)` 和文本哈希。
- ID 用**内容寻址 + 位置**的混合策略：`hash(normalized_text) + section_path + ordinal`，保证重新导入同一版本时锚点可复原。
- 重新解析、换版本、OCR 修正后，用"文本哈希 + 前后缀模糊匹配"做**重锚定（re-anchor）**，失败的高亮进"待确认"队列而不是静默丢失。

**没有这一条，划线、引用、跳转、评测全部做不了。** 这是第一优先级，也是最容易在早期被"先用纯文本凑合"毁掉的地方。

### C2. 文档必须结构化到"场景"粒度

段落级不够。角色扮演需要的是**场景（scene）**：谁在场、发生什么、在哪儿、什么时间线位置。

- 章节 → 场景（场景分隔符、POV 切换、时间跳转）→ 段落 → 句子。
- 对白要有**说话人归属（speaker attribution）**，并带置信度；低置信度宁可留空，不要瞎猜。
- 论文/商业文章用另一套结构：摘要/章节/论点/论据/图表/引用文献。

### C3. 角色必须被显式建模（Profile + Boundary + Relations）

角色卡不是一段自由文本 prompt，而是结构化 schema，每个字段可回溯到原文证据（见 §5.2）。其中**知识边界（knowledge boundary）**是最容易被忽略、又最决定体验的字段。

### C4. 上下文必须"装配"而非"拼接"

一次生成前，要有一个明确的装配阶段：选哪些片段、各自多少 token、优先级、去重、引用编号、超预算时砍谁。这个阶段必须是**可检查、可日志化、可复现**的。

### C5. 事实层与人格层必须解耦

**先说清事实，再决定怎么说。** 两步走：

1. **证据草稿（evidence draft）**：中性、有引用地回答"文本里到底怎么写的/外部资料怎么说"。
2. **人格渲染（persona render）**：把草稿转成角色口吻，保留引用。

好处：幻觉可测、引用可验、换角色不用重新检索、出戏可归因（是"事实错了"还是"口吻错了"）。

### C6. 外部信息必须隔离与标注

Deep Research 的产出不能以原文形式进人格上下文。必须先变成**结构化 dossier**（带出处、带时间、带可信度），以"外部证据"的独立标签注入。否则模型会把书评观点当成角色自己的记忆。

### C7. 必须可评测

角色扮演的质量如果不可度量，就只能靠感觉调 prompt，永远调不好。最小评测集：检索命中率、引用有效率、出戏率、剧透泄漏率、角色混淆率、声音相似度。

---

## 3. 五个关键判断（也是最容易踩的坑）

### 判断一：难的不是 RAG，是"知识边界"和"出戏"

朴素实现一定会出现这些失败：

- 角色说出它在本章还不知道的事（剧透 / 全知）。
- 角色用通用助手的语气说话（"很高兴为您解答"）。
- 多个角色声音趋同（都是同一个模型在说话）。
- 角色引用原文时引错、或编造引文。
- 角色把"书外的学术观点"当成自己的记忆。

解法不是"更好的 prompt"，而是三件事的组合：**检索范围受叙事位置约束 + 声音语料强条件 + 引用强制绑定**。

### 判断二：长上下文不能替代检索

即使模型支持 1M token，也不该把整本书塞进去：

- 成本与延迟不可接受；
- "lost in the middle" 导致关键段落被忽略；
- 无法给出精确引用；
- 无法做版本对比和增量更新。

正确姿势：**结构感知的小块检索 + 父级/场景扩展（small-to-big）+ 精排**。把"检索到的 8 个片段"扩展成"3 个完整场景"，比塞 200 个碎片更有效。

### 判断三：可信度来自"分层可见"

一个回答里的信息其实有五种来源，UI 和 prompt 都必须能区分：

| 层 | 含义 | UI 表现 |
|---|---|---|
| L1 Canon | 原文明确写了 | 引用到具体 span，可跳转 |
| L2 Character Belief | 角色以为/相信的（可能是错的） | "以角色之口"标记 |
| L3 Author Intent | 作者/叙述者的意图（需外部资料支撑） | 切"作者视角"才出现 |
| L4 Scholarship | 书评、论文、访谈等外部观点 | 独立引用样式 + 来源域名 |
| L5 Model Inference | 模型自己的推断 | 显式标注"推测" |

**这张表就是产品的信任基础设施。** 用户随时知道"这句话是书里说的，还是这个角色以为的，还是网上某人说的"。

### 判断四：Deep Research 的风险是"污染"，不是"搜不到"

联网检索本身是成熟工程。真正的风险是：抓回来的网页里可能包含 prompt injection、错误信息、剧透、以及和原著冲突的解读，一旦混进人格上下文，角色就"精神分裂"了。

所以研究子系统必须是**单向阀**：外部 → 结构化 dossier → 人工/规则审核 → 带标签的检索语料。绝不"抓来即用"。

### 判断五：划线问答是上下文管理最好的入口

全自动检索永远有误差，而**用户手工钉选（pin）是零误差的**。所以产品应该把"上下文"做成可见对象：

> **Context Board（上下文板）**：中间可折叠一栏，显示"这个角色现在带着什么"——钉选的段落、角色卡、已建立的事实、研究卡片。用户可以增删、可以看到 token 占用。

这既是功能，也是解释性（"它为什么这么回答"），还是调试工具。我认为这是这个产品最值得做的差异化。

---

## 4. 系统架构

```
┌────────────────────────────────────────────────────────────────────┐
│  前端：阅读器（EPUB/PDF/HTML） + 对话面板 + Context Board + 标注层    │
└───────────────┬────────────────────────────────┬───────────────────┘
                │ SSE / WebSocket                │
┌───────────────▼────────────────────────────────▼───────────────────┐
│  API 网关（会话、鉴权、配额、流式转发）                              │
└───┬──────────────┬──────────────┬──────────────┬───────────────────┘
    │              │              │              │
┌───▼────┐  ┌──────▼──────┐  ┌────▼─────┐  ┌─────▼──────────┐
│ 解析服务│  │ 索引与检索  │  │ 编排引擎 │  │ 研究代理        │
│ Ingest │  │ Retrieval   │  │ Director │  │ Research Agent  │
└───┬────┘  └──────┬──────┘  └────┬─────┘  └─────┬──────────┘
    │              │              │              │
┌───▼──────────────▼──────────────▼──────────────▼───────────────────┐
│  数据层：文档模型 / 向量 / 全文 / 图 / 会话 / 研究档案 / 评测         │
└────────────────────────────────────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────────────────────┐
│  模型层：LLM（强/弱路由）、Embedding、Reranker、OCR、NLI 校验        │
└────────────────────────────────────────────────────────────────────┘
```

### 4.1 模块职责

| 模块 | 职责 | 关键产出 |
|---|---|---|
| **Ingest** | 格式适配、OCR、阅读顺序重建、结构识别、规范化 | 规范文档树 + 锚点 |
| **Enrichment** | 场景切分、实体/别名、对白归属、角色卡、关系图、时间线 | 结构化增强层 |
| **Index** | 分层分块、向量、全文、图、实体索引 | 可过滤的多路索引 |
| **Retrieval** | 查询改写、多路召回、融合、精排、父级扩展 | 带引用的证据包 |
| **Director** | 意图识别、路由、计划、工具调用 | 执行计划 |
| **Context Builder** | 预算分配、优先级、去重、引用编号、渲染 | 最终 prompt + 引用表 |
| **Persona** | 角色卡编译、声音渲染、边界守卫 | 第一人称输出 |
| **Research** | 查询规划、抓取、抽取、聚类、dossier 合成 | 隔离的外部证据 |
| **Memory** | 会话账本、事实登记、跨会话偏好 | 一致性的来源 |
| **Eval/Obs** | 黄金集、LLM judge、trace、成本 | 可迭代性 |

### 4.2 一条重要的架构原则

> **证据层必须与人格无关（persona-agnostic），人格层只是渲染器。**

这意味着 `search_canon(query, scope, boundary)` 的返回结果里不含任何"角色语气"，只有证据和被引用的片段。任何角色都可以复用同一份证据包。这直接带来：

- 同一段原文，切换三个角色问，检索只做一次；
- 引用校验只需要在证据层做一次；
- 角色声音的 A/B 测试不影响事实正确性。

---

## 5. 关键流水线

### 5.1 解析与锚点（最脏的一段）

**格式分层处理，不要一套 parser 打天下：**

| 格式 | 方案 | 质量 | 备注 |
|---|---|---|---|
| EPUB | 解析 spine + XHTML，天然有章节结构 | ★★★★★ | **强烈建议优先支持**，成本最低质量最高 |
| 原生 PDF | 版面分析模型（Docling / MinerU / Marker 一类）+ 阅读顺序重建 | ★★★☆ | 双栏论文、脚注、图表是难点 |
| 扫描 PDF | 上述 + OCR（PaddleOCR / Tesseract） | ★★☆ | 必须保留页码锚点 |
| DOCX/MD/HTML | 直接结构化 | ★★★★★ | |
| 网页文章 | 正文抽取（trafilatura / readability）+ 快照 | ★★★★ | 必须存快照，否则引用会失效 |
| 论文 | 优先 arXiv LaTeX 源，其次 PDF | ★★★★ | LaTeX 源的结构远好于 PDF |

**规范化后的文档模型：**

```
Document
 └─ Section (chapter / part)
     └─ Scene (可选，小说必需)
         └─ Block (heading | paragraph | dialogue | footnote | caption | table | quote)
             └─ Sentence
                 └─ Span  ← 高亮、引用、锚点都落在这里
```

**锚点序列化格式（参考 W3C Web Annotation）：**

```json
{
  "block_id": "b_7f3a...",
  "start": 128, "end": 186,
  "exact": "他从未真正相信过任何人",
  "prefix": "在离开之前，",
  "suffix": "，包括他自己。",
  "text_hash": "sha256:...",
  "page": 142,
  "doc_version": 3
}
```

重锚定策略：精确匹配 → 哈希匹配 → 前后缀模糊匹配 → 人工确认队列。

**解析质量必须能被人工修正**：提供一个"段落合并/拆分/说话人纠正"的轻量编辑器。解析错误不修正，后面所有 RAG 都是在垃圾上做检索。

### 5.2 角色上下文抽取（用户说的"解析出角色的上下文"）

分四遍跑，逐层收敛，每层都可缓存、可增量：

**Pass 1 — 实体与别名**
- LLM + 规则在所有章节上抽人物/组织/地点；合并别名（"林黛玉 / 黛玉 / 颦儿"）。
- 产出：`entity(id, canonical_name, aliases[], type, first_appearance)`

**Pass 2 — 证据归集**
- 每个角色的所有提及段落、所有归属到他的对白、他出现的场景列表。
- 产出：`character_evidence(character_id, span_id, kind)`，kind ∈ {mention, dialogue, action, description, relation}

**Pass 3 — 角色卡合成（结构化 + 引用）**

```yaml
character:
  id: char_hamlet
  canonical_name: Hamlet
  aliases: [Prince of Denmark, 哈姆雷特]
  voice:
    register: 书面、哲思、多疑、善用反问与双关
    avg_utterance_len: 长句为主，独白极长
    tics: ["To be or not to be", 大量隐喻]
    exemplars:            # 8–15 条真实台词，最重要
      - span_id: s_1021
        text: "..."
    avoid: ["现代网络用语", "直接说教", "自称 AI"]
  traits:
    explicit: [忧郁, 迟疑, 机敏]      # 原文明确
    inferred: [自我厌恶]              # 标注为推断
  relations:
    - target: char_ophelia
      type: 恋人
      tension: 爱与利用并存
      evidence: [s_889, s_1204]
  motives: [复仇, 验证叔父的罪行]
  knowledge_boundary:
    narrative_position: act_3_scene_1
    knows:   [父王之死是谋杀, 戏班可用]
    unaware: [自己最终会死, 叔父的结局]
    rules: ["不提及本时点之后的事件", "被追问未来时以角色身份回避"]
  taboos: [不打破第四面墙, 不讨论"我是虚构人物"除非用户显式要求]
```

**Pass 4 — 关系图与时间线**
- 角色关系边（含张力描述与证据）、事件时间线（谁在何时何地做了什么）。
- 这张图在检索时用于"关系扩展"：问"A 和 B 的关系"时，直接拉边 + 边的证据，而不是靠向量碰运气。

**关键点：角色卡是"文档的视图（view）"，不是"文档的副本"。** 重新解析文档时，角色卡可以增量重建；用户手工编辑过的字段优先级最高，且被版本化保存。

### 5.3 检索与上下文装配

**查询理解先于检索。** 用户问"他怎么看待背叛"时，需要补出：主体（当前角色）、范围（该角色出现的场景）、叙事边界（不能含第三章之后）、关键词（"背叛"的近义表达）。

```python
def build_context(session, user_turn):
    # 1) 解析意图与范围
    intent = classify(user_turn)           # chat | explain | evidence | research | compare
    persona = session.active_persona
    scope = Scope(
        documents=persona.documents,
        chapter_max=persona.narrative_position,   # 剧透闸门
        characters=[persona.character_id],
    )

    # 2) 多路召回（并行）
    q = rewrite(user_turn, persona)        # 补全代词、补全实体、生成 2-3 个变体
    dense  = vector_search(q, scope, k=40)
    sparse = bm25_search(q, scope, k=40)
    graph  = graph_expand(q, scope)        # 关系 / 场景成员 / 时间线
    pinned = session.pins                  # 用户钉选，权重最高

    # 3) 融合 + 精排 + 父级扩展
    fused = rrf([dense, sparse, graph])
    top   = rerank(q, fused, k=12)
    evid  = expand_to_parent(top, level="scene")   # small-to-big
    evid  = dedupe_and_diversify(evid)             # MMR，避免同质片段

    # 4) 预算装配（优先级从高到低）
    budget = Budget(total=model_ctx - reserve)
    ctx = Section()
    ctx += persona.card.to_prompt()                 # 固定，约 1.2k
    ctx += pinned.to_prompt()                       # 用户意图，必留
    ctx += session.ledger.to_prompt()               # 已确立事实
    ctx += evid.to_prompt(budget.allocate(0.45))    # 证据
    ctx += session.research_dossiers.to_prompt(0.15)
    ctx += session.dialogue_summary.to_prompt(0.15)
    ctx += recent_turns(4)
    # 5) 引用登记
    return CitationRegistry.bind(ctx)
```

**预算分配的经验值（32k 上下文窗口）：**

| 内容 | 占比 | 说明 |
|---|---|---|
| 角色卡 + 规则 | 5% | 固定开销 |
| 钉选上下文 | 10–20% | 用户显式意图，不可砍 |
| 会话账本 | 5% | 防前后矛盾 |
| 检索证据 | 40–45% | 主体 |
| 研究 dossier 摘要 | 10–15% | 有研究时 |
| 对话摘要 + 近轮 | 15% | |
| 输出预留 | 10% | |

**两个工程细节：**
1. **引用编号要在装配阶段生成**，不要靠模型自己编 `[1][2]`。给每个片段分配 `S1..Sn`，模型只能引用已存在的编号，输出后做一次"引用存在性 + 支持性"校验。
2. **关键证据放头尾**（lost-in-the-middle），且同一场景的片段合并成一个块，减少碎片。

### 5.4 一次对话的完整时序

```
用户输入
  → Director 判意图（闲聊/解释/求证/对比/研究/找原文）
  → 若是"求证/解释"：走证据草稿（中性回答 + 引用）
  → 若是"角色闲聊"：走人格渲染（证据包 → 角色口吻）
  → [可选] 触发 Research 工具（需用户确认或显式按钮）
  → Context Builder 装配 + 引用登记
  → Persona Renderer 生成（流式）
  → 输出后校验：引用有效性 / 剧透扫描 / 角色一致性
  → 落库：消息、引用、消耗、trace
  → 前端：答案 + 引用徽标 + 可展开的"证据与上下文"
```

**剧透扫描**是一个很便宜的兜底：用规则 + 小模型检查输出是否包含 `narrative_position` 之后的事件关键词（从后续章节的角色卡/事件表里提取）。命中则重写。

### 5.5 划线 → 提问

这是最高频的交互，必须做到 300ms 内出工具条。

```
选区
 → 锚点化（block + offsets + hash）
 → 自动扩窗（句 → 段 → 场景），并在 UI 明示"将连同上下文一起发送"
 → 快捷动作：问此角色 / 问作者 / 解释 / 查外部资料 / 钉入上下文
 → 组装：锚点原文 + 扩窗上下文 + 相关场景检索结果
 → 生成（默认走"解释"路径，而非角色闲聊）
```

设计要点：
- **扩窗必须可见**，否则用户不知道模型看到了什么，信任感会崩。
- 划线本身要能"钉住"，跨多轮保留——这就是 C4 里说的用户手工上下文管理。
- 支持"多选合并提问"（选中三段，问"这三处矛盾吗"），这是做研究型阅读的高价值动作。

### 5.6 Deep Research 管线

```
触发（用户按钮 / Agent 工具调用，需理由）
 → 规划：把"了解这个角色"拆成子查询
     · 作者生平与创作背景
     · 学术批评与解读（角色 X 的争议）
     · 书评与读者接受
     · 改编/衍生作品
     · 访谈、演讲、档案
 → 检索：多来源并行（搜索引擎 API + 学术库 + 百科 + 出版社/图书馆）
 → 抓取：正文抽取 + **快照存档**（引用必须可复现）
 → 抽取：claim 级抽取（主张 + 出处 + 引文 + 日期）
 → 治理：去重、聚类、矛盾检测、可信度分层、注入检测
 → 合成：Dossier（结构化，≤ 800 字 + 引用列表）
 → 入库：作为 external corpus，带 trust_tier 与时间戳
 → 注入：只在需要时以"外部证据"标签进入上下文
```

**Dossier 结构：**

```yaml
dossier:
  id: d_2026_11_13_hamlet
  subject: {type: character, name: Hamlet, work: "Hamlet"}
  queries: [...]
  sections:
    - heading: 学界对哈姆雷特延宕的三种解释
      claims:
        - text: "心理分析派认为延宕源于俄狄浦斯情结"
          source: {url: "...", title: "...", author: "...", date: "1998", tier: academic}
          confidence: medium
          conflicts_with: [c_12]
  open_questions: [...]
  freshness: 2026-11-13
  trust_summary: {academic: 4, review: 6, blog: 3}
```

**注入策略（三档，用户可选）：**

| 模式 | 行为 | 适用 |
|---|---|---|
| 纯入戏 | 只用原著，不引入外部信息 | 沉浸式角色扮演 |
| 入戏 + 转述（默认） | 角色可以说"我听说后世有人这样评价我……"，但必须标注是外部说法 | 大多数场景 |
| 出戏分析 | 角色退场，切到"作者/评论家"人格，直接讨论研究材料 | 学习、写作、研究 |

**安全与合规：**
- 抓取内容一律视为**数据**，剥离其中的指令性文本（防 prompt injection）。
- 域名白名单/黑名单 + robots + 速率限制 + 缓存。
- 版权：只存必要片段用于引用与摘要，不整本复制；对外导出时限制长引用。
- 每个 claim 必须可点回原始 URL 与快照时间。

---

## 6. 数据模型（核心表）

```sql
-- 文档与结构
works(id, title, author, language, metadata_json)
editions(id, work_id, isbn, publisher, year, format)
documents(id, edition_id, version, source_uri, parse_status, checksum)
sections(id, document_id, parent_id, kind, title, ordinal, path)
blocks(id, section_id, kind, ordinal, text, page_from, page_to, attrs_json)
sentences(id, block_id, ordinal, start, end)

-- 角色与知识
entities(id, document_id, type, canonical_name, aliases_json, first_seen)
characters(id, entity_id, card_json, version, edited_by_user)
scenes(id, section_id, ordinal, summary, pov_character_id, time_marker)
scene_members(scene_id, character_id, role)
relations(id, src_character_id, dst_character_id, type, tension, evidence_span_ids)
events(id, scene_id, description, participants_json, story_time)

-- 检索
chunks(id, document_id, block_ids, level, text, token_count, embedding)
chunk_fts(chunk_id, text)                       -- FTS5 / tsvector
entity_mentions(entity_id, span_id, kind)

-- 用户产物
highlights(id, document_id, anchor_json, color, note, created_at, status)
pins(session_id, target_type, target_id, added_at, weight)
notes(id, document_id, anchor_json, body, tags)

-- 会话
sessions(id, document_ids, active_character_id, narrative_position, mode)
messages(id, session_id, role, content, citations_json, meta_json, created_at)
ledger(session_id, fact, source, established_at)   -- 已确立的"设定"
summaries(session_id, upto_message_id, content)

-- 研究
research_jobs(id, session_id, subject, status, started_at)
sources(id, url, title, fetched_at, snapshot_path, tier, content_hash)
claims(id, source_id, text, quote, confidence, contradicts_claim_id)
dossiers(id, subject, sections_json, freshness, trust_summary)

-- 评测
eval_sets(id, name, doc_id)
eval_items(id, set_id, question, expected_span_ids, rubric_json)
eval_runs(id, set_id, model, prompt_version, metrics_json)
```

**为什么用 SQL + 向量扩展而不是专用向量库？**
单用户/小团队场景，SQLite（WAL + FTS5 + sqlite-vec）或 Postgres（pgvector）足够，且事务、过滤、迁移都简单。角色关系图用邻接表 + 递归 CTE 就够，真到万级节点再考虑 Kuzu/Neo4j。**不要一上来就上重型基础设施**，这个产品的瓶颈在解析和编排，不在存储。

---

## 7. 技术选型

| 层 | 推荐 | 备选 | 取舍说明 |
|---|---|---|---|
| 形态 | 本地优先 Web 应用（FastAPI + React） | Tauri 桌面壳 | 本地优先利于版权与隐私；Tauri 后期打包 |
| 前端阅读器 | EPUB.js + 自定义标注层；PDF.js | ProseMirror 自渲染 | 标注需要能拿到文本层坐标 |
| 标注 | 自研 anchor（参考 W3C Web Annotation） | 现成 annotation 库 | 锚点模型必须自己掌控 |
| PDF 解析 | Docling / MinerU（版面+阅读顺序） | LlamaParse 等托管 | 托管质量高但有成本与隐私问题 |
| OCR | PaddleOCR | Tesseract | 中文质量差异明显 |
| 网页抽取 | trafilatura | readability-lxml | 快照必须落盘 |
| 存储 | SQLite + FTS5 + sqlite-vec | Postgres + pgvector | 单机够用，后期可平迁 |
| 图 | SQL 邻接表 | Kuzu（嵌入式） | 先简单 |
| Embedding | bge-m3（中英、长文） | multilingual-e5 | 必须中英双语 |
| Rerank | bge-reranker-v2-m3 | Cohere Rerank | 精排是效果分水岭 |
| LLM 路由 | 强模型做人格渲染；弱模型做抽取/摘要 | — | 成本差 10 倍以上 |
| 编排 | 手写类型化状态机 | LangGraph | **不要用隐藏上下文的框架** |
| 任务队列 | 后台 worker + SQLite job 表 | Celery/RQ | 富化任务是长任务，要进度事件 |
| 流式 | SSE | WebSocket | SSE 足够 |
| 评测 | 黄金集 + LLM judge + 人工抽检 | — | 必须和 prompt 版本绑定 |

**关于编排框架的强烈建议**：这个产品的核心资产是"上下文如何被组装"。任何把 prompt 组装藏在黑盒里的框架（大部分 Agent 框架都是）都会让你在调试时无从下手。用显式的、可打印中间态的代码，哪怕丑一点。

---

## 8. 成本与延迟预算（一本书，30 万字）

| 阶段 | 一次性成本 | 时间 |
|---|---|---|
| 解析（EPUB） | 几乎为 0 | 5–15s |
| 解析（原生 PDF） | 0（本地模型） | 30–90s |
| 场景切分 + 对白归属 | 20–40 次 LLM 调用（批量） | 1–3 min |
| 实体抽取 | 30–60 次调用 | 1–3 min |
| 角色卡合成（10 个主要角色） | 10–20 次调用 | 1–2 min |
| 嵌入（3000 chunks） | 本地 GPU 几分钟 / API 几美分 | — |
| **单轮对话** | 检索 200–600ms + 首 token 1–2s | — |
| **单次 Deep Research** | 10–30 次抓取 + 5–10 次 LLM | 30–90s |

**关键优化**：富化按需触发（用户点开某角色才生成该角色卡）、结果缓存、按文档版本增量重跑。不要一导入就全量跑完。

---

## 9. 评测体系（最容易被跳过、但决定生死）

| 指标 | 定义 | 目标 |
|---|---|---|
| Span Recall@10 | 黄金问题对应的证据片段是否被召回 | ≥ 0.85 |
| Citation Validity | 引用片段是否真的支持该句（NLI 判定） | ≥ 95% |
| Groundedness | 事实性陈述中有引用的比例 | ≥ 90% |
| Spoiler Leakage | 输出包含叙事位置之后事件的比例 | = 0 |
| Character Confusion | 声音/知识被误认为其他角色的比例 | ≤ 2% |
| Voice Adherence | 盲评"这像不像该角色"（1–5） | ≥ 4.0 |
| Parse Alignment | 段落边界与人工标注的一致率 | ≥ 95% |

做法：每本书人工标 30–50 条黄金问题（含"应该拒答"和"应该承认不知道"的负例），每次改 chunking / prompt / 模型都跑一遍回归。**这个集合就是你的护城河**，比 prompt 本身值钱。

---

## 10. 路线图

### P0（4–6 周）：验证核心假设
**范围**：EPUB/MD/TXT；单本书；3–5 个主要角色；混合检索；角色对话带引用；划线提问；钉选上下文；Context Board 雏形。
**不做**：PDF、Deep Research、多书、OCR。
**验收**：找 5 个真实读者，每人聊 20 轮，愿意继续用；引用可点回原文；零剧透。

### P1（6–8 周）：可用性
**范围**：原生 PDF；场景切分与对白归属；作者人格；Deep Research（含 dossier）；笔记与导出；会话账本；评测集跑通。
**验收**：Span Recall ≥ 0.8；引用有效率 ≥ 90%；研究结果可溯源到 URL + 快照。

### P2（8–12 周）：差异化
**范围**：扫描件 OCR；多书对比阅读；角色关系图可视化；时间线；多人格圆桌（同一问题让三个角色分别回答）；分享与协作。
**验收**：留存与付费意愿数据；单本解析成本可接受。

### 明确的非目标（至少 P0/P1 不做）
- 不做通用聊天助手（会稀释定位）。
- 不做社区/书评平台。
- 不做自动整本"改写/续写"（版权与质量双重风险）。
- 不做全自动多 Agent 自主研究（不可控、不可评测）。

---

## 11. 风险清单

| 风险 | 影响 | 对策 |
|---|---|---|
| PDF 解析质量差 | 一切下游失效 | 优先 EPUB；提供人工修正；解析质量可视化 |
| 角色"出戏" | 核心体验崩塌 | 真实台词做 few-shot；声音评测；边界守卫；剧透扫描 |
| 过度 grounding 导致无趣 | 用户觉得"不如直接搜" | 模式切换：入戏闲聊 vs 求证；默认允许合理创作但标注 |
| 上下文污染（研究材料） | 角色记忆混乱 | dossier 化 + 标签 + 单向阀 + 注入检测 |
| 富化成本高 | 单位经济模型不成立 | 按需 + 缓存 + 小模型 + 增量 |
| 版权 | 法律风险 | 用户自带内容；只存片段；不提供整本导出 |
| 上下文窗口幻觉 | 效果不稳定 | 显式预算 + 装配日志 + 回归评测 |
| 框架黑盒 | 无法调试 | 自研编排，中间态全可打印 |

---

## 12. 需要产品方先定的 5 个问题

1. **主战场是虚构类还是非虚构？** 小说的对白归属/场景切分/知识边界是重活；论文和商业文章更偏"论点-证据"结构。两者共用 70% 架构，但 P0 选一个做深。
2. **单机本地优先，还是云端多用户？** 直接决定解析放哪、模型怎么调、版权怎么处理。
3. **角色扮演的"事实约束"要多严？** 是"每句话都要有出处"，还是"允许合理演绎、只标注关键事实"？这是产品调性，不是技术选择。
4. **Deep Research 是显式按钮还是 Agent 自主触发？** 建议默认显式（可控、可解释、成本可见），自主作为高级选项。
5. **是否要"作者人格"作为独立一等公民？** 我建议要——它和角色人格的知识边界、语气、可用材料完全不同，且是学习/写作场景的高频需求。

---

## 附录 A：人格渲染 prompt 骨架

```text
[身份]
你是《{work}》中的 {character}。当前叙事时点：{narrative_position}。
你只知道这个时点之前发生的事。

[声音]
{voice.register}
典型句式：{voice.tics}
参考你的原话（保持这个语感，不要模仿内容）：
  - "{exemplar_1}"
  - "{exemplar_2}"
避免：{voice.avoid}

[你确信的事]
{knowledge.knows}

[你不知道的事]
{knowledge.unaware}
若被问及，以角色身份回避、搪塞或反问，不要编造，也不要承认自己是虚构人物。

[你与他人的关系]
{relations}

[本轮可用证据]（引自原文，编号 S1..Sn）
{S1} {scene_text}
{S2} {scene_text}

[外部研究]（非原著内容，仅在合适时转述，且必须说明是"后世/他人"的说法）
{dossier_summary}

[已经发生过的对话设定]（保持连贯，不要自相矛盾）
{ledger}

[规则]
1. 永远第一人称，保持角色口吻。
2. 涉及事实时优先依据 [本轮可用证据]，并在句末标注 [S#]。
3. 不主动提及 {narrative_position} 之后的事件。
4. 引用外部研究时明确说"我听说/后人认为"，不要当作自己的记忆。
5. 不知道就说不知道，用角色的方式表达，不要编。
```

## 附录 B：上下文装配的"账本"格式（用于调试）

```json
{
  "turn_id": "t_8821",
  "model": "...",
  "budget": {"total": 32000, "used": 28410},
  "sections": [
    {"name": "persona_card", "tokens": 1180, "source": "char_hamlet@v3"},
    {"name": "pins", "tokens": 2400, "items": ["h_221", "h_305"]},
    {"name": "ledger", "tokens": 410, "items": 7},
    {"name": "evidence", "tokens": 14200, "items": 9, "recall_paths": ["dense", "graph"]},
    {"name": "research", "tokens": 2100, "dossier": "d_hamlet"},
    {"name": "history", "tokens": 6100, "turns": 4},
    {"name": "reserve", "tokens": 3000}
  ],
  "dropped": [{"chunk": "c_991", "reason": "budget"}],
  "citations": ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"]
}
```

这份账本让"为什么它这样回答"变成可查的，而不是玄学。

---

## 附录 C：为什么"人格化"不能只靠 prompt

一个常见的误解是：写一段好的 system prompt，模型就能演好角色。实践中它会失效，因为：

1. **知识泄漏**：模型的参数里已经有整本书，它会不自觉使用后文信息。必须靠"检索范围过滤 + 输出扫描"，而不是靠"请不要剧透"。
2. **声音漂移**：多轮之后模型会滑回默认助手口吻。必须每轮重新注入声音样例，并用"声音检查"做后处理。
3. **角色混淆**：同时扮演多个角色时，边界会糊。必须一次只渲染一个人格，多人对话用"轮流渲染 + 共享证据包"。
4. **记忆不一致**：模型会忘记上一轮自己做的设定。必须外置成 ledger，每轮注入。

结论：**人格 = 检索约束 + 声音条件 + 外部记忆 + 输出校验，四件套，缺一不可。**
