# -*- coding: utf-8 -*-
"""Node CRUD end-to-end tests (pytest version)"""

import json
import urllib.request
import urllib.error
import pytest

BASE = "http://127.0.0.1:48090"


def req(m, p, b=None):
    d = json.dumps(b, ensure_ascii=False).encode("utf-8") if b else None
    r = urllib.request.Request(BASE + p, data=d, method=m)
    r.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8") if e.fp else str(e)}


@pytest.fixture(scope="module")
def project_for_nodes():
    """Create a project for node CRUD tests."""
    s, d = req("POST", "/api/projects", {
        "description": "DB design 2d. API dev 5d depends on DB. Frontend 3d depends on DB. Test 2d depends on API and Frontend."
    })
    assert s == 200, f"Create failed: {s}"
    pid = d["project"]["id"]
    nodes = d["project"]["nodes"]
    orig_count = len(nodes)
    assert orig_count >= 3, f"Expected >=3 nodes, got {orig_count}"
    return pid, nodes


class TestNodeCRUD:

    def test_1_add_node_nl(self, project_for_nodes, needs_llm):
        """Add a node via natural language"""
        pid, nodes = project_for_nodes
        orig_count = len(nodes)
        s, d = req("POST", f"/api/projects/{pid}/nodes", {
            "description": "Performance testing 2d, depends on API dev, by QA"
        })
        assert s == 200, f"NL add failed: {s}"
        new_count = len(d["project"]["nodes"])
        assert new_count == orig_count + 1, f"Expected {orig_count+1} nodes, got {new_count}"
        assert d.get("new_node_id"), "No new_node_id returned"

    def test_2_add_node_manual(self, project_for_nodes):
        """Add a node via manual mode"""
        pid, nodes = project_for_nodes
        orig_count = len(nodes)
        first_id = nodes[0]["id"]
        s, d = req("POST", f"/api/projects/{pid}/nodes", {
            "name": "Security review",
            "estimated_days": 2,
            "pre_dependencies": [first_id],
        })
        assert s == 200, f"Manual add failed: {s}"
        new_count = len(d["project"]["nodes"])
        assert new_count == orig_count + 1, f"Expected {orig_count+1} nodes, got {new_count}"

    def test_3_edit_node(self, project_for_nodes):
        """Edit an existing node's properties"""
        pid, nodes = project_for_nodes
        # Use the first node from the original project
        nid = nodes[0]["id"]
        s, d = req("PUT", f"/api/projects/{pid}/nodes/{nid}", {
            "name": "Updated Node Name",
            "estimated_days": 5,
            "confidence": 0.5,
            "resources": ["QA", "DevOps"],
        })
        assert s == 200, f"Edit failed: {s}"
        edited = [n for n in d["project"]["nodes"] if n["id"] == nid]
        assert len(edited) == 1, f"Node {nid} not found after edit"
        edited = edited[0]
        assert edited["name"] == "Updated Node Name", "Name not updated"
        assert edited["estimated_days"] == 5, "Days not updated"
        assert len(edited["resources"]) == 2, "Resources not updated"

    def test_4_edit_dependencies(self, project_for_nodes):
        """Edit node dependencies"""
        pid, nodes = project_for_nodes
        nid = nodes[0]["id"]
        s, d = req("PUT", f"/api/projects/{pid}/nodes/{nid}", {
            "pre_dependencies": ["task_1", "task_3"]
        })
        assert s == 200, f"Edit deps failed: {s}"
        edited = [n for n in d["project"]["nodes"] if n["id"] == nid][0]
        assert len(edited["pre_dependencies"]) == 2, "Deps not updated"

    def test_5_delete_with_downstream(self, project_for_nodes):
        """Delete a node that has downstream dependents"""
        pid, nodes = project_for_nodes
        # Get updated project
        s, d = req("GET", f"/api/projects/{pid}")
        assert s == 200
        proj_data = d["project"]
        all_nodes = proj_data["nodes"]

        # Find node with downstream dependents
        target = None
        for n in all_nodes:
            downstream = [x for x in all_nodes if n["id"] in x.get("pre_dependencies", [])]
            if downstream:
                target = n["id"]
                break
        if not target and all_nodes:
            target = all_nodes[0]["id"]

        s, d = req("DELETE", f"/api/projects/{pid}/nodes/{target}")
        assert s == 200, f"Delete failed: {s}"
        assert "deleted_name" in d, "Missing deleted_name"
        if d.get("affected_count", 0) > 0:
            assert d["affected_count"] > 0, "Should have affected nodes"

        # Verify downstream deps cleaned
        for n in d.get("project", {}).get("nodes", []):
            assert target not in n.get("pre_dependencies", []), f"Node {n['id']} still has dep on {target}"

    def test_6_delete_without_downstream(self, project_for_nodes):
        """Delete a node with no downstream dependents"""
        pid, nodes = project_for_nodes
        s, d = req("GET", f"/api/projects/{pid}")
        assert s == 200
        all_nodes = d["project"]["nodes"]
        orig_count = len(all_nodes)

        if not all_nodes:
            pytest.skip("No nodes to delete")
        last_id = all_nodes[-1]["id"]

        s, d = req("DELETE", f"/api/projects/{pid}/nodes/{last_id}")
        assert s == 200, f"Delete failed: {s}"
        new_count = len(d.get("project", {}).get("nodes", []))
        assert new_count == orig_count - 1, f"Expected {orig_count-1} nodes, got {new_count}"

    def test_7_re_schedule_integrity(self, project_for_nodes):
        """Re-schedule after all changes maintains integrity"""
        pid, _ = project_for_nodes
        s, d = req("POST", f"/api/projects/{pid}/schedule")
        assert s == 200, f"Re-schedule failed: {s}"
        proj = d["project"]
        assert len(proj["schedule"]["critical_path"]) > 0, "No critical path"
        assert "risks" in proj, "No risks after re-schedule"

    def test_8_cleanup(self, project_for_nodes):
        """Delete the test project"""
        pid, _ = project_for_nodes
        s, d = req("DELETE", f"/api/projects/{pid}")
        assert s == 200, f"Delete project failed: {s}"
