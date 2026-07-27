# -*- coding: utf-8 -*-
"""Extended Node CRUD E2E tests — tags, notes, status transitions, full lifecycle.

Covers frontend→backend→frontend for node editing operations not in test_node_crud_e2e.py.
"""

import pytest
from helpers import req, create_test_project, assert_valid_schedule


@pytest.fixture(scope="module")
def node_project():
    """Create a project for extended node CRUD tests."""
    pid, proj = create_test_project(
        "Node CRUD test: DB design 2d, API dev 5d depends on DB design, "
        "Frontend dev 3d depends on DB design, Testing 2d."
    )
    return pid, proj


class TestNodeEdit:
    """Editing node properties."""

    def test_edit_node_tags(self, node_project):
        """Tags can be set and retrieved."""
        pid, proj = node_project
        node = proj["nodes"][0]
        new_tags = ["backend", "P0", "critical"]

        s, d = req("PUT", f"/api/projects/{pid}/nodes/{node['id']}", {
            "tags": new_tags
        })
        assert s == 200, f"Edit tags failed: {d}"
        edited = [n for n in d["project"]["nodes"] if n["id"] == node["id"]][0]
        assert sorted(edited.get("tags", [])) == sorted(new_tags), \
            f"Tags not saved: {edited.get('tags')}"

    def test_edit_node_notes(self, node_project):
        """Notes preserve multiline and special characters."""
        pid, proj = node_project
        node = proj["nodes"][0]
        notes = "Line 1: 中文字符\nLine 2: special chars / \"quotes\"\nLine 3: emoji 🎉"

        s, d = req("PUT", f"/api/projects/{pid}/nodes/{node['id']}", {
            "notes": notes
        })
        assert s == 200, f"Edit notes failed: {d}"
        edited = [n for n in d["project"]["nodes"] if n["id"] == node["id"]][0]
        assert edited.get("notes") == notes, \
            f"Notes not preserved: '{edited.get('notes')}' vs '{notes}'"

    def test_edit_node_name(self, node_project):
        """Node name can be changed."""
        pid, proj = node_project
        node = proj["nodes"][0]
        new_name = "Updated Task Name 更新后的名称"

        s, d = req("PUT", f"/api/projects/{pid}/nodes/{node['id']}", {
            "name": new_name
        })
        assert s == 200, f"Edit name failed: {d}"
        edited = [n for n in d["project"]["nodes"] if n["id"] == node["id"]][0]
        assert edited["name"] == new_name, f"Name not changed: {edited['name']}"

    def test_edit_node_all_fields(self, node_project):
        """All editable fields can be changed at once."""
        pid, proj = node_project
        node = proj["nodes"][0]

        s, d = req("PUT", f"/api/projects/{pid}/nodes/{node['id']}", {
            "name": "Complete Edit Test",
            "estimated_days": 7.5,
            "confidence": 0.95,
            "resources": ["Alice", "Bob"],
            "notes": "All fields updated",
            "tags": ["test", "complete"],
        })
        assert s == 200, f"Edit all fields failed: {d}"
        edited = [n for n in d["project"]["nodes"] if n["id"] == node["id"]][0]
        assert edited["name"] == "Complete Edit Test"
        assert edited["estimated_days"] == 7.5
        assert edited["confidence"] == 0.95
        assert sorted(edited["resources"]) == sorted(["Alice", "Bob"])
        assert edited["notes"] == "All fields updated"
        assert sorted(edited["tags"]) == sorted(["test", "complete"])

    def test_edit_triggers_reschedule(self, node_project):
        """Editing a node triggers automatic re-scheduling."""
        pid, proj = node_project
        node = proj["nodes"][0]
        old_schedule = proj.get("schedule", {})

        s, d = req("PUT", f"/api/projects/{pid}/nodes/{node['id']}", {
            "estimated_days": 999.0  # Huge change — should definitely affect schedule
        })
        assert s == 200, f"Edit failed: {d}"
        new_schedule = d["project"]["schedule"]
        assert_valid_schedule(new_schedule)
        # With a huge increase in duration, schedule should change
        assert new_schedule["total_duration_days"] != old_schedule.get("total_duration_days", 0), \
            "Schedule should change after huge duration edit"


class TestNodeDelete:
    """Node deletion and dependency cleanup."""

    def test_delete_node_with_downstream(self, node_project):
        """Deleting a node with downstream dependents cleans up references."""
        pid, proj = node_project
        # Find a node that has downstream dependents
        nodes = proj["nodes"]
        target = None
        downstream_count = 0
        for n in nodes:
            downstream = [x for x in nodes if n["id"] in x.get("pre_dependencies", [])]
            if downstream:
                target = n
                downstream_count = len(downstream)
                break

        if not target:
            pytest.skip("No node has downstream dependents")

        s, d = req("DELETE", f"/api/projects/{pid}/nodes/{target['id']}")
        assert s == 200, f"Delete failed: {d}"
        assert d["deleted"] == target["id"]
        assert "deleted_name" in d
        assert d["affected_count"] == downstream_count
        assert len(d.get("affected_names", [])) == downstream_count

        # Verify cleanup
        updated = d["project"]
        for n in updated["nodes"]:
            assert target["id"] not in n.get("pre_dependencies", []), \
                f"Node {n['id']} still references deleted {target['id']}"

    def test_delete_leaf_node(self, node_project):
        """Deleting a leaf node (no downstream) works cleanly."""
        pid, proj = node_project
        # Find leaf node
        all_ids = {n["id"] for n in proj["nodes"]}
        all_preds = set()
        for n in proj["nodes"]:
            all_preds.update(n.get("pre_dependencies", []))
        leaves = [n for n in proj["nodes"] if n["id"] not in all_preds]
        if not leaves:
            pytest.skip("No leaf nodes")

        target = leaves[-1]
        s, d = req("DELETE", f"/api/projects/{pid}/nodes/{target['id']}")
        assert s == 200, f"Delete leaf failed: {d}"
        assert d["affected_count"] == 0, \
            f"Leaf node should have 0 affected, got {d['affected_count']}"

    def test_delete_triggers_reschedule(self, tmp_project):
        """Deleting a node triggers re-scheduling."""
        pid, proj = tmp_project
        # Find leaf node
        all_ids = {n["id"] for n in proj["nodes"]}
        all_preds = set()
        for n in proj["nodes"]:
            all_preds.update(n.get("pre_dependencies", []))
        leaves = [n for n in proj["nodes"] if n["id"] not in all_preds]
        if not leaves:
            pytest.skip("No leaf nodes to delete")

        target = leaves[-1]
        s, d = req("DELETE", f"/api/projects/{pid}/nodes/{target['id']}")
        assert s == 200
        assert d["project"].get("schedule") is not None
        assert_valid_schedule(d["project"]["schedule"])


class TestNodeStatusTransitions:
    """Node status lifecycle: pending → in_progress → completed."""

    @pytest.fixture
    def status_project(self):
        """Fresh project for status transition tests."""
        pid, proj = create_test_project(
            "Status test project with database design for 2 days and API dev for 3 days."
        )
        yield pid, proj
        req("DELETE", f"/api/projects/{pid}")

    def test_status_pending_to_in_progress(self, status_project):
        """Transition from pending to in_progress."""
        pid, proj = status_project
        node = proj["nodes"][0]

        s, d = req("PUT", f"/api/projects/{pid}/nodes/{node['id']}", {
            "status": "in_progress",
            "progress": 30,
        })
        assert s == 200, f"Status transition failed: {d}"
        edited = [n for n in d["project"]["nodes"] if n["id"] == node["id"]][0]
        assert edited["status"] == "in_progress"
        assert edited["progress"] == 30

    def test_status_to_completed(self, status_project):
        """Transition to completed."""
        pid, proj = status_project
        node = proj["nodes"][0]

        s, d = req("PUT", f"/api/projects/{pid}/nodes/{node['id']}", {
            "status": "completed",
            "progress": 100,
        })
        assert s == 200, f"Complete transition failed: {d}"
        edited = [n for n in d["project"]["nodes"] if n["id"] == node["id"]][0]
        assert edited["status"] == "completed"
        assert edited["progress"] == 100

    def test_invalid_status_value(self, status_project):
        """Setting an invalid status returns 422 validation error."""
        pid, proj = status_project
        node = proj["nodes"][0]

        s, d = req("PUT", f"/api/projects/{pid}/nodes/{node['id']}", {
            "status": "invalid_status_xyz"
        })
        # FastAPI validates Enum — returns 422
        assert s == 422, f"Expected 422 for invalid status, got {s}: {d}"
