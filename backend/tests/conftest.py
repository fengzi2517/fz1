"""测试基座：内存 SQLite + FakeLLM + 鉴权客户端"""
import inspect
import os
import sys
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="rp_test_"))
os.environ.setdefault("AUTH_ENABLED", "true")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Database, set_db  # noqa: E402
from app.llm.adapter import llm_adapter  # noqa: E402
from app.store import store  # noqa: E402


class FakeLLM:
    """可控 LLM：chat/chat_json/chat_stream/embed 全假"""

    def __init__(self):
        self.stream_chunks = ["你好", "，世界。"]
        self.chat_content = "这是滚动摘要。"
        self.json_script = []       # 按调用顺序弹出（dict 或 callable(messages, default)）
        self.stream_error = None    # 设置后 chat_stream 抛异常
        self.streams = 0
        self.chats = 0
        self.chat_prompts = []      # 记录 chat() 收到的消息，供断言
        self.json_prompts = []

    async def chat(self, messages, **kw):
        self.chats += 1
        self.chat_prompts.append(messages)
        return {"content": self.chat_content}

    async def chat_json(self, messages, default=None, **kw):
        self.json_prompts.append(messages)
        if self.json_script:
            item = self.json_script.pop(0)
            if callable(item):
                res = item(messages, default)
                if inspect.isawaitable(res):
                    return await res
                return res
            return item
        return default if default is not None else {}

    async def chat_stream(self, messages, **kw):
        self.streams += 1
        if self.stream_error:
            raise self.stream_error
        for chunk in self.stream_chunks:
            yield chunk

    async def embed(self, texts):
        return None


@pytest.fixture(autouse=True)
def memory_db():
    db = Database(":memory:")
    store.bind(db)
    set_db(db)
    yield db
    set_db(None)
    store.bind(None)
    db.close()


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    for name in ("chat", "chat_json", "chat_stream", "embed"):
        monkeypatch.setattr(llm_adapter, name, getattr(fake, name), raising=False)
    monkeypatch.setattr(type(llm_adapter), "embed_available",
                        property(lambda self: False), raising=False)
    return fake


@pytest.fixture
def api_app():
    from app.api import (asr, auth, characters, chat, conversations, groups,
                         memory, tts)
    app = FastAPI()
    for r in (auth, chat, characters, memory, groups, asr, tts, conversations):
        app.include_router(r.router)
    return app


@pytest.fixture
def client(api_app):
    return TestClient(api_app)


@pytest.fixture
def user(memory_db):
    """已注册测试用户 + Bearer 头"""
    from app.core.security import create_token, hash_password
    uid = store.create_user("tester", hash_password("pass1234"))
    return {"id": uid, "headers": {"Authorization": f"Bearer {create_token(uid)}"}}
