"""LLM 可视化配置 —— api_key 掩码 GET、深合并原子写、探活不落盘"""

import asyncio

from fastapi import APIRouter, Request

from web.api.common import enc
from web.service import settings_store
from web.service.settings_store import (
    SettingsError,
    get_settings_view,
    load_setting_file,
    test_llm_connection,
    update_settings,
)

router = APIRouter()

TEST_TIMEOUT = 10.0


@router.get("/api/settings")
async def settings_get(request: Request):
    """全量配置（api_key 掩码，绝不返回明文）"""
    raw = await asyncio.to_thread(load_setting_file)
    return enc(get_settings_view(raw))


@router.put("/api/settings")
async def settings_put(request: Request):
    """局部字段深合并写回：整读 → 深合并 → 校验 → 原子写 → 写后钩子"""
    try:
        patch = await request.json()
    except Exception:
        return _err("请求体必须是 JSON")
    if not isinstance(patch, dict):
        return _err("请求体必须是 JSON 对象")

    app = request.app
    try:
        merged, changed = await asyncio.to_thread(update_settings, patch)
    except SettingsError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"配置写入失败: {e}", status_code=500)

    # 写后钩子
    if "data" in changed:
        try:
            from deeppulse import config as _config

            # 写哪个文件就重载哪个文件（SETTING_PATH 可被测试 monkeypatch，不得硬编码工程根）
            await asyncio.to_thread(_config.reload_data_settings, settings_store.SETTING_PATH)
        except Exception:
            pass
    if "llm" in changed or "agent" in changed:
        # llm/agent 变更 → 销毁缓存 agent（下一问按新配置重建）
        app.state.agent_pool.invalidate_all()
    app.state.setting = merged
    # 重建 agent 时要用新配置（pool 持有自己的 setting 引用）
    app.state.agent_pool.setting = merged

    return enc(get_settings_view(merged))


@router.post("/api/settings/test")
async def settings_test(request: Request):
    """轻量探活：独立构造 LLMClient ping 一次，整体 ~10s 超时（不建 StockAgent、不落盘）"""
    try:
        override = await request.json()
    except Exception:
        override = None
    if override is not None and not isinstance(override, dict):
        return _err("请求体必须是 JSON 对象")

    raw = await asyncio.to_thread(load_setting_file)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(test_llm_connection, raw, override),
            timeout=TEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {"status": "error", "message": f"连接测试超时（{int(TEST_TIMEOUT)}秒）"}
    return enc(result)


def _err(detail: str, status_code: int = 400):
    from web.api.common import error_response

    return error_response(detail, status_code=status_code)
