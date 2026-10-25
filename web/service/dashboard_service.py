"""主页看板数据服务 —— 事实组多源取数 + 60s TTL + 面板纯派生

看板铁律（用户约束，勿破）：
1. 仅数据整理：绝不掺任何算法推荐 / 评分 / 情绪评级 / 操作建议（构造层就不生成该类字段，
   亦不复用 get_market_sentiment——其输出含评级与建议文案）。
2. 多数据源失效自动切换：每个事实组有序源列表 + 每源熔断器 + stale-if-error。
3. 涨跌停 / 连板判定只用池 API 与全市场快照的原始涨跌幅及不复权昨收，
   绝不用前复权 daily_kline 自算（除权日失真）。

架构：事实组（PanelFetcher）→ 纯函数派生面板 → GET /api/dashboard（前端 60s 轮询）。
failover 单位是事实组而非面板：ladder / limit_up 同出 zt_pool 一次取数，
overview.counts 与池面板同源同次，杜绝分钟内自相矛盾。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime

from deeppulse import config
from deeppulse.src.resilience import CircuitBreaker

# 看板数据刷新周期（秒）。模块常量：看板是纯展示面，不进 setting.json。
DASHBOARD_TTL = 60.0
# 熔断恢复窗口比默认 60s 短——分钟级轮询下源要快速回血
SOURCE_RECOVERY_S = 30.0
SOURCE_FAIL_THRESHOLD = 2
# 涨跌停判定容差（价格取整使显示涨跌幅与名义限制有 ±0.1 个百分点舍入偏差）
LIMIT_TOLERANCE = 0.1
# 池交易日解析结果的记忆时长（交易日一天一变，避免每次刷新都探日期）
POOL_DATE_CACHE_S = 3600.0
# 回退命中往日时短记忆：盘前/数据停更解除后尽快换到今日池，不得把「昨日」锁死一小时
POOL_DATE_CACHE_WALKBACK_S = 300.0
POOL_DATE_LOOKBACK_DAYS = 10
# 强制刷新（?refresh=1）最短间隔：跨站 GET 可被恶意页面连打放大上游取数，强制刷须节流
FORCE_MIN_INTERVAL_S = 10.0

# 本地口径「两市成交额」覆盖门槛：沪深全 A 约 5300 只，截面覆盖 ≥5000 只（约 94%）
# 且零缺额才敢称两市合计（宁缺勿造，防止陈旧截面/自选股小库把局部和冒充成两市总数）
LOCAL_AMOUNT_MIN_COVERED = 5000

# ── 事实结果 ──────────────────────────────────────────────────────────────


@dataclass
class FactResult:
    """一个事实组的一次取数结果"""

    data: object  # dict/list，或 None（全源失败且无过期缓存）
    source: str | None
    error: str | None
    stale: bool = False  # True = 全源失败回吐的过期缓存
    updated_at: float = 0.0  # data 实际获取成功的时间（time.time）


def _is_error_payload(payload) -> str | None:
    """源函数失败信号 → 错误串；正常数据返回 None。

    项目惯例：池/新闻等源函数把异常吞成 {"error": …} 字典，或 [{"error": …}] 列表头，
    不抛异常。此类载荷一律视为失败以触发换源；空池/空列表是合法结果。
    """
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    if isinstance(payload, list) and payload and isinstance(payload[0], dict) and payload[0].get("error"):
        return str(payload[0]["error"])
    return None


class PanelFetcher:
    """通用事实组取数器：TTL 双检锁 + 每源一个熔断器 + 活动源优先。

    语义：
    - 失败 = 抛异常 / 返回 {"error":…} 字典（或列表头）/ 返回 None → 记熔断换下一源；
    - 空池/空列表 = 合法结果，直接入缓存（非交易日不打爆接口）；
    - 全源失败 → 回吐过期缓存并标 stale=True；无缓存 → data=None + 合并错误串。
    锁内取数保证同一事实组的并发请求单飞（锁内双检）。
    """

    def __init__(self, name: str, sources: list[tuple[str, Callable]], ttl: float = DASHBOARD_TTL):
        self.name = name
        self._ttl = ttl
        self._lock = threading.Lock()
        self._cached: FactResult | None = None
        self._active = 0
        self._sources = [
            (
                sname,
                fn,
                CircuitBreaker(failure_threshold=SOURCE_FAIL_THRESHOLD, recovery_timeout=SOURCE_RECOVERY_S),
            )
            for sname, fn in sources
        ]

    def get(self, force: bool = False) -> FactResult:
        with self._lock:
            now = time.time()
            if not force and self._cached is not None and (now - self._cached.updated_at) < self._ttl:
                return self._cached

            errors: list[str] = []
            order = sorted(range(len(self._sources)), key=lambda i: 0 if i == self._active else 1)
            for i in order:
                sname, fn, breaker = self._sources[i]
                if not breaker.can_execute():
                    errors.append(f"{sname}: 熔断中，跳过")
                    continue
                try:
                    payload = fn()
                    err = _is_error_payload(payload)
                    if err is not None:
                        raise RuntimeError(err)
                    if payload is None:
                        raise RuntimeError("返回空")
                    breaker.record_success()
                    self._active = i
                    self._cached = FactResult(
                        data=payload, source=sname, error=None, stale=False, updated_at=time.time()
                    )
                    return self._cached
                except Exception as e:  # noqa: BLE001 — 换源语义：任何失败都试下一源
                    breaker.record_failure()
                    errors.append(f"{sname}: {e}")

            merged = "；".join(errors) if errors else "全部数据源不可用"
            if self._cached is not None:
                return FactResult(
                    data=self._cached.data,
                    source=self._cached.source,
                    error=merged,
                    stale=True,
                    updated_at=self._cached.updated_at,
                )
            return FactResult(data=None, source=None, error=merged, stale=False, updated_at=0.0)


# ── 池交易日解析 ────────────────────────────────────────────────────────────

_pool_date_state: dict = {"date": None, "at": 0.0, "ttl": POOL_DATE_CACHE_S}
_pool_date_lock = threading.Lock()
_force_state: dict = {"at": 0.0}
_force_lock = threading.Lock()


def _prev_workday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _pool_signature(res: dict | None) -> tuple | None:
    """池内容指纹（代码+连板数）；空池返回 None。供回声检测逐股比对。"""
    stocks = (res or {}).get("stocks") or []
    if not stocks:
        return None
    return tuple(sorted((str(s.get("code")), s.get("streak")) for s in stocks))


def _walk_pool_date(probe_fn: Callable[[str], dict], base: date) -> tuple[str, dict | None, str | None]:
    """回退式池日期解析。返回 (date, hit_or_last_empty_result, error)。

    仅「成功但空」才回退工作日（≤POOL_DATE_LOOKBACK_DAYS）；网络失败（error 字典/抛异常）
    不回退——失败不是「无数据」，回退会把网络故障伪装成非交易日。

    回声检测（实测 2026-10-18 东财池语义）：对「无独立池且不早于最后有池日」的日期
    （周末/节假日/盘前）接口原样返回最近池、date 字段回显查询日——单看非空会把周五的池
    错标成周日；而早于最后有池日的周末/节假日返回空、不回声，故最后有池日的前一工作日
    只可能是自己的池或空。据此与前一工作日探测结果逐股比对：一致即证明该日没有自己的池，
    携带对照结果继续回退（对照已取过，不重复请求）。
    """
    d = base
    last_empty: dict | None = None
    prefetched: dict | None = None
    for _ in range(POOL_DATE_LOOKBACK_DAYS + 1):
        ds = d.strftime("%Y%m%d")
        res = prefetched
        prefetched = None
        if res is None:
            try:
                res = probe_fn(ds)
            except Exception as e:  # noqa: BLE001 — 网络失败不回退
                return ds, None, str(e)
            err = _is_error_payload(res)
            if err is not None:
                return ds, None, err
        if isinstance(res, dict) and (res.get("stocks") or res.get("count")):
            prev = _prev_workday(d)
            try:
                res_prev = probe_fn(prev.strftime("%Y%m%d"))
            except Exception:  # noqa: BLE001 — 对照失败无法证伪，采信已取结果
                return ds, res, None
            err = _is_error_payload(res_prev)
            if err is not None:
                return ds, res, None
            if _pool_signature(res) != _pool_signature(res_prev):
                return ds, res, None  # D 有独立池数据
            # 回声：D 没有自己的池，携带对照结果回退继续找最后有池日
            d = prev
            prefetched = res_prev
            continue
        last_empty = res
        d = _prev_workday(d)
    if prefetched is not None:
        # 回退窗口内探测结果始终逐股相同（回声区超窗，疑似数据停更）：宁可报错也不错标日期
        return base.strftime("%Y%m%d"), None, "池交易日无法定位（回退窗口内各日探测结果相同，数据疑似停更）"
    # 全空（长假/无涨停）：用今天，空池本身是合法结果；载荷日期归一为今天，避免轮询间 trade_date 翻转
    empty = dict(last_empty or {"count": 0, "stocks": []}, date=base.strftime("%Y%m%d"))
    return base.strftime("%Y%m%d"), empty, None


def _set_pool_date(ds: str) -> None:
    # 命中今天 → 长记忆；回退命中往日 → 短记忆（盘前/节后数据恢复后 5 分钟内换到今日池）
    ttl = POOL_DATE_CACHE_S if ds == date.today().strftime("%Y%m%d") else POOL_DATE_CACHE_WALKBACK_S
    _pool_date_state.update({"date": ds, "at": time.time(), "ttl": ttl})


def _fresh_pool_date() -> str | None:
    if _pool_date_state["date"] and (time.time() - _pool_date_state["at"]) < _pool_date_state.get(
        "ttl", POOL_DATE_CACHE_S
    ):
        return _pool_date_state["date"]
    return None


def resolve_pool_date(probe: Callable[[str], dict] | None = None, today: date | None = None) -> str:
    """解析应查询的池交易日（YYYYMMDD）。生产路径带 1 小时记忆；注入 probe 时恒重新解析（测试）。"""
    if probe is not None:
        ds, _res, _err = _walk_pool_date(probe, today or date.today())
        return ds
    with _pool_date_lock:
        cached = _fresh_pool_date()
        if cached is not None:
            return cached
        from deeppulse.agent.market import get_limit_up_pool

        ds, _res, err = _walk_pool_date(get_limit_up_pool, today or date.today())
        if err is None:  # 网络失败不写记忆（日期未知）
            _set_pool_date(ds)
        return ds


def _iso(yyyymmdd: str | None) -> str | None:
    s = str(yyyymmdd or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s or None


# ── 快照降级推导（唯一自算路径：原始涨跌幅 + 不复权昨收） ────────────────────


def _snap_col(snap, *names: str) -> str | None:
    return next((c for c in names if c in snap.columns), None)


def _iter_snap_rows(snap):
    """快照行 → 归一化 dict；列名东财中文口径为主、英文兜底"""
    code_col = _snap_col(snap, "代码", "code")
    name_col = _snap_col(snap, "名称", "name")
    price_col = _snap_col(snap, "最新价", "current")
    pct_col = _snap_col(snap, "涨跌幅", "change_pct")
    high_col = _snap_col(snap, "最高", "high")
    prev_col = _snap_col(snap, "昨收", "yesterday_close")
    turnover_col = _snap_col(snap, "换手率", "turnover")
    amount_col = _snap_col(snap, "成交额", "amount")
    vol_col = _snap_col(snap, "成交量", "volume")
    if code_col is None or pct_col is None:
        raise ValueError("全市场快照缺少代码/涨跌幅列")

    import pandas as pd

    def _f(v):
        try:
            f = float(v)
            return None if pd.isna(f) else f
        except (TypeError, ValueError):
            return None

    for _, row in snap.iterrows():
        code = str(row.get(code_col, "")).zfill(6)
        amount = _f(row.get(amount_col)) if amount_col else None
        yield {
            "code": code,
            "name": str(row.get(name_col, "")) if name_col else "",
            "price": _f(row.get(price_col)) if price_col else None,
            "change_pct": _f(row.get(pct_col)),
            "high": _f(row.get(high_col)) if high_col else None,
            "prev_close": _f(row.get(prev_col)) if prev_col else None,
            "turnover": _f(row.get(turnover_col)) if turnover_col else None,
            # 成交量留作「零成交」正面事实的判定依据（volume=0 且未涨跌 ⇒ 成交额定为 0 而非未知）
            "volume": _f(row.get(vol_col)) if vol_col else None,
            # 缺失/NaN 不得折成 0（宁缺勿造）：None 表示未知
            "amount": amount / 1e8 if amount is not None else None,  # 元 → 亿
        }


def _is_two_market(code: str) -> bool:
    """沪深两市 A 股代码（60/68 沪、00/30 深）：「两市成交额」口径只计两市，北交所/B 股不冒充"""
    return str(code)[:2] in ("60", "68", "00", "30")


def _limit_pct(code: str, name: str) -> float | None:
    from deeppulse.src.codes import price_limit_pct

    n = (name or "").strip().upper()
    # 新股上市初期不设涨跌幅限制（首日名带 N 前缀、随后 4 日带 C 前缀，交易所命名惯例）：
    # 无限制股不参与涨跌停判定，price_limit_pct 对此类股不适用
    if n.startswith("N") or n.startswith("C"):
        return None
    try:
        return price_limit_pct(code, name or None)
    except ValueError:
        return None  # 非 A 股代码不参与涨跌停判定


def _pool_row(r: dict, *, zt: bool) -> dict:
    """快照行 → 池行。字段形状与东财池函数对齐：zt 11 字段、dt/zb 仅 4 字段。
    streak 显式 None（降级路径绝不默认 1——不得捏造连板数）。"""
    row = {
        "code": r["code"],
        "name": r["name"],
        "price": round(r["price"], 2) if r["price"] is not None else None,
        "change_pct": round(r["change_pct"], 2) if r["change_pct"] is not None else None,
    }
    if zt:
        row.update(
            {
                "turnover": round(r["turnover"], 2) if r.get("turnover") is not None else None,
                "amount": round(r["amount"], 2) if r.get("amount") is not None else None,
                "first_time": None,
                "last_time": None,
                "open_count": None,
                "streak": None,
                "industry": None,
            }
        )
    return row


def _derived_pool_date() -> str:
    """快照推导路径的交易日标注：池日期记忆优先；无记忆时周末回退上一工作日。
    节假日无法离线判定（无交易日历），只能尽力——快照本身是最新截面，日期仅是标注。"""
    cached = _fresh_pool_date()
    if cached:
        return cached
    today = date.today()
    d = _prev_workday(today) if today.weekday() >= 5 else today
    return d.strftime("%Y%m%d")


def _round_half_up(x: float, ndigits: int = 2) -> float:
    """四舍五入（交易所涨停价口径）。Python round 是银行家舍入，边界会差 0.01。"""
    from decimal import ROUND_HALF_UP, Decimal

    return float(Decimal(str(x)).quantize(Decimal("1." + "0" * ndigits), rounding=ROUND_HALF_UP))


def zt_from_snapshot(snap) -> dict:
    """快照推导涨停池：涨跌幅 ≥ 板块限制 - 容差"""
    stocks = []
    for r in _iter_snap_rows(snap):
        limit = _limit_pct(r["code"], r["name"])
        if limit is None or r["change_pct"] is None:
            continue
        if r["change_pct"] >= limit - LIMIT_TOLERANCE:
            stocks.append(_pool_row(r, zt=True))
    return {"date": _derived_pool_date(), "count": len(stocks), "stocks": stocks, "derived": "spot"}


def dt_from_snapshot(snap) -> dict:
    """快照推导跌停池：涨跌幅 ≤ -(板块限制 - 容差)"""
    stocks = []
    for r in _iter_snap_rows(snap):
        limit = _limit_pct(r["code"], r["name"])
        if limit is None or r["change_pct"] is None:
            continue
        if r["change_pct"] <= -(limit - LIMIT_TOLERANCE):
            stocks.append(_pool_row(r, zt=False))
    return {"date": _derived_pool_date(), "count": len(stocks), "stocks": stocks, "derived": "spot"}


def zb_from_snapshot(snap) -> dict:
    """快照推导炸板池：盘中最高触及涨停价（不复权昨收×(1+限制)四舍五入 2 位）但未封住"""
    stocks = []
    for r in _iter_snap_rows(snap):
        limit = _limit_pct(r["code"], r["name"])
        if limit is None or r["change_pct"] is None:
            continue
        limit_price = None
        if r["prev_close"] and r["prev_close"] > 0:
            limit_price = _round_half_up(r["prev_close"] * (1 + limit / 100), 2)
        touched = limit_price is not None and r["high"] is not None and r["high"] >= limit_price - 0.01
        sealed = r["change_pct"] >= limit - LIMIT_TOLERANCE
        if touched and not sealed:
            stocks.append(_pool_row(r, zt=False))
    return {"date": _derived_pool_date(), "count": len(stocks), "stocks": stocks, "derived": "spot"}


def _bucketize(pcts: list[float], limits: list[float | None]) -> tuple[dict, dict]:
    """涨跌家数 + 涨跌幅分布桶（口径对齐 tools.market._market_overview_online，
    额外补上「涨>9% 未触板 / 跌>9% 未触板」两尾桶——20cm/30cm 板存在此区间）"""
    up = down = flat = 0
    buckets = {
        "limit_up": 0,
        "up_gt9": 0,
        "up_7_9": 0,
        "up_3_7": 0,
        "up_0_3": 0,
        "flat": 0,
        "down_0_3": 0,
        "down_3_7": 0,
        "down_7_9": 0,
        "down_gt9": 0,
        "limit_down": 0,
    }
    for pct, limit in zip(pcts, limits, strict=True):
        is_up_limit = limit is not None and pct >= limit - LIMIT_TOLERANCE
        is_down_limit = limit is not None and pct <= -(limit - LIMIT_TOLERANCE)
        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        else:
            flat += 1
        if is_up_limit:
            buckets["limit_up"] += 1
        elif is_down_limit:
            buckets["limit_down"] += 1
        elif pct > 9:
            buckets["up_gt9"] += 1
        elif pct > 7:
            buckets["up_7_9"] += 1
        elif pct > 3:
            buckets["up_3_7"] += 1
        elif pct > 0:
            buckets["up_0_3"] += 1
        elif pct == 0:
            buckets["flat"] += 1
        elif pct >= -3:
            buckets["down_0_3"] += 1
        elif pct >= -7:
            buckets["down_3_7"] += 1
        elif pct >= -9:
            buckets["down_7_9"] += 1
        else:
            buckets["down_gt9"] += 1
    breadth = {"total": len(pcts), "up": up, "down": down, "flat": flat}
    return breadth, buckets


def market_stats_from_snapshot(snap) -> dict:
    """快照口径市场概况：涨跌家数 + 分布桶 + 两市成交额（亿元）"""
    pcts: list[float] = []
    limits: list[float | None] = []
    amount_total = 0.0
    amount_known = 0  # 两市行里成交额已知的行数
    amount_missing = 0  # 两市行里有成交迹象却缺成交额的行数
    for r in _iter_snap_rows(snap):
        # 成交额会计独立于涨跌幅过滤在其之前进行：缺涨跌幅行的已知额不得被丢、未知额不得绕过护栏
        two = _is_two_market(r["code"])
        amount = r.get("amount")
        if amount is not None:
            if two:
                amount_total += amount
                amount_known += 1
        elif two and not (r.get("volume") in (0, None) and r["change_pct"] in (0, None)):
            # 有成交迹象（有量或有涨跌）却缺额 = 真未知 → 缺额行，宁缺勿造护栏
            # 反之显式零成交 / 全空停牌行（东财 "-" 编码）= 无交易的一致证据，按 0 贡献、不算缺额
            # （未知≠0 禁的是把「不知道」写成 0，不禁止有据的 0；零量却涨跌 = 数据自相矛盾按未知）
            amount_missing += 1
        if r["change_pct"] is None:
            continue  # 涨跌幅只决定 breadth/涨跌停桶，不参与成交额会计
        pcts.append(r["change_pct"])
        limits.append(_limit_pct(r["code"], r["name"]))
    breadth, buckets = _bucketize(pcts, limits)
    note = "全市场快照口径（成交额仅计沪深两市），涨跌停按板块涨跌幅限制判定"
    if amount_missing:
        note += f"；成交额缺 {amount_missing} 只不给两市合计（宁缺勿造）"
    return {
        "breadth": breadth,
        "buckets": buckets,
        # 有真缺额时不以残缺和冒充「两市」合计（宁缺勿造）；显式 0 成交额是事实不是未知，照给 0
        "amount_total_yi": round(amount_total, 2) if amount_known > 0 and amount_missing == 0 else None,
        "note": note,
    }


def market_stats_from_local(db_path=None) -> dict:
    """本地 DuckDB 兜底口径：最近交易日 close vs 前收。

    前复权序列收益率 ≠ 交易所不复权涨跌幅（除权日失真）——涨跌停判定桶不适用（置 null），
    且缺 stock_info / 单根 K 线的股票被剔除。

    两市成交额：daily_kline.amount 是名义成交额（元），与复权价无关，跨除权日可加总；
    仅计沪深 A 股（60/68/00/30 开头），北交所/B 股不冒充「两市」。宁缺勿造两条闸：
    任一沪深行缺额（NaN/NULL）即整额不给（与快照口径同纪律），零缺额时还须覆盖
    ≥ LOCAL_AMOUNT_MIN_COVERED 只才给——绝不拿局部和冒充两市合计，note 如实披露。
    """
    from deeppulse.src.database import get_connection

    order = list(getattr(config, "DATA_SOURCES", []) or [])
    case = " ".join(f"WHEN '{name}' THEN {i}" for i, name in enumerate(order))
    priority = f"CASE COALESCE(data_source, '') {case} ELSE {len(order)} END"
    sql = f"""
        WITH dedup AS (
            SELECT code, trade_date, close, amount,
                   ROW_NUMBER() OVER (
                       PARTITION BY code, trade_date
                       ORDER BY {priority}
                   ) AS rn
            FROM daily_kline
        ),
        series AS (
            SELECT code, trade_date, close, amount,
                   LAG(close) OVER (PARTITION BY code ORDER BY trade_date) AS prev_close
            FROM dedup WHERE rn = 1
        )
        SELECT s.code, i.name, s.close, s.prev_close, s.trade_date, s.amount
        FROM series s
        JOIN stock_info i ON s.code = i.code
        WHERE s.trade_date = (SELECT MAX(trade_date) FROM series)
          AND s.prev_close IS NOT NULL AND s.prev_close > 0 AND s.close IS NOT NULL
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    if not rows:
        raise ValueError("本地库无可用截面数据")
    pcts: list[float] = []
    section_date = str(rows[0][4]) if rows[0][4] is not None else None
    amount_total = 0.0
    amount_covered = 0
    amount_missing = 0
    for code, _name, close, prev_close, _d, amount in rows:
        pcts.append((close - prev_close) / prev_close * 100)
        if not _is_two_market(code):
            continue  # 北交所/B 股不冒充「两市」口径
        # NaN 额按未知（部分源缺额落 NaN 而非 NULL）：与 NULL 同计入缺额，不得折 0 也不得静默丢弃
        if amount is not None and amount == amount:
            amount_total += float(amount) / 1e8  # 元 → 亿（名义额，与复权无关）
            amount_covered += 1
        else:
            amount_missing += 1
    # 复权口径判涨跌停失真（复权铁律）：涨跌停桶不判定，置 null 表示未知而非 0
    breadth, buckets = _bucketize(pcts, [None] * len(pcts))
    buckets["limit_up"] = None
    buckets["limit_down"] = None
    # 缺额行宁缺勿造拒给（与快照口径同纪律）：不以残缺和冒充两市合计
    amount_ok = amount_missing == 0 and amount_covered >= LOCAL_AMOUNT_MIN_COVERED
    note = (
        f"本地前复权收盘口径（截面 {section_date or '未知'}，除权日失真），涨跌停桶不适用；"
        f"覆盖 {len(pcts)} 只（缺 stock_info/单根K线已剔除）"
    )
    if amount_missing:
        note += f"；沪深截面成交额缺 {amount_missing} 只，不给两市合计（宁缺勿造）"
    elif amount_ok:
        note += f"；两市成交额=沪深截面 {amount_covered} 只名义额求和"
    else:
        note += f"；沪深截面仅 {amount_covered} 只含成交额，不足 {LOCAL_AMOUNT_MIN_COVERED} 只不给两市合计（宁缺勿造）"
    return {
        "breadth": breadth,
        "buckets": buckets,
        "amount_total_yi": round(amount_total, 2) if amount_ok else None,
        "note": note,
    }


# ── 事实源（零参可调用，供 PanelFetcher 使用） ─────────────────────────────


def _zt_pool_em() -> dict:
    """东财涨停池：日期探测与取数一体（命中即用，不打第二次接口），写入日期记忆供 dt/zb 复用"""
    from deeppulse.agent.market import get_limit_up_pool

    with _pool_date_lock:
        cached = _fresh_pool_date()
        if cached is None:
            ds, res, err = _walk_pool_date(get_limit_up_pool, date.today())
            if res is not None:
                _set_pool_date(ds)
                return res
            if err is not None:
                return {"error": err}
            _set_pool_date(ds)
            return {"error": "涨停池无数据（不应发生）"}
    return get_limit_up_pool(cached)


def _dt_pool_em() -> dict:
    from deeppulse.agent.market import get_limit_down_pool

    return get_limit_down_pool(resolve_pool_date())


def _zb_pool_em() -> dict:
    from deeppulse.agent.market import get_failed_limit_up

    return get_failed_limit_up(resolve_pool_date())


def _snapshot_fact() -> dict:
    from deeppulse.src.online import fetch_market_snapshot

    snap = fetch_market_snapshot()  # 失败抛 OnlineFetchError
    return {"snapshot": snap}


def _zt_from_snapshot_fact() -> dict:
    from deeppulse.src.online import fetch_market_snapshot

    return zt_from_snapshot(fetch_market_snapshot())


def _dt_from_snapshot_fact() -> dict:
    from deeppulse.src.online import fetch_market_snapshot

    return dt_from_snapshot(fetch_market_snapshot())


def _zb_from_snapshot_fact() -> dict:
    from deeppulse.src.online import fetch_market_snapshot

    return zb_from_snapshot(fetch_market_snapshot())


def _stats_from_snapshot_fact() -> dict:
    from deeppulse.src.online import fetch_market_snapshot

    return market_stats_from_snapshot(fetch_market_snapshot())


def _stats_from_local_fact() -> dict:
    return market_stats_from_local()


def _sectors_fact() -> dict:
    from deeppulse.agent.market import get_sector_ranking

    return get_sector_ranking("industry", 10)


def _news_em_fact() -> list:
    from deeppulse.agent.news import search_eastmoney_news

    return search_eastmoney_news(num=20)


def _news_sina_fact() -> list:
    from deeppulse.agent.news import search_sina_finance

    return search_sina_finance(num=20)


def _indices_fact() -> dict:
    from web.service.indices import fetch_indices_with_source

    items, source, err = fetch_indices_with_source()
    if not items:
        return {"error": err or "指数行情为空"}
    return {"items": items, "source": source}


# ── 纯派生（无 I/O，单测直调） ─────────────────────────────────────────────


def build_ladder(zt_data: dict | None) -> dict:
    """连板梯队：按 streak 分层（高→低），未知连板数单列「连板数未知」层，绝不默认 1"""
    data = zt_data or {}
    stocks = data.get("stocks") or []
    tiers_map: dict[int, list] = {}
    unknown: list = []
    for s in stocks:
        if not isinstance(s, dict):
            continue  # 载荷形状意外不得炸掉整个端点
        st = s.get("streak")
        if isinstance(st, int) and not isinstance(st, bool) and st >= 1:
            tiers_map.setdefault(st, []).append(s)
        else:
            unknown.append(s)
    tiers = [
        {
            "streak": st,
            "label": "首板" if st == 1 else f"{st}板",
            "count": len(items),
            "stocks": items,
        }
        for st, items in sorted(tiers_map.items(), reverse=True)
    ]
    if unknown:
        tiers.append({"streak": None, "label": "连板数未知", "count": len(unknown), "stocks": unknown})
    max_streak = max(tiers_map) if tiers_map else None
    return {
        "trade_date": _iso(data.get("date")),
        "max_streak": max_streak,
        "tiers": tiers,
    }


def build_pool_panel(fact: FactResult) -> dict:
    """池面板：count 恒等于 len(stocks)（与 overview.counts 一致性钉子）。

    取数失败（data=None）时 count/trade_date 置 null——「不知道」不得捏造成 0/空池。
    """
    if isinstance(fact.data, dict):
        stocks = fact.data.get("stocks") or []
        out = {"trade_date": _iso(fact.data.get("date")), "count": len(stocks), "stocks": stocks}
    else:
        out = {"trade_date": None, "count": None, "stocks": []}
    return {"data": out, "error": fact.error, "source": fact.source, "stale": fact.stale}


def build_news_panel(em_items: list | None, sina_items: list | None) -> dict:
    """资讯面板：双源合并，标题前 20 字去重，时间倒序。第三方标题/摘要原样保留（不加工不评级）。

    sources_used = 在线成功的源（含空结果；条目全被去重也算用过）；None=该源失败不计。
    """
    items: list[dict] = []
    seen: set[str] = set()
    sources_used: list[str] = []
    for got, label in ((em_items, "东方财富"), (sina_items, "新浪财经")):
        if got is None:
            continue
        if label not in sources_used:
            sources_used.append(label)
        for it in got:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "")
            key = title[:20]
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "title": title,
                    "source": it.get("source") or label,
                    "time": it.get("time") or "",
                    "summary": it.get("summary") or "",
                    "url": it.get("url") or "",
                }
            )
    items.sort(key=lambda x: str(x.get("time") or ""), reverse=True)
    return {"items": items, "sources_used": sources_used}


def build_overview(
    zt: FactResult,
    dt: FactResult,
    zb: FactResult,
    stats: FactResult,
    indices: FactResult,
) -> dict:
    """市场概况（composite）：counts 与池面板同一次取数；streak_dist/max_streak 只统计已知连板数。

    池事实全败（data=None）时对应计数/炸板率置 null——未知不得捏造成 0（前端对 null 显示 --）。
    """
    zt_known = isinstance(zt.data, dict)
    dt_known = isinstance(dt.data, dict)
    zb_known = isinstance(zb.data, dict)
    zt_data = zt.data if zt_known else {}
    dt_data = dt.data if dt_known else {}
    zb_data = zb.data if zb_known else {}
    stats_data = stats.data if isinstance(stats.data, dict) else {}
    indices_data = indices.data if isinstance(indices.data, dict) else {}
    zt_stocks = [s for s in (zt_data.get("stocks") or []) if isinstance(s, dict)]
    dt_stocks = dt_data.get("stocks") or []
    zb_stocks = zb_data.get("stocks") or []

    dist: dict[int, int] = {}
    for s in zt_stocks:
        st = s.get("streak")
        if isinstance(st, int) and not isinstance(st, bool) and st >= 1:
            dist[st] = dist.get(st, 0) + 1
    streak_dist = [{"streak": k, "count": v} for k, v in sorted(dist.items(), reverse=True)]
    max_streak = max(dist) if dist else None

    zt_n = len(zt_stocks) if zt_known else None
    zb_n = len(zb_stocks) if zb_known else None
    dt_n = len(dt_stocks) if dt_known else None
    if zt_n is not None and zb_n is not None:
        failed_rate = round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) > 0 else 0.0
    else:
        failed_rate = None  # 一侧未知不得给出伪精确比率

    industry_count: dict[str, int] = {}
    for s in zt_stocks:
        ind = str(s.get("industry") or "").strip()
        if ind:
            industry_count[ind] = industry_count.get(ind, 0) + 1
    industry_top = [
        {"industry": k, "count": v} for k, v in sorted(industry_count.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    ]

    sources = {
        "stats": stats.source,
        "zt": zt.source,
        "dt": dt.source,
        "zb": zb.source,
        "indices": indices_data.get("source"),
    }
    errors = [
        f"{name}: {fact.error}"
        for name, fact in (("概况", stats), ("涨停池", zt), ("跌停池", dt), ("炸板池", zb), ("指数", indices))
        if fact.error
    ]
    return {
        "data": {
            "indices": indices_data.get("items") or [],
            "breadth": stats_data.get("breadth"),
            "buckets": stats_data.get("buckets"),
            "counts": {"limit_up": zt_n, "limit_down": dt_n, "failed": zb_n},
            "failed_rate": failed_rate,
            "streak_dist": streak_dist,
            "max_streak": max_streak,
            "amount_total_yi": stats_data.get("amount_total_yi"),
            "industry_top": industry_top,
            "note": stats_data.get("note"),
            "sources": sources,
        },
        "error": "；".join(errors) if errors else None,
        "source": "composite",
        "stale": any(f.stale for f in (zt, dt, zb, stats, indices)),
    }


# ── 事实组注册表与聚合入口 ─────────────────────────────────────────────────


def _make_fetchers() -> dict[str, PanelFetcher]:
    return {
        "zt_pool": PanelFetcher("zt_pool", [("em-zt-pool", _zt_pool_em), ("spot-derived", _zt_from_snapshot_fact)]),
        "dt_pool": PanelFetcher("dt_pool", [("em-dt-pool", _dt_pool_em), ("spot-derived", _dt_from_snapshot_fact)]),
        "zb_pool": PanelFetcher("zb_pool", [("em-zb-pool", _zb_pool_em), ("spot-derived", _zb_from_snapshot_fact)]),
        "stats": PanelFetcher(
            "stats", [("em-spot", _stats_from_snapshot_fact), ("local-duckdb", _stats_from_local_fact)]
        ),
        "sectors": PanelFetcher("sectors", [("sector-rank", _sectors_fact)]),
        "indices": PanelFetcher("indices", [("sina-tencent", _indices_fact)]),
        "news_em": PanelFetcher("news_em", [("em-news", _news_em_fact)]),
        "news_sina": PanelFetcher("news_sina", [("sina-news", _news_sina_fact)]),
    }


_fetchers: dict[str, PanelFetcher] | None = None
_fetchers_lock = threading.Lock()


def _get_fetchers() -> dict[str, PanelFetcher]:
    global _fetchers
    with _fetchers_lock:
        if _fetchers is None:
            _fetchers = _make_fetchers()
        return _fetchers


def clear_dashboard_cache() -> None:
    """清空事实组缓存/熔断/池日期记忆/强制刷新节流（测试与手动重置用）"""
    global _fetchers
    with _fetchers_lock:
        _fetchers = None
    _pool_date_state.update({"date": None, "at": 0.0, "ttl": POOL_DATE_CACHE_S})
    with _force_lock:
        _force_state["at"] = 0.0


def _is_trading_session(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    t = now.time()
    return now.weekday() < 5 and dtime(9, 15) <= t <= dtime(15, 0)


def fetch_dashboard(force: bool = False) -> dict:
    """聚合全部看板面板。同步阻塞（含网络 I/O），调用方须包 asyncio.to_thread。

    force=True 跳过 TTL（手动刷新按钮），但强制刷新最短间隔 FORCE_MIN_INTERVAL_S——
    跨站 GET 可被恶意页面连打放大上游取数（东财限频事故教训）。面板级错误内嵌，整体恒可序列化。
    """
    if force:
        with _force_lock:
            now = time.time()
            if now - _force_state["at"] < FORCE_MIN_INTERVAL_S:
                force = False  # 节流内的强制刷按普通请求走（TTL 内仍命中缓存）
            else:
                _force_state["at"] = now
    fetchers = _get_fetchers()
    keys = list(fetchers)
    results: dict[str, FactResult] = {}
    with ThreadPoolExecutor(max_workers=len(keys)) as pool:
        futures = {k: pool.submit(fetchers[k].get, force) for k in keys}
        for k, fut in futures.items():
            try:
                results[k] = fut.result()
            except Exception as e:  # noqa: BLE001 — 契约：整体恒可序列化，单事实组异常内嵌
                results[k] = FactResult(data=None, source=None, error=str(e), stale=False, updated_at=0.0)

    zt, dt, zb = results["zt_pool"], results["dt_pool"], results["zb_pool"]
    stats, sectors, indices = results["stats"], results["sectors"], results["indices"]
    news = build_news_panel(
        results["news_em"].data if isinstance(results["news_em"].data, list) else None,
        results["news_sina"].data if isinstance(results["news_sina"].data, list) else None,
    )

    zt_panel = build_pool_panel(zt)
    panels = {
        "overview": build_overview(zt, dt, zb, stats, indices),
        "ladder": {
            "data": build_ladder(zt.data if isinstance(zt.data, dict) else None),
            "error": zt.error,
            "source": zt.source,
            "stale": zt.stale,
        },
        "limit_up": zt_panel,
        "failed": build_pool_panel(zb),
        "limit_down": build_pool_panel(dt),
        "sectors": _build_sectors_panel(sectors),
        "news": {
            "data": news,
            "error": "；".join(
                f"{name}: {fact.error}"
                for name, fact in (("东方财富", results["news_em"]), ("新浪财经", results["news_sina"]))
                if fact.error
            )
            or None,
            "source": "+".join(
                [lbl for lbl, fact in (("em", results["news_em"]), ("sina", results["news_sina"])) if fact.data]
            )
            or None,
            "stale": results["news_em"].stale or results["news_sina"].stale,
        },
    }

    trade_date = zt_panel["data"]["trade_date"]
    if not trade_date:
        for p in (panels["limit_down"], panels["failed"]):
            if p["data"]["trade_date"]:
                trade_date = p["data"]["trade_date"]
                break
    updated_candidates = [f.updated_at for f in results.values() if f.updated_at]
    return {
        "updated_at": datetime.fromtimestamp(max(updated_candidates)).isoformat(timespec="seconds")
        if updated_candidates
        else None,
        "as_of": trade_date,
        "session": "trading" if _is_trading_session() else "closed",
        "panels": panels,
    }


def _build_sectors_panel(fact: FactResult) -> dict:
    """板块排行面板：保留事实字段，字段缺失置 null（不给默认值）"""
    data = fact.data if isinstance(fact.data, dict) else {}
    sectors_in = data.get("sectors") or []
    sectors_out = []
    for s in sectors_in:
        if not isinstance(s, dict):
            continue
        sectors_out.append(
            {
                "name": s.get("name"),
                "change_pct": s.get("change_pct"),
                "inflow": s.get("inflow"),
                "up_count": s.get("up_count"),
                "down_count": s.get("down_count"),
                "leader": s.get("leader"),
                "leader_change": s.get("leader_change"),
                "amount": s.get("amount"),
            }
        )
    return {
        "data": {
            "board_type": data.get("type"),
            "source_label": data.get("source"),
            "note": data.get("note"),
            "sectors": sectors_out,
        },
        "error": fact.error,
        "source": fact.source,
        "stale": fact.stale,
    }
