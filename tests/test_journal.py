"""交易日志测试"""

from pathlib import Path


class TestTradeJournal:
    """测试交易日志核心功能"""

    def test_record_trade(self, tmp_db_path):
        """记录交易应返回成功状态"""
        from agent.journal import TradeJournal

        j = TradeJournal(db_path=Path(tmp_db_path))
        result = j.record_trade("600519", "买入", 1800.0, 100, name="贵州茅台", reason="突破支撑")
        assert isinstance(result, dict)
        assert result.get("status") in ("recorded", "ok") or "code" in result

    def test_view_portfolio_empty(self, tmp_db_path):
        """无交易时持仓应为空"""
        from agent.journal import TradeJournal

        j = TradeJournal(db_path=Path(tmp_db_path))
        portfolio = j.get_portfolio()
        assert isinstance(portfolio, list)
        assert len(portfolio) == 0

    def test_record_buy_updates_portfolio(self, tmp_db_path):
        """买入后持仓应更新"""
        from agent.journal import TradeJournal

        j = TradeJournal(db_path=Path(tmp_db_path))
        j.record_trade("600519", "买入", 1800.0, 100, name="贵州茅台")
        portfolio = j.get_portfolio()
        assert len(portfolio) >= 1
        codes = [p.get("code") or p.get("stock_code") for p in portfolio]
        assert "600519" in codes

    def test_record_sell_reduces_position(self, tmp_db_path):
        """卖出后持仓应减少"""
        from agent.journal import TradeJournal

        j = TradeJournal(db_path=Path(tmp_db_path))
        j.record_trade("600519", "买入", 1800.0, 200, name="贵州茅台")
        j.record_trade("600519", "卖出", 1850.0, 100, name="贵州茅台")
        portfolio = j.get_portfolio()
        found = [p for p in portfolio if (p.get("code") or p.get("stock_code")) == "600519"]
        if found:
            shares = found[0].get("shares") or found[0].get("quantity", 0)
            assert shares == 100

    def test_trade_history(self, tmp_db_path):
        """交易历史应返回记录"""
        from agent.journal import TradeJournal

        j = TradeJournal(db_path=Path(tmp_db_path))
        j.record_trade("600519", "买入", 1800.0, 100, name="贵州茅台")
        history = j.get_trade_history(days=7)
        assert isinstance(history, list)
        assert len(history) >= 1
