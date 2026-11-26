"""SQLite 存储层测试"""
from app.store import store


class TestUsersProfile:
    def test_create_and_get(self):
        uid = store.create_user("alice", "hash")
        u = store.get_user(uid)
        assert u["username"] == "alice"
        assert store.get_user_by_username("alice")["id"] == uid
        assert store.get_user_by_username("nobody") is None

    def test_profile_roundtrip(self):
        uid = store.create_user("bob", "hash")
        assert store.get_profile(uid) == ""
        store.set_profile(uid, "# 档案")
        assert store.get_profile(uid) == "# 档案"
        store.set_profile(uid, "# 档案v2")
        assert store.get_profile(uid) == "# 档案v2"


class TestCharacters:
    def test_crud(self):
        uid = store.create_user("carol", "hash")
        cid = store.create_character(uid, "小美", profile_md="# 小美",
                                     tags=["御姐"], talkativeness=80,
                                     nickname="美美")
        c = store.get_character(uid, cid)
        assert c["name"] == "小美" and c["tags"] == ["御姐"]
        assert c["talkativeness"] == 80 and c["nickname"] == "美美"
        store.update_character(uid, cid, talkativeness=30, name="阿美")
        c = store.get_character(uid, cid)
        assert c["talkativeness"] == 30 and c["name"] == "阿美"
        assert store.get_character("u_other", cid) is None  # 租户隔离

    def test_delete_archives_memories(self):
        uid = store.create_user("dave", "hash")
        cid = store.create_character(uid, "角色A")
        store.add_memory(uid, "character", "他喜欢猫", character_id=cid)
        assert len(store.list_memories(uid, scope="character", character_id=cid)) == 1
        store.delete_character(uid, cid)
        assert store.list_memories(uid, scope="character", character_id=cid) == []
        assert len(store.list_memories(uid, status=None)) == 1  # 归档未删


class TestSessionsMessages:
    def test_seq_monotonic_and_tail_window(self):
        uid = store.create_user("erin", "hash")
        sid = store.create_session(uid, "char", character_id="c_1")
        for i in range(10):
            store.append_message(sid, "user" if i % 2 == 0 else "assistant",
                                 f"消息{i}")
        msgs = store.get_messages(sid)
        assert [m["seq"] for m in msgs] == list(range(1, 11))
        tail = store.get_messages(sid, limit=3)
        assert [m["seq"] for m in tail] == [8, 9, 10]
        assert store.count_messages(sid) == 10

    def test_latest_session(self):
        uid = store.create_user("frank", "hash")
        s1 = store.create_session(uid, "char", character_id="c_1")
        store.append_message(s1, "user", "hi")
        s2 = store.create_session(uid, "char", character_id="c_1")
        assert store.latest_session(uid, "c_1")["id"] == s2
        store.set_session(s2, status="archived")
        assert store.latest_session(uid, "c_1")["id"] == s1

    def test_candidates_and_flags(self):
        uid = store.create_user("grace", "hash")
        sid = store.create_session(uid, "char", character_id="c_1")
        mid = store.append_message(sid, "assistant", "v1", flags={"interrupted": 1})
        m = store.get_messages(sid)[0]
        assert m["flags"]["interrupted"] == 1
        store.set_candidates(sid, mid, ["v1", "v2"], 1)
        m = store.get_messages(sid)[0]
        assert m["content"] == "v2" and m["candidates"] == ["v1", "v2"]
        assert m["active_candidate"] == 1
        store.update_message_content(sid, mid, "改后")
        m = store.get_messages(sid)[0]
        assert m["content"] == "改后" and m["flags"].get("edited") == 1

    def test_trash_hidden(self):
        uid = store.create_user("heidi", "hash")
        sid = store.create_session(uid, "char", character_id="c_1")
        store.set_session(sid, status="trashed")
        assert all(s["id"] != sid for s in store.list_sessions(uid))
        assert any(s["id"] == sid for s in store.list_sessions(uid, include_trashed=True))


class TestGroups:
    def test_create_group_links_session(self):
        uid = store.create_user("ivan", "hash")
        c1 = store.create_character(uid, "A")
        c2 = store.create_character(uid, "B")
        gid = store.create_group(uid, "测试群", [c1, c2])
        g = store.get_group(uid, gid)
        assert g["session_id"] and set(g["member_ids"]) == {c1, c2}
        sess = store.get_session(g["session_id"])
        assert sess["kind"] == "group" and sess["group_id"] == gid

    def test_total_user_turns(self):
        uid = store.create_user("judy", "hash")
        gid = store.create_group(uid, "G", ["c_a"])
        sid = store.get_group(uid, gid)["session_id"]
        store.append_message(sid, "user", "一")
        store.append_message(sid, "assistant", "二", character_id="c_a")
        store.append_message(sid, "user", "三")
        assert store.group_total_user_turns(sid) == 2

    def test_delete_group(self):
        uid = store.create_user("kim", "hash")
        gid = store.create_group(uid, "G", ["c_a"])
        sid = store.get_group(uid, gid)["session_id"]
        store.append_message(sid, "user", "x")
        assert store.delete_group(uid, gid)
        assert store.get_group(uid, gid) is None
        assert store.get_messages(sid) == []

    def test_muted_and_scheduler(self):
        uid = store.create_user("leo", "hash")
        gid = store.create_group(uid, "G", ["c_a", "c_b"])
        store.update_group(uid, gid, muted_ids=["c_a"],
                           scheduler={"mode": "auto", "speakers_per_round": 3})
        g = store.get_group(uid, gid)
        assert g["muted_ids"] == ["c_a"]
        assert g["scheduler"]["speakers_per_round"] == 3


class TestJobs:
    def test_enqueue_dedup_claim_finish(self):
        uid = store.create_user("mona", "hash")
        sid = store.create_session(uid, "char", character_id="c_1")
        j1 = store.enqueue_job(sid, "chat", 0, 10)
        j2 = store.enqueue_job(sid, "chat", 0, 10)
        assert j1 == j2  # 去重
        assert store.jobs_pending() == 1
        job = store.claim_job()
        assert job["id"] == j1 and store.claim_job() is None
        store.finish_job(j1, ok=False, error="boom")
        assert store.claim_job() is None  # failed 不再 claim
        store.reset_running_jobs()

    def test_reset_running(self):
        uid = store.create_user("nick", "hash")
        sid = store.create_session(uid, "char", character_id="c_1")
        jid = store.enqueue_job(sid, "chat", 0, 5)
        store.claim_job()
        store.reset_running_jobs()
        assert store.jobs_pending() == 1
        assert store.claim_job()["id"] == jid
