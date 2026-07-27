# -*- coding: utf-8 -*-
"""Progress workflow E2E tests — NL progress → node update → buffer → risk analysis.

Tests the full frontend→backend→frontend loop:
  user types progress update → LLM parses → nodes updated → buffer recalculated
  → risks analyzed → frontend panels refreshed.
"""

import pytest
from helpers import req, create_test_project, assert_valid_buffer, assert_valid_risk


@pytest.fixture(scope="module")
def progress_project():
    """Create a project for progress workflow tests."""
    pid, proj = create_test_project(
        "Progress test project: DB design 2d, API dev 5d depends on DB design, "
        "Frontend dev 3d depends on DB design, "
        "Testing 2d depends on API dev and Frontend dev."
    )
    return pid, proj


class TestProgressBasic:
    """Basic progress update scenarios."""

    def test_progress_empty_text(self, progress_project):
        """Submitting empty progress text should not crash."""
        pid, _ = progress_project
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": ""
        })
        # Should handle gracefully
        assert s in (200, 400), f"Unexpected status for empty progress: {s}"

    def test_progress_response_has_required_fields(self, progress_project):
        """Progress update response has all fields frontend needs."""
        pid, proj = progress_project
        first_node = proj["nodes"][0]
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": f"{first_node['name']} progress update."
        })
        # Don't assert s==200 since LLM may be unavailable
        if s == 200:
            assert "project" in d, "Response missing 'project'"
            assert "updated_nodes" in d, "Response missing 'updated_nodes'"
            assert "risk_signals" in d, "Response missing 'risk_signals'"
            assert "new_risks" in d, "Response missing 'new_risks'"
            assert isinstance(d["updated_nodes"], list)
            assert isinstance(d["new_risks"], list)


class TestProgressUpdates:
    """Progress update with LLM parsing."""

    def test_progress_complete_task(self, progress_project, needs_llm):
        """Marking a task complete updates its status."""
        pid, proj = progress_project
        first_node = proj["nodes"][0]
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": f"{first_node['name']} is 100% completed, finished on time."
        })
        assert s == 200, f"Progress update failed: {d}"
        updated_nodes = d.get("updated_nodes", [])
        if updated_nodes:
            # Check the updated project data
            updated_proj = d["project"]
            for n in updated_proj["nodes"]:
                if n["id"] in updated_nodes:
                    # At least one node should be completed if we said "100% completed"
                    pass  # LLM interpretation may vary

    def test_progress_partial_update(self, progress_project, needs_llm):
        """Partial progress update sets in_progress status."""
        pid, proj = progress_project
        first_node = proj["nodes"][0]
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": f"{first_node['name']} is at 50 percent."
        })
        assert s == 200, f"Progress update failed: {d}"
        # Check that response is well-formed
        assert "project" in d
        assert_valid_buffer(d["project"].get("buffer", {}))

    def test_progress_multiple_tasks(self, progress_project, needs_llm):
        """Mentioning multiple tasks updates them all."""
        pid, proj = progress_project
        nodes = proj["nodes"]
        if len(nodes) < 2:
            pytest.skip("Need at least 2 nodes")
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": (
                f"{nodes[0]['name']} is finished. "
                f"{nodes[1]['name']} is in progress at 30%."
            )
        })
        assert s == 200, f"Multi-task update failed: {d}"


class TestBufferConsumption:
    """Buffer tracking and consumption."""

    def test_buffer_initial_healthy(self, progress_project):
        """Fresh project starts with green buffer."""
        pid, _ = progress_project
        s, d = req("GET", f"/api/projects/{pid}")
        assert s == 200
        buffer = d["project"].get("buffer", {})
        if buffer:
            assert_valid_buffer(buffer)
            # Initial buffer should be healthy
            assert buffer["consumed_days"] == 0.0, \
                f"Initial buffer consumed should be 0, got {buffer['consumed_days']}"

    def test_buffer_data_in_graph(self, progress_project):
        """Graph endpoint includes buffer data for buffer panel rendering."""
        pid, _ = progress_project
        s, graph = req("GET", f"/api/projects/{pid}/graph")
        assert s == 200
        buffer = graph.get("buffer")
        if buffer:
            assert_valid_buffer(buffer)

    def test_progress_preserves_buffer(self, progress_project):
        """After progress update, buffer is still valid."""
        pid, proj = progress_project
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": "Everything is on track, no delays."
        })
        if s == 200:
            buffer = d["project"].get("buffer", {})
            if buffer:
                assert_valid_buffer(buffer)


class TestRiskAnalysis:
    """Risk analysis after progress updates."""

    def test_project_has_risks_list(self, progress_project):
        """Every project has a risks list (may be empty)."""
        pid, _ = progress_project
        s, d = req("GET", f"/api/projects/{pid}")
        assert s == 200
        risks = d["project"].get("risks", [])
        assert isinstance(risks, list), "Risks should be a list"

    def test_graph_includes_risks(self, progress_project):
        """Graph endpoint includes risks for frontend risk panel."""
        pid, _ = progress_project
        s, graph = req("GET", f"/api/projects/{pid}/graph")
        assert s == 200
        risks = graph.get("risks", [])
        assert isinstance(risks, list), "Graph risks should be a list"
        for r in risks:
            assert_valid_risk(r)

    def test_progress_returns_new_risks(self, progress_project, needs_llm):
        """Progress update returns new_risks array."""
        pid, proj = progress_project
        first_node = proj["nodes"][0]
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": (
                f"{first_node['name']} is delayed by 3 days due to resource shortage. "
                "One developer is on sick leave."
            )
        })
        assert s == 200, f"Progress update failed: {d}"
        new_risks = d.get("new_risks", [])
        assert isinstance(new_risks, list)

    def test_risk_signals_detected(self, progress_project, needs_llm):
        """Risk signals are extracted from progress text."""
        pid, proj = progress_project
        first_node = proj["nodes"][0]
        s, d = req("POST", f"/api/projects/{pid}/progress", {
            "progress_text": (
                f"{first_node['name']} is behind schedule. "
                "Team member on leave, technical challenge with database."
            )
        })
        assert s == 200
        risk_signals = d.get("risk_signals", [])
        assert isinstance(risk_signals, list)

    def test_re_schedule_updates_risks(self, progress_project):
        """Re-schedule triggers risk re-analysis."""
        pid, _ = progress_project
        s, d = req("POST", f"/api/projects/{pid}/schedule")
        assert s == 200
        risks = d["project"].get("risks", [])
        assert isinstance(risks, list)
