"""会话列表 API - 消息页展示角色单聊与群聊的最近一条消息"""
from datetime import datetime

from fastapi import APIRouter

from app.services.character_service import character_service
from app.services.group_service import group_service
from app.services.memory_service import memory_service

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

PREVIEW_CHARS = 40


def _preview(text: str) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text[:PREVIEW_CHARS] + ("…" if len(text) > PREVIEW_CHARS else "")


@router.get("")
async def list_conversations(user_id: str):
    """按最近消息时间倒序返回角色单聊与群聊"""
    items = []

    for ch in await character_service.list_characters(user_id):
        session_id = await memory_service.get_latest_session(user_id, ch.character_id)
        if not session_id:
            continue
        messages = await memory_service.get_recent_history(
            user_id, ch.character_id, session_id, limit=1
        )
        if not messages:
            continue
        last = messages[-1]
        items.append({
            "type": "character",
            "id": ch.character_id,
            "name": ch.name,
            "preview": _preview(last.content),
            "timestamp": last.timestamp.isoformat() if last.timestamp else None,
            "members": [],
        })

    for g in await group_service.list_groups(user_id):
        history = await group_service.get_history(user_id, g.group_id, limit=1)
        if not history:
            continue
        last = history[-1]
        if last.get("role") == "user":
            preview = f"我: {last.get('content', '')}"
        else:
            preview = f"{last.get('name') or '角色'}: {last.get('content', '')}"
        ts = last.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts).isoformat()
            except ValueError:
                ts = None
        else:
            ts = None
        items.append({
            "type": "group",
            "id": g.group_id,
            "name": g.name,
            "preview": _preview(preview),
            "timestamp": ts,
            "members": [m.name for m in g.members],
        })

    items.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return items
