"""角色管理 API v2"""
from fastapi import APIRouter, HTTPException

from app.core.security import UserDep, validate_id
from app.models.schemas import CharacterCreate, CharacterCreateV2
from app.services.character_service import character_service
from app.store import store

router = APIRouter(prefix="/api/characters", tags=["characters"])


@router.post("")
async def create_character(request: CharacterCreateV2, user_id: str = UserDep):
    """创建角色（导入人设，LLM 解析为结构化档案）"""
    return await character_service.create_character(
        user_id, request.name, request.raw_content,
        nickname=request.nickname, talkativeness=request.talkativeness)


@router.get("")
async def list_characters(user_id: str = UserDep):
    return character_service.list_characters(user_id)


@router.get("/{character_id}")
async def get_character(character_id: str, user_id: str = UserDep):
    validate_id(character_id, "character_id")
    profile = character_service.get_character(user_id, character_id)
    if profile is None:
        raise HTTPException(404, "角色不存在")
    return profile


@router.put("/{character_id}")
async def update_character(character_id: str, request: CharacterCreate,
                           user_id: str = UserDep):
    validate_id(character_id, "character_id")
    profile = await character_service.update_character(
        user_id, character_id, request.name, request.raw_content)
    if profile is None:
        raise HTTPException(404, "角色不存在")
    return profile


@router.patch("/{character_id}")
async def patch_character(character_id: str, payload: dict, user_id: str = UserDep):
    """轻量更新：昵称/健谈度/语音等，不重新解析人设"""
    validate_id(character_id, "character_id")
    if not store.get_character(user_id, character_id):
        raise HTTPException(404, "角色不存在")
    fields = {k: v for k, v in payload.items()
              if k in ("name", "nickname", "talkativeness", "voice_config",
                       "description")}
    if fields:
        store.update_character(user_id, character_id, **fields)
    return store.get_character(user_id, character_id)


@router.delete("/{character_id}")
async def delete_character(character_id: str, user_id: str = UserDep):
    validate_id(character_id, "character_id")
    return {"success": character_service.delete_character(user_id, character_id)}
