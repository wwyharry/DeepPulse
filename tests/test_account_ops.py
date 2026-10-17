"""账户写操作链路测试 - 自选/持仓增删改「说了就要生效」
覆盖：名称→代码归一、remove 如实返回、交易归一与清仓、识别失败拒写库。
"""

from pathlib import Path


def _seed(db_path, stocks: list[tuple[str, str]]):
    """初始化表结构并写入 stock_info 行"""
    from deeppulse.src.database import get_connection, init_tables

    conn = get_connection(Path(db_path))
    init_tables(conn)
    for code, name in stocks:
        conn.execute("INSERT OR REPLACE INTO stock_info (code, name) VALUES (?, ?)", [code, name])
    conn.close()


class TestResolveStock:
    def _query(self, db_path):
        from deeppulse.src.query import StockQuery

        return StockQuery(db_path=Path(db_path))

    def test_six_digit_code_without_info_row(self, tmp_db_path):
        """6位代码即使不在 stock_info 也视为有效（新股/未入库不阻断写操作）"""
        _seed(tmp_db_path, [])
        info, candidates = self._query(tmp_db_path).resolve_stock("600519")
        assert info == {"code": "600519", "name": ""}
        assert candidates == []

    def test_name_resolves_to_code(self, tmp_db_path):
        _seed(tmp_db_path, [("600519", "贵州茅台")])
        info, _ = self._query(tmp_db_path).resolve_stock("贵州茅台")
        assert info == {"code": "600519", "name": "贵州茅台"}

    def test_decorated_code_normalized(self, tmp_db_path):
        _seed(tmp_db_path, [])
        q = self._query(tmp_db_path)
        for ref in ("sh600519", "600519.SH", "600519.sz"):
            info, _ = q.resolve_stock(ref)
            assert info["code"] == "600519", ref

    def test_ambiguous_name_returns_candidates(self, tmp_db_path):
        _seed(tmp_db_path, [("600519", "贵州茅台"), ("6005190", "茅台科技")])
        info, candidates = self._query(tmp_db_path).resolve_stock("茅台")
        assert info is None
        assert len(candidates) >= 2

    def test_unknown_ref(self, tmp_db_path):
        _seed(tmp_db_path, [("600519", "贵州茅台")])
        info, candidates = self._query(tmp_db_path).resolve_stock("不存在的股票")
        assert info is None
        assert candidates == []

    def test_empty_ref(self, tmp_db_path):
        _seed(tmp_db_path, [])
        assert self._query(tmp_db_path).resolve_stock("") == (None, [])
        assert self._query(tmp_db_path).resolve_stock(None) == (None, [])


class TestWatchlistHonestOps:
    def _wl(self, db_path):
        from deeppulse.agent.watchlist import WatchlistManager

        _seed(db_path, [("600519", "贵州茅台"), ("000001", "平安银行")])
        return WatchlistManager(db_path=Path(db_path))

    def test_add_by_name_stores_code(self, tmp_db_path):
        """名称入库：落库的是代码，名称自动填充"""
        wl = self._wl(tmp_db_path)
        result = wl.add("贵州茅台", group="默认")
        assert result["status"] == "ok"
        assert result["code"] == "600519"
        assert result["name"] == "贵州茅台"
        assert "600519" in wl.get_codes("默认")

    def test_add_unresolvable_ref_rejected(self, tmp_db_path):
        """识别不了的引用拒绝写库——不再产生垃圾行"""
        wl = self._wl(tmp_db_path)
        result = wl.add("神秘代码", group="默认")
        assert result["status"] == "error"
        assert wl.get_codes("默认") == []

    def test_remove_missing_is_error_not_silent_ok(self, tmp_db_path):
        """删了不存在的：如实报错，绝不静默空操作报成功"""
        wl = self._wl(tmp_db_path)
        result = wl.remove("600519", "默认")
        assert result["status"] == "error"
        assert "不在自选股" in result["message"]

    def test_remove_wrong_group_hints_actual_groups(self, tmp_db_path):
        """删错分组：报错并列出实际分组，LLM 可带正确 group 重试"""
        wl = self._wl(tmp_db_path)
        wl.add("600519", group="白酒")
        result = wl.remove("贵州茅台", "默认")
        assert result["status"] == "error"
        assert result["groups"] == ["白酒"]
        assert "600519" in wl.get_codes("白酒")  # 原样还在

    def test_remove_by_name_works(self, tmp_db_path):
        wl = self._wl(tmp_db_path)
        wl.add("600519", group="默认")
        result = wl.remove("贵州茅台", "默认")
        assert result["status"] == "ok"
        assert result["removed"] == 1
        assert wl.get_codes("默认") == []

    def test_last_remove_cleans_alert_rules(self, tmp_db_path):
        """最后一份删除后级联清理告警规则（不留永不触发的死规则）"""
        wl = self._wl(tmp_db_path)
        wl.add("600519", group="默认")
        wl.add_alert_rule("600519", "price_above", {"threshold": 2000.0})
        assert len(wl.get_alert_rules("600519")) == 1
        wl.remove("600519", "默认")
        assert wl.get_alert_rules("600519") == []


class TestJournalNormalizedWrites:
    def _journal(self, db_path):
        from deeppulse.agent.journal import TradeJournal

        _seed(db_path, [("600519", "贵州茅台")])
        return TradeJournal(db_path=Path(db_path))

    def test_record_trade_by_name_resolves_code(self, tmp_db_path):
        """名称记账：落库代码并自动补名称"""
        j = self._journal(tmp_db_path)
        result = j.record_trade("贵州茅台", "买入", 1800.0, 100)
        assert result.get("status") in ("recorded", "ok") or "code" in result
        portfolio = j.get_portfolio()
        assert any(p.get("code") == "600519" for p in portfolio)
        assert portfolio[0].get("name") == "贵州茅台"

    def test_liquidate_uses_actual_holding(self, tmp_db_path):
        """清仓忽略调用方数量，按实际持仓全量卖出"""
        j = self._journal(tmp_db_path)
        j.record_trade("600519", "买入", 1800.0, 200)
        result = j.record_trade("600519", "清仓", 1850.0, 99999)
        assert result.get("status") in ("recorded", "ok") or "code" in result
        held = [p for p in j.get_portfolio() if p.get("code") == "600519"]
        assert not held or held[0].get("shares", 0) == 0

    def test_unknown_ref_rejected_without_journal_rows(self, tmp_db_path):
        """识别失败拒写：不产生脏流水/脏持仓"""
        j = self._journal(tmp_db_path)
        result = j.record_trade("神秘代码", "买入", 10.0, 100)
        assert result["status"] == "error"
        assert j.get_portfolio() == []
        assert j.get_trade_history(days=7) == []

    def test_sell_over_holding_rejected(self, tmp_db_path):
        j = self._journal(tmp_db_path)
        j.record_trade("600519", "买入", 1800.0, 100)
        result = j.record_trade("600519", "卖出", 1850.0, 500)
        assert result["status"] == "error"
        assert "超过持仓" in result["message"]


class TestToolsLayerAccountOps:
    """tools 层（LLM 实际调用的入口）行为对齐"""

    def _setup(self, db_path, monkeypatch):
        from deeppulse.agent.journal import TradeJournal
        from deeppulse.agent.tools import portfolio as portfolio_tools
        from deeppulse.agent.watchlist import WatchlistManager

        _seed(db_path, [("600519", "贵州茅台")])
        monkeypatch.setattr(portfolio_tools, "_watchlist", WatchlistManager(db_path=Path(db_path)))
        monkeypatch.setattr(portfolio_tools, "_journal", TradeJournal(db_path=Path(db_path)))

    def test_record_trade_liquidate_any_shares(self, tmp_db_path, monkeypatch):
        import json

        from deeppulse.agent.tools.portfolio import record_trade

        self._setup(tmp_db_path, monkeypatch)
        record_trade("600519", "买入", 1800.0, 200)
        result = json.loads(record_trade("贵州茅台", "清仓", 1850.0, 99999))
        assert result.get("status") in ("recorded", "ok") or "code" in result

    def test_remove_from_watchlist_reports_noop(self, tmp_db_path, monkeypatch):
        import json

        from deeppulse.agent.tools.portfolio import remove_from_watchlist

        self._setup(tmp_db_path, monkeypatch)
        result = json.loads(remove_from_watchlist("600519"))
        assert result["status"] == "error"
        assert "不在自选股" in result["message"]
