"""Pydantic 数据模型"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional, Literal


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: Optional[datetime] = None
    tokens: int = 0


class ChatRequest(BaseModel):
    user_id: str = ""
    character_id: str
    session_id: Optional[str] = None
    message: str
    # 可选：请求级模型参数覆盖（不传则用 .env 默认值）
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)


class CharacterCreate(BaseModel):
    user_id: str = ""
    name: str
    raw_content: str  # 用户粘贴/导入的原始人设文本


class CharacterProfile(BaseModel):
    character_id: str
    name: str
    description: str = ""
    personality: str = ""
    speaking_style: str = ""
    background: str = ""
    relationship: str = ""
    tags: List[str] = []
    created_at: Optional[datetime] = None
    # 原始人设文本（仅详情接口返回，便于编辑时回填）
    raw_content: str = ""


class EndSessionRequest(BaseModel):
    user_id: str = ""
    character_id: str
    session_id: str


class RecallResult(BaseModel):
    source: Literal["long-term", "character-memory"]
    date: str
    content: str
    score: float


# ========== M2 群聊 ==========

class GroupCreate(BaseModel):
    user_id: str = ""
    name: str
    character_ids: List[str]


class GroupMember(BaseModel):
    character_id: str
    name: str
    description: str = ""


class GroupProfile(BaseModel):
    group_id: str
    name: str
    character_ids: List[str]
    members: List[GroupMember] = []
    created_at: Optional[datetime] = None


class GroupChatRequest(BaseModel):
    user_id: str = ""   # 鉴权模式下由 token 决定，保留字段仅用于 AUTH_ENABLED=false 开发模式
    message: str
    # 发言控制：auto=智能调度(提及+健谈度)；all=全员轮转(旧 round_robin)；target=指定角色
    reply_mode: Literal["auto", "all", "target", "round_robin"] = "auto"
    target_character_id: Optional[str] = None


class GroupMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    character_id: Optional[str] = None
    name: str = ""
    timestamp: Optional[datetime] = None


# ========== 升级新增 ==========

class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class GroupMemberIn(BaseModel):
    user_id: str = ""
    character_id: str


class GroupMuteRequest(BaseModel):
    user_id: str = ""
    muted_ids: List[str] = []


class MessageUpdate(BaseModel):
    content: str


class SwipeRequest(BaseModel):
    active: int = 0


class SessionBranchRequest(BaseModel):
    message_id: int
    title: str = ""


class CharacterCreateV2(BaseModel):
    user_id: str = ""
    name: str
    raw_content: str
    nickname: str = ""
    talkativeness: int = Field(50, ge=0, le=100)
