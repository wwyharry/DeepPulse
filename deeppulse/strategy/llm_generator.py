"""LLM 策略生成器 - 基于大模型生成/分析结构化多因子策略

复用 deeppulse.agent.client.LLMClient，将股票行情画像 + 因子库目录
注入 prompt，要求 LLM 输出 StrategySpec JSON；并提供回测结果分析能力，
输出策略调整动作，供自动优化循环使用。
"""

import json
import re

import numpy as np
import pandas as pd

from deeppulse.strategy.factor_library import list_factors
from deeppulse.strategy.spec import FactorSpec, StrategySpec


class LLMGenerationError(Exception):
    """LLM 策略生成失败（重试后仍无法得到合法策略）"""


def build_kline_stats(df: pd.DataFrame) -> dict:
    """从带指标的 K线 DataFrame 提取行情画像，供 LLM prompt 使用"""
    if df is None or df.empty:
        return {}
    close = df["close"]
    ret = close.pct_change().dropna()
    stats: dict = {
        "bars": int(len(df)),
        "latest_close": round(float(close.iloc[-1]), 2),
        "period_return_pct": round(float((close.iloc[-1] / close.iloc[0] - 1) * 100), 2),
        "annual_volatility_pct": round(float(ret.std() * np.sqrt(244) * 100), 2) if len(ret) > 1 else 0.0,
        "avg_volume": round(float(df["volume"].mean()), 0) if "volume" in df else 0.0,
    }
    # 区间最大回撤
    cummax = close.cummax()
    drawdown = (close - cummax) / cummax
    stats["max_drawdown_pct"] = round(float(drawdown.min() * 100), 2)
    # 最新指标快照
    last = df.iloc[-1]
    snapshot = {}
    for col in [
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "dif",
        "dea",
        "macd",
        "rsi6",
        "rsi14",
        "k",
        "d",
        "j",
        "boll_upper",
        "boll_mid",
        "boll_lower",
        "atr14",
        "vol_ratio",
    ]:
        if col in df.columns and pd.notna(last.get(col)):
            snapshot[col] = round(float(last[col]), 4)
    stats["indicator_snapshot"] = snapshot
    return stats


def _factor_catalog_prompt() -> str:
    """生成因子库目录文本（供 prompt 注入）"""
    lines = []
    for f in list_factors():
        params = ", ".join(f"{k}(默认{v.default}, 范围{v.min}~{v.max})" for k, v in f.params.items()) or "无参数"
        lines.append(f"- {f.id}【{f.name}】: {f.description}；参数: {params}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON 对象（容忍 ```json 代码块包裹和前后杂文本）"""
    if not text:
        raise ValueError("LLM 返回内容为空")
    # 优先匹配 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = m.group(1) if m else None
    if candidate is None:
        # 退而求其次：取第一个 { 到最后一个 }
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("LLM 输出中未找到 JSON 对象")
        candidate = text[start : end + 1]
    return json.loads(candidate)


class LLMStrategyGenerator:
    """LLM 策略生成器：生成初始策略 + 分析回测结果给出调整建议"""

    def __init__(self, llm_client=None):
        self._client = llm_client

    @property
    def client(self):
        """惰性创建 LLM 客户端（避免无配置环境下 import 即失败）"""
        if self._client is None:
            from deeppulse.agent.client import LLMClient, load_setting

            self._client = LLMClient(load_setting())
        return self._client

    def _chat(self, prompt: str) -> str:
        resp = self.client.chat([{"role": "user", "content": prompt}])
        return resp.get("content") or ""

    def generate(self, code: str, kline_stats: dict, hint: str | None = None) -> StrategySpec:
        """让 LLM 基于行情画像从因子库中生成多因子策略

        Args:
            code: 股票代码
            kline_stats: build_kline_stats() 的输出
            hint: 用户补充要求（如"偏好短线反弹"）

        Raises:
            LLMGenerationError: 重试后仍无法得到合法策略
        """
        prompt = f"""你是一位资深 A 股量化策略研究员。请为股票 {code} 设计一个多因子量化择时策略。

【该股近期行情画像】
{json.dumps(kline_stats, ensure_ascii=False, indent=2)}

【可用因子库】（只能从下列因子中选取，不得编造）
{_factor_catalog_prompt()}

【要求】
1. 选取 2~5 个互补的因子组合（趋势/动量/量能/位置类搭配），给出每个因子的 weight(0.1~2.0)、direction(1正向/-1反向) 和参数（必须在给定范围内）。
2. entry_threshold 为综合得分入场阈值（0.3~0.8）。
3. exit_preset 从 conservative/balanced/aggressive 中选择。
{f"4. 用户附加要求: {hint}" if hint else ""}

严格只输出如下 JSON，不要输出任何其他文字：
{{
  "name": "策略名称",
  "factors": [{{"factor_id": "因子id", "params": {{}}, "weight": 1.0, "direction": 1}}],
  "entry_threshold": 0.6,
  "exit_preset": "balanced",
  "llm_rationale": "一段话说明因子选择逻辑"
}}"""
        last_error: Exception | None = None
        for _ in range(3):  # 首次 + 最多重试 2 次
            try:
                text = self._chat(prompt)
                spec = StrategySpec.from_dict(_extract_json(text))
                errors = spec.validate()
                if errors:
                    raise ValueError("策略校验失败: " + "; ".join(errors))
                return spec
            except (ValueError, KeyError, json.JSONDecodeError, TypeError) as e:
                last_error = e
        raise LLMGenerationError(f"LLM 策略生成失败（已重试）: {last_error}")

    def analyze_backtest(self, spec: StrategySpec, result: dict, history: list[dict]) -> dict:
        """让 LLM 分析回测结果，输出调整动作

        Returns:
            {"comment": str, "actions": [{"type": "add_factor"|"remove_factor"|
             "adjust_weight"|"adjust_param"|"adjust_threshold", ...}]}
        """
        metrics = {
            k: result.get(k)
            for k in [
                "total_return",
                "annual_return",
                "max_drawdown",
                "win_rate",
                "sharpe_ratio",
                "profit_factor",
                "total_trades",
                "avg_hold_days",
            ]
        }
        history_brief = [
            {
                "iteration": h.get("iteration"),
                "score": h.get("score"),
                "total_return": (h.get("metrics") or {}).get("total_return"),
            }
            for h in history[-5:]
        ]
        prompt = f"""你是一位资深量化策略调优专家。以下是一个多因子策略的回测结果，请分析并给出调整动作。

【当前策略】
{spec.to_json()}

【回测指标】
{json.dumps(metrics, ensure_ascii=False, indent=2)}

【退出统计】
{json.dumps(result.get("exit_statistics", {}), ensure_ascii=False)}

【近期迭代历史】
{json.dumps(history_brief, ensure_ascii=False)}

【可用因子库】
{_factor_catalog_prompt()}

请判断问题所在（如交易过少=阈值过高、回撤过大=缺少防守因子、胜率低=因子冲突等），
严格只输出如下 JSON：
{{
  "comment": "一段中文分析评语",
  "actions": [
    {{"type": "adjust_param", "factor_id": "因子id", "param": "参数名", "value": 数值}},
    {{"type": "adjust_weight", "factor_id": "因子id", "weight": 数值}},
    {{"type": "adjust_threshold", "value": 0.3到0.8之间的数值}},
    {{"type": "add_factor", "factor_id": "因子id", "params": {{}}, "weight": 1.0, "direction": 1}},
    {{"type": "remove_factor", "factor_id": "因子id"}}
  ]
}}
actions 可以为空数组（表示当前结构合理，仅依赖参数网格搜索）。"""
        try:
            text = self._chat(prompt)
            data = _extract_json(text)
            actions = data.get("actions")
            return {
                "comment": str(data.get("comment", "")),
                "actions": actions if isinstance(actions, list) else [],
            }
        except (ValueError, KeyError, json.JSONDecodeError, TypeError) as e:
            return {"comment": f"LLM 分析失败: {e}", "actions": []}

    def analyze_final_strategy(self, code: str, history: list[dict], best_spec: dict, best_result: dict) -> dict:
        """最终分析：LLM 综合所有轮次结果，选出最优策略并给出原因

        Returns:
            {"reasoning": str, "recommended_spec": dict | None}
        """
        # 构建历史摘要
        history_summary = []
        for h in history:
            m = h.get("metrics", {})
            history_summary.append(
                {
                    "iteration": h.get("iteration"),
                    "phase": h.get("phase", "unknown"),
                    "score": h.get("score"),
                    "total_return": m.get("total_return"),
                    "win_rate": m.get("win_rate"),
                    "max_drawdown": m.get("max_drawdown"),
                    "sharpe_ratio": m.get("sharpe_ratio"),
                    "total_trades": m.get("total_trades"),
                    "comment": (h.get("llm_comment") or "")[:100],  # 截断
                }
            )

        # 最佳策略指标
        best_metrics = {
            k: best_result.get(k)
            for k in ["total_return", "annual_return", "max_drawdown", "win_rate", "sharpe_ratio", "total_trades"]
        }

        prompt = f"""你是一位资深量化策略评审专家。以下是股票 {code} 的策略优化全过程，请综合分析并给出最终建议。

【优化历史】
{json.dumps(history_summary, ensure_ascii=False, indent=2)}

【当前最佳策略】
{json.dumps(best_spec, ensure_ascii=False, indent=2)}

【最佳策略指标】
{json.dumps(best_metrics, ensure_ascii=False, indent=2)}

请综合分析：
1. 各轮策略的表现对比（不只看评分，还要看收益、风险、交易频率等）
2. 策略的稳定性（是否有大起大落）
3. 风险收益比（夏普比率、最大回撤）
4. 是否有改进空间

严格只输出如下 JSON：
{{
  "reasoning": "详细的分析推理过程，说明为什么选择这个策略",
  "recommended_spec": null 或 策略规格JSON（如果你认为可以进一步优化）
}}

如果当前最佳策略已经足够好，recommended_spec 设为 null。"""

        try:
            text = self._chat(prompt)
            data = _extract_json(text)
            return {
                "reasoning": str(data.get("reasoning", "")),
                "recommended_spec": data.get("recommended_spec"),
            }
        except (ValueError, KeyError, json.JSONDecodeError, TypeError) as e:
            return {"reasoning": f"最终分析失败: {e}", "recommended_spec": None}


def apply_actions(spec: StrategySpec, actions: list[dict]) -> StrategySpec:
    """将 LLM 给出的调整动作应用到策略规格上，返回新规格（应用失败的动作跳过）"""
    new_spec = StrategySpec.from_dict(spec.to_dict())  # 深拷贝
    by_id = {f.factor_id: f for f in new_spec.factors}
    for action in actions:
        try:
            atype = action.get("type")
            fid = action.get("factor_id")
            if atype == "adjust_param" and fid in by_id:
                by_id[fid].params[action["param"]] = action["value"]
            elif atype == "adjust_weight" and fid in by_id:
                by_id[fid].weight = max(0.1, float(action["weight"]))
            elif atype == "adjust_threshold":
                new_spec.entry_threshold = min(0.8, max(0.3, float(action["value"])))
            elif atype == "add_factor" and fid and fid not in by_id:
                new_spec.factors.append(
                    FactorSpec(
                        factor_id=fid,
                        params=action.get("params") or {},
                        weight=float(action.get("weight", 1.0)),
                        direction=int(action.get("direction", 1)),
                    )
                )
                by_id[fid] = new_spec.factors[-1]
            elif atype == "remove_factor" and fid in by_id and len(new_spec.factors) > 1:
                new_spec.factors = [f for f in new_spec.factors if f.factor_id != fid]
                del by_id[fid]
        except (KeyError, ValueError, TypeError):
            continue
    # 应用后若整体非法，回退为原规格
    return spec if new_spec.validate() else new_spec
