"""鉴权与安全工具（P0-1）

- 密码：stdlib pbkdf2_hmac（sha256，10 万轮，随机盐）
- Token：HS256 JWT（stdlib hmac 实现，零新依赖）
- ID 校验：白名单正则，堵住旧版 user_id 路径穿越（01 文档 §6.6）
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request

from app.config import AUTH_ENABLED, AUTH_SECRET, AUTH_TOKEN_TTL

ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def validate_id(value: str, field: str = "id") -> str:
    if not value or not ID_RE.match(value):
        raise HTTPException(status_code=400, detail=f"非法的 {field}")
    return value


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return f"pbkdf2${base64.b64encode(salt).decode()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, digest_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        salt = base64.b64decode(salt_b64)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, Exception):
        return False


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_token(user_id: str, ttl: Optional[int] = None) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({
        "sub": user_id, "iat": now, "exp": now + (ttl or AUTH_TOKEN_TTL),
    }).encode())
    sig = hmac.new(AUTH_SECRET.encode(), f"{header}.{payload}".encode(),
                   hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"


def decode_token(token: str) -> Optional[str]:
    try:
        header, payload, sig = token.split(".")
        expect = _b64url(hmac.new(AUTH_SECRET.encode(), f"{header}.{payload}".encode(),
                                  hashlib.sha256).digest())
        if not hmac.compare_digest(expect, sig):
            return None
        data = json.loads(_b64url_decode(payload))
        if data.get("exp", 0) < time.time():
            return None
        return data.get("sub")
    except (ValueError, KeyError, Exception):
        return None


def new_user_id() -> str:
    return f"u_{secrets.token_hex(6)}"


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


async def current_user(request: Request) -> str:
    """FastAPI 依赖：AUTH_ENABLED 时从 Bearer token 解析 user_id；
    否则（仅显式 AUTH_ENABLED=false 的开发模式）允许 query/form 自报 user_id。
    生产环境绝不走自报分支（修 D7/§6.6）。
    """
    if AUTH_ENABLED:
        auth = request.headers.get("Authorization", "")
        token = ""
        if auth.startswith("Bearer "):
            token = auth[7:]
        elif request.query_params.get("token"):
            # 原生播放器/上传等无法带 header 的 GET 请求用 ?token=
            token = request.query_params["token"]
        else:
            raise HTTPException(status_code=401, detail="缺少访问令牌")
        user_id = decode_token(token)
        if not user_id:
            raise HTTPException(status_code=401, detail="令牌无效或已过期")
        return validate_id(user_id, "user_id")
    user_id = request.query_params.get("user_id") or ""
    if not user_id:
        form = getattr(request.state, "form_user_id", None)
        user_id = form or ""
    if not user_id:
        raise HTTPException(status_code=400, detail="缺少 user_id")
    return validate_id(user_id, "user_id")


UserDep = Depends(current_user)