"""数据验证层 — K线数据合理性检查"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def validate_kline_df(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """验证K线数据合理性，剔除/修正异常值

    检查项：
        1. 必须字段完整性
        2. 关键字段（价格/成交量/日期）不允许 NaN
        3. 价格合理性：open/high/low/close 均须 > 0（剔除负价与全 0 停牌标记行），
           且 high >= low, high >= open/close, low <= open/close
        4. 成交量非负
        5. 去重 + 按日期排序

    Args:
        df: 原始K线 DataFrame
        code: 股票代码（用于日志）

    Returns:
        清洗后的 DataFrame
    """
    if df.empty:
        return df

    original_len = len(df)

    # 1. 必须字段检查
    required = ["trade_date", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"[{code}] 缺少必要列: {missing}")

    # 2. NaN 检查 — 关键字段不允许 NaN（含 volume：NaN 成交量会以 NULL 入库并污染量能分析）
    key_cols = ["trade_date", "open", "high", "low", "close", "volume"]
    nan_mask = df[key_cols].isnull().any(axis=1)
    if nan_mask.any():
        nan_count = int(nan_mask.sum())
        logger.warning(f"[{code}] 发现 {nan_count} 行含 NaN 关键字段（价格/成交量/日期），已剔除")
        df = df[~nan_mask].copy()

    if df.empty:
        return df

    # 3. 价格合理性：价格必须为正（剔除负价与全 0 停牌标记），且 high >= low, high >= open/close, low <= open/close
    nonpos = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    invalid = (
        nonpos
        | (df["high"] < df["low"])
        | (df["high"] < df["open"])
        | (df["high"] < df["close"])
        | (df["low"] > df["open"])
        | (df["low"] > df["close"])
    )
    if invalid.any():
        invalid_count = int(invalid.sum())
        logger.warning(f"[{code}] 发现 {invalid_count} 行价格不合理（非正价格/全0停牌标记 或 high<low 等），已剔除")
        df = df[~invalid].copy()

    if df.empty:
        return df

    # 4. 成交量非负
    if "volume" in df.columns:
        neg_vol = df["volume"] < 0
        if neg_vol.any():
            neg_count = neg_vol.sum()
            logger.warning(f"[{code}] 发现 {neg_count} 行负成交量，已修正为 0")
            df.loc[neg_vol, "volume"] = 0

    # 5. 去重 + 排序
    df = df.drop_duplicates(subset=["trade_date"], keep="first")
    df = df.sort_values("trade_date").reset_index(drop=True)

    if len(df) < original_len:
        logger.info(f"[{code}] 数据验证: {original_len} → {len(df)} 行")

    return df
