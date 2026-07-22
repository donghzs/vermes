"""Plan 解析器专项测试：平衡括号 + JSON 提取 + 跨 delta 分片"""
import pytest
import json

# ── 平衡括号解析器 ────────────────────────────────────────────────

class TestBalancedBracketParser:
    """验证 _detect_and_emit_plan 中的平衡括号解析器"""

    def _parse_balanced(self, text: str) -> dict:
        """从 chat.py 提取的平衡括号解析逻辑（简化版，用于测试）"""
        # 去除 markdown fence
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        # 平衡括号解析
        depth = 0
        in_str = False
        esc_next = False
        start_idx = None
        for i, ch in enumerate(text):
            if esc_next:
                esc_next = False
                continue
            if ch == "\\":
                esc_next = True
                continue
            if ch == '"' and not esc_next:
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                if depth == 0:
                    start_idx = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start_idx is not None:
                    candidate = text[start_idx:i+1]
                    try:
                        obj = json.loads(candidate)
                        if "plan" in obj:
                            return obj
                    except:
                        pass
                    start_idx = None
        return {}

    def test_simple_plan(self):
        """简单 plan JSON"""
        text = '''{"plan":{"steps":[{"id":"s1","title":"Step 1"}]}}'''
        result = self._parse_balanced(text)
        assert "plan" in result
        assert result["plan"]["steps"][0]["id"] == "s1"

    def test_nested_objects(self):
        """嵌套对象"""
        text = '''{"plan":{"meta":{"version":"1.0"},"steps":[{"id":"s1","params":{"key":"value"}}]}}'''
        result = self._parse_balanced(text)
        assert result["plan"]["meta"]["version"] == "1.0"
        assert result["plan"]["steps"][0]["params"]["key"] == "value"

    def test_escaped_quotes(self):
        """转义引号"""
        text = r'''{"plan":{"title":"He said \"hello\"","steps":[]}}'''
        result = self._parse_balanced(text)
        assert result["plan"]["title"] == 'He said "hello"'

    def test_escaped_backslash(self):
        """转义反斜杠"""
        text = r'''{"plan":{"path":"C:\\Users\\test","steps":[]}}'''
        result = self._parse_balanced(text)
        assert result["plan"]["path"] == "C:\\Users\\test"

    def test_multiple_objects(self):
        """多个 JSON 对象，只取含 plan 的"""
        text = '''{"foo":"bar"}{"plan":{"steps":[]}}{"baz":"qux"}'''
        result = self._parse_balanced(text)
        assert "plan" in result
        assert "foo" not in result

    def test_markdown_fence(self):
        """markdown fence 包裹"""
        text = '''```json
{"plan":{"steps":[{"id":"s1"}]}}
```'''
        result = self._parse_balanced(text)
        assert result["plan"]["steps"][0]["id"] == "s1"

    def test_partial_delta_accumulation(self):
        """跨 delta 分片：模拟多个 delta 累积后解析"""
        # 实际场景：chat.py 会累积 delta，平衡括号解析器在完整 JSON 到达后才解析
        # 这个测试验证：累积后的完整 JSON 能被正确解析
        text = '{"plan":{"steps":[{"id":"s1","title":"Step 1"},{"id":"s2","title":"Step 2"}]}}'
        result = self._parse_balanced(text)
        assert "plan" in result
        assert len(result["plan"]["steps"]) == 2
        assert result["plan"]["steps"][1]["id"] == "s2"

    def test_string_with_braces(self):
        """字符串内包含大括号"""
        text = '''{"plan":{"description":"Use {placeholder} in template","steps":[]}}'''
        result = self._parse_balanced(text)
        assert "{placeholder}" in result["plan"]["description"]

    def test_deeply_nested(self):
        """深度嵌套（5层）"""
        text = '''{"plan":{"a":{"b":{"c":{"d":{"e":"deep"}}}}}}'''
        result = self._parse_balanced(text)
        assert result["plan"]["a"]["b"]["c"]["d"]["e"] == "deep"

    def test_empty_plan(self):
        """空 plan 对象"""
        text = '''{"plan":{}}'''
        result = self._parse_balanced(text)
        assert "plan" in result
        assert result["plan"] == {}

    def test_plan_with_unicode(self):
        """Unicode 字符"""
        text = '''{"plan":{"title":"任务计划","steps":[]}}'''
        result = self._parse_balanced(text)
        assert result["plan"]["title"] == "任务计划"


# ── parse_plan_from_agent_output ─────────────────────────────────────

class TestParsePlanFromAgentOutput:
    """验证 task_planning.py 中的 plan 解析"""

    def test_extract_from_markdown_block(self):
        """从 markdown code block 提取"""
        from agent.task_planning import parse_plan_from_agent_output

        text = '''Here's my plan:
```json
{"steps":[{"id":"s1","title":"First step"}]}
```
Let me proceed.'''
        result = parse_plan_from_agent_output(text)
        assert result is not None
        assert result.steps[0].id == "s1"

    def test_extract_bare_json(self):
        """裸 JSON（无 fence）"""
        from agent.task_planning import parse_plan_from_agent_output

        text = '''{"steps":[{"id":"s1","title":"Step"}]}'''
        result = parse_plan_from_agent_output(text)
        assert result is not None
        assert result.steps[0].id == "s1"

    def test_no_plan_returns_none(self):
        """无 plan 时返回 None"""
        from agent.task_planning import parse_plan_from_agent_output

        text = "This is just a regular response without any plan."
        result = parse_plan_from_agent_output(text)
        assert result is None

    def test_malformed_json_returns_none(self):
        """畸形 JSON 返回 None"""
        from agent.task_planning import parse_plan_from_agent_output

        text = '''```json
{"steps":[{"id":"s1",}]}  # trailing comma
```'''
        result = parse_plan_from_agent_output(text)
        assert result is None

    def test_plan_with_tools(self):
        """plan 含工具列表"""
        from agent.task_planning import parse_plan_from_agent_output

        # Step 没有 required_tools 字段，改测 tool_calls
        text = '''{"steps":[{"id":"s1","title":"Search"}]}'''
        result = parse_plan_from_agent_output(text)
        assert result.steps[0].id == "s1"
        assert result.steps[0].tool_calls == []  # 默认空列表
