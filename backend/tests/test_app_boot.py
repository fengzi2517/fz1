"""整体装配 + 生命周期（P0-4）：用真实 app（含 lifespan）跑通注册→建角色→对话→记忆→会话列表"""
import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.db as db_module
from app import main
from app.services.distill_service import distill_service
from app.services.idle_service import IdleSweeper

EXPECTED_ROUTES = [
    "/api/auth/register", "/api/auth/login",
    "/api/chat/send", "/api/chat/history", "/api/chat/sessions",
    "/api/characters", "/api/memory/entries", "/api/memory/search",
    "/api/groups", "/api/conversations", "/api/tts/speech",
    "/api/asr/transcribe",
]


def sse_events(text: str):
    return [json.loads(line[6:]) for line in text.splitlines()
            if line.startswith("data: ")]


@pytest.fixture
def fresh_workers():
    """每轮 lifespan 跑在不同的临时 loop 上，需重置 worker 的 loop 绑定状态"""
    distill_service._worker_task = None
    distill_service._stop = asyncio.Event()
    yield
    distill_service._worker_task = None
    distill_service._stop = asyncio.Event()


def test_lifespan_starts_and_stops_background_workers(fake_llm, fresh_workers, monkeypatch):
    started = []

    class SpySweeper(IdleSweeper):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            started.append(self)

    monkeypatch.setattr(main, "IdleSweeper", SpySweeper)
    assert distill_service._worker_task is None

    with TestClient(main.app) as client:
        assert distill_service._worker_task is not None
        assert not distill_service._worker_task.done()
        assert started and started[0]._task is not None
        assert not started[0]._task.done()
        assert client.get("/health").json()["status"] == "ok"

    assert distill_service._worker_task is None
    assert started[0]._task is None


def test_all_routers_mounted_on_real_app(fake_llm, fresh_workers):
    with TestClient(main.app) as client:
        paths = set(client.get("/openapi.json").json()["paths"])
    missing = [p for p in EXPECTED_ROUTES if p not in paths]
    assert not missing, f"未挂载的路由: {missing}"


def test_end_to_end_journey_over_real_app(fake_llm, fresh_workers):
    with TestClient(main.app) as client:
        reg = client.post("/api/auth/register",
                          json={"username": "alice", "password": "secret123"})
        assert reg.status_code == 200
        uid = reg.json()["user_id"]
        headers = {"Authorization": f"Bearer {reg.json()['token']}"}

        # 重复注册 / 错密码 / 正确登录
        assert client.post("/api/auth/register",
                           json={"username": "alice",
                                 "password": "secret123"}).status_code == 409
        assert client.post("/api/auth/login",
                           json={"username": "alice",
                                 "password": "nope12345"}).status_code == 401
        login = client.post("/api/auth/login",
                            json={"username": "alice", "password": "secret123"})
        assert login.status_code == 200 and login.json()["user_id"] == uid

        # 开发模式后门 ?user_id= 在鉴权开启时必须无效
        assert client.get("/api/chat/sessions", params={"user_id": uid}).status_code == 401

        char = client.post("/api/characters", headers=headers, json={
            "name": "林晚", "nickname": "晚晚", "talkativeness": 80,
            "raw_content": "林晚，26 岁插画师，说话轻、爱用短句，深夜工作。"}).json()["id"]

        send = client.post("/api/chat/send", headers=headers,
                           json={"character_id": char, "message": "在忙吗"})
        events = sse_events(send.text)
        assert [e["type"] for e in events][-1] == "done"
        sid = events[-1]["session_id"]

        hist = client.get("/api/chat/history", params={"character_id": char},
                          headers=headers).json()
        assert hist["session_id"] == sid
        assert [m["role"] for m in hist["messages"]] == ["user", "assistant"]

        # 画像读写
        client.put("/api/memory/profile", headers=headers,
                   json={"content": "# 用户档案\n- 常住杭州"})
        assert "常住杭州" in client.get("/api/memory/profile",
                                        headers=headers).json()["content"]

        # 记忆条目：新增 → 检索 → 列表
        add = client.post("/api/memory/entries", headers=headers,
                          json={"content": "用户住在杭州，习惯深夜工作", "scope": "user"})
        assert add.json()["action"] == "ADD"
        action = client.post("/api/memory/entries", headers=headers,
                             json={"content": "用户喜欢雨天", "scope": "user"}).json()["action"]
        assert action in ("ADD", "NOOP", "UPDATE", "MERGE")
        hits = client.get("/api/memory/search", params={"q": "杭州"},
                          headers=headers).json()["hits"]
        assert any("杭州" in h["content"] for h in hits)
        assert client.get("/api/memory/long-term", headers=headers).json()["entries"]

        # 会话列表出现该角色，并带最近消息预览
        conv = client.get("/api/conversations", headers=headers).json()
        item = next(i for i in conv if i["id"] == char)
        assert item["type"] == "character" and item["session_id"] == sid
        assert item["preview"]

        # 越权：他人 token 看不到 alice 的会话
        other = client.post("/api/auth/register",
                            json={"username": "bob", "password": "secret123"})
        other_h = {"Authorization": f"Bearer {other.json()['token']}"}
        assert not client.get("/api/chat/history",
                              params={"character_id": char},
                              headers=other_h).json().get("messages")
        assert client.delete(f"/api/chat/sessions/{sid}",
                             headers=other_h).status_code == 404


def test_shutdown_closes_database_connection(fake_llm, fresh_workers):
    with TestClient(main.app) as client:
        db = db_module.get_db()
        client.post("/api/auth/register",
                    json={"username": "carol", "password": "secret123"})
        db.query("SELECT 1")                 # 运行期可用
    with pytest.raises(sqlite3.ProgrammingError):
        db.query("SELECT 1")                 # 关停后连接已关闭
