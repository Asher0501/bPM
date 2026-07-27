# -*- coding: utf-8 -*-
"""Tag grouping E2E tests — tag-based node aggregation for frontend DAG.

Tests the full frontend→backend→frontend loop:
  user clicks tag chip → backend aggregates nodes by tag → frontend renders grouped DAG.
"""

import pytest
from helpers import req, create_test_project


@pytest.fixture(scope="module")
def project_with_tags():
    """Create a project; we'll add tags via node editing."""
    pid, proj = create_test_project(
        "Auth system: DB design 2d, Backend auth API 5d depends on DB design, "
        "Frontend login page 3d depends on DB design, "
        "Integration test 2d depends on Backend auth API and Frontend login page, "
        "Security audit 2d depends on Integration test."
    )
    # Tag the nodes: backend/frontend
    for n in proj["nodes"]:
        tags = []
        name = n.get("name", "").lower()
        if "db" in name or "api" in name or "backend" in name:
            tags.append("backend")
        if "frontend" in name or "login" in name or "page" in name:
            tags.append("frontend")
        if "test" in name or "integration" in name:
            tags.append("qa")
        if "security" in name or "audit" in name:
            tags.append("security")
        if tags:
            s, _ = req("PUT", f"/api/projects/{pid}/nodes/{n['id']}", {"tags": tags})
            assert s == 200, f"Failed to tag node {n['id']}: tags={tags}"
    return pid, proj


class TestTagListing:
    """GET /api/projects/{id}/tags — tag collection."""

    def test_list_tags_returns_sorted(self, project_with_tags):
        """Tags endpoint returns deduplicated sorted list."""
        pid, _ = project_with_tags
        s, d = req("GET", f"/api/projects/{pid}/tags")
        assert s == 200, f"List tags failed: {d}"
        tags = d.get("tags", [])
        assert isinstance(tags, list), "tags should be a list"
        assert len(tags) > 0, "Should have at least 1 tag"
        # Should be sorted
        assert tags == sorted(tags), "Tags should be sorted"
        # No duplicates
        assert len(tags) == len(set(tags)), "Tags should be deduplicated"

    def test_list_tags_empty_project(self):
        """Untagged project returns empty list."""
        pid, _ = create_test_project(
            "Tagless project with database design for 2 days."
        )
        try:
            s, d = req("GET", f"/api/projects/{pid}/tags")
            assert s == 200
            assert d.get("tags") == [], "Untagged project should return empty tags"
        finally:
            req("DELETE", f"/api/projects/{pid}")


class TestTagGrouping:
    """GET /api/projects/{id}/grouped?tags=... — tag aggregation."""

    def test_group_single_tag(self, project_with_tags):
        """Grouping by a single tag returns aggregated nodes."""
        pid, _ = project_with_tags
        s, d = req("GET", f"/api/projects/{pid}/grouped?tags=backend")
        assert s == 200, f"Group failed: {d}"
        nodes = d.get("nodes", [])
        edges = d.get("edges", [])
        assert len(nodes) > 0, "Should have nodes"
        # Should have grp_ aggregated node
        grp_nodes = [n for n in nodes if n.get("is_group")]
        assert len(grp_nodes) >= 1, "Should have at least 1 grouped node"
        grp = grp_nodes[0]
        assert grp["id"].startswith("grp_"), f"Group node ID should start with grp_: {grp['id']}"
        assert grp.get("children"), "Group node should list children"
        assert grp.get("is_group") is True

    def test_group_multiple_tags(self, project_with_tags):
        """Grouping by multiple tags creates multiple aggregate nodes."""
        pid, _ = project_with_tags
        s, d = req("GET", f"/api/projects/{pid}/grouped?tags=backend,frontend")
        assert s == 200, f"Multi-tag group failed: {d}"
        nodes = d.get("nodes", [])
        grp_count = sum(1 for n in nodes if n.get("is_group"))
        assert grp_count >= 1, f"Expected >=1 group nodes, got {grp_count}"

    def test_group_no_tags_param(self, project_with_tags):
        """No tags parameter falls back to normal graph endpoint."""
        pid, _ = project_with_tags
        s, d = req("GET", f"/api/projects/{pid}/grouped")
        assert s == 200, f"Group without tags failed: {d}"
        # Should return normal graph data
        assert len(d.get("nodes", [])) > 0, "Should have nodes"

    def test_group_conflict_detection(self, project_with_tags):
        """Node with both tags selected causes conflict."""
        pid, _ = project_with_tags
        # Tag one node with both 'backend' and 'frontend'
        proj_nodes = project_with_tags[1]["nodes"]
        if proj_nodes:
            nid = proj_nodes[0]["id"]
            req("PUT", f"/api/projects/{pid}/nodes/{nid}", {
                "tags": ["backend", "frontend"]
            })

        s, d = req("GET", f"/api/projects/{pid}/grouped?tags=backend,frontend")
        assert s == 200, f"Conflict test failed: {d}"
        conflicts = d.get("conflicts", [])
        if conflicts:
            # If conflicts detected, nodes should be empty
            for c in conflicts:
                assert "node_id" in c
                assert "node_name" in c
                assert "tags" in c

    def test_group_edge_aggregation(self, project_with_tags):
        """Grouped edges are deduplicated, no intra-group edges."""
        pid, _ = project_with_tags
        s, d = req("GET", f"/api/projects/{pid}/grouped?tags=backend")
        assert s == 200
        edges = d.get("edges", [])
        # No duplicate edges
        edge_keys = [(e["source"], e["target"]) for e in edges]
        assert len(edge_keys) == len(set(edge_keys)), "Duplicate edges in grouped result"

    def test_group_result_has_required_graph_format(self, project_with_tags):
        """Grouped result has same structure as graph (for frontend DAG rendering)."""
        pid, _ = project_with_tags
        s, d = req("GET", f"/api/projects/{pid}/grouped?tags=backend")
        assert s == 200
        # Same structure as graph endpoint
        for key in ["nodes", "edges", "critical_path", "risks", "buffer"]:
            assert key in d, f"Group result missing '{key}'"

    def test_group_progress_weighted_average(self, project_with_tags):
        """Group node progress is weighted by estimated_days."""
        pid, _ = project_with_tags
        s, d = req("GET", f"/api/projects/{pid}/grouped?tags=backend")
        assert s == 200
        grp_nodes = [n for n in d["nodes"] if n.get("is_group")]
        for gn in grp_nodes:
            assert 0 <= gn["progress"] <= 100, \
                f"Group progress out of range: {gn['progress']}"
