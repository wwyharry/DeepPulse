"""Unit tests for agent/history.py — 工具组原子折叠 + 滑动窗口 + 预算配置（B7）。"""

import json

import pytest

from deeppulse.agent.history import (
    FOLD_MARKER,
    HistoryUnit,
    build_fold_summary,
    compact_history,
    estimate_tokens,
    iter_units,
    message_chars,
    message_tokens,
    repair_pairing,
    validate_pairing,
)

# ── 消息构造助手（两协议形状） ──────────────────────────────────────


def oa_assistant(content="", tool_calls=None, reasoning=None):
    msg = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    if tool_calls is not None:
        msg["tool_calls"] = [
            {"id": tid, "type": "function", "function": {"name": name, "arguments": json.dumps({})}}
            for tid, name in tool_calls
        ]
    return msg


def oa_tool(tid, content="{}"):
    return {"role": "tool", "tool_call_id": tid, "content": content}


def an_assistant(tool_uses=None, text=None):
    content = []
    if text is not None:
        content.append({"type": "text", "text": text})
    for tid, name in tool_uses or []:
        content.append({"type": "tool_use", "id": tid, "name": name, "input": {}})
    return {"role": "assistant", "content": content}


def an_tool_result(tid, content="{}"):
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]}


def user(text):
    return {"role": "user", "content": text}


def system_note(text):
    return {"role": "system", "content": text}


def mk_history_oa(n_turns=3, tools_per_turn=1):
    """openai 形状历史：system 主提示 + n 轮「user + assistant(tool_calls) + tool 结果 + assistant 文本」"""
    msgs = [system_note("主提示")]
    for t in range(n_turns):
        msgs.append(user(f"问题{t}"))
        tcs = [(f"call_{t}_{j}", f"tool_{j}") for j in range(tools_per_turn)]
        msgs.append(oa_assistant(tool_calls=tcs))
        msgs.extend(oa_tool(tid) for tid, _ in tcs)
        msgs.append(oa_assistant(f"回答{t}"))
    return msgs


def mk_history_an(n_turns=3, tools_per_turn=1):
    msgs = [system_note("主提示")]
    for t in range(n_turns):
        msgs.append(user(f"问题{t}"))
        tcs = [(f"call_{t}_{j}", f"tool_{j}") for j in range(tools_per_turn)]
        msgs.append(an_assistant(tool_uses=tcs))
        msgs.extend(an_tool_result(tid) for tid, _ in tcs)
        msgs.append(an_assistant(text=f"回答{t}"))
    return msgs


BIG = "数" * 3000


# ── A1-A4 单元切分 ──────────────────────────────────────


class TestIterUnits:
    def test_openai_tool_group_atomic(self):
        msgs = [
            user("q"),
            oa_assistant(tool_calls=[("c1", "get_kline")]),
            oa_tool("c1"),
            oa_tool("c2"),  # 同组第二条结果（并行场景的顺序结果）
            system_note("注意：结果已截断"),
            oa_assistant("ok"),
        ]
        units = iter_units(msgs, "openai")
        assert [u.kind for u in units] == ["user_text", "tool_group", "assistant_text"]
        # assistant + 2 条 tool 结果 + 尾随 system 注释 = 4 条，整组原子
        assert len(units[1].messages) == 4
        assert units[1].messages[-1] == system_note("注意：结果已截断")

    def test_openai_group_includes_trailing_system_note(self):
        msgs = [
            user("q"),
            oa_assistant(tool_calls=[("c1", "t")]),
            oa_tool("c1"),
            system_note("截断提醒"),
            oa_assistant("ok"),
        ]
        units = iter_units(msgs, "openai")
        assert [u.kind for u in units] == ["user_text", "tool_group", "assistant_text"]
        assert len(units[1].messages) == 3  # assistant + tool + system note
        assert units[1].messages[2] == system_note("截断提醒")

    def test_openai_parallel_tool_group_is_one_unit(self):
        msgs = [
            user("q"),
            oa_assistant(tool_calls=[("c1", "a"), ("c2", "b")]),
            oa_tool("c1"),
            oa_tool("c2"),
            oa_assistant("done"),
        ]
        units = iter_units(msgs, "openai")
        assert [u.kind for u in units] == ["user_text", "tool_group", "assistant_text"]
        assert len(units[1].messages) == 3  # assistant + 2 条 tool 结果

    def test_anthropic_tool_group_and_user_text(self):
        msgs = [
            user("q"),
            an_assistant(tool_uses=[("c1", "a"), ("c2", "b")]),
            an_tool_result("c1"),
            an_tool_result("c2"),
            an_assistant(text="done"),
        ]
        units = iter_units(msgs, "anthropic")
        assert [u.kind for u in units] == ["user_text", "tool_group", "assistant_text"]
        assert len(units[1].messages) == 3  # assistant + 2 条 tool_result 消息

    def test_mixed_shapes_reasoning_and_empty_content(self):
        msgs = [
            user("q"),
            oa_assistant("", tool_calls=[("c1", "t")], reasoning="想一想"),
            oa_tool("c1"),
            oa_assistant(""),  # 空 content 收尾
        ]
        units = iter_units(msgs, "openai")
        assert [u.kind for u in units] == ["user_text", "tool_group", "assistant_text"]
        assert units[1].messages[0].get("reasoning_content") == "想一想"


# ── A5-A7 折叠不变量 ──────────────────────────────────────


class TestFoldInvariants:
    @pytest.mark.parametrize("protocol", ["openai", "anthropic"])
    def test_pairing_preserved_after_fold(self, protocol):
        mk = mk_history_oa if protocol == "openai" else mk_history_an
        msgs = mk(n_turns=6, tools_per_turn=2)
        assert validate_pairing(msgs, protocol) == []
        out = compact_history(msgs, protocol=protocol, max_messages=10, max_chars=0, keep_last_turns=1)
        assert validate_pairing(out, protocol) == []
        assert len(out) < len(msgs)

    @pytest.mark.parametrize("protocol", ["openai", "anthropic"])
    def test_folded_tool_ids_completely_disappear(self, protocol):
        mk = mk_history_oa if protocol == "openai" else mk_history_an
        msgs = mk(n_turns=5, tools_per_turn=1)
        out = compact_history(msgs, protocol=protocol, max_messages=8, max_chars=0)
        flat = json.dumps(out, ensure_ascii=False)
        assert "call_0_0" not in flat  # 最旧组的 id 完全消失
        assert "call_4_0" in flat  # 最新组保留

    def test_fold_placeholder_shape_and_unicity(self):
        msgs = mk_history_oa(n_turns=8, tools_per_turn=2)
        out1 = compact_history(msgs, protocol="openai", max_messages=12, max_chars=0)
        ph1 = [m for m in out1 if m.get("role") == "system" and str(m.get("content", "")).startswith(FOLD_MARKER)]
        assert len(ph1) == 1
        assert len(ph1[0]["content"]) <= 600

        # 二次折叠：仍只有一个占位符，且合并了旧摘要
        out2 = compact_history(
            out1 + mk_history_oa(n_turns=4, tools_per_turn=2)[1:], protocol="openai", max_messages=8, max_chars=0
        )
        ph2 = [m for m in out2 if m.get("role") == "system" and str(m.get("content", "")).startswith(FOLD_MARKER)]
        assert len(ph2) == 1
        assert "更早摘要" in ph2[0]["content"] or "已压缩" in ph2[0]["content"]


# ── A8-A12 滑动窗口 / 预算 ──────────────────────────────────────


class TestSlidingWindow:
    def test_under_budget_unchanged(self):
        msgs = mk_history_oa(n_turns=2, tools_per_turn=1)
        out = compact_history(msgs, protocol="openai", max_messages=100, max_chars=10**9)
        assert out == msgs
        assert not any(str(m.get("content", "")).startswith(FOLD_MARKER) for m in out)

    def test_max_messages_folds_oldest_turns_keeps_tail(self):
        msgs = mk_history_oa(n_turns=5, tools_per_turn=1)
        out = compact_history(msgs, protocol="openai", max_messages=10, max_chars=0)
        # 最后一轮 user 问题与 assistant 回复原样在尾部
        assert out[-1] == oa_assistant("回答4")
        assert user("问题4") in out
        # 中间轮被折叠（首用户除外——被钉住）
        assert user("问题1") not in out and user("问题2") not in out
        assert validate_pairing(out, "openai") == []

    def test_max_chars_triggers_fold(self):
        msgs = [system_note("主提示")]
        for t in range(4):
            msgs.append(user(f"问题{t}"))
            msgs.append(oa_assistant(tool_calls=[(f"c{t}", "t")]))
            msgs.append(oa_tool(f"c{t}", content=BIG))
            msgs.append(oa_assistant(f"回答{t}"))
        out = compact_history(msgs, protocol="openai", max_messages=0, max_chars=6000)
        assert len(out) < len(msgs)
        total = sum(message_chars(m) for m in out[1:])
        assert total < sum(message_chars(m) for m in msgs[1:])

    def test_max_tokens_triggers_fold(self):
        msgs = [system_note("主提示")]
        for t in range(4):
            msgs.append(user(f"问题{t}"))
            msgs.append(oa_assistant(tool_calls=[(f"c{t}", "t")]))
            msgs.append(oa_tool(f"c{t}", content=BIG))
            msgs.append(oa_assistant(f"回答{t}"))
        out = compact_history(msgs, protocol="openai", max_messages=0, max_chars=0, max_tokens=2500)
        assert len(out) < len(msgs)
        total = sum(message_tokens(m) for m in out[1:])
        assert total < sum(message_tokens(m) for m in msgs[1:])
        assert validate_pairing(out, "openai") == []

    def test_max_tokens_zero_means_unlimited(self):
        msgs = mk_history_oa(n_turns=5, tools_per_turn=1)
        out = compact_history(msgs, protocol="openai", max_messages=0, max_chars=0, max_tokens=0)
        assert out == msgs

    def test_keep_last_turns(self):
        msgs = mk_history_oa(n_turns=5, tools_per_turn=1)
        out = compact_history(msgs, protocol="openai", max_messages=6, max_chars=0, keep_last_turns=2)
        assert user("问题3") in out and user("问题4") in out
        assert user("问题1") not in out  # 首用户被钉住不算

    def test_first_user_pinned_and_head_identity(self):
        msgs = mk_history_oa(n_turns=6, tools_per_turn=1)
        out = compact_history(msgs, protocol="openai", max_messages=8, max_chars=0)
        assert out[0] is msgs[0]  # 同一 dict 引用（refresh_memory_context 契约）
        # 首个用户问题钉住于占位符之后
        ph_idx = next(i for i, m in enumerate(out) if str(m.get("content", "")).startswith(FOLD_MARKER))
        assert out[ph_idx + 1] == user("问题0")


# ── A13-A19 边界 ──────────────────────────────────────


class TestEdges:
    def test_short_history_untouched(self):
        assert compact_history([], protocol="openai", max_messages=5, max_chars=100) == []
        only_head = [system_note("主提示")]
        assert compact_history(only_head, protocol="openai", max_messages=5, max_chars=100) == only_head
        msgs = [system_note("主提示"), user("hi")]
        assert compact_history(msgs, protocol="openai", max_messages=5, max_chars=100) == msgs

    def test_tool_group_never_split_across_boundary(self):
        # 预算卡在两个大 tool_group 之间：旧组整组折叠、新组整组保留，绝不拆半
        msgs = [system_note("主提示"), user("q")]
        msgs.append(oa_assistant(tool_calls=[("g1a", "t"), ("g1b", "t")]))
        msgs.append(oa_tool("g1a", content=BIG))
        msgs.append(oa_tool("g1b", content=BIG))
        msgs.append(oa_assistant(tool_calls=[("g2a", "t"), ("g2b", "t")]))
        msgs.append(oa_tool("g2a", content=BIG))
        msgs.append(oa_tool("g2b", content=BIG))
        msgs.append(oa_assistant("最终"))
        out = compact_history(msgs, protocol="openai", max_messages=0, max_chars=8000)
        assert validate_pairing(out, "openai") == []
        ids_in_out = {m.get("tool_call_id") for m in out if m.get("role") == "tool"}
        assert "g1a" not in ids_in_out and "g1b" not in ids_in_out  # 旧组整组消失
        assert ids_in_out == {"g2a", "g2b"}  # 新组完整保留

    def test_huge_turn_folds_oldest_tool_group_keeps_floors(self):
        msgs = [system_note("主提示"), user("q")]
        for j in range(4):
            msgs.append(oa_assistant(tool_calls=[(f"c{j}", "t")]))
            msgs.append(oa_tool(f"c{j}", content=BIG))
        msgs.append(oa_assistant("最终回答"))
        out = compact_history(msgs, protocol="openai", max_messages=0, max_chars=3000)
        assert validate_pairing(out, "openai") == []
        assert user("q") in out  # turn 的 user_text 硬底
        assert out[-1] == oa_assistant("最终回答")  # 最后 assistant 硬底

    def test_zero_budget_means_unlimited(self):
        msgs = mk_history_oa(n_turns=5, tools_per_turn=1)
        out = compact_history(msgs, protocol="openai", max_messages=0, max_chars=0)
        assert out == msgs

    def test_malformed_messages_do_not_crash(self):
        msgs = [
            {"role": "system", "content": "主提示"},
            {"role": "user", "content": None},
            {"role": "assistant"},  # 缺 content/tool_calls
            {"content": "匿名"},  # 缺 role
            {"role": "tool", "tool_call_id": "ghost"},  # 孤儿 tool
        ]
        out = compact_history(msgs, protocol="openai", max_messages=2, max_chars=0)
        assert out[0] is msgs[0]

    def test_estimate_tokens_monotonic(self):
        assert estimate_tokens("") == 0
        zh = estimate_tokens("数" * 100)
        en = estimate_tokens("a" * 100)
        assert zh > en  # CJK 更贵
        assert estimate_tokens("数" * 200) >= zh

    def test_validate_pairing_reports_bad_history(self):
        # 悬空 tool_call（缺结果）
        bad1 = [system_note("s"), user("q"), oa_assistant(tool_calls=[("c1", "t")]), oa_assistant("x")]
        assert any("悬空" in e for e in validate_pairing(bad1, "openai"))
        # 孤儿 tool 结果
        bad2 = [system_note("s"), oa_tool("ghost")]
        assert any("孤儿" in e for e in validate_pairing(bad2, "openai"))
        # anthropic 悬空 tool_use
        bad3 = [system_note("s"), user("q"), an_assistant(tool_uses=[("c1", "t")]), an_assistant(text="x")]
        assert any("悬空" in e for e in validate_pairing(bad3, "anthropic"))

    def test_anthropic_pairing_after_fold(self):
        msgs = mk_history_an(n_turns=6, tools_per_turn=2)
        out = compact_history(msgs, protocol="anthropic", max_messages=10, max_chars=0)
        assert validate_pairing(out, "anthropic") == []
        assert any(m.get("role") == "system" and str(m.get("content", "")).startswith(FOLD_MARKER) for m in out)


# ── 配对修复 ──────────────────────────────────────


class TestRepairPairing:
    def test_openai_dangling_filled(self):
        # 中断残留：assistant(c1,c2) 只有 c1 的结果 → 补 c2 合成结果
        msgs = [
            system_note("s"),
            user("q"),
            oa_assistant(tool_calls=[("c1", "t1"), ("c2", "t2")]),
            oa_tool("c1"),
        ]
        out = repair_pairing(msgs, "openai")
        assert validate_pairing(out, "openai") == []
        tool_ids = [m["tool_call_id"] for m in out if m.get("role") == "tool"]
        assert tool_ids == ["c1", "c2"]
        synth = next(m for m in out if m.get("tool_call_id") == "c2")
        assert "未执行" in synth["content"] and "t2" in synth["content"]

    def test_openai_dangling_closed_before_next_turn(self):
        # 悬空组在下一 user 消息前必须补完结果（API 顺序约束）
        msgs = [
            system_note("s"),
            user("q1"),
            oa_assistant(tool_calls=[("c1", "t")]),
            user("q2"),
        ]
        out = repair_pairing(msgs, "openai")
        assert validate_pairing(out, "openai") == []
        idx_synth = next(i for i, m in enumerate(out) if m.get("role") == "tool")
        idx_q2 = next(i for i, m in enumerate(out) if m == user("q2"))
        assert idx_synth < idx_q2

    def test_openai_orphan_tool_dropped(self):
        msgs = [system_note("s"), oa_tool("ghost"), user("q")]
        out = repair_pairing(msgs, "openai")
        assert validate_pairing(out, "openai") == []
        assert all(m.get("role") != "tool" for m in out)

    def test_anthropic_dangling_filled(self):
        msgs = [
            system_note("s"),
            user("q"),
            an_assistant(tool_uses=[("c1", "t1"), ("c2", "t2")]),
            an_tool_result("c1"),
        ]
        out = repair_pairing(msgs, "anthropic")
        assert validate_pairing(out, "anthropic") == []
        synth_ids = [
            b["tool_use_id"]
            for m in out
            if m.get("role") == "user" and isinstance(m.get("content"), list)
            for b in m["content"]
            if b.get("type") == "tool_result"
        ]
        assert set(synth_ids) == {"c1", "c2"}

    def test_anthropic_orphan_result_blocks_dropped(self):
        msgs = [
            system_note("s"),
            an_assistant(tool_uses=[("c1", "t")]),
            an_tool_result("ghost"),
            an_tool_result("c1"),
        ]
        out = repair_pairing(msgs, "anthropic")
        assert validate_pairing(out, "anthropic") == []
        result_ids = [
            b["tool_use_id"]
            for m in out
            if m.get("role") == "user" and isinstance(m.get("content"), list)
            for b in m["content"]
            if b.get("type") == "tool_result"
        ]
        assert result_ids == ["c1"]

    def test_valid_history_unchanged_and_idempotent(self):
        msgs = mk_history_oa(n_turns=2, tools_per_turn=2)
        out1 = repair_pairing(msgs, "openai")
        out2 = repair_pairing(out1, "openai")
        assert out1 == msgs
        assert out2 == out1
        msgs_an = mk_history_an(n_turns=2, tools_per_turn=2)
        assert repair_pairing(msgs_an, "anthropic") == msgs_an

    def test_system_notes_do_not_close_group(self):
        # 组内 system 注释不关闭配对窗口：合成结果补在收尾 assistant 之前
        msgs = [
            system_note("s"),
            user("q"),
            oa_assistant(tool_calls=[("c1", "t")]),
            system_note("注释"),
            oa_assistant("ok"),
        ]
        out = repair_pairing(msgs, "openai")
        assert validate_pairing(out, "openai") == []
        idx_synth = next(i for i, m in enumerate(out) if m.get("role") == "tool")
        idx_final = next(i for i, m in enumerate(out) if m.get("role") == "assistant" and m.get("content") == "ok")
        assert idx_synth < idx_final

    def test_multiple_dangling_groups(self):
        # 两段悬空历史各自补齐（下一组开始前先补上一组缺口）
        msgs = [
            system_note("s"),
            user("q1"),
            oa_assistant(tool_calls=[("a1", "t")]),
            oa_assistant("中间回答"),
            user("q2"),
            oa_assistant(tool_calls=[("b1", "t")]),
        ]
        out = repair_pairing(msgs, "openai")
        assert validate_pairing(out, "openai") == []
        tool_ids = [m["tool_call_id"] for m in out if m.get("role") == "tool"]
        assert tool_ids == ["a1", "b1"]


# ── 摘要与工具函数 ──────────────────────────────────────


class TestSummaryAndHelpers:
    def test_build_fold_summary_contents(self):
        msgs = mk_history_oa(n_turns=2, tools_per_turn=2)[1:]
        units = iter_units(msgs, "openai")
        summary = build_fold_summary(units, max_chars=600)
        assert summary.startswith(FOLD_MARKER)
        assert "问题0" in summary
        assert "tool_0" in summary
        assert len(summary) <= 600

    def test_build_fold_summary_respects_limit_and_prev(self):
        units = [HistoryUnit("user_text", [user("长问题" * 200)], is_turn_start=True)]
        prev = f"{FOLD_MARKER} 旧摘要内容"
        summary = build_fold_summary(units, prev_summary=prev, max_chars=80)
        assert summary.startswith(FOLD_MARKER)
        assert len(summary) <= 80

    def test_message_chars(self):
        assert message_chars({"role": "user", "content": "数" * 10}) > 10
        assert message_chars({"a": object()}) > 0  # default=str 兜底

    def test_message_tokens(self):
        zh = message_tokens({"role": "user", "content": "数" * 100})
        en = message_tokens({"role": "user", "content": "a" * 100})
        assert zh > en  # CJK 更贵
        assert message_tokens({"a": object()}) > 0  # default=str 兜底
