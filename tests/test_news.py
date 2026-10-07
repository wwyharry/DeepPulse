"""新闻工具测试 - mock HTTP 请求"""

from unittest.mock import MagicMock, patch


class TestSearchEastmoneyNews:
    """测试东方财富快讯搜索"""

    @patch("deeppulse.agent.news.requests.get")
    def test_returns_results(self, mock_get):
        """正常返回新闻列表"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "list": [
                    {"title": "A股大涨", "content": "<p>沪指收涨</p>", "digest": "", "showtime": "2026-10-07 10:00:00", "art_code": "1"},
                ]
            }
        }
        mock_get.return_value = mock_resp
        from deeppulse.agent.news import search_eastmoney_news

        results = search_eastmoney_news(num=5)
        assert isinstance(results, list)
        assert results[0]["title"] == "A股大涨"
        assert "<" not in results[0]["summary"]  # HTML 标签已清理

    @patch("deeppulse.agent.news.requests.get")
    def test_keyword_filters(self, mock_get):
        """关键词过滤生效"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": {
                "list": [
                    {"title": "贵州茅台业绩", "content": "", "digest": "", "showtime": "", "art_code": "1"},
                    {"title": "宁德时代扩产", "content": "", "digest": "", "showtime": "", "art_code": "2"},
                ]
            }
        }
        mock_get.return_value = mock_resp
        from deeppulse.agent.news import search_eastmoney_news

        results = search_eastmoney_news(keyword="茅台", num=5)
        assert len(results) == 1
        assert "茅台" in results[0]["title"]

    @patch("deeppulse.agent.news.requests.get")
    def test_network_error_handled(self, mock_get):
        """网络异常应返回错误信息而非崩溃"""
        mock_get.side_effect = Exception("Connection timeout")
        from deeppulse.agent.news import search_eastmoney_news

        results = search_eastmoney_news("A股", num=5)
        # 应返回包含 error 键的列表或空列表
        assert isinstance(results, list)


class TestSearchStockNews:
    """测试个股新闻搜索"""

    @patch("deeppulse.agent.news.search_eastmoney_news")
    def test_returns_envelope(self, mock_em):
        """返回标准信封结构（query/total/sources/results）"""
        mock_em.return_value = [{"title": "东财快讯", "url": "http://eastmoney.com", "source": "东财", "time": ""}]
        from deeppulse.agent.news import search_stock_news

        result = search_stock_news("600519", num=5)
        assert isinstance(result, dict)
        assert result["query"] == "600519"
        assert result["sources"] == ["东方财富"]
        assert len(result["results"]) == 1
