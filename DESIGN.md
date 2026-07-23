# bePm — AI 辅助项目排期管理系统 · 设计文档

> **目标**：输入项目详情（自然语言/文件），自动解析任务、推断依赖、生成拓扑排序图（DAG）。
> 输入进展描述，自动更新各节点进度，提示风险并给出可执行的建议。
> 前后端解耦，参考 Nexus 架构模式。

---

## 一、背景（Background）

### 1.1 问题陈述

软件项目排期管理长期存在三个痛点：

1. **排期靠经验，缺乏结构**：项目经理根据经验排期，容易遗漏隐藏依赖、低估任务复杂度，产生"填日期"式计划。
2. **进展跟踪滞后**：偏差被解释而非处理，小延迟通过"四轮放大"机制最终演变为整体延期——前段延误挤压后段测试工期，风险在项目尾部集中爆发。
3. **风险不可见**：关键路径、资源冲突、依赖瓶颈等风险往往在发生后才暴露，缺乏前置预警。

### 1.2 解决思路

bePm 通过 LLM + DAG 拓扑排序图来解决上述问题：

- **自然语言 → 结构化 DAG**：用户以自然语言描述项目，AI 解析为结构化任务列表，自动推断依赖关系，生成拓扑排序图。
- **实时可视化**：DAG 中每个节点代表一个子里程碑，用颜色标记进度和风险状态。
- **进展驱动的风险分析**：用户输入项目进展（自然语言），AI 映射到各个节点并更新进度，同时扫描全部风险维度，提供可执行建议。

### 1.3 核心概念映射

| 概念 | 含义 | 在系统中的体现 |
|------|------|---------------|
| **任务（Task）** | 一个可完成的工作单元 | DAG 中的一个节点 |
| **子里程碑（Sub-milestone）** | 若干个相关任务的聚合体 | 一个汇聚节点（fan-in point） |
| **依赖关系（Dependency）** | 任务之间的前置关系 | DAG 中的有向边 A→B |
| **关键路径（Critical Path）** | 决定项目最短完成时间的任务序列 | 拓扑排序中的最长路径 |
| **缓冲区（Buffer）** | 吸收不确定性的时间储备 | 关键路径末尾 + 非关键链汇入处 |
| **进展（Progress）** | 节点的完成状态 | 节点属性（0-100%，含置信度标记） |

---

## 二、架构（Architecture）

### 2.1 架构概览

```
┌──────────────────────────────────────────────────────────────────────┐
│                          用户交互层                                   │
│  ┌─────────────────────────┐     ┌──────────────────────────────┐    │
│  │  Web 前端 (浏览器)       │     │  Claude Code PM Agent       │    │
│  │  HTML/CSS/JS + Cyto-   │     │  (agent.md)                 │    │
│  │  scape.js DAG 可视化    │     │  对话式项目管理入口           │    │
│  └───────────┬─────────────┘     └──────────────┬───────────────┘    │
│              │ HTTP/WS                          │ Agent Tool Calls   │
└──────────────┼──────────────────────────────────┼────────────────────┘
               │                                  │
               ▼                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                           后端服务层                                  │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  Python FastAPI (backend/main.py)                            │    │
│  │                                                               │    │
│  │  REST API (/api/*)          WebSocket (/ws/*)                 │    │
│  │  ├─ POST /api/projects      ├─ /ws/projects/{id}             │    │
│  │  ├─ GET  /api/projects/{id} │   实时推送:                     │    │
│  │  ├─ POST /.../{id}/schedule │   - node_status (进度变更)      │    │
│  │  ├─ POST /.../{id}/progress │   - risk_alert (风险警报)       │    │
│  │  ├─ GET  /.../{id}/graph    │   - suggestion (建议推送)       │    │
│  │  ├─ PUT  /.../{id}/task/{n} │                                 │    │
│  │  └─ DELETE /.../{id}        │                                 │    │
│  └───────────┬──────────────────────────────────────────────────┘    │
│              │                                                       │
│  ┌───────────▼──────────────────────────────────────────────────┐    │
│  │  AI 引擎 (engine/)                                            │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐                   │    │
│  │  │ Parser   │  │Scheduler │  │Analyzer  │                   │    │
│  │  │ NL→JSON  │  │拓扑排序  │  │风险分析  │                   │    │
│  │  │          │  │+关键路径 │  │+建议生成 │                   │    │
│  │  └──────────┘  └──────────┘  └──────────┘                   │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  数据层 (data/projects/)                                      │    │
│  │  JSON 文件持久化 — 每个项目一个 .json 文件                     │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 三层职责边界（参考 Nexus 设计原则）

| 层 | 职责 | 不做什么 |
|----|------|---------|
| **前端** | 渲染 DAG 图、收集用户输入、展示风险面板 | 不做排期计算，不做风险分析 |
| **后端 API** | 路由请求、数据持久化、WebSocket 推送 | 不做 AI 推理，不感知任务语义 |
| **AI 引擎** | LLM 调用：NL 解析、依赖推断、风险分析 | 不直接面对用户，不做持久化 |

---

## 三、4+1 视图

### 3.1 逻辑视图（Logical View）

系统的功能分解与模块职责：

```
┌─────────────────────────────────────────────────────┐
│                    bePm 系统                         │
├─────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │ 项目输入模块 │  │ 排期引擎模块 │  │ 进展追踪模块 │ │
│  │             │  │             │  │             │ │
│  │ · 文本输入  │  │ · NL 解析   │  │ · 进展输入  │ │
│  │ · 文件上传  │  │ · 依赖推断  │  │ · 进度映射  │ │
│  │ · 会话恢复  │  │ · 拓扑排序  │  │ · 图更新    │ │
│  │             │  │ · 关键路径  │  │ · 风险分析  │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘ │
│         │                │                │        │
│  ┌──────┴────────────────┴────────────────┴──────┐ │
│  │              核心数据模型                       │ │
│  │  Project → TaskNode[] → DAG (edges[]) → State │ │
│  └───────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────┐ │
│  │              DAG 可视化模块                     │ │
│  │  · 拓扑布局 · 状态颜色 · 关键路径高亮           │ │
│  │  · 风险标记 · 交互编辑 · 缩放平移              │ │
│  └────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────┐ │
│  │              PM Agent（对话入口）               │ │
│  │  · 工具：read_project / create_schedule         │ │
│  │  · 工具：update_progress / analyze_risk         │ │
│  └────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### 3.2 过程视图（Process View）

系统运行时流程，重点描述调度和 WebSocket 事件流。

#### 3.2.1 项目排期流程

```
用户输入项目描述（NL）
        │
        ▼
┌──────────────────┐
│ Parser            │  Claude API: 解析 NL → 结构化任务列表
│ (engine/parser.py)│  输出: [{id, name, description, estimated_days,
│                   │          confidence, pre_dependencies, resources}]
└────────┬─────────┘
         │ tasks[]
         ▼
┌──────────────────┐
│ Scheduler         │  1. 构建邻接表 (依赖关系)
│ (engine/scheduler │  2. 拓扑排序 (Kahn算法)
│  .py)             │  3. 计算最早开始/结束时间 (ES/EF)
│                   │  4. 计算最晚开始/结束时间 (LS/LF)
│                   │  5. 识别关键路径 (float=0)
│                   │  6. 添加缓冲区 (项目缓冲+输入缓冲)
└────────┬─────────┘
         │ DAG JSON
         ▼
┌──────────────────┐
│ 持久化 + 响应      │  → 存入 data/projects/{id}.json
│                   │  → 返回给前端渲染 DAG
└──────────────────┘
```

**拓扑排序算法**：

```
输入: TaskNode[] (含 pre_dependencies)
输出: 拓扑排序后的 TaskNode[] + 关键路径标记

1. 构建入度表 indegree[task_id]
2. 队列初始化: 入度为0的节点入队
3. 拓扑排序:
   while queue not empty:
     node = queue.pop()
     sorted.append(node)
     for successor in node.successors:
       indegree[successor]--
       if indegree[successor] == 0:
         queue.push(successor)
4. 正向计算 ES/EF:
   ES[start] = 0 (或项目开始日期)
   EF[node] = ES[node] + duration[node]
   ES[child] = max(ES[child], EF[parent])
5. 反向计算 LS/LF:
   LF[end] = project_deadline (或 EF[end])
   LS[node] = LF[node] - duration[node]
   LF[parent] = min(LF[parent], LS[child])
6. 关键路径: float[node] = LS[node] - ES[node] == 0
```

#### 3.2.2 进展更新与风险分析流程

```
用户输入进展描述（NL）
        │
        ▼
┌──────────────────┐
│ Parser            │  Claude API: 解析 NL → 节点进度映射
│ (进度解析)        │  输出: [{task_id, progress%, status, notes}]
└────────┬─────────┘
         │ progress_updates[]
         ▼
┌──────────────────┐
│ Scheduler         │  1. 更新指定节点的 progress / status
│ (状态更新)        │  2. 重新计算关键路径（如已完成则标记）
│                   │  3. 推送 node_status 事件 (WebSocket)
└────────┬─────────┘
         │ updated DAG
         ▼
┌──────────────────┐
│ Analyzer          │  扫描 8 个风险维度（见 3.2.3）
│ (engine/analyzer  │  Claude API: 生成风险报告 + 建议
│  .py)             │  推送 risk_alert 事件 (WebSocket)
└────────┬─────────┘
         │ risk_report
         ▼
┌──────────────────┐
│ 前端更新          │  DAG 节点变色 + 风险面板刷新
└──────────────────┘
```

#### 3.2.3 风险分析维度（8 维扫描）

每次进展更新后，Analyzer 自动扫描以下维度：

| 维度 | 检测项 | 算法/方法 |
|------|--------|----------|
| **1. 关键路径风险** | 关键路径上有节点延迟 → 项目整体延期 | 实时重算 float，标记延迟节点的影响链 |
| **2. 多重关键路径** | 存在 ≥2 条关键路径 → 风险指数级上升 | 统计 float=0 的路径数量 |
| **3. 收敛点风险** | 多条入边的汇聚节点（fan-in > 2） | 遍历 DAG 计算入度，高入度节点标记"依赖瓶颈" |
| **4. 缓冲区消耗** | 缓冲消耗超过阈值（绿<1/3 / 黄 1/3~2/3 / 红>2/3） | 三色管理法监控项目缓冲 + 输入缓冲 |
| **5. 依赖链风险** | 长依赖链（深度 > 5）→ 不确定性放大 | DFS 计算从入口出发的最长依赖链 |
| **6. 资源冲突风险** | 同一时段多任务争用同一资源（人/环境） | 按时间段检查资源分配重叠（Critical Chain） |
| **7. 估时置信度** | 低置信度任务（confidence < 0.6）在关键路径上 | 结合 confidence 标记 + 关键路径属性 |
| **8. 近关键路径风险** | float < 总工期 10% 的路径可能成为新的关键路径 | 扫描 float < 0.1 × total_duration 的节点链 |

**风险等级判定**：

```
风险等级 = f(延迟天数, 是否在关键路径, 缓冲消耗占比, 依赖链深度)

🔴 严重 (Critical):
  - 关键路径任务延迟 > 2天
  - 项目缓冲消耗 > 2/3
  - 多条关键路径同时存在

🟡 警告 (Warning):
  - 非关键路径任务延迟 > 3天
  - 项目缓冲消耗 1/3 ~ 2/3
  - 近关键路径上的低置信度任务

🟢 正常 (Normal):
  - 进度符合计划
  - 缓冲消耗 < 1/3
```

#### 3.2.4 WebSocket 实时推送协议

```
服务端 → 客户端（JSON）

1. node_status — 节点状态变更:
   {
     "type": "node_status",
     "data": {
       "task_id": "task_3",
       "progress": 60,
       "status": "in_progress",  // pending | in_progress | completed | delayed | blocked
       "updated_at": "2026-07-22T15:30:00Z"
     }
   }

2. risk_alert — 风险告警:
   {
     "type": "risk_alert",
     "data": {
       "risk_id": "risk_001",
       "level": "critical",       // critical | warning | info
       "dimension": "关键路径延迟",
       "task_id": "task_3",
       "message": "task_3 位于关键路径，已延迟 3 天，预计影响上线日期",
       "suggestion": "建议：1) 增加资源加速 task_3；2) 评估是否可以并行化后续任务"
     }
   }

3. suggestion — 主动建议:
   {
     "type": "suggestion",
     "data": {
       "category": "资源优化",
       "message": "task_5 和 task_8 可并行执行（无直接依赖），建议同时推进以缩短总工期"
     }
   }
```

### 3.3 开发视图（Development View）

#### 3.3.1 目录结构

```
bePm/
├── DESIGN.md                     # 本文档
├── raw.txt                       # 原始需求
├── agent.md                      # Claude Code PM Agent 定义
│
├── backend/                      # Python FastAPI 后端
│   ├── main.py                   # 入口: FastAPI app + WebSocket 注册
│   ├── requirements.txt          # Python 依赖
│   ├── api/
│   │   ├── __init__.py
│   │   ├── projects.py           # REST API: /api/projects/*
│   │   └── ws.py                 # WebSocket: /ws/projects/{id}
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── parser.py             # LLM 调用: NL 输入 → 结构化任务 + 进度映射
│   │   ├── scheduler.py          # 拓扑排序 + 关键路径 + 缓冲区计算
│   │   └── analyzer.py           # 8维风险扫描 + 建议生成
│   ├── models/
│   │   ├── __init__.py
│   │   └── project.py            # Pydantic 数据模型
│   └── data/
│       └── projects/             # JSON 项目文件存储
│           └── {project_id}.json
│
├── frontend/                     # 静态前端
│   ├── index.html                # 主页面 (单页应用)
│   ├── css/
│   │   └── style.css             # 样式
│   └── js/
│       ├── app.js                # 主逻辑: 输入/输出编排
│       ├── api.js                # REST API 封装
│       ├── ws.js                 # WebSocket 客户端
│       └── dag.js                # Cytoscape.js DAG 渲染 + 交互
│
└── frontend/                     # (可选) 如需更重的 React/Vue 前端
```

#### 3.3.2 核心数据模型

```python
# backend/models/project.py

from pydantic import BaseModel
from typing import Optional
from enum import Enum

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DELAYED = "delayed"
    BLOCKED = "blocked"

class TaskNode(BaseModel):
    """DAG 中的一个任务节点"""
    id: str                          # 唯一标识，如 "task_1"
    name: str                        # 任务名称
    description: str                 # 任务描述
    estimated_days: float            # 预估工期（天）
    confidence: float                # 估时置信度 0.0-1.0
    pre_dependencies: list[str]      # 前置任务 ID 列表
    resources: list[str]             # 所需资源（人员/环境）
    progress: float = 0.0            # 进度 0-100
    status: TaskStatus = TaskStatus.PENDING
    start_date: Optional[str] = None # 计划开始日期
    end_date: Optional[str] = None   # 计划结束日期
    actual_start: Optional[str] = None
    actual_end: Optional[str] = None
    notes: str = ""                  # 备注

class DAG(BaseModel):
    """项目的拓扑图"""
    nodes: list[TaskNode]
    edges: list[dict]                # [{"from": "task_1", "to": "task_2"}, ...]

class ScheduleResult(BaseModel):
    """排期结果"""
    topological_order: list[str]     # 拓扑排序后的任务 ID 序列
    critical_path: list[str]         # 关键路径上的任务 ID
    total_duration_days: float       # 预计总工期
    project_buffer_days: float       # 项目缓冲区大小
    node_times: dict[str, dict]      # {task_id: {ES, EF, LS, LF, float}}

class RiskItem(BaseModel):
    """单条风险"""
    risk_id: str
    level: str                       # critical | warning | info
    dimension: str                   # 风险维度
    task_id: Optional[str]           # 关联任务
    message: str
    suggestion: str

class Project(BaseModel):
    """完整的项目实体"""
    id: str
    name: str
    description: str
    created_at: str
    updated_at: str
    deadline: str                    # 项目截止日期
    dag: DAG
    schedule: Optional[ScheduleResult]
    risks: list[RiskItem]
    buffers: dict                    # {project_buffer: {total, consumed}, feeding_buffers: [...]}
```

#### 3.3.3 API 路由定义

```
POST   /api/projects                   创建项目（提交项目描述文本）
  Request:  { "description": "项目自然语言描述...", "deadline": "2026-12-31" }
  Response: { "project_id": "...", "dag": {...}, "schedule": {...} }

GET    /api/projects                   列出所有项目
GET    /api/projects/{id}              获取项目详情
DELETE /api/projects/{id}              删除项目

POST   /api/projects/{id}/schedule     重新触发自动排期
  Request:  { "additional_info": "补充信息..." }  (可选)
  Response: { "dag": {...}, "schedule": {...} }

POST   /api/projects/{id}/progress     提交进展更新
  Request:  { "progress_text": "进展描述..." }
  Response: { "updated_nodes": [...], "risks": [...] }

GET    /api/projects/{id}/graph        获取 DAG 拓扑数据（前端渲染用）
  Response: { "nodes": [...], "edges": [...], "critical_path": [...], "risks": [...] }

PUT    /api/projects/{id}/task/{task_id}  手动编辑某个节点
  Request:  { "progress": 80, "status": "in_progress" }

WS     /ws/projects/{id}               订阅实时推送
  → Server: node_status | risk_alert | suggestion
```

### 3.4 物理视图（Physical View）

#### 3.4.1 开发环境

```
┌──────────────────────────────────────────────┐
│  开发机 (localhost)                           │
│                                              │
│  ┌────────────────┐   ┌───────────────────┐ │
│  │ 前端开发服务器   │   │ 后端 FastAPI       │ │
│  │ (python -m      │   │ uvicorn main:app   │ │
│  │  http.server    │   │ --port 48080       │ │
│  │  :8080)         │   │                    │ │
│  │                 │   │ 静态文件 serve 自   │ │
│  │                 │   │ frontend/ 目录     │ │
│  └───────┬─────────┘   └─────────┬─────────┘ │
│          │ HTTP/WS               │           │
│          └───────────┬───────────┘           │
│                      │                       │
│              ┌───────▼─────────┐             │
│              │ Claude API      │ (外部)       │
│              │ api.anthropic   │             │
│              │ .com            │             │
│              └─────────────────┘             │
│                                              │
│              ┌─────────────────┐             │
│              │ JSON 文件存储    │             │
│              │ data/projects/  │             │
│              └─────────────────┘             │
└──────────────────────────────────────────────┘
```

#### 3.4.2 生产环境部署

```
┌──────────────────────────────────────────────┐
│  服务器                                       │
│                                              │
│  ┌──────────────────────────────────────┐    │
│  │ Nginx (可选)                          │    │
│  │ · /api/* → proxy to :48080           │    │
│  │ · /ws/* → proxy ws to :48080         │    │
│  │ · / → static files (frontend/)       │    │
│  └──────────────┬───────────────────────┘    │
│                 │                             │
│  ┌──────────────▼───────────────────────┐    │
│  │ Python FastAPI (uvicorn)              │    │
│  │ · 单进程，多 worker 可选               │    │
│  │ · 端口 48080                          │    │
│  └──────────────┬───────────────────────┘    │
│                 │                             │
│  ┌──────────────▼──────┐  ┌──────────────┐  │
│  │ data/projects/      │  │ 外部 API      │  │
│  │ (JSON 文件 / volume) │  │ (Claude API)  │  │
│  └─────────────────────┘  └──────────────┘  │
└──────────────────────────────────────────────┘
```

**关键物理决策**：

| 决策 | 选择 | 理由 |
|------|------|------|
| 存储 | JSON 文件 | 用户要求，可读性好，Git 可跟踪，会话间可恢复 |
| 静态资源 | 后端直接 serve frontend/ | 简化部署，无需额外静态服务器 |
| WebSocket | 同一端口 48080 | FastAPI 原生支持，无需额外端口 |
| 并发模型 | Async/Await (asyncio) | FastAPI 原生异步，适合 I/O 密集的 LLM 调用 |
| 无数据库 | JSON 文件 | 个人项目管理工具，规模可控 |

### 3.5 场景视图（Scenarios）— 核心用例

#### 场景 1：创建项目并自动排期

```
用户操作:
  1. 打开 Web 前端 (http://localhost:48080)
  2. 在输入框中粘贴项目描述:
     "我们计划开发一个电商平台，需要在12月31日前上线。
      后端API开发约需20天，前端开发约需15天，两者可以并行。
      但前端需要后端先完成接口设计（2天）。
      数据库设计需要3天，完成后端开发才能开始。
      联调测试需要5天，部署上线需要1天。"
  3. 设置截止日期: 2026-12-31
  4. 点击"自动排期"

系统处理:
  1. Parser 调用 Claude API 解析:
     - task_1: 数据库设计 (3天, confidence:0.9)
     - task_2: 接口设计 (2天, confidence:0.9)
     - task_3: 后端API开发 (20天, pre=["task_1","task_2"], confidence:0.7)
     - task_4: 前端开发 (15天, pre=["task_2"], confidence:0.7)
     - task_5: 联调测试 (5天, pre=["task_3","task_4"], confidence:0.8)
     - task_6: 部署上线 (1天, pre=["task_5"], confidence:0.95)

  2. Scheduler:
     - 拓扑排序: task_1→task_2→task_3→task_5→task_6 (关键路径 31天)
                                    └─task_4──┘
     - 计算时间表，设置项目缓冲 = 31×0.5 = 15.5天
     - 总工期 31天 + 缓冲，安全完成时间约 46.5天

  3. 前端渲染:
     - Cytoscape.js 绘制 DAG，关键路径红色高亮
     - 右侧面板显示排期详情和时间线

预期结果:
  - DAG 图展示所有任务及其依赖关系
  - 关键路径清晰可见
  - 缓冲区域绿色标示
```

#### 场景 2：提交进展并获取风险分析

```
用户操作:
  1. 项目执行一周后，在进展输入框输入:
     "数据库设计已经完成，接口设计也完成了。
      后端API开发正在进行中，完成了约30%。
      但是负责后端的小王请假了3天，进度比预期慢。"
  2. 点击"更新进展"

系统处理:
  1. Parser 解析进展:
     - task_1: progress=100%, status="completed"
     - task_2: progress=100%, status="completed"
     - task_3: progress=30%, status="in_progress"
     - 识别风险信号: "小王请假3天" → task_3 可能延迟

  2. Analyzer 扫描风险:
     🔴 严重: task_3 位于关键路径，已延迟 1.5 天(预计 vs 实际进度)
     🔴 严重: 项目缓冲消耗 10% (1.5/15.5)，当前处于绿色区但速度需警惕
     🟡 警告: task_3 置信度仅 0.7，实际执行证实低估了复杂度
     🟡 警告: 存在资源单点风险 (task_3 依赖关键人员小王)

  3. 建议生成:
     - "task_3 是当前唯一的关键路径瓶颈，建议立即安排备份人员接手"
     - "task_4 (前端) 可以先行推进独立模块，减少等待浪费"
     - "考虑将 task_3 拆分为更小的可交付单元，加速部分功能验证"

  4. 前端更新:
     - task_1, task_2 节点变绿 (已完成)
     - task_3 节点变黄 (进行中但风险)
     - 风险面板弹出红色告警卡片
     - WebSocket 实时推送更新
```

#### 场景 3：通过 PM Agent 对话管理项目

```
用户（在 Claude Code 中）:
  > /agent PM
  > 帮我看看电商平台项目目前的风险情况

PM Agent:
  [调用 backend API: GET /api/projects/ecommerce-platform]
  
  当前风险等级: 🟡 2个严重, 3个警告
  
  🔴 关键路径延迟:
  - task_3 (后端API开发) 进度 30%，比计划落后 1.5 天
  - 关键人员小王请假，预计还需 3 天恢复
  
  建议立即行动:
  1. 安排备份开发者接手 task_3
  2. 前端团队先推进不需要 API 的模块
  3. 明天站会重新评估是否调整上限日期
  
  需要我帮你更新进度或重新排期吗？
```

---

## 四、PM Agent 设计（`agent.md`）

```markdown
# PM Agent

你是 bePm 的项目管理助手，专注于帮助用户进行项目排期、进展追踪和风险分析。

## 核心能力

1. **项目解析与排期**: 解析用户的项目描述（自然语言/文件），生成结构化的任务 DAG
2. **进展更新**: 解析用户的进展描述，更新各节点进度
3. **风险分析**: 自动扫描 8 个风险维度，提供可执行的建议
4. **排期调整**: 根据变化重新计算关键路径和时间表

## 可用工具

- read_project(project_id) → 获取项目完整状态
- create_schedule(description, deadline) → 创建排期
- update_progress(project_id, progress_text) → 更新进展
- analyze_risk(project_id) → 执行风险扫描
- edit_task(project_id, task_id, updates) → 手动编辑节点

## 交互原则

- 以数据说话：分析项目时引用具体的节点、天数和百分比
- 建议可执行：每条建议包含具体行动步骤
- 风险优先级：始终先讲最严重的风险
```

---

## 五、关键设计决策

| 决策 | 选择 | 替代方案 | 理由 |
|------|------|---------|------|
| 后端语言 | Python (FastAPI) | Node.js (Express), Rust | LLM 生态最佳，开发速度快 |
| 前端框架 | 纯 JS + Cytoscape.js | React + Cytoscape.js | 轻量，规模可控，避免过度工程 |
| 数据存储 | JSON 文件 | SQLite, PostgreSQL | 用户要求，Git 可跟踪，会话恢复 |
| AI 引擎 | Claude API (Anthropic SDK) | OpenAI, 本地模型 | 用户环境已有，质量最佳 |
| DAG 算法 | Kahn 拓扑排序 + CPM | 约束求解器 | 经典、可解释、轻量 |
| 实时通信 | WebSocket | SSE, 轮询 | 双向通信，即时推送 |

---

## 六、后续扩展（V1 → V2）

| 方向 | 内容 |
|------|------|
| 多项目管理 | 跨项目的资源冲突检测 |
| 工时统计 | 实际工时 vs 预估工时的偏差分析 |
| 甘特图 | 在 DAG 基础上增加时间轴线视图 |
| 历史学习 | 基于历史项目的估时偏差，优化新项目置信度模型 |
| 日历集成 | 同步到 Google Calendar / Outlook |
| 协作 | 多人编辑、评论、@提醒 |
| MCP 集成 | 作为 MCP server 供 Claude Code 调用 |

---

## 附录 A：参考资源

- [Nexus 架构文档](../10_nexus/doc/design/ARCHITECTURE.md)
- [GAO Schedule Assessment Guide](https://guides.gaoinnovations.gov/schedule-guide/)
- [PMI Risk-Based Scheduling and Analysis](https://www.pmi.org/learning/library/risk-based-scheduling-analysis-9033)
- [Critical Chain Project Management](https://www.spasvo.com/ceshi/xmgl/fxgl/2013515150151.html)
