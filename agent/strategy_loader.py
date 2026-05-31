"""短线战法加载器 - 从 strategies/ 目录加载 Markdown 战法文件，支持关键词检索"""

import re
from pathlib import Path

# 战法目录
STRATEGIES_DIR = Path(__file__).parent / "strategies"


class StrategyLoader:
    """短线战法加载与检索

    功能：
    - 启动时扫描 strategies/ 目录下所有 .md 文件
    - 解析每个战法的标题、标签、关键条件
    - 支持按关键词/技术指标/市场信号检索匹配的战法
    """

    def __init__(self, strategies_dir: Path = None):
        self.dir = strategies_dir or STRATEGIES_DIR
        self.strategies = []  # [{name, file, content, keywords, tags}]
        self._load_all()

    def _load_all(self):
        """扫描并加载所有 .md 战法文件"""
        self.strategies = []
        if not self.dir.exists():
            return

        for f in sorted(self.dir.glob("*.md")):
            if f.name == "README.md":
                continue
            try:
                content = f.read_text(encoding="utf-8").strip()
                if not content:
                    continue
                strategy = self._parse_strategy(f, content)
                self.strategies.append(strategy)
            except Exception as e:
                print(f"加载战法文件 {f.name} 失败: {e}")

    def _parse_strategy(self, filepath: Path, content: str) -> dict:
        """解析单个战法文件，提取标题、关键词、标签"""
        # 提取标题（第一个 # 开头的行）
        title_match = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        name = title_match.group(1).strip() if title_match else filepath.stem

        # 提取关键词：技术指标 + 中文关键术语
        keywords = set()

        # 技术指标关键词
        tech_patterns = [
            r"MA\d+",
            r"MACD",
            r"RSI",
            r"KDJ",
            r"BOLL",
            r"布林",
            r"金叉",
            r"死叉",
            r"超买",
            r"超卖",
            r"放量",
            r"缩量",
            r"突破",
            r"回踩",
            r"支撑",
            r"压力",
            r"均线",
            r"背离",
            r"涨停",
            r"跌停",
            r"封板",
            r"炸板",
            r"换手",
        ]
        for pattern in tech_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                keywords.add(pattern)

        # 提取 ## 标题作为标签
        tags = []
        for m in re.finditer(r"^##\s+(.+)", content, re.MULTILINE):
            tag = m.group(1).strip()
            # 去掉序号
            tag = re.sub(r"^[\d.]+\s*", "", tag)
            if tag:
                tags.append(tag)

        # 提取数值型关键条件（如 RSI<20, MA5>MA10）
        numeric_conditions = re.findall(r"(?:MA|RSI|KDJ|MACD|BOLL|J|K|D|DIF|DEA)\s*[<>]=?\s*[\d.]+", content)
        keywords.update(numeric_conditions[:10])

        return {
            "name": name,
            "file": filepath.name,
            "content": content,
            "keywords": list(keywords),
            "tags": tags,
        }

    def search(self, query: str, top_k: int = 3) -> list:
        """根据查询关键词检索匹配的战法

        Args:
            query: 查询文本（股票分析上下文、技术指标状态等）
            top_k: 返回条数

        Returns:
            [{name, file, score, match_reason, content_preview}]
        """
        if not self.strategies:
            return []

        # 从查询中提取匹配词
        query_terms = set()

        # 技术指标
        for pattern in [
            r"MA\d+",
            r"MACD",
            r"RSI",
            r"KDJ",
            r"BOLL",
            r"布林",
            r"金叉",
            r"死叉",
            r"超买",
            r"超卖",
            r"放量",
            r"缩量",
            r"突破",
            r"回踩",
            r"支撑",
            r"压力",
            r"均线",
            r"背离",
            r"涨停",
            r"跌停",
        ]:
            if re.search(pattern, query, re.IGNORECASE):
                query_terms.add(pattern)

        # 数值条件
        numeric = re.findall(r"(?:MA|RSI|KDJ|MACD|BOLL|J|K|D)\s*[<>]=?\s*[\d.]+", query)
        query_terms.update(numeric)

        # 中文关键术语（2-4字）
        cn_terms = re.findall(r"[一-鿿]{2,4}", query)
        query_terms.update(cn_terms)

        if not query_terms:
            # 无明确关键词时，返回所有战法的简要列表
            return [
                {
                    "name": s["name"],
                    "file": s["file"],
                    "score": 0.1,
                    "match_reason": "无明确匹配",
                    "content_preview": s["content"][:200],
                }
                for s in self.strategies[:top_k]
            ]

        # 评分
        scored = []
        for strategy in self.strategies:
            score = 0
            matched = []

            # 关键词匹配
            for kw in strategy["keywords"]:
                for qt in query_terms:
                    if qt in kw or kw in qt:
                        score += 1
                        matched.append(kw)
                        break

            # 标签匹配
            for tag in strategy["tags"]:
                for qt in query_terms:
                    if qt in tag:
                        score += 0.5
                        matched.append(tag)
                        break

            # 内容全文匹配（权重较低）
            for qt in query_terms:
                if len(qt) >= 2 and qt in strategy["content"]:
                    score += 0.2

            if score > 0:
                scored.append(
                    {
                        "name": strategy["name"],
                        "file": strategy["file"],
                        "score": round(score, 2),
                        "match_reason": "匹配: " + ", ".join(matched[:5]),
                        "content_preview": strategy["content"][:300],
                    }
                )

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def get_strategy_content(self, filename: str) -> str:
        """获取指定战法文件的完整内容"""
        for s in self.strategies:
            if s["file"] == filename:
                return s["content"]
        return ""

    def list_strategies(self) -> list:
        """列出所有已加载的战法"""
        return [
            {
                "name": s["name"],
                "file": s["file"],
                "tags": s["tags"],
                "keywords": s["keywords"][:5],
            }
            for s in self.strategies
        ]

    def get_all_content_summary(self, max_chars: int = 3000) -> str:
        """获取所有战法的摘要，用于注入 system prompt"""
        if not self.strategies:
            return ""

        lines = []
        total = 0
        for s in self.strategies:
            # 取标题和前几行内容
            preview = s["content"][:200].replace("\n", " ")
            line = f"- **{s['name']}** ({s['file']}): {preview}..."
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line) + 1

        return "\n".join(lines)


# 全局实例（惰性加载）
_loader = None


def get_strategy_loader() -> StrategyLoader:
    """获取全局 StrategyLoader 实例（单例）"""
    global _loader
    if _loader is None:
        _loader = StrategyLoader()
    return _loader


def reload_strategies():
    """重新加载战法文件（新增/修改后调用）"""
    global _loader
    _loader = StrategyLoader()
    return _loader
