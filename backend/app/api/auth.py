"""鉴权 API（P0-1）"""
from fastapi import APIRouter, HTTPException

from app.core.security import create_token, hash_password, verify_password
from app.models.schemas import LoginRequest, RegisterRequest
from app.store import store

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
async def register(req: RegisterRequest):
    username = req.username.strip()
    if len(username) < 2:
        raise HTTPException(400, "用户名至少 2 个字符")
    if store.get_user_by_username(username):
        raise HTTPException(409, "用户名已存在")
    uid = store.create_user(username, hash_password(req.password))
    return {"user_id": uid, "token": create_token(uid), "username": username}


@router.post("/login")
async def login(req: LoginRequest):
    user = store.get_user_by_username(req.username.strip())
    if not user or not verify_password(req.password, user["pw_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    return {"user_id": user["id"], "token": create_token(user["id"]),
            "username": user["username"]}
