# bePm 设计宪章

> 此文件是 bePm 项目的**设计宪法**——后续所有修改必须遵循这些原则。
> 违反红线的改动将被拒绝。优化建议必须在原则框架内提出。

---

## 一、核心设计哲学

### 1.1 纯度边界（Purity Boundary）

**输入构造层和执行层之间，不允许有任何写死的逻辑，只有 LLM。**

这是 bePm 最根本的设计原则（源自 ISSUE-007）。规则引擎和 LLM 判断混合会导致不可调试的行为。

```
输入构造层（机械） → LLM 层（纯 AI，无硬编码） → 编排层（机械翻译） → ★确认门★ → 执行层（机械执行）
```

- ✅ LLM 做所有语义判断
- ✅ 代码只做机械翻译和原子执行
- ❌ 禁止在 parser 和执行层之间添加硬编码规则
- ⚠️ 例外：`structural_risk_scan()` 仅在 LLM 不可用时作为降级兜底

### 1.2 确认门模式（Confirmation Gate）

**任何涉及拓扑变更的操作，必须先展示计划给用户确认，再执行。**

- LLM 输出意图 → 编排层翻译为原子操作 → 确认门 → 用户确认 → 执行（不重调 LLM）
- 确认后执行时**绝对不重调 LLM**——保证执行的就是用户看到的

### 1.3 原子操作体系（Atomic Operations）

DAG 操作只有 5 种原子操作 + ask_user：
- `add_node` / `edit_node` / `delete_node`
- `add_edge` / `remove_edge`
- `ask_user`（信息不足时反问）

复杂场景由 LLM 组合多个原子操作完成，编排层不做任何判断。

### 1.4 三层分离

| 层 | 职责 | 绝不做什么 |
|----|------|-----------|
| **前端** | 渲染 DAG、收集输入、展示风险 | 不做排期计算、不做风险分析 |
| **后端** | 路由、持久化、WebSocket 推送 | 不做 AI 推理、不感知任务语义 |
| **引擎** | LLM 调用、CPM 排期、风险扫描 | 不直接面对用户、不做持久化 |

---

## 二、设计红线（绝对不能破坏）

### 🔴 红线 1：JSON 提取必须对象优先

`_extract_json()` 的边界检测必须先检查 `{` 再检查 `[`。当 `{` 在 `[` 之前时，返回对象而非数组。

**原因**：LLM 输出 `{"tasks": [...]}` 时，数组在对象内部。先匹配 `[` 会导致只返回内层数组。参见 ISSUE-008 / commit 4a6984b 的修复。

### 🔴 红线 2：DAG 更新必须保留 is-group class

`addNodeToGraph()` 和 `_updateGraph()` 的 classes 行必须与 `render()` 保持一致，包含 `is_group` 判断。否则分组节点在增量渲染时丢失样式。

### 🔴 红线 3：create_schedule 会污染传入的节点

`create_schedule(nodes, deadline)` 会将 ES/EF/LS/LF 写回传入的 TaskNode 对象。在分组/聚合场景中，如果传入的是项目中的真实节点，必须在调用前快照时间值，调用后恢复。

**推荐**：新代码不要调用 `create_schedule` 做子图排期。对聚合节点直接用 min/max 计算边界。

### 🔴 红线 4：儿童数据保持数组类型

DAG 节点的 `children` 字段在存储和渲染中必须保持数组类型（`list`），不能 `join` 为字符串。`_buildLabel` 依赖 `Array.isArray` 判断。

### 🔴 红线 5：前端状态变量不可从外部直接修改

`currentProjectId`、`_editingNodeId`、`_pendingOps`、`_activeTags` 等 IIFE 闭包内的变量，只能通过暴露在 `window.app` 上的函数操作。

### 🔴 红线 6：Cytoscape 属性白名单

dag.js 的 Cytoscape style 中**禁止使用**以下属性（Cytoscape.js 3.x 不支持）：
- `shadow-*`（shadow-blur, shadow-color, shadow-opacity, shadow-offset-*）
- `scale`（不能用 `node.style("scale", ...)`）
- 只能用 `opacity` 做淡入动画

---

## 三、协作原则

### 3.1 Prompt 外部化

所有 LLM Prompt 优先从 `backend/prompts/*.md` 加载，代码内置值为 fallback。非技术人员可以直接编辑 .md 文件。

### 3.2 配置外部化

`backend/config.json` 管控所有可配置参数。优先级：环境变量 > config.json > 代码默认值。

### 3.3 测试看护

- 端到端测试必须覆盖"前端事件 → 后端处理 → 前端反馈"完整闭环
- 关键缺陷修复后必须添加回归测试
- 所有涉及 `create_schedule` 的测试必须断言节点时间值前后一致

### 3.4 LLM Provider 可插拔

`LlmProvider` 通过注册表模式支持任意后端。新加后端只需实现 `execute(ctx) -> LlmResult` 并在 `_SDK_PROVIDER_REGISTRY` 注册。禁止在 parser 中硬编码 provider 逻辑。

### 3.5 不重复造轮子

- 排期 = `scheduler.py`（CPM / Kahn）
- 风险 = `structural_risk_scan()` + LLM 分析
- 意图 = `intents.py`（纯映射，不判断）
- 存储在 `.projects/{id}/` 目录结构

---

## 四、常见陷阱速查

| 陷阱 | 表现 | 修复 |
|------|------|------|
| `map_intent_to_ops` 未导入 | command 端点 500 | `from engine.intents import map_intent_to_ops` |
| `structural_risk_scan` 未导入 | batch reschedule 失败 | `from engine.scheduler import structural_risk_scan` |
| 新增字段只加模型不加翻译层 | LLM 不知道可以输出新字段 | `_build_schema_for_translation()` 同步更新字段列表 |
| 新增字段不加 `_build_plan_text` | 用户确认计划时不显示变更 | `_build_plan_text()` 添加字段展示分支 |
| NOT_FOUND handler re-raise | 非 API 路径返回 500 | 返回 `Response(404)` 而非 `raise exc` |
| 根路径 `/` 无 Cache-Control | 浏览器缓存旧 HTML | CharsetMiddleware 匹配 `/` 和 `text/html` |
| `_reschedule_project` 不传 `skip_llm` | 手动操作 30s 无响应 | 所有手动 CRUD 使用 `skip_llm=True` |

---

## 五、项目结构约定

```
backend/
├── main.py              # 入口 + 中间件 + 异常处理
├── config.py             # 配置加载（数据类 + config.json）
├── config.json           # 可编辑配置文件
├── api/
│   ├── projects.py       # REST API（CRUD + 排期 + 分组 + 待办）
│   └── ws.py             # WebSocket 连接管理
├── engine/
│   ├── parser.py         # LLM 调用 + JSON 提取 + 双层架构
│   ├── intents.py        # 原子意图定义 + 编排器
│   ├── scheduler.py      # CPM 排期 + 风险扫描
│   └── llm_provider.py   # 可插拔 LLM Provider
├── models/
│   └── project.py        # Pydantic 数据模型
├── prompts/              # LLM Prompt 文件（.md）
└── tests/                # pytest 测试（E2E + 单元 + 回归）
frontend/
├── index.html
├── css/style.css
├── js/
│   ├── api.js            # REST 封装
│   ├── dag.js            # Cytoscape DAG 渲染
│   ├── ws.js             # WebSocket 客户端
│   └── app.js            # 主应用逻辑
└── vendor/               # 本地化的第三方库
```

---

*最后更新: 2026-07-30*
*版本: v0.2.0*
