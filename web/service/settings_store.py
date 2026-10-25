"""setting.json 安全读写 —— 整读深合并原子写、api_key 掩码、中文校验报错"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

# 与 deeppulse/agent/client.py 同口径：项目根绝对路径（测试 monkeypatch 本常量）
SETTING_PATH = Path(__file__).resolve().parents[2] / "setting.json"

# data.default_source 合法源白名单（与 deeppulse.config.DATA_SOURCES 一致）
_VALID_SOURCES = ("baostock", "akshare", "eastmoney", "tencent", "sina")
_VALID_PROTOCOLS = ("openai", "anthropic")

# 硬键段：构造 StockAgent/LLMClient 时硬索引，必须是 JSON 对象
_HARD_SECTIONS = ("llm", "embedding", "agent", "data")

# 进程级写锁：把 load→merge→save 整段 RMW 串行化（asyncio.to_thread 并发 PUT 会落线程池）
_SETTINGS_LOCK = threading.Lock()


class SettingsError(ValueError):
    """配置校验/读写错误（message 为中文，直接作为 HTTP detail 返回）"""


def _is_placeholder_api_key(key_val) -> bool:
    """占位 key 判定（与 deeppulse/agent/client.py 口径一致）"""
    if key_val is None:
        return True
    s = str(key_val)
    if not s.strip():
        return True
    if s.startswith("${") and s.endswith("}") and not s[2:-1].strip():
        return True
    return s.startswith("sk-your")


def load_setting_file(path: Path | None = None, strict: bool = False) -> dict:
    """读取 setting.json（宽松模式：缺失/坏 JSON 返回 {}；strict 模式抛 SettingsError）"""
    p = Path(path or SETTING_PATH)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        if strict:
            raise SettingsError(f"配置文件不存在: {p}") from None
        return {}
    except (json.JSONDecodeError, OSError) as e:
        if strict:
            raise SettingsError(f"配置文件损坏或不可读: {p}（{e}）") from None
        return {}


def save_setting_file(data: dict, path: Path | None = None) -> None:
    """原子写 setting.json（唯一临时文件 + flush/fsync + rename，indent=2，ensure_ascii=False）。

    临时文件名带 pid + uuid 后缀：并发写各用各的 tmp，互不截断；
    fsync 后再 os.replace，避免 rename 落在半截内容上。
    """
    p = Path(path or SETTING_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def deep_merge(base: dict, patch: dict) -> dict:
    """深合并 patch 到 base 的副本（嵌套 dict 递归合并，其余类型整体覆盖）"""
    out = dict(base or {})
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def mask_api_key(key_val) -> tuple[str, str]:
    """api_key → (掩码, key_form)。key_form: raw | env_ref | placeholder。

    绝不返回明文；掩码形如 sk-***abcd（保留尾 4 位）。
    """
    if _is_placeholder_api_key(key_val):
        return "", "placeholder"
    s = str(key_val)
    if s.startswith("${") and s.endswith("}"):
        inner = s[2:-1].strip()
        tail = inner[-4:] if len(inner) >= 4 else ""
        return f"***{tail}", "env_ref"
    tail = s[-4:] if len(s) >= 8 else "***"
    if s.startswith("sk-"):
        return f"sk-***{tail}", "raw"
    return f"***{tail}", "raw"


def _section(raw: dict, name: str) -> dict:
    """取配置段；非 dict（null/历史坏值）一律当空段，绝不让响应组装 500"""
    val = (raw or {}).get(name)
    return val if isinstance(val, dict) else {}


def get_settings_view(raw: dict) -> dict:
    """组装 GET /api/settings 响应形状（绝不含明文 api_key）"""
    llm = _section(raw, "llm")
    emb = _section(raw, "embedding")
    agent = _section(raw, "agent")
    data = _section(raw, "data")

    llm_masked, llm_form = mask_api_key(llm.get("api_key"))
    emb_masked, _ = mask_api_key(emb.get("api_key"))

    return {
        "llm": {
            "provider": llm.get("provider"),
            "base_url": llm.get("base_url"),
            "api_key_masked": llm_masked,
            "key_form": llm_form,
            "model": llm.get("model"),
            "protocol": llm.get("protocol"),
            "max_tokens": llm.get("max_tokens"),
            "temperature": llm.get("temperature"),
            "timeout": llm.get("timeout"),
        },
        "embedding": {
            "provider": emb.get("provider"),
            "model": emb.get("model"),
            "api_key_masked": emb_masked,
            "base_url": emb.get("base_url"),
        },
        "agent": {
            "max_rounds": agent.get("max_rounds"),
            "max_rounds_extension": agent.get("max_rounds_extension"),
            "verbose": agent.get("verbose"),
            "enable_judge": agent.get("enable_judge"),
        },
        "data": {
            "online_fallback": data.get("online_fallback"),
            "cache_to_db": data.get("cache_to_db"),
            "default_source": data.get("default_source"),
            "db_path": data.get("db_path"),
        },
    }


def _validate_llm(llm: dict, patch_llm: dict | None) -> None:
    """校验 llm 段（message 一律中文）"""
    # patch 显式给了 protocol 键：null/空 = 拒绝（会把 llm.protocol 硬键写坏）
    if patch_llm is not None and "protocol" in patch_llm:
        pin = patch_llm.get("protocol")
        if pin is None or (isinstance(pin, str) and not pin.strip()):
            raise SettingsError("protocol 必须是 openai 或 anthropic（不能为 null 或空）")

    proto = llm.get("protocol")
    if proto is not None and proto not in _VALID_PROTOCOLS:
        raise SettingsError("protocol 必须是 openai 或 anthropic")

    # anthropic 官方 SDK 不接受 base_url：仅在前端动了 protocol/base_url 时强校验
    if patch_llm is not None and ("protocol" in patch_llm or "base_url" in patch_llm):
        if proto == "anthropic" and str(llm.get("base_url") or "").strip():
            raise SettingsError("Anthropic 官方不支持自定义 base_url，请清空 base_url 后再切换协议")

    if "max_tokens" in llm and llm.get("max_tokens") is not None:
        mv = llm.get("max_tokens")
        if isinstance(mv, bool) or not isinstance(mv, (int, float)) or int(mv) <= 0:
            raise SettingsError("max_tokens 必须是正整数")

    if "temperature" in llm and llm.get("temperature") is not None:
        tv = llm.get("temperature")
        if isinstance(tv, bool) or not isinstance(tv, (int, float)):
            raise SettingsError("temperature 必须是数字")

    if "timeout" in llm and llm.get("timeout") is not None:
        t = llm.get("timeout")
        if isinstance(t, bool) or not isinstance(t, (int, float)) or float(t) < 0:
            raise SettingsError("timeout 必须是非负数字")

    # 新写入的 api_key 拒绝占位值（留空/不传 = 不变，不在此校验）
    if patch_llm is not None and "api_key" in patch_llm:
        key = patch_llm.get("api_key")
        if key is not None and str(key).strip() and _is_placeholder_api_key(key):
            raise SettingsError(
                'API Key 无效（空值/空白/"${}"占位/"sk-your"示例值）。请直接填入真实 key，或引用环境变量 "${YOUR_ENV_VAR}"'
            )


def _validate_agent(agent: dict, patch_agent: dict | None = None) -> None:
    """校验 agent 段硬键类型（构造 StockAgent 时是硬索引）。

    patch 显式提供的键一律严校验（含 null）；patch 未动的键允许盘上遗留 null 不拦无关写。
    """
    provided = patch_agent or {}
    if "max_rounds" in agent or "max_rounds" in provided:
        v = agent.get("max_rounds")
        if v is None and "max_rounds" not in provided:
            pass  # 盘上遗留 null 且本次未动该键：不拦
        elif isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise SettingsError("max_rounds 必须是正整数（不能为 null/布尔/小数）")
    if "max_rounds_extension" in agent or "max_rounds_extension" in provided:
        v = agent.get("max_rounds_extension")
        if v is None and "max_rounds_extension" not in provided:
            pass  # 盘上遗留 null 且本次未动该键：不拦
        elif isinstance(v, bool) or not isinstance(v, int) or v < 0:
            # 与 max_rounds 不同：0 合法（关闭附加额度，硬上限 = 基础轮次）
            raise SettingsError("max_rounds_extension 必须是非负整数（0=关闭附加额度，不能为 null/布尔/小数）")
    for key in ("verbose", "enable_judge"):
        if key not in agent and key not in provided:
            continue
        v = agent.get(key)
        if v is None and key not in provided:
            continue  # 同上：盘上遗留 null 且本次未动
        if not isinstance(v, bool):
            raise SettingsError(f"{key} 必须是布尔值（不能为 null）")


def _validate_data(data: dict) -> None:
    """校验 data 段（default_source 白名单）"""
    src = data.get("default_source", None)
    if src is not None and src not in _VALID_SOURCES:
        raise SettingsError(f"default_source 必须是 {'/'.join(_VALID_SOURCES)} 之一")


def _ensure_dict_sections(cfg: dict) -> None:
    """llm/embedding/agent/data 硬段必须是 JSON 对象。

    非 dict（null/字符串/数字/数组）会把构造路径写坏：字符串让 `in` 变子串判断恒放行、
    None 被 `or {}` 洗掉后落盘、数字直接 AttributeError 500。一律 400 中文拒绝。
    """
    for section in _HARD_SECTIONS:
        if section in cfg and not isinstance(cfg[section], dict):
            raise SettingsError(f"{section} 段必须是 JSON 对象（不能是 null/字符串/数字/数组）")


def _is_masked_api_key_value(val, current_key) -> bool:
    """掩码形态判定：含 *** 或等于当前 key 的 mask_api_key() 输出（GET→PUT 回写防护）"""
    s = str(val)
    if "***" in s:
        return True
    masked, _ = mask_api_key(current_key)
    return bool(masked) and s == masked


def validate_settings(raw: dict, patch: dict) -> None:
    """按合并后的全量配置校验（patch 用于局部语义判定）"""
    llm = _section(raw, "llm")
    _validate_llm(llm, patch.get("llm") if isinstance(patch.get("llm"), dict) else None)
    _validate_agent(_section(raw, "agent"), patch.get("agent") if isinstance(patch.get("agent"), dict) else None)
    _validate_data(_section(raw, "data"))


def update_settings(patch: dict) -> tuple[dict, set]:
    """整读 → 深合并 → 校验 → 原子写。返回 (合并后全量配置, 变更的顶层段名集合)。

    api_key 缺失/空串 = 不变（防掩码回写污染）；掩码形态值直接 400 拒绝；
    其余未知键原样保留。整段 RMW 在进程级锁内串行化（并发 PUT 不丢更新、不写坏文件）。
    """
    if not isinstance(patch, dict):
        raise SettingsError("请求体必须是 JSON 对象")
    _ensure_dict_sections(patch)  # 硬段类型先挡：null/字符串/数字不得进合并
    with _SETTINGS_LOCK:
        raw = load_setting_file(strict=True)
        patch = dict(patch)

        # 掩码形态的 api_key 绝不当真实 key 落盘：400 可见误用（请填完整 key 或留空保持不变）
        for section in ("llm", "embedding"):
            sec = patch.get(section)
            if isinstance(sec, dict) and "api_key" in sec:
                key = sec.get("api_key")
                if key is not None and str(key).strip():
                    cur = _section(raw, section).get("api_key")
                    if _is_masked_api_key_value(key, cur):
                        raise SettingsError("请填写完整 key 或留空保持不变（检测到掩码值，不会作为真实 API Key 保存）")

        # api_key 空串/不传 = 不变：先从 patch 摘掉空 key，再深合并
        for section in ("llm", "embedding"):
            sec = patch.get(section)
            if isinstance(sec, dict) and "api_key" in sec:
                if sec.get("api_key") is None or str(sec.get("api_key")).strip() == "":
                    sec = dict(sec)
                    sec.pop("api_key", None)
                    if sec:
                        patch[section] = sec
                    else:
                        patch.pop(section, None)

        # api_key_masked 只读，绝不落盘
        for section in ("llm", "embedding"):
            sec = patch.get(section)
            if isinstance(sec, dict) and "api_key_masked" in sec:
                sec = dict(sec)
                sec.pop("api_key_masked", None)
                if sec:
                    patch[section] = sec
                else:
                    patch.pop(section, None)

        merged = deep_merge(raw, patch)
        _ensure_dict_sections(merged)  # 盘上坏段未被本次 patch 修好 → 拒绝，不把坏值再写回
        validate_settings(merged, patch)

        changed = {k for k in patch.keys() if merged.get(k) != raw.get(k)}
        save_setting_file(merged)
        return merged, changed


def build_llm_setting(raw: dict, override: dict | None = None) -> dict:
    """构造 LLMClient 用的 setting 副本（可叠加临时覆盖，不落盘）。

    api_key 空串/None = 不变（与 update_settings 同口径）；${ENV_VAR} 形式在构造前展开。
    """
    base = dict(raw or {})
    if override:
        override = dict(override)
        for section in ("llm", "embedding"):
            sec = override.get(section)
            if isinstance(sec, dict):
                sec = dict(sec)
                sec.pop("api_key_masked", None)
                # 空串/None = 保持磁盘值不变，防止掩码/空值覆盖真实 key
                if "api_key" in sec and (sec.get("api_key") is None or str(sec.get("api_key")).strip() == ""):
                    sec.pop("api_key", None)
                if sec:
                    override[section] = sec
                else:
                    override.pop(section, None)
        base = deep_merge(base, override)

    # ${ENV_VAR} 展开（与 load_setting / agent_pool 同口径）；llm 严格、embedding 宽松
    llm = base.get("llm")
    if isinstance(llm, dict) and llm.get("api_key") is not None:
        from deeppulse.agent.client import resolve_api_key

        llm["api_key"] = resolve_api_key(llm["api_key"])
    emb = base.get("embedding")
    if isinstance(emb, dict) and isinstance(emb.get("api_key"), str):
        v = emb["api_key"].strip()
        if v.startswith("${") and v.endswith("}") and v[2:-1].strip():
            try:
                from deeppulse.agent.client import resolve_api_key

                emb["api_key"] = resolve_api_key(v)
            except ValueError:
                pass
    return base


def test_llm_connection(raw: dict, override: dict | None = None) -> dict:
    """轻量探活：独立构造 LLMClient 并 ping 一次（不建 StockAgent）。同步阻塞，须在 to_thread 里跑。"""
    try:
        setting = build_llm_setting(raw, override)
        from deeppulse.agent.client import LLMClient

        t0 = time.time()
        client = LLMClient(setting)
        client.chat([{"role": "user", "content": "ping"}])
        return {"status": "ok", "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"status": "error", "message": str(e) or e.__class__.__name__}
