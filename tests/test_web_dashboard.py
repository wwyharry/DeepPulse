"""主页看板测试 —— 纯事实钉子：禁荐股、多源切换、计数一致性、TTL/stale、日期回退

看板铁律（用户约束）：仅数据整理，绝不掺任何算法推荐/评分/操作建议；
多数据源失效自动切换；涨跌停判定只用池 API/快照原始涨跌幅（复权铁律）。
"""

from datetime import date

import pytest

from tests.fakes_web import (
    assert_no_recommendation,
    guard_sentiment,
    make_snap_df,
    make_test_client,
    patch_indices_with_source,
    patch_news_sources,
    patch_pool,
    patch_sector_ranking,
    patch_sina,
    patch_snapshot,
)
from web.service.dashboard_service import (
    PanelFetcher,
    _prev_workday,
    _walk_pool_date,
    build_ladder,
    build_news_panel,
    clear_dashboard_cache,
    dt_from_snapshot,
    market_stats_from_local,
    market_stats_from_snapshot,
    resolve_pool_date,
    zb_from_snapshot,
    zt_from_snapshot,
)
from web.service.indices import _to_item, fetch_indices_sina

# ── 固定样本（禁词安全：不含任何荐股语汇） ─────────────────────────────────

ZT_STOCKS = [
    {
        "code": "600001",
        "name": "甲电子",
        "price": 10.0,
        "change_pct": 10.0,
        "turnover": 1.2,
        "amount": 3.4,
        "first_time": "09:30:00",
        "last_time": "14:00:00",
        "open_count": 1,
        "streak": 3,
        "industry": "电子",
    },
    {
        "code": "000002",
        "name": "乙股份",
        "price": 11.0,
        "change_pct": 10.0,
        "turnover": 2.2,
        "amount": 4.4,
        "first_time": "09:31:00",
        "last_time": "09:31:00",
        "open_count": 0,
        "streak": 1,
        "industry": "电子",
    },
    {
        "code": "300003",
        "name": "丙创",
        "price": 12.0,
        "change_pct": 20.0,
        "turnover": 3.2,
        "amount": 5.4,
        "first_time": "10:00:00",
        "last_time": "10:00:00",
        "open_count": 0,
        "streak": 2,
        "industry": "医药",
    },
]
ZT_POOL = {"date": "20261017", "count": 3, "stocks": ZT_STOCKS}
DT_POOL = {
    "date": "20261017",
    "count": 1,
    "stocks": [{"code": "600009", "name": "丁跌", "price": 9.0, "change_pct": -10.0}],
}
ZB_POOL = {
    "date": "20261017",
    "count": 2,
    "stocks": [
        {"code": "600010", "name": "戊炸", "price": 9.5, "change_pct": 5.0},
        {"code": "600011", "name": "己炸", "price": 8.5, "change_pct": -2.0},
    ],
}
SECTORS = {
    "type": "行业板块",
    "source": "同花顺详情",
    "sectors": [
        {
            "name": "电子",
            "change_pct": 2.5,
            "inflow": 12.3,
            "up_count": 80,
            "down_count": 20,
            "leader": "甲电子",
            "leader_change": 10.0,
            "amount": 300.0,
        },
    ],
}
NEWS_EM = [
    {
        "title": "东财快讯一",
        "source": "东方财富",
        "time": "2026-10-17 10:00:00",
        "summary": "摘要一",
        "url": "https://e.1",
    }
]
NEWS_SINA = [
    {
        "title": "新浪要闻一",
        "source": "新浪财经",
        "time": "2026-10-17 09:00:00",
        "summary": "摘要二",
        "url": "https://s.1",
    }
]


def patch_happy(
    monkeypatch,
    *,
    zt=ZT_POOL,
    dt=DT_POOL,
    zb=ZB_POOL,
    snap=None,
    sectors=SECTORS,
    em=NEWS_EM,
    sina=NEWS_SINA,
    indices=None,
    indices_err=None,
):
    """全源正样本打桩；返回 calls 计数表供断言"""
    calls = {"zt": [], "dt": [], "zb": [], "snap": [], "sectors": [], "em": [], "sina": [], "idx": []}
    patch_pool(monkeypatch, "zt", zt, calls["zt"])
    patch_pool(monkeypatch, "dt", dt, calls["dt"])
    patch_pool(monkeypatch, "zb", zb, calls["zb"])
    if snap is None:
        snap = make_snap_df(
            [
                {
                    "code": "600001",
                    "name": "甲电子",
                    "price": 10.0,
                    "change_pct": 10.0,
                    "high": 10.0,
                    "prev_close": 9.09,
                    "amount": 123450000.0,
                },
            ]
        )
    if isinstance(snap, Exception):
        patch_snapshot(monkeypatch, exc=snap, calls=calls["snap"])
    else:
        patch_snapshot(monkeypatch, df=snap, calls=calls["snap"])
    patch_sector_ranking(monkeypatch, sectors, calls["sectors"])
    patch_news_sources(monkeypatch, em=em, sina=sina, calls=calls["em"])
    if indices is None:
        indices = [{"code": "sh000001", "name": "上证指数", "price": 3250.5, "change_amt": 50.5, "change_pct": 1.58}]
    patch_indices_with_source(monkeypatch, items=indices, source="sina", err=indices_err, calls=calls["idx"])
    return calls


@pytest.fixture(autouse=True)
def _reset_dashboard():
    clear_dashboard_cache()
    yield
    clear_dashboard_cache()


# ── 纯派生：连板梯队 ────────────────────────────────────────────────────────


class TestBuildLadder:
    def test_tiers_grouped_by_streak_desc(self):
        lad = build_ladder(ZT_POOL)
        assert [t["label"] for t in lad["tiers"]] == ["3板", "2板", "首板"]
        assert [t["streak"] for t in lad["tiers"]] == [3, 2, 1]
        assert lad["max_streak"] == 3
        assert lad["trade_date"] == "2026-10-17"
        for t in lad["tiers"]:
            assert t["count"] == len(t["stocks"])

    def test_unknown_streak_is_single_tier_never_fabricated(self):
        """降级推导的涨停股连板数未知：单列「连板数未知」层，绝不默认 1"""
        lad = build_ladder(
            {
                "date": "20261017",
                "stocks": [
                    {"code": "1", "name": "x", "streak": None},
                    {"code": "2", "name": "y"},
                ],
            }
        )
        assert len(lad["tiers"]) == 1
        assert lad["tiers"][0]["streak"] is None
        assert lad["tiers"][0]["label"] == "连板数未知"
        assert lad["tiers"][0]["count"] == 2
        assert lad["max_streak"] is None
        assert not any(t["streak"] == 1 for t in lad["tiers"]), "禁止把未知捏造成首板"

    def test_mixed_known_and_unknown(self):
        lad = build_ladder(
            {
                "stocks": [
                    {"code": "1", "name": "x", "streak": 2},
                    {"code": "2", "name": "y", "streak": None},
                ]
            }
        )
        assert [t["label"] for t in lad["tiers"]] == ["2板", "连板数未知"]

    def test_empty_and_none(self):
        assert build_ladder(None)["tiers"] == []
        assert build_ladder({"stocks": []})["max_streak"] is None


# ── 纯派生：快照推导三池（复权铁律：只用原始涨跌幅/不复权昨收） ────────────


class TestSnapshotDerivation:
    def _snap(self):
        return make_snap_df(
            [
                # 主板涨停 10% → zt
                {
                    "code": "600001",
                    "name": "甲电子",
                    "price": 11.0,
                    "change_pct": 10.0,
                    "high": 11.0,
                    "prev_close": 10.0,
                },
                # 创业板 20cm 涨 15% 不是涨停（zst 应为空、stats 落 up_gt9）
                {"code": "300003", "name": "丙创", "price": 23.0, "change_pct": 15.0, "high": 23.0, "prev_close": 20.0},
                # 主板跌停 -10% → dt
                {"code": "600009", "name": "丁跌", "price": 9.0, "change_pct": -10.0, "high": 10.0, "prev_close": 10.0},
                # 触板回落：最高 11.00 ≥ 涨停价 10.86（9.87×1.1 四舍五入）但收 10.30 未封 → zb
                {
                    "code": "600010",
                    "name": "戊炸",
                    "price": 10.30,
                    "change_pct": 4.36,
                    "high": 11.0,
                    "prev_close": 9.87,
                },
                # 主板 ST 5%：涨 5% → zt
                {"code": "000012", "name": "庚ST", "price": 10.5, "change_pct": 5.0, "high": 10.5, "prev_close": 10.0},
            ]
        )

    def test_zt_dt_zb_classification_with_tolerance(self):
        snap = self._snap()
        zt = zt_from_snapshot(snap)
        codes = {s["code"] for s in zt["stocks"]}
        assert codes == {"600001", "000012"}, "20cm 板 +15% 不得算涨停；ST 按 5% 判"
        assert all(s["streak"] is None for s in zt["stocks"]), "推导路径绝无连板数"
        assert all(len(s) == 11 for s in zt["stocks"]), "zt 行字段形状须与东财池一致"
        dt = dt_from_snapshot(snap)
        assert {s["code"] for s in dt["stocks"]} == {"600009"}
        assert all(len(s) == 4 for s in dt["stocks"]), "dt/zb 行仅 4 字段"
        zb = zb_from_snapshot(snap)
        assert {s["code"] for s in zb["stocks"]} == {"600010"}, "触板回落算炸板；已封板不算"
        assert all(len(s) == 4 for s in zb["stocks"])

    def test_zb_limit_price_rounds_from_unadjusted_prev_close(self):
        snap = make_snap_df(
            [
                {"code": "600001", "name": "甲", "price": 10.0, "change_pct": 1.3, "high": 10.85, "prev_close": 9.87},
                {"code": "600002", "name": "乙", "price": 10.0, "change_pct": 1.3, "high": 10.84, "prev_close": 9.87},
            ]
        )
        zb = zb_from_snapshot(snap)
        assert {s["code"] for s in zb["stocks"]} == {"600001"}, "涨停价 round(9.87*1.1,2)=10.86，high≥10.85 触板"

    def test_stats_buckets_and_amount(self):
        snap = make_snap_df(
            [
                {
                    "code": "600001",
                    "name": "甲",
                    "price": 11,
                    "change_pct": 10.0,
                    "high": 11,
                    "prev_close": 10,
                    "amount": 1e8,
                },
                {
                    "code": "600002",
                    "name": "乙",
                    "price": 10.5,
                    "change_pct": 5.0,
                    "high": 10.5,
                    "prev_close": 10,
                    "amount": 2e8,
                },
                {
                    "code": "300003",
                    "name": "丙",
                    "price": 11.5,
                    "change_pct": 15.0,
                    "high": 11.5,
                    "prev_close": 10,
                    "amount": 3e8,
                },
                {
                    "code": "600004",
                    "name": "丁",
                    "price": 10,
                    "change_pct": 0.0,
                    "high": 10,
                    "prev_close": 10,
                    "amount": 4e8,
                },
                {
                    "code": "600005",
                    "name": "戊",
                    "price": 9.0,
                    "change_pct": -10.0,
                    "high": 10,
                    "prev_close": 10,
                    "amount": 5e8,
                },
            ]
        )
        stats = market_stats_from_snapshot(snap)
        b = stats["breadth"]
        assert b == {"total": 5, "up": 3, "down": 1, "flat": 1}
        bk = stats["buckets"]
        assert bk["limit_up"] == 1 and bk["up_3_7"] == 1 and bk["up_gt9"] == 1
        assert bk["flat"] == 1 and bk["limit_down"] == 1
        assert stats["amount_total_yi"] == pytest.approx(15.0), "成交额元 → 亿"
        assert isinstance(stats["amount_total_yi"], float)

    def test_stats_no_limit_column_fallbacks(self):
        """非 A 股代码（price_limit_pct 抛 ValueError）不参与涨跌停判定但仍计入家数"""
        snap = make_snap_df(
            [{"code": "999999", "name": "怪", "price": 1, "change_pct": 10.0, "high": 1, "prev_close": 1}]
        )
        stats = market_stats_from_snapshot(snap)
        assert stats["breadth"]["total"] == 1
        assert stats["buckets"]["limit_up"] == 0


# ── 纯派生：本地口径兜底 ────────────────────────────────────────────────────


class TestMarketStatsFromLocal:
    def test_close_vs_prev_close_breadth(self, tmp_path):
        from deeppulse.src.database import get_connection, init_tables, insert_daily_kline, upsert_stock_info

        db = tmp_path / "local.duckdb"
        conn = get_connection(db)
        init_tables(conn)
        upsert_stock_info(
            conn,
            [
                {"code": "600001", "name": "甲电子", "market": "sh", "board": "main", "list_date": None},
                {"code": "600002", "name": "乙股份", "market": "sh", "board": "main", "list_date": None},
                {"code": "600003", "name": "丙平", "market": "sh", "board": "main", "list_date": None},
            ],
        )
        records = []
        for code, closes in (("600001", (10.0, 11.0)), ("600002", (10.0, 9.0)), ("600003", (10.0, 10.0))):
            for d, c in zip(("2026-10-16", "2026-10-17"), closes, strict=True):
                records.append(
                    {
                        "code": code,
                        "trade_date": d,
                        "open": c,
                        "high": c,
                        "low": c,
                        "close": c,
                        "volume": 100,
                        "amount": 1000.0,
                        "turnover": 1.0,
                        "data_source": "test",
                    }
                )
        insert_daily_kline(conn, records)
        conn.close()

        stats = market_stats_from_local(db)
        assert stats["breadth"] == {"total": 3, "up": 1, "down": 1, "flat": 1}, "close vs 前收口径"
        assert stats["amount_total_yi"] is None, "覆盖不足不得给两市合计（宁缺勿造）"
        assert "不足" in stats["note"] and "两市合计" in stats["note"], "note 须披露成交额不给的原因"
        assert "前复权" in stats["note"] and "涨跌停桶不适用" in stats["note"], "口径注记须披露复权失真"
        assert "2026-10-17" in stats["note"], "口径注记须给截面日期（本地库陈旧时可核）"
        assert stats["buckets"]["limit_up"] is None and stats["buckets"]["limit_down"] is None, (
            "复权口径不得判涨跌停（null=未知，不是 0）"
        )
        assert stats["buckets"]["up_gt9"] == 1 and stats["buckets"]["down_gt9"] == 1  # 幅度桶仍按复权收益率如实统计

    def test_local_amount_two_market_sum_with_gate(self, tmp_path, monkeypatch):
        """本地口径两市成交额：沪深名义额求和；北交所不计；缺额行拒给；覆盖门槛不足不给"""
        import web.service.dashboard_service as ds
        from deeppulse.src.database import get_connection, init_tables, insert_daily_kline, upsert_stock_info

        def _build(rows):
            db = tmp_path / "local.duckdb"
            conn = get_connection(db)
            init_tables(conn)
            upsert_stock_info(
                conn,
                [
                    {"code": code, "name": code, "market": "sh", "board": "main", "list_date": None}
                    for code, _amt in rows
                ],
            )
            records = []
            for code, amt in rows:
                for d, c in zip(("2026-10-16", "2026-10-17"), (10.0, 10.5), strict=True):
                    records.append(
                        {
                            "code": code,
                            "trade_date": d,
                            "open": c,
                            "high": c,
                            "low": c,
                            "close": c,
                            "volume": 100,
                            "amount": amt,
                            "turnover": 1.0,
                            "data_source": "test",
                        }
                    )
            insert_daily_kline(conn, records)
            conn.close()
            return db

        # 沪深求和，北交所 9e8 不冒充两市
        db = _build([("600001", 3e8), ("000002", 2e8), ("430003", 9e8)])
        monkeypatch.setattr(ds, "LOCAL_AMOUNT_MIN_COVERED", 2)
        stats = ds.market_stats_from_local(db)
        assert stats["amount_total_yi"] == pytest.approx(5.0), "沪深 3e8+2e8=5 亿；北交所 9e8 不计"
        assert "沪深截面 2 只" in stats["note"], "note 披露求和覆盖"
        # 门槛高过覆盖即不给（宁缺勿造）
        monkeypatch.setattr(ds, "LOCAL_AMOUNT_MIN_COVERED", 3)
        stats = ds.market_stats_from_local(db)
        assert stats["amount_total_yi"] is None, "门槛高过覆盖即不给（宁缺勿造）"
        assert "不足" in stats["note"]
        # 沪深行缺额（NULL）：残缺和不得冒充两市合计（与快照口径同纪律），note 披露缺几只
        monkeypatch.setattr(ds, "LOCAL_AMOUNT_MIN_COVERED", 2)
        db2 = _build([("600001", 3e8), ("000002", 2e8), ("600003", None)])
        stats = ds.market_stats_from_local(db2)
        assert stats["amount_total_yi"] is None, "缺额行不得被静默丢弃后给残缺和"
        assert "缺 1 只" in stats["note"], "note 须披露缺额行数"

    def test_local_amount_nan_is_unknown_not_zero(self, tmp_path, monkeypatch):
        """真 NaN 额按未知计入缺额拒给（duckdb NaN ≠ NULL，守卫 amount == amount 须拦住）"""
        import web.service.dashboard_service as ds
        from deeppulse.src.database import get_connection, init_tables, upsert_stock_info

        db = tmp_path / "local.duckdb"
        conn = get_connection(db)
        init_tables(conn)
        upsert_stock_info(
            conn,
            [
                {"code": "600001", "name": "甲", "market": "sh", "board": "main", "list_date": None},
                {"code": "600002", "name": "乙", "market": "sh", "board": "main", "list_date": None},
            ],
        )
        # pandas 落库会把 NaN 洗成 NULL；原生 INSERT 制造真 NaN 额
        for code, amt_sql in (("600001", "300000000.0"), ("600002", "CAST('NaN' AS DOUBLE)")):
            for d, c in (("2026-10-16", 10.0), ("2026-10-17", 10.5)):
                conn.execute(
                    "INSERT INTO daily_kline (code, trade_date, open, high, low, close, volume, amount, turnover, data_source) "
                    f"VALUES ('{code}', DATE '{d}', {c}, {c}, {c}, {c}, 100, {amt_sql}, 1.0, 'test')"
                )
        conn.close()
        monkeypatch.setattr(ds, "LOCAL_AMOUNT_MIN_COVERED", 1)
        stats = ds.market_stats_from_local(db)
        assert stats["amount_total_yi"] is None, "NaN 额按未知计入缺额，不得折 0 也不得静默丢弃"
        assert "缺 1 只" in stats["note"]

    def test_multi_source_priority_dedup(self, tmp_path):
        """同 code 同日多源冲突：按 config.DATA_SOURCES 优先级取高优先源，不得随机/双计"""
        from deeppulse import config
        from deeppulse.src.database import get_connection, init_tables, insert_daily_kline, upsert_stock_info

        order = list(getattr(config, "DATA_SOURCES", []) or [])
        assert len(order) >= 2, "用例需要至少两个在线源名"
        hi, lo = order[0], order[-1]
        db = tmp_path / "prio.duckdb"
        conn = get_connection(db)
        init_tables(conn)
        upsert_stock_info(conn, [{"code": "600001", "name": "甲", "market": "sh", "board": "main", "list_date": None}])
        insert_daily_kline(
            conn,
            [
                {
                    "code": "600001",
                    "trade_date": "2026-10-16",
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1,
                    "amount": 1.0,
                    "turnover": 1.0,
                    "data_source": hi,
                },
                {
                    "code": "600001",
                    "trade_date": "2026-10-17",
                    "open": 11.0,
                    "high": 11.0,
                    "low": 11.0,
                    "close": 11.0,
                    "volume": 1,
                    "amount": 1.0,
                    "turnover": 1.0,
                    "data_source": lo,
                },  # 低优先源：涨
                {
                    "code": "600001",
                    "trade_date": "2026-10-17",
                    "open": 9.0,
                    "high": 9.0,
                    "low": 9.0,
                    "close": 9.0,
                    "volume": 1,
                    "amount": 1.0,
                    "turnover": 1.0,
                    "data_source": hi,
                },  # 高优先源：跌
            ],
        )
        conn.close()
        stats = market_stats_from_local(db)
        assert stats["breadth"]["down"] == 1 and stats["breadth"]["up"] == 0, "同日冲突须按源优先级取数"

    def test_empty_db_raises(self, tmp_path):
        from deeppulse.src.database import get_connection, init_tables

        db = tmp_path / "empty.duckdb"
        conn = get_connection(db)
        init_tables(conn)
        conn.close()
        with pytest.raises(ValueError):
            market_stats_from_local(db)


# ── 纯派生：新闻合并 ────────────────────────────────────────────────────────


class TestBuildNewsPanel:
    def test_merge_dedup_sort(self):
        prefix = "01234567890123456789共同标题"  # 前 20 字完全一致（>20 字才切得到前缀）
        em = [
            {
                "title": f"{prefix}AAAA",
                "source": "东方财富",
                "time": "2026-10-17 10:00:00",
                "summary": "a",
                "url": "u1",
            },
            {"title": "东财独有", "source": "东方财富", "time": "2026-10-17 11:00:00", "summary": "b", "url": "u2"},
        ]
        sina = [
            {
                "title": f"{prefix}BBBB",
                "source": "新浪财经",
                "time": "2026-10-17 09:00:00",
                "summary": "c",
                "url": "u3",
            },
        ]
        panel = build_news_panel(em, sina)
        # 标题前 20 字去重：后到的同题丢弃
        assert len(panel["items"]) == 2
        assert [i["time"] for i in panel["items"]] == ["2026-10-17 11:00:00", "2026-10-17 10:00:00"], "时间倒序"
        assert set(panel["sources_used"]) == {"东方财富", "新浪财经"}

    def test_none_vs_empty_sources(self):
        panel = build_news_panel(None, [])
        assert panel["items"] == []
        assert panel["sources_used"] == ["新浪财经"], "None=失败不计；[]=在线且空计入"
        panel2 = build_news_panel([], [])
        assert set(panel2["sources_used"]) == {"东方财富", "新浪财经"}


# ── 池日期解析 ──────────────────────────────────────────────────────────────


class TestResolvePoolDate:
    def test_walk_back_to_previous_workday(self):
        seen = []

        def probe(ds):
            seen.append(ds)
            return (
                {"date": ds, "count": 1, "stocks": [{"code": "1"}]}
                if ds == "20261016"
                else {"date": ds, "count": 0, "stocks": []}
            )

        # 2026-10-17 是周六 → 探今天空 → 回退周五 20261016 命中 → 对照周四确认非回声
        assert resolve_pool_date(probe=probe, today=date(2026, 10, 17)) == "20261016"
        assert seen == ["20261017", "20261016", "20261015"]

    def test_echoed_latest_pool_is_not_stamped_as_today(self):
        """东财池回声钉：周日/周六探测返回周五的池并回显查询日，as_of 须落周五（实测 2026-10-18）"""
        seen = []

        def probe(ds):
            seen.append(ds)
            if ds >= "20261016":  # ≥ 最后有池日且无独立池 → 回声（含周五自己的池）
                return {"date": ds, "count": 2, "stocks": [{"code": "1", "streak": 3}, {"code": "2", "streak": 1}]}
            if ds == "20261015":  # 早于最后有池日 → 自己的池
                return {"date": ds, "count": 1, "stocks": [{"code": "3", "streak": 2}]}
            return {"date": ds, "count": 0, "stocks": []}

        assert resolve_pool_date(probe=probe, today=date(2026, 10, 18)) == "20261016"

    def test_weekday_holiday_echo_walks_through_to_last_trading_day(self):
        """工作日节假日同样回声：连续两天节假日须一路回退到最后有池日，不得停在节假日"""

        def probe(ds):
            if ds >= "20261012":  # 周一起回声/周一自己的池（同内容）
                return {"date": ds, "count": 1, "stocks": [{"code": "9", "streak": 2}]}
            return {"date": ds, "count": 1, "stocks": [{"code": "8", "streak": 5}]}

        # 2026-10-13/14 连休（周二三）→ 回退至周一 20261012
        assert resolve_pool_date(probe=probe, today=date(2026, 10, 14)) == "20261012"

    def test_echo_check_failure_accepts_fetched_date(self):
        """对照探测失败无法证伪：采信已取结果，不无限回退"""
        seen = []

        def probe(ds):
            seen.append(ds)
            if ds == "20261018":
                return {"date": ds, "count": 1, "stocks": [{"code": "1", "streak": 1}]}
            raise RuntimeError("对照失败")

        assert resolve_pool_date(probe=probe, today=date(2026, 10, 18)) == "20261018"
        assert seen == ["20261018", "20261016"]

    def test_unending_echo_reports_error_not_wrong_stamp(self):
        """回退窗口内探测结果全同（回声区超窗，疑似数据停更）：报错而非错标日期"""

        def probe(ds):
            return {"date": ds, "count": 1, "stocks": [{"code": "1", "streak": 1}]}

        ds, res, err = _walk_pool_date(probe, date(2026, 10, 18))
        assert err is not None and res is None
        assert ds == "20261018"

    def test_error_does_not_walk_back(self):
        seen = []

        def probe(ds):
            seen.append(ds)
            return {"error": "网络失败"}

        assert resolve_pool_date(probe=probe, today=date(2026, 10, 17)) == "20261017"
        assert seen == ["20261017"], "失败不是无数据，不得回退"

    def test_exception_does_not_walk_back(self):
        seen = []

        def probe(ds):
            seen.append(ds)
            raise RuntimeError("boom")

        assert resolve_pool_date(probe=probe, today=date(2026, 10, 17)) == "20261017"
        assert seen == ["20261017"]

    def test_all_empty_falls_back_to_today(self):
        def probe(ds):
            return {"date": ds, "count": 0, "stocks": []}

        assert resolve_pool_date(probe=probe, today=date(2026, 10, 17)) == "20261017"


# ── PanelFetcher 语义 ──────────────────────────────────────────────────────


class TestPanelFetcher:
    def test_error_dict_triggers_failover(self):
        calls = {"a": 0, "b": 0}

        def src_a():
            calls["a"] += 1
            return {"error": "源A挂了"}

        def src_b():
            calls["b"] += 1
            return {"payload": 1}

        f = PanelFetcher("t", [("a", src_a), ("b", src_b)], ttl=60)
        r = f.get()
        assert r.data == {"payload": 1} and r.source == "b" and r.error is None and not r.stale
        assert calls == {"a": 1, "b": 1}

    def test_exception_and_none_failover(self):
        def bad():
            raise RuntimeError("boom")

        def empty():
            return None

        def good():
            return [1]

        f = PanelFetcher("t", [("bad", bad), ("empty", empty), ("good", good)], ttl=60)
        r = f.get()
        assert r.data == [1] and r.source == "good"

    def test_empty_pool_is_legal_result(self):
        calls = []

        def empty_pool():
            calls.append(1)
            return {"date": "20261017", "count": 0, "stocks": []}

        f = PanelFetcher("t", [("only", empty_pool)], ttl=60)
        r = f.get()
        assert r.error is None and r.source == "only" and r.data["count"] == 0
        assert len(calls) == 1

    def test_ttl_and_force(self):
        calls = []

        def src():
            calls.append(1)
            return {"n": len(calls)}

        f = PanelFetcher("t", [("s", src)], ttl=60)
        assert f.get().data == {"n": 1}
        assert f.get().data == {"n": 1}, "TTL 内不得重复取数"
        assert len(calls) == 1
        assert f.get(force=True).data == {"n": 2}
        assert len(calls) == 2

    def test_stale_if_error(self):
        state = {"fail": False}

        def src():
            if state["fail"]:
                return {"error": "挂了"}
            return {"v": 1}

        f = PanelFetcher("t", [("s", src)], ttl=60)
        r1 = f.get()
        assert r1.stale is False
        state["fail"] = True
        r2 = f.get(force=True)
        assert r2.stale is True and r2.data == {"v": 1} and r2.error is not None

    def test_breaker_skips_open_source(self):
        calls = {"a": 0, "b": 0}
        b_script = [{"error": "挂"}, {"ok": 1}, {"error": "又挂了"}]

        def src_a():
            calls["a"] += 1
            return {"error": "挂"}

        def src_b():
            calls["b"] += 1
            return b_script[calls["b"] - 1]

        f = PanelFetcher("t", [("a", src_a), ("b", src_b)], ttl=0)
        f.get()  # a 失败 1、b 失败 1 → 全败
        f.get()  # a 失败 2（熔断开）、b 成功 → 活动源=b
        assert calls["a"] == 2 and calls["b"] == 2
        r = f.get()  # b 再败 → 试 a → a 熔断中须跳过 → 回吐 b 的过期成功
        assert calls["a"] == 2, "熔断打开后源必须被跳过"
        assert r.stale is True and r.data == {"ok": 1}

    def test_all_fail_without_cache(self):
        f = PanelFetcher("t", [("a", lambda: {"error": "x"}), ("b", lambda: {"error": "y"})], ttl=60)
        r = f.get()
        assert r.data is None and r.stale is False
        assert r.error and "a" in r.error and "b" in r.error


# ── API 层 ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    with make_test_client(tmp_path, db_path=tmp_path / "t.duckdb") as c:
        yield c


class TestDashboardAPI:
    def test_shape_contract(self, client, monkeypatch):
        patch_happy(monkeypatch)
        guard_sentiment(monkeypatch)
        r = client.get("/api/dashboard")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"updated_at", "as_of", "session", "panels"}
        assert body["session"] in ("trading", "closed")
        assert set(body["panels"]) == {
            "overview",
            "ladder",
            "limit_up",
            "failed",
            "limit_down",
            "sectors",
            "news",
        }
        for name, panel in body["panels"].items():
            assert set(panel) == {"data", "error", "source", "stale"}, name

    def test_ban_recommendation_language(self, client, monkeypatch):
        """核心钉：响应体任何角落不得含荐股语义；get_market_sentiment 一次都不许调"""
        patch_happy(monkeypatch)
        guard_sentiment(monkeypatch)
        body = client.get("/api/dashboard").json()
        assert_no_recommendation(body)
        # 各面板逐一过禁词（含错误分支文案）
        body2 = client.get("/api/dashboard?refresh=1").json()
        assert_no_recommendation(body2)

    def test_ban_recommendation_language_on_error_branches(self, client, monkeypatch):
        """错误分支文案同样过禁词（全源失败/部分失败两个形态）"""
        patch_happy(
            monkeypatch,
            zt={"error": "东财涨停池挂了"},
            sectors=RuntimeError("板块炸了"),
            em=[{"error": "东财快讯挂了"}],
        )
        guard_sentiment(monkeypatch)
        assert_no_recommendation(client.get("/api/dashboard").json())
        clear_dashboard_cache()
        patch_happy(
            monkeypatch,
            zt={"error": "z"},
            dt={"error": "d"},
            zb={"error": "b"},
            snap=RuntimeError("s"),
            sectors=RuntimeError("sec"),
            em=[{"error": "e"}],
            sina=[{"error": "si"}],
            indices=[],
            indices_err="指数双源挂了",
        )
        clear_dashboard_cache()
        assert_no_recommendation(client.get("/api/dashboard").json())

    def test_counts_consistency_and_numeric_types(self, client, monkeypatch):
        patch_happy(monkeypatch)
        body = client.get("/api/dashboard").json()
        ov = body["panels"]["overview"]["data"]
        assert (
            ov["counts"]["limit_up"]
            == body["panels"]["limit_up"]["data"]["count"]
            == len(body["panels"]["limit_up"]["data"]["stocks"])
        )
        assert ov["counts"]["limit_down"] == body["panels"]["limit_down"]["data"]["count"]
        assert ov["counts"]["failed"] == body["panels"]["failed"]["data"]["count"]
        zt_n, zb_n = ov["counts"]["limit_up"], ov["counts"]["failed"]
        assert ov["failed_rate"] == round(zb_n / (zt_n + zb_n) * 100, 1)
        assert isinstance(ov["failed_rate"], float)
        assert isinstance(ov["amount_total_yi"], float)
        for i in ov["indices"]:
            assert isinstance(i["change_pct"], (int, float)) and not isinstance(i["change_pct"], bool)
        assert ov["streak_dist"] == [{"streak": 3, "count": 1}, {"streak": 2, "count": 1}, {"streak": 1, "count": 1}]
        assert ov["max_streak"] == 3
        assert ov["industry_top"][0] == {"industry": "电子", "count": 2}

    def test_as_of_not_stamped_by_echoed_query_date(self, client, monkeypatch):
        """回声端到端钉：今天无独立池时 API 回显最近池+查询日，as_of 须落真实最后交易日"""
        calls = patch_happy(monkeypatch)
        last = _prev_workday(date.today())  # 仿真「最后有池日=前一工作日，今天无独立池」
        last_s = last.strftime("%Y%m%d")

        def zt_echo(trade_date=None):
            if trade_date >= last_s:  # ≥ 最后有池日：自己的池/回声同内容，date 回显查询日
                return {"date": trade_date, "count": 3, "stocks": ZT_STOCKS}
            return {"date": trade_date, "count": 0, "stocks": []}

        patch_pool(monkeypatch, "zt", zt_echo, calls["zt"])
        body = client.get("/api/dashboard").json()
        expect = f"{last.year:04d}-{last.month:02d}-{last.day:02d}"
        assert body["as_of"] == expect, f"回声池不得按查询日错标: as_of={body['as_of']} 期望={expect}"

    def test_failover_to_spot_derived_no_fabricated_streak(self, client, monkeypatch):
        """主源 error 字典 → 切快照推导；梯队出「连板数未知」，全响应无捏造 streak==1"""
        patch_happy(
            monkeypatch,
            zt={"error": "东财涨停池挂了"},
            snap=make_snap_df(
                [
                    {
                        "code": "600001",
                        "name": "甲电子",
                        "price": 11.0,
                        "change_pct": 10.0,
                        "high": 11.0,
                        "prev_close": 10.0,
                    },
                    {
                        "code": "000012",
                        "name": "庚ST",
                        "price": 10.5,
                        "change_pct": 5.0,
                        "high": 10.5,
                        "prev_close": 10.0,
                    },
                ]
            ),
        )
        body = client.get("/api/dashboard").json()
        ladder = body["panels"]["ladder"]
        assert ladder["source"] == "spot-derived", "主源失败须自动切换"
        assert ladder["error"] is None, "备源成功即清 error"
        tiers = ladder["data"]["tiers"]
        assert len(tiers) == 1 and tiers[0]["label"] == "连板数未知" and tiers[0]["streak"] is None
        assert tiers[0]["count"] == 2

        def _walk(o):
            if isinstance(o, dict):
                if "streak" in o:
                    assert o["streak"] != 1, f"捏造 streak=1: {o}"
                for v in o.values():
                    _walk(v)
            elif isinstance(o, list):
                for v in o:
                    _walk(v)

        _walk(body)

    def test_panel_error_isolation(self, client, monkeypatch):
        patch_happy(monkeypatch, sectors=RuntimeError("板块源炸了"))
        body = client.get("/api/dashboard").json()
        assert body["panels"]["sectors"]["error"] is not None
        assert body["panels"]["overview"]["data"]["counts"]["limit_up"] == 3, "板块失败不得拖垮其他面板"
        assert body["panels"]["news"]["data"]["items"]

    def test_ttl_then_refresh(self, client, monkeypatch):
        calls = patch_happy(monkeypatch)
        client.get("/api/dashboard")
        n_dt = len(calls["dt"])
        client.get("/api/dashboard")
        assert len(calls["dt"]) == n_dt, "TTL 内不得重复取数"
        client.get("/api/dashboard?refresh=1")
        assert len(calls["dt"]) == n_dt + 1, "refresh=1 须穿透 TTL"

    def test_news_single_source_survives(self, client, monkeypatch):
        patch_happy(monkeypatch, em=[{"error": "东财快讯挂了"}])
        body = client.get("/api/dashboard").json()
        news = body["panels"]["news"]
        assert [i["title"] for i in news["data"]["items"]] == ["新浪要闻一"]
        assert news["data"]["sources_used"] == ["新浪财经"]
        assert "东方财富" in (news["error"] or "")

    def test_post_not_allowed(self, client, monkeypatch):
        patch_happy(monkeypatch)
        assert client.post("/api/dashboard").status_code in (404, 405)

    def test_all_pool_sources_failed_counts_are_null_not_zero(self, client, monkeypatch):
        """未知不得捏造成 0：池事实全败时 count/failed_rate 置 null（前端 --）"""
        patch_happy(
            monkeypatch, zt={"error": "z挂"}, dt={"error": "d挂"}, zb={"error": "z挂2"}, snap=RuntimeError("快照也挂")
        )
        body = client.get("/api/dashboard").json()
        ov = body["panels"]["overview"]["data"]
        assert ov["counts"] == {"limit_up": None, "limit_down": None, "failed": None}
        assert ov["failed_rate"] is None, "一侧未知不得给伪精确炸板率"
        assert body["panels"]["limit_up"]["data"]["count"] is None
        assert body["panels"]["limit_up"]["data"]["stocks"] == []
        assert body["panels"]["limit_up"]["error"] is not None

    def test_snapshot_amount_missing_returns_none(self, monkeypatch):
        """成交额缺行不得折 0 / 不得以残缺和冒充两市合计（宁缺勿造）"""
        snap = make_snap_df(
            [
                {"code": "600001", "name": "甲", "price": 10.0, "change_pct": 1.0, "high": 10.0, "prev_close": 9.9},
                {"code": "600002", "name": "乙", "price": 10.0, "change_pct": 2.0, "high": 10.0, "prev_close": 9.8},
            ]
        )
        stats = market_stats_from_snapshot(snap)
        assert stats["amount_total_yi"] is None, "缺行不得以部分和冒充两市合计"
        assert "缺 2 只" in stats["note"], "note 须披露成交额缺几只"
        snap2 = make_snap_df(
            [
                {
                    "code": "600001",
                    "name": "甲",
                    "price": 10.0,
                    "change_pct": 1.0,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "amount": 2e8,
                },
            ]
        )
        assert market_stats_from_snapshot(snap2)["amount_total_yi"] == 2.0
        # 零成交量且未涨跌 = 全天没成交的正面事实：成交额按 0 入账，不拖垮合计（未知≠0 不禁已知 0）
        snap3 = make_snap_df(
            [
                {
                    "code": "600001",
                    "name": "甲",
                    "price": 10.0,
                    "change_pct": 1.0,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "amount": 2e8,
                },
                {
                    "code": "600002",
                    "name": "乙",
                    "price": 9.9,
                    "change_pct": 0.0,
                    "high": 9.9,
                    "prev_close": 9.9,
                    "volume": 0,
                },
            ]
        )
        stats3 = market_stats_from_snapshot(snap3)
        assert stats3["amount_total_yi"] == 2.0, "零成交行按 0 入账不拖垮合计"
        assert "缺" not in stats3["note"], "零成交不是缺额"
        # 有成交量却缺成交额 = 真未知：仍不给合计（宁缺勿造）
        snap4 = make_snap_df(
            [
                {
                    "code": "600001",
                    "name": "甲",
                    "price": 10.0,
                    "change_pct": 1.0,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "amount": 2e8,
                },
                {
                    "code": "600002",
                    "name": "乙",
                    "price": 10.0,
                    "change_pct": 1.0,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "volume": 5000,
                },
            ]
        )
        assert market_stats_from_snapshot(snap4)["amount_total_yi"] is None
        # 零成交量却带涨跌 = 数据自相矛盾：按未知，不得当 0 入账
        snap5 = make_snap_df(
            [
                {
                    "code": "600001",
                    "name": "甲",
                    "price": 10.0,
                    "change_pct": 1.0,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "amount": 2e8,
                },
                {
                    "code": "600002",
                    "name": "乙",
                    "price": 10.0,
                    "change_pct": 1.0,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "volume": 0,
                },
            ]
        )
        assert market_stats_from_snapshot(snap5)["amount_total_yi"] is None

    def test_snapshot_amount_independent_of_pct_filter(self):
        """成交额会计独立于涨跌幅过滤：缺涨跌幅行已知额不丢、未知额计入缺额；北交所不计两市"""
        # 缺涨跌幅行的已知额照常入账（breadth 仍只计涨跌幅已知行）
        snap = make_snap_df(
            [
                {
                    "code": "600001",
                    "name": "甲",
                    "price": 10.0,
                    "change_pct": 1.0,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "amount": 1e8,
                },
                {
                    "code": "600002",
                    "name": "乙",
                    "price": 10.0,
                    "change_pct": None,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "amount": 5e8,
                },
            ]
        )
        stats = market_stats_from_snapshot(snap)
        assert stats["amount_total_yi"] == 6.0, "缺涨跌幅行的已知额不得被丢"
        assert stats["breadth"]["total"] == 1, "breadth 仍只计涨跌幅已知行"
        # 缺涨跌幅 + 缺额 + 有量 = 真缺额：不得绕过宁缺勿造护栏
        snap2 = make_snap_df(
            [
                {
                    "code": "600001",
                    "name": "甲",
                    "price": 10.0,
                    "change_pct": 1.0,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "amount": 1e8,
                },
                {
                    "code": "600002",
                    "name": "乙",
                    "price": 10.0,
                    "change_pct": None,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "volume": 5000,
                },
            ]
        )
        stats2 = market_stats_from_snapshot(snap2)
        assert stats2["amount_total_yi"] is None, "缺涨跌幅行的缺额不得绕过护栏"
        assert "缺 1 只" in stats2["note"], "note 须披露缺几只"
        # 全空停牌行（无价无量无额无涨跌）= 无交易的一致证据，不算缺额
        snap3 = make_snap_df(
            [
                {
                    "code": "600001",
                    "name": "甲",
                    "price": 10.0,
                    "change_pct": 1.0,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "amount": 1e8,
                },
                {"code": "600002", "name": "乙", "price": None, "change_pct": None, "high": None, "prev_close": 9.9},
            ]
        )
        assert market_stats_from_snapshot(snap3)["amount_total_yi"] == 1.0
        # 北交所成交额不进「两市」合计
        snap4 = make_snap_df(
            [
                {
                    "code": "600001",
                    "name": "甲",
                    "price": 10.0,
                    "change_pct": 1.0,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "amount": 1e8,
                },
                {
                    "code": "430003",
                    "name": "丙北交",
                    "price": 10.0,
                    "change_pct": 1.0,
                    "high": 10.0,
                    "prev_close": 9.9,
                    "amount": 9e8,
                },
            ]
        )
        assert market_stats_from_snapshot(snap4)["amount_total_yi"] == 1.0, "北交所不冒充两市"
        # 空快照 → None（未知），不得给 0
        assert market_stats_from_snapshot(make_snap_df([]))["amount_total_yi"] is None
        # 显式零成交 → 0.0 是事实不是未知
        zeros = make_snap_df(
            [
                {
                    "code": "600001",
                    "name": "甲",
                    "price": 10.0,
                    "change_pct": 0.0,
                    "high": 10.0,
                    "prev_close": 10.0,
                    "amount": 0.0,
                },
            ]
        )
        assert market_stats_from_snapshot(zeros)["amount_total_yi"] == 0.0

    def test_new_listing_no_limit_not_derived_into_pools(self, monkeypatch):
        """新股上市初期无涨跌幅限制：N/C 前缀股不得被快照推导判进涨跌停/炸板池"""
        snap = make_snap_df(
            [
                {
                    "code": "300001",
                    "name": "N新股",
                    "price": 25.0,
                    "change_pct": 150.0,
                    "high": 25.0,
                    "prev_close": 10.0,
                },
                {
                    "code": "688001",
                    "name": "C次新",
                    "price": 12.5,
                    "change_pct": 25.0,
                    "high": 12.5,
                    "prev_close": 10.0,
                },
                {
                    "code": "600001",
                    "name": "甲电子",
                    "price": 11.0,
                    "change_pct": 10.0,
                    "high": 11.0,
                    "prev_close": 10.0,
                },
            ]
        )
        zt = zt_from_snapshot(snap)
        assert [r["code"] for r in zt["stocks"]] == ["600001"], "无限制新股不得判涨停"
        zb = zb_from_snapshot(snap)
        assert zb["stocks"] == [], "无限制新股不得判炸板（本例无其他触板回落股）"

    def test_sources_labels_visible(self, client, monkeypatch):
        patch_happy(monkeypatch)
        body = client.get("/api/dashboard").json()
        ov = body["panels"]["overview"]["data"]["sources"]
        assert ov["zt"] == "em-zt-pool"
        assert ov["stats"] == "em-spot"
        assert ov["indices"] == "sina"
        assert body["panels"]["limit_up"]["source"] == "em-zt-pool"
        assert body["panels"]["news"]["source"] == "em+sina"


# ── 指数备源（新浪 → 腾讯自动切换） ────────────────────────────────────────


class TestIndicesFallback:
    def test_sina_ok_no_tencent(self, monkeypatch):
        import web.service.indices as ix

        calls = []

        def sina():
            calls.append("sina")
            return [{"code": "sh000001", "name": "上证指数", "price": 1.0, "change_amt": 0.1, "change_pct": 1.0}], None

        def tencent():
            calls.append("tencent")
            return [{"code": "sh000001", "name": "上证指数", "price": 2.0, "change_amt": 0.2, "change_pct": 2.0}], None

        monkeypatch.setattr(ix, "fetch_indices_sina", sina)
        monkeypatch.setattr(ix, "fetch_indices_tencent", tencent)
        items, source, err = ix.fetch_indices_with_source()
        assert source == "sina" and items[0]["price"] == 1.0 and err is None
        assert calls == ["sina"]

    def test_sina_fail_tencent_used(self, monkeypatch):
        import web.service.indices as ix

        monkeypatch.setattr(ix, "fetch_indices_sina", lambda: ([], "指数行情请求失败: 模拟网络故障"))
        monkeypatch.setattr(
            ix,
            "fetch_indices_tencent",
            lambda: (
                [{"code": "sh000001", "name": "上证指数", "price": 2.0, "change_amt": 0.2, "change_pct": 2.0}],
                None,
            ),
        )
        items, source, err = ix.fetch_indices_with_source()
        assert source == "tencent" and items and err is None

    def test_both_fail_combined_error(self, monkeypatch):
        import web.service.indices as ix

        monkeypatch.setattr(ix, "fetch_indices_sina", lambda: ([], "新浪挂了"))
        monkeypatch.setattr(ix, "fetch_indices_tencent", lambda: ([], "腾讯也挂了"))
        items, source, err = ix.fetch_indices_with_source()
        assert items == [] and source is None
        assert "新浪挂了" in err and "腾讯也挂了" in err

    def test_partial_parse_is_failure_not_success(self, monkeypatch):
        """缺指数不得冒充成功（否则备源永远不被尝试）：3 缺 2 按失败处理"""
        partial = 'var hq_str_sh000001="上证指数,3300.00,3200.00,3250.50,3260.00,3190.00";\n'.encode("gbk")
        patch_sina(monkeypatch, partial)
        items, err = fetch_indices_sina()
        assert items == [] and "不完整" in (err or "")

    def test_partial_sina_fails_over_to_tencent(self, monkeypatch):
        import web.service.indices as ix

        partial = 'var hq_str_sh000001="上证指数,3300.00,3200.00,3250.50,3260.00,3190.00";\n'.encode("gbk")
        patch_sina(monkeypatch, partial)
        full = [
            {"code": c, "name": n, "price": 1.0, "change_amt": 0.1, "change_pct": 1.0}
            for c, n in (("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"))
        ]
        monkeypatch.setattr(ix, "fetch_indices_tencent", lambda: (full, None))
        items, source, err = ix.fetch_indices_with_source()
        assert source == "tencent" and len(items) == 3, "部分解析必须触发备源"

    def test_missing_fields_are_null_not_zero(self, monkeypatch):
        """空现价行剔除（不得伪 -100%）；空昨收保留行但涨跌置 null（不得伪平盘）"""
        payload = (
            'var hq_str_sh000001="上证指数,3300.00,3200.00,3250.50,3260.00,3190.00";\n'
            'var hq_str_sz399001="深证成指,10000.00,,";\n'
            'var hq_str_sz399006="创业板指,2000.00,,1950.10,1960.00,1850.00";\n'
        ).encode("gbk")
        patch_sina(monkeypatch, payload)
        items, err = fetch_indices_sina()
        assert items == [] and err, "空现价行剔除后缺码按失败"
        payload2 = (
            'var hq_str_sh000001="上证指数,3300.00,3200.00,3250.50,3260.00,3190.00";\n'
            'var hq_str_sz399001="深证成指,10000.00,,9950.25,9960.00,9850.00";\n'
            'var hq_str_sz399006="创业板指,2000.00,1900.00,1950.10,1960.00,1850.00";\n'
        ).encode("gbk")
        patch_sina(monkeypatch, payload2)
        items, err = fetch_indices_sina()
        assert err is None and len(items) == 3
        by = {i["code"]: i for i in items}
        assert by["sz399001"]["price"] == 9950.25
        assert by["sz399001"]["change_amt"] is None and by["sz399001"]["change_pct"] is None, "未知涨跌必须 null"

    def test_to_item_unknown_baseline_gives_null_change(self):
        it = _to_item("sh000001", "上证指数", 100.0, 0.0)
        assert it["change_amt"] is None and it["change_pct"] is None
        it2 = _to_item("sh000001", "上证指数", 100.0, None)
        assert it2["change_amt"] is None and it2["change_pct"] is None
