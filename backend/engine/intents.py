# -*- coding: utf-8 -*-
"""纯原子意图定义 + 编排器（意图 → 原子操作，1:1 映射）

意图层只暴露 DAG 拓扑的原子操作。LLM 自行组合原子操作完成复杂场景。
"""

import json
from dataclasses import dataclass, field


# ---- 原子操作定义 ----

@dataclass
class AtomicOp:
    """一个原子操作"""
    op: str          # "add_node" | "edit_node" | "delete_node" | "add_edge" | "remove_edge"
    params: dict = field(default_factory=dict)


# ---- 语义意图定义（纯原子） ----

INTENT_DEFS = {
    "add_node": {
        "description": "新增一个任务节点。节点创建后是孤立的——需要额外用 add_edge 建立依赖关系。",
        "params": ["name", "estimated_days", "confidence", "resources", "notes",
                    "pre_dependencies"],       # 可选：创建时直接指定前驱
        "example": {
            "intent": "add_node",
            "name": "用户调研",
            "estimated_days": 3,
            "confidence": 0.9,
            "pre_dependencies": ["task_1"],
            "resources": ["产品经理"],
            "notes": "在UI设计之前进行"
        }
    },
    "delete_node": {
        "description": "永久删除一个任务节点。系统会自动清理所有关联的边。",
        "params": ["node_id"],
        "example": {
            "intent": "delete_node",
            "node_id": "task_4"
        }
    },
    "edit_node": {
        "description": "修改已有节点的属性。只填需要修改的字段，未填的字段保持不变。",
        "params": ["node_id", "name", "progress", "status", "estimated_days",
                    "confidence", "resources", "notes", "pre_dependencies", "tags"],
        "example": {
            "intent": "edit_node",
            "node_id": "task_1",
            "name": "数据库表结构设计",
            "progress": 100,
            "status": "completed"
        }
    },
    "add_edge": {
        "description": "添加一条依赖边（source → target）。表示 target 任务必须等 source 任务完成后才能开始。",
        "params": ["source", "target"],
        "example": {
            "intent": "add_edge",
            "source": "task_1",
            "target": "task_2"
        }
    },
    "remove_edge": {
        "description": "移除一条依赖边。不会删除节点本身。",
        "params": ["source", "target"],
        "example": {
            "intent": "remove_edge",
            "source": "task_1",
            "target": "task_2"
        }
    },
    "ask_user": {
        "description": "需要用户确认或补充信息。",
        "params": ["question", "options"],
        "example": {
            "intent": "ask_user",
            "question": "你想删除哪个节点？",
            "options": ["task_3 后端API", "task_4 前端页面"]
        }
    },
}


# ---- 编排器：意图 → 原子操作（1:1 映射） ----

def _generate_node_id(node_map: dict) -> str:
    """生成不重复的节点 ID"""
    base = len(node_map) + 1
    new_id = f"task_{base}"
    counter = 0
    while new_id in node_map:
        counter += 1
        new_id = f"task_{base}_{counter}"
    return new_id


def map_intent_to_ops(intent: dict, node_map: dict) -> tuple[list[AtomicOp], dict | None]:
    """将语义意图映射为原子操作序列（1:1 映射，一个 intent → 一个 AtomicOp）

    复杂场景由 LLM 输出多个 intent，上层循环调用本函数。

    Returns:
        (operations, response_extra)
    """
    intent_type = intent.get("intent", "")

    if intent_type == "add_node":
        new_id = _generate_node_id(node_map)
        op = AtomicOp("add_node", {
            "id": new_id,
            "name": intent.get("name", ""),
            "description": intent.get("description", intent.get("name", "")),
            "estimated_days": float(intent.get("estimated_days", 3)),
            "confidence": float(intent.get("confidence", 0.7)),
            "pre_dependencies": intent.get("pre_dependencies", []),
            "resources": intent.get("resources", []),
            "notes": intent.get("notes", ""),
        })
        return [op], None

    elif intent_type == "delete_node":
        nid = intent.get("node_id", "")
        if nid and nid in node_map:
            return [AtomicOp("delete_node", {"node_id": nid})], None
        return [], {"action": "ask", "question": f"节点 {nid} 不存在，请确认节点ID"}

    elif intent_type == "edit_node":
        nid = intent.get("node_id", "")
        if nid and nid in node_map:
            params = {"node_id": nid}
            for key in ("name", "progress", "status", "estimated_days",
                        "confidence", "resources", "notes", "pre_dependencies", "tags"):
                val = intent.get(key)
                if val is not None:
                    params[key] = val
            return [AtomicOp("edit_node", params)], None
        return [], {"action": "ask", "question": f"节点 {nid} 不存在"}

    elif intent_type == "add_edge":
        src = intent.get("source", "")
        tgt = intent.get("target", "")
        if src and tgt and src in node_map and tgt in node_map and src != tgt:
            return [AtomicOp("add_edge", {"source": src, "target": tgt})], None
        return [], None  # 静默跳过无效边

    elif intent_type == "remove_edge":
        src = intent.get("source", "")
        tgt = intent.get("target", "")
        if src and tgt:
            return [AtomicOp("remove_edge", {"source": src, "target": tgt})], None
        return [], None

    elif intent_type == "ask_user":
        return [], {"action": "ask", "question": intent.get("question", ""),
                     "options": intent.get("options", [])}

    elif intent_type == "update_progress":
        # 兼容旧格式，映射到 edit_node
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
        return [], {"action": "unknown_intent", "raw": intent}
