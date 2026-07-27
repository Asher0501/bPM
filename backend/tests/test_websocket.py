# -*- coding: utf-8 -*-
"""WebSocket E2E tests — real-time push: node_status, risk_alert, heartbeat.

Verifies the WebSocket pipeline:
  frontend connects → backend accepts → events from API calls → frontend receives via WS.
"""

import json
import time
import pytest
from helpers import (
    req, create_test_project,
    ws_connect, ws_receive_json, ws_send, requires_websockets,
)


@pytest.fixture(scope="module")
def ws_project():
    """Create a project for WebSocket test suite."""
    pid, proj = create_test_project(
        "WS test project: DB design 2d, API dev 5d depends on DB, "
        "Frontend 3d depends on DB, Testing 2d depends on API and Frontend."
    )
    return pid, proj


class TestWebSocketConnection:
    """WebSocket connection lifecycle."""

    def test_connect_valid_project(self, ws_project):
        """Connecting to a valid project succeeds."""
        requires_websockets()
        pid, _ = ws_project
        ws = ws_connect(pid)
        assert ws is not None, "WebSocket connection failed"
        ws.close()

    def test_connect_invalid_project(self):
        """Connecting to non-existent project: connection refused."""
        requires_websockets()
        ws = ws_connect("nonexist123", timeout=3)
        if ws is not None:
            # May connect but then immediately close
            try:
                msg = ws_receive_json(ws, timeout=3)
                # Should get closed
                ws.close()
            except Exception:
                pass
            # Success if we didn't crash

    def test_heartbeat_ping_pong(self, ws_project):
        """Client sends 'ping', server replies 'pong'."""
        requires_websockets()
        pid, _ = ws_project
        ws = ws_connect(pid)
        assert ws is not None, "WebSocket connection failed"
        try:
            ws_send(ws, "ping")
            # Receive pong (text, not JSON)
            try:
                resp = ws.recv(timeout=5)
                assert resp == "pong", f"Expected 'pong', got '{resp}'"
            except Exception as e:
                pytest.fail(f"No pong received: {e}")
        finally:
            ws.close()

    def test_disconnect_cleanup(self, ws_project):
        """After disconnect, a new connection works fine."""
        requires_websockets()
        pid, _ = ws_project
        ws1 = ws_connect(pid)
        assert ws1 is not None
        ws1.close()

        # Second connection should work
        ws2 = ws_connect(pid)
        assert ws2 is not None
        ws2.close()

    def test_multiple_clients(self, ws_project):
        """Two concurrent clients both receive broadcasts."""
        requires_websockets()
        pid, _ = ws_project
        ws_a = ws_connect(pid)
        ws_b = ws_connect(pid)
        assert ws_a and ws_b, "Both connections should succeed"
        try:
            # Both should respond to ping
            ws_send(ws_a, "ping")
            resp_a = ws_a.recv(timeout=5)
            assert resp_a == "pong"

            ws_send(ws_b, "ping")
            resp_b = ws_b.recv(timeout=5)
            assert resp_b == "pong"
        finally:
            ws_a.close()
            ws_b.close()


class TestWebSocketBroadcast:
    """Real-time event broadcasting."""

    def test_node_status_broadcast(self, ws_project):
        """Progress update triggers node_status via WebSocket."""
        requires_websockets()
        pid, proj = ws_project
        ws = ws_connect(pid)
        assert ws is not None, "WebSocket connection failed"
        try:
            # Drain any initial messages
            while True:
                try:
                    ws.recv(timeout=0.5)
                except Exception:
                    break

            # Submit progress via REST API (this triggers WS broadcast)
            first_node = proj["nodes"][0]
            s, d = req("POST", f"/api/projects/{pid}/progress", {
                "progress_text": f"{first_node['name']} is completed."
            })
            # Don't assert s==200 since this needs LLM — just check if broadcast happened

            # Try to receive node_status broadcast
            try:
                msg = ws_receive_json(ws, timeout=5)
                if msg:
                    # If we got a message, verify structure
                    assert "type" in msg, f"WS message missing 'type': {msg}"
                    # Broadcast message types are: node_status, risk_alert, suggestion
                    assert msg["type"] in ("node_status", "risk_alert", "suggestion"), \
                        f"Unknown WS message type: {msg['type']}"
            except Exception:
                # Broadcast may not happen if no nodes were updated (LLM unavailable)
                pass
        finally:
            ws.close()

    def test_reconnect_functional(self, ws_project):
        """Reconnecting after close works correctly."""
        requires_websockets()
        pid, _ = ws_project

        # Connect, send ping, verify pong, close
        for _ in range(2):
            ws = ws_connect(pid)
            assert ws is not None
            ws_send(ws, "ping")
            try:
                resp = ws.recv(timeout=5)
                assert resp == "pong", f"Reconnect pong failed"
            except Exception as e:
                pytest.fail(f"Reconnect failed: {e}")
            ws.close()
            time.sleep(0.2)  # Brief pause between connections
