"""记忆查看 API"""
from fastapi import APIRouter

from app.services.memory_service import memory_service
from app.utils.markdown_parser import parse_front_matter
import aiofiles
from app.config import get_user_dir, get_character_dir

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("/long-term")
async def get_long_term_memory(user_id: str):
    """查看用户长期记忆"""
    path = get_user_dir(user_id) / "long-term.md"
    if not path.exists():
        return {"content": ""}
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        content = await f.read()
    _, body = parse_front_matter(content)
    return {"content": body}


@router.get("/character")
async def get_character_memory(user_id: str, character_id: str):
    """查看角色记忆"""
    path = get_character_dir(user_id, character_id) / "memory.md"
    if not path.exists():
        return {"content": ""}
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        content = await f.read()
    _, body = parse_front_matter(content)
    return {"content": body}
