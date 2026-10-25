"""大盘指数实时 —— 自 tui/app.py:_fetch_indices 上提（name 取 fields[0]，带错误原因）

多源切换：新浪直连主源，失败自动切腾讯 qt.gtimg.cn 备源（用户约束：多数据源失效自动切换）。
"""

INDEX_CODES = ("sh000001", "sz399001", "sz399006")
# 备用中文名（新浪返回的 fields[0] 已是名称，仅兜底）
INDEX_LABELS = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指"}


def _to_item(code: str, name: str, current: float, yesterday: float | None) -> dict:
    # 昨收缺失/非法时涨跌未知置 null——前端对 null 显示 --，0.0 会伪装成平盘
    if yesterday is not None and yesterday > 0:
        change_amt = round(current - yesterday, 2)
        change_pct = round((current - yesterday) / yesterday * 100, 2)
    else:
        change_amt = None
        change_pct = None
    return {
        "code": code,
        "name": name,
        "price": current,
        "change_amt": change_amt,
        "change_pct": change_pct,
    }


def _check_complete(indices: list[dict], empty_msg: str = "指数行情解析结果为空（可能休市或接口返回异常）") -> tuple[list[dict], str | None]:
    """三码完整性校验：缺任一指数按失败处理，触发备源——部分缺失不得冒充成功"""
    if not indices:
        return [], empty_msg
    missing = [c for c in INDEX_CODES if c not in {i["code"] for i in indices}]
    if missing:
        return [], f"指数行情解析不完整（缺 {','.join(missing)}）"
    return indices, None


def fetch_indices_sina() -> tuple[list[dict], str | None]:
    """新浪直连获取三大指数实时报价。同步阻塞（curl_cffi HTTP）。"""
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as e:
        return [], f"缺少 curl_cffi 依赖: {e}"

    codes = list(INDEX_CODES)
    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
    # 新浪行情接口要求 Referer + GBK 解码 + chrome 伪装，普通 requests 会被拒
    headers = {"Referer": "https://finance.sina.com.cn"}

    try:
        session = curl_requests.Session(impersonate="chrome")
        resp = session.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace").strip()
    except Exception as e:
        return [], f"指数行情请求失败: {e}"

    indices = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or '=""' in line:
            continue
        try:
            var_part = line.split("=")[0]
            sina_code = var_part.split("_")[-1]
            data_str = line.split('"')[1]
            fields = data_str.split(",")

            # fields[0] 才是指数名称（fields[1] 是开盘价）
            name = fields[0] or INDEX_LABELS.get(sina_code, sina_code)
            # 现价缺失不得补 0.0（会伪出 -100%）：按解析失败剔除该行，由完整性校验兜底
            current = float(fields[3]) if fields[3] else None
            if current is None:
                continue
            yesterday = float(fields[2]) if fields[2] else None

            indices.append(_to_item(sina_code, name, current, yesterday))
        except (IndexError, ValueError):
            continue

    return _check_complete(indices)


def fetch_indices_tencent() -> tuple[list[dict], str | None]:
    """腾讯 qt.gtimg.cn 备源。返回形状与新浪路径一致。

    报文形如 v_sh000001="1~上证指数~000001~3250.50~3200.00~..."，
    关键字段位：[1]=名称 [3]=最新价 [4]=昨收（~ 分隔，指数段与个股段略有差异，
    仅取名称/现价/昨收三个稳定位）。
    """
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as e:
        return [], f"缺少 curl_cffi 依赖: {e}"

    codes = list(INDEX_CODES)
    url = f"https://qt.gtimg.cn/q={','.join(codes)}"
    try:
        session = curl_requests.Session(impersonate="chrome")
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace").strip()
    except Exception as e:
        return [], f"腾讯指数行情请求失败: {e}"

    indices = []
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        try:
            var_part = line.split("=")[0]
            code = var_part.split("_")[-1]
            data_str = line.split('"')[1]
            fields = data_str.split("~")
            name = fields[1] or INDEX_LABELS.get(code, code)
            current = float(fields[3]) if fields[3] else None
            if current is None:
                continue
            yesterday = float(fields[4]) if fields[4] else None
            indices.append(_to_item(code, name, current, yesterday))
        except (IndexError, ValueError):
            continue

    return _check_complete(indices, empty_msg="腾讯指数行情解析结果为空（可能休市或接口返回异常）")


def fetch_indices_with_source() -> tuple[list[dict], str | None, str | None]:
    """新浪 → 腾讯自动切换。返回 (indices, source, error)。"""
    items, err = fetch_indices_sina()
    if items:
        return items, "sina", None
    items2, err2 = fetch_indices_tencent()
    if items2:
        return items2, "tencent", None
    return [], None, f"新浪: {err}；腾讯: {err2}"


def fetch_indices() -> tuple[list[dict], str | None]:
    """三大指数实时（新浪主源、腾讯备源自动切换）。

    返回 (indices, error)：成功 error=None；失败 error=中文原因且 indices=[]。
    同步阻塞（curl_cffi HTTP），调用方须包 asyncio.to_thread。
    """
    items, _source, err = fetch_indices_with_source()
    return items, err
