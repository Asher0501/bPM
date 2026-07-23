/* bePm — Cytoscape.js DAG 可视化 */

var DAGView = (function () {
  var _cy = null;

  function _hasCytoscape() {
    return typeof cytoscape !== "undefined";
  }

  function _doLayout() {
    if (!_cy) return;
    var dagMsg = document.getElementById("dag-layout-msg");
    try {
      _cy.layout({ name: "dagre", rankDir: "LR", spacingFactor: 1.6, nodeDimensionsIncludeLabels: true, rankSep: 80, nodeSep: 40 }).run();
      if (dagMsg) dagMsg.textContent = "";
    } catch (e) {
      console.warn("[DAG] dagre failed:", e.message);
      if (dagMsg) dagMsg.textContent = "dagre不可用，使用备用布局";
      try {
        _cy.layout({ name: "breadthfirst", directed: true, spacingFactor: 1.3 }).run();
        if (dagMsg) dagMsg.textContent = "";
      } catch (e2) {
        console.error("[DAG] all layouts failed:", e2.message);
        if (dagMsg) dagMsg.textContent = "布局失败，请刷新页面";
      }
    }
  }

  function init(containerId) {
    containerId = containerId || "cy-container";
    if (!_hasCytoscape()) { console.warn("[DAG] no cytoscape"); return null; }

    // 强制清理
    if (_cy) { try { _cy.destroy(); } catch(e){}; _cy = null; }

    var container = document.getElementById(containerId);
    if (!container) { console.warn("[DAG] no container #"+containerId); return null; }

    _cy = cytoscape({
      container: container,
      style: [
        {
          selector: "node",
          style: {
            "shape": "roundrectangle",
            "background-color": "#6b7280",
            label: "data(label)",
            "text-valign": "center",
            "text-halign": "center",
            "font-size": "10px",
            color: "#fff",
            width: 172,
            height: 52,
            "padding": "6px",
            "border-width": 0,
            "font-weight": "600",
            "text-wrap": "wrap",
            "text-max-width": "158px",
            "text-justification": "center",
            "background-opacity": 1,
          },
        },
        {
          selector: "node.critical",
          style: { "border-width": 3, "border-color": "#dc2626", width: 180, height: 58 },
        },
        { selector: "node.status-completed", style: { "background-color": "#059669" } },
        { selector: "node.status-in_progress", style: { "background-color": "#4f46e5" } },
        { selector: "node.status-pending", style: { "background-color": "#64748b" } },
        { selector: "node.status-delayed", style: { "background-color": "#e11d48" } },
        { selector: "node.status-blocked", style: { "background-color": "#d97706" } },
        {
          selector: "edge", style: { width: 2, "line-color": "#94a3b8", "target-arrow-color": "#94a3b8", "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 1.2 },
        },
        {
          selector: "edge.critical", style: { width: 3, "line-color": "#e11d48", "target-arrow-color": "#e11d48" },
        },
      ],
    });

    // 点击节点显示 tooltip
    _cy.on("tap", "node", function (evt) {
      var node = evt.target;
      var data = node.data();
      _showTooltip(node, data);
    });
    _cy.on("tap", function (evt) {
      if (evt.target === _cy) _hideTooltip();
    });

    return _cy;
  }

  function render(graphData) {
    // 每次 render 都重新创建，避免状态污染
    if (_cy) { try { _cy.destroy(); } catch(e){}; _cy = null; }
    init();
    if (!_cy) return;

    var nodes = graphData.nodes || [];
    var edges = graphData.edges || [];
    var critical_path = graphData.critical_path || [];
    var cp = {};
    critical_path.forEach(function (nid) { cp[nid] = true; });

    // 添加节点
    nodes.forEach(function (n) {
      var resources = (n.resources || []).filter(Boolean);
      var fo = resources.length > 0 ? resources.join(",") : "";
      var pct = (n.progress > 0 && n.progress < 100) ? ("  " + n.progress + "%") : "";
      var nameStr = n.name || n.id;
      var nameLine = nameStr.length > 18 ? nameStr.substring(0, 16) + "…" : nameStr;
      var sep = "─".repeat(Math.min(nameStr.length, 16));
      var foLine = fo ? ("FO: " + fo + pct) : (pct ? ("进度: " + n.progress + "%") : "");
      var esStr = n.es_date || ("D+" + (n.es != null ? n.es : "?"));
      var efStr = n.ef_date || ("D+" + (n.ef != null ? n.ef : "?"));
      var dateLine = esStr + " → " + efStr;
      if (n.is_critical) dateLine += " ★";
      var label = nameLine + "\n" + sep + "\n" + foLine + "\n" + dateLine;

      _cy.add({
        group: "nodes",
        data: {
          id: n.id, label: label, name: n.name, progress: n.progress,
          status: n.status, estimatedDays: n.estimated_days, confidence: n.confidence,
          isCritical: n.is_critical, floatDays: n.float_days,
          es: n.es, ef: n.ef, ls: n.ls, lf: n.lf,
          esDate: n.es_date, efDate: n.ef_date, lsDate: n.ls_date, lfDate: n.lf_date,
          resources: (n.resources || []).join(", "), notes: n.notes || "",
        },
        classes: ("status-" + n.status + (n.is_critical ? " critical" : "")).trim(),
      });
    });

    // 添加边
    edges.forEach(function (e) {
      var isCrit = cp[e.source] && cp[e.target];
      _cy.add({
        group: "edges",
        data: { id: e.source + "->" + e.target, source: e.source, target: e.target },
        classes: isCrit ? "critical" : "",
      });
    });

    // 布局
    _doLayout();

    // fit
    setTimeout(function () { if (_cy) _cy.fit(40); }, 400);
  }

  function updateNodeStatus(taskId, progress, status) {
    if (!_cy) return;
    var node = _cy.getElementById(taskId);
    if (node.length === 0) return;
    node.data("progress", progress);
    node.data("status", status);
    "completed,in_progress,pending,delayed,blocked".split(",").forEach(function (s) { node.removeClass("status-" + s); });
    node.addClass("status-" + status);
    // rebuild label
    var nm = node.data("name") || "";
    var fo = node.data("resources") || "";
    var esD = node.data("esDate"), efD = node.data("efDate");
    var isCrit = node.data("isCritical");
    var pct = (progress > 0 && progress < 100) ? ("  " + progress + "%") : "";
    var nameStr = nm.length > 18 ? nm.substring(0, 16) + "…" : nm;
    var sep = "─".repeat(Math.min(nm.length, 16));
    var foLine = fo ? ("FO: " + fo + pct) : (pct ? ("进度: " + progress + "%") : "");
    var dateLine = (esD || "?") + " → " + (efD || "?");
    if (isCrit) dateLine += " ★";
    node.data("label", nameStr + "\n" + sep + "\n" + foLine + "\n" + dateLine);
  }

  function highlightCriticalPath(criticalPath) {
    if (!_cy) return;
    _cy.nodes().removeClass("critical");
    _cy.edges().removeClass("critical");
    var cpSet = {};
    (criticalPath || []).forEach(function (nid) { cpSet[nid] = true; });
    _cy.nodes().forEach(function (n) { if (cpSet[n.id()]) n.addClass("critical"); });
    _cy.edges().forEach(function (e) {
      if (cpSet[e.data("source")] && cpSet[e.data("target")]) e.addClass("critical");
    });
  }

  function fit() { if (_cy) _cy.fit(40); }

  // ---- Tooltip ----
  var _tooltip = null;

  function _showTooltip(node, data) {
    _hideTooltip();
    _tooltip = document.createElement("div");
    _tooltip.className = "dag-tooltip";
    var statusLabel = { completed: "完成", in_progress: "进行中", pending: "未开始", delayed: "延迟", blocked: "阻塞" };
    var rows = "<tr><td>进度</td><td>" + data.progress + "%</td></tr>"
      + "<tr><td>状态</td><td>" + (statusLabel[data.status] || data.status) + "</td></tr>"
      + "<tr><td>工期</td><td>" + data.estimatedDays + " 天</td></tr>";
    if (data.resources) rows += "<tr><td>FO</td><td>" + data.resources + "</td></tr>";
    var esStr = data.esDate || ("D+" + data.es);
    var efStr = data.efDate || ("D+" + data.ef);
    var lsStr = data.lsDate || ("D+" + data.ls);
    var lfStr = data.lfDate || ("D+" + data.lf);
    rows += "<tr><td>最早</td><td>" + esStr + " → " + efStr + "</td></tr>";
    rows += "<tr><td>最晚</td><td>" + lsStr + " → " + lfStr + "</td></tr>";
    if (data.floatDays != null) rows += "<tr><td>浮动</td><td>" + data.floatDays + " 天</td></tr>";
    rows += "<tr><td>关键路径</td><td>" + (data.isCritical ? "是 ★" : "否") + "</td></tr>";
    _tooltip.innerHTML = "<strong>" + data.name + "</strong><table>" + rows + "</table>";
    document.body.appendChild(_tooltip);
    var pos = node.renderedPosition();
    _tooltip.style.left = (pos.x + 20) + "px";
    _tooltip.style.top = (pos.y - 10) + "px";
  }

  function _hideTooltip() {
    if (_tooltip) { _tooltip.remove(); _tooltip = null; }
  }

  // 注入 tooltip 样式
  (function () {
    if (document.getElementById("dag-tooltip-style")) return;
    var s = document.createElement("style");
    s.id = "dag-tooltip-style";
    s.textContent = ".dag-tooltip{position:absolute;z-index:1000;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:10px 14px;box-shadow:0 4px 12px rgba(0,0,0,0.15);font-size:12px;pointer-events:none;min-width:180px}.dag-tooltip strong{display:block;margin-bottom:6px;font-size:13px}.dag-tooltip table{width:100%}.dag-tooltip td{padding:2px 0}.dag-tooltip td:first-child{color:#64748b;width:60px}";
    document.head.appendChild(s);
  })();

  function clear() {
    if (_cy) { try { _cy.destroy(); } catch(e){}; _cy = null; }
  }

  return {
    init: init, render: render, updateNodeStatus: updateNodeStatus,
    highlightCriticalPath: highlightCriticalPath, fit: fit, clear: clear,
  };
})();
