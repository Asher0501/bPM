# -*- coding: utf-8 -*-
"""bePm 核心数据模型"""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
from datetime import datetime


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DELAYED = "delayed"
    BLOCKED = "blocked"


class RiskLevel(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class TaskNode(BaseModel):
    """DAG 中的一个任务节点"""
    id: str                                    # 唯一标识，如 "task_1"
    name: str                                  # 任务名称
    description: str = ""                      # 任务描述
    estimated_days: float = 1.0                # 预估工期（天）
    confidence: float = 0.8                    # 估时置信度 0.0-1.0
    pre_dependencies: list[str] = []           # 前置任务 ID 列表
    resources: list[str] = []                  # 所需资源（人员/角色/环境）
    progress: float = 0.0                      # 进度 0-100
    status: TaskStatus = TaskStatus.PENDING    # 当前状态
    start_date: Optional[str] = None           # 计划开始日期
    end_date: Optional[str] = None             # 计划结束日期
    actual_start: Optional[str] = None         # 实际开始日期
    actual_end: Optional[str] = None           # 实际结束日期
    es: Optional[float] = None                 # Earliest Start (相对天数)
    ef: Optional[float] = None                 # Earliest Finish
    ls: Optional[float] = None                 # Latest Start
    lf: Optional[float] = None                 # Latest Finish
    float_days: Optional[float] = None         # 浮动时间
    is_critical: bool = False                  # 是否在关键路径上
    notes: str = ""                            # 备注
    last_progress_update: Optional[str] = None # 上次进度更新时间 (ISO格式)


class EdgeDef(BaseModel):
    """DAG 中的一条边"""
    source: str                                # from task_id
    target: str                                # to task_id


class ScheduleResult(BaseModel):
    """排期结果"""
    topological_order: list[str] = []           # 拓扑排序后的任务 ID 序列
    critical_path: list[str] = []               # 关键路径上的任务 ID
    total_duration_days: float = 0.0            # 预计总工期
    project_buffer_days: float = 0.0            # 项目缓冲区大小
    project_buffer_consumed: float = 0.0        # 已消耗缓冲
    buffer_ratio: float = 0.0                   # 消耗比例


class RiskItem(BaseModel):
    """单条风险"""
    risk_id: str
    level: RiskLevel = RiskLevel.INFO
    dimension: str                              # 风险维度
    task_id: Optional[str] = None               # 关联任务
    message: str
    suggestion: str = ""


class BufferInfo(BaseModel):
    """缓冲区信息"""
    total_days: float = 0.0
    consumed_days: float = 0.0
    remaining_days: float = 0.0
    ratio: float = 0.0                          # 消耗比例
    status: str = "green"                       # green | yellow | red


class Project(BaseModel):
    """完整的项目实体"""
    id: str
    name: str
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    deadline: str = ""                          # 项目截止日期
    nodes: list[TaskNode] = []
    edges: list[EdgeDef] = []
    schedule: Optional[ScheduleResult] = None
    risks: list[RiskItem] = []
    buffer: Optional[BufferInfo] = None
    raw_input: str = ""                         # 原始输入文本
    messages: list[dict] = []                    # LLM 对话历史 [{role, content}, ...]


# ---- API Request/Response Models ----


class CreateProjectRequest(BaseModel):
    description: str = ""
    deadline: str = ""                          # 截止日期，如 "2026-12-31"
    file_text: Optional[str] = None             # 可选：从文件读取的内容
    additional_info: str = ""                   # 补充信息


class ProgressUpdateRequest(BaseModel):
    progress_text: str                          # 进展描述（自然语言）


class EditTaskRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    estimated_days: Optional[float] = None
    confidence: Optional[float] = None
    pre_dependencies: Optional[list[str]] = None
    resources: Optional[list[str]] = None
    progress: Optional[float] = None
    status: Optional[TaskStatus] = None
    notes: Optional[str] = None


class AddNodeRequest(BaseModel):
    description: str = ""                          # 自然语言描述（AI 解析）
    name: Optional[str] = None                     # 手动模式：任务名
    estimated_days: Optional[float] = None         # 手动模式：工期
    confidence: Optional[float] = None             # 手动模式：置信度
    pre_dependencies: Optional[list[str]] = None   # 手动模式：前置依赖ID列表
    resources: Optional[list[str]] = None          # 手动模式：资源
    notes: Optional[str] = None                    # 备注
    confirmed: bool = False                        # 用户已确认拓扑变更计划
    ops_to_execute: Optional[list[dict]] = None    # 确认后直接执行的原子操作


class DeleteNodeResponse(BaseModel):
    deleted: str                                   # 被删节点 ID
    affected_nodes: list[str]                      # 受影响的节点 ID 列表
    project: Optional[dict] = None                 # 更新后的项目


class ProjectListResponse(BaseModel):
    id: str
    name: str
    deadline: str
    created_at: str
    updated_at: str
    node_count: int
    risk_count: int
    critical_risk_count: int


class ProjectDetailResponse(BaseModel):
    project: Project


class ScheduleResponse(BaseModel):
    project: Project


class ProgressResponse(BaseModel):
    project: Project
    updated_nodes: list[str]                    # 被更新的节点 ID
    new_risks: list[RiskItem]


class GraphResponse(BaseModel):
    """前端 DAG 渲染用的精简数据"""
    nodes: list[dict]
    edges: list[dict]
    critical_path: list[str]
    schedule: Optional[dict]


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
