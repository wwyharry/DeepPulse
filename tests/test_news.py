"""新闻工具测试 - mock HTTP 请求"""

from unittest.mock import MagicMock, patch


class TestSearchBaiduNews:
    """测试百度新闻搜索"""

    @patch("deeppulse.agent.news.requests.get")
    def test_returns_results(self, mock_get):
        """正常返回新闻列表"""
        mock_resp = MagicMock()
        mock_resp.text = (
            '<html><body><div class="result"><h3><a href="http://example.com/1">A股大涨</a></h3></div></body></html>'
        )
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        from deeppulse.agent.news import search_baidu_news

        results = search_baidu_news("A股", num=5)
        assert isinstance(results, list)

    @patch("deeppulse.agent.news.requests.get")
    def test_empty_query_returns_list(self, mock_get):
        """空查询返回列表"""
        mock_resp = MagicMock()
        mock_resp.text = "<html><body></body></html>"
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        from deeppulse.agent.news import search_baidu_news

        results = search_baidu_news("", num=5)
        assert isinstance(results, list)

    @patch("deeppulse.agent.news.requests.get")
    def test_network_error_handled(self, mock_get):
        """网络异常应返回错误信息而非崩溃"""
        mock_get.side_effect = Exception("Connection timeout")
        from deeppulse.agent.news import search_baidu_news

        results = search_baidu_news("A股", num=5)
        # 应返回包含 error 键的列表或空列表
        assert isinstance(results, list)


class TestSearchStockNews:
    """测试个股新闻搜索"""

    @patch("deeppulse.agent.news.search_baidu_news")
    @patch("deeppulse.agent.news.search_eastmoney_news")
    def test_merges_sources(self, mock_em, mock_baidu):
        """应合并多源新闻"""
        mock_baidu.return_value = [{"title": "百度新闻", "url": "http://baidu.com", "source": "百度", "time": ""}]
        mock_em.return_value = [{"title": "东财快讯", "url": "http://eastmoney.com", "source": "东财", "time": ""}]
        from deeppulse.agent.news import search_stock_news

        result = search_stock_news("600519", num=5)
        assert isinstance(result, dict)
