"""WebSocket 实时行情推送"""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/realtime")
async def realtime_ws(websocket: WebSocket):
    """实时行情 WebSocket

    Client → Server:
        {"type": "subscribe", "codes": ["600519", "000001"]}
        {"type": "unsubscribe", "codes": ["600519"]}

    Server → Client:
        {"type": "quotes", "data": [{"code": "600519", "price": 1685.0, ...}]}
        {"type": "error", "message": "..."}
    """
    await websocket.accept()
    subscribed_codes: set[str] = set()

    try:
        # 启动行情推送任务
        push_task = asyncio.create_task(_push_quotes(websocket, subscribed_codes))

        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            if data["type"] == "subscribe":
                codes = data.get("codes", [])
                subscribed_codes.update(codes)
                await websocket.send_json({
                    "type": "subscribed",
                    "codes": list(subscribed_codes),
                })

            elif data["type"] == "unsubscribe":
                codes = data.get("codes", [])
                subscribed_codes -= set(codes)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if "push_task" in locals():
            push_task.cancel()


async def _push_quotes(websocket: WebSocket, subscribed_codes: set[str]):
    """定时推送行情数据"""
    from deeppulse.src.realtime import RealtimeQuoteManager

    manager = RealtimeQuoteManager()

    while True:
        await asyncio.sleep(30)

        if not subscribed_codes:
            continue

        try:
            quotes = manager.get_realtime(list(subscribed_codes))
            await websocket.send_json({
                "type": "quotes",
                "data": [
                    {
                        "code": q.code,
                        "name": q.name,
                        "price": q.price,
                        "change": q.change,
                        "change_pct": q.change_pct,
                        "volume": q.volume,
                        "amount": q.amount,
                    }
                    for q in quotes
                ],
            })
        except Exception as e:
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                break
