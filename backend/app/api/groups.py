"""群聊 API v2（P2）：CRUD + 成员/静音/公告/调度配置 + SSE 与 WebSocket（支持插话）"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.api.sse import sse_response
from app.config import AUTH_ENABLED
from app.core.security import UserDep, decode_token, validate_id
from app.models.schemas import GroupChatRequest, GroupCreate, GroupMemberIn, GroupMuteRequest
from app.services.distill_service import distill_service
from app.services.group_service import group_chat_service, group_service
from app.store import store

router = APIRouter(prefix="/api/groups", tags=["groups"])


class GroupUpdate(BaseModel):
    name: str = ""
    announcement: str = ""
    summary: Optional[str] = None
    scheduler: Optional[dict] = None


@router.post("")
async def create_group(request: GroupCreate, user_id: str = UserDep):
    try:
        return group_service.create_group(user_id, request.name, request.character_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("")
async def list_groups(user_id: str = UserDep):
    return group_service.list_groups(user_id)


@router.get("/{group_id}")
async def get_group(group_id: str, user_id: str = UserDep):
    group = group_service.get_group(user_id, group_id)
    if not group:
        raise HTTPException(404, "群聊不存在")
    return group


@router.put("/{group_id}")
async def update_group(group_id: str, request: GroupUpdate, user_id: str = UserDep):
    try:
        group_service._require(user_id, group_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    fields = {}
    if request.name:
        fields["name"] = request.name
    if request.announcement:
        fields["announcement"] = request.announcement
    if request.summary is not None:
        fields["summary"] = request.summary
    if request.scheduler is not None:
        fields["scheduler"] = request.scheduler
    if fields:
        store.update_group(user_id, group_id, **fields)
    return store.get_group(user_id, group_id)


@router.delete("/{group_id}")
async def delete_group(group_id: str, user_id: str = UserDep):
    return {"success": group_service.delete_group(user_id, group_id)}


@router.post("/{group_id}/members")
async def add_member(group_id: str, request: GroupMemberIn, user_id: str = UserDep):
    try:
        return group_service.add_member(user_id, group_id, request.character_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{group_id}/members/{character_id}")
async def remove_member(group_id: str, character_id: str, user_id: str = UserDep):
    try:
        return group_service.remove_member(user_id, group_id, character_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/{group_id}/mute")
async def set_muted(group_id: str, request: GroupMuteRequest, user_id: str = UserDep):
    try:
        return group_service.set_muted(user_id, group_id, request.muted_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{group_id}/history")
async def get_group_history(group_id: str, user_id: str = UserDep, limit: int = 200):
    group = group_service.get_group(user_id, group_id)
    if not group or not group.get("session_id"):
        raise HTTPException(404, "群聊不存在")
    return {"group_id": group_id, "session_id": group["session_id"],
            "messages": store.get_messages(group["session_id"], limit=limit)}


@router.post("/{group_id}/chat")
async def group_chat(group_id: str, request: GroupChatRequest, user_id: str = UserDep):
    """群聊发言（SSE）。round_robin 兼容映射为 all"""
    mode = {"round_robin": "all"}.get(request.reply_mode, request.reply_mode)
    return sse_response(group_chat_service.chat_stream(
        user_id, group_id, request.message,
        reply_mode=mode, target_character_id=request.target_character_id))


@router.post("/{group_id}/end")
async def end_group_chat(group_id: str, user_id: str = UserDep):
    group = group_service.get_group(user_id, group_id)
    if not group or not group.get("session_id"):
        raise HTTPException(404, "群聊不存在")
    job_id = distill_service.request(user_id, group["session_id"], "group")
    return {"status": "queued" if job_id else "up_to_date", "group_id": group_id,
            "job_id": job_id}


def _ws_user(websocket: WebSocket, token: str, user_id: str) -> Optional[str]:
    if AUTH_ENABLED:
        uid = decode_token(token or "")
        return uid
    return user_id or None


@router.websocket("/{group_id}/ws")
async def group_chat_ws(websocket: WebSocket, group_id: str,
                        token: str = Query(""), user_id: str = Query("")):
    """群聊 WebSocket：与 SSE 事件协议一致，额外支持用户中途插话（打断半截话标记 interrupted）"""
    uid = _ws_user(websocket, token, user_id)
    if not uid:
        await websocket.close(code=4401)
        return
    validate_id(group_id, "group_id")
    if not group_service.get_group(uid, group_id):
        await websocket.close(code=4404)
        return

    await websocket.accept()
    send_lock = asyncio.Lock()
    state: dict = {"task": None, "interrupt": asyncio.Event()}

    async def send(event: dict):
        async with send_lock:
            await websocket.send_json(event)

    async def run_round(message: str, reply_mode: str, target: Optional[str]):
        try:
            async for event in group_chat_service.chat_stream(
                    uid, group_id, message, reply_mode=reply_mode,
                    target_character_id=target, interrupt_event=state["interrupt"]):
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

            mode = {"round_robin": "all"}.get(
                data.get("reply_mode", "auto"), data.get("reply_mode", "auto"))
            running = state["task"]
            if running is not None and not running.done():
                state["interrupt"].set()
                try:
                    await asyncio.wait_for(asyncio.shield(running), timeout=10)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass

            state["interrupt"] = asyncio.Event()
            state["task"] = asyncio.create_task(
                run_round(content, mode, data.get("target_character_id")))
    except WebSocketDisconnect:
        pass
    finally:
        task = state["task"]
        if task is not None and not task.done():
            state["interrupt"].set()
            task.cancel()
