"""策略计算引擎（Agent 后端）- 只出数、不说话

定位：不面向用户，只服务 Agent 的操作建议。把"操作逻辑"放到历史数据上
验证（胜率/期望值/回撤/蒙特卡洛），做参数校准、市场状态刻画与保守仓位计算，
让 Agent 的建议有量化托底而不是指标目测。

降级原则（非常保守）：
- 数据不足时拒绝输出统计数字（status="degraded"），给出定性引导，禁止编造；
- 数据充足时也默认走安全路线：保守退出预设（conservative）+ 保守仓位上限；
- 所有统计输出自带 in-sample 声明与样本量警示（sample_warning）。
"""

import math

from deeppulse.src.query import StockQuery
from deeppulse.strategy.backtest import (
    add_indicators,
    backtest_stock,
    backtest_strategy_multi_stock,
    monte_carlo_simulation,
)
from deeppulse.strategy.exit_strategies import PRESET_EXIT_STRATEGIES
from deeppulse.strategy.models import BacktestResult
from deeppulse.strategy.optimizer import grid_search_params
from deeppulse.strategy.spec import LOGIC_JSON_HINT, StrategySpec, normalize_logic_dict

# ── 数据充分性阈值（保守） ─────────────────────────────
MIN_BARS_FOR_BACKTEST = 120  # 低于此根数不输出任何回测统计
MIN_BARS_FOR_CALIBRATE = 250  # 参数校准需要更长历史
MIN_BARS_FOR_REGIME = 60  # 单股进入 regime 采样的最低根数
MIN_TRADES_FOR_STATS = 10  # 低于此笔数打 sample_warning
MIN_TRADES_FOR_KELLY = 20  # 低于此笔数不给 Kelly，只给档位
MIN_REGIME_SAMPLES = 5  # 有效采样股数低于此判 degraded

# ── 保守仓位约束 ───────────────────────────────────────
DEFAULT_RISK_PCT = 0.02  # 单笔风险 2%
RISK_PCT_MAX = 0.05  # 单笔风险上限 5%
DEFAULT_KELLY_FRACTION = 0.5  # 半 Kelly
KELLY_POSITION_CAP = 0.25  # 单股仓位上限 25%
LOT_SIZE = 100  # A股 1 手 = 100 股


# ═══════════════════ 操作逻辑历史验证 ═══════════════════


def _degraded(code: str, reason: str, bars: int, guidance: str, min_bars: int = MIN_BARS_FOR_BACKTEST) -> dict:
    """降级响应：不输出统计数字，只给定性引导"""
    return {
        "status": "degraded",
        "code": code,
        "bars_available": bars,
        "min_bars_required": min_bars,
        "reason": reason,
        "guidance": guidance,
    }


def evaluate_trade_logic(
    code: str,
    logic: dict,
    days: int = 500,
    exit_preset: str = "conservative",
    sample_codes: str | list | None = None,
    n_simulations: int = 500,
) -> dict:
    """把操作逻辑（StrategySpec 形状）放到历史数据上验证。

    返回胜率/期望值/盈亏比/回撤/蒙特卡洛分位；数据不足时降级（不编数字）。
    """
    code = str(code or "").strip()
    if not code:
        return {"error": "code 不能为空"}

    # 1) 逻辑校验：先容错归一化（direction/weight 等常见格式错误自动修复），再一次性返回全部错误
    spec_d, auto_fixed, hard_errors = normalize_logic_dict(logic)
    if hard_errors:
        return {"error": "策略规格非法", "validation_errors": hard_errors, "auto_fixed": auto_fixed, "hint": LOGIC_JSON_HINT}
    try:
        spec = StrategySpec.from_dict(spec_d)
    except Exception as e:
        return {"error": f"logic_json 解析失败: {e}", "hint": LOGIC_JSON_HINT}
    errors = spec.validate()
    if errors:
        out = {"error": "策略规格非法", "validation_errors": errors}
        if auto_fixed:
            out["auto_fixed"] = auto_fixed
        return out

    # 2) 退出策略：默认保守（安全优先）
    if exit_preset not in PRESET_EXIT_STRATEGIES:
        return {
            "error": f"未知退出预设: {exit_preset}",
            "available": list(PRESET_EXIT_STRATEGIES.keys()),
        }

    # 3) 数据充分性检查（非常保守：拿不准就降级）
    try:
        df = StockQuery().get_daily_kline(code, limit=days)
    except Exception as e:
        return _degraded(code, f"历史数据获取失败: {e}", 0, "无法回测验证，请仅做定性分析并明确声明数据不足")
    bars = int(len(df))
    if bars == 0:
        return _degraded(code, "无历史K线数据", 0, "无法回测验证；可先 /fetch update 采集数据，或仅做定性分析")
    if bars < MIN_BARS_FOR_BACKTEST:
        return _degraded(
            code,
            f"历史K线不足（{bars} < {MIN_BARS_FOR_BACKTEST} 根）",
            bars,
            "无法回测验证，请勿引用胜率/期望值等统计数字，仅做定性分析并声明样本不足",
        )

    # 4) 回测（in-sample）
    try:
        signal_func = _compile(spec)
        result = backtest_stock(
            code,
            signal_func=signal_func,
            days=days,
            use_dynamic_exit=True,
            exit_strategy_preset=exit_preset,
        )
    except Exception as e:
        return {"error": f"回测失败: {e}", "code": code, "bars_available": bars}

    out = _metrics_payload(result, bars=bars)
    out["code"] = code
    out["exit_preset"] = exit_preset
    out["spec_name"] = spec.name

    # 5) 蒙特卡洛（样本充足才跑）
    if result.total_trades >= MIN_TRADES_FOR_STATS:
        mc = monte_carlo_simulation(result.total_return, result.trades, n_simulations=int(n_simulations))
        out["monte_carlo"] = mc if "error" not in mc else {"skipped": mc.get("error")}
    else:
        out["monte_carlo"] = {"skipped": f"样本不足（{result.total_trades} < {MIN_TRADES_FOR_STATS} 笔）"}

    # 6) 多股样本验证（可选）
    code_list = _split_codes(sample_codes)
    if code_list:
        try:
            multi = backtest_strategy_multi_stock(
                strategy=spec.name,
                codes=code_list,
                days=days,
                use_dynamic_exit=True,
                exit_strategy_preset=exit_preset,
                signal_func=signal_func,
            )
            if "error" not in multi:
                out["multi_stock"] = {
                    "tested_stocks": multi.get("tested_stocks"),
                    "stocks_with_trades": multi.get("stocks_with_trades"),
                    "avg_win_rate": multi.get("avg_win_rate"),
                    "avg_return": multi.get("avg_return"),
                    "positive_rate": multi.get("positive_rate"),
                    "top_stocks": multi.get("top_stocks", [])[:5],
                }
        except Exception as e:
            out["multi_stock"] = {"skipped": f"多股验证失败: {e}"}

    if auto_fixed:
        out["auto_fixed"] = auto_fixed  # 透明披露自动修正项，模型无需重试
    return out


def _compile(spec: StrategySpec):
    from deeppulse.strategy.spec import compile_signal_func

    return compile_signal_func(spec)


def _split_codes(sample_codes) -> list[str]:
    if not sample_codes:
        return []
    if isinstance(sample_codes, str):
        return [c.strip() for c in sample_codes.replace("，", ",").split(",") if c.strip()]
    return [str(c).strip() for c in sample_codes if str(c).strip()]


def _metrics_payload(result: BacktestResult, bars: int) -> dict:
    """组装统计输出 + 诚实性标注（in-sample / 样本警示）"""
    n = result.total_trades
    sample_warning = n < MIN_TRADES_FOR_STATS
    if bars >= MIN_BARS_FOR_CALIBRATE and not sample_warning:
        confidence = "中"
    else:
        confidence = "低"

    notes = ["回测为 in-sample（同一窗口内拟合），无 walk-forward 样外验证"]
    if sample_warning:
        notes.append(f"交易样本不足（{n} 笔 < {MIN_TRADES_FOR_STATS}），胜率/期望值统计意义有限，禁止引用精确数字")
    if bars < MIN_BARS_FOR_CALIBRATE:
        notes.append(f"历史窗口偏短（{bars} 根 < {MIN_BARS_FOR_CALIBRATE} 根），结论置信度低")

    trades_sample = [
        {
            "entry_date": t.get("entry_date"),
            "exit_date": t.get("exit_date"),
            "pnl_pct": round(float(t.get("pnl_pct", 0)), 4),
            "exit_reason": t.get("exit_reason"),
        }
        for t in (result.trades or [])[-3:]
    ]

    return {
        "status": "ok",
        "in_sample": True,
        "confidence": confidence,
        "bars_used": bars,
        "sample_warning": sample_warning,
        "metrics": {
            "win_rate": round(result.win_rate, 4),
            "expectancy": round(result.expectancy, 4),
            "profit_factor": round(result.profit_factor, 2),
            "max_drawdown": round(result.max_drawdown, 4),
            "total_return": round(result.total_return, 4),
            "total_trades": n,
            "avg_hold_days": round(result.avg_hold_days, 1),
            "sharpe_ratio": round(result.sharpe_ratio, 2),
        },
        "trades_sample": trades_sample,
        "exit_statistics": result.exit_statistics,
        "notes": notes,
    }


# ═══════════════════ 参数校准 ═══════════════════


def calibrate_params(code: str, logic: dict, days: int = 250, max_combos: int = 30) -> dict:
    """单参数扰动式网格校准（无 LLM），返回 best_params 与评分面。

    in-sample 搜参有过拟合风险，输出必带 warning。
    """
    code = str(code or "").strip()
    if not code:
        return {"error": "code 不能为空"}
    spec_d, auto_fixed, hard_errors = normalize_logic_dict(logic)
    if hard_errors:
        return {"error": "策略规格非法", "validation_errors": hard_errors, "auto_fixed": auto_fixed, "hint": LOGIC_JSON_HINT}
    try:
        spec = StrategySpec.from_dict(spec_d)
    except Exception as e:
        return {"error": f"logic_json 解析失败: {e}", "hint": LOGIC_JSON_HINT}
    errors = spec.validate()
    if errors:
        out = {"error": "策略规格非法", "validation_errors": errors}
        if auto_fixed:
            out["auto_fixed"] = auto_fixed
        return out

    try:
        df = StockQuery().get_daily_kline(code, limit=days)
    except Exception as e:
        return _degraded(code, f"历史数据获取失败: {e}", 0, "无法校准参数，请使用默认参数并声明未经校准")
    bars = int(len(df))
    if bars < MIN_BARS_FOR_CALIBRATE:
        return _degraded(
            code,
            f"历史K线不足（{bars} < {MIN_BARS_FOR_CALIBRATE} 根）",
            bars,
            "参数校准需要更长历史；请使用默认参数并声明未经校准",
            min_bars=MIN_BARS_FOR_CALIBRATE,
        )

    budget = max(1, min(int(max_combos), 50))
    try:
        best_spec, best_result, best_score, surface = grid_search_params(
            code, spec, days=days, base_score=float("-inf"), budget=budget
        )
    except Exception as e:
        return {"error": f"参数校准失败: {e}", "code": code}

    if best_result is None:
        return {
            "status": "degraded",
            "code": code,
            "bars_available": bars,
            "reason": "所有参数组合均无有效回测结果",
            "guidance": "请检查逻辑是否产生交易信号，或改用更宽松的 entry_threshold",
        }

    out = {
        "status": "ok",
        "code": code,
        "in_sample": True,
        "bars_used": bars,
        "warning": "in-sample 搜参存在过拟合风险，样本外表现可能显著更差",
        "best_params": {
            "factors": [f.to_dict() for f in best_spec.factors],
            "entry_threshold": best_spec.entry_threshold,
        },
        "best_metrics": {
            "win_rate": round(best_result.win_rate, 4),
            "expectancy": round(best_result.expectancy, 4),
            "max_drawdown": round(best_result.max_drawdown, 4),
            "total_trades": best_result.total_trades,
        },
        "best_score": round(best_score, 4) if best_score == best_score else None,  # NaN 防御
        "combos_tested": len(surface),
        "score_surface": surface[:10],
    }
    if auto_fixed:
        out["auto_fixed"] = auto_fixed
    return out


# ═══════════════════ 市场状态刻画 ═══════════════════


def compute_market_regime(codes=None, sample_size: int = 20, days: int = 120) -> dict:
    """横截面聚合刻画市场状态：trend / range / high-vol / low-vol。

    codes 为空时从本地股票列表随机抽样（固定种子可复现）。
    """
    codes = _split_codes(codes)
    sampled_from = "explicit"
    if not codes:
        sampled_from = "market-sample"
        try:
            info = StockQuery().get_stock_info()
        except Exception as e:
            return _degraded("market", f"股票列表获取失败: {e}", 0, "无法刻画市场状态，请改用 market_sentiment 情绪评级定性判断")
        if info is None or info.empty:
            return _degraded("market", "本地无股票列表数据", 0, "请先 /fetch stocks 采集，或对指定 codes 调用")
        pool = info
        if "delist_date" in info.columns:
            pool = info[info["delist_date"].isna() | (info["delist_date"] == "")]
        n = min(int(sample_size), len(pool))
        if n <= 0:
            return _degraded("market", "无可用采样股票", 0, "请对指定 codes 调用")
        codes = pool["code"].sample(n, random_state=42).astype(str).tolist()

    query = StockQuery()
    samples = []
    skipped = 0
    for code in codes:
        try:
            df = query.get_daily_kline(code, limit=days)
        except Exception:
            skipped += 1
            continue
        if len(df) < MIN_BARS_FOR_REGIME:
            skipped += 1
            continue
        samples.append(_regime_descriptors(df))

    if len(samples) < MIN_REGIME_SAMPLES:
        return _degraded(
            "market",
            f"有效采样不足（{len(samples)} 只 < {MIN_REGIME_SAMPLES} 只，跳过 {skipped} 只）",
            len(samples),
            "无法可靠刻画市场状态；请缩小范围指定 codes，或改用 market_sentiment 定性判断",
        )

    def _median(key):
        vals = sorted(s[key] for s in samples)
        return vals[len(vals) // 2]

    vol_med = _median("realized_vol")
    adx_med = _median("adx")
    up_ratio = sum(s["up"] for s in samples) / len(samples)
    boll_med = _median("boll_width_pctile")

    # 分级阈值写死并对外披露，避免黑盒
    if adx_med >= 25:
        regime = "trend"
        advice = {"position_bias": "中", "strategy_bias": "趋势/接力类"}
    elif vol_med > 0.35:
        regime = "high-vol"
        advice = {"position_bias": "轻", "strategy_bias": "控制仓位、快进快出"}
    elif vol_med < 0.15:
        regime = "low-vol"
        advice = {"position_bias": "中", "strategy_bias": "潜伏等待变盘"}
    else:
        regime = "range"
        advice = {"position_bias": "轻-中", "strategy_bias": "高抛低吸"}

    return {
        "status": "ok",
        "regime": regime,
        "sampled_stocks": len(samples),
        "skipped_stocks": skipped,
        "sampled_from": sampled_from,
        "descriptors": {
            "realized_vol_ann": round(vol_med, 4),
            "adx_median": round(adx_med, 2),
            "boll_width_pctile": round(boll_med, 3),
            "up_ratio": round(up_ratio, 3),
        },
        "thresholds": {"trend_adx": 25, "high_vol_ann": 0.35, "low_vol_ann": 0.15},
        "advice_hint": advice,
    }


def _regime_descriptors(df) -> dict:
    """单股 regime 描述子：年化已实现波动 / ADX / 布林带宽分位 / 上涨占比"""
    d = add_indicators(df.copy())
    close = d["close"]
    ret = close.pct_change().dropna()
    tail = ret.tail(20)
    realized_vol = float(tail.std() * math.sqrt(244)) if len(tail) >= 10 else 0.2

    adx_val = float(d["adx"].iloc[-1]) if "adx" in d.columns and d["adx"].notna().iloc[-1] else 20.0

    if {"boll_upper", "boll_lower", "boll_mid"}.issubset(d.columns):
        width = ((d["boll_upper"] - d["boll_lower"]) / d["boll_mid"]).dropna()
        if len(width) >= 20:
            cur = float(width.iloc[-1])
            pctile = float((width <= cur).mean())
        else:
            pctile = 0.5
    else:
        pctile = 0.5

    up = float((ret.tail(20) > 0).mean()) if len(ret) >= 10 else 0.5
    return {"realized_vol": realized_vol, "adx": adx_val, "boll_width_pctile": pctile, "up": up}


# ═══════════════════ 保守仓位计算 ═══════════════════


def _min_lot(code: str | None) -> int:
    """最小买入股数：科创板最低 200 股（超出部分以 100 股递增），其余板块 100 股"""
    if code is not None:
        try:
            from deeppulse.src import codes as _codes

            if _codes.classify_board(code) == ("sh", "star"):
                return 200
        except Exception:  # noqa: BLE001 — 仓位计算不因代码分类失败而中断
            pass
    return LOT_SIZE


def position_size(
    capital: float,
    entry_price: float,
    stop_price: float,
    atr: float = 0,
    risk_pct: float = DEFAULT_RISK_PCT,
    win_rate: float | None = None,
    avg_win: float | None = None,
    avg_loss: float | None = None,
    sample_trades: int = 0,
    kelly_fraction: float = DEFAULT_KELLY_FRACTION,
    code: str | None = None,
) -> dict:
    """保守仓位：固定风险预算 + 半Kelly（样本充足时）+ 硬性仓位上限。

    - 单笔风险默认 2%，硬上限 5%（超出自动收缩）
    - Kelly 仅在样本 >= 20 笔且胜率/盈亏可用时给出，仓位硬上限 25%
    - 不足 Kelly 条件时输出轻/中/重档位而非精确数字
    - code 可选：科创板最小买入 200 股（步长 100），其余板块 100 股
    """
    min_lot = _min_lot(code)
    try:
        capital = float(capital)
        entry_price = float(entry_price)
        stop_price = float(stop_price)
    except (TypeError, ValueError):
        return {"error": "capital/entry_price/stop_price 必须是数值"}
    if capital <= 0 or entry_price <= 0:
        return {"error": "capital 与 entry_price 必须大于 0"}

    # 止损缺失（<=0）或无效（>=买入价）时用 ATR 兜底（2 倍 ATR，保守）
    stop_source = "explicit"
    if stop_price <= 0 or stop_price >= entry_price:
        if atr and float(atr) > 0:
            stop_price = entry_price - 2.0 * float(atr)
            stop_source = "atr_2x"
        else:
            return {"error": "stop_price 必须是小于 entry_price 的正数（做多），或提供 atr 让引擎推算保守止损"}
    risk_per_share = entry_price - stop_price

    risk_pct = min(max(float(risk_pct or DEFAULT_RISK_PCT), 0.001), RISK_PCT_MAX)
    risk_budget = capital * risk_pct
    shares = int(risk_budget / risk_per_share // min_lot) * min_lot
    if shares <= 0:
        return {
            "error": f"按当前风险预算不足 1 手（{min_lot} 股）",
            "risk_per_share": round(risk_per_share, 4),
            "risk_budget": round(risk_budget, 2),
        }

    position_value = shares * entry_price
    position_pct = position_value / capital

    # Kelly（样本充足才算；不足则只给档位）
    kelly_full = kelly_used = None
    kelly_note = ""
    try:
        w = float(win_rate) if win_rate is not None else None
        aw = float(avg_win) if avg_win is not None else None
        al = abs(float(avg_loss)) if avg_loss is not None else None
    except (TypeError, ValueError):
        w = aw = al = None
    if w is not None and aw and al and 0 < w < 1:
        if int(sample_trades) >= MIN_TRADES_FOR_KELLY:
            payoff = aw / al
            kelly_full = w - (1 - w) / payoff
            kelly_used = max(0.0, min(float(kelly_fraction) * kelly_full, KELLY_POSITION_CAP))
            # 仓位不超过 Kelly 上限
            if position_pct > kelly_used > 0:
                shares = int(capital * kelly_used / entry_price // min_lot) * min_lot
                shares = max(shares, min_lot)
                position_value = shares * entry_price
                position_pct = position_value / capital
        else:
            kelly_note = f"样本不足（{int(sample_trades)} < {MIN_TRADES_FOR_KELLY} 笔），不给出 Kelly，仅按风险预算+档位"
    else:
        kelly_note = "未提供胜率/盈亏样本，不给出 Kelly，仅按风险预算+档位"

    if position_pct <= 0.10:
        tier = "轻仓"
    elif position_pct <= 0.20:
        tier = "中仓"
    else:
        tier = "重仓（已接近单股上限）"

    out = {
        "risk_per_share": round(risk_per_share, 4),
        "stop_source": stop_source,
        "suggested_shares": shares,
        "position_value": round(position_value, 2),
        "position_pct": round(position_pct, 4),
        "position_tier": tier,
        "max_loss_at_stop": round(shares * risk_per_share, 2),
        "risk_pct_used": round(risk_pct, 4),
        "caps": {"risk_pct_max": RISK_PCT_MAX, "position_pct_max": KELLY_POSITION_CAP},
    }
    if kelly_used is not None:
        out["kelly_full"] = round(kelly_full, 4)
        out["kelly_used"] = round(kelly_used, 4)
        out["kelly_fraction"] = kelly_fraction
    if kelly_note:
        out["kelly_note"] = kelly_note
    return out
