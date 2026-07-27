# -*- coding: utf-8 -*-
"""回归看护测试 — 针对已修复的 10 个缺陷，确保不再复发。

每个测试函数注释标注了它看护的 Issue 编号和根因。
"""

import json
import os
import re
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import req, create_test_project, assert_valid_graph, BASE


# ═══════════════════════════════════════════════════════════════════════
# Issue 1: map_intent_to_ops 未导入 → command 端点 500
# Issue 10: 旧项目数据损坏 → 所有节点 Float 相同、CP=0
# ═══════════════════════════════════════════════════════════════════════

class TestIssue1_MissingImport:
    """看护 Issue #1: map_intent_to_ops 必须可导入且 NL command 不 500"""

    def test_map_intent_to_ops_importable(self):
        """map_intent_to_ops 可从 engine.intents 导入"""
        from engine.intents import map_intent_to_ops
        assert callable(map_intent_to_ops)

    def test_structural_risk_scan_importable(self):
        """structural_risk_scan 可从 engine.scheduler 导入"""
        from engine.scheduler import structural_risk_scan
        assert callable(structural_risk_scan)

    def test_command_nl_no_500(self, tmp_project):
        """NL command 调用不抛 NameError / 500"""
        pid, _ = tmp_project
        s, d = req("POST", f"/api/projects/{pid}/command", {
            "description": "Add a task for code review, 2 days."
        })
        # 可能 200（LLM 解析成功）或 400（LLM 返回 ask/confirm_plan）
        # 但不应该 500
        assert s != 500, f"Command returned 500: {d}"
        assert s < 500, f"Command returned {s}: {d}"


# ═══════════════════════════════════════════════════════════════════════
# Issue 2: not_found_handler re-raise → favicon/非API 500
# Issue 9: 根路径 / 无 Cache-Control
# ═══════════════════════════════════════════════════════════════════════

class TestIssue2_NotFoundHandler:
    """看护 Issue #2: 非 API 404 不返回 500"""

    def test_favicon_returns_404_not_500(self):
        """GET /favicon.ico 返回 404，不是 500"""
        import urllib.request, urllib.error
        try:
            r = urllib.request.Request(f"{BASE}/favicon.ico")
            with urllib.request.urlopen(r, timeout=5):
                pass
        except urllib.error.HTTPError as e:
            assert e.code != 500, f"favicon.ico returned 500"
            assert e.code == 404, f"Expected 404, got {e.code}"

    def test_random_page_returns_404_not_500(self):
        """GET /random-nonexistent-page 返回文本 404，不是 500"""
        s, d = req("GET", "/random-nonexistent-page")
        assert s != 500, f"Random page returned 500"
        assert s == 404, f"Expected 404, got {s}"

    def test_api_404_returns_json(self):
        """GET /api/nonexistent 返回 JSON 404"""
        s, d = req("GET", "/api/nonexistent-endpoint-xyz")
        assert s == 404
        # req() 将 error body 放在 d["error"] 中，detail 在 JSON 字符串内部
        error_text = str(d.get("error", "")) + str(d.get("detail", ""))
        assert "detail" in error_text or "not found" in error_text.lower(), \
            f"API 404 should contain detail: {d}"


class TestIssue9_CacheHeaders:
    """看护 Issue #9: 开发模式静态资源有 no-cache 头"""

    def test_root_path_has_no_cache(self):
        """GET / 返回 Cache-Control: no-cache"""
        import urllib.request
        r = urllib.request.Request(f"{BASE}/")
        with urllib.request.urlopen(r, timeout=5) as resp:
            cc = resp.headers.get("Cache-Control", "")
            assert "no-cache" in cc or "no-store" in cc, \
                f"Root path missing Cache-Control: {dict(resp.headers)}"

    def test_js_files_have_no_cache(self):
        """GET /js/app.js 返回 Cache-Control: no-cache"""
        import urllib.request
        r = urllib.request.Request(f"{BASE}/js/app.js")
        with urllib.request.urlopen(r, timeout=5) as resp:
            cc = resp.headers.get("Cache-Control", "")
            assert "no-cache" in cc or "no-store" in cc, \
                f"JS file missing Cache-Control"

    def test_html_files_have_no_etag(self):
        """HTML 响应不应有 ETag（开发模式防止 304）"""
        import urllib.request
        r = urllib.request.Request(f"{BASE}/")
        with urllib.request.urlopen(r, timeout=5) as resp:
            etag = resp.headers.get("ETag", "")
            assert not etag, f"HTML should not have ETag in dev mode: {etag}"


# ═══════════════════════════════════════════════════════════════════════
# Issue 3: Cytoscape shadow-* 不兼容
# Issue 4: Cytoscape scale 不兼容 → 渲染崩溃
# Issue 6: addNodeToGraph 缺 is-group class
# Issue 7: children 数组→字符串类型错误
# ═══════════════════════════════════════════════════════════════════════

class TestIssue3_4_DAGJS_Compatibility:
    """看护 Issue #3, #4: dag.js 不含无效 Cytoscape 属性"""

    _DAG_JS_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "frontend", "js", "dag.js"
    )

    # Cytoscape.js 3.x 不支持的属性
    _INVALID_CYTOSCAPE_PROPS = [
        "shadow-blur", "shadow-color", "shadow-opacity",
        "shadow-offset-x", "shadow-offset-y",
    ]

    def _read_dag_js(self):
        if not os.path.exists(self._DAG_JS_PATH):
            pytest.skip(f"dag.js not found at {self._DAG_JS_PATH}")
        with open(self._DAG_JS_PATH, "r", encoding="utf-8") as f:
            return f.read()

    def test_no_shadow_properties(self):
        """dag.js 的 Cytoscape style 不含 shadow-* 属性"""
        content = self._read_dag_js()
        for prop in self._INVALID_CYTOSCAPE_PROPS:
            # 允许在注释或 CSS 样式块中出现（如 .dag-tooltip），
            # 但不在 Cytoscape style 对象中
            in_style_context = False
            for line in content.split("\n"):
                if '"' in line and prop in line:
                    # 检测是否在 CSS-in-JS 字符串中 (不是注释)
                    if 'selector:' in line or 'style:' in line or line.strip().startswith('"'):
                        pass  # 在附近的上下文中，用更宽松的方式检查
            # 简化检查：确认 dag.js 中没有这个属性的字符串
            count = content.count(f'"{prop}"')
            assert count == 0, \
                f'dag.js contains invalid Cytoscape property "{prop}" ({count} occurrences)'

    def test_no_scale_style_on_node(self):
        """dag.js 不对 Cytoscape node 使用 scale style"""
        content = self._read_dag_js()
        # node.style("scale", ...) 和 style: { scale: ... } 都不应有
        assert 'style("scale"' not in content, "dag.js uses node.style('scale', ...)"
        assert '"scale"' not in content or 'transform' in content, \
            "dag.js has 'scale' in Cytoscape style — verify it's not on nodes"

    def test_addNodeToGraph_has_is_group_class(self):
        """addNodeToGraph 函数的 classes 包含 is-group"""
        content = self._read_dag_js()
        # 在 addNodeToGraph 函数中找 classes 行
        assert 'is-group' in content, "dag.js should contain 'is-group' class reference"
        # 确认在 addNodeToGraph 附近的 classes 行中有 is_group 判断
        func_start = content.find("function addNodeToGraph")
        if func_start == -1:
            pytest.skip("addNodeToGraph function not found")
        func_body = content[func_start:func_start + 1200]
        classes_line = [l for l in func_body.split("\n") if "classes:" in l]
        if classes_line:
            assert "is_group" in classes_line[0], \
                f"addNodeToGraph classes missing is_group: {classes_line[0]}"

    def test_children_kept_as_array(self):
        """addNodeToGraph 中 children 保持数组类型（不 join 为字符串）"""
        content = self._read_dag_js()
        # children 应该是 n.children || []，不是 .join(", ")
        # 在 addNodeToGraph 中
        func_start = content.find("function addNodeToGraph")
        func_body = content[func_start:func_start + 1200] if func_start != -1 else ""
        # children 不应该被 join
        if func_start != -1:
            assert 'children: n.children || []' in func_body or \
                   'children: (n.children || [])' in func_body, \
                   "children should be stored as array, not joined string in addNodeToGraph"
        # _updateGraph 中的 children 也应该是数组
        update_start = content.find("function _updateGraph")
        update_body = content[update_start:update_start + 1500] if update_start != -1 else ""
        if update_start != -1:
            children_lines = [l for l in update_body.split("\n") if 'children' in l.lower()]
            for cl in children_lines:
                if '.join' in cl and 'children' in cl:
                    assert False, \
                        f"children should not be .join()'d in _updateGraph: {cl.strip()}"


# ═══════════════════════════════════════════════════════════════════════
# Issue 5: _extract_json 数组优先于对象 → LLM 返回被错误解析
# ═══════════════════════════════════════════════════════════════════════

class TestIssue5_JSONExtraction:
    """看护 Issue #5: _extract_json 优先对象而非数组"""

    def test_object_has_priority_over_nested_array(self):
        """当 JSON 顶层是对象、内部含数组时，返回对象"""
        from engine.parser import _extract_json
        # 模拟 LLM 返回: {"tasks": [{"id": "task_1"}]}
        text = '{"project_name": "Test", "tasks": [{"id": "task_1", "name": "DB"}]}'
        result = _extract_json(text, schema_type="project")
        assert isinstance(result, dict), \
            f"Expected dict, got {type(result).__name__}. Object must take priority over nested array."
        assert "project_name" in result
        assert "tasks" in result
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["id"] == "task_1"

    def test_top_level_array_still_works(self):
        """当 JSON 顶层就是数组时，正常返回数组"""
        from engine.parser import _extract_json
        text = '[{"intent": "add_node", "name": "X", "estimated_days": 2}]'
        result = _extract_json(text, schema_type="intent")
        assert isinstance(result, list), f"Expected list, got {type(result).__name__}"
        assert len(result) == 1
        assert result[0]["intent"] == "add_node"

    def test_fenced_object_with_nested_array(self):
        """```json ... ``` 包裹的对象含嵌套数组 → 返回对象"""
        from engine.parser import _extract_json
        text = '```json\n{"tasks": [{"id": "t1"}], "analysis": "ok"}\n```'
        result = _extract_json(text)
        assert isinstance(result, dict), \
            f"Fenced object with nested array should return dict, got {type(result).__name__}"
        assert len(result.get("tasks", [])) == 1

    def test_project_parse_returns_dict(self):
        """parse_project 总是返回 dict"""
        from conftest import has_llm
        if not has_llm():
            pytest.skip("LLM not configured")
        from engine.parser import parse_project
        result = parse_project(
            "Build a simple app with DB design for 2 days and API dev for 3 days."
        )
        assert isinstance(result, dict), \
            f"parse_project must return dict, got {type(result).__name__}"
        assert "tasks" in result
        assert isinstance(result["tasks"], list)


# ═══════════════════════════════════════════════════════════════════════
# Issue 8: create_schedule(members) 污染原始节点 → 分组时间偏移
# ═══════════════════════════════════════════════════════════════════════

class TestIssue8_GroupingSideEffects:
    """看护 Issue #8: get_grouped 不污染原始节点的 ES/EF/LS/LF"""

    @pytest.fixture(scope="module")
    def tagged_project(self):
        """创建含标签的项目"""
        pid, proj = create_test_project(
            "Grouping test: DB design 2d, API dev 5d depends on DB design, "
            "Frontend dev 3d depends on DB design, Testing 2d."
        )
        # Tag nodes
        for i, n in enumerate(proj["nodes"]):
            tags = ["backend"] if i < 2 else ["frontend"] if i == 2 else ["qa"]
            s, _ = req("PUT", f"/api/projects/{pid}/nodes/{n['id']}", {"tags": tags})
            assert s == 200, f"Failed to tag {n['id']}"
        return pid

    def test_node_times_unchanged_after_grouping(self, tagged_project):
        """调用 get_grouped 后，节点时间值不变"""
        pid = tagged_project

        # 1. 读取原始节点时间
        s1, proj_before = req("GET", f"/api/projects/{pid}")
        assert s1 == 200
        times_before = {
            n["id"]: (n.get("es"), n.get("ef"), n.get("ls"), n.get("lf"))
            for n in proj_before["project"]["nodes"]
        }

        # 2. 调用 get_grouped
        s2, _grouped = req("GET", f"/api/projects/{pid}/grouped?tags=backend")
        assert s2 == 200

        # 3. 再次读取节点时间
        s3, proj_after = req("GET", f"/api/projects/{pid}")
        assert s3 == 200
        times_after = {
            n["id"]: (n.get("es"), n.get("ef"), n.get("ls"), n.get("lf"))
            for n in proj_after["project"]["nodes"]
        }

        # 4. 比对：时间值必须完全相同
        for nid, before in times_before.items():
            after = times_after.get(nid)
            assert after is not None, f"Node {nid} missing after grouping"
            es_b, ef_b, ls_b, lf_b = before
            es_a, ef_a, ls_a, lf_a = after
            assert es_b == es_a, \
                f"Node {nid} ES changed: {es_b} → {es_a} (grouping side effect!)"
            assert ef_b == ef_a, \
                f"Node {nid} EF changed: {ef_b} → {ef_a} (grouping side effect!)"
            assert ls_b == ls_a, \
                f"Node {nid} LS changed: {ls_b} → {ls_a} (grouping side effect!)"
            assert lf_b == lf_a, \
                f"Node {nid} LF changed: {lf_b} → {lf_a} (grouping side effect!)"

    def test_grouped_nodes_have_required_fields(self, tagged_project):
        """分组节点包含所有前端渲染需要的字段"""
        pid = tagged_project
        s, d = req("GET", f"/api/projects/{pid}/grouped?tags=backend")
        assert s == 200
        grp_nodes = [n for n in d.get("nodes", []) if n.get("is_group")]
        if not grp_nodes:
            pytest.skip("No group nodes created")
        for gn in grp_nodes:
            for field in ["id", "name", "progress", "status", "estimated_days",
                          "es", "ef", "ls", "lf", "float_days", "is_critical",
                          "is_group", "children", "resources"]:
                assert field in gn, f"Group node missing '{field}'"
            assert isinstance(gn["children"], list), \
                f"children should be list, got {type(gn['children'])}"
            assert gn["id"].startswith("grp_"), \
                f"Group node id should start with grp_: {gn['id']}"


# ═══════════════════════════════════════════════════════════════════════
# Issue 10: 历史数据修复验证
# ═══════════════════════════════════════════════════════════════════════

class TestIssue10_DataIntegrity:
    """看护 Issue #10: 所有项目排期数据健康"""

    def test_all_projects_have_critical_path(self):
        """每个项目至少有一个节点在关键路径上"""
        s, projects = req("GET", "/api/projects")
        assert s == 200
        for p in projects.get("projects", []):
            pid = p["id"]
            s2, proj = req("GET", f"/api/projects/{pid}")
            if s2 != 200:
                continue  # 可能已被删除
            cp = proj["project"]["schedule"]["critical_path"]
            assert len(cp) > 0, \
                f"Project {pid} ({p['name']}) has zero critical path nodes"

    def test_all_projects_have_correct_float(self):
        """关键路径节点 float ≈ 0，非关键路径节点 float > 0 或未定义"""
        s, projects = req("GET", "/api/projects")
        assert s == 200
        for p in projects.get("projects", []):
            pid = p["id"]
            s2, proj = req("GET", f"/api/projects/{pid}")
            if s2 != 200:
                continue
            cp = set(proj["project"]["schedule"]["critical_path"])
            nodes = proj["project"]["nodes"]
            if not cp:
                continue
            for n in nodes:
                f = n.get("float_days")
                if f is None:
                    continue
                if n["id"] in cp:
                    assert abs(f) < 0.01, \
                        f"CP node {n['id']} in project {pid} has float={f}, expected ≈0"
                # 非 CP 节点可以有任意 float（包括 0 如果有并行等长路径）

    def test_batch_reschedule_endpoint_works(self):
        """批量重建排期端点正常工作"""
        s, d = req("POST", "/api/projects/batch/reschedule")
        assert s == 200
        assert "results" in d
        for r in d["results"]:
            assert r["status"] == "ok", \
                f"Batch reschedule failed for {r['id']}: {r.get('error', '?')}"
            assert r.get("cp_nodes", 0) > 0, \
                f"Project {r['id']} has 0 CP nodes after reschedule"

    def test_project_schedule_fields_present(self):
        """每个项目的排期字段都完整"""
        s, projects = req("GET", "/api/projects")
        assert s == 200
        for p in projects.get("projects", []):
            pid = p["id"]
            s2, proj = req("GET", f"/api/projects/{pid}")
            if s2 != 200:
                continue
            sched = proj["project"]["schedule"]
            for field in ["topological_order", "critical_path",
                          "total_duration_days", "project_buffer_days"]:
                assert field in sched, \
                    f"Project {pid} schedule missing '{field}'"
            buf = proj["project"]["buffer"]
            for field in ["total_days", "consumed_days", "remaining_days",
                          "ratio", "status"]:
                assert field in buf, \
                    f"Project {pid} buffer missing '{field}'"
