"""量化引擎工具 - Agent 的策略计算后端（只出数、不说话）

把操作逻辑放历史数据上验证（胜率/期望值/蒙特卡洛）、参数校准、
市场状态刻画、保守仓位计算。数据不足自动降级，绝不编造统计数字。
"""

import json

from deeppulse.agent.tools._shared import parse_positive_int


def evaluate_trade_logic(
    code: str,
    logic_json: str,
    days: int = 500,
    exit_preset: str = "conservative",
    sample_codes: str = "",
    n_simulations: int = 500,
) -> str:
    """把你的操作逻辑放到历史数据上验证，返回胜率/期望值/盈亏比/最大回撤/蒙特卡洛分位。

    logic_json 是 StrategySpec 形状的 JSON 字符串，例如：
    {"name": "放量突破回踩", "factors": [{"factor_id": "ma_trend", "params": {"fast": 5, "slow": 20}, "weight": 1.0, "direction": 1}], "entry_threshold": 0.6}

    数据不足时返回 status=degraded（不输出统计数字），请降级为定性分析并明确声明。
    同一逻辑最多评测 2 次（初评 + 修正后一次），不要反复微调刷分。
    """
    from deeppulse.strategy import calc_engine

    days = parse_positive_int(days, "days")
    try:
        logic = json.loads(logic_json) if isinstance(logic_json, str) else logic_json
    except Exception as e:
        return json.dumps({"error": f"logic_json 不是合法 JSON: {e}"}, ensure_ascii=False)
    if not isinstance(logic, dict):
        return json.dumps({"error": "logic_json 必须是 JSON 对象"}, ensure_ascii=False)

    out = calc_engine.evaluate_trade_logic(
        code,
        logic,
        days=days,
        exit_preset=str(exit_preset or "conservative"),
        sample_codes=sample_codes,
        n_simulations=parse_positive_int(n_simulations, "n_simulations"),
    )
    return json.dumps(out, ensure_ascii=False, default=str)


def calibrate_params(code: str, logic_json: str, days: int = 250, max_combos: int = 30) -> str:
    """对操作逻辑的因子参数做单参数扰动式网格校准（in-sample，有过拟合风险）。

    返回 best_params 与评分面。仅在给出具体止损/持有参数前按需调用；
    校准结果必须向用户声明为 in-sample 拟合。
    """
    from deeppulse.strategy import calc_engine

    days = parse_positive_int(days, "days")
    try:
        logic = json.loads(logic_json) if isinstance(logic_json, str) else logic_json
    except Exception as e:
        return json.dumps({"error": f"logic_json 不是合法 JSON: {e}"}, ensure_ascii=False)
    if not isinstance(logic, dict):
        return json.dumps({"error": "logic_json 必须是 JSON 对象"}, ensure_ascii=False)

    out = calc_engine.calibrate_params(code, logic, days=days, max_combos=parse_positive_int(max_combos, "max_combos"))
    return json.dumps(out, ensure_ascii=False, default=str)


def market_regime(codes: str = "", sample_size: int = 20, days: int = 120) -> str:
    """刻画当前市场状态（trend/range/high-vol/low-vol）与仓位/战法倾向提示。

    codes 为空时从本地股票列表随机抽样（可复现）。与 market_sentiment 情绪
    评级互补：本工具刻画量价结构，情绪工具刻画涨跌停赚钱效应。
    """
    from deeppulse.strategy import calc_engine

    out = calc_engine.compute_market_regime(
        codes=codes,
        sample_size=parse_positive_int(sample_size, "sample_size"),
        days=parse_positive_int(days, "days"),
    )
    return json.dumps(out, ensure_ascii=False, default=str)


def position_size(
    capital: float,
    entry_price: float,
    stop_price: float,
    atr: float = 0,
    risk_pct: float = 0.02,
    win_rate: float = None,
    avg_win: float = None,
    avg_loss: float = None,
    sample_trades: int = 0,
) -> str:
    """保守仓位计算：固定风险预算（默认单笔 2%）+ 半 Kelly（样本充足时）+ 单股 25% 硬上限。

    stop_price 应低于 entry_price（做多）；缺止损位时可传 atr 由引擎按 2 倍 ATR 推算。
    样本 < 20 笔时不给 Kelly，仅输出轻/中/重仓档位——请勿向用户报精确 Kelly 数字。
    """
    from deeppulse.strategy import calc_engine

    out = calc_engine.position_size(
        capital=capital,
        entry_price=entry_price,
        stop_price=stop_price,
        atr=atr,
        risk_pct=risk_pct,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        sample_trades=int(sample_trades or 0),
    )
    return json.dumps(out, ensure_ascii=False, default=str)


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "evaluate_trade_logic",
            "description": "把操作逻辑（多因子规则）放到历史数据上验证，返回胜率/期望值/盈亏比/最大回撤/蒙特卡洛分位。给出操作建议前必须先用它验证，禁止只凭指标目测给买点。数据不足时返回 status=degraded，请降级为定性分析。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码，如 '600519'"},
                    "logic_json": {
                        "type": "string",
                        "description": "操作逻辑 JSON（StrategySpec 形状）：factors[{factor_id, params, weight, direction}] + entry_threshold，可选 exit_preset/exit_params",
                    },
                    "days": {"type": "integer", "description": "回测天数（交易日）", "default": 500, "minimum": 1},
                    "exit_preset": {
                        "type": "string",
                        "description": "退出策略预设，默认 conservative（安全优先）",
                        "default": "conservative",
                        "enum": ["conservative", "balanced", "aggressive"],
                    },
                    "sample_codes": {
                        "type": "string",
                        "description": "可选，逗号分隔的额外股票代码，用于多股普适性验证",
                    },
                    "n_simulations": {"type": "integer", "description": "蒙特卡洛模拟次数", "default": 500, "minimum": 1},
                },
                "required": ["code", "logic_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calibrate_params",
            "description": "对操作逻辑的因子参数做网格校准（in-sample，有过拟合风险，须向用户声明）。仅在需要给出具体参数前按需调用，不要每次分析都调。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "logic_json": {"type": "string", "description": "操作逻辑 JSON（同 evaluate_trade_logic）"},
                    "days": {"type": "integer", "description": "校准天数，需要较长历史", "default": 250, "minimum": 1},
                    "max_combos": {"type": "integer", "description": "评测参数组合上限（1-50）", "default": 30, "minimum": 1},
                },
                "required": ["code", "logic_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "market_regime",
            "description": "刻画市场量价结构状态（trend/range/high-vol/low-vol）及仓位/战法倾向提示。与 market_sentiment（情绪）互补，用于调整操作建议的进攻/防守倾向。",
            "parameters": {
                "type": "object",
                "properties": {
                    "codes": {"type": "string", "description": "逗号分隔的采样股票代码；为空时从本地股票列表随机抽样"},
                    "sample_size": {"type": "integer", "description": "随机抽样股数", "default": 20, "minimum": 1},
                    "days": {"type": "integer", "description": "每只股票取多少交易日刻画状态", "default": 120, "minimum": 1},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "position_size",
            "description": "保守仓位计算：单笔风险预算（默认2%，上限5%）+ 半Kelly（样本≥20笔才给）+ 单股25%硬上限。给仓位建议前必须先算，禁止拍脑袋报仓位比例。",
            "parameters": {
                "type": "object",
                "properties": {
                    "capital": {"type": "number", "description": "总资金"},
                    "entry_price": {"type": "number", "description": "计划买入价"},
                    "stop_price": {"type": "number", "description": "止损价（做多须低于买入价；无止损时传 0 并提供 atr）"},
                    "atr": {"type": "number", "description": "ATR 值，止损缺失时按 2 倍 ATR 推算保守止损", "default": 0},
                    "risk_pct": {"type": "number", "description": "单笔风险比例（0-0.05）", "default": 0.02},
                    "win_rate": {"type": "number", "description": "历史胜率（0-1，来自 evaluate_trade_logic）"},
                    "avg_win": {"type": "number", "description": "平均盈利幅度（如 0.05=5%）"},
                    "avg_loss": {"type": "number", "description": "平均亏损幅度（正数，如 0.03=3%）"},
                    "sample_trades": {"type": "integer", "description": "上述胜率/盈亏的样本笔数，<20 不给 Kelly"},
                },
                "required": ["capital", "entry_price", "stop_price"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "evaluate_trade_logic": evaluate_trade_logic,
    "calibrate_params": calibrate_params,
    "market_regime": market_regime,
    "position_size": position_size,
}
