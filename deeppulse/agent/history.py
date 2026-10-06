"""消息历史上下文管理 - 协议感知的工具组原子折叠 + 滑动窗口（B7）

长会话下 StockAgent.messages 只增不减，每轮 LLM 调用全量重发历史必然超出
模型上下文窗口。本模块提供纯函数式的历史压缩：

- **工具组原子折叠**：把「assistant(tool_calls) + 全部 tool 结果 + 尾随 system
  注释」作为不可拆分的原子单元（tool_group）整组进出折叠区，从构造上保持
  tool_call/tool_result 配对不变量（openai: tool_calls[i].id ↔ tool.tool_call_id；
  anthropic: tool_use.id ↔ tool_result.tool_use_id）。
- **滑动窗口**：按用户文本边界切 turn，从最新向前保留；最近 keep_last_turns
  个 turn 硬底保留，仅折叠旧 turn。
- **预算配置**：max_messages / max_chars / max_tokens 任一超限即触发折叠（0 = 该项不限）。

折叠产物是单条 `{"role": "system", "content": "[上下文折叠] ..."}` 占位摘要
（对 openai 与既有中段 system 注释同形；对 anthropic 会被合并进顶层 system
参数，语义正确且不伪造对话轮次）。全历史只保留一个活占位符：再次折叠时旧
占位符摘要并入新摘要。

边界：预算是软目标 + 硬底（极小预算下输出仍可能超）；折叠不可逆，早前工具
细节需重新查询；既有「异常中断留下的悬空 tool_call_id」在保留区不主动修复，
该组被折叠即自愈。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field

from deeppulse.config import HISTORY_FOLD_SUMMARY_MAX_CHARS

FOLD_MARKER = "[上下文折叠]"

# 单元类型
KIND_TOOL_GROUP = "tool_group"
KIND_USER_TEXT = "user_text"
KIND_ASSISTANT_TEXT = "assistant_text"
KIND_SYSTEM_NOTE = "system_note"


@dataclass
class HistoryUnit:
    """原子单元：不可再分的折叠/保留粒度"""

    kind: str
    messages: list[dict] = field(default_factory=list)
    is_turn_start: bool = False

    @property
    def chars(self) -> int:
        return sum(message_chars(m) for m in self.messages)

    @property
    def tokens(self) -> int:
        return sum(message_tokens(m) for m in self.messages)


def message_chars(msg: dict) -> int:
    """消息长度度量：JSON 序列化字符数（与项目既有字符度量口径一致）"""
    try:
        return len(json.dumps(msg, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(msg))


def estimate_tokens(text: str) -> int:
    """粗略 token 估算（CJK 字符≈1 token，其余≈0.25 token/字符）。

    max_tokens 预算的计量口径；不追求分词精度，只要求同输入稳定、随长度单调。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿" or "　" <= ch <= "〿" or "＀" <= ch <= "￯")
    other = len(text) - cjk
    return cjk + math.ceil(other * 0.25)


def message_tokens(msg: dict) -> int:
    """消息 token 度量：JSON 序列化文本过 estimate_tokens 粗估"""
    try:
        return estimate_tokens(json.dumps(msg, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return estimate_tokens(str(msg))


# ── 协议感知的形状判定 ──────────────────────────────────────


def _is_tool_calls_assistant(msg: dict, protocol: str) -> bool:
    """assistant 消息是否发起工具调用（含并行 tool_calls）"""
    if msg.get("role") != "assistant":
        return False
    if protocol == "anthropic":
        content = msg.get("content")
        if isinstance(content, list):
            return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
        return False
    return bool(msg.get("tool_calls"))


def _is_tool_result(msg: dict, protocol: str) -> bool:
    """是否为工具结果消息（anthropic 下包在 role=user 的块列表里）"""
    if protocol == "anthropic":
        if msg.get("role") != "user":
            return False
        content = msg.get("content")
        return isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    return msg.get("role") == "tool"


def _is_user_text(msg: dict, protocol: str) -> bool:
    """真用户消息（str content；anthropic 下与 tool_result 包裹的 user 区分）"""
    return msg.get("role") == "user" and not _is_tool_result(msg, protocol)


def _tool_names(assistant_msg: dict) -> list[str]:
    """从 assistant 消息提取工具名（兼容两种协议形状）"""
    names = []
    for tc in assistant_msg.get("tool_calls") or []:
        if isinstance(tc, dict):
            fn = tc.get("function")
            name = fn.get("name") if isinstance(fn, dict) else None
            names.append(str(name or ""))
    content = assistant_msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                names.append(str(block.get("name") or ""))
    return names


# ── 单元切分 ──────────────────────────────────────


def iter_units(messages: list[dict], protocol: str) -> list[HistoryUnit]:
    """把消息序列切为原子单元（不含 messages[0] 主提示，由调用方先拆出）。

    tool_group = assistant(tool_calls) + 全部顺序 tool 结果 + 尾随紧邻的
    system 注释（注释正是对这些结果的提醒，归属同组）。
    """
    units: list[HistoryUnit] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if _is_tool_calls_assistant(msg, protocol):
            group = [msg]
            i += 1
            while i < n and _is_tool_result(messages[i], protocol):
                group.append(messages[i])
                i += 1
            while i < n and messages[i].get("role") == "system":
                group.append(messages[i])
                i += 1
            units.append(HistoryUnit(KIND_TOOL_GROUP, group))
        elif _is_user_text(msg, protocol):
            units.append(HistoryUnit(KIND_USER_TEXT, [msg], is_turn_start=True))
            i += 1
        elif msg.get("role") == "system":
            units.append(HistoryUnit(KIND_SYSTEM_NOTE, [msg]))
            i += 1
        else:
            # assistant 文本（含 [分析中断]/轮次耗尽占位、reasoning_content 附带键）
            # 或异常形状（孤儿 tool 结果等）——按文本单元处理，validate_pairing 负责报违规
            units.append(HistoryUnit(KIND_ASSISTANT_TEXT, [msg]))
            i += 1
    return units


# ── 折叠摘要 ──────────────────────────────────────


def build_fold_summary(folded: list[HistoryUnit], prev_summary: str | None = None, max_chars: int | None = None) -> str:
    """生成折叠占位摘要：规模、用户问题列表、工具调用统计、（可选）并入旧摘要"""
    limit = HISTORY_FOLD_SUMMARY_MAX_CHARS if max_chars is None else max(0, int(max_chars))
    n_msgs = sum(len(u.messages) for u in folded)
    n_chars = sum(u.chars for u in folded)
    n_turns = sum(1 for u in folded if u.is_turn_start)
    questions = [str(u.messages[0].get("content") or "").strip()[:40] for u in folded if u.is_turn_start]
    questions = [q for q in questions if q]

    counter: Counter[str] = Counter()
    for u in folded:
        if u.kind == KIND_TOOL_GROUP:
            for name in _tool_names(u.messages[0]):
                if name:
                    counter[name] += 1

    lines = [f"{FOLD_MARKER} 以下早前对话已压缩（{n_turns} 轮 / {n_msgs} 条消息 / 约 {n_chars} 字符）。"]
    if questions:
        numbered = " ".join(f"{i + 1}.「{q}」" for i, q in enumerate(questions))
        lines.append(f"用户问题：{numbered}")
    if counter:
        stats = ", ".join(f"{name}×{cnt}" for name, cnt in counter.most_common(10))
        lines.append(f"调用工具：{stats}")
    if prev_summary:
        body = prev_summary.strip()
        if body.startswith(FOLD_MARKER):
            body = body[len(FOLD_MARKER) :].strip()
        if body:
            lines.append(f"更早摘要：{body}")
    lines.append("（早前工具结果与推理细节已省略；如需数据请重新查询）")

    summary = "\n".join(lines)
    if limit > 0 and len(summary) > limit:
        summary = summary[: limit - 1] + "…"
    return summary


# ── 滑动窗口 + 折叠主入口 ──────────────────────────────────────


def _turn_size(turn: list[HistoryUnit]) -> tuple[int, int, int]:
    msgs = sum(len(u.messages) for u in turn)
    chars = sum(u.chars for u in turn)
    tokens = sum(u.tokens for u in turn)
    return msgs, chars, tokens


def _over_budget(msgs: int, chars: int, tokens: int, max_messages: int, max_chars: int, max_tokens: int) -> bool:
    return (
        (max_messages > 0 and msgs > max_messages)
        or (max_chars > 0 and chars > max_chars)
        or (max_tokens > 0 and tokens > max_tokens)
    )


def compact_history(
    messages: list[dict],
    *,
    protocol: str,
    max_messages: int,
    max_chars: int,
    max_tokens: int = 0,
    keep_last_turns: int = 1,
    fold_summary_max_chars: int | None = None,
) -> list[dict]:
    """按预算对历史做工具组原子折叠 + 滑动窗口，返回新列表（不改输入）。

    - messages[0]（system 主提示）以同一 dict 引用保留在 [0]（refresh_memory_context
      原地改写 messages[0]["content"] 的契约）。
    - max_messages / max_chars / max_tokens 为 0 表示该项不限；全为 0 时恒不折叠。
    - 硬底：最近 keep_last_turns（至少 1）个完整 turn、每个 turn 的 user_text、
      会话最后一条 assistant 文本永不折叠。
    - 首个 user_text 原消息钉住于占位符之后（保 JudgeAgent 的 user_query）。
    """
    if not messages or len(messages) <= 1:
        return list(messages)
    if max_messages <= 0 and max_chars <= 0 and max_tokens <= 0:
        return list(messages)

    head = messages[0]
    units = iter_units(messages[1:], protocol)

    # 旧折叠占位符先留在原位；仅当发生新折叠时才摘出并入新摘要
    prev_summary: str | None = None

    # 按用户文本边界切 turn（首个用户前的孤儿单元自成前缀 turn，可优先折叠）
    turns: list[list[HistoryUnit]] = []
    for u in units:
        if u.is_turn_start or not turns:
            turns.append([u])
        else:
            turns[-1].append(u)

    # 从最新 turn 向前选保留后缀（受预算约束；硬底 turn 无条件保留）
    floor = max(1, int(keep_last_turns))
    keep_from = 0
    used_msgs = 0
    used_chars = 0
    used_tokens = 0
    for ti in range(len(turns) - 1, -1, -1):
        tm, tc, tk = _turn_size(turns[ti])
        is_hard_floor = ti >= len(turns) - floor
        if is_hard_floor:
            keep_from = ti
            used_msgs += tm
            used_chars += tc
            used_tokens += tk
            continue
        if not _over_budget(used_msgs + tm, used_chars + tc, used_tokens + tk, max_messages, max_chars, max_tokens):
            keep_from = ti
            used_msgs += tm
            used_chars += tc
            used_tokens += tk
        else:
            break

    folded_turns = [list(t) for t in turns[:keep_from]]
    kept_turns = [list(t) for t in turns[keep_from:]]

    # 兜底削减：硬底 turn 仍超预算时，从保留区最旧的 tool_group/system_note 并入折叠区
    if kept_turns and _over_budget(used_msgs, used_chars, used_tokens, max_messages, max_chars, max_tokens):
        while kept_turns and _over_budget(used_msgs, used_chars, used_tokens, max_messages, max_chars, max_tokens):
            target = None
            for turn in kept_turns:
                for ui, u in enumerate(turn):
                    if u.kind in (KIND_TOOL_GROUP, KIND_SYSTEM_NOTE):
                        target = (turn, ui, u)
                        break
                if target:
                    break
            if target is None:
                break  # 只剩 user_text/assistant_text 硬底，接受软超
            turn, ui, u = target
            turn.pop(ui)
            if not turn:
                kept_turns.remove(turn)
            folded_turns.append([u])
            used_msgs -= len(u.messages)
            used_chars -= u.chars
            used_tokens -= u.tokens
        # 保持 folded_turns 时序：兜底单元来自保留区最旧侧，追加到折叠区末尾即有序

    if not folded_turns:
        return list(messages)

    # 发生折叠：摘出旧占位符（任意位置），并入新摘要
    for turn_list in (folded_turns, kept_turns):
        for turn in turn_list:
            for ui in range(len(turn) - 1, -1, -1):
                u = turn[ui]
                if u.kind == KIND_SYSTEM_NOTE and str(u.messages[0].get("content") or "").startswith(FOLD_MARKER):
                    prev_summary = str(u.messages[0].get("content"))
                    turn.pop(ui)

    # 首个 user_text 原消息钉住（若落入折叠区则取出）
    pinned_first_user: dict | None = None
    for turn in folded_turns:
        for u in turn:
            if u.is_turn_start:
                pinned_first_user = u.messages[0]
                u.messages.pop(0)
                u.is_turn_start = False
                break
        if pinned_first_user is not None:
            break

    # 清理空单元/空 turn（重建列表，避免迭代中删除）
    for turn_list in (folded_turns, kept_turns):
        cleaned = []
        for turn in turn_list:
            turn[:] = [u for u in turn if u.messages]
            if turn:
                cleaned.append(turn)
        turn_list[:] = cleaned

    folded_flat = [u for turn in folded_turns for u in turn]
    out: list[dict] = [head]
    if folded_flat:
        out.append({"role": "system", "content": build_fold_summary(folded_flat, prev_summary, fold_summary_max_chars)})
    if pinned_first_user is not None:
        out.append(pinned_first_user)
    for turn in kept_turns:
        for u in turn:
            out.extend(u.messages)
    return out


def count_tool_calls(messages: list[dict]) -> int:
    """统计消息序列中的工具调用次数（两协议形状；供折叠统计用）"""
    n = 0
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        n += len(msg.get("tool_calls") or [])
        content = msg.get("content")
        if isinstance(content, list):
            n += sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use")
    return n


# ── 配对校验 ──────────────────────────────────────


def validate_pairing(messages: list[dict], protocol: str) -> list[str]:
    """校验 tool_call/tool_result 配对不变量，返回违规描述列表（空 = 合法）"""
    errors: list[str] = []
    pending: dict[str, str] = {}

    def flush_pending(at: int) -> None:
        for tid, name in pending.items():
            errors.append(f"悬空 tool_call: id={tid} name={name} 缺少 tool 结果（#{at} 处结束）")
        pending.clear()

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        if _is_tool_calls_assistant(msg, protocol):
            if pending:
                flush_pending(idx)  # 新组开始前上一组仍有缺口 → 违规
            for tid, name in _iter_tool_call_ids(msg):
                if tid in pending:
                    errors.append(f"重复 tool_call id={tid}（#{idx}）")
                pending[tid] = name
        elif _is_tool_result(msg, protocol):
            for tid in _iter_tool_result_ids(msg):
                if tid in pending:
                    del pending[tid]
                else:
                    errors.append(f"孤儿 tool 结果: tool_call_id={tid}（#{idx}）")
        elif role == "user" or (role == "assistant" and not _is_tool_calls_assistant(msg, protocol)):
            flush_pending(idx)
        # role=system 不结束配对窗口（组内可夹 system 注释）
    if pending:
        flush_pending(len(messages))
    return errors


def _iter_tool_call_ids(assistant_msg: dict) -> list[tuple[str, str]]:
    pairs = []
    for tc in assistant_msg.get("tool_calls") or []:
        if isinstance(tc, dict):
            fn = tc.get("function")
            name = fn.get("name") if isinstance(fn, dict) else None
            pairs.append((str(tc.get("id") or ""), str(name or "")))
    content = assistant_msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                pairs.append((str(block.get("id") or ""), str(block.get("name") or "")))
    return pairs


def _iter_tool_result_ids(msg: dict) -> list[str]:
    ids = []
    if msg.get("role") == "tool":
        ids.append(str(msg.get("tool_call_id") or ""))
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                ids.append(str(block.get("tool_use_id") or ""))
    return ids
