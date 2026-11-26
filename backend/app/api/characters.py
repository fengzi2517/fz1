"""角色管理 API"""
from fastapi import APIRouter, HTTPException
from typing import List

from app.models.schemas import CharacterCreate, CharacterProfile
from app.services.character_service import character_service

router = APIRouter(prefix="/api/characters", tags=["characters"])


@router.post("", response_model=CharacterProfile)
async def create_character(request: CharacterCreate):
    """创建角色（导入人设）"""
    return await character_service.create_character(
        request.user_id, request.name, request.raw_content
    )


@router.get("", response_model=List[CharacterProfile])
async def list_characters(user_id: str):
    """获取用户的角色列表"""
    return await character_service.list_characters(user_id)


@router.get("/{character_id}", response_model=CharacterProfile)
async def get_character(user_id: str, character_id: str):
    """获取角色详情"""
    profile = await character_service.get_character(user_id, character_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    return profile


@router.put("/{character_id}", response_model=CharacterProfile)
async def update_character(character_id: str, request: CharacterCreate):
    """更新角色人设"""
    profile = await character_service.update_character(
        request.user_id, character_id, request.name, request.raw_content
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    return profile


@router.delete("/{character_id}")
async def delete_character(user_id: str, character_id: str):
    """删除角色"""
    success = await character_service.delete_character(user_id, character_id)
    return {"success": success}
