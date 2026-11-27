"""P2-4/5/6/7/8 群聊事件流与视角测试"""
import asyncio

import pytest

from app.services.group_service import GroupChatService, group_service
from app.store import store


@pytest.fixture
def group(memory_db):
    uid = store.create_user("gu", "hash")
    c1 = store.create_character(uid, "小美", profile_md="# 小美",
                                talkativeness=100)
    c2 = store.create_character(uid, "阿强", profile_md="# 阿强",
                                talkativeness=100)
    gid = group_service.create_group(uid, "测试群", [c1, c2])["id"]
    return uid, {"c1": c1, "c2": c2}, gid, store.get_group(uid, gid)["session_id"]


class TestChatStreamBasics:
    async def test_group_not_found(self, memory_db, fake_llm):
        uid = store.create_user("x", "hash")
        events = [e async for e in GroupChatService().chat_stream(
            uid, "g_missing", "hi")]
        assert events[0]["type"] == "error"

    async def test_target_not_member_rejected(self, group, fake_llm):
        uid, cs, gid, sid = group
        events = [e async for e in GroupChatService().chat_stream(
            uid, gid, "hi", reply_mode="target", target_character_id="c_nope")]
        assert any(e["type"] == "error" for e in events)

    async def test_mention_single_speaker(self, group, fake_llm):
        uid, cs, gid, sid = group
        events = [e async for e in GroupChatService().chat_stream(
            uid, gid, "@小美 你觉得呢")]
        types = [e["type"] for e in events]
        assert types[0] == "start" and types[-1] == "end"
        assert events[0]["character_id"] == cs["c1"]
        started = {e["character_id"] for e in events if e["type"] == "start"}
        assert started == {cs["c1"]}
        end = events[-1]
        assert end["scheduled"] == [cs["c1"]]
        msgs = store.get_messages(sid)
        assert msgs[1]["role"] == "assistant" and msgs[1]["name"] == "小美"

    async def test_speaker_llm_failure_reports_error(self, group, fake_llm):
        """回归：异常日志曾引用未定义的名字，NameError 会盖掉 error 事件让前端只见流断开"""
        uid, cs, gid, sid = group
        fake_llm.stream_error = RuntimeError("上游 503")
        events = [e async for e in GroupChatService().chat_stream(
            uid, gid, "大家好", reply_mode="all")]
        errs = [e for e in events if e["type"] == "error"]
        assert len(errs) == 2 and all("上游 503" in e["message"] for e in errs)
        assert all(e.get("character_id") in (cs["c1"], cs["c2"]) for e in errs)
        assert events[-1]["type"] == "end"
        assert store.count_messages(sid) == 1          # 只有用户那句，失败不落半截话

    async def test_all_mode_sequential_both_speak(self, group, fake_llm):
        uid, cs, gid, sid = group
        events = [e async for e in GroupChatService().chat_stream(
            uid, gid, "大家好", reply_mode="all")]
        starts = [e for e in events if e["type"] == "start"]
        assert [s["character_id"] for s in starts] == [cs["c1"], cs["c2"]]
        # 每 token 都带 character_id（修 G4）
        for t in (e for e in events if e["type"] == "token"):
            assert t["character_id"] in (cs["c1"], cs["c2"])
        assert store.count_messages(sid) == 3  # user + 2 assistant

    async def test_parallel_mode_events_tagged(self, group, fake_llm):
        uid, cs, gid, sid = group
        events = [e async for e in GroupChatService().chat_stream(
            uid, gid, "随便聊聊", reply_mode="auto")]
        starts = {e["character_id"] for e in events if e["type"] == "start"}
        assert starts == {cs["c1"], cs["c2"]}  # 无人提及 → 两人并行
        for t in (e for e in events if e["type"] == "token"):
            assert "character_id" in t and "name" in t
        done_ids = {e["character_id"] for e in events if e["type"] == "done"}
        assert done_ids == starts
        saved = {m["character_id"] for m in store.get_messages(sid)
                 if m["role"] == "assistant"}
        assert saved == starts

    async def test_muted_member_never_picked(self, group, fake_llm):
        uid, cs, gid, sid = group
        group_service.set_muted(uid, gid, [cs["c1"]])
        events = [e async for e in GroupChatService().chat_stream(
            uid, gid, "随便聊聊", reply_mode="auto")]
        starts = {e["character_id"] for e in events if e["type"] == "start"}
        assert cs["c1"] not in starts


class TestInterrupt:
    async def test_preset_interrupt_skips_generation(self, group, fake_llm):
        uid, cs, gid, sid = group
        ev = asyncio.Event()
        ev.set()
        events = [e async for e in GroupChatService().chat_stream(
            uid, gid, "@小美 说", reply_mode="target",
            target_character_id=cs["c1"], interrupt_event=ev)]
        types = [e["type"] for e in events]
        assert "interrupted" in types
        assert "token" not in types
        assert store.count_messages(sid) == 1  # 只有用户消息

    async def test_interrupted_half_msg_flagged_not_distilled(self, memory_db,
                                                              fake_llm):
        """半截话标记 interrupted 且蒸馏时被剔除（G8 全链路）"""
        from app.services.distill_service import distill_service
        uid = store.create_user("hu", "hash")
        c1 = store.create_character(uid, "小美")
        gid = group_service.create_group(uid, "G", [c1])["id"]
        sid = store.get_group(uid, gid)["session_id"]
        GroupChatService._save(sid, {"id": c1, "name": "小美"}, "说到一半",
                               interrupted=True)
        GroupChatService._save(sid, {"id": c1, "name": "小美"}, "完整的一句话")
        flags = [m["flags"] for m in store.get_messages(sid)]
        assert flags[0].get("interrupted") == 1
        assert "interrupted" not in flags[1]

        fake_llm.chat_content = "摘要"
        distill_service.request(uid, sid, "group")
        job = store.claim_job()
        await distill_service.run_job(job)
        prompt = fake_llm.chat_prompts[0][0]["content"]
        assert "完整的一句话" in prompt and "说到一半" not in prompt


class TestPrivateView:
    def test_history_renders_self_as_you(self, memory_db):
        uid = store.create_user("iv", "hash")
        c1 = store.create_character(uid, "小美")
        c2 = store.create_character(uid, "阿强")
        history = [
            {"role": "user", "content": "早"},
            {"role": "assistant", "content": "早上好呀", "character_id": c1,
             "name": "小美"},
            {"role": "assistant", "content": "哼", "character_id": c2, "name": "阿强"},
            {"role": "system", "content": "阿强 退出了群聊"},
        ]
        mine = GroupChatService._render_history_for(history, {"id": c1})
        assert "你: 早上好呀" in mine      # 自己视角（修 G3）
        assert "阿强: 哼" in mine
        assert "用户: 早" in mine
        assert "阿强 退出了群聊" in mine

    async def test_member_context_has_own_memory_and_relation(self, group,
                                                              fake_llm):
        uid, cs, gid, sid = group
        store.add_memory(uid, "character", "小美记得用户爱吃辣", character_id=cs["c1"])
        store.set_relation(uid, cs["c1"], affinity=0.9, emotion="开心")
        svc = GroupChatService()
        member = store.get_character(uid, cs["c1"])
        built = await svc._build_member_messages(
            uid, member, "上次吃辣的事", {"summary": "", "older": [],
                                         "announcement": "校园背景", "history": []},
            [member])
        joined = "\n".join(m["content"] for m in built)
        assert "爱吃辣" in joined          # 个人记忆注入
        assert "好感度 0.90" in joined     # 关系状态注入
        assert "校园背景" in joined         # 群公告注入


class TestMembership:
    def test_join_leave_system_messages(self, group, fake_llm):
        uid, cs, gid, sid = group
        c3 = store.create_character(uid, "小雅")
        group_service.add_member(uid, gid, c3)
        hist = store.get_messages(sid)
        assert any(m["role"] == "system" and "小雅 加入了群聊" in m["content"]
                   for m in hist)
        group_service.remove_member(uid, gid, c3)
        hist = store.get_messages(sid)
        assert any(m["role"] == "system" and "小雅 退出了群聊" in m["content"]
                   for m in hist)

    def test_member_limit(self, memory_db, fake_llm):
        from app.config import GROUP_MEMBER_LIMIT
        uid = store.create_user("ju", "hash")
        ids = [store.create_character(uid, f"C{i}")
               for i in range(GROUP_MEMBER_LIMIT + 1)]
        gid = group_service.create_group(uid, "G", ids[:GROUP_MEMBER_LIMIT])["id"]
        with pytest.raises(ValueError):
            group_service.add_member(uid, gid, ids[GROUP_MEMBER_LIMIT])
        with pytest.raises(ValueError):
            group_service.create_group(uid, "大群", ids[:GROUP_MEMBER_LIMIT + 1])

    def test_cannot_remove_last_member(self, group):
        uid, cs, gid, sid = group
        group_service.remove_member(uid, gid, cs["c1"])
        with pytest.raises(ValueError):
            group_service.remove_member(uid, gid, cs["c2"])

    def test_create_requires_valid_chars(self, memory_db):
        uid = store.create_user("ku", "hash")
        with pytest.raises(ValueError):
            group_service.create_group(uid, "G", ["c_missing"])
        with pytest.raises(ValueError):
            group_service.create_group(uid, "G", [])
