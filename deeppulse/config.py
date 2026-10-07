"""A股日K数据库项目配置"""

import json
from datetime import date, timedelta
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 数据库路径（可被 setting.json 的 data.db_path 覆盖，见 reload_data_settings）
_DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "astock.duckdb"
DB_PATH = _DEFAULT_DB_PATH

# 数据采集范围
END_DATE = date.today()
START_DATE = END_DATE - timedelta(days=5 * 365)  # 过去5年

# 数据源列表（按优先级排序，互为备份；单源失败/无数据自动切换下一源）
# baostock/akshare/eastmoney/tencent 为前复权口径；sina 为不复权，仅作最后兜底
DATA_SOURCES = ["baostock", "akshare", "eastmoney", "tencent", "sina"]

# 采集控制
FETCH_DELAY_SECONDS = 0  # 每次API调用间隔（秒），BaoStock无限流可设0，AkShare建议0.3
BATCH_SIZE = 50  # 批量写入数据库的行数

# 记忆系统配置
MEMORY_DECAY_THRESHOLD = 0.1  # 有效权重低于此值的记忆将被遗忘
MEMORY_CONTEXT_MAX_CHARS = 4000  # 注入 system prompt 的记忆上下文最大字符数
MEMORY_SEARCH_TOP_K = 5  # 默认搜索返回条数
MEMORY_COMPRESSION_BATCH = 10  # 单次压缩合并的记忆条数
MEMORY_SESSION_TTL_HOURS = 24  # 会话临时记忆过期时间（小时）

# Embedding 配置
EMBEDDING_DIMENSION = 768  # 向量维度（text2vec-base-chinese 输出 768 维）
EMBEDDING_BATCH_SIZE = 10  # 每批生成 embedding 的条数
EMBEDDING_CACHE_MAX = 5000  # 内存中缓存的最大向量数

# 工具输出限制：单个工具结果的最大字符数，防止上下文溢出导致 LLM 调用卡死
MAX_TOOL_RESULT_CHARS = 8000

# 数据可选化：在线兜底与本地缓存（股票行情数据可不预先下载）
DATA_ONLINE_FALLBACK = True  # 本地缺数时在线拉取（默认开）
DATA_CACHE_TO_DB = False  # 在线兜底结果写回本地库（默认关，每次重新拉取）
DATA_DEFAULT_SOURCE = None  # 在线日线首选源；None = DATA_SOURCES[0]
ONLINE_FETCH_TIMEOUT = 30  # 单次在线取数总超时秒数
ONLINE_MINUTE_INTERVAL = 0.5  # akshare 分钟接口限频间隔（秒）
SCREEN_ONLINE_MAX_CANDIDATES = 30  # 选股在线路径精筛候选上限
BACKTEST_ONLINE_MAX_CODES = 10  # 无本地数据时在线回测的 codes 上限

# 消息历史预算：每轮 LLM 调用前按预算做工具组原子折叠 + 滑动窗口（B7 上下文管理）
HISTORY_MAX_MESSAGES = 60  # 保留历史最大消息条数（不含 system 主提示）；0 = 不限
HISTORY_MAX_CHARS = 40000  # 保留历史最大字符预算；0 = 不限
HISTORY_MAX_TOKENS = 16000  # 保留历史最大 token 预算（estimate_tokens 粗估）；0 = 不限
HISTORY_KEEP_LAST_TURNS = 1  # 最近 N 轮用户问答永不折叠
HISTORY_FOLD_SUMMARY_MAX_CHARS = 600  # 折叠占位符摘要的最大字符数

# 工具调用去重检测窗口：检查最近N次调用是否有重复
TOOL_REPEAT_DETECTION_WINDOW = 5

# 实时行情配置（按优先级排序，三态熔断自动故障切换）
REALTIME_SOURCES = ["sina", "tencent", "eastmoney"]  # tencent 带真实成交时间戳
REALTIME_TIMEOUT = 10.0  # 单源超时秒数

# 重试策略配置
RETRY_MAX_RETRIES = 3  # 最大重试次数（不含首次尝试）
RETRY_BASE_DELAY = 1.0  # 基础延迟秒数
RETRY_MAX_DELAY = 30.0  # 最大延迟秒数
RETRY_BACKOFF_FACTOR = 2.0  # 退避因子

# 熔断器配置
CIRCUIT_FAILURE_THRESHOLD = 3  # 触发熔断的连续失败次数
CIRCUIT_RECOVERY_TIMEOUT = 60  # 熔断恢复超时秒数
CIRCUIT_HALF_OPEN_CALLS = 1  # 半开状态最大试探调用数

# 数据采集韧性配置
FETCH_RECONNECT_INTERVAL = 150  # 每处理N只股票主动重连一次
FETCH_CONSECUTIVE_FAIL_THRESHOLD = 3  # 连续失败N次触发重连
FETCH_TIMEOUT = 30  # 单只股票采集超时秒数

# 数据验证配置
DATA_VALIDATION_ENABLED = True  # 是否启用数据验证


# ── setting.json "data" 段覆盖 ──────────────────────────────────────


def load_data_settings() -> dict:
    """读取 setting.json 的 data 段。

    只取数据相关配置（online_fallback / cache_to_db / default_source / db_path），
    不做 API Key 校验（不能走 agent.client.load_setting，src 层不应依赖 agent）。
    文件缺失/无 data 段/解析失败 → 返回 {}（用默认值）。
    """
    try:
        with open(PROJECT_ROOT / "setting.json", encoding="utf-8") as f:
            data = json.load(f)
        data = (data or {}).get("data") or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def reload_data_settings() -> None:
    """按 setting.json 的 data 段覆盖模块级常量（import 期自动调用一次，测试可重载）。

    每次从默认值重新合并，重复调用不会叠加覆盖。
    """
    global DB_PATH, DATA_ONLINE_FALLBACK, DATA_CACHE_TO_DB, DATA_DEFAULT_SOURCE
    data = load_data_settings()

    db_path = data.get("db_path")
    DB_PATH = Path(db_path) if db_path else _DEFAULT_DB_PATH

    DATA_ONLINE_FALLBACK = bool(data.get("online_fallback", True))
    DATA_CACHE_TO_DB = bool(data.get("cache_to_db", False))

    source = data.get("default_source")
    DATA_DEFAULT_SOURCE = source if source in DATA_SOURCES else None


reload_data_settings()
