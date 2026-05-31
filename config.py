"""A股日K数据库项目配置"""

from datetime import date, timedelta
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent

# 数据库路径
DB_PATH = PROJECT_ROOT / "db" / "astock.duckdb"

# 数据采集范围
END_DATE = date.today()
START_DATE = END_DATE - timedelta(days=5 * 365)  # 过去5年

# 数据源列表（按优先级排序）
DATA_SOURCES = ["akshare", "baostock"]

# 采集控制
FETCH_DELAY_SECONDS = 0.1  # 每次API调用间隔（秒），BaoStock无限流可适当降低
BATCH_SIZE = 50  # 批量写入数据库的行数

# 市场筛选：沪深主板
MARKETS = {
    "sh": {"prefix": ["6"], "name": "上海主板"},
    "sz": {"prefix": ["0"], "name": "深圳主板"},
}

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

# 实时行情配置
REALTIME_SOURCES = ["sina", "eastmoney"]  # 按优先级排序
REALTIME_TIMEOUT = 10.0  # 单源超时秒数
