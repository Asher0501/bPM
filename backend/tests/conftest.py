# -*- coding: utf-8 -*-
"""pytest fixtures for bePm E2E tests"""

import pytest
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:48090"


def req(method, path, body=None):
    """Helper: make an HTTP request to the test server."""
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


def has_llm() -> bool:
    """Check if LLM API key is configured (Anthropic or compatible).

    Imports config lazily. Returns True if an API key is found.
    """
    try:
        from config import get_anthropic_config
        cfg = get_anthropic_config()
        return bool(cfg.get("api_key"))
    except Exception:
        return False


def needs_llm():
    """Fixture: skip test if no LLM API key is configured.

    Usage: add 'needs_llm' as a parameter to any test function that depends on LLM.
    """
    if not has_llm():
        pytest.skip("LLM API key not configured — test requires LLM")


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "needs_llm: Skip test if no LLM API key is configured "
        "(checks ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in env or claude settings)",
    )


@pytest.fixture
def api():
    """Provides the req helper and BASE URL."""
    ns = {"req": req, "BASE": BASE}
    return ns
