# CodeLandscapeViewer 增强方案

> 从第一性原理推导："对人和 Agent 都友好的代码库理解系统"
> 基础：CodeLandscapeViewer（FastAPI + D3.js 力导向图）
> 基于 5 份深度调研文档合成
> 日期：2026-05-15

---

## 地基：基于 CodeLandscapeViewer 开发

### 为什么不从零造轮子

CodeLandscapeViewer 已经提供了完整的前后端骨架（MIT 许可），**核心循环已经跑通**：

```
浏览器输入路径 → POST /api/analyze → 遍历仓库 → 语言解析器 → Graph → JSON → D3.js 力导向图
```

我们的方案不是替换它，而是**在三层架构的每一层做增量扩展**。

### 现有代码 → 目标模块 映射

```
CodeLandscapeViewer 现状 (3,256行)               目标增强
─────────────────────────────────────           ──────────────────
backend/
  app.py                  FastAPI 入口    ──→   加 /api/search, /api/conventions, MCP server
    @app.post("/api/analyze")                    @app.post("/api/search")
    @app.get("/api/status")                       @app.post("/api/conventions")
    @app.get("/")                                 MCP: tools/list, tools/call

  analyzer/
    orchestrator.py      仓库遍历+分派    ──→   加 tree-sitter 解析器注册
      collect_files()         ✅ 复用            tree_sitter_analyzer.py (新增)
      analyze_repo()          ✅ 复用              ├─ ts_analyzer.py    (新增)
                                                  ├─ go_analyzer.py    (新增)
                                                  └─ rust_analyzer.py  (新增)

    graph.py             内存图模型      ──→   扩展为知识图谱
      Node {id, label,       ✅ 复用            Node 加: docstring, signature,
           type, file_path}                            embedding_vector
      Edge {source,          ✅ 复用            Edge 加: line_number, context
            target, type}
      Graph (nodes+edges)    ──重写──→    KnowledgeGraph (KuzuDB 持久化)
                                              ├─ 图遍历: N-hop neighbors
                                              ├─ 向量索引: embedding search
                                              └─ Cypher 查询接口

    python_analyzer.py   Python AST   ──→   保留，作为 tree-sitter 的补充
    js_analyzer.py       JS 正则      ──→   用 tree-sitter TS 解析器替换
    generic_analyzer.py  通用文件级    ✅    保留用于不支持的语言兜底

frontend/
  js/
    graph.js             D3力导向图    ✅    保留为默认视图
      Canvas渲染 11K+节点  ✅    复用            ✅ Canvas 渲染层完全复用
      缩放/拖拽/悬停       ✅    复用
    detail-panel.js      节点详情面板  ──→   扩展为完整侧边栏
      当前: 连接/依赖链                        添加: 调用链导航、源码预览、
                                                AI 解释、影响面计算
    sidebar.js           过滤+搜索     ──→   加自然语言查询、多视图切换
    app.js               主控逻辑      ──→   加视图路由器 (force → tree → matrix)

  新增:
    views/
      tree-view.js       层级树视图     (新增)
      matrix-view.js     邻接矩阵       (新增)
      sunburst-view.js   太阳图         (新增)
    code-panel.js         Monaco编辑    (新增, 嵌入 Monaco Editor)
```

### 复用 vs 新增 统计

| 层 | 复用 CodeLandscapeViewer | 新增 |
|----|--------------------------|------|
| **后端入口** | FastAPI 框架、静态文件服务、`/api/analyze` | `/api/search`、`/api/conventions`、MCP server |
| **解析** | `collect_files()`、`generic_analyzer`、Python AST | tree-sitter TS/Go/Rust/C++ 解析器 |
| **图模型** | `Node`/`Edge` dataclass | `KnowledgeGraph` (KuzuDB)、`Schema` 定义 |
| **检索** | 无 | ast-grep 集成、KuzuDB 内嵌向量索引 (HNSW) |
| **记忆** | 无 | Mnemosyne store/ (原子写入+supersede链)、`.agent-conventions.yaml` 生成器 |
| **前端渲染** | Canvas D3 力导向图、缩放拖拽、悬停高亮 | 层级树、邻接矩阵、太阳图视图 |
| **前端交互** | `detail-panel.js` (30%) | 完整侧边栏、自然语言搜索、Monaco Editor |
| **Agent接口** | 无 | agent-toolkit CLI+MCP 骨架 (CommandRegistry, ToolRegistry, Crash-restart) |

### `_ref/` 仅开发时参考，可读可复制，发布前删除

本项目是 CodeLandscapeViewer 的 fork。其他仓库**克隆到 `_ref/` 仅作为开发时的参考**，
需要哪些源码，从 `_ref/` 复制到项目模块中自由调整。开发完成后项目完全独立。

```
开发阶段 (一次性):
  git clone <各参考仓库> → _ref/
  cp -r _ref/kuzu/tools/python_api/ → backend/graph/kuzu_store/
  cp -r _ref/mnemosyne/src/mnemosyne/store/ → backend/memory/store/
  cp -r _ref/agent-toolkit/src/agent_toolkit/ → backend/toolkit/
  ... 抄完即完工 ...

发布后 (独立项目):
  _ref/ 可删除
  所有代码在 backend/、frontend/ 中自包含
```

### 一次完整开发流程

```bash
# 1. 创建项目目录
mkdir code-understanding-system && cd code-understanding-system
git init

# 2. 拉取地基 — CodeLandscapeViewer 全部内容直接成为项目代码
git clone https://github.com/glenwrhodes/CodeLandscapeViewer.git /tmp/clv-temp
cp -r /tmp/clv-temp/{backend,frontend,requirements.txt,LICENSE} .
rm -rf /tmp/clv-temp

# 3. 建 _ref/ 拉参考源（仅开发时用）
mkdir _ref
git clone https://github.com/tree-sitter/tree-sitter.git _ref/tree-sitter
git clone https://github.com/ast-grep/ast-grep.git _ref/ast-grep
git clone https://github.com/kuzudb/kuzu.git _ref/kuzu
git clone https://github.com/xinzhuwang-wxz/agent-toolkit.git _ref/agent-toolkit
git clone https://github.com/xinzhuwang-wxz/Mnemosyne.git _ref/mnemosyne

# 4. 从 _ref/ 抄需要的源码到项目模块
cp -r _ref/kuzu/tools/python_api/ backend/graph/kuzu_store/
cp -r _ref/mnemosyne/src/mnemosyne/store/ backend/memory/store/       # 原子写入+supersede链
cp -r _ref/mnemosyne/src/mnemosyne/retrieval/ backend/search/adaptive/  # 诊断回退模式
cp -r _ref/agent-toolkit/src/agent_toolkit/ backend/toolkit/           # CLI+MCP 骨架
# ... 按需抄，抄完即完工

# 5. 开始开发 — 改的都是项目自己的代码
# _ref/ 仅参考，不改
# backend/、frontend/ 是我们开发的主战场
```

**最终项目目录**：
```
code-understanding-system/
  ├─ backend/                    ← 地基 (原 CodeLandscapeViewer) + 我们的扩展
  │     ├─ app.py                    FastAPI 入口（已有 + 新增路由）
  │     ├─ analyzer/                 原解析器（待替换为 tree-sitter）
  │     ├─ graph/                    Node/Edge 模型（扩展为 KuzuDB）
  │     ├─ search/                   ← 新增：检索引擎
  │     ├─ memory/                   ← 新增：记忆层
  │     └─ mcp_server.py            ← 新增：MCP 接口（继承 agent-toolkit Tool 基类）
  ├─ frontend/                   ← 地基 (原 CodeLandscapeViewer) + 我们的扩展
  │     └─ js/
  │           ├─ graph.js            D3 力导向图（复用）
  │           ├─ detail-panel.js     节点面板（扩展）
  │           ├─ views/              ← 新增：多视图
  │           └─ code-panel/         ← 新增：Monaco 编辑器
  ├─ _ref/                       ← 参考源（仅开发时，发布前删除）
  │     ├─ tree-sitter/
  │     ├─ ast-grep/
  │     ├─ kuzu/
  │     ├─ agent-toolkit/
  │     └─ mnemosyne/
  └─ requirements.txt
```

---

## 零、第一性原理

### "理解代码库"意味着什么？

```
理解 = 知道什么存在 × 知道怎么连接 × 知道为什么这样 × 能找到 × 能追踪变化

WHAT   → 实体：文件、函数、类、类型、接口
HOW    → 关系：调用、导入、继承、数据流
WHY    → 惯例：命名模式、架构决策、设计意图
WHERE  → 检索：搜索、导航、提问
WHEN   → 变更：git 历史、影响面、演进轨迹
```

### 人类 vs Agent 的根本差异

> 对 Agent 友好 = 让 Claude Code、Codex、OpenClaw、Hermes Agent 等能直接**调用**本系统。
> 通过 MCP Server 暴露工具接口（搜索代码、查询图谱、获取惯例、分析影响等），
> Agent 在我们的代码库里工作时，可以实时检索和理解代码结构。

| 维度 | 人类 | Agent |
|------|------|-------|
| 输入速度 | ~40 bit/s (阅读) | ~100K tokens/s |
| 工作记忆 | 7±2 chunks | 上下文窗口内几乎无限 |
| 强项 | 模式感知、格式塔 | 穷举搜索、一致性检查 |
| 弱项 | 疲惫、遗漏 | 上下文过长时质量下降 |
| 偏好 | 视觉、空间、交互 | 结构化、可查询、确定性 |
| 解释需求 | "为什么？"（因果、叙事） | "是什么？"（结构、穷举） |
| **接入方式** | **浏览器打开 Web UI** | **MCP 协议调用工具** |
| **典型操作** | 拖拽查看图谱、点击节点 | `search_semantic("auth")`、`traverse_graph(...)` |

**→ 共享语义模型，分叉呈现层**（Human-Agent Collaboration Research, §1.1）

---

## 一、系统架构

```
                              ┌──────────────────────────┐
                              │     PRESENTATION LAYER    │
                              │                           │
                              │  ┌─────────┐ ┌─────────┐ │
                              │  │ 人类界面 │ │Agent界面│ │
                              │  │ D3 多视图│ │ MCP/API │ │
                              │  │ 对话侧栏│ │ 查询接口│ │
                              │  │ 代码透镜│ │ 上下文   │ │
                              │  └────┬─────┘ └────┬────┘ │
                              │       │    ┌───────┘      │
                              │       │    │ ┌──────────┐ │
                              │       │    │ │ CLI 入口  │ │
                              │       │    │ │ code-kg   │ │
                              │       │    │ └──────────┘ │
                              └───────┼────┼────┼─────────┘
                                      │    │    │
                         ┌────────────┴────┴────┴──────────┐
                         │       SEMANTIC MODEL LAYER          │
                         │                                     │
                         │  ┌─────────┐ ┌────────┐ ┌───────┐  │
                         │  │符号图谱 │ │调用图谱│ │数据流 │  │
                         │  │(定义/引用│ │(调用/依赖│ │(读写/ │  │
                         │  │ /类型)   │ │ /继承)  │ │传递)   │  │
                         │  └─────────┘ └────────┘ └───────┘  │
                         │  ┌─────────┐ ┌────────┐ ┌───────┐  │
                         │  │惯例规则 │ │文档索引│ │历史注释│  │
                         │  │.yaml    │ │+embed  │ │Git+PR │  │
                         │  └─────────┘ └────────┘ └───────┘  │
                         └─────────────────┬───────────────────┘
                                           │
                         ┌─────────────────┴───────────────────┐
                         │         ANALYSIS LAYER              │
                         │                                     │
                         │  ┌───────┐ ┌──────┐ ┌───────────┐  │
                         │  │解析    │ │检索  │ │ 记忆       │  │
                         │  │tree-   │ │三层  │ │ working→  │  │
                         │  │sitter  │ │递进  │ │ episodic→ │  │
                         │  │        │ │      │ │ semantic  │  │
                         │  └───────┘ └──────┘ └───────────┘  │
                         └─────────────────────────────────────┘
```

**核心原则**（Human-Agent Collab Research, §10）：
1. 语义模型层是唯一真相来源
2. 分析结果流入模型，不直接到 UI
3. 人类和 Agent 读取同一模型，呈现方式不同
4. 模型持久化，"理解"随时间累积
5. 每次人/AI 交互都建立在上一次之上

---

## 二、语义模型层（最关键的基础设施）

### 2.1 知识图谱 Schema

基于 Code KG Research 的三层实体分类：

**Layer 1: 结构实体（tree-sitter AST 提取）**
```
Repository → Package → File → Class/Interface → Method/Function
                                          → Field/Property
                              → Top-level Function
                              → TypeAlias
                              → Config
```

**Layer 2: 语义实体（SCIP/类型推断）**
```
Symbol        → 规范标识（名称+作用域+类别）
Reference     → Symbol 在特定位置的引用
Definition    → Symbol 的定义位置
TypeRelation  → Symbol 的类型关系
```

**Layer 3: 行为实体（静态分析）**
```
APICall        → 函数/方法调用
HTTPEndpoint   → 路由处理器
Middleware     → 装饰器/包装器
DataFlow       → 变量→使用→转换链
Dependency     → 包/模块依赖
```

**15 种关系类型**:
`CONTAINS`, `INVOKES`, `IMPLEMENTS`, `EXTENDS`, `IMPORTS`, `REFERENCES`, `TYPED_AS`, `THROWS`, `DECORATES`, `DEPENDS_ON`, `HANDLES`, `READS`/`WRITES`, `CALLS_TRANSITIVELY`, `DATA_FLOWS_TO`

### 2.2 解析策略

| 组件 | 工具 | 用途 |
|------|------|------|
| **实体+边界** | tree-sitter (⭐25k) | 100+语言解析，增量更新，WASM 可跑浏览器 |
| **结构化搜索** | ast-grep (⭐13k) | YAML 规则，精确模式匹配 |
| **跨文件引用** | SCIP (Sourcegraph) | Phase 4 引入，需语言特定 indexer（scip-python/scip-typescript） |
| **类型推断** | 各语言 LSP | 精确的类型关系 |

**分块策略**（Agent Memory Research, §3 + Code KG Research, §7）：
- 默认：函数级（tree-sitter 识别函数边界）
- OOP 代码库：**metadata 层类级索引，chunk 层仍按方法分块**，通过 LlamaIndex ParentDocumentRetriever 关联类→方法层级
- **绝不使用字符级分块**——破坏代码语义

每个 chunk 附带元数据：docstring、函数签名、imports、decorators、文件路径、所属模块。

### 2.3 图数据库（同时承担向量存储）

| 规模 | 推荐 | 理由 |
|------|------|------|
| **个人/本地** | **KuzuDB** (嵌入式) | Cypher 查询、内置 HNSW 向量索引（`ARRAY` 类型 + `array_cosine_similarity`）、单文件部署 |
| **团队** | Neo4j | 并发、GDS 图算法 |

**KuzuDB 同时承担图和向量两种存储**——不需要额外的向量数据库。HNSW 索引对 10K-500K 级别的函数向量足够快（<10ms 检索）。

**Embedding 模型选择**：全量初始索引用 Voyage-code-2（API，质量最高），增量更新用本地 ONNX 模型（如 `all-MiniLM-L6-v2`，KuzuDB 原生支持 ONNX runtime 推理）。

---

## 三、混合检索引擎

### 3.1 三层递进 + Mnemosyne 自适应回退

```
查询："找到所有处理 JWT 验证的中间件"
        │
        ▼
┌──────────────────────┐
│ Layer 1: 结构化 (0-50ms)│ ast-grep: pattern = {kind: function_declaration,
│ 精确模式 + BM25       │   has: {kind: decorator, pattern: "middleware"}}
│                      │ BM25 (SQLite FTS5): "JWT verify middleware auth"
└──────┬───────────────┘
       │
       ├─ healthy (有结果, 分数区分度高) → 返回
       ├─ zero   (无结果)              → 自动降级到 Layer 2
       ├─ few    (结果稀少)             → 降级 + 放宽过滤条件
       └─ flat   (分数相近难区分)       → 降级到 Layer 2 取更多候选
       │
       ▼
┌──────────────────────┐
│ Layer 2: 语义 (5-50ms)│ Voyage-code-2 / 本地 ONNX embedding
│ KuzuDB HNSW 向量检索 │ → KuzuDB array_cosine_similarity 搜索 Top-20
│                      │ → 与 Layer 1 结果合并
└──────┬───────────────┘
       │
       ├─ 需要上下文 → Layer 3
       └─ 否则 → RRF 融合返回
       │
       ▼
┌──────────────────────┐
│ Layer 3: 图谱 (10-200ms)│ KuzuDB Cypher 查询 → N-hop 邻居展开
│ 遍历 + LLM 解释       │ LLM 解释："authMiddleware 被 3 个路由调用,
│                      │   调用 jwt.verify()，修改会影响 userRoutes..."
└──────────────────────┘
```

**融合策略**：三层结果用 **Reciprocal Rank Fusion (RRF)** 合并——`score = Σ 1/(k + rank)`，k=60。不需要权重调参，RRF 自动给跨层共识高分。

**自适应回退**（借鉴 Mnemosyne 的检索诊断模式）：
- 每层执行后计算 4 个信号：`healthy`（区分度高，返回）、`zero`（无结果，降级）、`few`（稀疏，放宽过滤+图扩展）、`flat`（分数扎堆，多取候选）
- 诊断是 O(1) 的（长度检查 + top-2 分数比），不影响延迟
- 全本地路径（BM25 + HNSW）无网络调用；LLM 解释仅在 Layer 3 按需触发

### 3.2 查询接口（人类→自然语言, Agent→结构化）

```
人类输入: "JWT 验证的中间件在哪？"
        │
        ▼ LLM 理解意图
        │
        ├→ 生成 ast-grep YAML: {kind: function_declaration,
        │     has: {kind: decorator, pattern: "middleware"}}
        ├→ 生成 Cypher: MATCH (m:Middleware)-[:HANDLES]->(e:Endpoint)
        │     WHERE m.name CONTAINS 'auth'
        ├→ 生成 embedding 查询: "JWT authentication middleware verify token"
        │
        ▼ 合并 + RRF → 返回结果 + LLM 解释（仅 Layer 3 触发）
```

Agent 直接调用 MCP 工具：`search_by_pattern`, `search_semantic`, `traverse_graph`, `ask_question`

### 3.3 冷启动与批量索引

首次分析代码库时：
- tree-sitter 遍历 → 生成所有 chunk → Voyage-code-2 批量 embedding（batch_size=128）→ 写入 KuzuDB HNSW 索引
- BM25 全文索引同步构建（SQLite FTS5）
- 对于 10K 函数级的代码库，全量索引 <5 分钟（embedding API 耗时为主）

增量更新时单条走本地 ONNX embedding，避免 API 调用开销。

---

## 四、记忆层

### 4.1 三层记忆（映射到代码库）

| 层次 | 内容 | 存储 | 生命周期 |
|------|------|------|----------|
| **Working** | 服务端**无状态**——每次 Agent tool call 根据请求参数动态组装上下文片段。Agent 自身维护 session 状态 | 不持久化 | 请求结束释放 |
| **Episodic** | "上次改 auth 时影响了 3 个路由"、分析历史、Agent 探索路径 | SQLite（Mnemosyne store/ 模式）| 跨会话；超 30 天自动 LLM 摘要压缩（保留关键决策，丢弃细节） |
| **Semantic** | 代码库惯例、架构原则、团队偏好 → `.agent-conventions.yaml` | Markdown 文件 + git（Mnemosyne 模式）| 代码变更时人工确认更新 |

**实现借鉴**：Mnemosyne 的 `store/`（原子写入 `os.replace`、supersede 链、git 规范化前缀）作为 Memory 基础设施层实现参考。不再依赖 mem0——Mnemosyne 的文件级、可审计设计更适合代码库记忆的长期可靠性需求。

### 4.2 记忆的生命周期

```
代码变更 → tree-sitter 增量解析 → 更新知识图谱
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
              结构实体更新    关系边更新        统计数据更新
                    │               │               │
                    └───────────────┴───────────────┘
                                    │
                        LLM 判断：这次变更值得记住吗？
                        │               │
                    是（惯例变化）   否（日常修改）
                        │               │
                        ▼               ▼
                  更新 Semantic    仅记录 Episodic
                  Memory           ("auth.ts 在 5/15 被修改")
```

---

## 五、双界面

### 5.1 人类界面

**6 种视图**（Code Navigation Research, §4）：
| 视图 | 场景 | 技术 |
|------|------|------|
| **力导向图** (已有) | 全局浏览、发现模块聚类 | D3.js force + Canvas |
| **层级树/径向树** | 调用链、依赖深度 | D3.js tree / radial |
| **邻接矩阵** | 发现循环依赖、高耦合热点 | D3.js matrix |
| **太阳图** | 目录结构 + 代码量分布 | D3.js sunburst |
| **代码城市** | 大规模代码库鸟瞰 | Three.js 3D |
| **地铁图** | 模块间数据流 | 自定义 SVG |

**10 个交互模式**（Human-Agent Collab Research, §10 Patterns）:
1. **语义代码透镜**: 选中代码 → AI 解释含义、连接、影响
2. **解释侧栏**: 点击节点 → 完整关系面板（调用链 + 被调用链 + 影响面）
3. **代码导览**: 预生成"你应该知道的 N 件事"引导探索
4. **Diff 叙述者**: Git diff → AI 总结变更及影响
5. **交互架构图**: 可视化图中直接操作（折叠/展开/高亮路径）
6. **Agent 追踪视图**: 展示 Agent 的"思考过程"和代码库探索路径
7. **橡皮筋选择**: 选中一行 → AI 解释；拖拽扩展到函数 → AI 解释函数；扩展到模块 → AI 解释架构
8. **问题种子**: AI 预生成"你可能想问的问题"
9. **增强 Blame**: Git blame + AI 摘要（commit message + PR 讨论）
10. **面包屑路径**: 始终显示"你从哪来，现在在哪"

### 5.2 Agent 界面（MCP Server）

**复用 agent-toolkit 的 CLI+MCP 骨架**。它已经提供了：

- `CommandRegistry` + `ToolRegistry` 自动注册（`__init_subclass__` 魔术方法）
- 结构化错误码（`ToolErrorCode`）+ Pydantic 配置层 + 双模日志
- Crash-restart MCP wrapper（Peekaboo 同款的 crash-backoff 脚本）
- Shell 补全生成、JSON 输出模式

我们的代码知识系统只需要实现**7 个具体 Tool 子类**：

```
src/agent_toolkit/mcp/tools/
  ├─ search_by_pattern.py   → 继承 AutoRegisteringTool，实现 ast-grep 搜索
  ├─ search_semantic.py     → 继承 AutoRegisteringTool，实现 embedding 搜索
  ├─ traverse_graph.py      → 继承 AutoRegisteringTool，实现 KuzuDB 图遍历
  ├─ get_conventions.py     → 继承 AutoRegisteringTool，返回 .agent-conventions.yaml
  ├─ get_context.py         → 继承 AutoRegisteringTool，上下文注入
  ├─ ask_question.py        → 继承 AutoRegisteringTool，LLM+检索问答
  └─ analyze_impact.py      → 继承 AutoRegisteringTool，影响面计算
```

每个 Tool 只需定义 `name`、`description`、`input_schema()`、`execute()` 四个方法，注册由基类 `__init_subclass__` 自动完成。

**`get_context` Tool 定义**（最关键的 Agent 接口）：
```yaml
name: get_context
description: 获取当前编码任务相关的代码上下文片段（token-budgeted）
inputSchema:
  task_description: string    # "在 auth 模块添加 refresh token 功能"
  current_file: string        # "src/auth/authMiddleware.ts"
  max_tokens: integer = 4000  # 输出 token 上限
output:
  conventions: object         # 匹配的 .agent-conventions.yaml 条目
  related_functions: array    # 相关函数签名 + docstring + 位置
  dependency_graph: object    # 当前文件的上下游依赖
  recent_changes: array       # 该文件近 30 天的变更摘要
```

```
Agent 通过 MCP 调用：
  tools/list          → 发现所有可用工具（agent-toolkit 框架自动生成）
  search_by_pattern   → ast-grep 结构化搜索
  search_semantic     → embedding 语义搜索
  traverse_graph      → 知识图谱遍历
  get_conventions     → 获取项目惯例
  get_context         → 获取当前任务相关的代码上下文
  ask_question        → 自然语言提问，LLM + 检索回答
  analyze_impact      → 分析某变更的影响面
```

同时保留 CLI 入口——人类也可以用终端命令：

```bash
code-kg search --query "JWT middleware" --semantic
code-kg impact --file auth.ts --lines 42-89
code-kg conventions --export
```

---

## 六、Agent 代码适配

### 6.1 自动惯例提取

（Agent Code Adaptation Research, §1-3）

```
代码库 → tree-sitter 解析
         │
         ├→ 静态规则: naming (camelCase/PascalCase), structure (src/features/<name>/)
         ├→ 统计挖掘: 90% 的文件用 named export, 发现未文档化的模式
         ├→ LLM 总结: "这个项目偏好函数式风格，错误用自定义 Result<T,E> 类型"
         └→ 人类审核: 纠正 LLM 幻觉，补充设计意图
                │
                ▼
         .agent-conventions.yaml
```

**格式**（Agent Code Adaptation Research, §3）——**多语言支持**：
```yaml
codebase: my-project
conventions:
  typescript:
    naming:
      components: PascalCase           # severity: MUST
      hooks: camelCase, useXxx prefix  # severity: MUST
    structure:
      commands: src/commands/<name>/
    anti_patterns:
      - "DON'T import from src/legacy/"
    architecture:
      state: "Zustand stores, no Redux"
  python:
    naming:
      classes: PascalCase
      functions: snake_case
    structure:
      tests: tests/test_<module>.py
  go:
    naming:
      exported: PascalCase
      unexported: camelCase
```

### 6.2 上下文注入

（Agent Code Adaptation Research, §4）

**多阶段注入策略**:
```
System Prompt    → .agent-conventions.yaml (惯例)
Planning Stage   → 架构概览 + 模块依赖图 (从哪里开始?)
Generation Stage → 类似文件的 few-shot 示例 + 依赖模块的 API
Verification     → lint/test 命令 + 惯例检查清单
```

**对比提示**（研究显示比仅展示好例子提升 25-40% 的代码风格一致率[^1]）: 不仅展示好例子，也展示反例。

### 6.3 Agent 自验证

（Agent Code Adaptation Research, §6-7）

```
Agent 写完代码 →
  ├→ 自动检查: 命名符合惯例？导入顺序正确？
  ├→ 运行验证: npm run lint && npm test
  ├→ 自修复: 格式问题自动修，语义问题标记
  └→ Reviewer Agent: 另一个 Agent（不同 prompt/model）审查
```

---

## 七、目标代码库的变更追踪

> 注意：这里的"更新"指我们**分析的目标代码库**发生了变更（新 commit、重构等），
> 不是 `_ref/` 参考源。`_ref/` 只是开发时的参考，抄完即完工。

我们的系统理解了一个代码库之后，代码库本身还在持续演进。
需要跟踪这些变化，让"理解"保持最新。

### 7.1 触发机制

```
代码库发生变更
    │
    ├─ Git Hook (推荐): post-commit / post-merge 自动触发增量分析
    ├─ 轮询模式: 定时检查 HEAD 是否变化 (适合无法装 hook 的场景)
    └─ 手动触发: 用户在 Web UI 点击 "Refresh Analysis"
```

### 7.2 增量分析（只处理变更文件）

```
git diff HEAD~1 --name-only
    │
    ├─ 新增文件 → 首次解析，全部 chunk + embed
    ├─ 修改文件 → 重新解析，删除旧实体/关系，插入新的
    ├─ 删除文件 → 移除所有相关节点和边
    └─ 重命名/移动 → 更新 file_path，关系保持不变 (git diff --find-renames)
```

### 7.3 影响面计算

```
修改了 auth.ts:42-89 (authMiddleware 函数)
    │
    ├─ 直接依赖: app.ts, userRoutes.ts, adminRoutes.ts 引用了它
    ├─ 级联影响: 这些文件调用的下游 → 共 17 个文件可能受影响
    ├─ 测试影响: 12 个测试文件覆盖了相关路径
    │
    └─ Web UI 展示: "⚠️ 此变更影响 17 个文件，建议跑 auth 相关测试"
```

### 7.4 版本快照与对比

每次分析生成一个 **Snapshot manifest**（JSON 文件，存在 `~/.code-kg/snapshots/`）：
```json
{
  "id": "snap_2026-05-15T14:30:00Z",
  "commit_hash": "def456",
  "stats": {
    "nodes": 11732, "edges": 10268,
    "files": 847, "functions": 8942
  },
  "delta": {
    "from_snapshot": "snap_2026-05-14T09:00:00Z",
    "nodes_added": 15, "edges_removed": 18,
    "files_changed": ["auth.ts", "middleware.ts"],
    "llm_summary": "重构认证中间件，将 JWT 逻辑提取为独立模块"
  }
}
```

### 7.5 Git 历史融入知识图谱

```
每个 Node/Edge 携带 git 元数据:
  Node {
    ...
    git_added: "2025-03-12 (commit 7f3a2b1)",
    git_last_modified: "2026-05-15 (commit def456)",
    git_authors: ["alice", "bob"],
    git_blame: "line 42-89: alice (2025-03-12), line 76: bob (2026-01-20)"
  }
```

### 7.6 知识保鲜策略

| 变更类型 | 更新频率 | 更新内容 |
|----------|----------|----------|
| **代码 push** | 即时 (<5s) | 增量解析变更文件，更新知识图谱 |
| **依赖变更** (package.json) | 即时 | 重新分析 import 关系 |
| **重构** (大量文件) | 后台 (<5min) | 全量重分析 + LLM 摘要 |
| **文档更新** | 即时 | 重新 embed 文档 chunk |
| **惯例变化** | 人工确认 | 更新 `.agent-conventions.yaml` |

### 7.7 Agent 感知代码演进

```
Agent 会话:
  "帮我在 auth 模块加一个 refresh token 功能"
  
  系统注入上下文:
  ├─ 当前代码结构 (最新 snapshot)
  ├─ 相关惯例 (.agent-conventions.yaml)
  └─ 最近变更历史: "auth.ts 最近被 alice 重构，JWT 逻辑已提取到 jwt.ts"
      ↑ Agent 知道不要去改已经重构过的旧路径
```

---

## 八、实施路径

### Phase 1: 语义模型 (4 周) ← 地基
- [ ] tree-sitter 替换现有正则解析 → 支持 Python/TS/Go/Rust
- [ ] KuzuDB 图存储 + HNSW 向量索引 → 实体+关系+向量持久化
- [ ] 函数级分块 + 元数据提取 (docstring, signature, imports)
- [ ] 测试：tree-sitter 解析准确率 ≥ 95%（vs 现有 Python AST）

### Phase 2: 检索 + 记忆 (3-4 周)
- [ ] ast-grep 结构化搜索集成（Layer 1）
- [ ] KuzuDB HNSW 向量检索（Layer 2）+ RRF 融合
- [ ] Mnemosyne 自适应回退编排（zero/few/flat→降级/扩展）
- [ ] 从 Mnemosyne store/ 抄原子写入+supersede 链 → episodic 记忆
- [ ] 测试：检索 recall@5 ≥ 0.85（eval 基准：30 查询 × 已知正确答案；正确答案来源：从目标代码库随机抽样函数，提取其 docstring 作为查询，原函数作为标准答案）

### Phase 3: 双界面 (4 周)
**人类**:
- [ ] 节点点击 → 完整关系面板（调用链、被调用链、影响面）
- [ ] 新增 3 种视图：层级树、邻接矩阵、太阳图（力导向图已有，代码城市+地铁图→Phase 4）
- [ ] Monaco Editor 嵌入 → 源码预览
- [ ] 搜索栏 → 自然语言查询

**Agent**:
- [ ] 集成 agent-toolkit 骨架 → 实现 7 个 Tool 子类
- [ ] Crash-restart MCP wrapper 部署
- [ ] `.agent-conventions.yaml` 自动生成 + 手动编辑
- [ ] 测试：MCP tools/list 正常返回 + 7 个工具端到端测试

### Phase 4: 进阶 (6 周)
- [ ] LSP 源码跳转（后端 WebSocket）
- [ ] SCIP 跨文件引用索引（scip-typescript + scip-python）
- [ ] 文档联合索引（源码注释 + Markdown + API 文档）
- [ ] Git diff → 影响分析 + LLM 变更摘要
- [ ] 代码城市 (Three.js) + 地铁图视图

### Phase 5: CI 集成 (按需)
- [ ] Agent 代码审查集成
- [ ] 代码导览 / 问题种子
- [ ] 部署方案 (Docker + pip install)

---

## 九、关键参考索引

| 调研文档 | 路径 | 核心贡献 |
|----------|------|----------|
| Code KG Research | `/Users/bamboo/code-kg-research.md` | 图谱 Schema、Hybrid Search、Chunking |
| Code Navigation Research | `/Users/bamboo/code-navigation-research.md` | LSP 在浏览器、18 种可视化、增量更新 |
| Agent Code Adaptation | `/Users/bamboo/agent-code-adaptation-research.md` | CLAUDE.md 对比、惯例提取、Style Transfer |
| Human-Agent Collab | `/Users/bamboo/human-agent-collaboration-research.md` | 第一性原理、认知模型、10 设计戒律 |
| Agent Memory | `/Users/bamboo/agent-memory-research.md` | 三层记忆、检索融合、代码分块 |

### 外部关键参考

| 仓库 | ⭐ | 参考价值 |
|------|-----|----------|
| **CodeLandscapeViewer** | — | 项目地基（MIT）—— 前端 D3 + 后端 FastAPI |
| **tree-sitter/tree-sitter** | 25,370 | 增量解析引擎 |
| **ast-grep/ast-grep** | 13,802 | 结构化代码搜索 |
| **sourcegraph/scip** | — | 精确代码智能（Phase 4） |
| **agent-toolkit** | — | CLI+MCP 通用骨架；`AutoRegisteringTool` 子类化即可添工具 |
| **Mnemosyne** | — | 文件级 Agent 记忆基础设施；`store/` 原子写入 + `retrieval/` 自适应诊断 |
| **Voyage-code-2** | — | 代码 Embedding SOTA |
| **ContextAtlas** | 30 | 最接近目标的完整参考 |
| **continue/continue** | 33,192 | Agent CI 集成 |

[^1]: Agent Code Adaptation Research §5——对比提示在代码风格迁移任务中的一致率提升测量。
