"""记忆系统常量定义"""

# 各记忆类型的默认衰减半衰期（天）
DEFAULT_HALF_LIFE = {
    "preference": 90,
    "insight": 30,
    "fact": 60,
    "context": 14,
    "summary": 45,
    "learning": 180,
}

# 股票代码提取：汉字属 \w，\b 在中文相邻时失效，必须用否定环视；
# 覆盖沪（6）、深（0/3）、北交所（4/8/920）全板块——manager/bm25/agent 三处共用，防再度漂移
STOCK_CODE_RE = r"(?<![0-9A-Za-z])[034689]\d{5}(?![0-9A-Za-z])"
