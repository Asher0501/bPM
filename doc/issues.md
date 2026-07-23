# bePm Issues

## ISSUE-001: AI 缺乏项目级会话记忆

### 发现日期

2026-07-22

### 现象

用户在新增节点（环节A）后，说"将环节A集成到整体编排"，AI 又创建了一个新节点而非操作已有节点。

根本原因：每次 LLM 调用是独立的（Anthropic API 无状态），没有传入之前的对话历史，AI 不知道"环节A"指什么。

### 复现步骤

1. 创建项目并排期
2. 添加新节点："加性能测试3天，依赖后端API" → AI 添加 task_6
3. 再次输入："将性能测试集成到编排" → AI 又新建了一个节点，而非调整 task_6

### 根因分析

Anthropic Messages API 是**完全无状态**的。每次 `client.messages.create()` 都是独立请求。所谓的"记忆"需要调用方自行维护 `messages` 数组并在每次请求时完整传入。

bePm 当前的 parser 每次只传入当前单条指令，没有携带该项目的对话历史。

### 解决方案

在 `project.json` 中新增 `messages` 字段，存储该项目的 LLM 对话历史。

#### 数据结构

```json
{
  "nodes": [...],
  "messages": [
    {"role": "user", "content": "加性能测试3天，依赖后端API"},
    {"role": "assistant", "content": "已添加 task_6（性能测试），依赖 task_2（后端API）"},
    {"role": "user", "content": "将性能测试集成到编排"},
    {"role": "assistant", "content": "task_6 已在项目中。你是指调整它的依赖关系吗？"}
  ]
}
```

#### 调用流程

```
用户输入
    │
    ▼
取出 project.messages（最近 20 条）作为上下文
    │
    ▼
注入到 LLM system prompt 顶部
    │
    ▼
LLM 基于完整上下文决策：新建 / 修改 / 反问
    │
    ▼
将本轮对话追加到 project.messages
```

#### 决策规则

| 场景 | AI 行为 |
|------|--------|
| 引用已有节点名 | 默认指已有节点，反问确认操作意图 |
| 明确描述新任务且无歧义 | 创建新节点 |
| 歧义时（不确定新建还是修改） | **反问用户**，列出选项 |
| 依赖关系模糊 | **反问**"依赖哪个任务？" |

### 修改范围

| 模块 | 变更 |
|------|------|
| `models/project.py` | Project 新增 `messages: list[dict]` |
| `engine/parser.py` | 所有 LLM 调用注入 messages 上下文 |
| `api/projects.py` | 操作后追加对话记录到 messages |


## ISSUE-002: 缺少边（依赖关系）管理 API

### 发现日期

2026-07-22

### 现象

节点 CRUD 已实现，但依赖关系只能通过编辑节点的 `pre_dependencies` 字段间接修改。没有独立的边管理能力，导致：
- 无法直接"添加一条依赖边"（让 B 依赖 A）
- 无法直接"删除一条依赖边"
- AI 反问"要调整依赖关系吗？"时，即使修改了 `edit_node` 也无法直观操作边

### 解决方案

新增边 CRUD API，与节点 CRUD 同级：

| 端点 | 说明 |
|------|------|
| `POST /api/projects/{id}/edges` | 添加边 `{source, target}` → target 依赖 source |
| `DELETE /api/projects/{id}/edges/{source}/{target}` | 删除边 |
| `GET /api/projects/{id}/edges` | 列出所有边（含节点名称） |

前端：编辑节点弹窗中的依赖勾选 = 批量添加/删除边。
NL 模式下，用户说"让后端API依赖数据库设计"→ AI 识别为 add_edge。

### 修改范围

| 模块 | 变更 |
|------|------|
| `api/projects.py` | 新增 edge CRUD 端点 |
| `engine/parser.py` | `parse_single_task` 支持 `add_edge`/`remove_edge` action |
| `frontend/js/api.js` | 新增 edge API 封装 |
| `frontend/js/app.js` | 刷新时更新边显示 |


## ISSUE-003: 节点数据存储结构优化

### 发现日期

2026-07-22

### 现象

每个项目的 `project.json` 和 `messages.json` 平铺在 `data/projects/` 下，缺乏组织。

### 解决方案

```
.projects/
├── index.json                  ← 项目注册表（秒级列表查询）
├── {project_id}/
│   ├── project.json            ← DAG + 节点 + 排期 + 风险
│   └── messages.json           ← 对话记忆（独立管理）
```

- `index.json` 维护所有项目的摘要信息，列出项目无需扫描文件夹
- 每个项目独立文件夹，后续可扩展 `snapshots/`、`exports/` 等
- 删除项目 = `shutil.rmtree` + 索引移除


## ISSUE-004: 新增节点时 AI 不理解整体项目拓扑

### 发现日期

2026-07-22

### 现象

新增节点时，LLM 只看到扁平节点列表 `[{id, name, deps}]`，无法理解：
- 整体工作流（从上游到下游的完整链条）
- 各阶段语义（调研→设计→开发→测试→部署）
- 汇聚点、关键路径、并行分支

导致 LLM 机械地把新节点连到所有根节点，不考虑业务逻辑是否合理。

### 示例

OA 项目中新增"用户培训材料准备"，AI 将其连到所有开发模块（4个），而非只连必要的直接下游。

### 解决方案

将传给 LLM 的上下文从**扁平节点列表**改为**图拓扑描述**：

```
### 依赖链（→ 表示先后顺序）
链1: 需求分析(5d) → 架构设计(3d) → 数据库设计(2d) → API开发(10d) → ...

### 图分析
- 最上游（无前置）: task_1(需求分析)
- 最下游（无后继）: task_9(部署)
- 汇聚点: task_8(集成测试, 入度4)
- 分支点: task_3(数据库设计) → task_4, task_5, task_6（并行）
```

新增 `_build_topology_description()` 和 `_build_simpler_description()` 函数，构建依赖链 + 图分析 + 并行分支信息传给 LLM。


## ISSUE-005: 禁止孤节点

### 发现日期

2026-07-22

### 现象

用户添加节点后，DAG 上出现孤立节点——没有边连接任何其他节点。用户说"图上没有这个节点"（实际有，但浮在一边看不见连接）。

### 根因

上游节点（如"用户调研"）的 `pre_dependencies=[]` 是正确的，但如果没有任何下游节点依赖它，它就成了孤岛。

### 解决方案

在 LLM prompt 中添加**禁止孤节点规则**：

> **绝对不允许出现孤节点。** 每次新增节点必须同时声明它与已有节点的连接关系。
> - 如果用户指定了依赖 → 按指定来
> - 如果用户没指定 → 根据业务逻辑推断
> - 如果无法推断 → 返回 ask_user，列出具体连接方案让用户选

LLM 必须在 `downstream_deps` 中指定直接下游任务（只填紧后，不填间接后继）。


## ISSUE-006: 拓扑变更缺少用户确认机制

### 发现日期

2026-07-22

### 现象

LLM 输出的拓扑变更计划直接执行，用户无法审查。如果 LLM 判断错误（如连了错误的下游节点），没有机会纠正。

### 解决方案

在编排层和执行层之间插入**确认门**：

```
LLM 输出意图 → 编排层翻译为原子操作 → ★确认门★ → 执行层
```

- 任何涉及拓扑变更的操作（add_node, add_edge, remove_edge, delete_node, edit_node），先展示计划给用户
- 用户确认后，直接执行存储的 ops（**不重调 LLM**），保证执行的与展示的一致
- 用户取消则丢弃

API 返回 `action: "confirm_plan"` 时携带 `plan`（人类可读文本）和 `ops_summary`（结构化操作列表）。前端展示计划 + 确认/取消按钮。用户确认后回传 `confirmed: true` + `ops_to_execute`，跳过 LLM 直接执行。


## ISSUE-007: 架构分层 — 移除 LLM 层中的硬编码逻辑

### 发现日期

2026-07-22

### 原则

**在输入构造层和执行层之间，不允许有任何写死的代码，只有 LLM。**

### 发现并移除的硬编码

| 位置 | 硬编码内容 | 处理 |
|------|-----------|------|
| `parser.py:_build_topology_description` | 任务角色推断（关键词匹配"调研/设计/开发/测试/部署"） | 删除 |
| `intents.py:_map_add_connected_node` | 根节点过滤（上游节点只连根节点） | 删除 |
| `api/projects.py` | `structural_risk_scan()` 硬编码阈值（入度≥3、深度≥5、置信度<0.6） | 删除所有调用，改为 LLM 分析 |
| `analyzer.py` | `merge_llm_risks()` 合并算法 | 不再引用 |

### 最终架构

```
输入层（机械）
  构建拓扑描述 + 调用 LLM
       ↓
LLM 层（纯 AI，无硬编码）
  理解语义 → 输出意图 JSON
       ↓
编排层（机械翻译）
  intents.py: 意图 → 原子操作（纯映射，不判断）
       ↓
确认门（架构关卡）
  展示计划 → 等用户确认
       ↓
执行层（机械执行）
  应用原子操作 + scheduler.py 重算
```

### ⚠️ 例外声明：LLM 不可用时的算法降级

`structural_risk_scan()` 虽包含硬编码阈值（入度≥3、深度>5、置信度<0.6、浮动<2天），但作为 **LLM 不可用时的兜底降级策略** 被保留，**不违反架构原则**。理由如下：

1. **降级路径仅在 LLM 不可用时触发**。当 API Key 正常配置时，`analyze_risks()` 优先使用 LLM 进行风险分析，算法扫描只有在 LLM 调用失败或 API Key 未配置时才会执行。
2. **用户可见性**。降级时，风险列表首条会明确标注 `info` 级别的提示："LLM 驱动的深度风险分析不可用（API Key 未配置）"，并引导用户配置。
3. **核心链路不受影响**。降级路径不在"输入构造层 → LLM → 编排层 → 执行层"的主干上，而是 `analyze_risks()` 的独立出口。
4. **保证用户体验**。若 LLM 完全不可用且无算法降级，风险分析将返回空列表，用户得不到任何结构风险提示。

**维护约定**：如需调整算法阈值，应通过配置参数而非修改代码。当前阈值定义为合理的默认值，无需频繁调整。


## ISSUE-008: LLM JSON 输出的 Schema 校验与反馈机制

### 发现日期

2026-07-22

### 现象

最初的实现用代码去"修复" LLM 输出的格式错误（补全截断的 JSON、移除多余逗号、给未引号的值加引号...），但这些 hack 无法覆盖所有情况，且掩盖了真正的问题。

### 用户反馈

> "要的不是容错！llm输出的json分布千变万化你不可能总是兜底，你需要为llm提供一个可靠的基于代码的反馈机制，让前一层知道是哪里出了问题以及问题原因值是什么。"

### 解决方案

**Schema 校验 + 结构化错误码 + LLM 重试循环：**

1. 定义每个 intent 的 schema（必填字段、字段类型、合法值）
2. LLM 输出后立即校验
3. 校验失败 → 构造结构化错误，注入 prompt → LLM 看到错误 → 重新输出（最多 3 次）

### 错误码设计

| 错误码 | 触发条件 | 反馈给 LLM 的内容 |
|--------|---------|------------------|
| `NO_JSON_FOUND` | 输出中没有 `{}` | LLM 输出的纯文本预览 |
| `JSON_PARSE_ERROR` | JSON 语法错误 | 行号 + 列号 + 错误原因 |
| `SCHEMA_ERROR` | 缺少必填字段 | 具体缺了哪个字段 |
| `SCHEMA_ERROR` | 字段类型错误 | 期望类型 vs 实际类型 |
| `SCHEMA_ERROR` | 未知 intent | 列出所有合法 intent 值 |

### 关键代码

```python
class JsonExtractError(Exception):
    def __init__(self, error_code: str, message: str, raw_preview: str):
        self.error_code = error_code
        self.message = message
        self.raw_preview = raw_preview

INTENT_SCHEMA = {
    "add_connected_node": {
        "required": ["intent", "name", "estimated_days"],
        "types": {"estimated_days": (int, float), "pre_dependencies": list, "downstream_deps": list},
    },
    # ...
}
```

重试循环：`for attempt in range(3): try LLM → validate → on error, feed back to LLM`


## ISSUE-009: 意图 JSON 未持久化

### 发现日期

2026-07-23

### 现象

LLM 输出的语义意图 JSON 只在内存中存在，请求结束后丢失。调试时无法追溯 AI 当时输出了什么。

### 解决方案

每次 LLM 解析后，将意图 JSON 存入 `messages.json`：

```python
project.messages.append({"role": "assistant", "content": f"[意图] {json.dumps(intent, ensure_ascii=False)}"})
```
