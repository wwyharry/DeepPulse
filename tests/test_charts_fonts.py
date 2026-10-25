"""图表中文字体选择器测试 —— 必须按真实安装集匹配，不许「请求名=可用」的假检测"""

from deeppulse.agent.charts import _CJK_FONT_CANDIDATES, pick_cjk_font


class TestPickCjkFont:
    def test_picks_first_installed_candidate(self):
        """候选顺序优先：macOS 装了 PingFang 就选它，不被排前面的 Windows 字体抢"""
        installed = {"DejaVu Sans", "PingFang SC", "Arial Unicode MS"}
        assert pick_cjk_font(installed) == "PingFang SC"

    def test_missing_requested_font_not_faked(self):
        """回归：FontProperties.get_name() 只回显请求名——缺失字体绝不能被当成可用"""
        installed = {"DejaVu Sans", "SimHei"}
        picked = pick_cjk_font(installed)
        assert picked == "SimHei"
        assert picked in installed, "选中的字体必须真在安装集里"

    def test_no_cjk_font_returns_none(self):
        assert pick_cjk_font({"DejaVu Sans"}) is None
        assert pick_cjk_font(set()) is None

    def test_real_environment_pick_is_installed_or_none(self):
        """实机：返回的字体名必须真的安装（或 None），杜绝再落 DejaVu 缺字"""
        from matplotlib import font_manager

        installed = {f.name for f in font_manager.fontManager.ttflist}
        picked = pick_cjk_font()
        assert picked is None or picked in installed

    def test_candidate_list_covers_three_platforms(self):
        joined = " ".join(_CJK_FONT_CANDIDATES)
        assert "PingFang SC" in joined  # macOS
        assert "Microsoft YaHei" in joined  # Windows
        assert "Noto Sans CJK SC" in joined  # Linux
