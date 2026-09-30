"""Unit tests for agent/reasoning.py dynamic prompts and market-state detection."""

from datetime import date
from unittest.mock import patch

import deeppulse.agent.reasoning as reasoning_mod
from deeppulse.agent.prompts import build_system_prompt
from deeppulse.agent.reasoning import (
    MARKET_STATE_STRATEGIES,
    build_dynamic_system_prompt,
    detect_market_state,
    get_analysis_strategy,
)


class TestBuildSystemPrompt:
    def test_date_injection(self):
        p = build_system_prompt(date(2026, 9, 30))
        assert "2026-09-30（周三）" in p

    def test_data_truth_rule_first(self):
        p = build_system_prompt()
        assert p.index("数据真实性铁律") < p.index("工具全景")

    def test_every_tool_mentioned(self):
        """工具目录与系统提示词对账：每个注册工具都必须在提示词中出现（防漂移）"""
        from deeppulse.agent.tools import TOOL_DEFINITIONS

        p = build_system_prompt()
        missing = [d["function"]["name"] for d in TOOL_DEFINITIONS if d["function"]["name"] not in p]
        assert not missing, f"系统提示词缺少工具说明: {missing}"

    def test_no_false_browser_claim(self):
        # generate_chart 工具以 auto_open=False 调用，提示词不得承诺自动打开浏览器
        assert "浏览器自动打开" not in build_system_prompt()

    def test_analysis_framework_numbering(self):
        p = build_system_prompt()
        assert "10. 用 stock_news" in p
        assert "15. 用 assess_trend" in p
        assert "18. **用 save_memory" in p
        assert p.count("\n16.") == 1  # 无重复编号

    def test_market_hotspot_section(self):
        p = build_system_prompt()
        assert "## 市场热点分析" in p
        assert "7. 综合分析市场情绪和热点方向" in p

    def test_screen_v2_mentioned(self):
        assert "screen_stocks_v2" in build_system_prompt()


class TestBuildDynamicSystemPrompt:
    def test_noop_without_state_or_profile(self):
        assert build_dynamic_system_prompt("BASE") == "BASE"

    def test_market_state_section_newline_separated(self):
        out = build_dynamic_system_prompt("BASE", market_state="高潮期")
        assert out.startswith("BASE\n\n## 当前市场状态: 高潮期")
        assert "1. " in out and "\n2. " in out  # 步骤不得挤在同一行
        assert "### 推荐战法" in out

    def test_profile_dual_keys(self):
        # 数据库英文键
        out = build_dynamic_system_prompt(
            "BASE", user_profile={"trading_style": {"value": "低吸"}, "risk_tolerance": {"value": "稳健"}}
        )
        assert "## 用户画像适配" in out
        assert "- 交易风格: 低吸" in out
        assert "低吸类战法" in out
        assert "控制仓位" in out

    def test_profile_chinese_keys_and_plain_strings(self):
        out = build_dynamic_system_prompt("BASE", user_profile={"交易风格": "打板", "风险偏好": "激进"})
        assert "打板" in out
        assert "进攻性" in out

    def test_unknown_market_state_falls_back(self):
        # 未知状态走默认策略，但 market_state 传入时仍输出状态段
        out = build_dynamic_system_prompt("BASE", market_state="神秘期")
        assert "## 当前市场状态: 神秘期" in out
        assert get_analysis_strategy("神秘期")["risk_level"] == "中"

    def test_all_states_have_strategy(self):
        for state in ("高潮期", "发酵期", "启动期", "低迷期", "冰点期"):
            assert state in MARKET_STATE_STRATEGIES
            s = get_analysis_strategy(state)
            assert s["reasoning"] and s["priority_tools"]


class TestDetectMarketState:
    def _run(self, value):
        reasoning_mod._market_state_cache = None
        with patch("deeppulse.agent.market.get_market_sentiment", return_value=value):
            return detect_market_state(ttl=0)

    def test_parses_rating(self):
        assert self._run({"涨停数": 50, "跌停数": 3, "情绪评级": "📈 发酵期"}) == "发酵期"
        assert self._run({"涨停数": 120, "跌停数": 1, "情绪评级": "🔥 高潮期"}) == "高潮期"
        assert self._run({"涨停数": 5, "跌停数": 40, "情绪评级": "🧊 冰点期"}) == "冰点期"

    def test_non_trading_day_returns_none(self):
        # 无任何涨跌停数据（非交易日/数据未更新）不得误判为冰点期
        assert self._run({"涨停数": 0, "跌停数": 0, "情绪评级": "🧊 冰点期"}) is None

    def test_real_zero_activity_day_still_reports(self):
        # 有跌停但无涨停是真实的冰点特征
        assert self._run({"涨停数": 0, "跌停数": 25, "情绪评级": "🧊 冰点期"}) == "冰点期"

    def test_error_returns_none(self):
        reasoning_mod._market_state_cache = None
        with patch("deeppulse.agent.market.get_market_sentiment", side_effect=RuntimeError("db down")):
            assert detect_market_state(ttl=0) is None

    def test_non_dict_returns_none(self):
        assert self._run(None) is None
        assert self._run("oops") is None

    def test_unparseable_rating_returns_none(self):
        assert self._run({"涨停数": 30, "跌停数": 2, "情绪评级": "未知"}) is None

    def test_result_is_cached(self):
        reasoning_mod._market_state_cache = None
        with patch(
            "deeppulse.agent.market.get_market_sentiment",
            return_value={"涨停数": 50, "跌停数": 3, "情绪评级": "📈 发酵期"},
        ) as mock_fn:
            assert detect_market_state(ttl=300) == "发酵期"
            assert detect_market_state(ttl=300) == "发酵期"
            assert mock_fn.call_count == 1  # 第二次走缓存

    def test_timeout_returns_none(self):
        import time as _time

        def slow():
            _time.sleep(2)
            return {"涨停数": 50, "跌停数": 3, "情绪评级": "📈 发酵期"}

        reasoning_mod._market_state_cache = None
        with patch("deeppulse.agent.market.get_market_sentiment", side_effect=slow):
            assert detect_market_state(ttl=0, timeout=0.1) is None
