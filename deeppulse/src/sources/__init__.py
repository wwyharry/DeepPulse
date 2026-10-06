from .akshare_source import AkShareSource
from .baostock_source import BaoStockSource
from .eastmoney_source import EastMoneySource
from .sina_source import SinaSource
from .tencent_source import TencentSource

__all__ = [
    "AkShareSource",
    "BaoStockSource",
    "EastMoneySource",
    "SinaSource",
    "TencentSource",
    "SOURCE_CLASSES",
    "make_source",
]

# 历史数据源注册表：名称 → 实现类（与 config.DATA_SOURCES 配置名对应）
# 标注 type 而非 type[DataSourceBase]：基类抽象属性会让 mypy 拒绝统一实例化
SOURCE_CLASSES: dict[str, type] = {
    "akshare": AkShareSource,
    "baostock": BaoStockSource,
    "eastmoney": EastMoneySource,
    "sina": SinaSource,
    "tencent": TencentSource,
}


def make_source(name: str):
    """按名称构造历史数据源实例

    Raises:
        KeyError: 未知数据源名（不再静默降级到别的源，避免张冠李戴）
    """
    try:
        return SOURCE_CLASSES[name]()
    except KeyError:
        raise KeyError(f"未知历史数据源: {name}（可用: {sorted(SOURCE_CLASSES)}）") from None
