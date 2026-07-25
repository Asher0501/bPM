# -*- coding: utf-8 -*-
"""WebSocket — 实时推送节点状态变更和风险告警"""

import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException

from api.projects import _load_project

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectionManager:
    """WebSocket 连接管理器：按 project_id 频道管理"""

    def __init__(self):
        # {project_id: [WebSocket, ...]}
        self._rooms: dict[str, list[WebSocket]] = {}

    async def connect(self, project_id: str, ws: WebSocket):
        await ws.accept()
        self._rooms.setdefault(project_id, []).append(ws)

    def disconnect(self, project_id: str, ws: WebSocket):
        if project_id in self._rooms:
            room = self._rooms[project_id]
            if ws in room:
                room.remove(ws)
            if not room:
                del self._rooms[project_id]

    async def broadcast(self, project_id: str, message: dict):
        """向订阅某项目的所有客户端广播消息"""
        for ws in self._rooms.get(project_id, []):
            try:
                await ws.send_json(message)
            except Exception:
                pass  # 客户端可能已断开

    async def broadcast_node_status(self, project_id: str, task_id: str,
                                     progress: float, status: str):
        """广播节点状态变更"""
        await self.broadcast(project_id, {
            "type": "node_status",
            "data": {
                "task_id": task_id,
                "progress": progress,
                "status": status,
            },
        })

    async def broadcast_risk_alert(self, project_id: str, risk: dict):
        """广播风险告警"""
        await self.broadcast(project_id, {
            "type": "risk_alert",
            "data": risk,
        })

    async def broadcast_suggestion(self, project_id: str, suggestion: str):
        """广播建议"""
        await self.broadcast(project_id, {
            "type": "suggestion",
            "data": {"message": suggestion},
        })


manager = ConnectionManager()


@router.websocket("/ws/projects/{project_id}")
async def ws_project(websocket: WebSocket, project_id: str):
    """订阅项目的实时更新"""
    # 验证项目存在 — 只捕获 HTTPException（项目不存在时 _load_project 抛出）
    try:
        _load_project(project_id)
    except HTTPException:
        await websocket.close(code=4004, reason="Project not found")
        return
    except Exception as e:
        logger.error("WebSocket 连接验证失败 (project=%s): %s", project_id, e, exc_info=True)
        await websocket.close(code=4004, reason="Internal error")
        return

    await manager.connect(project_id, websocket)

    try:
        while True:
            # 保持连接，等待客户端消息（心跳检测）
            data = await websocket.receive_text()
            # 支持心跳 ping/pong
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(project_id, websocket)
