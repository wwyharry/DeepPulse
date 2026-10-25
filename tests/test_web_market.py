"""Web 行情/自选/持仓/交易 API 测试：指数 mock、缺价 null、歧义候选、清仓、Timestamp 序列化。

全部离线：新浪 HTTP 层用假 curl_cffi；fetch_quotes 默认 mock 为空（conftest.no_network 不拦实时行情）。
"""

import pytest

from tests.fakes_web import (
    make_quote,
    make_test_client,
    patch_fetch_quotes,
    patch_market_overview,
    patch_sina,
    seed_stock_info,
)


@pytest.fixture(autouse=True)
def offline_quotes(monkeypatch):
    """默认无行情（缺码 → null/'--'）；需要价格的用例自行 patch_fetch_quotes"""
    patch_fetch_quotes(monkeypatch, {})


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "t.duckdb"


@pytest.fixture
def client(tmp_path, db_path):
    with make_test_client(tmp_path, db_path=db_path) as c:
        yield c


# ── GET /api/market/indices ───────────────────────────────────────


class TestIndices:
    def test_indices_name_from_fields0_and_math(self, client, monkeypatch):
        calls = patch_sina(monkeypatch)
        r = client.get("/api/market/indices")
        assert r.status_code == 200
        body = r.json()
        assert body["error"] in (None, "")
        items = {i["code"]: i for i in body["indices"]}
        assert set(items) == {"sh000001", "sz399001", "sz399006"}

        sh = items["sh000001"]
        # name 必须取 fields[0]（名称），绝不能是 fields[1]（开盘价 3300.00）
        assert sh["name"] == "上证指数"
        assert sh["name"] != "3300.00"
        assert sh["price"] == 3250.50
        assert sh["change_amt"] == 50.50
        assert sh["change_pct"] == 1.58
        for field in ("code", "name", "price", "change_amt", "change_pct"):
            assert field in sh
        assert items["sz399001"]["name"] == "深证成指"
        assert items["sz399006"]["name"] == "创业板指"

        # 请求细节：新浪直连必须带 Referer + 三码
        assert calls, "指数请求应走 curl_cffi Session（假会话未捕获到调用）"
        req = calls[0]
        assert req["headers"].get("Referer") == "https://finance.sina.com.cn"
        assert "sh000001" in req["url"] and "sz399001" in req["url"] and "sz399006" in req["url"]

    def test_indices_error_branch(self, client, monkeypatch):
        patch_sina(monkeypatch, Exception("模拟网络故障"))
        r = client.get("/api/market/indices")
        assert r.status_code == 200
        body = r.json()
        assert body["indices"] == []
        assert isinstance(body["error"], str) and body["error"].strip(), "失败必须给错误原因"
        assert "模拟网络故障" in body["error"] or "失败" in body["error"] or "指数" in body["error"]

    def test_market_overview_is_parsed_json_dict(self, client, monkeypatch):
        # market_overview() 返回 JSON 字符串，API 必须 loads 成对象
        patch_market_overview(monkeypatch, {"up_count": 1200, "down_count": 800, "flat_count": 100})
        r = client.get("/api/market/overview")
        assert r.status_code == 200
        overview = r.json()["overview"]
        assert isinstance(overview, dict), "overview 必须是对象不是 JSON 字符串"
        assert overview["up_count"] == 1200
        assert overview["down_count"] == 800


# ── watchlist ─────────────────────────────────────────────────────


class TestWatchlist:
    def test_get_price_null_when_quote_missing(self, client, monkeypatch, db_path):
        seed_stock_info(db_path, [("600519", "贵州茅台")])
        r = client.post(
            "/api/watchlist",
            json={"code": "600519", "group": "核心", "target_price": 1900, "stop_loss": 1700, "notes": "长线"},
        )
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "code": "600519", "name": "贵州茅台", "group": "核心"}

        # 无行情：price/change_pct 为 null，前端显示 '--'，不是 0 更不是字符串
        patch_fetch_quotes(monkeypatch, {})
        body = client.get("/api/watchlist").json()
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert set(item) == {"code", "name", "group", "price", "change_pct", "target_price", "stop_loss", "notes"}
        assert item["code"] == "600519"
        assert item["name"] == "贵州茅台"
        assert item["group"] == "核心"
        assert item["price"] is None
        assert item["change_pct"] is None
        assert item["target_price"] == 1900
        assert item["stop_loss"] == 1700
        assert item["notes"] == "长线"

        # 有行情：数值型（前端负责格式化），volume 口径保持股
        patch_fetch_quotes(monkeypatch, {"600519": make_quote("600519", current=1856.0, yesterday=1833.4)})
        item = client.get("/api/watchlist").json()["items"][0]
        assert item["price"] == 1856.0
        assert isinstance(item["price"], float)
        assert item["change_pct"] == pytest.approx(1.23, abs=0.01)
        assert item.get("volume") == 12345678, "volume 必须保持「股」不转手"

    def test_nan_target_price_serializes_as_null_not_500(self, client, db_path):
        """回归：pandas fetchdf 把 SQL NULL 读成 NaN → JSON 序列化 500（实机日志 target_price nan）"""
        from deeppulse.src.database import get_connection

        seed_stock_info(db_path, [("600519", "贵州茅台")])
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO watchlist (code, name, group_name, target_price, stop_loss_price, notes) VALUES (?,?,?,?,?,?)",
            ["600519", "贵州茅台", "默认", float("nan"), float("nan"), ""],
        )
        conn.close()

        r = client.get("/api/watchlist")
        assert r.status_code == 200, r.text
        item = r.json()["items"][0]
        assert item["target_price"] is None
        assert item["stop_loss"] is None
        # 反证：若序列化层漏网，响应里会出现非法 NaN 字面量
        assert "NaN" not in r.text

    def test_post_unrecognized_returns_400_with_candidates(self, client, db_path):
        seed_stock_info(db_path, [("000001", "平安银行"), ("601318", "平安保险")])
        r = client.post("/api/watchlist", json={"code": "平安"})
        assert r.status_code == 400
        body = r.json()
        assert "无法识别" in body.get("detail", "") or "歧义" in body.get("detail", "")
        candidates = body.get("candidates")
        assert isinstance(candidates, list) and len(candidates) == 2
        assert {c["code"] for c in candidates} == {"000001", "601318"}
        for c in candidates:
            assert set(c) == {"code", "name"}

        # 完全无法识别（非 6 位数字）→ 400 + 中文原因（无候选时可不带 candidates）
        r2 = client.post("/api/watchlist", json={"code": "★根本不是股票★"})
        assert r2.status_code == 400
        assert r2.json().get("detail", "").strip()

    def test_delete_empty_op_and_wrong_group(self, client, db_path):
        seed_stock_info(db_path, [("600519", "贵州茅台")])
        # 从没加过 → 400（绝不静默空操作）
        r = client.delete("/api/watchlist/600519?group=默认")
        assert r.status_code == 400
        assert "不在" in r.json().get("detail", "") or "无需删除" in r.json().get("detail", "")

        assert client.post("/api/watchlist", json={"code": "600519", "group": "甲"}).status_code == 200
        # 分组不符 → 400 + groups 提示可重试的分组
        r2 = client.delete("/api/watchlist/600519?group=默认")
        assert r2.status_code == 400
        assert r2.json().get("groups") == ["甲"]
        assert "分组" in r2.json().get("detail", "")
        # 带对分组 → 成功
        r3 = client.delete("/api/watchlist/600519?group=甲")
        assert r3.status_code == 200
        assert r3.json() == {"status": "ok", "code": "600519", "name": "贵州茅台", "group": "甲", "removed": 1}
        assert client.get("/api/watchlist").json()["items"] == []


# ── portfolio / trades ────────────────────────────────────────────


class TestPortfolioTrades:
    def test_post_trade_and_portfolio_math(self, client, monkeypatch, db_path):
        seed_stock_info(db_path, [("600519", "贵州茅台")])
        r = client.post(
            "/api/trades",
            json={"code": "600519", "action": "买入", "price": 1000, "shares": 100, "reason": "试仓"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["action"] == "买入"
        assert body["code"] == "600519"
        assert body["price"] == 1000
        assert body["shares"] == 100

        # 无行情：price/profit 为 null
        body = client.get("/api/portfolio").json()
        item = body["items"][0]
        assert set(item) == {"code", "name", "shares", "avg_cost", "price", "profit", "profit_pct"}
        assert item["shares"] == 100
        assert item["avg_cost"] == 1000
        assert item["price"] is None
        assert item["profit"] is None

        # 有行情：现价盈亏按 fetch_quotes 现价计算
        patch_fetch_quotes(monkeypatch, {"600519": make_quote("600519", current=1856.0, yesterday=1833.4)})
        item = client.get("/api/portfolio").json()["items"][0]
        assert item["price"] == 1856.0
        assert item["profit"] == pytest.approx((1856.0 - 1000) * 100)
        assert item["profit_pct"] == pytest.approx((1856.0 - 1000) / 1000 * 100)

    def test_trades_history_serializes_timestamps(self, client, db_path):
        seed_stock_info(db_path, [("600519", "贵州茅台")])
        assert (
            client.post("/api/trades", json={"code": "600519", "action": "buy", "price": 10, "shares": 100}).status_code
            == 200
        )
        r = client.get("/api/trades?days=7")
        assert r.status_code == 200, "pandas.Timestamp 序列化不得炸"
        trades = r.json()["trades"]
        assert len(trades) == 1
        t = trades[0]
        assert t["code"] == "600519"
        assert t["action"] == "买入"  # 别名归一（journal 端）
        assert t["price"] == 10
        assert t["shares"] == 100
        # 时间列必须已序列化为字符串
        assert isinstance(t.get("trade_date", ""), str)
        # portfolio 契约只有七键、不输出任何时间列（更没有 first_buy_date）：
        # 集合断言钉死形状，杜绝 .get(..., "") 型空转断言
        r2 = client.get("/api/portfolio")
        assert r2.status_code == 200
        p = r2.json()["items"][0]
        assert set(p) == {"code", "name", "shares", "avg_cost", "price", "profit", "profit_pct"}
        assert "first_buy_date" not in p

    def test_clear_position_ignores_shares(self, client, monkeypatch, db_path):
        seed_stock_info(db_path, [("600519", "贵州茅台")])
        assert (
            client.post(
                "/api/trades", json={"code": "600519", "action": "买入", "price": 1000, "shares": 100}
            ).status_code
            == 200
        )
        # 清仓：shares 任意，以实际持仓为准
        r = client.post("/api/trades", json={"code": "600519", "action": "清仓", "price": 1100, "shares": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["action"] == "卖出"
        assert body["shares"] == 100, "清仓应按实际持仓 100 股记账，不是请求里的 1"
        assert client.get("/api/portfolio").json()["items"] == []

    def test_trade_errors_are_400_with_chinese_detail(self, client, db_path):
        seed_stock_info(db_path, [("600519", "贵州茅台")])
        # 非法 action
        r = client.post("/api/trades", json={"code": "600519", "action": "梭哈", "price": 1, "shares": 1})
        assert r.status_code == 400
        assert "action" in r.json().get("detail", "") or "不支持" in r.json().get("detail", "")
        # 无法识别的代码 → 400 + 中文原因（有歧义候选时才附 candidates）
        r2 = client.post("/api/trades", json={"code": "???", "action": "买入", "price": 1, "shares": 1})
        assert r2.status_code == 400
        assert r2.json().get("detail", "").strip()
        # 无持仓卖出 → 400
        r3 = client.post("/api/trades", json={"code": "600519", "action": "卖出", "price": 1, "shares": 1})
        assert r3.status_code == 400
        assert "持仓" in r3.json().get("detail", "") or "卖出" in r3.json().get("detail", "")


# ── JSON 序列化安全（NaN/Inf → null） ─────────────────────────────


class TestJsonSafety:
    """enc / sse_frame / SafeJSONResponse：非有限浮点一律 null（JSON 规范禁 NaN/Inf）"""

    def test_enc_maps_nan_inf_to_null(self):
        import math

        from web.api.common import enc

        out = enc({"a": float("nan"), "b": float("inf"), "c": float("-inf"), "d": 1.5, "e": [float("nan"), 2]})
        assert out == {"a": None, "b": None, "c": None, "d": 1.5, "e": [None, 2]}
        assert out["d"] == 1.5 and not math.isfinite(float("nan"))  # 正常值不受影响

    def test_enc_handles_numpy_scalars(self):
        import numpy as np

        from web.api.common import enc

        out = enc({"x": np.float64("nan"), "y": np.float32("nan"), "z": np.float64(2.5), "i": np.int64(3)})
        assert out["x"] is None
        assert out["y"] is None
        assert out["z"] == 2.5
        assert out["i"] == 3

    def test_sse_frame_with_nan_is_json_parseable(self):
        import json

        from web.api.common import sse_frame

        frame = sse_frame("content", {"text": "x", "n": float("nan")})
        payload = frame.split("data: ", 1)[1].rsplit("\n", 1)[0]
        data = json.loads(payload)  # 浏览器 JSON.parse 同口径：不允许 NaN 字面量
        assert data == {"text": "x", "n": None}

    def test_safe_json_response_renders_nan_as_null(self):
        from web.api.common import SafeJSONResponse

        resp = SafeJSONResponse({"v": float("nan")})
        assert resp.body == b'{"v":null}'

    def test_timestamp_still_stringified(self):
        import pandas as pd

        from web.api.common import enc

        out = enc({"t": pd.Timestamp("2026-10-17 09:30:00")})
        assert isinstance(out["t"], str) and out["t"].startswith("2026-10-17")
