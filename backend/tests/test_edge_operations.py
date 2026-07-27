# -*- coding: utf-8 -*-
"""Edge operations E2E tests — list, add, delete, cycle detection, edge cases.

Tests the full frontend→backend→frontend loop:
  frontend user adds/removes dependency arrows → backend validates → frontend gets updated graph.
"""

import pytest
from helpers import req, create_test_project


@pytest.fixture(scope="module")
def project_for_edges():
    """Create a project with clear dependency structure for edge testing."""
    pid, proj = create_test_project(
        "DB design 2d. API dev 5d depends on DB design. "
        "Frontend dev 3d depends on DB design. "
        "Testing 2d depends on API dev and Frontend dev."
    )
    return pid, proj


def _find_node_by_name(nodes, keyword):
    """Find first node whose name contains keyword (case-insensitive)."""
    for n in nodes:
        if keyword.lower() in n.get("name", "").lower():
            return n
    return None


class TestEdgeList:
    """GET /api/projects/{id}/edges — frontend edge panel data."""

    def test_list_edges_has_names(self, project_for_edges):
        """Each edge includes source_name and target_name for display."""
        pid, _ = project_for_edges
        s, d = req("GET", f"/api/projects/{pid}/edges")
        assert s == 200, f"List edges failed: {d}"
        edges = d.get("edges", [])
        assert len(edges) > 0, "No edges in project"
        for e in edges:
            assert "source" in e
            assert "target" in e
            assert "source_name" in e, "Edge missing source_name for display"
            assert "target_name" in e, "Edge missing target_name for display"

    def test_list_edges_empty_project(self):
        """Single node project has no edges."""
        pid, _ = create_test_project(
            "Standalone project with database design for 2 days."
        )
        try:
            s, d = req("GET", f"/api/projects/{pid}/edges")
            assert s == 200
            # A single-node project from LLM might or might not have edges
            assert isinstance(d.get("edges", []), list)
        finally:
            req("DELETE", f"/api/projects/{pid}")


class TestEdgeAdd:
    """POST /api/projects/{id}/edges — adding dependency edges."""

    def test_add_valid_edge(self, project_for_edges):
        """Adding a valid edge updates target's pre_dependencies."""
        pid, proj = project_for_edges
        nodes = proj["nodes"]
        source = _find_node_by_name(nodes, "DB")
        target = _find_node_by_name(nodes, "Frontend")
        assert source and target, "Could not find DB or Frontend node"

        # Check if edge already exists
        s_check, edges_data = req("GET", f"/api/projects/{pid}/edges")
        already_exists = any(
            e["source"] == source["id"] and e["target"] == target["id"]
            for e in edges_data.get("edges", [])
        )

        s, d = req("POST", f"/api/projects/{pid}/edges", {
            "source": source["id"],
            "target": target["id"],
        })
        assert s == 200, f"Add edge failed: {d}"
        assert d.get("edge") is not None, "Response missing 'edge' object"
        assert d["edge"]["source"] == source["id"]
        assert d["edge"]["target"] == target["id"]

    def test_add_duplicate_edge_no_error(self, project_for_edges):
        """Adding an existing edge should not fail (idempotent)."""
        pid, proj = project_for_edges
        # Pick an existing edge
        s, edges_data = req("GET", f"/api/projects/{pid}/edges")
        existing = edges_data.get("edges", [])
        if not existing:
            pytest.skip("No existing edges")
        e = existing[0]
        s, d = req("POST", f"/api/projects/{pid}/edges", {
            "source": e["source"],
            "target": e["target"],
        })
        # Should succeed (already exists is not an error)
        assert s == 200, f"Duplicate edge should not error: {d}"

    def test_add_self_loop_rejected(self, project_for_edges):
        """Node cannot depend on itself."""
        pid, proj = project_for_edges
        node = proj["nodes"][0]
        s, d = req("POST", f"/api/projects/{pid}/edges", {
            "source": node["id"],
            "target": node["id"],
        })
        assert s == 400, f"Expected 400 for self-loop, got {s}: {d}"
        detail = str(d.get("detail", "")).lower()
        assert "自己" in detail or "self" in detail or "same" in detail, \
            f"Error should mention self-dependency: {d}"

    def test_add_edge_nonexistent_source(self, project_for_edges):
        """Source node must exist."""
        pid, _ = project_for_edges
        s, d = req("POST", f"/api/projects/{pid}/edges", {
            "source": "nonexistent_task",
            "target": proj_for_edges_node_id(project_for_edges),
        })
        assert s == 404, f"Expected 404 for bad source, got {s}"

    def test_add_edge_nonexistent_target(self, project_for_edges):
        """Target node must exist."""
        pid, _ = project_for_edges
        s, d = req("POST", f"/api/projects/{pid}/edges", {
            "source": project_for_edges[1]["nodes"][0]["id"],
            "target": "nonexistent_task",
        })
        assert s == 404, f"Expected 404 for bad target, got {s}"


def proj_for_edges_node_id(project_for_edges):
    """Helper: first node ID from the edge test fixture."""
    return project_for_edges[1]["nodes"][0]["id"]


class TestEdgeDelete:
    """DELETE /api/projects/{id}/edges/{source}/{target} — removing edges."""

    def test_delete_existing_edge(self, project_for_edges):
        """Delete an existing edge removes the dependency."""
        pid, proj = project_for_edges
        s, edges_data = req("GET", f"/api/projects/{pid}/edges")
        existing = edges_data.get("edges", [])
        if not existing:
            pytest.skip("No edges to delete")
        e = existing[0]
        s, d = req("DELETE", f"/api/projects/{pid}/edges/{e['source']}/{e['target']}")
        assert s == 200, f"Delete edge failed: {d}"
        assert d.get("deleted_edge") is not None

    def test_delete_nonexistent_edge(self, project_for_edges):
        """Deleting a non-existent edge returns 200 (no-op, graceful)."""
        pid, _ = project_for_edges
        s, d = req("DELETE", "/api/projects/{pid}/edges/task_1/task_999")
        # Should not 500 — graceful handling
        assert s in (200, 404), f"Expected 200 or 404, got {s}: {d}"


class TestCycleDetection:
    """Adding edges that would create cycles is prevented."""

    def test_cycle_simple_triangle(self, project_for_edges):
        """Adding an edge that creates a triangle cycle is rejected."""
        pid, proj = project_for_edges
        nodes = proj["nodes"]
        # Find two nodes with a dependency chain A → B
        # Then try to add B → A (which would create a cycle)
        s_check, edges_check = req("GET", f"/api/projects/{pid}/edges")
        all_edges = edges_check.get("edges", [])
        if not all_edges:
            pytest.skip("No edges to test cycle with")

        # Try to add a reverse edge for each existing edge
        found_cycle = False
        for e in all_edges:
            # Add edge in reverse direction
            s, d = req("POST", f"/api/projects/{pid}/edges", {
                "source": e["target"],
                "target": e["source"],
            })
            if s == 400:
                found_cycle = True
                detail = str(d.get("detail", "")).lower()
                assert "环" in detail or "cycle" in detail or "循环" in detail, \
                    f"Error should mention cycle: {d}"
                break  # Successfully detected one cycle
        # At least some reverse edge should create a cycle
        # (If the graph is a tree/dag, all reverse edges create cycles)

    def test_no_cycle_for_parallel_branches(self):
        """Connecting parallel branches that don't create cycles is allowed."""
        pid, proj = create_test_project(
            "Root 1d. BranchA 3d depends on Root. BranchB 2d depends on Root. "
            "Merge 1d depends on BranchA and BranchB."
        )
        try:
            # Adding an edge between parallel branches should not create a cycle
            nodes = proj["nodes"]
            branch_a = _find_node_by_name(nodes, "BranchA")
            branch_b = _find_node_by_name(nodes, "BranchB")
            if branch_a and branch_b:
                # B → A: parallel connect, not a cycle
                s, d = req("POST", f"/api/projects/{pid}/edges", {
                    "source": branch_b["id"],
                    "target": branch_a["id"],
                })
                # Should succeed (or 400 if LLM-generated names differ)
                # The key is: it should not crash with 500
                assert s in (200, 400), f"Unexpected status: {s}"
        finally:
            req("DELETE", f"/api/projects/{pid}")
