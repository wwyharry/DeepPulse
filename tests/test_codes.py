"""codes 模块测试：板块分类 / 涨跌幅限制 / 符号映射"""

import pytest

from deeppulse.src import codes


class TestClassifyBoard:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("688001", ("sh", "star")),  # 科创板
            ("689009", ("sh", "star")),  # 科创板 CDR
            ("600000", ("sh", "main")),  # 沪主板
            ("601398", ("sh", "main")),
            ("603000", ("sh", "main")),
            ("605117", ("sh", "main")),
            ("300750", ("sz", "chinext")),  # 创业板
            ("301236", ("sz", "chinext")),  # 创业板注册制
            ("302001", ("sz", "chinext")),
            ("000001", ("sz", "main")),  # 深主板
            ("002594", ("sz", "main")),
            ("430047", ("bj", "bse")),  # 北交所
            ("830799", ("bj", "bse")),
            ("872190", ("bj", "bse")),
            ("920000", ("bj", "bse")),  # 北交所新代码段
        ],
    )
    def test_classify_known_boards(self, code, expected):
        assert codes.classify_board(code) == expected

    @pytest.mark.parametrize(
        "code",
        [
            "900901",  # B股（沪）
            "200011",  # B股（深）
            "510300",  # 基金
            "113050",  # 转债
            "400001",  # 老三板
            "420001",  # 老三板
            "899050",  # 北证50 指数段
            "sh000001",  # 带前缀全码（指数）不参与个股分类
            "6000001",  # 超长
            "abc123",  # 非数字
        ],
    )
    def test_classify_rejects_non_a_share(self, code):
        assert codes.classify_board(code) is None

    def test_short_code_zfill(self):
        assert codes.classify_board("1") == ("sz", "main")  # zfill(6) → 000001
        assert codes.classify_board("830799") == ("bj", "bse")

    def test_board_name(self):
        assert codes.board_name("star") == "科创板"
        assert codes.board_name("bse") == "北交所"


class TestPriceLimitPct:
    @pytest.mark.parametrize(
        ("code", "name", "expected"),
        [
            ("600000", "浦发银行", 10.0),
            ("600000", "ST某某", 5.0),
            ("600000", "st某某", 5.0),  # 小写 ST
            ("600000", "*ST某某", 5.0),
            ("300750", "宁德时代", 20.0),
            ("300750", "ST某某", 20.0),  # 创业板 ST 同 20%
            ("688001", "华兴源创", 20.0),
            ("688001", "ST某某", 20.0),  # 科创板 ST 同 20%
            ("920000", "北证股", 30.0),
        ],
    )
    def test_limits(self, code, name, expected):
        assert codes.price_limit_pct(code, name) == expected

    def test_limit_without_name(self):
        assert codes.price_limit_pct("000001") == 10.0  # 不传名默认非 ST

    def test_limit_unknown_code_raises(self):
        with pytest.raises(ValueError):
            codes.price_limit_pct("900901")


class TestSymbolMapping:
    def test_sina_and_tencent_symbols(self):
        assert codes.to_sina_symbol("600000") == "sh600000"
        assert codes.to_sina_symbol("000001") == "sz000001"
        assert codes.to_sina_symbol("300750") == "sz300750"
        assert codes.to_sina_symbol("688001") == "sh688001"
        assert codes.to_sina_symbol("920000") == "bj920000"
        assert codes.to_tencent_symbol("430047") == "bj430047"

    def test_em_secid(self):
        assert codes.to_em_secid("600000") == "1.600000"
        assert codes.to_em_secid("000001") == "0.000001"
        assert codes.to_em_secid("300750") == "0.300750"
        assert codes.to_em_secid("688001") == "1.688001"
        assert codes.to_em_secid("920000") == "0.920000"  # 北交所同深市市场号

    def test_bs_code(self):
        assert codes.to_bs_code("600000") == "sh.600000"
        assert codes.to_bs_code("000001") == "sz.000001"
        assert codes.to_bs_code("300750") == "sz.300750"
        assert codes.to_bs_code("920000") is None  # BaoStock 不支持北交所

    def test_unknown_code_raises(self):
        with pytest.raises(ValueError):
            codes.to_sina_symbol("900901")
        with pytest.raises(ValueError):
            codes.market_of("900901")

    def test_market_of(self):
        assert codes.market_of("600000") == "sh"
        assert codes.market_of("000001") == "sz"
        assert codes.market_of("920000") == "bj"
