/* bePm — WebSocket 客户端 */

const WSClient = (() => {
  let _ws = null;
  let _projectId = null;
  let _handlers = {};
  let _pingInterval = null;

  function connect(projectId) {
    disconnect();

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws/projects/${projectId}`;
    _projectId = projectId;

    _ws = new WebSocket(url);

    _ws.onopen = () => {
      console.log(`[WS] Connected to project ${projectId}`);
      // 心跳保持连接
      _pingInterval = setInterval(() => {
        if (_ws && _ws.readyState === WebSocket.OPEN) {
          _ws.send("ping");
        }
      }, 30000);
    };

    _ws.onmessage = (event) => {
      if (event.data === "pong") return;
      try {
        const msg = JSON.parse(event.data);
        const handler = _handlers[msg.type];
        if (handler) handler(msg.data);
      } catch (e) {
        console.warn("[WS] Failed to parse message:", e);
      }
    };

    _ws.onclose = () => {
      console.log("[WS] Disconnected");
      if (_pingInterval) clearInterval(_pingInterval);
    };

    _ws.onerror = (e) => {
      console.error("[WS] Error:", e);
    };
  }

  function disconnect() {
    if (_pingInterval) clearInterval(_pingInterval);
    if (_ws) {
      _ws.close();
      _ws = null;
    }
    _projectId = null;
  }

  function on(msgType, handler) {
    _handlers[msgType] = handler;
  }

  function off(msgType) {
    delete _handlers[msgType];
  }

  return { connect, disconnect, on, off };
})();
