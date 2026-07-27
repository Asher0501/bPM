# -*- coding: utf-8 -*-
"""Scheduler — 拓扑排序 + 关键路径 + 缓冲区计算"""

import logging
import re
from collections import deque
from models.project import TaskNode, EdgeDef, ScheduleResult, BufferInfo
from config import get_config

logger = logging.getLogger(__name__)


def _scheduler_config():
    return get_config().scheduler


def _risk_scan_config():
    return get_config().risk_scan


def topological_sort(nodes: list[TaskNode]) -> list[str]:
    """
    Kahn 算法进行拓扑排序。
    返回按拓扑顺序排列的 task_id 列表。
    如果存在环（无法拓扑排序），返回 None。
    """
    indegree: dict[str, int] = {n.id: 0 for n in nodes}
    successors: dict[str, list[str]] = {n.id: [] for n in nodes}

    for node in nodes:
        for pre in node.pre_dependencies:
            # 确保前置节点存在，不存在时记录警告并跳过
            if pre in indegree:
                indegree[node.id] += 1
                successors[pre].append(node.id)
            else:
                logger.warning("拓扑排序: 节点 %s 引用了不存在的依赖 %s，已跳过", node.id, pre)

    # Kahn's algorithm
    queue = deque([nid for nid, deg in indegree.items() if deg == 0])
    sorted_ids: list[str] = []

    while queue:
        current = queue.popleft()
        sorted_ids.append(current)
        for succ in successors.get(current, []):
            indegree[succ] -= 1
            if indegree[succ] == 0:
                queue.append(succ)

    # 如果排序结果少于节点数，说明存在环
    if len(sorted_ids) < len(nodes):
        remaining = [nid for nid in indegree if indegree[nid] > 0]
        logger.warning(
            "拓扑排序检测到环！涉及 %d 个节点: %s。这些节点将被追加到排序末尾，但其 ES/EF/LS/LF 计算不完整。",
            len(remaining), remaining,
        )
        sorted_ids.extend(remaining)

    return sorted_ids


def compute_critical_path(
    nodes: list[TaskNode],
    edges: list[EdgeDef],
    project_deadline_days: float | None = None,
) -> tuple[dict[str, dict], list[str], float]:
    """
    使用 CPM (Critical Path Method) 计算关键路径。

    关键路径识别基于 total_duration（项目实际最早完工时间），不受 deadline 影响。
    当提供 project_deadline_days 时，LS/LF 值会按 deadline 进行调整，
    使得甘特图能正确反映截止日期紧迫度。

    返回:
        node_times: {task_id: {es, ef, ls, lf, float}}
        critical_path: 关键路径上的 task_id 列表
        total_duration: 总工期（天）
    """
    node_map = {n.id: n for n in nodes}
    sorted_ids = topological_sort(nodes)

    # 构建后继映射
    successors: dict[str, list[str]] = {nid: [] for nid in node_map}
    for edge in edges:
        if edge.source in successors:
            successors[edge.source].append(edge.target)

    # 前置映射
    predecessors: dict[str, list[str]] = {nid: [] for nid in node_map}
    for edge in edges:
        if edge.target in predecessors:
            predecessors[edge.target].append(edge.source)

    # ---- 正向传播：计算 ES / EF ----
    es: dict[str, float] = {}
    ef: dict[str, float] = {}

    for nid in sorted_ids:
        node = node_map[nid]
        if not node.pre_dependencies:
            es[nid] = 0.0
        else:
            # ES = max(EF of all predecessors)
            max_predecessor_ef = 0.0
            for pre in node.pre_dependencies:
                if pre in ef:
                    max_predecessor_ef = max(max_predecessor_ef, ef[pre])
            es[nid] = max_predecessor_ef
        ef[nid] = es[nid] + node.estimated_days

    total_duration = max(ef.values()) if ef else 0.0

    # ---- 反向传播（第一遍）：基于 total_duration 计算 LS/LF ----
    # 使用 total_duration 作为基线，确保关键路径识别正确（float ≈ 0）
    finish_time = total_duration

    ls: dict[str, float] = {}
    lf: dict[str, float] = {}

    # 从拓扑逆序计算
    for nid in reversed(sorted_ids):
        succ_list = successors.get(nid, [])
        if not succ_list:
            # 终点节点
            lf[nid] = finish_time
        else:
            # LF = min(LS of all successors)
            min_succ_ls = float("inf")
            for succ in succ_list:
                if succ in ls:
                    min_succ_ls = min(min_succ_ls, ls[succ])
            lf[nid] = min_succ_ls if min_succ_ls != float("inf") else finish_time
        ls[nid] = lf[nid] - node_map[nid].estimated_days

    # ---- 保存第一遍（基于 total_duration）的 LS/LF ----
    # 用于 float 计算和关键路径识别，不受 deadline 影响
    ls_baseline = ls.copy()
    lf_baseline = lf.copy()

    # ---- 反向传播（第二遍）：基于 deadline 调整 LS/LF ----
    # 当有 deadline 时，用它重新计算 LS/LF，用于甘特图显示
    if project_deadline_days is not None:
        deadline_finish = max(project_deadline_days, total_duration)
        ls_dl: dict[str, float] = {}
        lf_dl: dict[str, float] = {}
        for nid in reversed(sorted_ids):
            succ_list = successors.get(nid, [])
            if not succ_list:
                lf_dl[nid] = deadline_finish
            else:
                min_succ_ls = float("inf")
                for succ in succ_list:
                    if succ in ls_dl:
                        min_succ_ls = min(min_succ_ls, ls_dl[succ])
                lf_dl[nid] = min_succ_ls if min_succ_ls != float("inf") else deadline_finish
            ls_dl[nid] = lf_dl[nid] - node_map[nid].estimated_days
        # 用 deadline 调整后的值覆盖 ls/lf（用于显示）
        ls = ls_dl
        lf = lf_dl

    # ---- 计算 Float（基于 total_duration 的基线 LS） ----
    node_times: dict[str, dict] = {}
    critical_path: list[str] = []

    for nid in node_map:
        # float 基于 total_duration 对应的基线 LS，确保关键路径识别正确
        float_val = ls_baseline.get(nid, 0) - es.get(nid, 0)
        node_times[nid] = {
            "es": round(es.get(nid, 0), 2),
            "ef": round(ef.get(nid, 0), 2),
            "ls": round(ls.get(nid, 0), 2),
            "lf": round(lf.get(nid, 0), 2),
            "float": round(float_val, 2),
        }
        # Float ≈ 0 表示在关键路径上
        if abs(float_val) < 0.01:
            critical_path.append(nid)

    return node_times, critical_path, total_duration


def build_edges_from_dependencies(nodes: list[TaskNode]) -> list[EdgeDef]:
    """从节点的 pre_dependencies 构建边列表"""
    edges = []
    for node in nodes:
        for pre in node.pre_dependencies:
            edges.append(EdgeDef(source=pre, target=node.id))
    return edges


def _parse_deadline_to_days(deadline: str | None) -> float | None:
    """将截止日期字符串解析为相对于项目开始的天数。

    排期时间轴是相对时间（0 = 项目启动），因此只有相对 deadline
    （如"30天"）才参与 LS/LF 偏移计算。绝对日期（如"2024-12-31"）
    无法在排期引擎中转为有意义的相对偏移，返回 None。

    支持格式:
    - "30天" → 30
    - "2个月" → 60（近似）
    - "2024-12-31" → None（绝对日期，不参与 LS/LF 偏移）
    - 空字符串 / 无法解析 → None
    """
    if not deadline or not deadline.strip():
        return None

    deadline = deadline.strip()

    # 尝试匹配 "N天" 或 "N 天" 或 "N天（含周末）" 等
    m = re.search(r'(\d+)\s*天', deadline)
    if m:
        return float(m.group(1))

    # 尝试匹配 "N个月" 或 "N 个月"
    m = re.search(r'(\d+)\s*个月', deadline)
    if m:
        return float(m.group(1)) * 30.0

    # 尝试匹配 "N周" 或 "N 周"
    m = re.search(r'(\d+)\s*周', deadline)
    if m:
        return float(m.group(1)) * 7.0

    # 绝对日期格式 "YYYY-MM-DD"：不参与 LS/LF 偏移计算
    # 排期引擎使用相对时间轴（0 = 项目启动），绝对日期无法映射
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', deadline)
    if m:
        return None

    return None


def create_schedule(
    nodes: list[TaskNode],
    deadline: str = "",
) -> ScheduleResult:
    """
    完整的排期流程:
    1. 构建边
    2. 拓扑排序
    3. 计算关键路径
    4. 计算缓冲区
    5. 写回节点属性
    """
    edges = build_edges_from_dependencies(nodes)

    sorted_ids = topological_sort(nodes)

    # 解析截止日期：将 deadline 字符串转为天数
    # 绝对日期（如"2024-12-31"）返回 None，不参与 LS/LF 偏移
    # 仅相对 deadline（如"30天"）会触发第二遍 LS/LF 计算
    deadline_days = _parse_deadline_to_days(deadline)

    node_times, critical_path, total_duration = compute_critical_path(
        nodes, edges, deadline_days
    )

    # 计算缓冲区（项目缓冲 = 关键路径工期 × 缓冲比例）
    buffer_days = round(total_duration * _scheduler_config().buffer_ratio, 1)

    # 写回节点数据
    node_map = {n.id: n for n in nodes}
    for nid, times in node_times.items():
        if nid in node_map:
            node_map[nid].es = times["es"]
            node_map[nid].ef = times["ef"]
            node_map[nid].ls = times["ls"]
            node_map[nid].lf = times["lf"]
            node_map[nid].float_days = times["float"]
            node_map[nid].is_critical = nid in critical_path

    return ScheduleResult(
        topological_order=sorted_ids,
        critical_path=critical_path,
        total_duration_days=round(total_duration, 2),
        project_buffer_days=buffer_days,
        project_buffer_consumed=0.0,
        buffer_ratio=0.0,
    )


def compute_buffer_info(schedule: ScheduleResult) -> BufferInfo:
    """根据 ScheduleResult 计算缓冲区信息"""
    total = schedule.project_buffer_days
    consumed = schedule.project_buffer_consumed
    remaining = max(0, total - consumed)
    ratio = consumed / total if total > 0 else 0.0

    if ratio < 0.33:
        status = "green"
    elif ratio < 0.67:
        status = "yellow"
    else:
        status = "red"

    return BufferInfo(
        total_days=round(total, 2),
        consumed_days=round(consumed, 2),
        remaining_days=round(remaining, 2),
        ratio=round(ratio, 4),
        status=status,
    )


def update_buffer_consumption(schedule: ScheduleResult, delay_days: float):
    """更新缓冲区消耗"""
    schedule.project_buffer_consumed += delay_days
    if schedule.project_buffer_days > 0:
        schedule.buffer_ratio = round(
            schedule.project_buffer_consumed / schedule.project_buffer_days, 4
        )
    else:
        schedule.buffer_ratio = 1.0


def structural_risk_scan(
    nodes: list,
    schedule,
    buffer_info,
) -> list[dict]:
    """纯算法风险扫描（不依赖LLM），基于项目状态计算结构风险。
    检测维度:
    1. 缓冲区消耗超标(yellow/red)
    2. 关键路径上低置信度节点
    3. 汇聚点(入度>=3的瓶颈节点)
    4. 长依赖链(深度>5)
    5. 近关键路径(系统性风险)
    6. 关键路径上已延迟节点
    """
    risks: list[dict] = []
    node_map = {n.id: n for n in nodes}
    risk_seq = [0]
    rcfg = _risk_scan_config()

    def _rid():
        risk_seq[0] += 1
        return f"struct_risk_{risk_seq[0]}"

    # 1. 缓冲区消耗检测
    if buffer_info:
        if buffer_info.status == 'red':
            risks.append({
                'risk_id': _rid(), 'level': 'critical', 'dimension': '缓冲区过度消耗', 'task_id': None,
                'message': f'项目缓冲已消耗 {buffer_info.ratio*100:.0f}%（{buffer_info.consumed_days:.1f}/{buffer_info.total_days:.1f}天），状态为红色。项目延期风险极高。',
                'suggestion': '建议：1) 审查关键路径任务并加速 2) 与干系人沟通范围裁剪 3) 考虑增加资源投入',
            })
        elif buffer_info.status == 'yellow':
            risks.append({
                'risk_id': _rid(), 'level': 'warning', 'dimension': '缓冲区明显消耗', 'task_id': None,
                'message': f'项目缓冲已消耗 {buffer_info.ratio*100:.0f}%（{buffer_info.consumed_days:.1f}/{buffer_info.total_days:.1f}天），状态为黄色。需要关注关键路径进展。',
                'suggestion': '建议：1) 密切监控关键路径任务 2) 识别潜在延迟因素 3) 准备应急计划',
            })

    # 2. 关键路径上低置信度节点
    for n in [x for x in nodes if x.is_critical and x.confidence < rcfg.confidence_warning]:
        risks.append({
            'risk_id': _rid(), 'level': 'warning' if n.confidence < (rcfg.confidence_warning * 0.67) else 'info',
            'dimension': '关键路径估时置信度低', 'task_id': n.id,
            'message': f'节点「{n.name}」(id={n.id}) 位于关键路径，置信度仅 {n.confidence:.0%}，工期估算不可靠。',
            'suggestion': f'建议：1) 将「{n.name}」拆分为更小的子任务 2) 参考历史数据重新估算',
        })

    # 3. 汇聚点检测(入度>=阈值)
    indeg: dict[str, int] = {n.id: 0 for n in nodes}
    for n in nodes:
        for p in n.pre_dependencies:
            if p in indeg:
                indeg[n.id] += 1
    for n in [x for x in nodes if indeg.get(x.id, 0) >= rcfg.merge_threshold_indegree]:
        risks.append({
            'risk_id': _rid(), 'level': 'warning', 'dimension': '高汇聚度瓶颈节点', 'task_id': n.id,
            'message': f'节点「{n.name}」(id={n.id}) 入度为 {indeg[n.id]}，是高汇聚度瓶颈。',
            'suggestion': f'建议：1) 确保「{n.name}」前置有足够缓冲 2) 考虑部分前置并行化 3) 预留额外资源',
        })

    # 4. 长依赖链检测(深度>阈值)
    succs: dict[str, list[str]] = {n.id: [] for n in nodes}
    for n in nodes:
        for p in n.pre_dependencies:
            if p in succs:
                succs[p].append(n.id)

    def _mdepth(nid: str, vis: set) -> int:
        if nid in vis:
            return 0
        vis.add(nid)
        if not succs.get(nid):
            return 1
        return 1 + max(_mdepth(c, vis) for c in succs[nid])

    for rid in [x.id for x in nodes if not x.pre_dependencies]:
        d = _mdepth(rid, set())
        if d > rcfg.chain_depth_warning:
            risks.append({
                'risk_id': _rid(), 'level': 'info', 'dimension': '长依赖链不确定性放大', 'task_id': rid,
                'message': f'从「{node_map[rid].name}」出发的依赖链深度为 {d} 层。链越长不确定性逐级放大。',
                'suggestion': '建议：1) 在长链中设置中间里程碑 2) 缩短关键路径链长 3) 考虑将长链分段管理',
            })

    # 5. 近关键路径检测(浮动<阈值天)
    if schedule and getattr(schedule, 'critical_path', None):
        near_cp = [n for n in nodes if not n.is_critical and n.float_days is not None and n.float_days < rcfg.near_critical_float_days]
        if len(near_cp) >= rcfg.near_critical_min_count:
            names = ', '.join(f'「{n.name}」({n.float_days:.1f}d)' for n in near_cp[:5])
            risks.append({
                'risk_id': _rid(), 'level': 'warning', 'dimension': '近关键路径风险', 'task_id': None,
                'message': f'存在 {len(near_cp)} 个浮动<2天的近关键路径节点：{names}。',
                'suggestion': '建议：1) 监控近关键路径节点进展 2) 纳入重点管理 3) 考虑预留额外缓冲',
            })

    # 6. 关键路径上已延迟节点
    for n in [x for x in nodes if x.is_critical and (x.status.value in ('delayed','blocked') or (x.progress<50 and x.status.value=='in_progress'))]:
        risks.append({
            'risk_id': _rid(), 'level': 'critical' if n.status.value=='blocked' else 'warning',
            'dimension': '关键路径节点延迟', 'task_id': n.id,
            'message': f'关键路径节点「{n.name}」(id={n.id}) 状态={n.status.value}，进度={n.progress:.0f}%。',
            'suggestion': f'建议：1) 排查「{n.name}」延迟根因 2) 评估后续任务并行化 3) 与干系人沟通延期影响',
        })

    return risks
