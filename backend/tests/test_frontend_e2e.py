# -*- coding: utf-8 -*-
"""前端视角端到端测试 — 模拟浏览器用户操作流程 (pytest version)"""

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
def project_for_frontend():
    """Create a project for frontend tests."""
    desc = (
        "Build a user login system. Tasks: "
        "Database design (2 days, by backend), "
        "API design (1 day, by backend, depends on DB design), "
        "Backend auth API (5 days, depends on API design), "
        "Frontend login page (3 days, depends on API design), "
        "Integration test (2 days, depends on Backend and Frontend both done), "
        "Deploy (1 day, depends on integration test). "
        "Deadline: 2026-10-31."
    )
    file_content = "Extra: Security audit, 2 days, before deploy, after integration test."
    s, d = req("POST", "/api/projects", {
        "description": desc,
        "deadline": "2026-10-31",
        "additional_info": "Team of 2 backend, 1 frontend",
        "file_text": file_content,
    })
    assert s == 200, f"Project creation failed: {s}"
    proj = d.get("project", {})
    pid = proj.get("id", "")
    assert len(pid) == 8, f"Invalid project id: {pid}"
    return pid, proj


class TestFrontendE2E:

    def test_1_html_page(self):
        """HTML page loads with all required elements"""
        with urllib.request.urlopen(urllib.request.Request(BASE + "/")) as resp:
            html = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "bePm" in html
        assert 'id="input-description"' in html
        assert 'id="input-deadline"' in html
        assert 'id="input-file"' in html
        assert "createSchedule()" in html
        assert "updateProgress()" in html
        assert 'id="cy-container"' in html
        assert 'id="risk-list"' in html
        assert 'id="stats-content"' in html
        assert 'id="buffer-content"' in html
        assert 'id="project-list"' in html
        assert "cytoscape@3" in html
        assert "dagre@0.8.5" in html
        assert "cytoscape-dagre@2" in html

    def test_2_graph_data(self, project_for_frontend):
        """Graph endpoint returns data for DAG rendering"""
        pid, _ = project_for_frontend
        s, graph = req("GET", f"/api/projects/{pid}/graph")
        assert s == 200, f"Graph failed: {s}"
        assert len(graph.get("nodes", [])) > 0
        assert len(graph.get("edges", [])) > 0
        assert len(graph.get("critical_path", [])) > 0
        assert "risks" in graph
        assert "buffer" in graph

        # Each node has required fields for frontend
        for i, gn in enumerate(graph["nodes"]):
            for field in ["id", "name", "progress", "status", "is_critical", "estimated_days"]:
                assert field in gn, f"graph node[{i}] missing '{field}'"

        # Edge format
        for i, ge in enumerate(graph["edges"]):
            assert "source" in ge and "target" in ge, f"graph edge[{i}] missing source/target"

    def test_3_project_panels(self, project_for_frontend):
        """Stats, risk, and buffer panels have valid data"""
        pid, proj = project_for_frontend
        nodes = proj["nodes"]
        schedule = proj.get("schedule", {})
        buffer = proj.get("buffer", {})
        risks = proj.get("risks", [])

        assert all("status" in n for n in nodes), "Nodes missing status"
        assert "total_duration_days" in schedule, "Schedule missing total_duration_days"
        assert isinstance(schedule.get("critical_path"), list), "Invalid critical_path type"

        # Risk panel
        if risks:
            r = risks[0]
            assert "level" in r
            assert "dimension" in r
            assert "message" in r

        # Buffer panel
        assert "total_days" in buffer
        assert "consumed_days" in buffer
        assert "status" in buffer
        assert buffer.get("status") in ("green", "yellow", "red")

    def test_4_progress_update(self, project_for_frontend, needs_llm):
        """Progress update processes NL input and returns results"""
        pid, proj = project_for_frontend
        nodes = proj["nodes"]
        first_node = nodes[0]
        progress_text = (
            f"{first_node['name']} is completed. "
            "Backend auth API is at 40%, slower than expected (1 day behind). "
            "One frontend developer is on leave for 3 days."
        )
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": progress_text,
        })
        assert s == 200, f"Progress update failed: {s}"
        assert len(d.get("updated_nodes", [])) > 0, "No nodes updated"

    def test_5_dag_update_after_progress(self, project_for_frontend, needs_llm):
        """After progress update, DAG nodes reflect status changes"""
        pid, proj = project_for_frontend
        nodes = proj["nodes"]
        first_node = nodes[0]
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": f"{first_node['name']} is completed."
        })
        assert s == 200
        updated = d.get("project", {})
        unodes = updated.get("nodes", [])
        completed_nodes = [n for n in unodes if n["status"] == "completed"]
        in_progress_nodes = [n for n in unodes if n["status"] == "in_progress"]
        assert len(completed_nodes) >= 1, "No completed nodes"
        assert len(in_progress_nodes) >= 1, "No in-progress nodes"

    def test_6_risk_panel_after_progress(self, project_for_frontend, needs_llm):
        """Risk panel refreshes after progress update"""
        pid, proj = project_for_frontend
        first_node = proj["nodes"][0]
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": (
                f"{first_node['name']} completed. "
                "Backend is 1 day behind schedule."
            )
        })
        assert s == 200
        updated_risks = d.get("new_risks", [])
        # Risk signals should contain delay-related terms
        all_msgs = " ".join(r.get("message", "") for r in updated_risks)
        assert len(updated_risks) > 0 or "delay" in all_msgs.lower(), "No risks after delayed progress"

    def test_7_buffer_gauge(self, project_for_frontend, needs_llm):
        """Buffer gauge values remain valid after update"""
        pid, proj = project_for_frontend
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": "Everything is on track."
        })
        assert s == 200
        updated_buffer = d.get("project", {}).get("buffer", {})
        assert updated_buffer.get("total_days", 0) > 0, "Buffer total_days == 0"
        assert updated_buffer.get("status") in ("green", "yellow", "red")

    def test_8_project_list(self, project_for_frontend):
        """Project list shows our project with metadata"""
        pid, _ = project_for_frontend
        s, d = req("GET", "/api/projects")
        assert s == 200
        proj_list = d.get("projects", [])
        our_proj = [p for p in proj_list if p["id"] == pid]
        assert len(our_proj) == 1, "Our project not in list"
        for field in ["node_count", "risk_count", "critical_risk_count"]:
            assert field in our_proj[0], f"List item missing {field}"

    def test_9_dag_topology_integrity(self, project_for_frontend):
        """DAG graph has valid edges and critical path"""
        pid, _ = project_for_frontend
        s, graph = req("GET", f"/api/projects/{pid}/graph")
        assert s == 200
        gnodes = graph["nodes"]
        gedges = graph["edges"]
        node_ids = set(n["id"] for n in gnodes)

        for e in gedges:
            assert e["source"] in node_ids, f"Edge source {e['source']} not in nodes"
            assert e["target"] in node_ids, f"Edge target {e['target']} not in nodes"

        gcp = set(graph["critical_path"])
        if gcp:
            assert all(nid in node_ids for nid in gcp), "Critical path node missing from graph"

    def test_10_file_upload_encoding(self, project_for_frontend):
        """File upload and additional_info preserved in raw_input"""
        _, proj = project_for_frontend
        raw = proj.get("raw_input", "")
        assert "Security audit" in raw, "file content missing from raw_input"
        assert "2 backend" in raw, "additional_info missing from raw_input"
        assert "login" in raw.lower(), "description missing from raw_input"

    def test_11_error_handling(self, project_for_frontend):
        """Error cases return appropriate status codes"""
        pid, _ = project_for_frontend
        s, d = req("GET", "/api/projects/ffffffff/graph")
        assert s == 404, f"Bad project graph expected 404, got {s}"

    def test_12_cleanup(self, project_for_frontend):
        """Delete project at end of test"""
        pid, _ = project_for_frontend
        s, d = req("DELETE", f"/api/projects/{pid}")
        assert s == 200, f"Delete failed: {s}"

        s, d = req("GET", f"/api/projects/{pid}")
        assert s == 404, f"Deleted project still accessible: {s}"
