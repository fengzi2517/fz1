"""群聊相关 API（M2）"""
import asyncio
import json
from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse

from app.models.schemas import GroupCreate, GroupChatRequest, GroupProfile
from app.services.group_service import (
    group_service,
    group_chat_service,
    group_distill_service,
)

router = APIRouter(prefix="/api/groups", tags=["groups"])


@router.post("", response_model=GroupProfile)
async def create_group(request: GroupCreate):
    """创建群聊"""
    try:
        return await group_service.create_group(
            request.user_id, request.name, request.character_ids
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=List[GroupProfile])
async def list_groups(user_id: str):
    """获取用户的群聊列表"""
    return await group_service.list_groups(user_id)


@router.get("/{group_id}", response_model=GroupProfile)
async def get_group(user_id: str, group_id: str):
    group = await group_service.get_group(user_id, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="群聊不存在")
    return group


@router.delete("/{group_id}")
async def delete_group(user_id: str, group_id: str):
    success = await group_service.delete_group(user_id, group_id)
    return {"success": success}


@router.get("/{group_id}/history")
async def get_group_history(user_id: str, group_id: str, limit: int = 50):
    """获取群聊历史消息"""
    messages = await group_service.get_history(user_id, group_id, limit=limit)
    return {"messages": messages}


@router.post("/{group_id}/end")
async def end_group_chat(
    group_id: str,
    user_id: str,
    background_tasks: BackgroundTasks,
):
    """结束群聊，触发按角色视角的记忆蒸馏"""
    background_tasks.add_task(
        group_distill_service.distill_group, user_id, group_id
    )
    return {"status": "distilling", "group_id": group_id}


@router.post("/{group_id}/chat")
async def group_chat(group_id: str, request: GroupChatRequest):
    """群聊发言：成员依次流式回复"""
    async def event_generator():
        try:
            async for event in group_chat_service.chat_stream(
                request.user_id,
                group_id,
                request.message,
                reply_mode=request.reply_mode,
                target_character_id=request.target_character_id,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            data = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/{group_id}/ws")
async def group_chat_ws(websocket: WebSocket, group_id: str, user_id: str):
    """
    群聊 WebSocket：与 SSE 事件协议一致，额外支持用户中途插话。

    客户端发送：
      {"type": "message", "content": "...", "reply_mode": "round_robin|target",
       "target_character_id": "c_xxx"}
      {"type": "ping"}
    服务端推送：start / token / done / interrupted / error / end / pong
    """
    await websocket.accept()
    send_lock = asyncio.Lock()
    state: dict = {"task": None, "interrupt": asyncio.Event()}

    async def send(event: dict):
        async with send_lock:
            await websocket.send_json(event)

    async def run_round(message: str, reply_mode: str, target: Optional[str]):
        try:
            async for event in group_chat_service.chat_stream(
                user_id,
                group_id,
                message,
                reply_mode=reply_mode,
                target_character_id=target,
                interrupt_event=state["interrupt"],
            ):
                await send(event)
        except Exception as e:
            await send({"type": "error", "message": f"群聊生成失败: {e}"})

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "ping":
                await send({"type": "pong"})
                continue
            if msg_type != "message":
                await send({"type": "error", "message": "未知的消息类型"})
                continue

            content = (data.get("content") or "").strip()
            if not content:
                continue

            running = state["task"]
            if running is not None and not running.done():
                # 插话：先让当前轮尽快收尾，再处理这条新消息
                state["interrupt"].set()
                await running

            state["interrupt"] = asyncio.Event()
            state["task"] = asyncio.create_task(
                run_round(
                    content,
                    data.get("reply_mode", "round_robin"),
                    data.get("target_character_id"),
                )
            )
    except WebSocketDisconnect:
        pass
    finally:
        task = state["task"]
        if task is not None and not task.done():
            state["interrupt"].set()
            task.cancel()
