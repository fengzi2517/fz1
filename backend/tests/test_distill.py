"""P1-6 事务化蒸馏管线测试"""
import pytest

from app.services.distill_service import distill_service
from app.store import store


@pytest.fixture
def setup(memory_db):
    uid = store.create_user("du", "hash")
    cid = store.create_character(uid, "阿强", profile_md="# 阿强")
    sid = store.create_session(uid, "char", character_id=cid)
    return uid, cid, sid


class TestRequest:
    def test_too_few_messages_no_job(self, setup, fake_llm):
        uid, cid, sid = setup
        store.append_message(sid, "user", "只有一条")
        assert distill_service.request(uid, sid, "chat") == 0

    def test_enqueues_range(self, setup, fake_llm):
        uid, cid, sid = setup
        for i in range(4):
            store.append_message(sid, "user" if i % 2 == 0 else "assistant", f"消息{i}")
        jid = distill_service.request(uid, sid, "chat")
        assert jid > 0
        job = store.db.query_one("SELECT * FROM distill_jobs WHERE id=?", (jid,))
        assert (job["range_start"], job["range_end"]) == (0, 4)


class TestRunJob:
    async def test_summary_written_and_cursor(self, setup, fake_llm):
        uid, cid, sid = setup
        fake_llm.chat_content = "用户与阿强聊了天气。"
        for i in range(4):
            store.append_message(sid, "user" if i % 2 == 0 else "assistant", f"聊{i}")
        distill_service.request(uid, sid, "chat")
        job = store.claim_job()
        await distill_service.run_job(job)
        store.finish_job(job["id"], ok=True)
        s = store.get_session(sid)
        assert "用户与阿强聊了天气" in s["summary"]
        assert s["distilled_upto"] == 4
        assert store.jobs_pending() == 0

    async def test_interrupted_excluded_from_transcript(self, setup, fake_llm):
        uid, cid, sid = setup
        fake_llm.chat_content = "摘要。"
        store.append_message(sid, "user", "我们去海边吧")
        store.append_message(sid, "assistant", "好呀", flags={"interrupted": 1})
        store.append_message(sid, "assistant", "海边真漂亮")
        distill_service.request(uid, sid, "chat")
        job = store.claim_job()
        await distill_service.run_job(job)
        prompt = fake_llm.chat_prompts[0][0]["content"]
        assert "我们去海边吧" in prompt
        assert "好呀" not in prompt  # 半截话不进蒸馏（修 G8）

    async def test_full_plan_applied_atomically(self, setup, fake_llm):
        uid, cid, sid = setup
        fake_llm.chat_content = "用户说喜欢猫，心情不错。"
        fake_llm.json_script = [
            {"user_preferences": ["用户喜欢猫"],
             "character_relationship": ["阿强对用户印象变好"]},
            {"changed": True, "profile": "# 用户档案\n- 养猫",
             "note": "profile"},
            {"affinity": 0.72, "emotion": "亲近", "stage": "熟人"},
        ]
        for i in range(4):
            store.append_message(sid, "user" if i % 2 == 0 else "assistant", f"聊{i}")
        distill_service.request(uid, sid, "chat")
        job = store.claim_job()
        await distill_service.run_job(job)
        assert any(e["content"] == "用户喜欢猫"
                   for e in store.list_memories(uid, scope="user"))
        assert any(e["content"] == "阿强对用户印象变好"
                   for e in store.list_memories(uid, scope="character",
                                                character_id=cid))
        assert "养猫" in store.get_profile(uid)
        rel = store.get_relation(uid, cid)
        assert rel["affinity"] == 0.72 and rel["emotion"] == "亲近"

    async def test_llm_failure_leaves_no_partial_state(self, setup, fake_llm):
        uid, cid, sid = setup
        fake_llm.chat_content = "   "  # 摘要为空 → 抛错
        for i in range(3):
            store.append_message(sid, "user" if i % 2 == 0 else "assistant", f"x{i}")
        jid = distill_service.request(uid, sid, "chat")
        job = store.claim_job()
        with pytest.raises(RuntimeError):
            await distill_service.run_job(job)
        s = store.get_session(sid)
        assert s["distilled_upto"] == 0 and s["summary"] == ""
        assert store.list_memories(uid) == []
        store.reset_running_jobs()
        assert store.jobs_pending() == 1  # 可重试

    async def test_rerun_idempotent(self, setup, fake_llm):
        uid, cid, sid = setup
        fake_llm.chat_content = "摘要一段。"
        for i in range(4):
            store.append_message(sid, "user" if i % 2 == 0 else "assistant", f"x{i}")
        distill_service.request(uid, sid, "chat")
        await distill_service.run_job(store.claim_job())
        assert distill_service.request(uid, sid, "chat") == 0  # 无新消息不重跑

    async def test_summary_rolls_with_marker(self, setup, fake_llm):
        uid, cid, sid = setup
        fake_llm.chat_content = "第二段摘要。"
        store.set_session(sid, summary="第一段旧摘要。")
        for i in range(4):
            store.append_message(sid, "user" if i % 2 == 0 else "assistant", f"x{i}")
        distill_service.request(uid, sid, "chat")
        await distill_service.run_job(store.claim_job())
        s = store.get_session(sid)
        assert "第一段旧摘要" in s["summary"] and "第二段摘要" in s["summary"]
        assert "（第 4 条为止）" in s["summary"]


class TestGroupDistill:
    async def test_group_job_writes_member_memories_and_summary(self, memory_db,
                                                                fake_llm):
        uid = store.create_user("gu", "hash")
        c1 = store.create_character(uid, "小美")
        gid = store.create_group(uid, "G", [c1])
        sid = store.get_group(uid, gid)["session_id"]
        store.append_message(sid, "user", "今天一起吃饭吧")
        store.append_message(sid, "assistant", "好呀", character_id=c1, name="小美")
        fake_llm.chat_content = "群里约了吃饭。"
        fake_llm.json_script = [{
            "user_preferences": [],
            "character_relationship": ["小美觉得用户随和"],
            "group_events": ["用户提议一起吃饭"],
        }]
        jid = distill_service.request(uid, sid, "group")
        assert jid > 0
        await distill_service.run_job(store.claim_job())
        g = store.get_group(uid, gid)
        assert "群里约了吃饭" in g["summary"]
        assert g["distilled_upto"] == 2
        entries = store.list_memories(uid, scope="character", character_id=c1)
        assert any(e["content"] == "小美觉得用户随和" for e in entries)
        assert any("（群聊见闻）用户提议一起吃饭" == e["content"] for e in entries)
