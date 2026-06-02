"""Unit tests for agent/market.py — market sentiment logic with mocked data sources."""

from unittest.mock import patch

from agent.market import get_market_sentiment


def _make_zt_result(count, stocks=None):
    """Build a mock limit-up pool result."""
    return {"date": "20240115", "count": count, "stocks": stocks or []}


def _make_dt_result(count):
    return {"date": "20240115", "count": count, "stocks": []}


def _make_zb_result(count):
    return {"date": "20240115", "count": count, "stocks": []}


class TestGetMarketSentiment:
    @patch("agent.market.get_failed_limit_up", return_value=_make_zb_result(5))
    @patch("agent.market.get_limit_down_pool", return_value=_make_dt_result(10))
    @patch("agent.market.get_limit_up_pool")
    def test_high_tide_sentiment(self, mock_zt, mock_dt, mock_zb):
        """80+ limit-ups, 5+ streak, <25% fail rate → 高潮期"""
        stocks = [
            {"code": "000001", "name": "A", "streak": 6, "industry": "科技"},
            {"code": "000002", "name": "B", "streak": 3, "industry": "科技"},
        ]
        # Need 80+ stocks; fill with stubs
        for i in range(78):
            stocks.append({"code": f"000{i + 3:03d}", "name": f"S{i}", "streak": 1, "industry": "科技"})
        mock_zt.return_value = _make_zt_result(80, stocks)

        result = get_market_sentiment("20240115")
        assert "高潮期" in result["情绪评级"]
        assert result["涨停数"] == 80

    @patch("agent.market.get_failed_limit_up", return_value=_make_zb_result(5))
    @patch("agent.market.get_limit_down_pool", return_value=_make_dt_result(10))
    @patch("agent.market.get_limit_up_pool")
    def test_fermentation_sentiment(self, mock_zt, mock_dt, mock_zb):
        """40+ limit-ups, 4+ streak, <35% fail rate → 发酵期"""
        stocks = [
            {"code": f"000{i:03d}", "name": f"S{i}", "streak": 4 if i == 0 else 1, "industry": "医药"}
            for i in range(45)
        ]
        mock_zt.return_value = _make_zt_result(45, stocks)

        result = get_market_sentiment("20240115")
        assert "发酵期" in result["情绪评级"]

    @patch("agent.market.get_failed_limit_up", return_value=_make_zb_result(2))
    @patch("agent.market.get_limit_down_pool", return_value=_make_dt_result(15))
    @patch("agent.market.get_limit_up_pool")
    def test_startup_sentiment(self, mock_zt, mock_dt, mock_zb):
        """20+ limit-ups, 3+ streak → 启动期"""
        stocks = [
            {"code": f"000{i:03d}", "name": f"S{i}", "streak": 3 if i == 0 else 1, "industry": "能源"}
            for i in range(25)
        ]
        mock_zt.return_value = _make_zt_result(25, stocks)

        result = get_market_sentiment("20240115")
        assert "启动期" in result["情绪评级"]

    @patch("agent.market.get_failed_limit_up", return_value=_make_zb_result(1))
    @patch("agent.market.get_limit_down_pool", return_value=_make_dt_result(30))
    @patch("agent.market.get_limit_up_pool")
    def test_sluggish_sentiment(self, mock_zt, mock_dt, mock_zb):
        """10-19 limit-ups → 低迷期"""
        stocks = [{"code": f"000{i:03d}", "name": f"S{i}", "streak": 1, "industry": "银行"} for i in range(15)]
        mock_zt.return_value = _make_zt_result(15, stocks)

        result = get_market_sentiment("20240115")
        assert "低迷期" in result["情绪评级"]

    @patch("agent.market.get_failed_limit_up", return_value=_make_zb_result(0))
    @patch("agent.market.get_limit_down_pool", return_value=_make_dt_result(50))
    @patch("agent.market.get_limit_up_pool")
    def test_freezing_sentiment(self, mock_zt, mock_dt, mock_zb):
        """<10 limit-ups → 冰点期"""
        stocks = [{"code": f"000{i:03d}", "name": f"S{i}", "streak": 1, "industry": "消费"} for i in range(5)]
        mock_zt.return_value = _make_zt_result(5, stocks)

        result = get_market_sentiment("20240115")
        assert "冰点期" in result["情绪评级"]

    @patch("agent.market.get_failed_limit_up", return_value=_make_zb_result(0))
    @patch("agent.market.get_limit_down_pool", return_value=_make_dt_result(0))
    @patch("agent.market.get_limit_up_pool")
    def test_zero_counts(self, mock_zt, mock_dt, mock_zb):
        """All zeros should not crash, should be 冰点期."""
        mock_zt.return_value = _make_zt_result(0, [])

        result = get_market_sentiment("20240115")
        assert result["涨停数"] == 0
        assert result["跌停数"] == 0
        assert "冰点期" in result["情绪评级"]

    @patch("agent.market.get_failed_limit_up", return_value=_make_zb_result(10))
    @patch("agent.market.get_limit_down_pool", return_value=_make_dt_result(5))
    @patch("agent.market.get_limit_up_pool")
    def test_industry_distribution(self, mock_zt, mock_dt, mock_zb):
        """Should aggregate industries correctly."""
        stocks = [
            {"code": "000001", "name": "A", "streak": 1, "industry": "科技"},
            {"code": "000002", "name": "B", "streak": 1, "industry": "科技"},
            {"code": "000003", "name": "C", "streak": 1, "industry": "医药"},
        ]
        mock_zt.return_value = _make_zt_result(3, stocks)

        result = get_market_sentiment("20240115")
        top = result["涨停行业TOP5"]
        assert top.get("科技") == 2
        assert top.get("医药") == 1

    @patch("agent.market.get_failed_limit_up", return_value=_make_zb_result(0))
    @patch("agent.market.get_limit_down_pool", return_value=_make_dt_result(0))
    @patch("agent.market.get_limit_up_pool")
    def test_streak_distribution(self, mock_zt, mock_dt, mock_zb):
        """Should compute streak distribution correctly."""
        stocks = [
            {"code": "000001", "name": "A", "streak": 3, "industry": "科技"},
            {"code": "000002", "name": "B", "streak": 3, "industry": "科技"},
            {"code": "000003", "name": "C", "streak": 1, "industry": "医药"},
        ]
        mock_zt.return_value = _make_zt_result(3, stocks)

        result = get_market_sentiment("20240115")
        dist = result["连板分布"]
        assert dist.get("3板") == 2
        assert dist.get("1板") == 1
        assert result["最大连板"] == 3
