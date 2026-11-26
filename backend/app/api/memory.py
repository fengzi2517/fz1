"""记忆管理 API v2（P1-5）：条目化查看/检索/编辑/删除/导出"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.security import UserDep, validate_id
from app.services.memory_service import memory_service
from app.store import store

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryAdd(BaseModel):
    content: str
    scope: str = "user"  # user | character | group
    character_id: Optional[str] = None
    importance: Optional[float] = None


class MemoryPatch(BaseModel):
    content: Optional[str] = None
    importance: Optional[float] = None


@router.get("/long-term")
async def get_long_term(user_id: str = UserDep):
    """用户级长期记忆（结构化条目）"""
    return {"entries": store.list_memories(user_id, scope="user")}


@router.get("/character")
async def get_character_memory(user_id: str = UserDep, character_id: str = Query(...)):
    validate_id(character_id, "character_id")
    return {"entries": store.list_memories(user_id, scope="character",
                                           character_id=character_id)}


@router.get("/profile")
async def get_profile(user_id: str = UserDep):
    return {"content": memory_service.get_profile(user_id)}


@router.put("/profile")
async def set_profile(payload: dict, user_id: str = UserDep):
    memory_service.set_profile(user_id, payload.get("content", ""))
    return {"ok": True}


@router.get("/search")
async def search_memory(q: str, user_id: str = UserDep,
                        character_id: Optional[str] = None, top_k: int = 10):
    hits = await memory_service.recall(user_id, character_id, q, top_k=top_k)
    return {"hits": [h.model_dump() for h in hits]}


@router.post("/entries")
async def add_entry(request: MemoryAdd, user_id: str = UserDep):
    if request.scope not in ("user", "character", "group"):
        raise HTTPException(400, "scope 必须是 user/character/group")
    action = await memory_service.add_memory_resolved(
        user_id, request.scope, request.content,
        character_id=request.character_id, importance=request.importance)
    return {"action": action}


@router.patch("/entries/{entry_id}")
async def patch_entry(entry_id: int, request: MemoryPatch, user_id: str = UserDep):
    entry = store.get_memory(entry_id)
    if not entry or entry["user_id"] != user_id:
        raise HTTPException(404, "记忆不存在")
    store.update_memory(entry_id, content=request.content, importance=request.importance)
    return {"ok": True}


@router.delete("/entries/{entry_id}")
async def delete_entry(entry_id: int, user_id: str = UserDep):
    entry = store.get_memory(entry_id)
    if not entry or entry["user_id"] != user_id:
        raise HTTPException(404, "记忆不存在")
    store.update_memory(entry_id, status="superseded")
    store.log_memory_action(user_id, "DELETE", entry_id, "manual")
    return {"ok": True}


@router.get("/log")
async def memory_log(user_id: str = UserDep, limit: int = 100):
    return {"actions": store.memory_log(user_id, limit=limit)}


@router.get("/export")
async def export_memory(user_id: str = UserDep):
    return memory_service.export_markdown(user_id)
