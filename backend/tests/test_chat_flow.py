"""对话服务端到端测试（FakeLLM）"""
import pytest

from app.models.schemas import ChatRequest
from app.services.chat_service import chat_service
from app.store import store


@pytest.fixture
def setup(memory_db):
    uid = store.create_user("cu", "hash")
    cid = store.create_character(uid, "阿强", profile_md="# 阿强\n喜欢围棋")
    return uid, cid


def collect(async_gen):
    import asyncio
    async def _run():
        return [e async for e in async_gen]
    return asyncio.run(_run())


class TestResolveSession:
    def test_new_when_none(self, setup):
        uid, cid = setup
        sess, rotated = chat_service.resolve_session(uid, cid, None)
        assert rotated is False and sess["kind"] == "char"

    def test_reuse_active(self, setup):
        uid, cid = setup
        s1, _ = chat_service.resolve_session(uid, cid, None)
        store.append_message(s1["id"], "user", "在吗")
        s2, rotated = chat_service.resolve_session(uid, cid, None)
        assert s2["id"] == s1["id"] and not rotated

    def test_rotation_archives_and_carries_summary(self, setup, fake_llm):
        from app.config import SESSION_MAX_MESSAGES
        uid, cid = setup
        sid = store.create_session(uid, "char", character_id=cid)
        store.set_session(sid, summary="前情：两人已和好")
        for i in range(SESSION_MAX_MESSAGES):
            store.append_message(sid, "user" if i % 2 == 0 else "assistant", f"m{i}")
        new_sess, rotated = chat_service.resolve_session(uid, cid, None)
        assert rotated
        assert store.get_session(sid)["status"] == "archived"
        assert store.jobs_pending() == 1  # 旧会话已入队蒸馏
        assert new_sess["summary"] == "前情：两人已和好"  # 摘要带走，跨会话不断忆


class TestChatStream:
    async def test_happy_path(self, setup, fake_llm):
        uid, cid = setup
        events = []
        async for e in chat_service.chat_stream(
                ChatRequest(user_id=uid, character_id=cid, message="你好呀")):
            events.append(e)
        types = [e["type"] for e in events]
        assert types == ["token", "token", "done"]
        done = events[-1]
        assert done["session_id"]
        assert "context_stats" in done
        msgs = store.get_messages(done["session_id"])
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[1]["content"] == "你好，世界。"

    async def test_llm_error_yields_error_event(self, setup, fake_llm):
        uid, cid = setup
        fake_llm.stream_error = RuntimeError("上游 503")
        events = []
        async for e in chat_service.chat_stream(
                ChatRequest(user_id=uid, character_id=cid, message="hi")):
            events.append(e)
        assert events[-1]["type"] == "error"
        sess = store.latest_session(uid, cid)
        msgs = store.get_messages(sess["id"])
        assert [m["role"] for m in msgs] == ["user"]  # 无假回复入库

    async def test_distill_triggered_after_threshold(self, setup, fake_llm):
        uid, cid = setup
        sid = store.create_session(uid, "char", character_id=cid)
        for i in range(12):
            store.append_message(sid, "user" if i % 2 == 0 else "assistant", f"x{i}")
        async for _ in chat_service.chat_stream(
                ChatRequest(user_id=uid, character_id=cid, message="继续",
                            session_id=sid)):
            pass
        assert store.jobs_pending() >= 1

    async def test_explicit_session_used(self, setup, fake_llm):
        uid, cid = setup
        sid = store.create_session(uid, "char", character_id=cid)
        async for _ in chat_service.chat_stream(
                ChatRequest(user_id=uid, character_id=cid, message="m",
                            session_id=sid)):
            pass
        assert store.count_messages(sid) == 2

    def test_build_messages_injects_memory_and_profile(self, setup, fake_llm):
        import asyncio
        uid, cid = setup
        store.add_memory(uid, "user", "用户下周要去东京")
        sid = store.create_session(uid, "char", character_id=cid)

        async def _run():
            return await chat_service.build_messages(uid, cid, sid, "去东京的事记得吗")
        msgs, stats = asyncio.run(_run())
        joined = "\n".join(m["content"] for m in msgs)
        assert "东京" in joined  # 混合召回注入
        assert "阿强" in joined  # 角色设定注入


class TestEndSession:
    def test_queue_or_uptodate(self, setup, fake_llm):
        uid, cid = setup
        sid = store.create_session(uid, "char", character_id=cid)
        import asyncio
        r = asyncio.run(chat_service.end_session(uid, sid))
        assert r["status"] == "up_to_date"
        store.append_message(sid, "user", "a")
        store.append_message(sid, "assistant", "b")
        r = asyncio.run(chat_service.end_session(uid, sid))
        assert r["status"] == "queued"
