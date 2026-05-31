"""热点新闻搜索 - 纯爬虫方案，无外部 API 依赖

数据源:
- 百度新闻: 综合财经新闻聚合
- 东方财富: A股快讯/要闻
- 新浪财经: 财经热点
"""
import re
import json
import time
from datetime import datetime
from urllib.parse import quote

from curl_cffi import requests
from bs4 import BeautifulSoup

# 通用请求头
_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

_TIMEOUT = 15


def search_baidu_news(keyword: str, num: int = 10) -> list[dict]:
    """百度新闻搜索

    Args:
        keyword: 搜索关键词（股票名称、代码、或任意财经关键词）
        num: 返回条数，默认10

    Returns:
        [{"title": str, "source": str, "time": str, "summary": str, "url": str}]
    """
    url = f"https://www.baidu.com/s?tn=news&rtt=1&bsst=1&cl=2&wd={quote(keyword)}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT,
                            impersonate="chrome")
        resp.raise_for_status()
    except Exception as e:
        return [{"error": f"百度新闻请求失败: {e}"}]

    soup = BeautifulSoup(resp.text, "lxml")
    results = []

    for item in soup.select(".result, .c-container"):
        # 标题：尝试多种选择器
        title_tag = (item.select_one(".news-title a") or
                     item.select_one("h3 a") or
                     item.select_one(".c-title a") or
                     item.select_one("a[href]"))
        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        if not title or len(title) < 4:
            continue

        link = title_tag.get("href", "")

        # 来源和时间
        source_tag = item.select_one(".news-source, .c-color-gray, .source, .c-gap-top-xsmall")
        source_text = source_tag.get_text(strip=True) if source_tag else ""

        source_name = ""
        pub_time = ""
        if source_text:
            parts = re.split(r"\s+", source_text, maxsplit=1)
            source_name = parts[0] if parts else ""
            if len(parts) > 1:
                pub_time = parts[1]

        # 摘要
        summary_tag = item.select_one(".c-font-normal, .news-summary, .content-right, .c-abstract")
        summary = summary_tag.get_text(strip=True) if summary_tag else ""
        if not summary:
            # fallback: 取所有文本，去掉标题
            all_text = item.get_text(strip=True)
            summary = all_text[len(title):].strip()

        results.append({
            "title": title,
            "source": source_name or "百度新闻",
            "time": pub_time,
            "summary": summary[:200],
            "url": link,
        })

        if len(results) >= num:
            break

    return results


def search_eastmoney_news(keyword: str = "", num: int = 10) -> list[dict]:
    """东方财富快讯/要闻

    Args:
        keyword: 过滤关键词（可选，空则返回最新快讯）
        num: 返回条数

    Returns:
        [{"title": str, "time": str, "summary": str, "url": str}]
    """
    url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
    params = {
        "client": "web",
        "biz": "web_724",
        "column": "724",
        "order": "1",
        "needInteractData": "0",
        "page_index": "1",
        "page_size": str(num * 2),
        "req_trace": str(int(time.time() * 1000)),
    }

    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT,
                            impersonate="chrome")
        data = resp.json()
    except Exception as e:
        return [{"error": f"东方财富快讯请求失败: {e}"}]

    results = []
    items = (data.get("data") or {}).get("list", [])

    for item in items:
        title = item.get("title", "").strip()
        content = item.get("content", "").strip()
        digest = item.get("digest", "").strip()
        pub_time = item.get("showtime", "")

        # 关键词过滤
        text_to_search = f"{title} {content} {digest}"
        if keyword and keyword.lower() not in text_to_search.lower():
            continue

        # 清理 HTML 标签
        content_clean = re.sub(r"<[^>]+>", "", content or digest)

        results.append({
            "title": title or content_clean[:50],
            "source": "东方财富",
            "time": pub_time,
            "summary": content_clean[:200],
            "url": f"https://finance.eastmoney.com/a/{item.get('art_code', '')}.html"
                   if item.get("art_code") else "",
        })

        if len(results) >= num:
            break

    return results


def search_sina_finance(num: int = 10) -> list[dict]:
    """新浪财经要闻

    Returns:
        [{"title": str, "time": str, "summary": str, "url": str}]
    """
    url = "https://feed.mix.sina.com.cn/api/roll/get"
    params = {
        "pageid": "153",
        "lid": "2516",
        "k": "",
        "num": str(num),
        "page": "1",
        "r": str(time.time()),
    }

    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT,
                            impersonate="chrome")
        data = resp.json()
    except Exception as e:
        return [{"error": f"新浪财经请求失败: {e}"}]

    results = []
    items = data.get("result", {}).get("data", [])

    for item in items:
        title = item.get("title", "").strip()
        intro = item.get("intro", "").strip()
        ctime = item.get("ctime", "")
        url_link = item.get("url", "")

        # 时间格式化
        pub_time = ""
        if ctime:
            try:
                pub_time = datetime.fromtimestamp(int(ctime)).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pub_time = ctime

        results.append({
            "title": title,
            "source": "新浪财经",
            "time": pub_time,
            "summary": re.sub(r"<[^>]+>", "", intro)[:200],
            "url": url_link,
        })

    return results


def search_stock_news(code_or_name: str, num: int = 10) -> dict:
    """综合搜索某只股票的相关新闻

    Args:
        code_or_name: 股票代码或名称
        num: 返回条数

    Returns:
        {"query": str, "results": [...], "sources": [...]}
    """
    all_results = []
    sources_used = []

    # 百度新闻搜索
    baidu_results = search_baidu_news(f"{code_or_name} 股票", num=num)
    if baidu_results and "error" not in baidu_results[0]:
        for r in baidu_results:
            r["source"] = f"百度新闻/{r.get('source', '')}"
        all_results.extend(baidu_results)
        sources_used.append("百度新闻")

    # 东方财富快讯（关键词过滤）
    em_results = search_eastmoney_news(keyword=code_or_name, num=num)
    if em_results and "error" not in em_results[0]:
        all_results.extend(em_results)
        sources_used.append("东方财富")

    # 去重（按标题相似度）
    seen_titles = set()
    unique_results = []
    for r in all_results:
        title_key = r["title"][:20]
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_results.append(r)

    return {
        "query": code_or_name,
        "total": len(unique_results),
        "sources": sources_used,
        "results": unique_results[:num],
    }


def search_market_hot_news(num: int = 10) -> dict:
    """搜索A股市场热点新闻（综合多源）

    Args:
        num: 每个源返回条数

    Returns:
        {"sources": [...], "results": [...]}
    """
    all_results = []
    sources_used = []

    # 东方财富快讯
    em = search_eastmoney_news(num=num)
    if em and "error" not in em[0]:
        all_results.extend(em)
        sources_used.append("东方财富")

    # 新浪财经要闻
    sina = search_sina_finance(num=num)
    if sina and "error" not in sina[0]:
        all_results.extend(sina)
        sources_used.append("新浪财经")

    # 百度 A股热搜
    baidu = search_baidu_news("A股 今日", num=num)
    if baidu and "error" not in baidu[0]:
        for r in baidu:
            r["source"] = f"百度新闻/{r.get('source', '')}"
        all_results.extend(baidu)
        sources_used.append("百度新闻")

    # 按时间排序（有时间的排前面）
    def time_sort_key(r):
        t = r.get("time", "")
        if not t:
            return "0000"
        return t

    all_results.sort(key=time_sort_key, reverse=True)

    return {
        "total": len(all_results),
        "sources": sources_used,
        "results": all_results[:num * 2],
    }
