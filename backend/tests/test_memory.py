"""P1-3/P1-4/P1-5 记忆引擎测试：混合召回 + 冲突消解 + 块上限 + 导出"""
import pytest

from app.services.memory_service import ACTIVE_ENTRY_LIMIT, memory_service
from app.store import store


@pytest.fixture
def uid():
    return store.create_user("memuser", "hash")


@pytest.fixture
def mem(uid):
    """预置几条记忆，返回 uid 与 id 映射"""
    ids = {}
    ids["coffee"] = store.add_memory(uid, "user", "用户喜欢喝美式咖啡，不加糖")
    ids["cat"] = store.add_memory(uid, "user", "用户养了一只橘猫叫咪咪")
    ids["char"] = store.add_memory(uid, "character", "用户第一次见面时很紧张",
                                   character_id="c_1")
    return {"uid": uid, "ids": ids}


class TestRecall:
    async def test_keyword_hit_ranked_first(self, mem, fake_llm):
        hits = await memory_service.recall(mem["uid"], None, "咖啡好喝吗",
                                           context_key="s1", round_no=1)
        assert hits
        assert hits[0].content == "用户喜欢喝美式咖啡，不加糖"

    async def test_scope_filter(self, mem, fake_llm):
        hits = await memory_service.recall(mem["uid"], "c_1", "紧张",
                                           context_key="s1", round_no=1)
        assert any("紧张" in h.content for h in hits)

    async def test_zero_hit_fallback_not_empty(self, mem, fake_llm):
        hits = await memory_service.recall(
            mem["uid"], None, "zzzqqq不存在的关键词xxx",
            context_key="s2", round_no=1)
        assert len(hits) > 0  # 兜底注入高价值记忆，防"我不记得"

    async def test_ledger_anti_repeat_penalty(self, mem, fake_llm):
        h1 = await memory_service.recall(mem["uid"], None, "咖啡",
                                         context_key="s3", round_no=10)
        before = {h.entry_id: h.score for h in h1}
        h2 = await memory_service.recall(mem["uid"], None, "咖啡",
                                         context_key="s3", round_no=11)
        after = {h.entry_id: h.score for h in h2}
        eid = max(before, key=before.get)
        assert after[eid] < before[eid]  # 近 3 轮内已注入 → 降权

    async def test_ledger_expired_after_rounds(self, mem, fake_llm):
        await memory_service.recall(mem["uid"], None, "咖啡",
                                    context_key="s4", round_no=1)
        h = await memory_service.recall(mem["uid"], None, "咖啡",
                                        context_key="s4", round_no=50)
        h0 = await memory_service.recall(mem["uid"], None, "咖啡",
                                         context_key="s4b", round_no=50)
        assert h[0].score == h0[0].score  # 远轮后惩罚已过窗

    async def test_format_hits(self, mem, fake_llm):
        hits = await memory_service.recall(mem["uid"], None, "咖啡",
                                           context_key="s5", round_no=1)
        text = memory_service.format_hits(hits)
        assert "关于用户" in text and "咖啡" in text


class TestConflictResolution:
    async def test_add_new_without_similar(self, mem, fake_llm):
        action = await memory_service.add_memory_resolved(
            mem["uid"], "user", "用户下周要去东京出差")
        assert action == "ADD"

    async def test_llm_noop_dedup(self, mem, fake_llm):
        fake_llm.json_script.append({"action": "NOOP"})
        before = len(store.list_memories(mem["uid"], scope="user"))
        action = await memory_service.add_memory_resolved(
            mem["uid"], "user", "用户爱喝咖啡（不加糖）")
        assert action == "NOOP"
        assert len(store.list_memories(mem["uid"], scope="user")) == before

    async def test_llm_update(self, mem, fake_llm):
        fake_llm.json_script.append({
            "action": "UPDATE", "target_ids": [mem["ids"]["coffee"]],
            "final_content": "用户现在改喝拿铁了", "importance": 0.8})
        action = await memory_service.add_memory_resolved(
            mem["uid"], "user", "用户改喝拿铁")
        assert action == "UPDATE"
        e = store.get_memory(mem["ids"]["coffee"])
        assert e["content"] == "用户现在改喝拿铁了"
        assert e["importance"] == 0.8

    async def test_llm_delete_then_add(self, mem, fake_llm):
        fake_llm.json_script.append({
            "action": "DELETE", "target_ids": [mem["ids"]["cat"]]})
        action = await memory_service.add_memory_resolved(
            mem["uid"], "user", "咪咪走失后用户又领养了一只狗")
        assert action == "DELETE+ADD"
        assert store.get_memory(mem["ids"]["cat"])["status"] == "superseded"
        assert store.memory_log(mem["uid"])[0]["action"] == "ADD"

    async def test_llm_merge(self, mem, fake_llm):
        a = store.add_memory(mem["uid"], "user", "用户喜欢美式")
        b = store.add_memory(mem["uid"], "user", "用户每天一杯美式")
        fake_llm.json_script.append({
            "action": "MERGE", "target_ids": [a, b],
            "final_content": "用户每天一杯美式咖啡"})
        action = await memory_service.add_memory_resolved(
            mem["uid"], "user", "用户 coffee 重度依赖，天天美式")
        assert action == "MERGE"
        assert store.get_memory(a)["content"] == "用户每天一杯美式咖啡"
        assert store.get_memory(b)["status"] == "superseded"

    async def test_hallucinated_target_ids_ignored(self, mem, fake_llm):
        fake_llm.json_script.append({
            "action": "UPDATE", "target_ids": [99999], "final_content": "x"})
        action = await memory_service.add_memory_resolved(
            mem["uid"], "user", "用户喜欢美式咖啡加糖")
        # 目标 id 不在相似集内 → 视为无效 UPDATE → 走 ADD 兜底
        assert action in ("ADD", "NOOP")

    async def test_llm_down_falls_back_to_word_dedup(self, mem, fake_llm):
        async def boom(messages, default):
            raise RuntimeError("llm offline")
        fake_llm.json_script.append(boom)
        action = await memory_service.add_memory_resolved(
            mem["uid"], "user", "用户喜欢喝美式咖啡，不加糖")
        assert action == "NOOP"  # 词面重合高 → 判重

    async def test_empty_content_noop(self, mem, fake_llm):
        assert await memory_service.add_memory_resolved(
            mem["uid"], "user", "   ") == "NOOP"


class TestBlockLimit:
    async def test_archives_lowest_score(self, uid, monkeypatch):
        monkeypatch.setattr("app.services.memory_service.ACTIVE_ENTRY_LIMIT", 5)
        for i in range(8):
            store.add_memory(uid, "user", f"用户记得第{i}件事",
                             importance=0.1 + i * 0.1)
        memory_service._enforce_block_limit(uid, "user", None)
        active = store.list_memories(uid, scope="user")
        assert len(active) == 5
        archived = store.list_memories(uid, scope="user", status="archived")
        assert len(archived) == 3
        assert all(e["importance"] < 0.4 for e in archived)


class TestExport:
    def test_export_markdown(self, mem, fake_llm):
        out = memory_service.export_markdown(mem["uid"])
        assert "咖啡" in out["long_term"]
        assert set(out["character_memories"]) == {"c_1"}
        assert "咪咪" in out["long_term"]
