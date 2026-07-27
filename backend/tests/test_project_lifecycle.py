# -*- coding: utf-8 -*-
"""Project lifecycle E2E tests — create → read → list → delete → index integrity.

Covers the full frontend→backend→frontend loop for project CRUD operations.
"""

import json
import pytest
from helpers import (
    req, BASE, create_test_project,
    assert_valid_graph, assert_valid_schedule, assert_valid_buffer,
)


class TestProjectCreate:
    """Project creation — various input combinations."""

    def test_create_minimal_project(self):
        """Minimal valid description creates a project with nodes."""
        pid, proj = create_test_project(
            "Build a todo app with database design for 2 days and API dev for 5 days."
        )
        assert len(proj.get("nodes", [])) >= 1, "Should have at least 1 node"
        assert_valid_schedule(proj["schedule"])
        req("DELETE", f"/api/projects/{pid}")

    def test_create_with_all_inputs(self):
        """Description + deadline + additional_info + file_text all merged into raw_input."""
        pid, proj = create_test_project(
            "Build e-commerce platform with database design for 2 days and API dev for 5 days.",
            deadline="2026-12-31",
            additional_info="Team has 3 backend devs",
            file_text="Extra: Performance test 3d",
        )
        raw = proj["raw_input"]
        assert "e-commerce" in raw.lower(), "description missing from raw_input"
        assert "3 backend" in raw, "additional_info missing from raw_input"
        assert "Performance test" in raw, "file_text missing from raw_input"
        assert proj["deadline"] == "2026-12-31"
        req("DELETE", f"/api/projects/{pid}")

    def test_create_too_short_description(self):
        """Description < 10 chars returns 400."""
        s, d = req("POST", "/api/projects", {"description": "short"})
        assert s == 400, f"Expected 400, got {s}"
        # Error detail may be in d["error"] (raw body) or d["detail"]
        error_text = str(d.get("error", "")) + str(d.get("detail", ""))
        assert "10" in error_text or "短" in error_text, \
            f"Error should mention minimum length: {d}"

    def test_create_preserves_structure(self):
        """Created project has complete structure for frontend rendering."""
        pid, proj = create_test_project(
            "Complete test project with DB design 2d, API dev 5d depends on DB design, "
            "Frontend 3d depends on DB design, Testing 2d depends on API and Frontend."
        )
        try:
            # Check all top-level fields frontend expects
            assert proj["name"], "Project name empty"
            assert proj["id"], "Project id empty"
            assert len(proj["nodes"]) >= 3, f"Expected >=3 nodes, got {len(proj['nodes'])}"
            assert len(proj["edges"]) > 0, "No edges created"
            assert_valid_schedule(proj["schedule"])
            assert_valid_buffer(proj["buffer"])
            # risks may be empty if LLM unavailable but should be a list
            assert isinstance(proj.get("risks", []), list)
            # raw_input should contain the description
            assert "DB design" in proj.get("raw_input", "")
            # messages list exists
            assert isinstance(proj.get("messages", []), list)
            # created_at / updated_at are ISO timestamps
            assert "T" in proj.get("created_at", ""), "created_at not ISO format"
        finally:
            req("DELETE", f"/api/projects/{pid}")


class TestProjectRead:
    """Project read — get by ID, list all."""

    def test_get_project_by_id(self, tmp_project):
        """GET project returns full detail for frontend rendering."""
        pid, proj_created = tmp_project
        s, d = req("GET", f"/api/projects/{pid}")
        assert s == 200, f"GET failed: {d}"
        proj = d["project"]
        assert proj["id"] == pid
        assert proj["name"] == proj_created["name"]
        assert len(proj["nodes"]) == len(proj_created["nodes"])
        assert_valid_schedule(proj["schedule"])

    def test_get_project_not_found(self):
        """GET non-existent project returns 404 with JSON detail."""
        s, d = req("GET", "/api/projects/nonexist12345")
        assert s == 404, f"Expected 404, got {s}"
        # Should be JSON with detail
        assert "detail" in d or "error" in d, f"No error detail: {d}"

    def test_get_project_has_graph_compatible_data(self, tmp_project):
        """GET project data is compatible with graph endpoint format."""
        pid, _ = tmp_project
        s, d = req("GET", f"/api/projects/{pid}")
        assert s == 200
        proj = d["project"]
        # Each node has fields that frontend DAG expects
        for n in proj["nodes"]:
            assert "id" in n
            assert "name" in n
            assert "status" in n
            assert "progress" in n
            assert "is_critical" in n
        # Edges reference real nodes
        node_ids = {n["id"] for n in proj["nodes"]}
        for e in proj.get("edges", []):
            assert e["source"] in node_ids
            assert e["target"] in node_ids


class TestProjectList:
    """Project listing — empty, with data, ordering."""

    def test_list_projects_returns_array(self):
        """List always returns a projects array (may be empty)."""
        s, d = req("GET", "/api/projects")
        assert s == 200
        assert isinstance(d.get("projects", []), list), "projects should be a list"

    def test_list_projects_has_required_fields(self, tmp_project):
        """Each project in list has fields needed for list panel rendering."""
        pid, _ = tmp_project
        s, d = req("GET", "/api/projects")
        assert s == 200
        proj_list = d["projects"]
        our = [p for p in proj_list if p["id"] == pid]
        assert len(our) == 1, f"Project {pid} not found in list"
        p = our[0]
        for field in ["id", "name", "deadline", "created_at", "updated_at",
                       "node_count", "risk_count", "critical_risk_count"]:
            assert field in p, f"List item missing field: {field}"


class TestProjectDelete:
    """Project deletion — cleanup, index removal."""

    def test_delete_project_cleanup(self):
        """Delete removes project; subsequent GET returns 404."""
        pid, _ = create_test_project(
            "Delete test project has database design for 2 days and API development for 5 days."
        )
        s, d = req("DELETE", f"/api/projects/{pid}")
        assert s == 200, f"Delete failed: {d}"
        assert d.get("status") == "deleted"

        # Verify gone
        s2, d2 = req("GET", f"/api/projects/{pid}")
        assert s2 == 404, f"Project should be 404 after delete, got {s2}"

    def test_delete_nonexistent_project(self):
        """Delete non-existent project returns 404."""
        s, d = req("DELETE", "/api/projects/nonexist12345")
        assert s == 404, f"Expected 404, got {s}"

    def test_delete_removes_from_index(self):
        """After delete, project not in listing."""
        pid, _ = create_test_project(
            "Index cleanup test with database design 2 days and API dev 5 days."
        )
        req("DELETE", f"/api/projects/{pid}")
        s, d = req("GET", "/api/projects")
        assert s == 200
        ids = [p["id"] for p in d.get("projects", [])]
        assert pid not in ids, "Deleted project still in index"


class TestMessagesPersistence:
    """Messages (LLM conversation history) storage."""

    def test_messages_stored_in_separate_file(self, tmp_project):
        """project.json does NOT contain messages; messages.json does."""
        pid, _ = tmp_project
        import os
        proj_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            ".projects", pid
        )
        pj_path = os.path.join(proj_dir, "project.json")
        mj_path = os.path.join(proj_dir, "messages.json")

        # project.json should exist
        assert os.path.exists(pj_path), "project.json missing"
        with open(pj_path, "r", encoding="utf-8") as f:
            pj_data = json.load(f)
        assert "messages" not in pj_data, "project.json should NOT contain messages"

        # messages.json should exist (even if empty)
        assert os.path.exists(mj_path), "messages.json missing"
        with open(mj_path, "r", encoding="utf-8") as f:
            mj_data = json.load(f)
        assert isinstance(mj_data, list), "messages.json should be a list"

    def test_raw_input_roundtrip(self, tmp_project):
        """Raw input preserves original text including special characters."""
        pid, _ = tmp_project
        s, d = req("GET", f"/api/projects/{pid}")
        assert s == 200
        raw = d["project"].get("raw_input", "")
        assert "Temp project" in raw, "raw_input should contain original description"
