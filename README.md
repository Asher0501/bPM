# bePm — AI 辅助项目排期管理系统

输入自然语言描述，自动解析任务、推断依赖、生成 DAG 拓扑排序图。输入进展描述，自动更新进度并扫描风险维度，给出可执行建议。

## 核心功能

- **NL → 结构化 DAG**：自然语言描述项目，AI 解析为任务列表 + 依赖关系 + 关键路径
- **实时可视化**：Cytoscape.js 渲染 DAG 图，节点颜色标注进度和风险状态
- **进展驱动的风险分析**：自然语言更新进展 → AI 映射到节点 → 重算缓冲区 → 扫描 8 大风险维度
- **标签分组聚合**：给节点打标签，按标签维度聚合成大节点，查看不同层级依赖关系
- **Web 前端 + API 双入口**：浏览器操作或程序化调用

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+ / FastAPI / Uvicorn |
| 前端 | HTML5 + CSS3 + Vanilla JS / Cytoscape.js / Dagre |
| AI | Anthropic SDK / OpenAI SDK / CLI（可插拔） |
| 持久化 | JSON 文件 (`.projects/`) |
| 实时通信 | WebSocket |

## 快速启动

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 配置 LLM

`~/.claude/settings.json` 的 `env` 段：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "sk-xxx",
    "ANTHROPIC_MODEL": "deepseek-v4-pro[1m]"
  }
}
```

支持三种 LLM 后端：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_PROVIDER` | `sdk` 或 `cli` | `sdk` |
| `LLM_SDK_TYPE` | `anthropic` 或 `openai`（仅 sdk 模式） | `anthropic` |
| `LLM_CLI_COMMAND` | CLI 命令模板（仅 cli 模式） | `claude -p "{prompt}" --output-format json` |

### 3. 启动

```bash
# Windows：双击 start.bat（自动打开浏览器）
# macOS / Linux：
cd backend && python main.py
```

浏览器访问 **http://127.0.0.1:48090**

## API 速查表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查（含 LLM 模式信息） |
| `GET` | `/api/projects` | 项目列表 |
| `POST` | `/api/projects` | 创建项目（自动排期） |
| `GET` | `/api/projects/{id}` | 项目详情 |
| `DELETE` | `/api/projects/{id}` | 删除项目 |
| `POST` | `/api/projects/{id}/command` | 统一 NL 命令入口（AI 自动判断意图） |
| `GET` | `/api/projects/{id}/graph` | DAG 拓扑数据 |
| `GET` | `/api/projects/{id}/tags` | 收集项目中所有标签 |
| `GET` | `/api/projects/{id}/grouped?tags=a,b` | 按标签分组聚合 DAG |
| `PUT` | `/api/projects/{id}/nodes/{nid}` | 编辑节点 |
| `DELETE` | `/api/projects/{id}/nodes/{nid}` | 删除节点 |
| `POST` | `/api/projects/{id}/edges` | 添加依赖边 |
| `DELETE` | `/api/projects/{id}/edges/{src}/{tgt}` | 删除依赖边 |
| `WS` | `/ws/projects/{id}` | WebSocket 实时推送 |

## 意图系统

6 种纯原子拓扑操作，LLM 自行组合完成复杂场景：

```
add_node      — 创建节点
delete_node   — 删除节点
edit_node     — 修改节点（name/progress/status/notes/tags/...）
add_edge      — 添加依赖边
remove_edge   — 移除依赖边
ask_user      — 反问用户
```

例如"在 A 和 B 之间插入 C"→ LLM 输出 `[add_node(C), remove_edge(A,B), add_edge(A,C), add_edge(C,B)]`

## 标签分组

1. 编辑节点时填入标签（逗号分隔），如 `backend, auth, P0`
2. DAG 顶栏出现标签 chips，点击选中
3. 同标签节点聚合成大节点——工期子图调度、进度加权、边去重
4. 支持多标签同时选中；冲突节点弹提示、不执行

## 项目结构

```
bePm/
├── README.md
├── DESIGN.md                # 详细设计文档
├── agent.md                 # Claude Code PM Agent 定义
├── start.bat                # Windows 一键启动（自动开浏览器）
├── backend/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置管理（API Key 等）
│   ├── requirements.txt     # Python 依赖
│   ├── prompts/             # LLM Prompt 文件（.md，可独立编辑）
│   ├── api/
│   │   ├── projects.py      # REST API 路由
│   │   └── ws.py            # WebSocket 管理
│   ├── engine/
│   │   ├── parser.py        # NL 解析（双层 LLM）
│   │   ├── scheduler.py     # 拓扑排序 + 关键路径
│   │   ├── intents.py       # 原子意图定义 + 编排映射
│   │   └── llm_provider.py  # LLM 执行器（SDK/CLI，可插拔）
│   └── models/
│       └── project.py       # 数据模型
├── frontend/
│   ├── index.html           # 单页应用
│   ├── css/style.css        # 样式（设计系统驱动）
│   └── js/
│       ├── app.js           # 主逻辑
│       ├── api.js           # API 封装
│       ├── dag.js           # DAG 可视化
│       └── ws.js            # WebSocket 客户端
└── .projects/               # 项目数据持久化（自动生成）
```
