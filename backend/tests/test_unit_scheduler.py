# -*- coding: utf-8 -*-
"""Unit tests for scheduler module (no server needed)."""

import sys
sys.path.insert(0, 'backend')

from models.project import TaskNode, EdgeDef, ScheduleResult, BufferInfo
from engine.scheduler import (
    topological_sort,
    compute_critical_path,
    build_edges_from_dependencies,
    create_schedule,
    compute_buffer_info,
    update_buffer_consumption,
    _parse_deadline_to_days,
)


class TestTopologicalSort:
    def test_linear_chain(self):
        n1 = TaskNode(id='t1', name='A', estimated_days=3)
        n2 = TaskNode(id='t2', name='B', estimated_days=5, pre_dependencies=['t1'])
        n3 = TaskNode(id='t3', name='C', estimated_days=2, pre_dependencies=['t2'])
        topo = topological_sort([n1, n2, n3])
        assert topo == ['t1', 't2', 't3']

    def test_parallel_paths(self):
        n1 = TaskNode(id='t1', name='Root', estimated_days=1)
        n2 = TaskNode(id='t2', name='A', estimated_days=4, pre_dependencies=['t1'])
        n3 = TaskNode(id='t3', name='B', estimated_days=3, pre_dependencies=['t1'])
        n4 = TaskNode(id='t4', name='Merge', estimated_days=1, pre_dependencies=['t2', 't3'])
        topo = topological_sort([n1, n2, n3, n4])
        assert len(topo) == 4
        assert topo.index('t1') < topo.index('t2')
        assert topo.index('t1') < topo.index('t3')
        assert topo.index('t2') < topo.index('t4')
        assert topo.index('t3') < topo.index('t4')

    def test_single_node(self):
        n1 = TaskNode(id='t1', name='Only', estimated_days=5)
        topo = topological_sort([n1])
        assert topo == ['t1']

    def test_missing_dependency_reference(self):
        n1 = TaskNode(id='t1', name='A', estimated_days=2)
        n2 = TaskNode(id='t2', name='B', estimated_days=3, pre_dependencies=['nonexistent'])
        topo = topological_sort([n1, n2])
        assert len(topo) == 2

    def test_multi_dependency(self):
        n1 = TaskNode(id='t1', name='A', estimated_days=3)
        n2 = TaskNode(id='t2', name='B', estimated_days=5, pre_dependencies=['t1'])
        n3 = TaskNode(id='t3', name='C', estimated_days=2, pre_dependencies=['t2', 't1'])
        topo = topological_sort([n1, n2, n3])
        assert len(topo) == 3


class TestCriticalPath:
    def test_linear_chain(self):
        n1 = TaskNode(id='t1', name='A', estimated_days=3)
        n2 = TaskNode(id='t2', name='B', estimated_days=5, pre_dependencies=['t1'])
        n3 = TaskNode(id='t3', name='C', estimated_days=2, pre_dependencies=['t2'])
        nodes = [n1, n2, n3]
        edges = build_edges_from_dependencies(nodes)
        nt, cp, dur = compute_critical_path(nodes, edges)
        assert cp == ['t1', 't2', 't3']
        assert abs(dur - 10.0) < 0.01
        assert all(abs(nt[nid]['float']) < 0.01 for nid in nt)

    def test_parallel_paths(self):
        n1 = TaskNode(id='t1', name='Root', estimated_days=1)
        n2 = TaskNode(id='t2', name='A', estimated_days=4, pre_dependencies=['t1'])
        n3 = TaskNode(id='t3', name='B', estimated_days=3, pre_dependencies=['t1'])
        n4 = TaskNode(id='t4', name='Merge', estimated_days=1, pre_dependencies=['t2', 't3'])
        nodes = [n1, n2, n3, n4]
        edges = build_edges_from_dependencies(nodes)
        nt, cp, dur = compute_critical_path(nodes, edges)
        assert abs(dur - 6.0) < 0.01
        assert 't2' in cp
        assert 't3' not in cp

    def test_single_node(self):
        n1 = TaskNode(id='t1', name='Only', estimated_days=5)
        edges = build_edges_from_dependencies([n1])
        nt, cp, dur = compute_critical_path([n1], edges)
        assert cp == ['t1']
        assert abs(dur - 5.0) < 0.01


class TestBuildEdges:
    def test_basic(self):
        n1 = TaskNode(id='t1', name='A', estimated_days=2)
        n2 = TaskNode(id='t2', name='B', estimated_days=3, pre_dependencies=['t1'])
        edges = build_edges_from_dependencies([n1, n2])
        assert len(edges) == 1
        assert edges[0].source == 't1'
        assert edges[0].target == 't2'

    def test_no_deps(self):
        n1 = TaskNode(id='t1', name='A', estimated_days=2)
        edges = build_edges_from_dependencies([n1])
        assert len(edges) == 0


class TestCreateSchedule:
    def test_basic(self):
        n1 = TaskNode(id='t1', name='A', estimated_days=3)
        n2 = TaskNode(id='t2', name='B', estimated_days=4, pre_dependencies=['t1'])
        n3 = TaskNode(id='t3', name='C', estimated_days=2, pre_dependencies=['t1'])
        n4 = TaskNode(id='t4', name='D', estimated_days=1, pre_dependencies=['t2', 't3'])
        nodes = [n1, n2, n3, n4]
        sr = create_schedule(nodes)
        assert len(sr.critical_path) > 0
        assert sr.total_duration_days > 0
        assert sr.project_buffer_days > 0
        # buffer = 50% of total duration
        assert abs(sr.project_buffer_days - sr.total_duration_days * 0.5) < 0.01

    def test_with_deadline_days(self):
        n1 = TaskNode(id='t1', name='A', estimated_days=3)
        n2 = TaskNode(id='t2', name='B', estimated_days=4, pre_dependencies=['t1'])
        nodes = [n1, n2]
        sr = create_schedule(nodes, deadline="30天")
        assert sr.total_duration_days == 7.0

    def test_with_absolute_deadline(self):
        n1 = TaskNode(id='t1', name='A', estimated_days=3)
        n2 = TaskNode(id='t2', name='B', estimated_days=4, pre_dependencies=['t1'])
        nodes = [n1, n2]
        sr = create_schedule(nodes, deadline="2024-12-31")
        assert sr.total_duration_days == 7.0


class TestBufferInfo:
    def test_green(self):
        sr = ScheduleResult(total_duration_days=20.0, project_buffer_days=10.0)
        bi = compute_buffer_info(sr)
        assert bi.total_days == 10.0
        assert bi.status == "green"

    def test_red(self):
        sr = ScheduleResult(
            total_duration_days=20.0, project_buffer_days=10.0,
            project_buffer_consumed=7.0, buffer_ratio=0.7
        )
        bi = compute_buffer_info(sr)
        assert bi.status == "red"

    def test_yellow(self):
        sr = ScheduleResult(
            total_duration_days=20.0, project_buffer_days=10.0,
            project_buffer_consumed=4.0, buffer_ratio=0.4
        )
        bi = compute_buffer_info(sr)
        assert bi.status == "yellow"


class TestUpdateBufferConsumption:
    def test_basic(self):
        sr = ScheduleResult(total_duration_days=20.0, project_buffer_days=10.0)
        update_buffer_consumption(sr, 2.0)
        assert sr.project_buffer_consumed == 2.0
        assert sr.buffer_ratio == 0.2


class TestParseDeadlineToDays:
    def test_days(self):
        assert _parse_deadline_to_days("30天") == 30.0
        assert _parse_deadline_to_days("5 天") == 5.0

    def test_months(self):
        assert _parse_deadline_to_days("2个月") == 60.0

    def test_weeks(self):
        assert _parse_deadline_to_days("3周") == 21.0

    def test_absolute_date(self):
        assert _parse_deadline_to_days("2024-12-31") is None

    def test_empty(self):
        assert _parse_deadline_to_days("") is None
        assert _parse_deadline_to_days(None) is None
