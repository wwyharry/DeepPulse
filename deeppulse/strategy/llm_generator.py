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

【可用因子库】（factor_id 只能从下列清单中选取，不得编造）
{_factor_catalog_prompt()}

【设计原则】
1. 选取 2~5 个**互补**的因子组合：趋势/动量/量能/位置类搭配，避免同类因子堆叠（如不要同时选3个均线类因子）。给出每个因子的 weight(0.1~2.0)、direction(1正向/-1反向) 和 params。
2. 每个因子输出 0~1 逐 bar 得分，加权归一后与 entry_threshold 比较产生入场信号。方向用 direction 表达：看多信号用 1，看空/反向信号用 -1，不要在参数里硬编码反向。direction=-1 为防守/扣分项（触发时压低综合分），请保证至少有一个 direction=1 的正向因子；综合分按正向因子权重和归一化后设定 entry_threshold。
3. params 必须在因子库给定的 min/max/step 范围内；不写的参数用默认值。**严禁编造因子库以外的 factor_id 或参数名**。
4. entry_threshold 为综合得分入场阈值（0.3~0.8）：阈值越高信号越少越精选，结合行情画像调整（震荡市宜高、趋势市宜适中）。
5. exit_preset 从 conservative/balanced/aggressive 中选择，与行情波动匹配。
6. 针对该股**近期**行情画像设计，但不要过拟合单一历史片段：因子逻辑应有交易常识支撑，llm_rationale 里说明选择逻辑。
{f"7. 用户附加要求: {hint}" if hint else ""}

严格只输出如下 JSON，不要输出任何其他文字（禁止 markdown 代码块以外的文本）：
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
        prompt = f"""你是一位资深量化策略调优专家。以下是一个多因子策略的回测结果，请诊断问题并给出调整动作。

【当前策略】
{spec.to_json()}

【回测指标】
{json.dumps(metrics, ensure_ascii=False, indent=2)}

【退出统计】
{json.dumps(result.get("exit_statistics", {}), ensure_ascii=False)}

【近期迭代历史】（最近 5 轮）
{json.dumps(history_brief, ensure_ascii=False)}

【可用因子库】（add_factor 的 factor_id 只能从下列清单选取）
{_factor_catalog_prompt()}

【诊断指引】（对照指标找根因，不要头痛医头）
- total_trades 过少（<10）→ 入场条件过严：entry_threshold 偏高，或因子太苛刻；优先 adjust_threshold / adjust_param 放宽
- max_drawdown 过大 → 缺少防守：加反向/防守类因子、收紧阈值、或换更保守的 exit_preset
- win_rate 低且盈亏比差 → 因子信号互相冲突或方向错误：检查 direction，remove 掉拖后腿的因子
- total_return 尚可但 sharpe 低/波动大 → 信号质量不稳：提高核心因子权重，砍掉弱因子
- avg_hold_days 异常（过长/过短）→ 信号滞后或过敏感：调整动量/趋势类因子参数
- 对照迭代历史：若分数连续下降说明上一轮调整方向错误，应小幅回调而非继续激进改动

【调整纪律】
1. 每轮最多 3 个动作，小步调整，一次只改一类问题
2. 只允许以下动作类型：adjust_param / adjust_weight / adjust_threshold / add_factor / remove_factor
3. adjust_param 的 param 必须是该因子真实存在的参数名，value 在其 min/max 范围内
4. adjust_weight 的 weight 必须为正数（建议 0.1~2.0）；adjust_threshold 在 0.3~0.8 之间
5. add_factor 时 factor_id 必须存在于因子库，params 在给定范围内
6. 不要为了改而改：若结构已合理，actions 用空数组（交由参数网格搜索微调）

严格只输出如下 JSON：
{{
  "comment": "一段中文分析评语（指出根因，而不是复述指标）",
  "actions": [
    {{"type": "adjust_param", "factor_id": "因子id", "param": "参数名", "value": 数值}},
    {{"type": "adjust_weight", "factor_id": "因子id", "weight": 数值}},
    {{"type": "adjust_threshold", "value": 0.3到0.8之间的数值}},
    {{"type": "add_factor", "factor_id": "因子id", "params": {{}}, "weight": 1.0, "direction": 1}},
    {{"type": "remove_factor", "factor_id": "因子id"}}
  ]
}}"""
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
2. 策略的稳定性（是否有大起大落；评分高的策略是否只是过拟合了某段行情）
3. 风险收益比（夏普比率、最大回撤、盈亏比）
4. 样本可信度（total_trades 太少时，胜率和收益的统计意义有限，必须在 reasoning 中说明）
5. 是否有改进空间

【结论原则】
- 优先选择风险收益比均衡、表现稳定的策略，而不是收益最高但回撤巨大的策略
- recommended_spec 仅在有明确改进方向时给出（如替换弱因子、微调阈值），且必须基于因子库中的 factor_id，结构与"当前最佳策略"一致
- 宁缺毋滥：没有足够证据支持改进时，recommended_spec 设为 null

严格只输出如下 JSON：
{{
  "reasoning": "详细的分析推理过程，说明为什么选择这个策略（含风险与样本量评估）",
  "recommended_spec": null 或 策略规格JSON（如果你认为可以进一步优化）
}}"""

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
