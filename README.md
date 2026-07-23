# bePm — AI 辅助项目排期管理系统

输入自然语言描述，自动解析任务、推断依赖、生成 DAG 拓扑排序图。输入进展描述，自动更新进度并扫描 8 大风险维度，给出可执行建议。

## 核心功能

- **NL → 结构化 DAG**：用自然语言描述项目，AI 自动解析为任务列表 + 依赖关系 + 关键路径
- **实时可视化**：Cytoscape.js 渲染 DAG 图，节点颜色标注进度和风险状态
- **进展驱动的风险分析**：自然语言更新进展 → AI 映射到节点 → 重算缓冲区 → 扫描全部风险维度
- **双入口**：Web 前端（浏览器）+ Claude Code PM Agent（对话式）
- **WebSocket 实时推送**：节点状态变更、风险告警即时同步

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+ / FastAPI / Uvicorn |
| 前端 | HTML5 + CSS3 + Vanilla JS / Cytoscape.js / Dagre |
| AI | Claude API (Anthropic SDK) |
| 持久化 | JSON 文件 (`.projects/`) |
| 实时通信 | WebSocket |

## 前置条件

1. **Python 3.11+** 已安装
2. **Anthropic API Key** 已配置（二选一）：
   - 环境变量：`ANTHROPIC_API_KEY`（或 `ANTHROPIC_AUTH_TOKEN`）
   - Claude Code 配置：在 `~/.claude/settings.json` 的 `env` 段中设置 `ANTHROPIC_API_KEY`
3. （可选）如需使用 DeepSeek 等代理，设置 `ANTHROPIC_BASE_URL` 和 `ANTHROPIC_MODEL`

## 快速启动

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 启动服务

**方式 A：直接运行（推荐）**

```bash
# Windows
start.bat

# macOS / Linux
cd backend && python main.py
```

**方式 B：指定端口**

```bash
cd backend
python main.py
# 默认监听 http://127.0.0.1:48090
# 可通过环境变量修改：HOST=0.0.0.0 PORT=8080 python main.py
```

### 3. 打开前端

浏览器访问 **http://127.0.0.1:48090**

启动成功后会看到：

```
============================================
  bePm - AI 辅助项目排期管理系统
============================================

  前端: http://127.0.0.1:48090
  API:  http://127.0.0.1:48090/api/health
  WebSocket: ws://127.0.0.1:48090/ws/projects/{id}

  按 Ctrl+C 停止服务
============================================
```

## 使用方式

### 方式一：Web 前端（可视化操作）

#### 创建项目

1. 点击 **「+ 新建项目」**
2. 用自然语言描述你的项目，例如：

```
我们计划开发一个电商平台，12月31日前上线。
数据库设计需要3天，由后端负责。
后端API开发需要20天，依赖数据库设计和接口设计。
前端开发需要15天，依赖接口设计。
接口设计需要2天，由后端和前端共同完成。
联调测试需要5天，依赖后端API和前端都完成。
部署上线需要1天，依赖联调测试通过。
```

3. 选择截止日期，点击 **「自动排期」**
4. AI 自动解析生成 DAG 图，标注关键路径和缓冲区

#### 更新进展

1. 在「项目进展」面板输入进展描述，例如：

```
数据库设计已经完成，后端API开发进度约30%，遇到了一些性能问题
```

2. AI 自动判断意图（更新进度 / 添加节点 / 删除节点 / 添加依赖）
3. DAG 图实时更新，风险面板同步刷新

#### DAG 交互

| 操作 | 说明 |
|------|------|
| 点击节点 | 查看详情 / 编辑 / 删除 |
| 拖拽节点 | 调整布局 |
| 滚轮缩放 | 放大缩小 |
| 颜色含义 | 🟢已完成 🔵进行中 ⚫未开始 🔴延迟 🟡阻塞 |
| 红色边框 | 关键路径节点 |

### 方式二：Claude Code PM Agent（对话式）

1. 确保后端已启动
2. 在 Claude Code 中加载 `agent.md`：

```
claude --mcp-config agent.md
```

3. 用自然语言对话即可：

```
你: 帮我排期一个项目：开发一个移动App，需要UI设计5天、前端开发15天、
    后端开发15天、测试5天。UI和后端可以并行，前端依赖UI，测试依赖前后端。
    12月底上线。

PM Agent: [调用 API 创建项目，展示 DAG 结构和风险分析]

你: UI 设计完成了，前端开始做了，进度大概20%

PM Agent: [更新进度，发现关键路径偏移，提示风险]
```

### 方式三：REST API（程序化调用）

```bash
# 创建项目
curl -X POST http://127.0.0.1:48090/api/projects \
  -H "Content-Type: application/json" \
  -d '{"description": "开发一个登录模块：数据库设计2天，API开发3天（依赖数据库），前端页面2天（依赖API）。", "deadline": "2026-08-15"}'

# 列出所有项目
curl http://127.0.0.1:48090/api/projects

# 获取项目详情
curl http://127.0.0.1:48090/api/projects/{project_id}

# 获取 DAG 图数据
curl http://127.0.0.1:48090/api/projects/{project_id}/graph

# 更新进展
curl -X POST http://127.0.0.1:48090/api/projects/{project_id}/progress \
  -H "Content-Type: application/json" \
  -d '{"progress_text": "数据库设计完成了，API开发进度50%"}'

# 重新排期
curl -X POST http://127.0.0.1:48090/api/projects/{project_id}/schedule

# 删除项目
curl -X DELETE http://127.0.0.1:48090/api/projects/{project_id}
```

## API 速查表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/projects` | 项目列表 |
| `POST` | `/api/projects` | 创建项目（自动排期） |
| `GET` | `/api/projects/{id}` | 项目详情 |
| `DELETE` | `/api/projects/{id}` | 删除项目 |
| `POST` | `/api/projects/{id}/schedule` | 重新排期 |
| `POST` | `/api/projects/{id}/progress` | 提交进展更新 |
| `GET` | `/api/projects/{id}/graph` | DAG 拓扑数据 |
| `POST` | `/api/projects/{id}/nodes` | 添加节点 |
| `PUT` | `/api/projects/{id}/nodes/{nid}` | 编辑节点 |
| `DELETE` | `/api/projects/{id}/nodes/{nid}` | 删除节点 |
| `POST` | `/api/projects/{id}/edges` | 添加依赖边 |
| `DELETE` | `/api/projects/{id}/edges/{src}/{tgt}` | 删除依赖边 |
| `WS` | `/ws/projects/{id}` | WebSocket 实时推送 |

## 项目结构

```
bePm/
├── README.md                # 本文件
├── DESIGN.md                # 详细设计文档
├── agent.md                 # Claude Code PM Agent 定义
├── start.bat                # Windows 启动脚本
├── backend/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置管理（API Key 等）
│   ├── requirements.txt     # Python 依赖
│   ├── api/
│   │   ├── projects.py      # REST API 路由
│   │   └── ws.py            # WebSocket 管理
│   ├── engine/
│   │   ├── parser.py        # NL 解析（LLM 调用）
│   │   ├── scheduler.py     # 拓扑排序 + 关键路径
│   │   └── intents.py       # 意图解析 + 原子操作
│   ├── models/
│   │   └── project.py       # 数据模型定义
│   └── tests/               # 测试用例
├── frontend/
│   ├── index.html           # 单页应用入口
│   ├── css/style.css        # 样式
│   └── js/
│       ├── app.js           # 主应用逻辑
│       ├── api.js           # REST API 封装
│       ├── dag.js           # DAG 可视化（Cytoscape.js）
│       └── ws.js            # WebSocket 客户端
├── doc/
│   └── issues.md            # 已知问题记录
└── .projects/               # 项目数据持久化（自动生成）
    └── {project_id}/
        ├── project.json
        └── messages.json
```

## 架构概览

```
┌─────────────────────────────────────────────────┐
│  用户交互层                                      │
│  ┌──────────────┐  ┌─────────────────────────┐  │
│  │ Web 前端      │  │ Claude Code PM Agent    │  │
│  │ Cytoscape.js  │  │ (agent.md 对话式)       │  │
│  └──────┬───────┘  └───────────┬─────────────┘  │
│         │ HTTP/WS              │ Tool Calls      │
└─────────┼──────────────────────┼─────────────────┘
          ▼                      ▼
┌─────────────────────────────────────────────────┐
│  Python FastAPI (backend/main.py)               │
│  REST API + WebSocket + Static Files             │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Parser   │  │Scheduler │  │Analyzer  │      │
│  │ NL→JSON  │  │拓扑排序  │  │风险分析  │      │
│  └──────────┘  └──────────┘  └──────────┘      │
│                                                  │
│  数据层: JSON 文件持久化 (.projects/)             │
└─────────────────────────────────────────────────┘
```

## 配置说明

### API Key 配置

项目会自动按以下优先级查找 Anthropic API Key：

1. 环境变量 `ANTHROPIC_API_KEY`
2. 环境变量 `ANTHROPIC_AUTH_TOKEN`
3. `~/.claude/settings.json` 的 `env.ANTHROPIC_API_KEY`
4. 项目 `.claude/settings.local.json` 的 `env.ANTHROPIC_API_KEY`

### 自定义模型

```json
// ~/.claude/settings.json
{
  "env": {
    "ANTHROPIC_API_KEY": "sk-ant-...",
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_MODEL": "deepseek-chat"
  }
}
```

### SDK 后端切换

项目支持可插拔的 LLM SDK 后端，通过 `LLM_SDK_TYPE` 环境变量切换：

| LLM_SDK_TYPE | 需要的包 | 兼容服务 |
|--------------|---------|---------|
| `anthropic`（默认） | `pip install anthropic` | Anthropic 官方 / 兼容端点 |
| `openai` | `pip install openai` | OpenAI / DeepSeek / 通义千问 / Ollama 等 |
| `cli` | 设置 `LLM_PROVIDER=cli` | 任何命令行工具，不限 SDK |

```bash
# 示例：用 DeepSeek 的 OpenAI 兼容端点
export LLM_SDK_TYPE=openai
export ANTHROPIC_BASE_URL=https://api.deepseek.com/v1
export ANTHROPIC_MODEL=deepseek-chat
export ANTHROPIC_API_KEY=sk-your-deepseek-key
```

**添加新 SDK 后端**只需编辑 `backend/engine/llm_provider.py`：
1. 继承 `LlmSdkProvider` 类，实现 `execute()` 方法
2. 在 `_SDK_PROVIDER_REGISTRY` 中注册一行

## 风险分析框架

每次更新进展后，AI 自动扫描以下 8 个维度：

| 优先级 | 维度 | 关注点 |
|--------|------|--------|
| P0 | 关键路径延迟 | 关键路径上任何延迟 = 项目整体延期 |
| P0 | 缓冲区消耗 | 缓冲 > 2/3 = 严重预警 |
| P1 | 多重关键路径 | >1 条关键路径 = 风险指数级上升 |
| P1 | 收敛点瓶颈 | 多入度节点 = 阻塞风险集中 |
| P1 | 资源冲突 | 人员/环境争用 = 实际效率远低于预期 |
| P2 | 依赖链深度 | 长链级联放大不确定性 |
| P2 | 估时置信度 | 低置信度 + 关键路径 = 高风险 |
| P2 | 近关键路径 | float < 10% = 明天可能变关键路径 |

## 常见问题

**Q: Windows 下中文乱码？**
A: `start.bat` 已自动设置 UTF-8 编码。手动运行时确保 `chcp 65001`。

**Q: 如何清空所有项目数据？**
A: 删除 `.projects/` 目录即可（每个项目独立文件夹，不影响代码）。

**Q: 前端 DAG 不显示？**
A: 检查网络是否能访问 CDN（Cytoscape.js 从 unpkg 加载）。离线环境下可下载到本地 `frontend/js/vendor/`。

**Q: API 返回 500 错误？**
A: 检查 Anthropic API Key 是否有效，以及 `backend/server.log`（如果存在）。
