/* bePm — 主应用逻辑 */

(function () {
  var currentProjectId = null;
  var _lastAddNodeDesc = "";

  function $(sel) { return document.querySelector(sel); }

  function safeGet(sel, name) {
    const el = document.querySelector(sel);
    if (!el) console.error("[App] Element not found:", sel);
    return el;
  }

  // ---- 初始化 ----

  function init() {
    // DAG 初始化可能因 CDN 加载失败而报错，保持页面其余功能可用
    try {
      if (typeof DAGView !== "undefined" && DAGView.init) {
        DAGView.init("cy-container");
        console.log("[App] DAGView initialized");
      } else {
        console.warn("[App] DAGView not available — DAG visualization disabled");
      }
    } catch (e) {
      console.error("[App] DAGView init failed:", e.message);
      showToast("DAG 可视化组件加载失败，请检查网络连接（CDN）", true);
    }

    // WebSocket 处理
    try {
      if (typeof WSClient !== "undefined") {
        WSClient.on("node_status", (data) => {
          if (typeof DAGView !== "undefined") DAGView.updateNodeStatus(data.task_id, data.progress, data.status);
        });
        WSClient.on("risk_alert", (data) => {
          loadRiskPanel(currentProjectId);
          showToast((data.level === "critical" ? "严重风险" : "风险告警") + ": " + data.message);
        });
        WSClient.on("suggestion", (data) => {
          showToast("建议: " + data.message);
        });
      }
    } catch (e) {
      console.error("[App] WS init failed:", e);
    }

    // 后端健康检查
    try {
      if (typeof API !== "undefined") {
        API.health()
          .then(function(data) {
            console.log("[App] Backend connected");
            var statusEl = $("#sys-status");
            if (statusEl && data) {
              statusEl.style.color = "#16a34a";
              statusEl.textContent = "LLM: " + (data.llm_provider || "?").toUpperCase() + " / " + (data.llm_model || "?");
            }
          })
          .catch(function() { showToast("后端未连接，请确认服务已启动", true); });
      }
    } catch (e) {
      console.error("[App] API health check failed:", e);
    }

    // 文件上传
    const fileInput = $("#input-file");
    if (fileInput) {
      fileInput.addEventListener("change", async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        try {
          const text = await file.text();
          const descArea = $("#input-description");
          if (descArea && descArea.value.trim() === "") {
            descArea.value = text;
          } else if (descArea) {
            descArea.value += "\n\n--- 文件: " + file.name + " ---\n" + text;
          }
        } catch (err) {
          showToast("文件读取失败: " + err.message, true);
        }
      });
    }

    // 标记页面就绪
    var sysStatus = document.getElementById("sys-status");
    if (sysStatus) {
      sysStatus.textContent = "v15 ready";
      sysStatus.style.color = "#22c55e";
    }
    var statusEl = $("#create-status");
    if (statusEl) {
      statusEl.textContent = "页面已就绪，请输入项目描述";
      statusEl.className = "status-msg";
      setTimeout(function () { if (statusEl.textContent === "页面已就绪，请输入项目描述") statusEl.textContent = ""; }, 3000);
    }
  }

  // ---- Panel 切换 ----

  function showNewProjectPanel() {
    // 断开当前项目
    currentProjectId = null;
    try { WSClient.disconnect(); } catch (e) { /* ok */ }

    // 切换面板
    const panels = {
      newProj: $("#panel-new-project"),
      progress: $("#panel-progress"),
      list: $("#panel-project-list"),
      nodes: $("#panel-nodes"),
    };
    if (panels.newProj) panels.newProj.classList.remove("hidden");
    if (panels.progress) panels.progress.classList.add("hidden");
    if (panels.list) panels.list.classList.add("hidden");
    if (panels.nodes) panels.nodes.classList.add("hidden");

    // 清空新建表单
    const desc = $("#input-description");
    const deadline = $("#input-deadline");
    const additional = $("#input-additional");
    const file = $("#input-file");
    const status = $("#create-status");
    if (desc) desc.value = "";
    if (deadline) deadline.value = "";
    if (additional) additional.value = "";
    if (file) file.value = "";
    if (status) { status.textContent = ""; status.className = "status-msg"; }

    // 重置 DAG 区域
    if (typeof DAGView !== "undefined" && DAGView.clear) {
      DAGView.clear();
    }
    const titleEl = $("#dag-title");
    const infoEl = $("#dag-info");
    if (titleEl) titleEl.textContent = "新建项目排期";
    if (infoEl) infoEl.textContent = "";

    // 清空右侧面板
    const riskList = $("#risk-list");
    const statsContent = $("#stats-content");
    const bufferContent = $("#buffer-content");
    if (riskList) riskList.innerHTML = '<p class="placeholder-text">暂无风险数据</p>';
    if (statsContent) statsContent.innerHTML = '<p class="placeholder-text">暂无项目数据</p>';
    if (bufferContent) bufferContent.innerHTML = '<p class="placeholder-text">暂无缓冲数据</p>';

    if (desc) desc.focus();
  }

  function showProgressPanel() {
    if (!currentProjectId) {
      showToast("请先创建一个项目", true);
      return;
    }
    const panels = {
      newProj: $("#panel-new-project"),
      progress: $("#panel-progress"),
      list: $("#panel-project-list"),
    };
    if (panels.newProj) panels.newProj.classList.add("hidden");
    if (panels.progress) panels.progress.classList.remove("hidden");
    if (panels.list) panels.list.classList.add("hidden");
    const prog = $("#input-progress");
    if (prog) prog.focus();
  }

  async function loadProjectList() {
    ["panel-new-project", "panel-progress"].forEach((id) => {
      const el = $("#" + id);
      if (el) el.classList.add("hidden");
    });
    const listPanel = $("#panel-project-list");
    if (listPanel) listPanel.classList.remove("hidden");

    const container = $("#project-list");
    if (!container) return;
    container.innerHTML = '<p class="placeholder-text">加载中...</p>';

    try {
      const data = await API.listProjects();
      const projects = data.projects || [];
      if (projects.length === 0) {
        container.innerHTML = '<p class="placeholder-text">暂无项目，点击"+ 新建项目"开始</p>';
        return;
      }
      container.innerHTML = projects.map((p) => {
        const badges = [];
        if (p.critical_risk_count > 0) badges.push('<span class="badge badge-danger">' + p.critical_risk_count + ' 严重</span>');
        const warnCount = p.risk_count - p.critical_risk_count;
        if (warnCount > 0) badges.push('<span class="badge badge-warning">' + warnCount + ' 警告</span>');
        return '<div class="project-item" onclick="app.openProject(\'' + p.id + '\')">'
          + '<div class="proj-name">' + escapeHtml(p.name) + '</div>'
          + '<div class="proj-meta">' + p.node_count + ' 个任务 | 截止 ' + (p.deadline || "未设置") + ' | ' + fmtDate(p.updated_at) + '</div>'
          + '<div class="proj-risks">' + badges.join(" ") + '</div>'
          + '</div>';
      }).join("");
    } catch (err) {
      container.innerHTML = '<p class="placeholder-text" style="color:#ef4444">加载失败: ' + escapeHtml(err.message) + '</p>';
    }
  }

  // ---- 核心操作 ----

  async function createSchedule() {
    const descEl = $("#input-description");
    const deadlineEl = $("#input-deadline");
    const addlEl = $("#input-additional");
    const fileEl = $("#input-file");

    if (!descEl) { showToast("页面加载异常，请刷新重试", true); return; }

    const description = descEl.value.trim();
    const deadline = deadlineEl ? deadlineEl.value : "";
    const additionalInfo = addlEl ? addlEl.value.trim() : "";
    let fileText = null;

    if (fileEl && fileEl.files.length > 0) {
      try { fileText = await fileEl.files[0].text(); }
      catch (err) { showToast("文件读取失败: " + err.message, true); return; }
    }

    if (!description && !fileText) {
      showCreateStatus("请输入项目描述或上传文件", "error");
      return;
    }

    showCreateStatus("正在理解项目并规划排期 \u{1F4CB}", "loading");

    try {
      if (typeof API === "undefined") throw new Error("API module not loaded");

      const data = await API.createProject({
        description,
        deadline,
        additional_info: additionalInfo,
        file_text: fileText,
      });

      const project = data.project;
      currentProjectId = project.id;

      // 渲染 DAG
      await loadAndRenderGraph(project.id);

      // 渲染面板
      renderRiskPanel(project.risks || []);
      renderStats(project);
      renderBuffer(project.buffer, project.schedule);
      renderNodeList(project);
      showNodePanel();

      // 更新标题
      const titleEl = $("#dag-title");
      const infoEl = $("#dag-info");
      if (titleEl) titleEl.textContent = project.name;
      const cpLen = (project.schedule && project.schedule.critical_path) ? project.schedule.critical_path.length : 0;
      const totalDays = (project.schedule && project.schedule.total_duration_days) ? project.schedule.total_duration_days : 0;
      if (infoEl) infoEl.textContent = "关键路径 " + cpLen + " 节点 | " + totalDays + " 天";

      // WebSocket
      try { WSClient.connect(project.id); } catch (e) { /* ok */ }

      showProgressPanel();
      showCreateStatus("排期完成 ✨ 项目 " + project.id + " 已就绪", "success");
    } catch (err) {
      console.error("[App] createSchedule error:", err);
      showCreateStatus("排期失败: " + err.message, "error");
    }
  }

  async function updateProgress() {
    if (!currentProjectId) {
      showProgressStatus("请先创建或选择一个项目", "error");
      return;
    }
    const progEl = $("#input-progress");
    if (!progEl) return;
    const progressText = progEl.value.trim();
    if (!progressText) {
      showProgressStatus("请输入进展描述", "error");
      return;
    }

    showProgressStatus("正在 AI 分析进展...", "loading");

    try {
      const data = await API.updateProgress(currentProjectId, progressText);
      const project = data.project;

      // 更新 DAG 节点状态
      if (data.updated_nodes && typeof DAGView !== "undefined") {
        for (const tid of data.updated_nodes) {
          const node = project.nodes.find((n) => n.id === tid);
          if (node) DAGView.updateNodeStatus(tid, node.progress, node.status);
        }
      }

      // 更新关键路径
      if (project.schedule && project.schedule.critical_path && typeof DAGView !== "undefined") {
        DAGView.highlightCriticalPath(project.schedule.critical_path);
      }

      // 刷新面板
      renderRiskPanel(project.risks || []);
      renderStats(project);
      renderBuffer(project.buffer, project.schedule);

      // 标题
      const infoEl = $("#dag-info");
      if (infoEl && project.schedule) {
        infoEl.textContent = "关键路径 " + (project.schedule.critical_path || []).length + " 节点 | " + (project.schedule.total_duration_days || 0) + " 天";
      }

      progEl.value = "";
      const riskCount = (data.new_risks || []).length;
      const updatedCount = (data.updated_nodes || []).length;
      showProgressStatus("已更新 " + updatedCount + " 个节点 | 发现 " + riskCount + " 项风险", "success");
    } catch (err) {
      console.error("[App] updateProgress error:", err);
      showProgressStatus("更新失败: " + err.message, "error");
    }
  }

  async function openProject(projectId) {
    try {
      const data = await API.getProject(projectId);
      const project = data.project;
      currentProjectId = projectId;

      await loadAndRenderGraph(projectId);
      renderRiskPanel(project.risks || []);
      renderStats(project);
      renderBuffer(project.buffer, project.schedule);
      renderNodeList(project);
      showNodePanel();

      const titleEl = $("#dag-title");
      const infoEl = $("#dag-info");
      if (titleEl) titleEl.textContent = project.name;
      if (infoEl && project.schedule) {
        infoEl.textContent = "关键路径 " + (project.schedule.critical_path || []).length + " 节点 | " + (project.schedule.total_duration_days || 0) + " 天";
      }

      try { WSClient.connect(projectId); } catch (e) { /* ok */ }
      showProgressPanel();
      showToast("已打开: " + project.name);
    } catch (err) {
      showToast("打开失败: " + err.message, true);
    }
  }

  // ---- 图加载 ----

  async function loadAndRenderGraph(projectId) {
    try {
      if (typeof DAGView === "undefined") {
        console.warn("[App] DAGView not available, skipping DAG render");
        return;
      }
      const graphData = await API.getGraph(projectId);
      DAGView.render(graphData);
      setTimeout(() => DAGView.fit(), 400);
    } catch (err) {
      console.error("[App] Graph render failed:", err);
    }
  }

  // ---- 面板渲染 ----

  function renderRiskPanel(risks) {
    const container = $("#risk-list");
    if (!container) return;
    if (!risks || risks.length === 0) {
      container.innerHTML = '<p class="placeholder-text" style="color:#22c55e">暂无风险</p>';
      return;
    }
    container.innerHTML = risks.map((r) =>
      '<div class="risk-item ' + r.level + '">'
      + '<div class="risk-dim">' + escapeHtml(r.dimension) + '</div>'
      + '<div class="risk-msg">' + escapeHtml(r.message) + '</div>'
      + (r.suggestion ? '<div class="risk-sug">' + escapeHtml(r.suggestion) + '</div>' : "")
      + (r.task_id ? '<div class="risk-sug" style="margin-top:2px;font-size:10px;">关联任务: ' + escapeHtml(r.task_id) + '</div>' : "")
      + '</div>'
    ).join("");
  }

  function renderStats(project) {
    const container = $("#stats-content");
    if (!container) return;
    const nodes = project.nodes || [];
    const completed = nodes.filter((n) => n.status === "completed").length;
    const inProgress = nodes.filter((n) => n.status === "in_progress").length;
    const delayed = nodes.filter((n) => n.status === "delayed").length;
    const blocked = nodes.filter((n) => n.status === "blocked").length;
    const totalDays = (project.schedule && project.schedule.total_duration_days) ? project.schedule.total_duration_days : 0;
    const criticalCount = (project.schedule && project.schedule.critical_path) ? project.schedule.critical_path.length : 0;

    container.innerHTML =
      '<div class="stat-row"><span class="stat-label">总任务数</span><span class="stat-value">' + nodes.length + '</span></div>'
      + '<div class="stat-row"><span class="stat-label">已完成</span><span class="stat-value" style="color:#22c55e">' + completed + '</span></div>'
      + '<div class="stat-row"><span class="stat-label">进行中</span><span class="stat-value" style="color:#3b82f6">' + inProgress + '</span></div>'
      + '<div class="stat-row"><span class="stat-label">延迟</span><span class="stat-value" style="color:#ef4444">' + delayed + '</span></div>'
      + '<div class="stat-row"><span class="stat-label">阻塞</span><span class="stat-value" style="color:#f59e0b">' + blocked + '</span></div>'
      + '<div class="stat-row"><span class="stat-label">总工期</span><span class="stat-value">' + totalDays + ' 天</span></div>'
      + '<div class="stat-row"><span class="stat-label">关键路径节点</span><span class="stat-value">' + criticalCount + '</span></div>'
      + '<div class="stat-row"><span class="stat-label">项目 ID</span><span class="stat-value" style="font-family:monospace;font-size:11px">' + project.id + '</span></div>';
  }

  function renderBuffer(buffer, schedule) {
    const container = $("#buffer-content");
    if (!container) return;
    if (!buffer) {
      container.innerHTML = '<p class="placeholder-text">暂无缓冲数据</p>';
      return;
    }
    const ratio = buffer.ratio || 0;
    const pct = (ratio * 100).toFixed(1);
    const total = buffer.total_days || 0;
    const consumed = buffer.consumed_days || 0;
    const remaining = buffer.remaining_days || 0;
    const status = buffer.status || "green";
    const statusText = { green: "健康", yellow: "关注", red: "危险" }[status] || status;
    const emoji = { green: "\\u2705", yellow: "\\u26a0\\ufe0f", red: "\\ud83d\\udd34" }[status] || "";

    container.innerHTML =
      '<div style="display:flex;justify-content:space-between;margin-bottom:4px">'
      + '<span style="font-size:12px">' + emoji + ' ' + statusText + '</span>'
      + '<span style="font-size:12px;font-weight:600">' + pct + '%</span>'
      + '</div>'
      + '<div class="buffer-bar"><div class="buffer-bar-fill ' + status + '" style="width:' + Math.min(ratio * 100, 100) + '%"></div></div>'
      + '<div style="font-size:11px;color:#64748b;margin-top:4px">总量 ' + total + ' 天 | 已消耗 ' + consumed + ' 天 | 剩余 ' + remaining + ' 天</div>'
      + (schedule ? '<div style="font-size:11px;color:#64748b;margin-top:2px">项目总工期 ' + schedule.total_duration_days + ' 天</div>' : "");
  }

  async function loadRiskPanel(projectId) {
    try {
      const graphData = await API.getGraph(projectId);
      renderRiskPanel(graphData.risks || []);
      renderBuffer(graphData.buffer, graphData.schedule);
    } catch (err) { /* silent */ }
  }

  // ---- 状态提示 ----

  function showCreateStatus(msg, type) {
    const el = $("#create-status");
    if (!el) return;
    el.textContent = msg;
    el.className = "status-msg " + (type || "");
  }

  function showProgressStatus(msg, type) {
    var el = document.getElementById("progress-status");
    if (!el) return;
    el.textContent = msg;
    el.className = "status-msg " + (type || "");
    if (type === "success") {
      setTimeout(function () { el.textContent = ""; el.className = "status-msg"; }, 5000);
    }
  }

  // ---- Toast ----

  function showToast(msg, isError) {
    if (isError === undefined) isError = false;
    let toast = document.getElementById("app-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "app-toast";
      toast.style.cssText = "position:fixed;bottom:20px;right:20px;z-index:9999;padding:10px 20px;border-radius:8px;font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,0.2);transition:opacity 0.3s;max-width:400px;";
      document.body.appendChild(toast);
    }
    toast.style.background = isError ? "#fef2f2" : "#f0fdf4";
    toast.style.color = isError ? "#ef4444" : "#16a34a";
    toast.style.border = "1px solid " + (isError ? "#ef4444" : "#22c55e");
    toast.textContent = msg;
    toast.style.opacity = "1";
    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(function () { toast.style.opacity = "0"; }, 4000);
  }

  // ---- 工具 ----

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function fmtDate(isoStr) {
    if (!isoStr) return "";
    var d = new Date(isoStr);
    try { return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
    catch (e) { return isoStr.slice(0, 16); }
  }

  // ---- 节点管理 ----

  var _editingNodeId = null;
  var _deletingNodeId = null;

  function showNodePanel() {
    var p = document.getElementById("panel-nodes");
    if (p) p.classList.remove("hidden");
  }

  function toggleManualAdd() {
    var form = document.getElementById("manual-add-form");
    if (form) form.classList.toggle("hidden");
  }

  // 暂存计划，用于确认后直接执行
  var _pendingOps = null;
  var _loadingTimer = null;
  var _loadingMessages = [
    "正在阅读项目结构 \u{1F4D6}",
    "正在琢磨依赖关系 \u{1F9E9}",
    "正在思考怎么安排 \u{1F4A1}",
    "正在规划任务位置 \u{1F4CD}",
    "马上就好 \u{2728}",
  ];

  function _startLoading(targetId) {
    targetId = targetId || "progress-status";
    var el = document.getElementById(targetId);
    if (!el) return;
    var i = 0;
    el.textContent = _loadingMessages[0];
    el.className = "status-msg loading";
    _loadingTimer = setInterval(function () {
      i = (i + 1) % _loadingMessages.length;
      el.textContent = _loadingMessages[i];
    }, 2000);
  }

  function _stopLoading(stage) {
    if (_loadingTimer) { clearInterval(_loadingTimer); _loadingTimer = null; }
  }

  async function confirmPlan() {
    if (!_pendingOps || !currentProjectId) return;
    var progressEl = document.getElementById("progress-status");
    if (progressEl) { progressEl.textContent = "正在执行变更 ✨"; progressEl.className = "status-msg loading"; }
    try {
      var progInput = document.getElementById("input-progress");
      var descText = progInput ? progInput.value.trim() : "确认执行计划";
      var data = await API.command(currentProjectId, {
        description: descText,
        confirmed: true,
        ops_to_execute: _pendingOps,
      });
      _pendingOps = null;
      if (progInput) progInput.value = "";
      if (progressEl) { progressEl.textContent = "搞定啦 ✅"; progressEl.className = "status-msg success"; }
      await refreshAfterNodeChange(data.project);
      if (progressEl) { setTimeout(function(){ progressEl.textContent = ""; progressEl.className = "status-msg"; }, 3000); }
    } catch (e) {
      console.error("confirmPlan error:", e);
      if (progressEl) { progressEl.textContent = "执行失败: " + e.message; progressEl.className = "status-msg error"; }
    }
  }

  function cancelPlan() {
    _pendingOps = null;
    var el = document.getElementById("add-node-status");
    if (el) { el.textContent = ""; el.className = "status-msg"; }
  }

  // ---- 统一入口：提交任何 NL 输入（进展更新 + 节点变更 + 边变更） ----
  async function submitProgress(optText) {
    var desc = optText || "";
    if (!desc) {
      var txt = document.getElementById("input-progress");
      desc = txt ? txt.value.trim() : "";
    }
    if (!desc) { showProgressStatus("请输入进展或变更描述", "error"); return; }
    if (!currentProjectId) { showToast("请先打开项目", true); return; }
    _startLoading();
    try {
      var data = await API.command(currentProjectId, { description: desc });
      _stopLoading(data.stage);
      await _handleProgressResponse(data, txt);
    } catch (e) {
      _stopLoading();
      showProgressStatus("出错了: " + e.message, "error");
    }
  }

  async function _handleProgressResponse(data, inputEl) {
    // 确认计划
    if (data.action === "confirm_plan") {
      _pendingOps = data.ops_summary;
      var planId = "plan-" + Date.now();
      var html = "<div style=\"margin-top:8px;padding:10px;background:#f0fdf4;border:1px solid #22c55e;border-radius:6px;font-size:12px\"><strong>计划:</strong><br><pre style=\"margin:4px 0;white-space:pre-wrap;font-family:inherit\">" + escapeHtml(data.plan) + "</pre>"
        + "<button style=\"margin-top:6px;padding:4px 12px;background:#22c55e;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px\" onclick=\"app.confirmPlan()\">确认执行</button> "
        + "<button style=\"margin-top:6px;padding:4px 12px;background:#6b7280;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px\" onclick=\"app.cancelPlan()\">取消</button>"
        + "<div style=\"margin-top:6px;display:flex;gap:4px\"><input type=\"text\" id=\"" + planId + "\" placeholder=\"或者说明怎么改...\" style=\"flex:1;font-size:12px;padding:4px 8px;border:1px solid #d1d5db;border-radius:4px\">"
        + "<button style=\"font-size:12px;padding:4px 8px;background:#3b82f6;color:#fff;border:none;border-radius:4px;cursor:pointer\" onclick=\"app.cancelPlan();var v=document.getElementById('" + planId + "').value.trim();if(v){app.submitProgress(v)}\">修改</button></div></div>";
      var psEl = document.getElementById("progress-status");
      if (psEl) { psEl.innerHTML = html; psEl.className = "status-msg success"; }
      return;
    }
    // AI 反问
    if (data.action === "ask") {
      var askId = "ask-" + Date.now();
      var html = "<div style=\"margin-top:8px;padding:10px;background:#fffbeb;border:1px solid #f59e0b;border-radius:6px;font-size:13px\"><strong>AI:</strong><br>" + escapeHtml(data.question);
      if (data.options && data.options.length > 0) {
        html += "<div style=\"margin-top:6px\">" + data.options.map(function(o) { return "<button style=\"margin:2px;font-size:11px;padding:3px 8px;cursor:pointer\" onclick=\"app.submitProgressFollowup('" + escapeHtml(o).replace(/'/g,"\\'") + "')\">" + escapeHtml(o) + "</button>"; }).join("") + "</div>";
      }
      html += "<div style=\"margin-top:8px;display:flex;gap:4px\"><input type=\"text\" id=\"" + askId + "\" placeholder=\"或者输入你的回答...\" style=\"flex:1;font-size:12px;padding:4px 8px;border:1px solid #d1d5db;border-radius:4px\">"
        + "<button style=\"font-size:12px;padding:4px 8px;background:#3b82f6;color:#fff;border:none;border-radius:4px;cursor:pointer;white-space:nowrap\" onclick=\"var v=document.getElementById('" + askId + "').value.trim();if(v)app.submitProgressFollowup(v)\">发送</button></div></div>";
      var psEl = document.getElementById("progress-status");
      if (psEl) { psEl.innerHTML = html; psEl.className = "status-msg warning"; }
      return;
    }
    // 成功
    if (inputEl) inputEl.value = "";
    showProgressStatus("搞定啦 ✅", "success");
    await refreshAfterNodeChange(data.project);
  }

  // 跟进行动（点击选项或输入自定义回复，不回写到输入框）
  async function submitProgressFollowup(text) {
    if (!currentProjectId) return;
    await submitProgress(text);
  }

  // ---- 原 addNodeNL（保留用于向后兼容，内部转发到统一入口） ----
  async function addNodeNL(optFollowUp) {
    var txt = document.getElementById("input-add-node");
    var desc = optFollowUp || (txt ? txt.value.trim() : "");
    if (!desc) {
      showAddNodeStatus("请输入任务描述", "error");
      return;
    }
    if (!currentProjectId) { showToast("请先打开项目", true); return; }

    // 构建请求体
    var reqBody = { description: desc };
    // 如果是确认执行，带上 confirmed 标志
    if (optFollowUp && optFollowUp === _lastAddNodeDesc) {
      reqBody.confirmed = true;
    }

    _startLoading();
    try {
      var data = await API.command(currentProjectId, reqBody);
      _stopLoading(data.stage);

      // 处理确认计划
      if (data.action === "confirm_plan") {
        _pendingOps = data.ops_summary;
        var planId = 'plan-' + Date.now();
        var planHtml = '<div style="margin-top:8px;padding:10px;background:#f0fdf4;border:1px solid #22c55e;border-radius:6px;font-size:12px">'
          + '<strong>计划（需确认）:</strong><br><pre style="margin:4px 0;white-space:pre-wrap;font-family:inherit">' + escapeHtml(data.plan) + '</pre>'
          + '<button style="margin-top:6px;padding:4px 12px;background:#22c55e;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px" onclick="app.confirmPlan()">确认执行</button> '
          + '<button style="margin-top:6px;padding:4px 12px;background:#6b7280;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px" onclick="app.cancelPlan()">取消</button>'
          + '<div style="margin-top:6px;display:flex;gap:4px">'
          + '<input type="text" id="' + planId + '" placeholder="或者说明要怎么改..." style="flex:1;font-size:12px;padding:4px 8px;border:1px solid #d1d5db;border-radius:4px">'
          + '<button style="font-size:12px;padding:4px 8px;background:#3b82f6;color:#fff;border:none;border-radius:4px;cursor:pointer;white-space:nowrap" onclick="app.cancelPlan();var v=document.getElementById(\'' + planId + '\').value.trim();if(v){document.getElementById(\'input-add-node\').value=v;app.addNodeNL()}">修改</button>'
          + '</div>'
          + '</div>';
        var asEl = document.getElementById("add-node-status");
        if (asEl) { asEl.innerHTML = planHtml; asEl.className = "status-msg success"; }
        return;
      }

  // 处理 AI 反问
  if (data.action === "ask") {
        _lastAddNodeDesc = desc;
        var askId = 'ask-' + Date.now();
        var questionHtml = '<div style="margin-top:8px;padding:10px;background:#fffbeb;border:1px solid #f59e0b;border-radius:6px;font-size:13px">'
          + '<strong>AI 需要确认:</strong><br>' + escapeHtml(data.question);
        if (data.options && data.options.length > 0) {
          questionHtml += '<div style="margin-top:6px">'
            + data.options.map(function(o, i) {
                return '<button style="margin:2px;font-size:11px;padding:3px 8px;cursor:pointer" onclick="app.addNodeNL(\'' + escapeHtml(o).replace(/'/g, "\\'") + '\')">' + escapeHtml(o) + '</button>';
              }).join("")
            + '</div>';
        }
        questionHtml += '<div style="margin-top:8px;display:flex;gap:4px">'
          + '<input type="text" id="' + askId + '" placeholder="或者输入你的回答..." style="flex:1;font-size:12px;padding:4px 8px;border:1px solid #d1d5db;border-radius:4px">'
          + '<button style="font-size:12px;padding:4px 8px;background:#3b82f6;color:#fff;border:none;border-radius:4px;cursor:pointer;white-space:nowrap" onclick="var v=document.getElementById(\'' + askId + '\').value.trim();if(v)app.addNodeNL(v)">发送</button>'
          + '</div>'
          + '</div>';
        var asEl = document.getElementById("add-node-status");
        if (asEl) { asEl.innerHTML = questionHtml; asEl.className = "status-msg warning"; }
        return;
      }

      // 成功
      _lastAddNodeDesc = "";
      if (txt) txt.value = "";
      showAddNodeStatus("搞定啦 " + data.new_node_id + " 已就位 ✅", "success");
      await refreshAfterNodeChange(data.project);
    } catch (e) {
      showAddNodeStatus("啊哦，出错了: " + e.message, "error");
    }
  }

  async function addNodeManual() {
    var name = document.getElementById("input-node-name");
    var days = document.getElementById("input-node-days");
    var deps = document.getElementById("input-node-deps");
    if (!name || !name.value.trim() || !days || !days.value.trim()) {
      showAddNodeStatus("请填写名称和工期", "error");
      return;
    }
    if (!currentProjectId) { showToast("请先打开项目", true); return; }
    showAddNodeStatus("手动添加中 \u{1F4DD}", "loading");
    try {
      var depList = deps && deps.value.trim() ? deps.value.split(",").map(function(s){return s.trim();}) : [];
      var data = await API.command(currentProjectId, {
        name: name.value.trim(),
        estimated_days: parseFloat(days.value),
        pre_dependencies: depList,
      });
      name.value = ""; days.value = ""; if (deps) deps.value = "";
      showAddNodeStatus("搞定啦 " + data.new_node_id + " 已就位 ✅", "success");
      await refreshAfterNodeChange(data.project);
    } catch (e) {
      showAddNodeStatus("啊哦，出错了: " + e.message, "error");
    }
  }

  function showAddNodeStatus(msg, type) {
    var el = document.getElementById("add-node-status");
    if (!el) return;
    el.textContent = msg;
    el.className = "status-msg " + (type || "");
    if (type === "success") {
      setTimeout(function(){ el.textContent = ""; el.className = "status-msg"; }, 5000);
    }
  }

  function renderNodeList(project) {
    var container = document.getElementById("node-list");
    if (!container) return;
    var nodes = project.nodes || [];
    var cpSet = {};
    if (project.schedule && project.schedule.critical_path) {
      project.schedule.critical_path.forEach(function(nid){cpSet[nid]=true;});
    }
    container.innerHTML = nodes.map(function(n){
      return '<div class="node-list-item">'
        + '<span class="nl-name' + (cpSet[n.id] ? ' critical-node' : '') + '" title="' + escapeHtml(n.name) + ' ' + n.estimated_days + 'd">'
        + (cpSet[n.id] ? '🔴 ' : '') + escapeHtml(n.name) + ' (' + n.estimated_days + 'd)</span>'
        + '<button class="node-btn-edit" onclick="app.openEditModal(\'' + n.id + '\')">编辑</button>'
        + '<button class="node-btn-del" onclick="app.openDeleteModal(\'' + n.id + '\')">删除</button>'
        + '</div>';
    }).join("");
  }

  // ---- 编辑弹窗 ----

  function openEditModal(nodeId) {
    if (!currentProjectId) return;
    _editingNodeId = nodeId;
    API.getProject(currentProjectId).then(function(data){
      var p = data.project;
      var node = p.nodes.find(function(n){return n.id===nodeId;});
      if (!node) return;
      document.getElementById("edit-node-name").value = node.name || "";
      document.getElementById("edit-node-days").value = node.estimated_days || 1;
      document.getElementById("edit-node-confidence").value = node.confidence || 0.8;
      document.getElementById("edit-node-resources").value = (node.resources||[]).join(", ");
      document.getElementById("edit-node-notes").value = node.notes || "";

      // 依赖勾选列表
      var depsHtml = p.nodes.filter(function(n){return n.id!==nodeId;}).map(function(n){
        var checked = (node.pre_dependencies||[]).indexOf(n.id) >= 0 ? " checked" : "";
        return '<label class="dep-checkbox"><input type="checkbox" value="' + n.id + '"' + checked + '>' + escapeHtml(n.name) + '</label>';
      }).join("");
      document.getElementById("edit-node-deps-list").innerHTML = depsHtml || '<span style="color:#64748b;font-size:12px">无其他节点</span>';
      document.getElementById("modal-edit-node").classList.remove("hidden");
    });
  }

  function closeEditModal() {
    document.getElementById("modal-edit-node").classList.add("hidden");
    _editingNodeId = null;
  }

  async function saveEditNode() {
    if (!_editingNodeId || !currentProjectId) return;
    var name = document.getElementById("edit-node-name").value.trim();
    var days = parseFloat(document.getElementById("edit-node-days").value) || 1;
    var conf = parseFloat(document.getElementById("edit-node-confidence").value) || 0.8;
    var resources = document.getElementById("edit-node-resources").value.split(",").map(function(s){return s.trim();}).filter(Boolean);
    var notes = document.getElementById("edit-node-notes").value.trim();

    // 收集勾选的依赖
    var checks = document.querySelectorAll("#edit-node-deps-list input[type=checkbox]");
    var deps = [];
    checks.forEach(function(cb){ if (cb.checked) deps.push(cb.value); });

    try {
      var data = await API.editNode(currentProjectId, _editingNodeId, {
        name: name, estimated_days: days, confidence: conf,
        resources: resources, pre_dependencies: deps, notes: notes,
      });
      closeEditModal();
      await refreshAfterNodeChange(data.project);
      showToast("节点已更新");
    } catch (e) {
      showToast("更新失败: " + e.message, true);
    }
  }

  // ---- 删除弹窗 ----

  function openDeleteModal(nodeId) {
    if (!currentProjectId) return;
    _deletingNodeId = nodeId;
    // 查找节点和下游
    API.getProject(currentProjectId).then(function(data){
      var p = data.project;
      var node = p.nodes.find(function(n){return n.id===nodeId;});
      if (!node) return;
      document.getElementById("delete-node-name").textContent = node.name;
      var downstream = p.nodes.filter(function(n){return (n.pre_dependencies||[]).indexOf(nodeId)>=0;});
      var info = document.getElementById("delete-affected-info");
      if (downstream.length > 0) {
        info.innerHTML = '<p style="color:#ef4444;font-size:13px">以下 ' + downstream.length + ' 个节点依赖此任务，删除后将自动清除依赖关系：</p>'
          + downstream.map(function(n){return '<div style="font-size:12px;padding:2px 0"> ' + escapeHtml(n.name) + '</div>';}).join("");
      } else {
        info.innerHTML = '<p style="color:#64748b;font-size:13px">没有其他节点依赖此任务，可以安全删除。</p>';
      }
      document.getElementById("modal-delete-node").classList.remove("hidden");
    });
  }

  function closeDeleteModal() {
    document.getElementById("modal-delete-node").classList.add("hidden");
    _deletingNodeId = null;
  }

  async function confirmDeleteNode() {
    if (!_deletingNodeId || !currentProjectId) return;
    try {
      var data = await API.deleteNode(currentProjectId, _deletingNodeId);
      closeDeleteModal();
      await refreshAfterNodeChange(data.project);
      showToast("已删除 " + data.deleted_name);
    } catch (e) {
      showToast("删除失败: " + e.message, true);
    }
  }

  // ---- 刷新 ----

  async function refreshAfterNodeChange(project) {
    renderNodeList(project);
    renderRiskPanel(project.risks || []);
    renderStats(project);
    renderBuffer(project.buffer, project.schedule);

    // 更新 DAG
    if (typeof DAGView !== "undefined") {
      await loadAndRenderGraph(project.id);
    }

    // 更新标题
    var infoEl = document.getElementById("dag-info");
    if (infoEl && project.schedule) {
      infoEl.textContent = "KP " + (project.schedule.critical_path||[]).length + " nodes | " + (project.schedule.total_duration_days||0) + "d";
    }
  }

  // ---- 公开 API ----

  window.app = {
    init: init,
    showNewProjectPanel: showNewProjectPanel,
    showProgressPanel: showProgressPanel,
    loadProjectList: loadProjectList,
    createSchedule: createSchedule,
    updateProgress: updateProgress,
    openProject: openProject,
    // 节点管理
    submitProgress: submitProgress,
    submitProgressFollowup: submitProgressFollowup,
    addNodeNL: addNodeNL,
    addNodeManual: addNodeManual,
    toggleManualAdd: toggleManualAdd,
    openEditModal: openEditModal,
    closeEditModal: closeEditModal,
    saveEditNode: saveEditNode,
    openDeleteModal: openDeleteModal,
    closeDeleteModal: closeDeleteModal,
    confirmDeleteNode: confirmDeleteNode,
    confirmPlan: confirmPlan,
    cancelPlan: cancelPlan,
  };

})();

document.addEventListener("DOMContentLoaded", function () {
  try {
    if (window.app && window.app.init) {
      window.app.init();
    }
  } catch (e) {
    console.error("[App] Init error:", e);
    var el = document.getElementById("create-status");
    if (el) {
      el.textContent = "Init failed: " + e.message;
      el.className = "status-msg error";
    }
  }
});
