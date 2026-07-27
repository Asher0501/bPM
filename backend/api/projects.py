# -*- coding: utf-8 -*-
"""REST API — 项目 CRUD + 排期 + 进展更新"""

import json
import logging
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

# 跨平台文件锁
if os.name == "nt":
    import msvcrt as _fcntl_module

    @contextmanager
    def _file_lock(f, exclusive=True, timeout=5.0):
        """Windows 文件锁"""
        deadline = time.time() + timeout
        while True:
            try:
                _fcntl_module.locking(f.fileno(), _fcntl_module.LK_NBLCK if exclusive else _fcntl_module.LK_NBRLCK, 1)
                break
            except OSError:
                if time.time() > deadline:
                    raise TimeoutError(f"无法在 {timeout}s 内获取文件锁")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                f.seek(0)
                _fcntl_module.locking(f.fileno(), _fcntl_module.LK_UNLCK, 1)
            except OSError:
                pass
else:
    import fcntl as _fcntl_module

    @contextmanager
    def _file_lock(f, exclusive=True, timeout=5.0):
        """Unix 文件锁"""
        op = _fcntl_module.LOCK_EX if exclusive else _fcntl_module.LOCK_SH
        deadline = time.time() + timeout
        while True:
            try:
                _fcntl_module.flock(f.fileno(), op | _fcntl_module.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    raise TimeoutError(f"无法在 {timeout}s 内获取文件锁")
                time.sleep(0.05)
        try:
            yield
        finally:
            _fcntl_module.flock(f.fileno(), _fcntl_module.LOCK_UN)

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from config import get_config
from models.project import (
    Project, TaskNode, EdgeDef, RiskItem, RiskLevel, TaskStatus,
    CreateProjectRequest, ProgressUpdateRequest, EditTaskRequest,
    AddNodeRequest,
    ProjectListResponse, ProjectDetailResponse, ScheduleResponse,
    ProgressResponse, GraphResponse,
    now_iso,
)
from engine.intents import AtomicOp, map_intent_to_ops
from engine.parser import (
    parse_project, parse_progress, analyze_risks, parse_single_task, sanitize_user_input,
)
from engine.scheduler import (
    create_schedule, build_edges_from_dependencies,
    update_buffer_consumption, compute_buffer_info,
    structural_risk_scan,
)
from api.ws import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# 项目根目录：bePm/.projects/
PROJECTS_ROOT = Path(__file__).parent.parent.parent / ".projects"
INDEX_PATH = PROJECTS_ROOT / "index.json"


def _load_index() -> list[dict]:
    """加载项目索引"""
    if not INDEX_PATH.exists():
        return []
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_index(index: list[dict]):
    """保存项目索引（文件锁防并发写）"""
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    with open(INDEX_PATH, "r+", encoding="utf-8") if INDEX_PATH.exists() else \
         open(INDEX_PATH, "w+", encoding="utf-8") as f:
        with _file_lock(f):
            f.seek(0)
            f.truncate()
            json.dump(index, f, ensure_ascii=False, indent=2)


def _upsert_index(project: Project):
    """更新或插入项目索引条目"""
    index = _load_index()
    entry = {
        "id": project.id,
        "name": project.name,
        "deadline": project.deadline,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "node_count": len(project.nodes),
        "path": str(_project_dir(project.id).relative_to(PROJECTS_ROOT.parent)),
    }
    for i, item in enumerate(index):
        if item.get("id") == project.id:
            index[i] = entry
            break
    else:
        index.append(entry)
    _save_index(index)


def _remove_from_index(project_id: str):
    """从索引中移除项目"""
    index = _load_index()
    index = [item for item in index if item.get("id") != project_id]
    _save_index(index)


def _project_dir(project_id: str) -> Path:
    """每个项目一个文件夹，含路径穿越防护"""
    if not re.match(r'^[a-zA-Z0-9_\-]+$', project_id):
        raise HTTPException(status_code=400, detail=f'Invalid project_id: {project_id}')
    resolved = os.path.realpath(PROJECTS_ROOT / project_id)
    if not resolved.startswith(os.path.realpath(PROJECTS_ROOT)):
        raise HTTPException(status_code=400, detail=f'Path traversal denied: {project_id}')
    return Path(resolved)


def _project_json(project_id: str) -> Path:
    return _project_dir(project_id) / "project.json"


def _messages_json(project_id: str) -> Path:
    return _project_dir(project_id) / "messages.json"


def _load_project(project_id: str) -> Project:
    """加载项目：project.json + messages.json"""
    pj = _project_json(project_id)
    if not pj.exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    with open(pj, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 加载独立的消息文件
    mj = _messages_json(project_id)
    if mj.exists():
        with open(mj, "r", encoding="utf-8") as f:
            data["messages"] = json.load(f)
    else:
        data["messages"] = []

    return Project(**data)


def _save_project(project: Project):
    """保存项目：project.json + messages.json 分开存储（含 messages 裁剪）"""
    # 限制 messages 增长（所有写入路径统一裁剪）
    msg_cfg = get_config().messages
    if len(project.messages) > msg_cfg.max_count:
        project.messages = project.messages[-msg_cfg.trim_to:]
        logger.info("项目 %s 消息裁剪: %d → %d", project.id, msg_cfg.max_count, msg_cfg.trim_to)

    proj_dir = _project_dir(project.id)
    proj_dir.mkdir(parents=True, exist_ok=True)
    project.updated_at = now_iso()

    # project.json：不含 messages
    data = project.model_dump()
    messages = data.pop("messages", [])
    with open(_project_json(project.id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # messages.json：对话历史单独存
    with open(_messages_json(project.id), "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)

    # 写回 messages 以便当前请求继续使用
    data["messages"] = messages

    # 更新索引
    _upsert_index(project)


def _detect_cycle(nodes: list, new_source: str, new_target: str) -> bool:
    """检测添加 new_source → new_target 边后是否会产生环。

    使用 DFS 从 new_source 出发，沿着已有依赖关系向前搜索，
    如果能从 new_source 到达 new_target，则添加 new_target → new_source
    会产生环。

    Args:
        nodes: 节点列表，每个节点要有 id 和 pre_dependencies 属性
        new_source: 源节点 ID（前置任务）
        new_target: 目标节点 ID（后置任务，将依赖 source）

    Returns:
        True 如果添加边会产生环
    """
    # 构建邻接表：target → list of sources (target depends on sources)
    # 我们要找的是从 target 能否到达 source（反向）
    # 即在已有图中，target 是否间接依赖 source
    # 如果添加 source → target，那么 target → ... → source 就是一个环
    node_ids = {n.id for n in nodes}
    if new_source not in node_ids or new_target not in node_ids:
        return False

    # 构建后继映射：source → [targets that depend on source]
    successors = {n.id: [] for n in nodes}
    for n in nodes:
        for pre in n.pre_dependencies:
            if pre in successors:
                successors[pre].append(n.id)

    # DFS 从 new_target 出发，看是否能到达 new_source
    visited = set()
    stack = [new_target]
    while stack:
        current = stack.pop()
        if current == new_source:
            return True  # 存在环
        if current in visited:
            continue
        visited.add(current)
        # 沿着依赖关系向下搜索
        for succ in successors.get(current, []):
            if succ not in visited:
                stack.append(succ)

    return False


# ---- Routes ----


@router.get("/projects")
async def list_projects():
    """列出所有项目（从 index.json 读取，秒级响应）"""
    index = _load_index()
    # 按更新时间倒序
    index.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    result = []
    for item in index:
        result.append({
            "id": item["id"],
            "name": item.get("name", ""),
            "deadline": item.get("deadline", ""),
            "created_at": item.get("created_at", ""),
            "updated_at": item.get("updated_at", ""),
            "node_count": item.get("node_count", 0),
            "risk_count": 0,
            "critical_risk_count": 0,
            "path": item.get("path", ""),
        })
    return {"projects": result}


@router.post("/projects")
async def create_project(req: CreateProjectRequest):
    """创建项目并自动排期"""
    # 1. 生成项目 ID
    project_id = uuid.uuid4().hex[:8]

    # 2. 校验输入：描述不能为空
    full_text = sanitize_user_input(req.description.strip())
    if req.file_text:
        full_text += "\n\n--- 文件内容 ---\n" + sanitize_user_input(req.file_text.strip())
    if req.additional_info:
        full_text += "\n\n--- 补充信息 ---\n" + sanitize_user_input(req.additional_info.strip())

    if len(full_text) < 10:
        raise HTTPException(
            status_code=400,
            detail="项目描述太短（至少需要10个字符），请提供更详细的项目信息",
        )

    parsed = parse_project(full_text, req.deadline, req.additional_info)

    # 3. 构建任务节点
    tasks_data = parsed.get("tasks", [])
    nodes = []
    for t in tasks_data:
        # 处理 LLM 可能遗漏的字段
        nodes.append(TaskNode(
            id=t.get("id", f"task_{len(nodes)+1}"),
            name=t.get("name", "未命名任务"),
            description=t.get("description", ""),
            estimated_days=float(t.get("estimated_days", 1.0)),
            confidence=float(t.get("confidence", 0.8)),
            pre_dependencies=t.get("pre_dependencies", []),
            resources=t.get("resources", []),
        ))

    if not nodes:
        raise HTTPException(status_code=400, detail="未能从描述中解析出任务，请提供更详细的项目信息")

    edges = build_edges_from_dependencies(nodes)

    # 4. 排期
    schedule = create_schedule(nodes, req.deadline)
    buffer_info = compute_buffer_info(schedule)

    # 5. 创建项目
    project = Project(
        id=project_id,
        name=parsed.get("project_name", "未命名项目"),
        description=parsed.get("analysis", req.description[:200]),
        created_at=now_iso(),
        updated_at=now_iso(),
        deadline=req.deadline,
        nodes=nodes,
        edges=edges,
        schedule=schedule,
        risks=[],
        buffer=buffer_info,
        raw_input=full_text,
    )

    # 6. LLM 风险分析（不影响项目创建，失败则继续使用空风险列表）
    try:
        llm_analysis = analyze_risks(project.model_dump())
        raw_risks = llm_analysis.get("risks", [])
        for r in raw_risks:
            try:
                project.risks.append(RiskItem(**r))
            except Exception as item_err:
                logger.warning(
                    "跳过无效风险项 (project=%s, risk_id=%s): %s",
                    project_id, r.get("risk_id", "?"), item_err,
                )
    except Exception as e:
        logger.error(
            "创建项目时风险分析失败 (project=%s): %s", project_id, e, exc_info=True
        )

    _save_project(project)
    return {"project": project.model_dump()}


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    """获取项目详情"""
    project = _load_project(project_id)
    return {"project": project.model_dump()}


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """删除项目（文件夹 + 索引）"""
    proj_dir = _project_dir(project_id)
    if not proj_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    shutil.rmtree(proj_dir)
    _remove_from_index(project_id)
    return {"status": "deleted", "project_id": project_id}


@router.post("/projects/{project_id}/schedule")
async def re_schedule(project_id: str, fast: bool = False):
    """重新触发自动排期（含风险分析）。?fast=true 跳过 LLM 仅算法扫描"""
    project = _load_project(project_id)
    _reschedule_project(project, skip_llm=fast)
    _save_project(project)
    return {"project": project.model_dump()}


@router.post("/projects/batch/reschedule")
async def batch_reschedule():
    """批量重建所有项目的排期（快速模式，仅算法扫描）"""
    index = _load_index()
    results = []
    for item in index:
        pid = item["id"]
        try:
            project = _load_project(pid)
            _reschedule_project(project, skip_llm=True)
            _save_project(project)
            cp = len(project.schedule.critical_path) if project.schedule else 0
            results.append({"id": pid, "status": "ok", "cp_nodes": cp})
        except Exception as e:
            results.append({"id": pid, "status": "error", "error": str(e)[:100]})
    return {"results": results}


@router.post("/projects/{project_id}/progress")
async def update_progress(project_id: str, req: ProgressUpdateRequest):
    """提交进展更新"""
    project = _load_project(project_id)

    # 1. 用 LLM 解析进展文本
    parsed = parse_progress(project.model_dump(), sanitize_user_input(req.progress_text))
    updates = parsed.get("updates", [])
    risk_signals = parsed.get("risk_signals", [])
    updated_node_ids = []

    node_map = {n.id: n for n in project.nodes}

    # 2. 更新节点
    for upd in updates:
        tid = upd.get("task_id", "")
        if tid in node_map:
            node = node_map[tid]
            old_progress = node.progress
            if "progress" in upd and upd["progress"] is not None:
                node.progress = float(upd["progress"])
            if "status" in upd and upd["status"]:
                try:
                    node.status = TaskStatus(upd["status"])
                except ValueError:
                    pass
            if "notes" in upd and upd["notes"]:
                node.notes = upd.get("notes", "")

            # 检测延迟：基于实际时间间隔归一化日进度增益
            if node.is_critical and node.estimated_days > 0:
                expected_daily_gain = 100.0 / node.estimated_days
                actual_gain = max(0.0, node.progress - old_progress)

                # 计算经过天数
                now = now_iso()
                elapsed_days = 1.0
                if node.last_progress_update:
                    try:
                        last_ts = datetime.fromisoformat(node.last_progress_update)
                        now_ts = datetime.fromisoformat(now)
                        elapsed_seconds = (now_ts - last_ts).total_seconds()
                        elapsed_days = max(0.5, elapsed_seconds / 86400.0)
                    except (ValueError, TypeError):
                        elapsed_days = 1.0

                actual_daily_gain = actual_gain / elapsed_days

                if actual_daily_gain < expected_daily_gain * 0.5:
                    shortfall_ratio = (expected_daily_gain - actual_daily_gain) / expected_daily_gain
                    delay_days = elapsed_days * shortfall_ratio
                    if project.schedule:
                        update_buffer_consumption(project.schedule, delay_days)

                node.last_progress_update = now

            updated_node_ids.append(tid)

    # 3. 重新评估进度消耗
    if project.schedule:
        buffer_info = compute_buffer_info(project.schedule)
        project.buffer = buffer_info

        # LLM 风险分析 — 带日志和逐条容错
        try:
            llm_analysis = analyze_risks(project.model_dump())
            raw_risks = llm_analysis.get("risks", [])
            project.risks = []
            for r in raw_risks:
                try:
                    project.risks.append(RiskItem(**r))
                except Exception as item_err:
                    logger.warning(
                        "跳过无效风险项 (project=%s, risk_id=%s): %s",
                        project_id, r.get("risk_id", "?"), item_err,
                    )
        except Exception as e:
            logger.error(
                "风险分析失败 (project=%s): %s", project_id, e, exc_info=True
            )
            project.risks = []

    _save_project(project)

    # ---- WebSocket 广播：推送节点状态变更和风险告警 ----
    for tid in updated_node_ids:
        node = node_map.get(tid)
        if node:
            await manager.broadcast_node_status(
                project_id, tid, node.progress, node.status.value
            )
    for r in project.risks:
        await manager.broadcast_risk_alert(project_id, r.model_dump())

    return {
        "project": project.model_dump(),
        "updated_nodes": updated_node_ids,
        "risk_signals": risk_signals,
        "new_risks": [r.model_dump() for r in project.risks],
    }


@router.get("/projects/{project_id}/graph")
async def get_graph(project_id: str):
    """获取 DAG 拓扑数据（前端渲染用）"""
    project = _load_project(project_id)

    # 基准日期：项目创建日 → 日偏移量 + 基准 = 实际日期
    try:
        base_date = datetime.fromisoformat(project.created_at)
    except (ValueError, TypeError):
        base_date = datetime.now()
    def _fmt_date(day_offset):
        """日偏移量 → YYYY-MM-DD 格式日期"""
        if day_offset is None:
            return None
        try:
            return (base_date + timedelta(days=float(day_offset))).strftime("%m/%d")
        except (ValueError, TypeError):
            return str(day_offset)

    # 编译节点：提取前端需要的最少字段
    nodes_data = []
    for n in project.nodes:
        nodes_data.append({
            "id": n.id,
            "name": n.name,
            "progress": n.progress,
            "status": n.status.value,
            "is_critical": n.is_critical,
            "estimated_days": n.estimated_days,
            "confidence": n.confidence,
            "resources": n.resources,
            "es": n.es, "ef": n.ef, "ls": n.ls, "lf": n.lf,
            "es_date": _fmt_date(n.es),
            "ef_date": _fmt_date(n.ef),
            "ls_date": _fmt_date(n.ls),
            "lf_date": _fmt_date(n.lf),
            "float_days": n.float_days,
            "notes": n.notes,
            "tags": n.tags or [],
        })

    edges_data = [{"source": e.source, "target": e.target} for e in project.edges]

    schedule_data = project.schedule.model_dump() if project.schedule else None

    return {
        "nodes": nodes_data,
        "edges": edges_data,
        "critical_path": project.schedule.critical_path if project.schedule else [],
        "schedule": schedule_data,
        "risks": [r.model_dump() for r in project.risks],
        "buffer": project.buffer.model_dump() if project.buffer else None,
    }


# ---- 标签 & 分组 ----


@router.get("/projects/{project_id}/tags")
async def get_tags(project_id: str):
    """收集项目中所有节点的标签（去重排序）"""
    project = _load_project(project_id)
    all_tags: set[str] = set()
    for n in project.nodes:
        for t in (n.tags or []):
            all_tags.add(t)
    return {"tags": sorted(all_tags)}


@router.get("/projects/{project_id}/grouped")
async def get_grouped(project_id: str, tags: str = ""):
    """按标签分组聚合——支持多 tag（逗号分隔）

    聚合规则:
      - 同一 tag 值的节点合并为一个聚合节点
      - 多 tag 时各自独立成组
      - 聚合节点属性由子节点子图调度计算
      - 边按分组间关系聚合，去重
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    if not tag_list:
        return await get_graph(project_id)

    project = _load_project(project_id)
    from datetime import datetime, timedelta

    base_date = datetime.now()
    try:
        base_date = datetime.fromisoformat(project.created_at)
    except (ValueError, TypeError):
        pass

    def _fmt(offset):
        try:
            return (base_date + timedelta(days=float(offset))).strftime("%m/%d")
        except Exception:
            return str(offset)

    # --- 检查冲突：有节点命中多个 tag → 拒绝执行 ---
    conflicts: list[dict] = []
    for n in project.nodes:
        matching = [t for t in tag_list if t in (n.tags or [])]
        if len(matching) > 1:
            conflicts.append({
                "node_id": n.id,
                "node_name": n.name,
                "tags": matching,
            })

    if conflicts:
        return {
            "nodes": [], "edges": [], "critical_path": [],
            "schedule": None, "risks": [], "buffer": None,
            "conflicts": conflicts,
        }

    # --- 分组 ---
    group_ids: set[str] = set()
    groups: dict[str, list] = {}
    standalone: list = []

    for n in project.nodes:
        matched = False
        for t in tag_list:
            if t in (n.tags or []):
                group_ids.add(n.id)
                groups.setdefault(t, []).append(n)
                matched = True
                break
        if not matched:
            standalone.append(n)

    result_nodes = []

    # --- 聚合节点 ---
    for gv, members in groups.items():
        # 进度加权平均
        total_weight = sum(m.estimated_days for m in members)
        agg_progress = (
            sum(m.progress * m.estimated_days for m in members) / total_weight
            if total_weight > 0 else 0
        )

        # 最差状态
        status_order = {"blocked": 5, "delayed": 4, "in_progress": 3, "pending": 2, "completed": 1}
        agg_status = max(members, key=lambda m: status_order.get(m.status.value, 0)).status.value

        # 时间边界：基于成员在完整项目排期中的位置（非子图重新排期）
        agg_es = min((m.es for m in members if m.es is not None), default=None)
        agg_ef = max((m.ef for m in members if m.ef is not None), default=None)
        agg_ls = min((m.ls for m in members if m.ls is not None), default=None)
        agg_lf = max((m.lf for m in members if m.lf is not None), default=None)

        # 工期：时间跨度的 span
        agg_duration = (agg_ef - agg_es) if (agg_es is not None and agg_ef is not None) else sum(m.estimated_days for m in members)

        # 关键路径：任一成员在关键路径上即为关键
        agg_critical = [m.id for m in members if m.is_critical]
        agg_float = round(agg_ls - agg_es, 2) if (agg_es is not None and agg_ls is not None) else None
        agg_is_critical = any(m.is_critical for m in members) or (agg_float is not None and abs(agg_float) < 0.01)

        # FO 去重
        fo_set: set[str] = set()
        for m in members:
            for r in (m.resources or []):
                fo_set.add(r)

        result_nodes.append({
            "id": f"grp_{gv}",
            "name": gv,
            "progress": round(agg_progress, 1),
            "status": agg_status,
            "is_critical": agg_is_critical,
            "estimated_days": round(agg_duration, 1),
            "confidence": round(sum(m.confidence for m in members) / len(members), 2),
            "resources": sorted(fo_set),
            "es": agg_es, "ef": agg_ef, "ls": agg_ls, "lf": agg_lf,
            "es_date": _fmt(agg_es) if agg_es is not None else None,
            "ef_date": _fmt(agg_ef) if agg_ef is not None else None,
            "ls_date": _fmt(agg_ls) if agg_ls is not None else None,
            "lf_date": _fmt(agg_lf) if agg_lf is not None else None,
            "float_days": agg_float,
            "is_group": True,
            "notes": f"{len(members)} 个子节点",
            "tags": list(tag_list),
            "children": [m.id for m in members],
        })

    # --- 独立节点 ---
    for n in standalone:
        result_nodes.append({
            "id": n.id, "name": n.name,
            "progress": n.progress, "status": n.status.value,
            "is_critical": n.is_critical,
            "estimated_days": n.estimated_days, "confidence": n.confidence,
            "resources": n.resources,
            "es": n.es, "ef": n.ef, "ls": n.ls, "lf": n.lf,
            "es_date": _fmt(n.es) if n.es is not None else None,
            "ef_date": _fmt(n.ef) if n.ef is not None else None,
            "ls_date": _fmt(n.ls) if n.ls is not None else None,
            "lf_date": _fmt(n.lf) if n.lf is not None else None,
            "float_days": n.float_days,
            "notes": n.notes, "tags": n.tags or [],
        })

    # --- 聚合边 ---
    result_edges = []
    seen_edges: set[tuple[str, str]] = set()

    all_node_ids = {n["id"] for n in result_nodes}
    # 建立 id → group 的映射
    id_to_group: dict[str, str] = {}
    for n in result_nodes:
        for cid in n.get("children", []):
            id_to_group[cid] = n["id"]
        id_to_group[n["id"]] = n["id"]

    for e in project.edges:
        src_grp = id_to_group.get(e.source, e.source)
        tgt_grp = id_to_group.get(e.target, e.target)
        if src_grp == tgt_grp:
            continue  # 组内边，去掉
        key = (src_grp, tgt_grp)
        if key not in seen_edges and src_grp in all_node_ids and tgt_grp in all_node_ids:
            seen_edges.add(key)
            result_edges.append({"source": src_grp, "target": tgt_grp})

    # --- 关键路径（聚合后重新算） ---
    # 对聚合节点简单识别：float ≈ 0 的就是关键
    cp = [n["id"] for n in result_nodes if n.get("is_critical")]

    return {
        "nodes": result_nodes,
        "edges": result_edges,
        "critical_path": cp,
        "schedule": None,
        "risks": [r.model_dump() for r in project.risks],
        "buffer": project.buffer.model_dump() if project.buffer else None,
        "conflicts": conflicts,
    }


# ---- 辅助函数 ----


def _reschedule_project(project: Project, skip_llm: bool = False):
    """重新排期 + 自动风险分析（含 LLM，失败时保留旧风险数据）

    Args:
        project: 项目对象
        skip_llm: True 时跳过 LLM 风险分析，仅做算法扫描
    """
    edges = build_edges_from_dependencies(project.nodes)
    schedule = create_schedule(project.nodes, project.deadline)
    buffer_info = compute_buffer_info(schedule)
    project.edges = edges
    project.schedule = schedule
    project.buffer = buffer_info

    if skip_llm:
        # 快速模式：仅算法扫描
        from models.project import ScheduleResult, BufferInfo
        algo_risks = structural_risk_scan(project.nodes, schedule, buffer_info)
        project.risks = [RiskItem(**r) if isinstance(r, dict) else r for r in algo_risks]
        return

    # LLM 风险分析 — 带日志和逐条容错
    try:
        llm_analysis = analyze_risks(project.model_dump())
        raw_risks = llm_analysis.get("risks", [])
        project.risks = []
        for r in raw_risks:
            try:
                project.risks.append(RiskItem(**r))
            except Exception as item_err:
                logger.warning(
                    "跳过无效风险项 (project=%s, risk_id=%s): %s",
                    project.id, r.get("risk_id", "?"), item_err,
                )
    except Exception as e:
        logger.error(
            "风险分析失败 (project=%s): %s", project.id, e, exc_info=True
        )
        # 失败时保留旧风险数据，不清空

def _find_downstream(node_id: str, nodes) -> list[str]:
    """找到直接依赖 node_id 的下游节点 ID 列表"""
    return [n.id for n in nodes if node_id in n.pre_dependencies]


def _build_plan_text(ops: list, node_map: dict) -> str:
    """将所有操作列表转为人类可读的变更计划（供用户一次性确认）"""
    lines = ["计划执行以下变更:"]
    for op in ops:
        p = op.params
        if op.op == "add_node":
            lines.append(f"  + 新增节点「{p.get('name','?')}」({p.get('estimated_days',0)}天)")
            deps = p.get("pre_dependencies", [])
            if deps:
                dep_names = [_get_node_name(node_map, d) for d in deps if d in node_map]
                if dep_names:
                    lines.append(f"    依赖: {', '.join(dep_names)}")
        elif op.op == "add_edge":
            sn = _get_node_name(node_map, p.get("source", ""))
            tn = _get_node_name(node_map, p.get("target", ""))
            lines.append(f"  + 添加依赖: {sn} → {tn}")
        elif op.op == "remove_edge":
            sn = _get_node_name(node_map, p.get("source", ""))
            tn = _get_node_name(node_map, p.get("target", ""))
            lines.append(f"  - 移除依赖: {sn} → {tn}")
        elif op.op == "delete_node":
            lines.append(f"  - 删除节点「{_get_node_name(node_map, p.get('node_id','?'))}」")
        elif op.op == "edit_node":
            node_name = _get_node_name(node_map, p.get('node_id', '?'))
            changes = []
            if "name" in p and p["name"] is not None:
                changes.append(f"名称 →「{p['name']}」")
            if "progress" in p and p["progress"] is not None:
                changes.append(f"进度 → {p['progress']}%")
            if "status" in p and p["status"] is not None:
                changes.append(f"状态 → {p['status']}")
            if "estimated_days" in p and p["estimated_days"] is not None:
                changes.append(f"工期 → {p['estimated_days']}天")
            if "confidence" in p and p["confidence"] is not None:
                changes.append(f"置信度 → {p['confidence']}")
            if "resources" in p and p["resources"] is not None:
                resources_str = ", ".join(p["resources"]) if isinstance(p["resources"], list) else str(p["resources"])
                changes.append(f"FO →「{resources_str}」")
            if "notes" in p and p["notes"] is not None:
                notes_preview = p["notes"][:40] + ("..." if len(p["notes"]) > 40 else "")
                changes.append(f"备注 →「{notes_preview}」")
            if "pre_dependencies" in p:
                dep_names = [_get_node_name(node_map, d) for d in p["pre_dependencies"] if d in node_map]
                changes.append(f"依赖 → [{', '.join(dep_names)}]")
            if changes:
                lines.append(f"  ~ 修改「{node_name}」: {'; '.join(changes)}")
            else:
                lines.append(f"  ~ 修改「{node_name}」")
    return "\n".join(lines)


def _get_node_name(node_map: dict, node_id: str) -> str:
    """安全获取节点名称，node_map 中存储的是 TaskNode 对象"""
    node = node_map.get(node_id)
    if node is None:
        return node_id
    if hasattr(node, "name"):
        return node.name
    if isinstance(node, dict):
        return node.get("name", node_id)
    return str(node)


# ---- 节点 CRUD ----


@router.put("/projects/{project_id}/task/{task_id}")
async def edit_task(project_id: str, task_id: str, req: EditTaskRequest):
    """手动编辑某个节点（兼容旧路由）"""
    return await _edit_node_impl(project_id, task_id, req)


@router.put("/projects/{project_id}/nodes/{node_id}")
async def edit_node(project_id: str, node_id: str, req: EditTaskRequest):
    """手动编辑某个节点"""
    return await _edit_node_impl(project_id, node_id, req)


async def _edit_node_impl(project_id: str, node_id: str, req: EditTaskRequest):
    project = _load_project(project_id)
    node_map = {n.id: n for n in project.nodes}
    if node_id not in node_map:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    node = node_map[node_id]
    update_data = req.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if hasattr(node, key):
            setattr(node, key, value)

    _reschedule_project(project)
    _save_project(project)
    return {"project": project.model_dump()}


@router.post("/projects/{project_id}/command")
async def process_command(project_id: str, req: AddNodeRequest):
    """添加节点（带会话记忆）：支持 NL 解析和手动模式，AI 不确定时反问用户"""
    project = _load_project(project_id)
    node_map = {n.id: n for n in project.nodes}
    result_node_id = ""

    if req.name and req.estimated_days:
        # ---- 手动模式：直接添加 ----
        _base = len(project.nodes) + 1
        result_node_id = f"task_{_base}"
        _counter = 0
        while result_node_id in node_map:
            _counter += 1
            result_node_id = f"task_{_base}_{_counter}"
        new_node = TaskNode(
            id=result_node_id, name=req.name,
            description=req.description or req.name,
            estimated_days=req.estimated_days,
            confidence=req.confidence or 0.8,
            pre_dependencies=req.pre_dependencies or [],
            resources=req.resources or [],
            notes=req.notes or "",
        )
        project.nodes.append(new_node)
        project.messages.append({"role": "user", "content": f"手动添加节点: {req.name}"})
        project.messages.append({"role": "assistant", "content": f"已手动添加 {result_node_id}（{req.name}），工期 {req.estimated_days} 天"})

    elif req.description:
        safe_desc = sanitize_user_input(req.description)
        # ---- 确认模式：直接执行已确认的计划 ----
        if req.confirmed and req.ops_to_execute:
            project.messages.append({"role": "user", "content": safe_desc})
            project.messages.append({"role": "assistant", "content": "[已确认] 执行变更计划"})
            ops = [AtomicOp(op=o["op"], params=o["params"]) for o in req.ops_to_execute]

            # ---- 前置校验：确认操作的节点和边是否仍然存在 ----
            stale_ops = _validate_ops_against_state(ops, node_map)
            if stale_ops:
                stale_desc = "; ".join(
                    f"{o.op}({o.params.get('node_id', o.params.get('source', '?'))})"
                    for o in stale_ops
                )
                project.messages.append({
                    "role": "assistant",
                    "content": f"[冲突] 以下操作引用的节点已不存在: {stale_desc}，已跳过",
                })
                ops = [o for o in ops if o not in stale_ops]
                if not ops:
                    _save_project(project)
                    return {
                        "action": "conflict",
                        "message": f"所有操作都已失效，因为以下节点不存在: {stale_desc}",
                        "project": project.model_dump(),
                    }

        else:
            # ---- NL 模式：AI 解析（带记忆上下文） ----
            # 传递足够丰富的节点信息供拓扑描述使用
            existing_summary = [
                {
                    "id": n.id,
                    "name": n.name,
                    "estimated_days": n.estimated_days,
                    "pre_dependencies": n.pre_dependencies,
                    "resources": n.resources,
                    "status": n.status.value,
                }
                for n in project.nodes
            ]
            intents = parse_single_task(
                safe_desc, existing_summary, project.messages
            )
            # 兼容旧格式（单对象 → 包装为数组）
            if isinstance(intents, dict):
                intents = [intents]

            project.messages.append({"role": "user", "content": safe_desc})
            project.messages.append({"role": "assistant", "content": f"[意图] {json.dumps(intents, ensure_ascii=False)}"})

            # ---- 编排层：意图数组 → 原子操作序列 ----
            ops = []
            for intent in intents:
                intent_ops, extra = map_intent_to_ops(intent, node_map)
                ops.extend(intent_ops)
                if extra and extra.get("action") == "ask":
                    project.messages.append({"role": "assistant", "content": f"[待确认] {extra['question']}"})
                    _save_project(project)
                    return {
                        "action": "ask",
                        "question": extra["question"],
                        "options": extra.get("options", []),
                        "project": project.model_dump(),
                        "stage": "parsed",
                    }

            # ---- 确认门：所有操作先展示计划，用户确认后再执行 ----
            if ops and not req.confirmed:
                plan_lines = _build_plan_text(ops, node_map)
                project.messages.append({"role": "assistant", "content": f"[计划] {plan_lines}"})
                _save_project(project)
                return {
                    "action": "confirm_plan",
                    "plan": plan_lines,
                    "ops_summary": [{"op": o.op, "params": o.params} for o in ops],
                    "project": project.model_dump(),
                    "stage": "planned",
                }

        # ---- 原子执行层（确认模式在此进入） ----
        # 先检测环：收集所有 add_edge 操作，预检是否会产生环
        for op in ops:
            if op.op == "add_edge":
                src, tgt = op.params["source"], op.params["target"]
                if _detect_cycle(project.nodes, src, tgt):
                    cycle_msg = f"添加边 {src}→{tgt} 会产生环，已拒绝。"
                    project.messages.append({"role": "assistant", "content": f"[拒绝] {cycle_msg}"})
                    _save_project(project)
                    raise HTTPException(
                        status_code=400,
                        detail=cycle_msg,
                    )

        for op in ops:
            if op.op == "add_node":
                nid = op.params["id"]
                # 使用 LLM 解析出的任务描述（而非用户原始输入）
                task_desc = op.params.get("description", "")
                if not task_desc:
                    task_desc = op.params.get("name", "新任务")
                new_node = TaskNode(
                    id=nid, name=op.params.get("name", "新任务"),
                    description=task_desc,
                    estimated_days=float(op.params.get("estimated_days", 3.0)),
                    confidence=float(op.params.get("confidence", 0.7)),
                    pre_dependencies=op.params.get("pre_dependencies", []),
                    resources=op.params.get("resources", []),
                    notes=op.params.get("notes", ""),
                )
                project.nodes.append(new_node)
                node_map[nid] = new_node
                result_node_id = nid
                project.messages.append({"role": "assistant", "content": f"已添加 {nid}（{new_node.name}）"})

            elif op.op == "add_edge":
                src, tgt = op.params["source"], op.params["target"]
                if src in node_map and tgt in node_map and src != tgt:
                    tn = node_map[tgt]
                    if src not in tn.pre_dependencies:
                        tn.pre_dependencies = list(tn.pre_dependencies) + [src]
                        project.messages.append({"role": "assistant", "content": f"已添加边 {src}→{tgt}"})

            elif op.op == "remove_edge":
                src, tgt = op.params["source"], op.params["target"]
                if tgt in node_map and src in node_map[tgt].pre_dependencies:
                    node_map[tgt].pre_dependencies = [d for d in node_map[tgt].pre_dependencies if d != src]
                    project.messages.append({"role": "assistant", "content": f"已删除边 {src}→{tgt}"})

            elif op.op == "delete_node":
                nid = op.params["node_id"]
                if nid in node_map:
                    project.nodes = [n for n in project.nodes if n.id != nid]
                    for n in project.nodes:
                        if nid in n.pre_dependencies:
                            n.pre_dependencies = [d for d in n.pre_dependencies if d != nid]
                    del node_map[nid]
                    project.messages.append({"role": "assistant", "content": f"已删除节点 {nid}"})

            elif op.op == "edit_node":
                tid = op.params.get("node_id", "")
                if tid in node_map:
                    node = node_map[tid]
                    for k in ["name", "estimated_days", "confidence", "resources", "notes", "pre_dependencies", "progress", "tags"]:
                        if k in op.params and op.params[k] is not None:
                            setattr(node, k, op.params[k])
                    if "status" in op.params and op.params["status"]:
                        try:
                            node.status = TaskStatus(op.params["status"])
                        except ValueError:
                            pass
                    project.messages.append({"role": "assistant", "content": f"已更新 {tid}（{node.name}）"})
    else:
        raise HTTPException(status_code=400, detail="请提供新任务的描述（自然语言）或手动填写名称和工期")

    _reschedule_project(project)
    _save_project(project)  # _save_project 内部统一裁剪 messages
    return {"project": project.model_dump(), "new_node_id": result_node_id}


def _validate_ops_against_state(ops: list, node_map: dict) -> list:
    """增量式校验：按顺序模拟执行操作，每步更新 working_map，再校验下一步"""
    stale = []
    working_map = dict(node_map)  # 副本，模拟执行过程
    for op in ops:
        if op.op in ("add_edge", "remove_edge"):
            src = op.params.get("source", "")
            tgt = op.params.get("target", "")
            if src not in working_map or tgt not in working_map:
                stale.append(op)
        elif op.op == "delete_node":
            nid = op.params.get("node_id", "")
            if nid not in working_map:
                stale.append(op)
            else:
                del working_map[nid]  # 模拟删除，后续操作可见
        elif op.op == "edit_node":
            nid = op.params.get("node_id", "")
            if nid not in working_map:
                stale.append(op)
        elif op.op == "add_node":
            nid = op.params.get("id", "")
            if nid in working_map:
                stale.append(op)
            else:
                working_map[nid] = True  # 模拟添加，后续操作可见
    return stale


@router.delete("/projects/{project_id}/nodes/{node_id}")
async def delete_node(project_id: str, node_id: str):
    """删除节点，返回受影响的依赖信息"""
    project = _load_project(project_id)
    node_map = {n.id: n for n in project.nodes}

    if node_id not in node_map:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    deleted_name = node_map[node_id].name
    affected = _find_downstream(node_id, project.nodes)
    affected_names = [node_map[aid].name for aid in affected]

    # 删除节点
    project.nodes = [n for n in project.nodes if n.id != node_id]
    # 清除其他节点的依赖引用
    for n in project.nodes:
        if node_id in n.pre_dependencies:
            n.pre_dependencies = [d for d in n.pre_dependencies if d != node_id]

    _reschedule_project(project)
    _save_project(project)

    return {
        "deleted": node_id,
        "deleted_name": deleted_name,
        "affected_count": len(affected),
        "affected_names": affected_names,
        "project": project.model_dump(),
    }


# ---- 边（依赖关系）CRUD ----


class AddEdgeRequest(BaseModel):
    source: str                                 # from node_id
    target: str                                 # to node_id (target depends on source)


@router.get("/projects/{project_id}/edges")
async def list_edges(project_id: str):
    """列出所有边，含节点名称"""
    project = _load_project(project_id)
    node_map = {n.id: n.name for n in project.nodes}

    edges = []
    for n in project.nodes:
        for pre in n.pre_dependencies:
            edges.append({
                "source": pre,
                "source_name": node_map.get(pre, "?"),
                "target": n.id,
                "target_name": n.name,
            })
    return {"edges": edges}


@router.post("/projects/{project_id}/edges")
async def add_edge(project_id: str, req: AddEdgeRequest):
    """添加依赖边：target 将依赖 source"""
    project = _load_project(project_id)
    node_map = {n.id: n for n in project.nodes}

    if req.source not in node_map:
        raise HTTPException(status_code=404, detail=f"Source node not found: {req.source}")
    if req.target not in node_map:
        raise HTTPException(status_code=404, detail=f"Target node not found: {req.target}")
    if req.source == req.target:
        raise HTTPException(status_code=400, detail="节点不能依赖自己")

    # 环检测：添加 source → target 后，target 不应间接依赖 source
    if _detect_cycle(project.nodes, req.source, req.target):
        raise HTTPException(
            status_code=400,
            detail=f"添加边 {req.source}→{req.target} 会产生循环依赖，已拒绝。",
        )

    target_node = node_map[req.target]
    if req.source not in target_node.pre_dependencies:
        target_node.pre_dependencies = list(target_node.pre_dependencies) + [req.source]
        project.messages.append({"role": "user", "content": f"添加依赖: {req.target} 依赖 {req.source}"})
        project.messages.append({"role": "assistant", "content": f"已添加边 {req.source}→{req.target}（{node_map[req.source]}→{target_node.name}）"})
    else:
        project.messages.append({"role": "assistant", "content": f"边 {req.source}→{req.target} 已存在，无需重复添加"})

    _reschedule_project(project)
    _save_project(project)
    return {"project": project.model_dump(), "edge": {"source": req.source, "target": req.target}}


@router.delete("/projects/{project_id}/edges/{source}/{target}")
async def delete_edge(project_id: str, source: str, target: str):
    """删除依赖边"""
    project = _load_project(project_id)
    node_map = {n.id: n for n in project.nodes}

    if target not in node_map:
        raise HTTPException(status_code=404, detail=f"Target node not found: {target}")

    target_node = node_map[target]
    if source in target_node.pre_dependencies:
        target_node.pre_dependencies = [d for d in target_node.pre_dependencies if d != source]
        project.messages.append({"role": "user", "content": f"删除依赖: {target} 不再依赖 {source}"})
        project.messages.append({"role": "assistant", "content": f"已删除边 {source}→{target}"})

    _reschedule_project(project)
    _save_project(project)
    return {"project": project.model_dump(), "deleted_edge": {"source": source, "target": target}}
