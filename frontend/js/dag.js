/* bePm — Cytoscape.js DAG 可视化 */

var DAGView = (function () {
  var _cy = null;

  function _hasCytoscape() {
    return typeof cytoscape !== "undefined";
  }

  function _buildLabel(n) {
    var name = n.name || n.id || "";
    if (n.is_group && n.children) {
      var c = Array.isArray(n.children) ? n.children.length : (String(n.children||"").split(",").filter(Boolean).length);
      if (c > 0) name = name + " [" + c + "]";
    }
    var pct = n.progress || 0;
    if (pct > 0 && pct < 100) name = name + "  " + pct + "%";
    // 日期行
    var es = n.es_date || "";
    var ef = n.ef_date || "";
    var dateStr = (es || ef) ? (es + (ef ? " → " + ef : "")) : "";
    return name + (dateStr ? "\n" + dateStr : "");
  }

  function _doLayout(animate) {
    if (!_cy) return;
    var dagMsg = document.getElementById("dag-layout-msg");
    var opts = { name: "dagre", rankDir: "LR", spacingFactor: 1.35, nodeDimensionsIncludeLabels: true, rankSep: 70, nodeSep: 35 };
    if (animate) { opts.animate = true; opts.animationDuration = 600; opts.animationEasing = "ease-in-out-cubic"; }
    try {
      _cy.layout(opts).run();
      if (dagMsg) dagMsg.textContent = "";
    } catch (e) {
      console.warn("[DAG] dagre failed:", e.message);
      if (dagMsg) dagMsg.textContent = "dagre不可用，使用备用布局";
      try {
        _cy.layout({ name: "breadthfirst", directed: true, spacingFactor: 1.3, animate: animate, animationDuration: 500 }).run();
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
            "background-color": "#475569",
            label: "data(label)",
            "text-valign": "center",
            "text-halign": "center",
            "font-size": "14px",
            color: "#e2e8f0",
            width: 184,
            height: 54,
            "padding": "10px 16px",
            "border-width": 1,
            "border-color": "rgba(255,255,255,0.08)",
            "border-style": "solid",
            "font-weight": "600",
            "text-wrap": "wrap",
            "text-max-width": "166px",
            "text-justification": "center",
            "background-opacity": 0.85,
            "text-margin-y": 3,
            "text-outline-width": 0.3,
            "text-outline-color": "rgba(0,0,0,0.3)",
            "transition-property": "background-color, border-color, width, height",
            "transition-duration": 400,
            "transition-timing-function": "cubic-bezier(0.16, 1, 0.3, 1)",
          },
        },
        {
          selector: "node.critical",
          style: {
            width: 194, height: 60,
            "border-width": 3,
            "border-color": "#818cf8",
            "border-style": "solid",
          },
        },
        {
          selector: "node.is-group",
          style: {
            "border-width": 2.5,
            "border-color": "#a78bfa",
            "border-style": "dashed",
            "padding": "12px 18px",
            "background-opacity": 0.6,
          },
        },
        {
          selector: "node.is-group.critical",
          style: {
            "border-width": 4,
            "border-color": "#818cf8",
            "border-style": "solid",
          },
        },
        // Liquid glass node colors
        { selector: "node.status-completed",
          style: { "background-color": "#0d9488", "border-color": "rgba(52,211,153,0.3)" } },
        { selector: "node.status-in_progress",
          style: { "background-color": "#7c3aed", "border-color": "rgba(139,92,246,0.3)" } },
        { selector: "node.status-pending",
          style: { "background-color": "#475569", "border-color": "rgba(255,255,255,0.06)" } },
        { selector: "node.status-delayed",
          style: { "background-color": "#d97706", "border-color": "rgba(251,191,36,0.3)" } },
        { selector: "node.status-blocked",
          style: { "background-color": "#e11d48", "border-color": "rgba(248,113,113,0.3)" } },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "rgba(148,163,184,0.4)",
            "target-arrow-color": "rgba(148,163,184,0.4)",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "arrow-scale": 1,
            "transition-property": "line-color, target-arrow-color, width",
            "transition-duration": 400,
            "transition-timing-function": "cubic-bezier(0.16, 1, 0.3, 1)",
          },
        },
        {
          selector: "edge.critical",
          style: {
            width: 2.5,
            "line-color": "#818cf8",
            "target-arrow-color": "#818cf8",
          },
        },
      ],
    });

    // 单击节点 → 直接弹出编辑
    _cy.on("tap", "node", function (evt) {
      var node = evt.target;
      var data = node.data();
      _hideTooltip();
      if (data.is_group) {
        // 聚合节点：仅显示 tooltip
        _showTooltip(node, data);
        return;
      }
      // 普通节点：直接打开编辑弹窗
      if (typeof window.app !== "undefined" && window.app.openEditModal) {
        window.app.openEditModal(data.id);
      }
    });
    _cy.on("tap", function (evt) {
      if (evt.target === _cy) _hideTooltip();
    });

    // 鼠标悬停 → tooltip 预览
    _cy.on("mouseover", "node", function (evt) {
      var node = evt.target;
      _showTooltip(node, node.data());
    });
    _cy.on("mouseout", "node", function () {
      _hideTooltip();
    });

    return _cy;
  }

  function render(graphData) {
    var nodes = graphData.nodes || [];
    var edges = graphData.edges || [];
    var critical_path = graphData.critical_path || [];
    var cp = {};
    critical_path.forEach(function (nid) { cp[nid] = true; });

    // 已有实例 → 增量更新（平滑动画）
    if (_cy) {
      _updateGraph(nodes, edges, cp, graphData);
      return;
    }

    // 首次渲染 → 完整创建
    init();
    if (!_cy) return;

    nodes.forEach(function (n) {
      _cy.add({
        group: "nodes",
        data: {
          id: n.id, label: _buildLabel(n), name: n.name, progress: n.progress,
          status: n.status, estimatedDays: n.estimated_days, confidence: n.confidence,
          isCritical: n.is_critical, floatDays: n.float_days,
          es: n.es, ef: n.ef, ls: n.ls, lf: n.lf,
          esDate: n.es_date, efDate: n.ef_date, lsDate: n.ls_date, lfDate: n.lf_date,
          resources: (n.resources || []).join(", "), notes: n.notes || "",
          tags: (n.tags || []).join(", "), children: (n.children || []).join(", "),
          is_group: n.is_group || false,
        },
        classes: ("status-" + n.status + (n.is_critical ? " critical" : "") + (n.is_group ? " is-group" : "")).trim(),
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

    // fit with smooth animation
    setTimeout(function () { if (_cy) _cy.animate({ fit: { eles: _cy.elements(), padding: 50 }, duration: 500, easing: "ease-in-out-cubic" }); }, 400);
  }

  function _updateGraph(nodes, edges, cp, graphData) {
    // 构建新旧 ID 集合
    var newIds = {};
    nodes.forEach(function (n) { newIds[n.id] = true; });
    var newEdges = {};
    edges.forEach(function (e) { newEdges[e.source + "->" + e.target] = true; });

    // 1. 移除不存在的元素
    _cy.remove(_cy.nodes().filter(function (n) { return !newIds[n.id()]; }));
    _cy.remove(_cy.edges().filter(function (e) {
      return !newEdges[e.data("source") + "->" + e.data("target")];
    }));

    // 2. 更新已有节点 / 添加新节点
    nodes.forEach(function (n) {
      var existing = _cy.getElementById(n.id);
      if (existing.length > 0) {
        // 更新
        existing.data("label", _buildLabel(n));
        existing.data("name", n.name);
        existing.data("progress", n.progress);
        existing.data("status", n.status);
        existing.data("isCritical", n.is_critical);
        existing.data("resources", (n.resources || []).join(", "));
        existing.data("notes", n.notes || "");
        existing.data("tags", (n.tags || []).join(", "));
        existing.data("children", n.children || []);
        existing.data("is_group", n.is_group || false);
        // 更新 class
        "completed,in_progress,pending,delayed,blocked".split(",").forEach(function (s) { existing.removeClass("status-" + s); });
        existing.addClass("status-" + n.status);
        if (n.is_critical) existing.addClass("critical"); else existing.removeClass("critical");
        if (n.is_group) existing.addClass("is-group"); else existing.removeClass("is-group");
      } else {
        // 新增节点（带 fade-in）
        addNodeToGraph(n, cp);
      }
    });

    // 3. 更新已有边 / 添加新边
    edges.forEach(function (e) {
      var eid = e.source + "->" + e.target;
      if (_cy.getElementById(eid).length === 0) {
        var isCrit = cp[e.source] && cp[e.target];
        _cy.add({
          group: "edges",
          data: { id: eid, source: e.source, target: e.target },
          classes: isCrit ? "critical" : "",
        });
      }
    });

    // 4. 动画布局
    _doLayout(true);

    // 5. smooth fit with spring easing
    setTimeout(function () {
      if (_cy) _cy.animate({ fit: { eles: _cy.elements(), padding: 50 }, duration: 600, easing: "ease-in-out-cubic" });
    }, 500);
  }

  function addNodeToGraph(n, cp) {
    var node = _cy.add({
      group: "nodes",
      data: {
        id: n.id, label: _buildLabel(n), name: n.name, progress: n.progress,
        status: n.status, estimatedDays: n.estimated_days, confidence: n.confidence,
        isCritical: n.is_critical, floatDays: n.float_days,
        es: n.es, ef: n.ef, ls: n.ls, lf: n.lf,
        esDate: n.es_date, efDate: n.ef_date, lsDate: n.ls_date, lfDate: n.lf_date,
        resources: (n.resources || []).join(", "), notes: n.notes || "",
        tags: (n.tags || []).join(", "), children: n.children || [],
      },
      classes: ("status-" + n.status + (n.is_critical ? " critical" : "") + (n.is_group ? " is-group" : "")).trim(),
    });
    // smooth fade-in
    node.style("opacity", 0);
    node.animate({
      style: { opacity: 1 },
      duration: 500,
      easing: "ease-in-out-cubic",
    });
  }

  function updateNodeStatus(taskId, progress, status) {
    if (!_cy) return;
    var node = _cy.getElementById(taskId);
    if (node.length === 0) return;
    node.data("progress", progress);
    node.data("status", status);
    "completed,in_progress,pending,delayed,blocked".split(",").forEach(function (s) { node.removeClass("status-" + s); });
    node.addClass("status-" + status);
    node.data("label", _buildLabel({ name: node.data("name"), progress: progress }));
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
    if (data.tags) rows += "<tr><td>标签</td><td>" + data.tags + "</td></tr>";
    if (data.children) rows += "<tr><td>子节点</td><td>" + data.children + "</td></tr>";
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

  // 注入 tooltip 样式（液态玻璃风格）
  (function () {
    if (document.getElementById("dag-tooltip-style")) return;
    var s = document.createElement("style");
    s.id = "dag-tooltip-style";
    s.textContent = ".dag-tooltip{position:absolute;z-index:1000;background:rgba(17,17,48,0.9);backdrop-filter:blur(24px) saturate(180%);-webkit-backdrop-filter:blur(24px) saturate(180%);border:1px solid rgba(255,255,255,0.08);border-radius:14px;padding:12px 16px;box-shadow:0 8px 32px rgba(0,0,0,0.5),0 0 48px rgba(129,140,248,0.08);font-size:12px;pointer-events:none;min-width:180px;color:#e2e8f0;animation:tooltip-in 0.15s cubic-bezier(0.34,1.56,0.64,1)}.dag-tooltip strong{display:block;margin-bottom:6px;font-size:13px;background:linear-gradient(135deg,#c7d2fe,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}.dag-tooltip table{width:100%}.dag-tooltip td{padding:2px 0}.dag-tooltip td:first-child{color:#64748b;width:60px}";
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
