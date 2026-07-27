# -*- coding: utf-8 -*-
"""Shared test helpers — importable utility functions for all test files.

Pytest automatically discovers conftest.py for fixtures, but conftest
is not directly importable. This module provides the shared functions
that test files need to import explicitly.
"""

import json
import os
import sys as _sys
import urllib.request
import urllib.error

# 从 config.json 读取测试配置
try:
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from config import get_config
    _test_cfg = get_config().test
    BASE = _test_cfg.base_url
    _TIMEOUT = _test_cfg.timeout_seconds
except Exception:
    BASE = "http://127.0.0.1:48090"
    _TIMEOUT = 120


# ═══════════════════════════════════════════════════════════════════════
# HTTP helper
# ═══════════════════════════════════════════════════════════════════════

def req(method, path, body=None):
    """Make an HTTP request to the test server.

    Returns (status_code, response_dict).
    """
    # URL encode the path to handle spaces and special chars
    from urllib.parse import quote
    url = BASE + quote(path, safe="/?:=&")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json; charset=utf-8")
    r.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"_raw": raw}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8") if e.fp else ""
        return e.code, {"error": body_text}
    except Exception as e:
        return 0, {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════
# Project fixture factory (for use outside pytest fixtures)
# ═══════════════════════════════════════════════════════════════════════

def create_test_project(description, deadline="", additional_info="", file_text=None):
    """Create a project via API and return (project_id, project_dict).

    Skips the test if LLM is unavailable or cannot parse the description.
    """
    body = {"description": description, "deadline": deadline,
            "additional_info": additional_info}
    if file_text:
        body["file_text"] = file_text
    s, d = req("POST", "/api/projects", body)
    if s != 200:
        import pytest
        detail = d.get("error", str(d))
        if "LLM" in detail or "解析" in detail or "parse" in detail.lower():
            pytest.skip(f"LLM cannot parse description: {detail[:100]}")
        pytest.fail(f"create_test_project failed ({s}): {detail[:200]}")
    proj = d.get("project", {})
    pid = proj.get("id", "")
    assert len(pid) == 8 and pid.isalnum(), f"Invalid project id: {pid}"
    return pid, proj


# ═══════════════════════════════════════════════════════════════════════
# Assertion helpers
# ═══════════════════════════════════════════════════════════════════════

def assert_valid_graph(graph):
    """Assert graph response has correct structure for DAG rendering."""
    assert "nodes" in graph, "Graph missing 'nodes'"
    assert "edges" in graph, "Graph missing 'edges'"
    assert "critical_path" in graph, "Graph missing 'critical_path'"
    assert "risks" in graph, "Graph missing 'risks'"
    assert "buffer" in graph, "Graph missing 'buffer'"
    node_ids = set()
    for gn in graph["nodes"]:
        for f in ["id", "name", "progress", "status", "is_critical"]:
            assert f in gn, f"Graph node missing '{f}'"
        node_ids.add(gn["id"])
    for ge in graph["edges"]:
        assert "source" in ge and "target" in ge
        assert ge["source"] in node_ids, f"Edge source '{ge['source']}' not in nodes"
        assert ge["target"] in node_ids, f"Edge target '{ge['target']}' not in nodes"
    for nid in graph.get("critical_path", []):
        assert nid in node_ids, f"CP node '{nid}' not in graph nodes"


def assert_valid_schedule(schedule):
    """Assert schedule result has required fields."""
    for f in ["topological_order", "critical_path", "total_duration_days",
              "project_buffer_days"]:
        assert f in schedule, f"Schedule missing '{f}'"


def assert_valid_buffer(buffer):
    """Assert buffer info has required fields and valid status."""
    for f in ["total_days", "consumed_days", "remaining_days", "ratio", "status"]:
        assert f in buffer, f"Buffer missing '{f}'"
    assert buffer["status"] in ("green", "yellow", "red"), \
        f"Invalid buffer status: {buffer['status']}"


def assert_valid_risk(risk):
    """Assert a risk dict has required fields."""
    for f in ["risk_id", "level", "dimension", "message"]:
        assert f in risk, f"Risk missing '{f}'"
    assert risk["level"] in ("critical", "warning", "info"), \
        f"Invalid risk level: {risk['level']}"


def assert_topo_order_valid(nodes, topo_order):
    """Assert topological order: predecessors appear before successors."""
    pos = {tid: i for i, tid in enumerate(topo_order)}
    for n in nodes:
        for pre in n.get("pre_dependencies", []):
            if pre in pos and n["id"] in pos:
                assert pos[pre] < pos[n["id"]], \
                    f"Topo order violated: {pre} after {n['id']}"


# ═══════════════════════════════════════════════════════════════════════
# WebSocket helpers
# ═══════════════════════════════════════════════════════════════════════

try:
    import websockets.sync.client as ws_sync
    _HAS_WS = True
except ImportError:
    _HAS_WS = False


def ws_connect(project_id, timeout=5):
    """Connect to project WebSocket. Returns client or None."""
    if not _HAS_WS:
        return None
    try:
        return ws_sync.connect(
            f"ws://127.0.0.1:48090/ws/projects/{project_id}",
            open_timeout=timeout
        )
    except Exception:
        return None


def ws_receive_json(ws, timeout=5):
    """Receive one JSON message from WebSocket."""
    if ws is None:
        return None
    try:
        return json.loads(ws.recv(timeout=timeout))
    except Exception:
        return None


def ws_send(ws, text):
    """Send text via WebSocket."""
    if ws:
        try:
            ws.send(text)
        except Exception:
            pass


def requires_websockets():
    """Raise pytest.skip if websockets library unavailable."""
    if not _HAS_WS:
        import pytest
        pytest.skip("websockets library not installed (pip install websockets)")
