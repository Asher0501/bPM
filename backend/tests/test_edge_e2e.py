# -*- coding: utf-8 -*-
"""Edge CRUD end-to-end tests (pytest version)"""

import json
import urllib.request
import urllib.error
import pytest

BASE = "http://127.0.0.1:48090"


def req(m, p, b=None):
    d = json.dumps(b, ensure_ascii=False).encode() if b else None
    r = urllib.request.Request(BASE + p, data=d, method=m)
    r.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return e.code, {"error": body}


@pytest.fixture(scope="module")
def project_with_edges():
    """Create a project and return (pid, nodes_map)"""
    s, d = req("POST", "/api/projects", {
        "description": "DB design 2d. API dev 5d depends on DB. Frontend 3d depends on DB. Test 2d depends on API and Frontend."
    })
    assert s == 200, f"Create failed: {s}"
    pid = d["project"]["id"]
    nodes = {n["id"]: n["name"] for n in d["project"]["nodes"]}
    return pid, nodes


class TestEdgeCRUD:

    def test_1_list_edges(self, project_with_edges):
        """List edges returns initial dependencies"""
        pid, nodes = project_with_edges
        s, d = req("GET", f"/api/projects/{pid}/edges")
        assert s == 200, f"List edges failed: {s}"
        assert len(d["edges"]) > 0, "No initial edges from dependencies"

    def test_2_add_edge(self, project_with_edges):
        """Add a new dependency edge"""
        pid, nodes = project_with_edges
        # Find frontend and db nodes
        fe = [nid for nid, name in nodes.items() if "ront" in name.lower()]
        db = [nid for nid, name in nodes.items() if "db" in name.lower() or "atabase" in name.lower()]
        if not fe or not db:
            pytest.skip("Could not find frontend or db nodes")
        s, d = req("POST", f"/api/projects/{pid}/edges", {"source": db[0], "target": fe[0]})
        assert s == 200, f"Add edge failed: {s}"
        assert d.get("edge") is not None, "No edge in response"

    def test_3_delete_edge(self, project_with_edges):
        """Delete an existing edge"""
        pid, nodes = project_with_edges
        s, d = req("GET", f"/api/projects/{pid}/edges")
        assert s == 200
        if not d.get("edges"):
            pytest.skip("No edges to delete")
        edge = d["edges"][0]
        src, tgt = edge["source"], edge["target"]
        s, d = req("DELETE", f"/api/projects/{pid}/edges/{src}/{tgt}")
        assert s == 200, f"Delete edge failed: {s}"

    def test_4_nl_edge(self, project_with_edges, needs_llm):
        """Natural language edge creation via add_node"""
        pid, nodes = project_with_edges
        s, d = req("POST", f"/api/projects/{pid}/nodes", {
            "description": "let frontend depend on database design"
        })
        assert s == 200, f"NL edge failed: {s}"
        assert d.get("action") is not None, "No action in response"

    def test_5_cleanup(self, project_with_edges):
        """Delete the project"""
        pid, _ = project_with_edges
        s, d = req("DELETE", f"/api/projects/{pid}")
        assert s == 200, f"Delete project failed: {s}"
