"""对话相关 API"""
import json
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import StreamingResponse

from app.config import SESSION_MAX_MESSAGES
from app.models.schemas import ChatRequest, EndSessionRequest
from app.services.chat_service import chat_service
from app.services.distill_service import distill_service
from app.services.memory_service import memory_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/history")
async def get_history(
    background_tasks: BackgroundTasks,
    user_id: str,
    character_id: str,
    session_id: Optional[str] = None,
):
    """获取会话历史；不传 session_id 时取该角色最近一次会话。

    会话软轮换：若最近会话已超过 SESSION_MAX_MESSAGES 条或跨天，
    则先把该会话送去蒸馏，并返回 session_id=null，让前端开启新会话，
    但历史消息仍然返回用于展示。
    """
    sid = session_id or await memory_service.get_latest_session(user_id, character_id)
    if not sid:
        return {"session_id": None, "messages": [], "rotated": False}

    messages = await memory_service.get_recent_history(
        user_id, character_id, sid, limit=2000
    )

    rotated = False
    if session_id is None and _need_rotate(messages):
        background_tasks.add_task(
            distill_service.distill_session, user_id, character_id, sid
        )
        rotated = True

    return {
        "session_id": None if rotated else sid,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp.isoformat() if m.timestamp else None,
            }
            for m in messages
            if m.role in ("user", "assistant")
        ],
        "rotated": rotated,
    }


def _need_rotate(messages) -> bool:
    """判断会话是否需要轮换（消息过多或跨天）"""
    if len(messages) >= SESSION_MAX_MESSAGES:
        return True
    if messages and messages[0].timestamp:
        return messages[0].timestamp.date() != datetime.now().date()
    return False


@router.post("/send")
async def send_message(request: ChatRequest):
    """发送消息，SSE 流式返回"""
    async def event_generator():
        try:
            async for event in chat_service.chat_stream(request):
                if event["type"] == "token":
                    data = json.dumps({
                        "type": "token",
                        "content": event["content"],
                        "session_id": event["session_id"],
                    }, ensure_ascii=False)
                    yield f"data: {data}\n\n"
                elif event["type"] == "done":
                    data = json.dumps({
                        "type": "done",
                        "session_id": event["session_id"],
                    }, ensure_ascii=False)
                    yield f"data: {data}\n\n"
                elif event["type"] == "error":
                    data = json.dumps({
                        "type": "error",
                        "message": event["message"],
                    }, ensure_ascii=False)
                    yield f"data: {data}\n\n"
        except Exception as e:
            data = json.dumps({
                "type": "error",
                "message": str(e),
            }, ensure_ascii=False)
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


@router.post("/end")
async def end_session(request: EndSessionRequest, background_tasks: BackgroundTasks):
    """结束会话，触发记忆蒸馏"""
    background_tasks.add_task(
        distill_service.distill_session,
        request.user_id, request.character_id, request.session_id
    )
    return {"status": "distilling", "session_id": request.session_id}
