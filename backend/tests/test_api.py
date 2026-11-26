"""API 层集成测试（TestClient + FakeLLM + Bearer 鉴权）"""
import json

import pytest


def sse_events(resp):
    assert resp.status_code == 200
    out = []
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


@pytest.fixture
def char(client, user, fake_llm):
    r = client.post("/api/characters",
                    json={"name": "阿强", "raw_content": "一个爱下围棋的程序员",
                          "nickname": "强哥", "talkativeness": 80},
                    headers=user["headers"])
    assert r.status_code == 200
    return r.json()


class TestCharacterApi:
    def test_create_list_get(self, client, user, char):
        assert char["name"] == "阿强"
        cid = char["id"]
        assert client.get("/api/characters", headers=user["headers"]).json()[0]["id"] == cid
        g = client.get(f"/api/characters/{cid}", headers=user["headers"])
        assert g.status_code == 200
        assert client.get(f"/api/characters/{cid}",
                          headers={"Authorization": "Bearer x"}).status_code == 401

    def test_patch_talkativeness(self, client, user, char):
        r = client.patch(f"/api/characters/{char['id']}",
                         json={"talkativeness": 20, "nickname": "Q"},
                         headers=user["headers"])
        assert r.json()["talkativeness"] == 20 and r.json()["nickname"] == "Q"

    def test_delete(self, client, user, char):
        assert client.delete(f"/api/characters/{char['id']}",
                             headers=user["headers"]).json()["success"]
        assert client.get(f"/api/characters/{char['id']}",
                          headers=user["headers"]).status_code == 404


class TestChatApi:
    def test_send_sse_and_history(self, client, user, char):
        r = client.post("/api/chat/send",
                        json={"character_id": char["id"], "message": "你好"},
                        headers=user["headers"])
        events = sse_events(r)
        assert events[-1]["type"] == "done"
        sid = events[-1]["session_id"]
        h = client.get("/api/chat/history",
                       params={"character_id": char["id"]},
                       headers=user["headers"]).json()
        assert h["session_id"] == sid
        assert [m["role"] for m in h["messages"]] == ["user", "assistant"]

    def test_sessions_list_create_branch_swipe(self, client, user, char, fake_llm):
        from app.store import store
        cid = char["id"]
        r = client.post("/api/chat/send",
                        json={"character_id": cid, "message": "第一条"},
                        headers=user["headers"])
        sid = sse_events(r)[-1]["session_id"]
        assert any(s["id"] == sid for s in
                   client.get("/api/chat/sessions", headers=user["headers"]).json())

        # 分支（ST checkpoint 语义）
        r = client.post(f"/api/chat/sessions/{sid}/branch",
                        json={"message_id": 1}, headers=user["headers"])
        assert r.status_code == 200
        branch = r.json()
        assert branch["parent_session_id"] == sid
        assert store.get_messages(branch["id"]) and \
            len(store.get_messages(branch["id"])) == 1

        # 重新生成 → 候选追加；swipe → 切回旧候选
        msgs = store.get_messages(sid)
        amid = msgs[-1]["id"] if msgs[-1]["role"] == "assistant" else None
        if amid is None:  # 保证存在 assistant 消息
            amid = store.append_message(sid, "assistant", "旧回复")
        r = client.post(f"/api/chat/messages/{sid}/{amid}/regenerate",
                        headers=user["headers"])
        events = sse_events(r)
        assert events[-1]["type"] == "done"
        m = next(x for x in store.get_messages(sid) if x["id"] == amid)
        assert len(m["candidates"]) >= 2
        assert m["content"] == m["candidates"][-1]  # 新候选生效
        r = client.post(f"/api/chat/messages/{sid}/{amid}/swipe",
                        json={"active": 0}, headers=user["headers"])
        assert r.status_code == 200
        m = next(x for x in store.get_messages(sid) if x["id"] == amid)
        assert m["content"] == m["candidates"][0]

    def test_edit_message(self, client, user, char):
        from app.store import store
        r = client.post("/api/chat/send",
                        json={"character_id": char["id"], "message": "原话"},
                        headers=user["headers"])
        sid = sse_events(r)[-1]["session_id"]
        mid = store.get_messages(sid)[0]["id"]
        r = client.patch(f"/api/chat/messages/{sid}/{mid}",
                         json={"content": "改后的话"}, headers=user["headers"])
        assert r.status_code == 200
        assert store.get_messages(sid)[0]["content"] == "改后的话"

    def test_delete_session_to_trash(self, client, user, char):
        from app.store import store
        r = client.post("/api/chat/send",
                        json={"character_id": char["id"], "message": "hi"},
                        headers=user["headers"])
        sid = sse_events(r)[-1]["session_id"]
        assert client.delete(f"/api/chat/sessions/{sid}",
                             headers=user["headers"]).status_code == 200
        assert store.get_session(sid)["status"] == "trashed"

    def test_end_session(self, client, user, char):
        r = client.post("/api/chat/send",
                        json={"character_id": char["id"], "message": "hi"},
                        headers=user["headers"])
        sid = sse_events(r)[-1]["session_id"]
        client.post("/api/chat/send",
                    json={"character_id": char["id"], "message": "again",
                          "session_id": sid}, headers=user["headers"])
        r = client.post("/api/chat/end",
                        json={"character_id": char["id"], "session_id": sid},
                        headers=user["headers"])
        assert r.json()["status"] in ("queued", "up_to_date")


class TestGroupApi:
    @pytest.fixture
    def two_chars(self, client, user, fake_llm):
        ids = []
        for n in ("小美", "阿强"):
            r = client.post("/api/characters",
                            json={"name": n, "raw_content": f"{n}的设定",
                                  "talkativeness": 100},
                            headers=user["headers"])
            ids.append(r.json()["id"])
        return ids

    def test_group_crud_and_chat(self, client, user, two_chars, fake_llm):
        r = client.post("/api/groups",
                        json={"name": "日常群", "character_ids": two_chars},
                        headers=user["headers"])
        assert r.status_code == 200
        gid = r.json()["id"]
        r = client.post(f"/api/groups/{gid}/chat",
                        json={"message": "@小美 早上好", "reply_mode": "auto"},
                        headers=user["headers"])
        events = sse_events(r)
        assert events[0]["type"] == "start"
        assert events[0]["character_id"] == two_chars[0]
        assert events[-1]["type"] == "end"
        hist = client.get(f"/api/groups/{gid}/history",
                          headers=user["headers"]).json()
        assert len(hist["messages"]) == 2

    def test_mute_and_members(self, client, user, two_chars, fake_llm):
        r = client.post("/api/groups",
                        json={"name": "G", "character_ids": two_chars},
                        headers=user["headers"])
        gid = r.json()["id"]
        r = client.put(f"/api/groups/{gid}/mute",
                       json={"muted_ids": [two_chars[0]]},
                       headers=user["headers"])
        assert r.json()["muted_ids"] == [two_chars[0]]
        third = client.post("/api/characters",
                            json={"name": "小雅", "raw_content": "第三人"},
                            headers=user["headers"]).json()["id"]
        r = client.post(f"/api/groups/{gid}/members",
                        json={"character_id": third}, headers=user["headers"])
        assert third in r.json()["member_ids"]
        r = client.delete(f"/api/groups/{gid}/members/{third}",
                          headers=user["headers"])
        assert third not in r.json()["member_ids"]

    def test_announcement_and_scheduler(self, client, user, two_chars):
        r = client.post("/api/groups",
                        json={"name": "G", "character_ids": two_chars},
                        headers=user["headers"])
        gid = r.json()["id"]
        r = client.put(f"/api/groups/{gid}",
                       json={"announcement": "校园背景，禁止 OOC",
                             "scheduler": {"speakers_per_round": 3}},
                       headers=user["headers"])
        assert "校园背景" in r.json()["announcement"]
        assert r.json()["scheduler"]["speakers_per_round"] == 3

    def test_cross_user_group_invisible(self, client, user, two_chars):
        gid = client.post("/api/groups",
                          json={"name": "G", "character_ids": two_chars},
                          headers=user["headers"]).json()["id"]
        r2 = client.post("/api/auth/register",
                         json={"username": "other", "password": "pass1234"})
        hdr = {"Authorization": f"Bearer {r2.json()['token']}"}
        assert client.get(f"/api/groups/{gid}", headers=hdr).status_code == 404
        assert client.get("/api/groups", headers=hdr).json() == []


class TestMemoryApi:
    def test_entries_flow(self, client, user, fake_llm):
        r = client.post("/api/memory/entries",
                        json={"content": "用户是左撇子", "scope": "user"},
                        headers=user["headers"])
        assert r.json()["action"] == "ADD"
        entries = client.get("/api/memory/long-term",
                             headers=user["headers"]).json()["entries"]
        assert entries[0]["content"] == "用户是左撇子"
        eid = entries[0]["id"]
        assert client.patch(f"/api/memory/entries/{eid}",
                            json={"importance": 0.9},
                            headers=user["headers"]).status_code == 200
        assert client.delete(f"/api/memory/entries/{eid}",
                             headers=user["headers"]).status_code == 200
        assert client.get("/api/memory/long-term",
                          headers=user["headers"]).json()["entries"] == []
        log = client.get("/api/memory/log", headers=user["headers"]).json()["actions"]
        assert any(a["action"] == "DELETE" for a in log)

    def test_profile_and_export(self, client, user, fake_llm):
        client.put("/api/memory/profile", json={"content": "# 档案v2"},
                   headers=user["headers"])
        assert "档案v2" in client.get("/api/memory/profile",
                                      headers=user["headers"]).json()["content"]
        client.post("/api/memory/entries",
                    json={"content": "用户住杭州", "scope": "user"},
                    headers=user["headers"])
        exp = client.get("/api/memory/export", headers=user["headers"]).json()
        assert "杭州" in exp["long_term"]

    def test_search(self, client, user, fake_llm):
        client.post("/api/memory/entries",
                    json={"content": "用户喜欢村上春树", "scope": "user"},
                    headers=user["headers"])
        r = client.get("/api/memory/search", params={"q": "村上春树是谁"},
                       headers=user["headers"])
        assert r.json()["hits"][0]["content"] == "用户喜欢村上春树"


class TestVoiceStatus:
    def test_public_status_endpoints(self, client):
        assert client.get("/api/tts/status").status_code == 200
        assert client.get("/api/asr/status").status_code == 200

    def test_speech_requires_auth(self, client):
        assert client.get("/api/tts/speech", params={"text": "x"}).status_code == 401

    def test_speech_token_query_ok(self, client, user, monkeypatch):
        from app.services.tts_service import tts_service
        async def fake_synth(text, voice="", speed=0.0):
            return b"ID3fake-mp3"
        monkeypatch.setattr(tts_service, "synthesize", fake_synth)
        r = client.get("/api/tts/speech",
                       params={"text": "你好", "token":
                               user["headers"]["Authorization"][7:]})
        assert r.status_code == 200
        assert r.content == b"ID3fake-mp3"


class TestConversationsApi:
    def test_lists_chars_and_groups(self, client, user, fake_llm):
        cid = client.post("/api/characters",
                          json={"name": "小美", "raw_content": "设定"},
                          headers=user["headers"]).json()["id"]
        r = client.post("/api/chat/send",
                        json={"character_id": cid, "message": "最近如何"},
                        headers=user["headers"])
        sse_events(r)
        gid = client.post("/api/groups",
                          json={"name": "三人群", "character_ids": [cid]},
                          headers=user["headers"]).json()["id"]
        client.post(f"/api/groups/{gid}/chat",
                    json={"message": "有人吗", "reply_mode": "target",
                          "target_character_id": cid},
                    headers=user["headers"])
        items = client.get("/api/conversations",
                           headers=user["headers"]).json()
        kinds = {i["type"] for i in items}
        assert kinds == {"character", "group"}
        g = next(i for i in items if i["type"] == "group")
        assert "有人吗" in g["preview"] or "小美" in g["preview"]
