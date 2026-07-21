"""AI 策略分析器 - 用已保存的策略 + LLM 分析当前行情，预测买卖点

核心方法 analyze_with_strategy() 接收一个已保存的个股策略 ID，加载
StrategySpec，计算当前各因子得分，调用 LLM 综合分析并给出操作建议。
"""

import json
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from deeppulse.strategy.models import StockStrategy
from deeppulse.strategy.repository import get_repository


class AIAnalyzer:
    """AI 策略分析器 - LLM 驱动的行情分析"""

    def __init__(self):
        self.repository = get_repository()

    def analyze_with_strategy(self, stock_strategy_id: str) -> dict:
        """用已保存的策略 + LLM 分析当前行情，预测买卖点

        流程：
        1. 获取已保存的 StockStrategy
        2. 加载最新 K 线数据 + 技术指标
        3. 计算当前各因子得分
        4. 构建行情画像
        5. 调用 LLM 综合分析
        """
        ss = self.repository.get_stock_strategy(stock_strategy_id)
        if not ss:
            return {"error": "策略不存在"}

        spec = ss.get_spec()

        # 加载 K 线数据
        df = self._load_kline(ss.stock_code, days=250)
        if df is None or df.empty:
            return {"error": f"股票 {ss.stock_code} 无K线数据"}

        from deeppulse.agent.indicators import TechnicalIndicators

        df = TechnicalIndicators.add_all(df)

        # 计算各因子得分
        from deeppulse.strategy.spec import factor_scores_detail

        detail = factor_scores_detail(spec, df)

        # 构建行情画像
        from deeppulse.strategy.llm_generator import build_kline_stats

        kline_stats = build_kline_stats(df)

        # 调用 LLM 分析
        analysis = self._llm_analyze(ss, spec, detail, kline_stats, df)

        return {
            "stock_code": ss.stock_code,
            "stock_name": ss.stock_name,
            "strategy_name": ss.name,
            "strategy_id": ss.id,
            "factor_scores": detail,
            "kline_stats": kline_stats,
            "llm_analysis": analysis,
        }

    def _llm_analyze(
        self,
        ss: StockStrategy,
        spec,
        detail: dict,
        kline_stats: dict,
        df: pd.DataFrame,
    ) -> dict:
        """调用 LLM 基于策略因子分析当前行情"""
        try:
            from deeppulse.agent.client import LLMClient, load_setting

            client = LLMClient(load_setting())
        except Exception as e:
            return {"error": f"LLM 客户端初始化失败: {e}"}

        # 构建因子描述
        factor_desc = []
        for f in spec.factors:
            factor_desc.append({
                "id": f.factor_id,
                "weight": f.weight,
                "direction": f.direction,
                "params": f.params,
            })

        # 构建因子得分
        factor_scores = []
        for f in detail.get("factors", []):
            factor_scores.append({
                "factor_id": f["factor_id"],
                "factor_name": f["factor_name"],
                "score": f["score"],
                "weighted_score": f["weighted_score"],
            })

        # 最近 5 日 K 线
        recent_kline = df.tail(5)[
            ["trade_date", "open", "high", "low", "close", "volume"]
        ].to_dict(orient="records")

        prompt = f"""你是一位资深 A 股量化交易分析师。请基于以下信息分析当前行情并给出操作建议。

【股票】{ss.stock_code} {ss.stock_name}
【策略】{ss.name}
【策略因子组合】
{json.dumps(factor_desc, ensure_ascii=False, indent=2)}
入场阈值: {spec.entry_threshold}
退出预设: {spec.exit_preset}

【当前各因子得分】
{json.dumps(factor_scores, ensure_ascii=False, indent=2)}
综合得分: {detail.get('total_score', 0)} / 阈值: {detail.get('entry_threshold', 0.6)}
是否触发: {'是' if detail.get('triggered') else '否'}

【近期行情画像】
{kline_stats}

【最近5日K线】
{json.dumps(recent_kline, ensure_ascii=False, default=str)}

请分析：
1. 当前各因子状态解读（哪些因子强/弱，为什么）
2. 综合研判：当前是否适合买入/持有/卖出
3. 关键价位：支撑位、压力位
4. 风险提示：需要注意的风险因素
5. 操作建议：具体的操作建议（买入/观望/止损/止盈）

严格输出 JSON：
{{
  "signal": "买入/观望/卖出",
  "confidence": 0.0到1.0之间的数值,
  "factor_analysis": [
    {{"factor_id": "因子id", "factor_name": "因子名称", "status": "强/中/弱", "interpretation": "解读"}}
  ],
  "overall_assessment": "综合研判文字",
  "key_levels": {{"support": 价格数值, "resistance": 价格数值}},
  "risk_factors": ["风险1", "风险2"],
  "action_advice": "具体操作建议",
  "reasoning": "完整推理过程"
}}"""

        try:
            resp = client.chat([{"role": "user", "content": prompt}])
            text = resp.get("content") or ""

            # 提取 JSON
            import re

            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            candidate = m.group(1) if m else None
            if candidate is None:
                start, end = text.find("{"), text.rfind("}")
                if start != -1 and end > start:
                    candidate = text[start : end + 1]
                else:
                    return {
                        "signal": "观望",
                        "confidence": 0.5,
                        "overall_assessment": text,
                        "error": "LLM 输出格式异常",
                    }

            return json.loads(candidate)

        except Exception as e:
            return {
                "signal": "观望",
                "confidence": 0.5,
                "overall_assessment": f"LLM 分析失败: {e}",
                "error": str(e),
            }

    def _load_kline(
        self, stock_code: str, days: int = 250
    ) -> Optional[pd.DataFrame]:
        """加载K线数据"""
        try:
            from deeppulse.src.query import StockQuery

            query = StockQuery()
            end = date.today()
            start = end - timedelta(days=int(days * 1.6))
            df = query.get_daily_kline(
                stock_code, start_date=str(start), end_date=str(end)
            )
            if df is None or df.empty:
                return None
            return df.sort_values("trade_date").reset_index(drop=True)
        except Exception:
            return None


# 全局实例
_analyzer = None


def get_ai_analyzer() -> AIAnalyzer:
    """获取全局 AI 分析器实例"""
    global _analyzer
    if _analyzer is None:
        _analyzer = AIAnalyzer()
    return _analyzer
