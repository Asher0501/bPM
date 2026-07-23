/* bePm — REST API 封装 */

const API = (() => {
  const BASE = window.location.origin;

  async function request(method, path, body = null) {
    const opts = {
      method,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
      },
    };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch(BASE + path, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  return {
    // 项目 CRUD
    listProjects()     { return request("GET", "/api/projects"); },
    getProject(id)     { return request("GET", `/api/projects/${id}`); },
    createProject(data){ return request("POST", "/api/projects", data); },
    deleteProject(id)  { return request("DELETE", `/api/projects/${id}`); },

    // 排期 & 进展
    reSchedule(id)     { return request("POST", `/api/projects/${id}/schedule`); },
    updateProgress(id, progressText) {
      return request("POST", `/api/projects/${id}/progress`, { progress_text: progressText });
    },

    // 图数据
    getGraph(id)       { return request("GET", `/api/projects/${id}/graph`); },

    // 编辑节点
    editTask(projectId, taskId, updates) {
      return request("PUT", `/api/projects/${projectId}/task/${taskId}`, updates);
    },
    editNode(projectId, nodeId, updates) {
      return request("PUT", `/api/projects/${projectId}/nodes/${nodeId}`, updates);
    },

    // 节点 CRUD
    command(projectId, data) {
      return request("POST", `/api/projects/${projectId}/command`, data);
    },
    deleteNode(projectId, nodeId) {
      return request("DELETE", `/api/projects/${projectId}/nodes/${nodeId}`);
    },

    // 边 CRUD
    listEdges(projectId) {
      return request("GET", `/api/projects/${projectId}/edges`);
    },
    addEdge(projectId, source, target) {
      return request("POST", `/api/projects/${projectId}/edges`, {source: source, target: target});
    },
    deleteEdge(projectId, source, target) {
      return request("DELETE", `/api/projects/${projectId}/edges/${source}/${target}`);
    },

    // 健康检查
    health()           { return request("GET", "/api/health"); },
  };
})();
