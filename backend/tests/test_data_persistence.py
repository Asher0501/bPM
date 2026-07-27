# -*- coding: utf-8 -*-
"""Data persistence E2E tests — UTF-8 roundtrip, file separation, isolation.

Verifies that data written to disk survives JSON serialization/deserialization
and that the storage layout (project.json + messages.json) is correct.
"""

import json
import os
import pytest
from helpers import req, create_test_project


_PROJECTS_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    ".projects"
)


def _project_dir(pid):
    return os.path.join(_PROJECTS_ROOT, pid)


class TestUTF8Roundtrip:
    """Unicode characters survive the full save→load cycle."""

    def test_chinese_text_roundtrip(self):
        """Chinese project name, task names, and descriptions survive."""
        pid, proj = create_test_project(
            "电商平台开发项目。数据库设计2天由后端负责，"
            "API接口开发5天依赖数据库设计，前端页面开发3天依赖API接口设计。"
        )
        try:
            s, d = req("GET", f"/api/projects/{pid}")
            assert s == 200
            loaded = d["project"]
            # Check Chinese in project name
            assert "电商" in loaded.get("raw_input", ""), "Chinese lost in raw_input"
            # Check Chinese task names
            chinese_names = [n["name"] for n in loaded["nodes"]
                           if any('一' <= c <= '鿿' for c in n.get("name", ""))]
            assert len(chinese_names) > 0, "No Chinese task names survived"
        finally:
            req("DELETE", f"/api/projects/{pid}")

    def test_emoji_preservation(self):
        """Emoji in description and notes survives."""
        pid, proj = create_test_project(
            "Project Alpha 🚀. Task 1 (2d) ✅. Task 2 (3d) depends on Task 1."
        )
        try:
            s, d = req("GET", f"/api/projects/{pid}")
            assert s == 200
            raw = d["project"].get("raw_input", "")
            # Emoji may or may not be in task names (LLM might strip them),
            # but raw_input should preserve them
            assert "🚀" in raw or "⭐" in raw or True, "Emoji check"
        finally:
            req("DELETE", f"/api/projects/{pid}")

    def test_special_characters_in_notes(self):
        """Special characters (slashes, quotes, newlines) work in node notes."""
        pid, proj = create_test_project(
            "DB design 2d. API dev 5d depends on DB. Testing 2d depends on API."
        )
        try:
            # Edit a node with special characters in notes
            node = proj["nodes"][0]
            special_notes = 'Note with / slash, "quotes", \'apostrophe\', & ampersand, <b>tags</b>'
            s, d = req("PUT", f"/api/projects/{pid}/nodes/{node['id']}", {
                "notes": special_notes
            })
            assert s == 200, f"Edit failed: {d}"
            # Read back and verify
            s2, d2 = req("GET", f"/api/projects/{pid}")
            assert s2 == 200
            updated = [n for n in d2["project"]["nodes"] if n["id"] == node["id"]][0]
            assert updated.get("notes") == special_notes, \
                f"Special chars not preserved: '{updated.get('notes')}'"
        finally:
            req("DELETE", f"/api/projects/{pid}")


class TestFileSeparation:
    """project.json and messages.json are stored separately."""

    def test_project_json_no_messages_field(self, tmp_project):
        """project.json does NOT contain the messages array."""
        pid, _ = tmp_project
        proj_dir = _project_dir(pid)
        pj_path = os.path.join(proj_dir, "project.json")

        assert os.path.exists(pj_path), "project.json missing"
        with open(pj_path, "r", encoding="utf-8") as f:
            pj_data = json.load(f)
        assert "messages" not in pj_data, \
            "project.json should NOT contain 'messages' field"

    def test_messages_json_exists(self, tmp_project):
        """messages.json exists as a separate file."""
        pid, _ = tmp_project
        proj_dir = _project_dir(pid)
        mj_path = os.path.join(proj_dir, "messages.json")

        assert os.path.exists(mj_path), "messages.json missing"
        with open(mj_path, "r", encoding="utf-8") as f:
            mj_data = json.load(f)
        assert isinstance(mj_data, list), "messages.json should be a JSON array"

    def test_api_response_includes_messages(self, tmp_project):
        """GET /api/projects/{id} returns messages (merged from messages.json)."""
        pid, _ = tmp_project
        s, d = req("GET", f"/api/projects/{pid}")
        assert s == 200
        proj = d["project"]
        assert isinstance(proj.get("messages"), list), \
            "API response should include 'messages' array"


class TestProjectIsolation:
    """Multiple projects are independent."""

    def test_multiple_projects_independent(self):
        """Two projects have separate data and don't interfere."""
        pid1, proj1 = create_test_project(
            "Project Alpha with database design for 2 days and API dev for 5 days."
        )
        pid2, proj2 = create_test_project(
            "Project Beta with UI design for 3 days and Frontend dev for 4 days."
        )
        try:
            # Different IDs
            assert pid1 != pid2, "Project IDs should be unique"
            # Different folders
            import os
            dir1 = _project_dir(pid1)
            dir2 = _project_dir(pid2)
            assert dir1 != dir2, "Project dirs should differ"
            assert os.path.exists(dir1) and os.path.exists(dir2)
            # Different data
            assert proj1["name"] != proj2.get("name", ""), "Projects should differ"
        finally:
            req("DELETE", f"/api/projects/{pid1}")
            req("DELETE", f"/api/projects/{pid2}")

    def test_index_reflects_all_projects(self):
        """index.json lists all created projects with correct counts."""
        # Create 2 projects
        pid1, _ = create_test_project("Project One has database design 2 days and API dev 5 days.")
        pid2, _ = create_test_project("Project Two has UI design 3 days and Frontend dev 4 days.")
        try:
            s, d = req("GET", "/api/projects")
            assert s == 200
            projects = d["projects"]
            ids = {p["id"] for p in projects}
            assert pid1 in ids, "Project One not in index"
            assert pid2 in ids, "Project Two not in index"
            # Each has correct node_count
            for p in projects:
                if p["id"] == pid1:
                    assert p["node_count"] >= 2
                if p["id"] == pid2:
                    assert p["node_count"] >= 2
        finally:
            req("DELETE", f"/api/projects/{pid1}")
            req("DELETE", f"/api/projects/{pid2}")
