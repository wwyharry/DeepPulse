"""自选股管理测试"""

from pathlib import Path


class TestWatchlistManager:
    """测试自选股管理核心功能"""

    def test_add_stock(self, tmp_db_path):
        """添加自选股应返回成功"""
        from agent.watchlist import WatchlistManager

        wl = WatchlistManager(db_path=Path(tmp_db_path))
        result = wl.add("600519", group="默认", notes="贵州茅台")
        assert isinstance(result, dict)
        assert result.get("status") == "added" or "code" in result

    def test_remove_stock(self, tmp_db_path):
        """移除自选股应返回成功"""
        from agent.watchlist import WatchlistManager

        wl = WatchlistManager(db_path=Path(tmp_db_path))
        wl.add("600519", group="默认")
        result = wl.remove("600519", "默认")
        assert isinstance(result, dict)

    def test_list_empty(self, tmp_db_path):
        """空自选股列表应返回空"""
        from agent.watchlist import WatchlistManager

        wl = WatchlistManager(db_path=Path(tmp_db_path))
        codes = wl.get_codes("默认")
        assert isinstance(codes, list)
        assert len(codes) == 0

    def test_add_and_list(self, tmp_db_path):
        """添加后应能列出"""
        from agent.watchlist import WatchlistManager

        wl = WatchlistManager(db_path=Path(tmp_db_path))
        wl.add("600519", group="默认")
        wl.add("000001", group="默认")
        codes = wl.get_codes("默认")
        assert "600519" in codes
        assert "000001" in codes

    def test_add_duplicate_handled(self, tmp_db_path):
        """重复添加不应崩溃"""
        from agent.watchlist import WatchlistManager

        wl = WatchlistManager(db_path=Path(tmp_db_path))
        wl.add("600519", group="默认")
        result = wl.add("600519", group="默认")
        assert isinstance(result, dict)

    def test_alert_rule(self, tmp_db_path):
        """设置告警规则应返回成功"""
        from agent.watchlist import WatchlistManager

        wl = WatchlistManager(db_path=Path(tmp_db_path))
        wl.add("600519", group="默认")
        result = wl.add_alert_rule("600519", "price_above", {"threshold": 2000.0})
        assert isinstance(result, dict)

    def test_multiple_groups(self, tmp_db_path):
        """不同分组应独立管理"""
        from agent.watchlist import WatchlistManager

        wl = WatchlistManager(db_path=Path(tmp_db_path))
        wl.add("600519", group="白酒")
        wl.add("000001", group="银行")
        baijiu = wl.get_codes("白酒")
        bank = wl.get_codes("银行")
        assert "600519" in baijiu
        assert "000001" in bank
        assert "600519" not in bank
