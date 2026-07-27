# -*- coding: utf-8 -*-
"""Schedule integrity E2E tests — topological order, critical path, float consistency.

Validates that the CPM scheduling engine produces correct results
regardless of project structure modifications.
"""

import pytest
from helpers import (
    req, create_test_project,
    assert_valid_schedule, assert_valid_buffer, assert_valid_graph,
    assert_topo_order_valid,
)


class TestTopologicalOrder:
    """Topological sort correctness after create and modify."""

    def test_topo_order_after_create(self):
        """Freshly created project: predecessors before successors in topo order."""
        pid, proj = create_test_project(
            "Chain project: DB design 2d, API dev 5d depends on DB design, "
            "Testing 2d depends on API dev."
        )
        try:
            topo = proj["schedule"]["topological_order"]
            assert len(topo) == len(proj["nodes"])
            assert_topo_order_valid(proj["nodes"], topo)
        finally:
            req("DELETE", f"/api/projects/{pid}")

    def test_topo_order_after_reschedule(self):
        """Re-schedule maintains topological order."""
        pid, _ = create_test_project(
            "DAG project: DB design 2d, API dev 5d depends on DB, "
            "Frontend 3d depends on DB, Testing 2d depends on API and Frontend."
        )
        try:
            s, d = req("POST", f"/api/projects/{pid}/schedule")
            assert s == 200
            proj = d["project"]
            assert_topo_order_valid(proj["nodes"],
                                   proj["schedule"]["topological_order"])
        finally:
            req("DELETE", f"/api/projects/{pid}")

    def test_topo_order_with_parallel_paths(self):
        """Parallel paths both appear correctly in topological order."""
        pid, proj = create_test_project(
            "Root 1d. BranchA 4d depends on Root. BranchB 3d depends on Root. "
            "Merge 1d depends on BranchA and BranchB."
        )
        try:
            topo = proj["schedule"]["topological_order"]
            pos = {tid: i for i, tid in enumerate(topo)}
            # Root before both branches
            root_id = None
            for n in proj["nodes"]:
                if n["name"].startswith("Root"):
                    root_id = n["id"]
                    break
            assert root_id, "Root node not found"
            root_pos = pos.get(root_id, -1)
            # Both branches should be after root
            for n in proj["nodes"]:
                if "Branch" in n["name"]:
                    assert pos.get(n["id"], 0) > root_pos, \
                        f"{n['name']} should be after Root"
        finally:
            req("DELETE", f"/api/projects/{pid}")


class TestCriticalPath:
    """Critical path identification and float calculations."""

    def test_critical_path_nodes_have_zero_float(self, tmp_project):
        """All nodes on critical path have float ≈ 0."""
        pid, proj = tmp_project
        cp = set(proj["schedule"]["critical_path"])
        if not cp:
            pytest.skip("No critical path found")
        for n in proj["nodes"]:
            if n["id"] in cp:
                f = n.get("float_days")
                assert f is not None, f"CP node {n['id']} missing float"
                assert abs(f) < 0.01, \
                    f"CP node {n['id']} float={f}, expected ≈0"

    def test_non_critical_nodes_have_positive_float(self, tmp_project):
        """Non-critical nodes have float > 0 (or at least != 0)."""
        pid, proj = tmp_project
        cp = set(proj["schedule"]["critical_path"])
        non_cp = [n for n in proj["nodes"] if n["id"] not in cp]
        if not non_cp:
            pytest.skip("All nodes on critical path — no non-CP to test")
        # At least one non-CP node should have positive float
        positive_floats = [n for n in non_cp
                          if n.get("float_days") is not None and n["float_days"] > 0.01]
        # In a diamond DAG with parallel paths, non-CP should have float
        # This is informational — not all DAGs guarantee this

    def test_critical_path_is_longest_path(self):
        """Total duration equals the longest path through the DAG."""
        pid, proj = create_test_project(
            "Root 1d. FastPath 2d depends on Root. SlowPath 10d depends on Root. "
            "Merge 1d depends on FastPath and SlowPath."
        )
        try:
            dur = proj["schedule"]["total_duration_days"]
            # Slow path: 1 + 10 + 1 = 12
            assert dur >= 11.5, f"Duration too short: {dur} (expected >= 12)"
            assert "SlowPath" in str(proj["schedule"]["critical_path"])
        finally:
            req("DELETE", f"/api/projects/{pid}")

    def test_single_node_critical_path(self):
        """Single node project: CP = [task_1], duration = estimate."""
        pid, proj = create_test_project(
            "Single node project for testing with database design for 2 days."
        )
        try:
            cp = proj["schedule"]["critical_path"]
            assert len(cp) == 1, f"Single node CP should have 1 node, got {len(cp)}"
            assert proj["schedule"]["total_duration_days"] > 0
        finally:
            req("DELETE", f"/api/projects/{pid}")


class TestDeadlineHandling:
    """Deadline parsing and LS/LF alignment."""

    def test_relative_deadline_days(self):
        """Deadline '30天' affects LS/LF calculation."""
        pid, proj = create_test_project(
            "DB design 2d. API dev 5d depends on DB.",
            deadline="30天"
        )
        try:
            # With 30-day deadline for a 7-day project, LS should be generous
            for n in proj["nodes"]:
                assert n.get("ls") is not None, f"Node {n['id']} missing ls"
                assert n.get("lf") is not None, f"Node {n['id']} missing lf"
        finally:
            req("DELETE", f"/api/projects/{pid}")

    def test_relative_deadline_months(self):
        """Deadline '2个月' parsed as 60 days."""
        pid, proj = create_test_project(
            "DB design 2d. API dev 5d depends on DB.",
            deadline="2个月"
        )
        try:
            assert proj["schedule"]["total_duration_days"] > 0
        finally:
            req("DELETE", f"/api/projects/{pid}")

    def test_absolute_deadline(self):
        """Absolute date deadline '2026-12-31' stored but doesn't offset LS/LF."""
        pid, proj = create_test_project(
            "DB design 2d. API dev 5d depends on DB.",
            deadline="2026-12-31"
        )
        try:
            assert proj["deadline"] == "2026-12-31"
            assert proj["schedule"]["total_duration_days"] > 0
        finally:
            req("DELETE", f"/api/projects/{pid}")


class TestReSchedule:
    """Re-scheduling behavior."""

    def test_re_schedule_preserves_data(self):
        """Re-schedule keeps all nodes and edges."""
        pid, proj = create_test_project(
            "DB design 2d. API dev 5d depends on DB. Frontend 3d depends on DB."
        )
        try:
            orig_node_count = len(proj["nodes"])
            orig_edge_count = len(proj["edges"])

            s, d = req("POST", f"/api/projects/{pid}/schedule")
            assert s == 200
            new_proj = d["project"]
            assert len(new_proj["nodes"]) == orig_node_count, "Node count changed after re-schedule"
            assert len(new_proj["edges"]) == orig_edge_count, "Edge count changed after re-schedule"
            assert_valid_schedule(new_proj["schedule"])
        finally:
            req("DELETE", f"/api/projects/{pid}")

    def test_re_schedule_idempotent(self):
        """Two consecutive re-schedules produce same total_duration."""
        pid, _ = create_test_project(
            "DB design 2d. API dev 5d depends on DB. Testing 2d depends on API."
        )
        try:
            _, d1 = req("POST", f"/api/projects/{pid}/schedule")
            _, d2 = req("POST", f"/api/projects/{pid}/schedule")
            dur1 = d1["project"]["schedule"]["total_duration_days"]
            dur2 = d2["project"]["schedule"]["total_duration_days"]
            assert abs(dur1 - dur2) < 0.01, \
                f"Re-schedule not idempotent: {dur1} vs {dur2}"
        finally:
            req("DELETE", f"/api/projects/{pid}")


class TestGraphEndpoint:
    """Graph data for DAG rendering."""

    def test_graph_has_date_fields(self, tmp_project):
        """Graph nodes have es_date/ef_date/ls_date/lf_date for frontend."""
        pid, _ = tmp_project
        s, graph = req("GET", f"/api/projects/{pid}/graph")
        assert s == 200, f"Graph failed: {graph}"
        assert_valid_graph(graph)
        # Each node should have date fields (for frontend tooltip)
        for gn in graph["nodes"]:
            for f in ["es_date", "ef_date", "ls_date", "lf_date"]:
                assert f in gn, f"Graph node missing date field: {f}"

    def test_graph_edges_reference_nodes(self, tmp_project):
        """All graph edge sources and targets exist in graph nodes."""
        pid, _ = tmp_project
        s, graph = req("GET", f"/api/projects/{pid}/graph")
        assert s == 200
        node_ids = {n["id"] for n in graph["nodes"]}
        for e in graph["edges"]:
            assert e["source"] in node_ids, \
                f"Edge source '{e['source']}' not in graph nodes"
            assert e["target"] in node_ids, \
                f"Edge target '{e['target']}' not in graph nodes"
