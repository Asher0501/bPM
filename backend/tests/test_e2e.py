# -*- coding: utf-8 -*-
"""bePm 端到端测试 — 覆盖所有功能路径 (pytest version)"""

import json
import urllib.request
import urllib.error
import pytest

BASE = "http://127.0.0.1:48090"


def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json; charset=utf-8")
    r.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8") if e.fp else ""
        return e.code, {"error": body_text}


@pytest.fixture(scope="module")
def created_project():
    """Fixture: create a project and return (pid, project_data, nodes)."""
    desc = (
        "We need to develop an e-commerce platform, must launch before 2026-12-31.\n"
        "Tasks:\n"
        "1. Requirements analysis & prototyping, 5 days, by product manager\n"
        "2. Database design, 3 days, depends on requirements analysis\n"
        "3. API design, 2 days, depends on requirements analysis\n"
        "4. Backend API development, 20 days, depends on database design AND API design\n"
        "5. Frontend development, 15 days, depends on API design\n"
        "6. Integration testing, 5 days, depends on backend AND frontend both done\n"
        "7. Deployment, 1 day, depends on integration testing passed"
    )
    deadline = "2026-12-31"
    s, d = req("POST", "/api/projects", {"description": desc, "deadline": deadline})
    assert s == 200, f"Project creation failed: {d}"
    proj = d.get("project", {})
    pid = proj.get("id", "")
    assert len(pid) == 8 and pid.isalnum(), f"Invalid project id: {pid}"
    return pid, proj, proj.get("nodes", [])


class TestBePmE2E:

    def test_1_health_check(self):
        """Health check endpoint returns 200 with utf-8 encoding"""
        s, d = req("GET", "/api/health")
        assert s == 200, f"Health check failed: {s}"
        assert d.get("encoding") == "utf-8"

    def test_2_create_project(self, created_project):
        """Project creation returns valid data"""
        pid, proj, nodes = created_project
        assert len(proj.get("name", "")) > 0, "Project name is empty"
        assert len(nodes) >= 5, f"Expected >=5 nodes, got {len(nodes)}"
        assert proj.get("schedule") is not None, "Missing schedule"
        cp = proj.get("schedule", {}).get("critical_path", [])
        assert len(cp) > 0, "Missing critical path"
        assert proj.get("buffer", {}).get("total_days", 0) > 0, "Buffer days == 0"
        assert proj.get("deadline") == "2026-12-31", "Deadline mismatch"
        assert len(proj.get("edges", [])) > 0, "No edges"
        node_with_times = [n for n in nodes if n.get("es") is not None]
        assert len(node_with_times) > 0, "Nodes lack es/ef/ls/lf"

    def test_3_file_upload(self):
        """File upload via file_text field"""
        file_content = "Additional task: Performance testing, 3 days, between integration testing and deployment"
        s, d = req("POST", "/api/projects", {
            "description": "Build an internal OA system with employee management, approval workflow, reports",
            "deadline": "2026-11-30",
            "file_text": file_content,
            "additional_info": "Team has 3 backend devs and 2 frontend devs",
        })
        assert s == 200, f"File upload failed: {s}"
        raw = d.get("project", {}).get("raw_input", "")
        assert "Performance testing" in raw, "file_text not in raw_input"
        assert "3 backend" in raw, "additional_info not in raw_input"

    def test_4_get_project(self, created_project):
        """Get project detail returns matching data"""
        pid, proj, _ = created_project
        s, d = req("GET", f"/api/projects/{pid}")
        assert s == 200, f"Get project failed: {s}"
        assert d.get("project", {}).get("name") == proj.get("name"), "Name mismatch"

    def test_5_get_graph(self, created_project):
        """Graph endpoint returns DAG data"""
        pid, _, _ = created_project
        s, d = req("GET", f"/api/projects/{pid}/graph")
        assert s == 200, f"Graph failed: {s}"
        assert len(d.get("nodes", [])) > 0, "No graph nodes"
        assert len(d.get("edges", [])) > 0, "No graph edges"
        assert len(d.get("critical_path", [])) > 0, "No critical path"
        gnode = d["nodes"][0] if d.get("nodes") else {}
        for field in ["name", "status", "progress", "is_critical"]:
            assert field in gnode, f"Graph node missing {field}"

    def test_6_risk_analysis(self, created_project):
        """Risk analysis produces multi-dimensional risks"""
        _, proj, _ = created_project
        risks = proj.get("risks", [])
        assert len(risks) > 0, "No risks detected"
        levels = set(r["level"] for r in risks)
        has_critical_or_warning = "critical" in levels or "warning" in levels
        assert has_critical_or_warning, f"No critical/warning risks: {levels}"
        dimensions = set(r["dimension"] for r in risks)
        assert len(dimensions) >= 2, f"Not enough risk dimensions: {dimensions}"

    def test_7_progress_update(self, created_project, needs_llm):
        """Progress update processes NL input and updates nodes"""
        pid, _, nodes = created_project
        first_task = nodes[0]
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": (
                f"{first_task['name']} is 100% completed. "
                "Database design is at 70%, a bit slower than expected (1 day behind). "
                "John is on leave for 2 days so frontend work may be impacted."
            )
        })
        assert s == 200, f"Progress update failed: {s}"
        assert len(d.get("updated_nodes", [])) > 0, "No nodes updated"
        updated_proj = d.get("project", {})
        has_completed = any(n["status"] == "completed" for n in updated_proj.get("nodes", []))
        assert has_completed, "No node marked completed"
        assert len(d.get("new_risks", [])) > 0, "No new risks generated"

    def test_8_project_list(self, created_project):
        """Project list returns index data"""
        s, d = req("GET", "/api/projects")
        assert s == 200, f"List failed: {s}"
        proj_list = d.get("projects", [])
        assert len(proj_list) >= 1, "No projects in list"
        first = proj_list[0]
        for field in ["node_count", "risk_count", "critical_risk_count"]:
            assert field in first, f"List item missing {field}"

    def test_9_re_schedule(self, created_project):
        """Re-schedule preserves critical path"""
        pid, _, _ = created_project
        s, d = req("POST", f"/api/projects/{pid}/schedule")
        assert s == 200, f"Re-schedule failed: {s}"
        cp = d.get("project", {}).get("schedule", {}).get("critical_path", [])
        assert len(cp) > 0, "Critical path missing after re-schedule"

    def test_10_edge_cases(self):
        """Edge cases: short description, non-existent project, delete"""
        # Too short description -> 400
        s, d = req("POST", "/api/projects", {"description": "x", "deadline": ""})
        assert s == 400, f"Expected 400 for short desc, got {s}"

        # Non-existent project -> 404
        s, d = req("GET", "/api/projects/nonexist12345")
        assert s == 404, f"Expected 404 for bad id, got {s}"

        # Create a temp project and delete it
        s, d = req("POST", "/api/projects", {
            "description": "Temp project with at least ten chars for testing deletion"
        })
        assert s == 200, f"Temp create failed: {s}"
        pid2 = d["project"]["id"]

        s, d = req("DELETE", f"/api/projects/{pid2}")
        assert s == 200, f"Delete failed: {s}"

        s, d = req("GET", f"/api/projects/{pid2}")
        assert s == 404, f"Deleted project still accessible, got {s}"

    def test_11_algorithm_validation(self, created_project, needs_llm):
        """Validate topological sort, critical path float, buffer integrity"""
        pid, proj, nodes = created_project

        # Get updated project after progress
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": "Requirements analysis is 100% completed. Everything else is in progress."
        })
        assert s == 200
        updated_proj = d.get("project", {})
        nodes_final = updated_proj.get("nodes", [])
        schedule_final = updated_proj.get("schedule", {})

        # Topological order: predecessors before successors
        topo_order = {tid: i for i, tid in enumerate(schedule_final.get("topological_order", []))}
        topo_ok = True
        for n in nodes_final:
            for pre in n.get("pre_dependencies", []):
                if pre in topo_order and n["id"] in topo_order:
                    if topo_order[pre] >= topo_order[n["id"]]:
                        topo_ok = False
                        break
        assert topo_ok, "Topological order violated"

        # Critical path: float ~0
        cp = set(schedule_final.get("critical_path", []))
        float_ok = all(
            n.get("float_days", 1) < 0.01 for n in nodes_final if n["id"] in cp
        )
        assert float_ok, "Critical path nodes have non-zero float"

        # Buffer integrity
        buffer = updated_proj.get("buffer", {})
        assert buffer.get("total_days", 0) > 0, "Buffer total_days == 0"
        assert buffer.get("status") in ("green", "yellow", "red"), f"Invalid buffer status: {buffer.get('status')}"

    def test_12_utf8_roundtrip(self, created_project):
        """UTF-8 encoding round-trip preserved"""
        _, proj, _ = created_project
        raw = proj.get("raw_input", "")
        assert "e-commerce" in raw.lower(), "raw_input lost content"
        assert "2026-12-31" in raw, "raw_input lost deadline"
