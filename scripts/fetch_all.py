"""全量日K数据采集脚本 - 调用统一 DataCollector，消除重复代码

韧性特性（已内置在 collector.py 中）：
- 周期性重连（每 150 只股票）
- 连续失败检测（3 次触发重连）
- 超时/连接错误立即重连
- 指数退避重试
- 数据验证
"""

import logging
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from deeppulse import config
from deeppulse.src.collector import fetch_all_kline, fetch_and_store_stock_list, open_connection
from deeppulse.src.database import get_stock_list

# 配置日志输出到文件和控制台
LOG_FILE = config.PROJECT_ROOT / "fetch_progress.log"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 60)
    logger.info("全量日K采集启动")
    logger.info(f"日期范围: {config.START_DATE} ~ {config.END_DATE}")
    logger.info(
        f"数据源: {config.DATA_SOURCES} | 超时: {config.FETCH_TIMEOUT}秒 | 重连间隔: {config.FETCH_RECONNECT_INTERVAL}"
    )
    logger.info("=" * 60)

    # 检查股票列表
    with open_connection() as conn:
        codes = get_stock_list(conn)
        if not codes:
            logger.info("stock_info 为空，自动采集股票列表...")
            fetch_and_store_stock_list()
            codes = get_stock_list(conn)
        logger.info(f"股票总数: {len(codes)}")

    # 全量采集（所有韧性逻辑已在 collector.fetch_all_kline 中）
    start_time = time.time()
    stats = fetch_all_kline()

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info(f"采集完成! 耗时: {elapsed / 60:.1f}分钟")
    logger.info(f"成功: {stats['success']}, 失败: {stats['failed']}, 总行数: {stats['rows']}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
