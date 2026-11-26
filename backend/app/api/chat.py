"""对话 API v2：SSE 流式 + 会话管理 + 消息操作（分支/swipe/编辑/重生成）"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.api.sse import sse_response
from app.core.security import UserDep, validate_id
from app.models.schemas import (
    ChatRequest, EndSessionRequest, MessageUpdate, SessionBranchRequest, SwipeRequest)
from app.services.chat_service import chat_service
from app.store import store

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/history")
async def get_history(user_id: str = UserDep, character_id: str = Query(...),
                      session_id: Optional[str] = None):
    validate_id(character_id, "character_id")
    if session_id:
        sess = store.get_session(session_id)
        if not sess or sess["user_id"] != user_id:
            raise HTTPException(404, "会话不存在")
    else:
        sess = store.latest_session(user_id, character_id)
    if not sess:
        return {"session_id": None, "messages": [], "rotated": False}
    messages = store.get_messages(sess["id"])
    return {
        "session_id": sess["id"],
        "rotated": False,
        "summary": sess.get("summary", ""),
        "messages": [{"id": m["id"], "role": m["role"], "content": m["content"],
                      "timestamp": m["created_at"],
                      "candidates": m["candidates"],
                      "active_candidate": m["active_candidate"]}
                     for m in messages if m["role"] in ("user", "assistant")],
    }


@router.post("/send")
async def send_message(request: ChatRequest, user_id: str = UserDep):
    request.user_id = user_id
    validate_id(request.character_id, "character_id")
    if request.session_id:
        validate_id(request.session_id, "session_id")
    return sse_response(chat_service.chat_stream(request))


@router.post("/end")
async def end_session(request: EndSessionRequest, user_id: str = UserDep):
    validate_id(request.session_id, "session_id")
    sess = store.get_session(request.session_id)
    if not sess or sess["user_id"] != user_id:
        raise HTTPException(404, "会话不存在")
    return await chat_service.end_session(user_id, request.session_id)


# ---------- 会话管理（修 D1/D5） ----------

@router.get("/sessions")
async def list_sessions(user_id: str = UserDep,
                        character_id: Optional[str] = None):
    return store.list_sessions(user_id, character_id)


@router.post("/sessions")
async def create_session(user_id: str = UserDep, character_id: str = Query(...)):
    validate_id(character_id, "character_id")
    sid = store.create_session(user_id, "char", character_id=character_id)
    return store.get_session(sid)


@router.post("/sessions/{sid}/branch")
async def branch_session(sid: str, req: SessionBranchRequest, user_id: str = UserDep):
    """分支：克隆到 message_id 为止，返回新会话（修 D2，ST checkpoint/branch）"""
    validate_id(sid, "session_id")
    sess = store.get_session(sid)
    if not sess or sess["user_id"] != user_id:
        raise HTTPException(404, "会话不存在")
    src = store.db.query_one("SELECT * FROM messages WHERE id=? AND session_id=?",
                             (req.message_id, sid))
    if not src:
        raise HTTPException(404, "消息不存在")
    new_sid = store.create_session(user_id, sess["kind"],
                                   character_id=sess["character_id"],
                                   group_id=sess["group_id"],
                                   parent_session_id=sid, branched_at=src["seq"],
                                   title=req.title or f"{sess['title']}·分支")
    rows = store.db.query(
        "SELECT seq, role, character_id, name, content, flags, token_count, created_at "
        "FROM messages WHERE session_id=? AND seq<=? ORDER BY seq", (sid, src["seq"]))
    with store.db.transaction() as conn:
        for r in rows:
            conn.execute(
                "INSERT INTO messages(session_id, seq, role, character_id, name, content, "
                "flags, token_count, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (new_sid, r["seq"], r["role"], r["character_id"], r["name"],
                 r["content"], r["flags"], r["token_count"], r["created_at"]))
        conn.execute("UPDATE sessions SET summary=?, distilled_upto=? WHERE id=?",
                     (sess.get("summary", ""), src["seq"], new_sid))
    return store.get_session(new_sid)


@router.delete("/sessions/{sid}")
async def delete_session(sid: str, user_id: str = UserDep, purge: bool = False):
    """默认进回收站（可恢复），purge=true 才连消息一起删"""
    validate_id(sid, "session_id")
    sess = store.get_session(sid)
    if not sess or sess["user_id"] != user_id:
        raise HTTPException(404, "会话不存在")
    if purge:
        store.db.execute("DELETE FROM messages WHERE session_id=?", (sid,))
        store.db.execute("DELETE FROM sessions WHERE id=?", (sid,))
    else:
        store.set_session(sid, status="trashed")
    return {"ok": True}


# ---------- 消息操作（修 D2） ----------

@router.patch("/messages/{sid}/{mid}")
async def edit_message(sid: str, mid: int, req: MessageUpdate, user_id: str = UserDep):
    sess = _own_session(sid, user_id)
    store.update_message_content(sess["id"], mid, req.content)
    return {"ok": True}


@router.post("/messages/{sid}/{mid}/delete_after")
async def truncate_after(sid: str, mid: int, user_id: str = UserDep):
    sess = _own_session(sid, user_id)
    store.db.execute("DELETE FROM messages WHERE session_id=? AND seq>?",
                     (sess["id"], mid))
    return {"ok": True}


@router.post("/messages/{sid}/{mid}/regenerate")
async def regenerate(sid: str, mid: int, user_id: str = UserDep):
    """重新生成该 assistant 消息：旧文本进候选（swipe 可切回）"""
    sess = _own_session(sid, user_id)
    row = store.db.query_one("SELECT * FROM messages WHERE id=? AND session_id=?",
                             (mid, sess["id"]))
    if not row or row["role"] != "assistant":
        raise HTTPException(400, "只能重新生成角色消息")

    async def gen():
        history = store.get_messages(sess["id"])
        prev_user = next((m["content"] for m in reversed(history)
                          if m["seq"] < row["seq"] and m["role"] == "user"), None)
        if prev_user is None:
            yield {"type": "error", "message": "找不到上游用户消息"}
            return
        messages, _ = await chat_service.build_messages(
            user_id, sess["character_id"], sess["id"], prev_user)
        # 排除该消息之后的内容（历史里可能已有更新的轮次）
        full = ""
        try:
            async for chunk in _regen_stream(messages):
                full += chunk
                yield {"type": "token", "content": chunk}
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return
        if full.strip():
            target = next((m for m in store.get_messages(sess["id"])
                           if m["id"] == mid), None)
            if target:
                cands = list(target["candidates"] or [])
                if target["content"] and target["content"] not in cands:
                    cands.append(target["content"])
                cands.append(full.strip())
                store.set_candidates(sess["id"], mid, cands, len(cands) - 1)
        yield {"type": "done"}

    return sse_response(gen())


async def _regen_stream(messages):
    from app.llm.adapter import llm_adapter
    async for chunk in llm_adapter.chat_stream(messages, max_tokens=800):
        yield chunk


@router.post("/messages/{sid}/{mid}/swipe")
async def swipe(sid: str, mid: int, req: SwipeRequest, user_id: str = UserDep):
    sess = _own_session(sid, user_id)
    msgs = store.get_messages(sess["id"])
    target = next((m for m in msgs if m["id"] == mid), None)
    if not target or not target["candidates"]:
        raise HTTPException(400, "该消息没有多个候选")
    if not (0 <= req.active < len(target["candidates"])):
        raise HTTPException(400, "候选序号越界")
    store.set_candidates(sess["id"], mid, target["candidates"], req.active)
    return {"ok": True, "content": target["candidates"][req.active]}


def _own_session(sid: str, user_id: str) -> dict:
    validate_id(sid, "session_id")
    sess = store.get_session(sid)
    if not sess or sess["user_id"] != user_id:
        raise HTTPException(404, "会话不存在")
    return sess
