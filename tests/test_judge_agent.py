"""Unit tests for judge_agent.py 的历史折叠兼容（B7）。"""

from deeppulse.agent.history import FOLD_MARKER, compact_history
from deeppulse.agent.judge_agent import JudgeAgent


def _judge():
    return JudgeAgent.__new__(JudgeAgent)


def _folded_history(protocol="openai"):
    """构造折叠后历史（含占位符 + 钉住首用户 + 最后一轮）"""
    msgs = [{"role": "system", "content": "主提示"}]
    for t in range(6):
        msgs.append({"role": "user", "content": f"问题{t}"})
        if protocol == "openai":
            msgs.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": f"c{t}", "type": "function", "function": {"name": "get_kline", "arguments": "{}"}}
                    ],
                }
            )
            msgs.append({"role": "tool", "tool_call_id": f"c{t}", "content": '{"kline": []}'})
        else:
            msgs.append(
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": f"c{t}", "name": "get_kline", "input": {}}],
                }
            )
            msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"c{t}", "content": "{}"}]})
        msgs.append({"role": "assistant", "content": f"回答{t}"})
    return compact_history(msgs, protocol=protocol, max_messages=8, max_chars=0)


class TestJudgeWithFoldedHistory:
    def test_user_query_survives_fold(self):
        context = _judge()._build_judge_context(_folded_history())
        assert "问题0" in context  # 首用户钉住，user_query 为原始首问

    def test_folded_summary_injected(self):
        context = _judge()._build_judge_context(_folded_history())
        assert "早前上下文（已折叠）" in context
        assert FOLD_MARKER in context or "已压缩" in context

    def test_anthropic_shape_no_crash(self):
        context = _judge()._build_judge_context(_folded_history(protocol="anthropic"))
        assert "问题0" in context
        assert "早前上下文（已折叠）" in context

    def test_no_fold_marker_no_section(self):
        msgs = [
            {"role": "system", "content": "主提示"},
            {"role": "user", "content": "问题0"},
            {"role": "assistant", "content": "回答0"},
        ]
        context = _judge()._build_judge_context(msgs)
        assert "早前上下文（已折叠）" not in context
