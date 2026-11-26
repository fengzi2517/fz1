"""会话列表 API - 消息页展示角色单聊与群聊的最近一条消息（SQLite v2）"""
from fastapi import APIRouter

from app.core.security import UserDep
from app.store import store

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

PREVIEW_CHARS = 40


def _preview(text: str) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text[:PREVIEW_CHARS] + ("…" if len(text) > PREVIEW_CHARS else "")


def _last_message(sid: str) -> dict:
    msgs = store.get_messages(sid, limit=200)
    return msgs[-1] if msgs else None


def _format_last(msg, is_group: bool) -> str:
    if not msg:
        return ""
    if msg["role"] == "user":
        return f"我: {msg['content']}"
    name = msg.get("name") or "角色"
    return f"{name}: {msg['content']}" if is_group else msg["content"]


@router.get("")
async def list_conversations(user_id: str = UserDep):
    """按最近消息时间倒序返回角色单聊与群聊"""
    items = []

    for ch in store.list_characters(user_id):
        sess = store.latest_session(user_id, ch["id"])
        if not sess:
            continue
        last = _last_message(sess["id"])
        if not last:
            continue
        items.append({
            "type": "character",
            "id": ch["id"],
            "name": ch["name"],
            "preview": _preview(_format_last(last, False)),
            "timestamp": last["created_at"],
            "session_id": sess["id"],
            "members": [],
        })

    for g in store.list_groups(user_id):
        sid = g.get("session_id")
        if not sid:
            continue
        last = _last_message(sid)
        names = [store.get_character(user_id, cid)["name"]
                 for cid in g["member_ids"] if store.get_character(user_id, cid)]
        items.append({
            "type": "group",
            "id": g["id"],
            "name": g["name"],
            "preview": _preview(_format_last(last, True)) if last else "",
            "timestamp": g.get("last_message_at") or last["created_at"] if last
            else g.get("created_at"),
            "session_id": sid,
            "members": names,
        })

    items.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return items
