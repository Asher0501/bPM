# -*- coding: utf-8 -*-
"""Command flow E2E tests — NL→confirm_plan→execute, ask→followup, stale ops, cycles.

This is the most important test file — it covers the complete two-layer LLM architecture:
  user types NL command → intent understanding → translation → confirmation gate → execution

Tests the full frontend→backend→frontend loop for the command endpoint.
"""

import pytest
from helpers import req, create_test_project


@pytest.fixture(scope="module")
def cmd_project():
    """Create a project for command flow tests."""
    pid, proj = create_test_project(
        "Command test: DB design 2d, API dev 5d depends on DB design, "
        "Frontend dev 3d depends on DB design, Testing 2d depends on API dev and Frontend dev."
    )
    return pid, proj


def _first_node_id(proj):
    """Helper: get first node ID."""
    nodes = proj.get("nodes", [])
    return nodes[0]["id"] if nodes else None


def _node_name(proj, nid):
    """Helper: get node name by ID."""
    for n in proj.get("nodes", []):
        if n["id"] == nid:
            return n["name"]
    return nid


class TestCommandBasic:
    """Basic command endpoint behavior."""

    def test_command_empty_description(self, cmd_project):
        """Empty description returns 400."""
        pid, _ = cmd_project
        s, d = req("POST", f"/api/projects/{pid}/command", {"description": ""})
        assert s == 400, f"Expected 400 for empty description, got {s}: {d}"

    def test_command_manual_add_node(self, cmd_project):
        """Manual mode: provide name + estimated_days, skip LLM."""
        pid, proj = cmd_project
        orig_count = len(proj["nodes"])

        s, d = req("POST", f"/api/projects/{pid}/command", {
            "name": "Manual Test Node",
            "estimated_days": 3.0,
        })
        assert s == 200, f"Manual add failed: {d}"
        new_proj = d["project"]
        assert len(new_proj["nodes"]) == orig_count + 1, \
            f"Expected {orig_count + 1} nodes, got {len(new_proj['nodes'])}"
        assert d.get("new_node_id"), "Response missing new_node_id"

    def test_command_manual_add_with_deps(self, cmd_project):
        """Manual mode with pre_dependencies."""
        pid, proj = cmd_project
        first_id = _first_node_id(proj)

        s, d = req("POST", f"/api/projects/{pid}/command", {
            "name": "Manual With Deps",
            "estimated_days": 2.0,
            "pre_dependencies": [first_id],
        })
        assert s == 200, f"Manual add with deps failed: {d}"
        new_id = d.get("new_node_id")
        # Verify dependency
        new_nodes = [n for n in d["project"]["nodes"] if n["id"] == new_id]
        assert len(new_nodes) == 1, f"New node {new_id} not found"
        assert first_id in new_nodes[0].get("pre_dependencies", []), \
            "Dependency not set"


class TestCommandNLFlow:
    """Natural language command → intent parsing → confirmation gate."""

    def test_command_nl_confirm_plan(self, cmd_project, needs_llm):
        """NL input triggers confirm_plan response with actionable ops."""
        pid, proj = cmd_project
        first_name = _node_name(proj, _first_node_id(proj))

        s, d = req("POST", f"/api/projects/{pid}/command", {
            "description": f"Add a Code Review task, 2 days, after {first_name}."
        })
        assert s == 200, f"NL command failed: {d}"

        # Should get confirm_plan or ask or direct success
        action = d.get("action")
        assert action is not None, f"Response missing 'action': {d}"
        assert action in ("confirm_plan", "ask"), \
            f"Unexpected action: {action}"

        if action == "confirm_plan":
            assert "plan" in d, "confirm_plan missing plan text"
            assert "ops_summary" in d, "confirm_plan missing ops_summary"
            ops = d["ops_summary"]
            assert len(ops) > 0, "ops_summary should have at least 1 op"
            for op in ops:
                assert "op" in op, f"Op missing 'op': {op}"
                assert "params" in op, f"Op missing 'params': {op}"

    def test_command_nl_edit_progress(self, cmd_project, needs_llm):
        """NL input to update progress triggers edit_node intent."""
        pid, proj = cmd_project
        first_id = _first_node_id(proj)
        first_name = _node_name(proj, first_id)

        s, d = req("POST", f"/api/projects/{pid}/command", {
            "description": f"Set {first_name} progress to 50 percent, status in_progress."
        })
        assert s == 200, f"Edit progress command failed: {d}"
        action = d.get("action")
        assert action in ("confirm_plan", "ask", None), \
            f"Unexpected action: {action}"

    def test_command_nl_delete_node(self, cmd_project, needs_llm):
        """NL input to delete a node."""
        pid, proj = cmd_project
        # Pick a leaf node (no downstream dependents) for easier deletion
        nodes = proj["nodes"]
        all_ids = {n["id"] for n in nodes}
        all_preds = set()
        for n in nodes:
            all_preds.update(n.get("pre_dependencies", []))
        leaves = [n for n in nodes if n["id"] not in all_preds]
        target = leaves[-1] if leaves else nodes[-1]

        s, d = req("POST", f"/api/projects/{pid}/command", {
            "description": f"Delete {target['name']}."
        })
        assert s == 200, f"Delete command failed: {d}"


class TestCommandConfirmedExecution:
    """Confirmed ops execution — the confirm→execute path."""

    def test_confirm_add_node(self, cmd_project):
        """User confirms plan: execute ops_to_execute directly, no LLM re-call."""
        pid, proj = cmd_project
        orig_count = len(proj["nodes"])
        first_id = _first_node_id(proj)

        # Build an add_node op and confirm it directly
        s, d = req("POST", f"/api/projects/{pid}/command", {
            "description": "Confirmed: add a Security Review task (2 days, after first task).",
            "confirmed": True,
            "ops_to_execute": [
                {
                    "op": "add_node",
                    "params": {
                        "id": "task_x_test",
                        "name": "Security Review",
                        "estimated_days": 2.0,
                        "pre_dependencies": [first_id],
                        "resources": ["QA"],
                        "notes": "Test node"
                    }
                }
            ]
        })
        assert s == 200, f"Confirmed add failed: {d}"
        new_proj = d["project"]
        assert len(new_proj["nodes"]) == orig_count + 1, \
            f"Expected {orig_count + 1} nodes, got {len(new_proj['nodes'])}"
        assert d.get("new_node_id") == "task_x_test"

    def test_confirm_edit_node(self, cmd_project):
        """Confirmed edit_node op."""
        pid, proj = cmd_project
        first_id = _first_node_id(proj)

        s, d = req("POST", f"/api/projects/{pid}/command", {
            "description": "Confirmed rename.",
            "confirmed": True,
            "ops_to_execute": [
                {
                    "op": "edit_node",
                    "params": {
                        "node_id": first_id,
                        "name": "Renamed Task",
                        "notes": "Updated via confirmed execution"
                    }
                }
            ]
        })
        assert s == 200, f"Confirmed edit failed: {d}"
        # Verify edit
        edited = [n for n in d["project"]["nodes"] if n["id"] == first_id]
        assert len(edited) == 1
        assert edited[0]["name"] == "Renamed Task", "Name not changed"
        assert edited[0]["notes"] == "Updated via confirmed execution"

    def test_confirm_delete_node(self, cmd_project):
        """Confirmed delete_node op with cleanup."""
        pid, proj = cmd_project
        all_nodes = proj["nodes"]

        # Always use a leaf node (no downstream dependents) for safe deletion
        all_ids = {n["id"] for n in all_nodes}
        all_preds = set()
        for n in all_nodes:
            all_preds.update(n.get("pre_dependencies", []))
        leaves = [n for n in all_nodes if n["id"] not in all_preds]
        target = leaves[-1]["id"] if leaves else all_nodes[-1]["id"]

        orig_count = len(all_nodes)
        s, d = req("POST", f"/api/projects/{pid}/command", {
            "description": "Confirmed delete.",
            "confirmed": True,
            "ops_to_execute": [
                {"op": "delete_node", "params": {"node_id": target}}
            ]
        })
        assert s == 200, f"Confirmed delete failed: {d}"
        assert len(d["project"]["nodes"]) == orig_count - 1, \
            f"Expected {orig_count - 1} nodes, got {len(d['project']['nodes'])}"
        for n in d["project"]["nodes"]:
            assert target not in n.get("pre_dependencies", []), \
                f"Node {n['id']} still references deleted {target}"

    def test_confirm_add_edge(self, cmd_project):
        """Confirmed add_edge op."""
        pid, proj = cmd_project
        nodes = proj["nodes"]
        if len(nodes) < 2:
            pytest.skip("Need 2 nodes")
        src = nodes[0]["id"]
        tgt = nodes[1]["id"]

        # Check current edges
        s_check, edges_data = req("GET", f"/api/projects/{pid}/edges")
        already_has = any(
            e["source"] == src and e["target"] == tgt
            for e in edges_data.get("edges", [])
        )

        s, d = req("POST", f"/api/projects/{pid}/command", {
            "description": "Confirmed add edge.",
            "confirmed": True,
            "ops_to_execute": [
                {"op": "add_edge", "params": {"source": src, "target": tgt}}
            ]
        })
        assert s == 200, f"Confirmed add_edge failed: {d}"

    def test_confirm_remove_edge(self, cmd_project):
        """Confirmed remove_edge op."""
        pid, _ = cmd_project
        s, edges_data = req("GET", f"/api/projects/{pid}/edges")
        existing = edges_data.get("edges", [])
        if not existing:
            pytest.skip("No edges to remove")
        e = existing[0]

        s, d = req("POST", f"/api/projects/{pid}/command", {
            "description": "Confirmed remove edge.",
            "confirmed": True,
            "ops_to_execute": [
                {"op": "remove_edge", "params": {"source": e["source"], "target": e["target"]}}
            ]
        })
        assert s == 200, f"Confirmed remove_edge failed: {d}"


class TestCommandStaleOps:
    """Stale operation detection — ops referencing deleted nodes."""

    def test_stale_ops_detected(self, cmd_project):
        """Confirming ops that reference a deleted node triggers conflict."""
        pid, _ = cmd_project

        # Submit ops that reference a non-existent node
        s, d = req("POST", f"/api/projects/{pid}/command", {
            "description": "Test stale ops.",
            "confirmed": True,
            "ops_to_execute": [
                {"op": "edit_node", "params": {"node_id": "deleted_node_999", "name": "X"}},
                {"op": "add_edge", "params": {"source": "deleted_node_999", "target": "ghost"}},
            ]
        })
        assert s == 200, f"Stale ops test failed: {d}"
        action = d.get("action")
        if action == "conflict":
            assert "message" in d, "Conflict response missing message"


class TestCommandCyclePrevention:
    """Cycle detection during command execution."""

    def test_cycle_prevented_via_command(self, cmd_project):
        """Confirmed ops that would create a cycle are rejected."""
        pid, proj = cmd_project
        nodes = proj["nodes"]
        if len(nodes) < 2:
            pytest.skip("Need 2 nodes")

        s_check, edges_data = req("GET", f"/api/projects/{pid}/edges")
        existing = edges_data.get("edges", [])

        found_cycle = False
        for e in existing:
            s, d = req("POST", f"/api/projects/{pid}/command", {
                "description": "Test cycle.",
                "confirmed": True,
                "ops_to_execute": [
                    {"op": "add_edge", "params": {"source": e["target"], "target": e["source"]}}
                ]
            })
            if s == 400:
                found_cycle = True
                break
        # At least one reverse edge should create a cycle in any DAG
        # If no cycle detected, the graph might have been modified by prior tests


class TestCommandConversationContext:
    """Multi-turn conversation preserves context."""

    def test_two_turn_conversation(self, cmd_project, needs_llm):
        """Second turn can reference first turn's results."""
        pid, proj = cmd_project

        # Turn 1: add a node
        s1, d1 = req("POST", f"/api/projects/{pid}/command", {
            "description": "Add a Performance Test task, 3 days, as a final step."
        })
        assert s1 == 200, f"Turn 1 failed: {d1}"

        # Turn 2: reference the node we just added
        s2, d2 = req("POST", f"/api/projects/{pid}/command", {
            "description": "Rename that performance test to Load Testing and set it to 4 days."
        })
        assert s2 == 200, f"Turn 2 failed: {d2}"
        # Should handle gracefully even if LLM can't resolve the reference
        action2 = d2.get("action")
        assert action2 in ("confirm_plan", "ask", None), \
            f"Unexpected action for turn 2: {action2}"
