# -*- coding: utf-8 -*-
"""语义意图 → 原子 API 调用链的映射层

AI 只输出语义意图（不关心 task_id、不关心 CRUD 细节），
编排器负责将意图翻译为原子 API 调用序列。
"""

import json
from dataclasses import dataclass, field


# ---- 原子操作定义 ----

@dataclass
class AtomicOp:
    """一个原子操作"""
    op: str          # "add_node" | "edit_node" | "delete_node" | "add_edge" | "remove_edge"
    params: dict = field(default_factory=dict)


# ---- 语义意图定义 ----

INTENT_DEFS = {
    "add_connected_node": {
        "description": "新增一个节点并建立边连接。downstream_deps 只填直接紧后任务",
        "params": ["name", "estimated_days", "confidence", "resources", "notes",
                    "pre_dependencies",    # 新节点依赖哪些节点（直接前驱）
                    "downstream_deps"],     # 直接依赖新节点的任务（不填间接后继！）
        "example": {
            "intent": "add_connected_node",
            "name": "用户调研",
            "estimated_days": 3,
            "confidence": 0.9,
            "downstream_deps": ["task_1", "task_2"],
            "notes": "用户调研应在UI设计和数据库设计之前"
        }
    },
    "add_task_in_chain": {
        "description": "在已有依赖链中插入一个任务",
        "params": ["name", "estimated_days", "after_task", "before_tasks"],
        "example": {
            "intent": "add_task_in_chain",
            "name": "代码评审",
            "estimated_days": 1,
            "after_task": "task_3",
            "before_tasks": ["task_5"]
        }
    },
    "delete_node_and_reconnect": {
        "description": "删除节点，将其上下游重新连接",
        "params": ["node_id"],
        "example": {
            "intent": "delete_node_and_reconnect",
            "node_id": "task_4"
        }
    },
    "connect_nodes": {
        "description": "在已有节点间建立依赖关系",
        "params": ["source_id", "target_ids"],
        "example": {
            "intent": "connect_nodes",
            "source_id": "task_1",
            "target_ids": ["task_2", "task_3"]
        }
    },
    "ask_user": {
        "description": "需要用户确认或补充信息",
        "params": ["question", "options"],
        "example": {
            "intent": "ask_user",
            "question": "新节点是否应依赖数据库设计？",
            "options": ["是，依赖数据库设计", "否，可以并行", "依赖其他任务..."]
        }
    },
    "update_progress": {
        "description": "更新已有任务的进度或状态",
        "params": ["updates"],
        "example": {
            "intent": "update_progress",
            "updates": [
                {"task_id": "task_1", "progress": 100, "status": "completed", "notes": "按时完成"},
                {"task_id": "task_2", "progress": 40, "status": "in_progress", "notes": "比计划慢1天"}
            ]
        }
    },
}


# ---- 编排器：意图 → 原子操作序列 ----


def map_intent_to_ops(intent: dict, node_map: dict) -> tuple[list[AtomicOp], dict | None]:
    """将语义意图映射为原子操作序列

    Args:
        intent: {"intent": "xxx", ...params}
        node_map: {node_id: TaskNode} 当前项目节点

    Returns:
        (operations, response_extra)  # 操作列表 + 额外响应信息
    """
    intent_type = intent.get("intent", "")

    if intent_type == "add_connected_node":
        ops = _map_add_connected_node(intent, node_map)
        return ops, None

    elif intent_type == "add_task_in_chain":
        ops = _map_add_task_in_chain(intent, node_map)
        return ops, None

    elif intent_type == "delete_node_and_reconnect":
        ops = _map_delete_and_reconnect(intent, node_map)
        return ops, None

    elif intent_type == "connect_nodes":
        ops = _map_connect_nodes(intent, node_map)
        return ops, None

    elif intent_type == "ask_user":
        return [], {"action": "ask", "question": intent.get("question", ""),
                     "options": intent.get("options", [])}

    elif intent_type == "update_progress":
        ops = []
        for upd in intent.get("updates", []):
            tid = upd.get("task_id", "")
            if tid and tid in node_map:
                params = {"node_id": tid}
                for key in ("progress", "status", "name", "notes"):
                    val = upd.get(key)
                    if val is not None:
                        params[key] = val
                ops.append(AtomicOp("edit_node", params))
        return ops, None

    else:
        # 未知意图 → 降级为直接执行原始格式
        return [], {"action": "unknown_intent", "raw": intent}


def _generate_node_id(node_map: dict) -> str:
    """生成不重复的节点 ID，避免 while 循环死锁"""
    base = len(node_map) + 1
    new_id = f"task_{base}"
    counter = 0
    while new_id in node_map:
        counter += 1
        new_id = f"task_{base}_{counter}"
    return new_id


def _map_add_connected_node(intent: dict, node_map: dict) -> list[AtomicOp]:
    """add_connected_node → [add_node, add_edge, add_edge, ...]

    自动过滤间接下游：如果 B 已经依赖 A，而新节点让 A 依赖它，
    则 B 不需要直接依赖新节点（通过 A 间接可达）。
    """
    ops = []
    new_id = _generate_node_id(node_map)

    # 1. 添加节点
    ops.append(AtomicOp("add_node", {
        "id": new_id,
        "name": intent.get("name", ""),
        "estimated_days": float(intent.get("estimated_days", 3)),
        "confidence": float(intent.get("confidence", 0.7)),
        "pre_dependencies": intent.get("pre_dependencies", []),
        "resources": intent.get("resources", []),
        "notes": intent.get("notes", ""),
    }))

    # 2. 前向边：照单执行 LLM 指定的 downstream_deps
    # 不设机械过滤——LLM 应自行判断直接后继，用户通过确认门把关
    for tid in intent.get("downstream_deps", []):
        if tid in node_map and tid != new_id:
            ops.append(AtomicOp("add_edge", {"source": new_id, "target": tid}))

    return ops


def _map_add_task_in_chain(intent: dict, node_map: dict) -> list[AtomicOp]:
    """在链中插入：先添加节点，再断开旧边、建立新边"""
    ops = []
    new_id = _generate_node_id(node_map)

    # 1. 添加节点（依赖 after_task）
    deps = [intent["after_task"]] if intent.get("after_task") in node_map else []
    ops.append(AtomicOp("add_node", {
        "id": new_id, "name": intent.get("name", ""),
        "estimated_days": float(intent.get("estimated_days", 3)),
        "confidence": float(intent.get("confidence", 0.7)),
        "pre_dependencies": deps,
        "resources": intent.get("resources", []), "notes": intent.get("notes", ""),
    }))

    # 2. 断开 before_tasks 对 after_task 的旧依赖，改为依赖新节点
    after = intent.get("after_task", "")
    for tid in intent.get("before_tasks", []):
        if tid in node_map and after in node_map:
            node = node_map[tid]
            if after in node.pre_dependencies:
                ops.append(AtomicOp("remove_edge", {"source": after, "target": tid}))
            ops.append(AtomicOp("add_edge", {"source": new_id, "target": tid}))

    return ops


def _map_delete_and_reconnect(intent: dict, node_map: dict) -> list[AtomicOp]:
    """删节点，上游→下游重连"""
    ops = []
    nid = intent.get("node_id", "")
    if nid not in node_map:
        return ops

    node = node_map[nid]
    downstream = [tid for tid, n in node_map.items() if nid in n.pre_dependencies]
    upstream = node.pre_dependencies

    # 1. 删除节点
    ops.append(AtomicOp("delete_node", {"node_id": nid}))

    # 2. 每个上游 → 每个下游
    for pre in upstream:
        for post in downstream:
            ops.append(AtomicOp("add_edge", {"source": pre, "target": post}))

    return ops


def _map_connect_nodes(intent: dict, node_map: dict) -> list[AtomicOp]:
    """建立依赖：source → 每个 target"""
    ops = []
    src = intent.get("source_id", "")
    for tid in intent.get("target_ids", []):
        if src in node_map and tid in node_map and src != tid:
            ops.append(AtomicOp("add_edge", {"source": src, "target": tid}))
    return ops
