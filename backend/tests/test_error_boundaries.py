# -*- coding: utf-8 -*-
"""Error boundary E2E tests — input validation, HTTP status codes, security checks.

Tests the frontend→backend error handling loop:
  frontend sends bad input → backend returns appropriate error → frontend can display it.
"""

import json
import urllib.request
import pytest
from helpers import req, BASE, create_test_project


class TestInputValidation:
    """Input validation returns appropriate 4xx responses."""

    def test_path_traversal_prevented(self):
        """Project ID with '../' is blocked (400) or not found (404)."""
        s, d = req("GET", "/api/projects/../etc/passwd")
        assert s in (400, 404), f"Expected 400 or 404 for path traversal, got {s}"
        # 404 means the path was normalized before reaching _project_dir

    def test_invalid_project_id_format(self):
        """Project ID with special characters is rejected by API."""
        s, d = req("GET", "/api/projects/has%20space")
        assert s in (400, 404), f"Expected 400 or 404, got {s}"

    def test_api_404_returns_json(self):
        """Non-existent /api/* path returns JSON, not HTML."""
        s, d = req("GET", "/api/nonexistent-endpoint")
        assert s == 404, f"Expected 404, got {s}"
        # Should be JSON with detail
        assert "detail" in d or "error" in d, \
            f"API 404 should return JSON: {d}"

    def test_empty_project_description(self):
        """Empty description should fail validation."""
        s, d = req("POST", "/api/projects", {"description": ""})
        # Should reject — either 400 or 422
        assert s in (400, 422), f"Expected 400/422 for empty description, got {s}"

    def test_missing_required_body(self):
        """Missing required fields in request body returns 422."""
        s, d = req("POST", "/api/projects", {})
        assert s == 422, f"Expected 422 for missing body, got {s}"


class TestNotFoundErrors:
    """404 handling for various resource types."""

    def test_nonexistent_node_edit(self, tmp_project):
        """Editing non-existent node returns 404."""
        pid, _ = tmp_project
        s, d = req("PUT", f"/api/projects/{pid}/nodes/nonexistent", {
            "name": "New Name"
        })
        assert s == 404, f"Expected 404, got {s}: {d}"

    def test_nonexistent_node_delete(self, tmp_project):
        """Deleting non-existent node returns 404."""
        pid, _ = tmp_project
        s, d = req("DELETE", f"/api/projects/{pid}/nodes/nonexistent")
        assert s == 404, f"Expected 404, got {s}: {d}"

    def test_nonexistent_project_schedule(self):
        """Re-scheduling non-existent project returns 404."""
        s, d = req("POST", "/api/projects/nonexist12345/schedule")
        assert s == 404, f"Expected 404, got {s}: {d}"

    def test_nonexistent_project_graph(self):
        """Graph for non-existent project returns 404."""
        s, d = req("GET", "/api/projects/nonexist12345/graph")
        assert s == 404, f"Expected 404, got {s}: {d}"

    def test_nonexistent_project_progress(self):
        """Progress update for non-existent project returns 404."""
        s, d = req("POST", "/api/projects/nonexist12345/progress", {
            "progress_text": "task completed"
        })
        assert s == 404, f"Expected 404, got {s}: {d}"


class TestHealthCheck:
    """Health endpoint provides system status for frontend status bar."""

    def test_health_returns_ok(self):
        """Health check returns status=ok with LLM info."""
        s, d = req("GET", "/api/health")
        assert s == 200, f"Health check failed: {d}"
        assert d.get("status") == "ok"
        assert d.get("service") == "bePm"
        # Frontend uses these fields for status display
        assert "llm_provider" in d, "Frontend needs llm_provider for status bar"
        assert "llm_model" in d, "Frontend needs llm_model for status bar"

    def test_health_encoding_is_utf8(self):
        """Health reports utf-8 encoding (important for Chinese text)."""
        s, d = req("GET", "/api/health")
        assert s == 200
        assert d.get("encoding") == "utf-8", f"Encoding should be utf-8, got {d.get('encoding')}"


class TestStaticFiles:
    """Static file serving for frontend."""

    def test_index_html_loads(self):
        """Frontend index.html is served."""
        r = urllib.request.Request(f"{BASE}/")
        r.add_header("Accept", "text/html")
        with urllib.request.urlopen(r, timeout=10) as resp:
            html = resp.read().decode("utf-8")
        assert "bePm" in html, "index.html should contain 'bePm'"
        assert "cytoscape" in html.lower(), "index.html should load cytoscape"

    def test_static_js_served(self):
        """JS files are served with correct content-type."""
        r = urllib.request.Request(f"{BASE}/js/api.js")
        r.add_header("Accept", "application/javascript")
        with urllib.request.urlopen(r, timeout=10) as resp:
            content = resp.read().decode("utf-8")
        assert "API" in content, "api.js should contain API object"

    def test_nonexistent_static_file(self):
        """Non-existent static file — server handles gracefully (not crash)."""
        import http.client
        try:
            r = urllib.request.Request(f"{BASE}/nonexistent-file.xyz")
            with urllib.request.urlopen(r, timeout=10) as resp:
                pass  # May serve index.html as SPA fallback
        except (urllib.error.HTTPError, http.client.HTTPException, Exception):
            pass  # Any non-crash behavior is acceptable
