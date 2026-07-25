# -*- coding: utf-8 -*-
"""AI Parser — 调用 LLM 解析自然语言输入

上下文构建与执行解耦（参考 Nexus Provider 模式）：
  parser.py 只构建 LlmContext → 交由 LlmExecutor 执行 → 取 LlmResult.content
"""

import json
import logging

from config import get_anthropic_config
from engine.llm_provider import LlmContext, LlmExecutor

logger = logging.getLogger(__name__)


def _get_executor() -> LlmExecutor:
    """获取 LLM 执行器。

    LLM_PROVIDER 控制执行模式（sdk/cli），默认 sdk
    LLM_SDK_TYPE  控制 SDK 后端（anthropic/openai/...），默认 anthropic
    """
    import os
    provider = os.environ.get("LLM_PROVIDER", "sdk").strip().lower()
    if provider not in ("sdk", "cli"):
        logger.warning("未知 LLM_PROVIDER=%s，回退为 sdk", provider)
        provider = "sdk"

    sdk_type = os.environ.get("LLM_SDK_TYPE", "anthropic").strip().lower()

    return LlmExecutor(provider=provider, sdk_type=sdk_type)  # type: ignore[arg-type]


def _call_llm(system_prompt: str, user_message: str, max_tokens: int = 32768) -> str:
    """统一的 LLM 调用入口——构建 LlmContext 并执行，返回纯文本"""
    cfg = get_anthropic_config()

    ctx = LlmContext(
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        model=cfg.get("model", "claude-sonnet-4-20250514"),
        max_tokens=max_tokens,
        api_key=cfg.get("api_key"),
        base_url=cfg.get("base_url"),
    )

    executor = _get_executor()
    result = executor.execute(ctx)
    return result.content


# ---- Prompt 加载：优先外部文件，代码内置为 fallback ----

import os as _os
_PROMPTS_DIR = _os.path.join(_os.path.dirname(__file__), "..", "prompts")

_DEFAULTS: dict[str, str] = {}  # 代码内置 fallback


def _load_prompt(name: str, default: str) -> str:
    """从 prompts/{name}.md 加载 prompt，不存在则用代码内置默认值"""
    filepath = _os.path.join(_PROMPTS_DIR, f"{name}.md")
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            logger.info("从文件加载 prompt: %s", filepath)
            return content
    except FileNotFoundError:
        logger.debug("prompt 文件不存在，使用内置默认: %s", filepath)
    except Exception as e:
        logger.warning("加载 prompt 文件失败 (%s): %s，使用内置默认", filepath, e)
    return default


# ---- System Prompts ----

PROJECT_PARSE_PROMPT = """你是一个专业的软件项目管理助手。你的任务是将用户的项目描述解析为结构化的任务列表。

## 解析规则
1. 从描述中提取所有独立的任务/阶段，每个任务必须是可以独立完成并验收的工作单元
2. 分析任务之间的依赖关系（哪些任务必须先完成，哪些可以并行）
3. 估计每个任务的工期（天数），以及你对该估计的置信度（0.0-1.0）
4. 识别每个任务的特性Owner（负责该任务的人员或角色）
5. 如果有项目截止日期，请记录

## 输出格式
请严格输出以下 JSON 格式（不要包含其他文字）：
```json
{
  "project_name": "项目名称",
  "tasks": [
    {
      "id": "task_1",
      "name": "任务名称",
      "description": "任务详细描述",
      "estimated_days": 5.0,
      "confidence": 0.8,
      "pre_dependencies": [],
      "resources": ["后端开发"]
    }
  ],
  "analysis": "简要分析项目结构和排期建议"
}
```

## 置信度指南
- 0.9-1.0: 任务非常明确，类似任务有丰富历史数据
- 0.7-0.9: 任务清楚但可能有一些未知因素
- 0.5-0.7: 任务存在较大不确定性
- <0.5: 需求模糊，建议进一步拆分或澄清
"""

PROGRESS_PARSE_PROMPT = """你是一个专业的软件项目管理助手。你的任务是将用户的进展描述映射到项目各个节点的进度更新。

## 输入
你会收到：
1. 当前项目状态（包含所有任务节点及其当前进度）
2. 用户的最新进展描述

## 解析规则
1. 识别进展描述中提到的每个任务，映射到对应的 task_id
2. 判断每个任务的完成进度 (0-100)
3. 判断任务状态: pending | in_progress | completed | delayed | blocked
4. 识别进展描述中暗示的风险信号：
   - 人员请假、离职
   - 技术难题、阻塞
   - 延期、加班
   - 需求变更
   - 资源冲突
   - 质量问题

## 输出格式
请严格输出以下 JSON 格式（不要包含其他文字）：
```json
{
  "updates": [
    {
      "task_id": "task_1",
      "progress": 100,
      "status": "completed",
      "notes": "已按时完成，质量符合预期"
    }
  ],
  "summary": "对本次进展的整体评价",
  "risk_signals": ["人员请假可能影响后续进度", "技术难点需要额外资源"]
}
```
"""

RISK_ANALYSIS_PROMPT = """你是一个专业的软件项目风险管理专家。请对以下项目状态进行全面的风险分析。

## 分析维度
1. **关键路径风险**: 关键路径上是否有延迟？延迟的传导效应？
2. **多重关键路径**: 是否存在多条关键路径（增加系统性风险）？
3. **收敛点风险**: 入度高的汇聚节点是否是瓶颈？
4. **缓冲区消耗**: 项目缓冲消耗是否健康？
5. **依赖链深度**: 长依赖链是否存在不确定性放大？
6. **资源冲突**: 同一时段是否有多任务争用同一资源？
7. **估时置信度**: 低置信度任务是否在关键路径上？
8. **近关键路径风险**: 浮动时间小的路径是否会成为新的关键路径？

## 输出格式
请严格输出以下 JSON 格式（不要包含其他文字）：
```json
{
  "risks": [
    {
      "risk_id": "risk_1",
      "level": "critical",
      "dimension": "关键路径延迟",
      "task_id": "task_3",
      "message": "task_3位于关键路径，已延迟2天，将直接影响上线日期",
      "suggestion": "建议：1) 为task_3增加人手加速 2) 检查后续任务是否可以并行化 3) 与产品确认是否有可延后的功能"
    }
  ],
  "overall_assessment": "整体风险评估",
  "top_actions": ["优先处理的关键行动1", "行动2", "行动3"]
}
```

风险等级定义：
- critical: 会直接导致项目延期或失败的风险
- warning: 需要关注但不会立即导致失败的风险
- info: 一般性提示和改进建议
"""


# ---- Parser Functions ----


def parse_project(description: str, deadline: str = "", additional_info: str = "") -> dict:
    """将自然语言项目描述解析为结构化任务"""
    full_input = description
    if deadline:
        full_input += f"\n\n项目截止日期: {deadline}"
    if additional_info:
        full_input += f"\n\n补充说明: {additional_info}"

    max_retries = 3
    last_error = None
    for attempt in range(max_retries):
        try:
            content = _call_llm(PROJECT_PARSE_PROMPT, full_input)
        except Exception as api_err:
            logger.warning(
                "parse_project LLM 调用失败 (attempt %d/%d): %s",
                attempt + 1, max_retries, str(api_err)[:120],
            )
            if attempt < max_retries - 1:
                continue
            return {"project_name": "未命名项目", "tasks": [], "analysis": "LLM 解析不可用: " + str(api_err)[:80]}

        try:
            return _extract_json(content, schema_type="project")
        except JsonExtractError as e:
            last_error = e
            if attempt < max_retries - 1:
                error_feedback = (
                    f"\n\n## ⚠️ 上一次输出校验失败\n"
                    f"错误码: {e.error_code}\n"
                    f"错误信息: {e.message}\n"
                    f"原始输出: {e.raw_preview}\n\n"
                    f"请修正以上问题，重新输出正确的 JSON。只输出 JSON，不要解释。"
                )
                full_input = full_input + error_feedback

    logger.warning("parse_project 重试耗尽 (%d 次), 返回空任务列表", max_retries)
    return {"project_name": "未命名项目", "tasks": [], "analysis": "LLM 解析失败，请检查 API 可用性后重试"}




def parse_progress(project_state: dict, progress_text: str) -> dict:
    """将自然语言进展描述映射到节点进度更新"""
    tasks_summary = []
    for node in project_state.get("nodes", []):
        tasks_summary.append({
            "id": node["id"],
            "name": node["name"],
            "progress": node.get("progress", 0),
            "status": node.get("status", "pending"),
            "estimated_days": node.get("estimated_days", 0),
            "is_critical": node.get("is_critical", False),
            "pre_dependencies": node.get("pre_dependencies", []),
        })

    state_str = json.dumps(tasks_summary, ensure_ascii=False, indent=2)
    full_input = f"""## 当前项目状态\n{state_str}\n\n## 用户进展描述\n{progress_text}"""

    max_retries = 3
    last_error = None
    for attempt in range(max_retries):
        try:
            content = _call_llm(PROGRESS_PARSE_PROMPT, full_input)
        except Exception as api_err:
            logger.warning(
                "parse_progress LLM 调用失败 (attempt %d/%d): %s",
                attempt + 1, max_retries, str(api_err)[:120],
            )
            if attempt < max_retries - 1:
                continue
            return {"updates": [], "summary": "LLM 解析不可用: " + str(api_err)[:80], "risk_signals": []}

        try:
            return _extract_json(content, schema_type="progress")
        except JsonExtractError as e:
            last_error = e
            if attempt < max_retries - 1:
                error_feedback = (
                    f"\n\n## ⚠️ 上一次输出校验失败\n"
                    f"错误码: {e.error_code}\n"
                    f"错误信息: {e.message}\n"
                    f"原始输出: {e.raw_preview}\n\n"
                    f"请修正以上问题，重新输出正确的 JSON。只输出 JSON，不要解释。"
                )
                full_input = full_input + error_feedback

    logger.warning("parse_progress 重试耗尽 (%d 次), 返回空更新列表", max_retries)
    return {"updates": [], "summary": "进展解析失败，请检查 API 可用性后重试", "risk_signals": []}


def analyze_risks(project_state: dict) -> dict:
    """对项目状态进行全面的风险分析（LLM + 算法兜底）

    优先使用 LLM 进行风险分析。若 LLM 不可用（API key 未配置）
    或分析失败，自动降级到纯算法风险扫描（structural_risk_scan），
    确保用户始终能看到结构风险，而非空列表。
    """
    import logging
    logger = logging.getLogger(__name__)

    # 构建分析输入（LLM 和算法扫描共用）
    tasks_summary = []
    for node in project_state.get("nodes", []):
        tasks_summary.append({
            "id": node["id"],
            "name": node["name"],
            "progress": node.get("progress", 0),
            "status": node.get("status", "pending"),
            "estimated_days": node.get("estimated_days", 0),
            "confidence": node.get("confidence", 0.8),
            "is_critical": node.get("is_critical", False),
            "float_days": node.get("float_days"),
            "pre_dependencies": node.get("pre_dependencies", []),
            "resources": node.get("resources", []),
            "es": node.get("es"),
            "ef": node.get("ef"),
        })

    schedule = project_state.get("schedule") or {}
    buffer_info = project_state.get("buffer") or {}

    # ---- 尝试 LLM 风险分析 ----
    try:
        state_str = json.dumps({
            "tasks": tasks_summary,
            "schedule": schedule,
            "buffer": buffer_info,
            "total_nodes": len(tasks_summary),
            "completed_nodes": sum(1 for n in tasks_summary if n.get("status") == "completed"),
            "in_progress_nodes": sum(1 for n in tasks_summary if n.get("status") == "in_progress"),
        }, ensure_ascii=False, indent=2)

        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            try:
                content = _call_llm(RISK_ANALYSIS_PROMPT, state_str)
            except Exception as api_err:
                if attempt < max_retries - 1:
                    continue
                raise

            try:
                return _extract_json(content, schema_type="risk_analysis")
            except JsonExtractError as e:
                last_error = e
                if attempt < max_retries - 1:
                    error_feedback = (
                        "\n\n## ⚠️ 上一次输出校验失败\n"
                        "错误码: " + str(e.error_code) + "\n"
                        "错误信息: " + str(e.message) + "\n"
                        "原始输出: " + str(e.raw_preview) + "\n\n"
                        "请修正以上问题，重新输出正确的 JSON。只输出 JSON，不要解释。"
                    )
                    state_str = state_str + error_feedback

        logger.warning(
            "LLM 风险分析重试耗尽 (%s), 降级到算法扫描",
            last_error.error_code if last_error else "unknown",
        )

    except RuntimeError as e:
        err_msg = str(e)
        logger.warning("LLM 风险分析不可用 (%s), 降级到算法扫描", err_msg[:80])
    except Exception as e:
        logger.error("LLM 风险分析异常 (%s), 降级到算法扫描", str(e)[:80], exc_info=True)

    # ---- 算法风险扫描兜底（不依赖 LLM） ----
    from engine.scheduler import structural_risk_scan
    from models.project import TaskNode, ScheduleResult, BufferInfo

    nodes = []
    for nd in project_state.get("nodes", []):
        try:
            nodes.append(TaskNode(**nd))
        except Exception:
            pass

    sched = None
    if schedule and schedule.get("total_duration_days") is not None:
        try:
            sched = ScheduleResult(**schedule)
        except Exception:
            pass

    buf = None
    if buffer_info and buffer_info.get("total_days") is not None:
        try:
            buf = BufferInfo(**buffer_info)
        except Exception:
            pass

    algo_risks = structural_risk_scan(nodes, sched, buf)

    llm_unavailable = False
    try:
        cfg = get_anthropic_config()
        if not cfg.get("api_key"):
            llm_unavailable = True
    except RuntimeError:
        llm_unavailable = True
    except Exception:
        llm_unavailable = True

    if llm_unavailable:
        algo_risks.insert(0, {
            "risk_id": "struct_risk_env",
            "level": "info",
            "dimension": "LLM 风险分析不可用",
            "task_id": None,
            "message": "LLM 驱动的深度风险分析不可用（API Key 未配置）。当前仅显示基于项目结构的算法分析结果。请设置 ANTHROPIC_API_KEY 环境变量或在 ~/.claude/settings.json 中配置以获得完整风险分析。",
            "suggestion": "建议：配置 ANTHROPIC_API_KEY 后重启服务以启用 LLM 风险分析。",
        })

    return {"risks": algo_risks, "overall_assessment": "算法风险扫描结果（LLM 不可用）", "top_actions": []}

def _extract_json(text: str, schema_type: str = "intent") -> dict:
    """从 LLM 响应中提取 JSON，校验 schema，校验失败则抛错供上层重试

    Args:
        text: LLM 响应文本
        schema_type: 校验类型 — "intent" 执行意图 schema 校验,
                     "project"/"progress"/"risk_analysis" 跳过意图校验
    """
    text = text.strip()

    # 提取 ```json ... ``` 块
    if "```json" in text:
        start = text.index("```json") + 7
        try:
            end = text.index("```", start)
            text = text[start:end].strip()
        except ValueError:
            text = text[start:].strip()
    elif "```" in text:
        start = text.index("```") + 3
        try:
            end = text.index("```", start)
            text = text[start:end].strip()
        except ValueError:
            text = text[start:].strip()

    # 定位 JSON 边界：优先数组 [...], 其次对象 {...}
    bracket_start = text.find("[")
    bracket_end = text.rfind("]")
    brace_start = text.find("{")
    brace_end = text.rfind("}")

    if bracket_start != -1 and bracket_end > bracket_start:
        text = text[bracket_start:bracket_end + 1]
    elif brace_start != -1:
        text = text[brace_start:brace_end + 1]
    else:
        raise JsonExtractError(
            "NO_JSON_FOUND",
            "LLM 输出中未找到 JSON",
            text[:500],
        )

    # 解析 JSON
    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise JsonExtractError(
            "JSON_PARSE_ERROR",
            f"JSON 解析失败: {e.msg}（位置 line {e.lineno} col {e.colno}）",
            text[:500],
        )

    # Schema 校验 — 支持数组和单对象
    if schema_type == "intent":
        items = result if isinstance(result, list) else [result]
        errors = []
        for item in items:
            if isinstance(item, dict) and "intent" in item:
                errors.extend(_validate_intent_schema(item))
        if errors:
            raise JsonExtractError(
                "SCHEMA_ERROR",
                f"Schema 校验失败: {'; '.join(errors)}",
                json.dumps(result, ensure_ascii=False)[:500],
            )

    return result


class JsonExtractError(Exception):
    """LLM 输出校验失败，包含结构化错误信息供上层反馈给 LLM 重试"""
    def __init__(self, error_code: str, message: str, raw_preview: str):
        self.error_code = error_code
        self.message = message
        self.raw_preview = raw_preview
        super().__init__(message)


# ---- Schema 定义 ----

INTENT_SCHEMA = {
    "add_node": {
        "required": ["intent", "name", "estimated_days"],
        "types": {"estimated_days": (int, float), "confidence": (int, float),
                   "pre_dependencies": list, "resources": list, "notes": str},
    },
    "delete_node": {
        "required": ["intent", "node_id"],
    },
    "edit_node": {
        "required": ["intent", "node_id"],
        "types": {"progress": (int, float), "estimated_days": (int, float),
                   "confidence": (int, float), "pre_dependencies": list,
                   "resources": list},
    },
    "add_edge": {
        "required": ["intent", "source", "target"],
    },
    "remove_edge": {
        "required": ["intent", "source", "target"],
    },
    "ask_user": {
        "required": ["intent", "question"],
        "types": {"options": list},
    },
    "update_progress": {
        "required": ["intent", "updates"],
        "types": {"updates": list},
    },
}


def _validate_intent_schema(result: dict) -> list[str]:
    """校验意图 JSON 是否符合 schema，返回错误列表（空列表 = 通过）"""
    errors = []
    intent = result.get("intent", "")

    if not intent:
        errors.append("缺少 'intent' 字段")
        return errors

    schema = INTENT_SCHEMA.get(intent)
    if not schema:
        errors.append(f"未知的 intent 类型: '{intent}'，合法值: {list(INTENT_SCHEMA.keys())}")
        return errors

    # 检查必填字段
    for field in schema.get("required", []):
        if field not in result or result[field] is None:
            errors.append(f"缺少必填字段 '{field}'")

    # 检查类型
    for field, expected_types in schema.get("types", {}).items():
        if field in result and result[field] is not None:
            if not isinstance(result[field], expected_types):
                errors.append(f"字段 '{field}' 类型错误: 期望 {expected_types}, 实际 {type(result[field]).__name__}")

    return errors


# ═══════════════════════════════════════════════════════════════════════
# 双层 LLM 调用：意图理解层（NL → NL） + 翻译层（NL + Schema → JSON）
# ═══════════════════════════════════════════════════════════════════════

# ---- 意图理解层 ----

INTENT_UNDERSTAND_PROMPT = """你是一个项目管理助手。分析用户输入，理解用户想对项目 DAG 图做什么操作。

## 可用操作类型
- **更新任务**：修改已有任务的进度、状态、名称、备注等。支持批量操作（一次更新多个任务）。
- **新增节点**：添加新任务，必须说明它与哪些已有节点连接（前驱和后继）。
- **在链中插入**：在已有依赖链中插入一个新任务。
- **删除节点**：删除节点并重连上下游。
- **连接节点**：在已有节点间建立依赖关系。
- **反问用户**：信息不足无法判断意图时需要确认。

## ★ 禁止孤节点
**绝对不允许出现孤节点。** 每次新增节点必须同时声明它与已有节点的连接关系。

## ★ downstream_deps 规则
downstream_deps 只填**直接后继**（新节点的紧后任务），不填间接后继。
- 例：图是 A→B→C→D，新增 X 应该在 B 和 C 之间 → pre_deps=["B"], downstream_deps=["C"]
- 例：图是 A→B, A→C, B→D, C→D，新增 X 应作为新根节点 → pre_deps=[], downstream_deps=["A"]（只连 A，B/C/D 通过 A 间接可达）
- 不要填整个下游链！只填直接紧后任务

## 输出格式
**用自然语言描述用户的意图，不要输出 JSON。**
- 描述要具体：操作哪些节点（从上下文中确定 task_id）、改什么字段（name/progress/status/notes）、是单个还是批量。
- 如果用户要求改名/翻译节点名称，逐个列出 task_id → 新名称的映射。
- 如果用户输入的进展描述匹配到具体任务，列出 task_id 和对应的进度/状态。
- 如果是反问，说明不清楚的地方和候选选项。

示例输出：
"用户想批量更新7个任务的备注为中文：task_1→'数据库设计'，task_2→'API设计'，task_3→'后端认证API'，task_4→'前端登录页面'，task_5→'集成测试'，task_6→'安全审计'，task_7→'部署'"
"用户想新增一个'代码评审'任务，工期2天，放在task_3之后、task_5之前，需要断开task_3→task_5的旧依赖"
"用户想更新task_1进度为100%、状态completed，task_2进度为40%、状态in_progress"
"用户问了一个问题，不确定是否要操作DAG，需要反问用户确认"
"""


# ---- 翻译层 ----

TRANSLATION_PROMPT = """你是翻译层。把"意图描述"翻译成 DAG 原子操作序列。

## 规则
1. **输出 JSON 数组** `[{...}, {...}]`，即使只有一个操作也用数组包裹
2. 输出的第一个字符必须是 `[`，最后一个字符必须是 `]`
3. JSON 字符串用双引号 `"`，禁止单引号，末尾不要有多余逗号
4. 从意图描述中提取具体值，不要编造不存在的 task_id

## ★ 复杂场景 = 多个原子操作组合
你只有 5 种原子操作（+ ask_user）。复杂场景通过多个操作组合实现：
- 「在 A 和 B 之间插入 C」→ [add_node(C,pre=[A]), remove_edge(A,B), add_edge(A,C), add_edge(C,B)]
- 「批量改名」→ [edit_node(node_id,name), edit_node(node_id,name), ...]
- 「替换节点」→ [delete_node(X), add_node(Y), add_edge(前驱,Y), add_edge(Y,后继)]
- 「只改进度/状态/备注」→ [edit_node(node_id,progress,status)]

---
{SCHEMA_DEF}
---

## 输出示例

改名:
[{"intent": "edit_node", "node_id": "task_2", "name": "后端API开发"}]

批量改名:
[{"intent": "edit_node", "node_id": "task_1", "name": "数据库设计"}, {"intent": "edit_node", "node_id": "task_2", "name": "API设计"}]

改进度:
[{"intent": "edit_node", "node_id": "task_1", "progress": 100, "status": "completed"}]

新增节点（孤节点，后续加边）:
[{"intent": "add_node", "name": "代码评审", "estimated_days": 2, "pre_dependencies": ["task_3"], "resources": ["后端开发"]}]

在链中插入（新增 + 断旧边 + 建新边）:
[{"intent": "add_node", "name": "代码评审", "estimated_days": 2, "pre_dependencies": ["task_3"]}, {"intent": "remove_edge", "source": "task_3", "target": "task_5"}, {"intent": "add_edge", "source": "task_3", "target": "task_new"}, {"intent": "add_edge", "source": "task_new", "target": "task_5"}]

删除节点:
[{"intent": "delete_node", "node_id": "task_4"}]

添加依赖:
[{"intent": "add_edge", "source": "task_1", "target": "task_2"}]

移除依赖:
[{"intent": "remove_edge", "source": "task_3", "target": "task_5"}]

反问:
[{"intent": "ask_user", "question": "你想删除哪个节点？", "options": ["task_3", "task_4"]}]
"""


# ---- 从外部文件覆盖 prompt（文件存在则用文件内容，否则保留内置默认） ----

PROJECT_PARSE_PROMPT = _load_prompt("project_parse", PROJECT_PARSE_PROMPT)
PROGRESS_PARSE_PROMPT = _load_prompt("progress_parse", PROGRESS_PARSE_PROMPT)
RISK_ANALYSIS_PROMPT = _load_prompt("risk_analysis", RISK_ANALYSIS_PROMPT)
INTENT_UNDERSTAND_PROMPT = _load_prompt("intent_understand", INTENT_UNDERSTAND_PROMPT)
TRANSLATION_PROMPT = _load_prompt("translation", TRANSLATION_PROMPT)


def _build_schema_for_translation() -> str:
    """构建翻译层所需的 Schema 描述文本——纯原子操作"""
    lines = []

    lines.append("## 核心规则")
    lines.append("")
    lines.append("**这是 DAG 拓扑的原子操作层。复杂场景用多个原子操作组合完成。**")
    lines.append("例如「在 A 和 B 之间插入新节点 C」= add_node(C) + remove_edge(A,B) + add_edge(A,C) + add_edge(C,B)")
    lines.append("例如「批量改名」= 多个 edit_node")
    lines.append("例如「替换节点」= delete_node + add_node + add_edge × N")
    lines.append("")

    # ---- add_node ----
    lines.append("## add_node — 新增节点")
    lines.append("创建一个新任务节点。可选 pre_dependencies 指定前驱。")
    lines.append("如需断开旧边+建新边，配合 remove_edge + add_edge。")
    lines.append("")
    lines.append("字段: name(必填), estimated_days(必填), confidence(0-1), pre_dependencies(数组), resources(数组), notes")
    lines.append('示例: {"intent":"add_node","name":"代码评审","estimated_days":2,"pre_dependencies":["task_3"],"resources":["后端"],"notes":"在task_3和task_5之间"}')
    lines.append("")

    # ---- delete_node ----
    lines.append("## delete_node — 删除节点")
    lines.append("永久删除一个节点，系统自动清理关联边。")
    lines.append("")
    lines.append("字段: node_id(必填)")
    lines.append('示例: {"intent":"delete_node","node_id":"task_4"}')
    lines.append("")

    # ---- edit_node ----
    lines.append("## edit_node — 修改节点")
    lines.append("修改已有节点的任意字段。只填需要改的字段，其余保持不变。")
    lines.append("这是改名/改进度/改状态/改备注/改依赖的唯一方式。")
    lines.append("支持 pre_dependencies 字段直接替换依赖列表。")
    lines.append("")
    lines.append("字段: node_id(必填), name, progress(0-100), status(pending|in_progress|completed|delayed|blocked), estimated_days, confidence, resources, notes, pre_dependencies")
    lines.append('示例(改名): {"intent":"edit_node","node_id":"task_2","name":"后端API开发"}')
    lines.append('示例(改进度): {"intent":"edit_node","node_id":"task_1","progress":100,"status":"completed"}')
    lines.append('示例(批量改名): [{"intent":"edit_node","node_id":"task_1","name":"数据库设计"},{"intent":"edit_node","node_id":"task_2","name":"API设计"}]')
    lines.append("")

    # ---- add_edge ----
    lines.append("## add_edge — 添加依赖边")
    lines.append("添加 source → target 的依赖关系（target 依赖 source）。")
    lines.append("")
    lines.append("字段: source(必填), target(必填)")
    lines.append('示例: {"intent":"add_edge","source":"task_1","target":"task_2"}')
    lines.append("")

    # ---- remove_edge ----
    lines.append("## remove_edge — 移除依赖边")
    lines.append("移除 source → target 的依赖关系。节点本身不受影响。")
    lines.append("")
    lines.append("字段: source(必填), target(必填)")
    lines.append('示例: {"intent":"remove_edge","source":"task_3","target":"task_5"}')
    lines.append("")

    # ---- ask_user ----
    lines.append("## ask_user — 反问用户")
    lines.append("信息严重不足时使用。尽量少用，能推断就直接操作。")
    lines.append("")
    lines.append("字段: question(必填), options(可选数组)")
    lines.append('示例: {"intent":"ask_user","question":"你想删除哪个节点？","options":["task_3","task_4"]}')
    lines.append("")

    # ---- 输出格式 ----
    lines.append("## 输出格式")
    lines.append("**输出 JSON 数组。** 单个操作也放在数组里。复杂场景输出多个对象。")
    lines.append("")
    lines.append("正确: [{\"intent\":\"edit_node\",\"node_id\":\"task_1\",\"progress\":100}]")
    lines.append("正确: [{\"intent\":\"add_node\",...},{\"intent\":\"remove_edge\",...},{\"intent\":\"add_edge\",...}]")
    lines.append("错误: {\"intent\":\"edit_node\",...}  ← 单个也要用数组包裹")

    return "\n".join(lines)


def _build_topology_description(nodes: list[dict]) -> str:
    """构建图拓扑描述：依赖链 + 层级 + 角色 + 关键路径"""
    node_map = {n["id"]: n for n in nodes}
    # 计算下游
    children: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    indegree: dict[str, int] = {}
    for n in nodes:
        indegree[n["id"]] = len(n.get("pre_dependencies", []))
        for pre in n.get("pre_dependencies", []):
            if pre in children:
                children[pre].append(n["id"])

    roots = [n for n in nodes if indegree[n["id"]] == 0]
    leaves = [n for n in nodes if not children[n["id"]]]

    # 构建所有依赖链（DFS）
    def _walk(nid, visited=None):
        if visited is None:
            visited = set()
        if nid in visited:
            return [[nid + "(cycle)"]]
        visited = visited | {nid}
        if not children.get(nid):
            return [[nid]]
        paths = []
        for child in children[nid]:
            for sub in _walk(child, visited):
                paths.append([nid] + sub)
        return paths

    chains = []
    for root in roots:
        for path in _walk(root["id"]):
            chains.append(path)

    # 格式化依赖链
    lines = ["### 依赖链（→ 表示先后顺序）"]
    for i, chain in enumerate(chains[:10], 1):  # 最多10条
        chain_str = " → ".join(
            f"{nid}({node_map[nid]['name']},{node_map[nid].get('estimated_days',0)}d)"
            for nid in chain
        )
        lines.append(f"链{i}: {chain_str}")

    # 图分析
    lines.append("\n### 图分析")
    lines.append(f"- 最上游（无前置）: {', '.join(r['id']+'('+r['name']+')' for r in roots)}")
    lines.append(f"- 最下游（无后继）: {', '.join(l['id']+'('+l['name']+')' for l in leaves)}")

    # 汇聚点（入度 ≥ 2）
    merges = [f"{n['id']}({n['name']},入度{indegree[n['id']]})" for n in nodes if indegree[n["id"]] >= 2]
    if merges:
        lines.append(f"- 汇聚点: {', '.join(merges)}")

    # 并行分支
    for n in nodes:
        if len(children.get(n["id"], [])) >= 2:
            kids = [f"{c}({node_map[c]['name']})" for c in children[n["id"]]]
            lines.append(f"- 分支点: {n['id']}({n['name']}) → {', '.join(kids)}（并行）")

    return "\n".join(lines)


def _build_simpler_description(nodes: list[dict]) -> list[dict]:
    """构建简化的节点列表（含下游信息）"""
    children: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for n in nodes:
        for pre in n.get("pre_dependencies", []):
            if pre in children:
                children[pre].append(n["id"])
    return [
        {
            "id": n.get("id"), "name": n.get("name"),
            "estimated_days": n.get("estimated_days", 0),
            "pre_dependencies": n.get("pre_dependencies", []),
            "downstream": children.get(n["id"], []),
            "status": n.get("status", "pending"),
        }
        for n in nodes
    ]


def parse_single_task(
    description: str,
    existing_nodes: list[dict],
    messages: list[dict] | None = None,
) -> dict:
    """双层 LLM 解析用户输入

    第1层（意图理解）：NL → NL，理解用户想做什么，不受 Schema 约束
    第2层（翻译）：NL + Schema → JSON，将意图描述翻译为标准化的意图 JSON

    Args:
        description: 用户当前输入
        existing_nodes: 已有节点列表 [{id, name}]
        messages: 该项目的对话历史 [{role, content}]

    Returns:
        {"intent": "...", ...}  标准化意图 JSON
    """

    # 构建拓扑结构描述
    topo_desc = _build_topology_description(existing_nodes)
    simpler_desc = _build_simpler_description(existing_nodes)

    # ---- 构建上下文（对话历史 + 拓扑 + 用户输入） ----
    existing_ids = {n.get("id") for n in existing_nodes}
    context_parts = []
    if messages:
        recent = messages[-20:]
        context_parts.append("## 对话历史")
        for m in recent:
            context_parts.append(f"[{m.get('role', '?')}]: {m.get('content', '')}")
        # 检查历史中提到的已删除节点
        stale_ids = set()
        import re as _re
        for m in recent:
            content = m.get("content", "")
            if "[待确认]" in content or "[反问]" in content:
                continue
            found = _re.findall(r'task_\d+', content)
            for tid in found:
                if tid not in existing_ids:
                    stale_ids.add(tid)
        if stale_ids:
            context_parts.append(f"\n**注意：历史中提到的以下节点已被删除，不再存在：{', '.join(sorted(stale_ids))}**")

    context_parts.append("## 项目拓扑结构（当前真实状态）")
    context_parts.append(topo_desc)
    context_parts.append("## 已有任务列表")
    context_parts.append(json.dumps(simpler_desc, ensure_ascii=False, indent=2))
    context_parts.append(f"## 用户当前输入\n{description}")

    full_input = "\n\n".join(context_parts)

    # ═════════════════════════════════════════════════════════════════
    # 第1层：意图理解（NL → NL），带重试
    # ═════════════════════════════════════════════════════════════════
    intent_nl = None
    max_retries = 3
    for attempt in range(max_retries):
        try:
            intent_nl = _call_llm(INTENT_UNDERSTAND_PROMPT, full_input, max_tokens=1024).strip()
            if intent_nl:
                break
        except Exception as api_err:
            logger.warning(
                "parse_single_task 意图理解层 API 失败 (attempt %d/%d): %s",
                attempt + 1, max_retries, str(api_err)[:120],
            )
            if attempt >= max_retries - 1:
                return {
                    "intent": "ask_user",
                    "question": f"AI 服务暂时不可用（{str(api_err)[:60]}），请稍后重试。",
                    "options": ["重试", "手动输入"],
                }

    if not intent_nl:
        return {
            "intent": "ask_user",
            "question": "无法理解你的意图，请重新描述。",
            "options": ["重试", "手动输入"],
        }

    # ═════════════════════════════════════════════════════════════════
    # 第2层：翻译（NL + Schema → JSON），带 schema 校验 + 重试
    # ═════════════════════════════════════════════════════════════════
    schema_def = _build_schema_for_translation()
    translation_system = TRANSLATION_PROMPT.replace("{SCHEMA_DEF}", schema_def)
    translation_input = f"## 意图描述\n{intent_nl}"

    last_error = None
    for attempt in range(max_retries):
        try:
            raw_output = _call_llm(translation_system, translation_input, max_tokens=2048)
        except Exception as api_err:
            logger.warning(
                "parse_single_task 翻译层 API 失败 (attempt %d/%d): %s",
                attempt + 1, max_retries, str(api_err)[:120],
            )
            if attempt >= max_retries - 1:
                return {
                    "intent": "ask_user",
                    "question": f"AI 服务暂时不可用（{str(api_err)[:60]}），请稍后重试。",
                    "options": ["重试", "手动输入"],
                }

        try:
            result = _extract_json(raw_output, schema_type="intent")
            intents_list = result if isinstance(result, list) else [result]
            logger.info("parse_single_task 成功: %d intent(s) intent_nl=%.100s", len(intents_list), intent_nl)
            return intents_list
        except JsonExtractError as e:
            last_error = e
            if attempt < max_retries - 1:
                # 错误反馈：告诉翻译层哪里错了
                error_feedback = (
                    f"\n\n## ⚠️ 上一次输出校验失败\n"
                    f"错误码: {e.error_code}\n"
                    f"错误信息: {e.message}\n"
                    f"原始输出: {e.raw_preview}\n\n"
                    f"请修正以上问题，重新输出正确的 JSON。只输出 JSON，不要解释。"
                )
                translation_input = translation_input + error_feedback

    # 重试耗尽 → 返回 ask
    logging.getLogger(__name__).error(
        "parse_single_task 翻译层重试耗尽: err=%s msg=%s raw=%.200s",
        last_error.error_code if last_error else 'unknown',
        last_error.message if last_error else '',
        last_error.raw_preview if last_error else '',
    )
    return {
        "intent": "ask_user",
        "question": f"AI 多次输出格式错误（{last_error.error_code if last_error else 'unknown'}），请重新描述你的需求。",
        "options": ["重试", "手动输入"],
    }

