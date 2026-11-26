"""群聊 WebSocket（P2）：鉴权关闭码、ping/pong、target 轮次落库、插话打断半截话、断连后服务可用"""
import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from app.llm.adapter import llm_adapter
from app.store import store


@pytest.fixture
def group(client, user, fake_llm):
    """依赖 fake_llm：建角色会走 LLM 解析，必须已经换成假模型"""
    ids = [client.post("/api/characters", headers=user["headers"], json={
        "name": name, "nickname": nick, "raw_content": f"{name}的设定",
        "talkativeness": 80}).json()["id"]
        for name, nick in (("阿强", "强哥"), ("小美", "美美"))]
    g = client.post("/api/groups", headers=user["headers"],
                    json={"name": "周末局", "character_ids": ids}).json()
    return {"ids": ids, "gid": g["id"], "sid": g["session_id"]}


@pytest.fixture
def token(user):
    return user["headers"]["Authorization"].split()[-1]


def ws_url(gid, token):
    return f"/api/groups/{gid}/ws?token={token}"


def read_until(ws, wanted, limit=200):
    got = []
    for _ in range(limit):
        ev = ws.receive_json()
        got.append(ev)
        if ev.get("type") == wanted:
            return got
    raise AssertionError(f"未在 {limit} 条事件里等到 {wanted!r}：{[e['type'] for e in got]}")


def test_ws_closes_without_token(client, group):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(ws_url(group["gid"], "")):
            pass
    assert exc.value.code == 4401


def test_ws_closes_for_unknown_group(client, token):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(ws_url("g_not_exist", token)):
            pass
    assert exc.value.code == 4404


def test_ws_ping_and_unknown_message_type(client, token, group):
    with client.websocket_connect(ws_url(group["gid"], token)) as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        ws.send_json({"type": "whatever"})
        assert ws.receive_json()["type"] == "error"


def test_ws_target_round_streams_and_persists(client, token, group, fake_llm):
    target = group["ids"][0]
    with client.websocket_connect(ws_url(group["gid"], token)) as ws:
        ws.send_json({"type": "message", "content": "阿强你在吗",
                      "reply_mode": "target", "target_character_id": target})
        events = read_until(ws, "end")

    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "end"
    assert "token" in kinds and "done" in kinds
    assert all(e.get("character_id") == target
               for e in events if e["type"] in ("start", "done"))

    msgs = store.get_messages(group["sid"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["character_id"] == target
    assert msgs[1]["content"] == "你好，世界。"
    assert not msgs[1]["flags"].get("interrupted")


def test_ws_target_not_in_group_errors_without_side_effects(client, token, group, fake_llm):
    with client.websocket_connect(ws_url(group["gid"], token)) as ws:
        ws.send_json({"type": "message", "content": "喂",
                      "reply_mode": "target", "target_character_id": "c_stranger"})
        ev = ws.receive_json()
    assert ev["type"] == "error"
    assert store.count_messages(group["sid"]) == 0


def test_ws_empty_message_is_ignored(client, token, group, fake_llm):
    with client.websocket_connect(ws_url(group["gid"], token)) as ws:
        ws.send_json({"type": "message", "content": "   "})
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}          # 空消息没起任何轮次
    assert store.count_messages(group["sid"]) == 0


def test_ws_interrupt_marks_half_reply_only(client, token, group, fake_llm, monkeypatch):
    """插话：半截话停止并标记 interrupted，新轮次正常完成（G8 的前提）"""
    async def slow_stream(messages, **kw):
        for i in range(8):
            await asyncio.sleep(0.05)
            yield f"第{i}段"

    monkeypatch.setattr(llm_adapter, "chat_stream", slow_stream)
    target = group["ids"][0]
    with client.websocket_connect(ws_url(group["gid"], token)) as ws:
        ws.send_json({"type": "message", "content": "慢慢说",
                      "reply_mode": "target", "target_character_id": target})
        first = read_until(ws, "token")
        assert first[-1]["type"] == "token"
        ws.send_json({"type": "message", "content": "别说了，换个话题",
                      "reply_mode": "target", "target_character_id": target})
        interrupted_round = read_until(ws, "end")
        second_round = read_until(ws, "end")

    assert "interrupted" in [e["type"] for e in interrupted_round]
    assert "interrupted" not in [e["type"] for e in second_round]

    msgs = store.get_messages(group["sid"])
    halves = [m for m in msgs if m["role"] == "assistant" and m["flags"].get("interrupted")]
    clean = [m for m in msgs if m["role"] == "assistant" and not m["flags"].get("interrupted")]
    assert len(halves) == 1 and halves[0]["content"].startswith("第0段")
    assert len(clean) == 1 and clean[0]["content"] == "第0段第1段第2段第3段第4段第5段第6段第7段"
    assert sum(1 for m in msgs if m["role"] == "user") == 2


def test_ws_stays_usable_after_disconnect_mid_reply(client, token, group, fake_llm, monkeypatch):
    async def endless_stream(messages, **kw):
        i = 0
        while True:
            await asyncio.sleep(0.02)
            yield f"话{i}"
            i += 1

    monkeypatch.setattr(llm_adapter, "chat_stream", endless_stream)
    with client.websocket_connect(ws_url(group["gid"], token)) as ws:
        ws.send_json({"type": "message", "content": "讲个长故事",
                      "reply_mode": "target", "target_character_id": group["ids"][0]})
        read_until(ws, "token")
        # 直接断开：处理器 finally 需置位 interrupt 并取消后台轮次

    with client.websocket_connect(ws_url(group["gid"], token)) as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    assert store.count_messages(group["sid"]) >= 1
