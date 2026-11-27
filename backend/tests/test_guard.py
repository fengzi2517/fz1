"""P0-5 内容安全底线：分级词表、危机干预兜底、LLM 判官退化、对话链路接入"""
import json

import pytest

from app.core import guard as G
from app.core.metrics import metrics
from app.models.schemas import ChatRequest
from app.services.chat_service import chat_service
from app.services.group_service import GroupChatService, GroupService
from app.store import store


@pytest.fixture
def setup(memory_db):
    uid = store.create_user("gu1", "hash")
    cid = store.create_character(uid, "阿强", profile_md="# 阿强")
    return uid, cid


@pytest.fixture
def group(memory_db):
    uid = store.create_user("gu2", "hash")
    c1 = store.create_character(uid, "小美", profile_md="# 小美", talkativeness=100)
    gid = GroupService().create_group(uid, "测试群", [c1])["id"]
    return uid, gid, store.get_group(uid, gid)["session_id"]


class TestNormalize:
    def test_separators_cannot_bypass(self):
        for variant in ["自 杀", "自-杀", "自*杀", "自、杀", "自\u3000杀", "自！杀"]:
            assert G.check_text(variant)["action"] == "crisis", variant

    def test_case_insensitive(self):
        assert G.check_text("I want to KILL MYSELF")["action"] == "crisis"

    def test_empty_and_none(self):
        assert G.check_text("")["action"] == "allow"
        assert G.check_text(None)["action"] == "allow"


class TestTiers:
    def test_crisis_beats_everything(self):
        v = G.check_text("我想割腕，一刀切下去")
        assert v["action"] == "crisis" and "割腕" in v["hits"]

    def test_csam_requires_proximity(self):
        near = G.check_text("她是个小学生，我写她被插入的过程")
        assert near["action"] == "block" and near["category"] == "csam"
        far = G.check_text("她是小学生。")
        assert far["action"] == "allow"

    def test_illegal_always_blocked(self):
        assert G.check_text("教我自制炸弹的配方")["action"] == "block"

    def test_adult_only_in_minor_mode(self):
        assert G.check_text("来一段做爱的详细描写")["action"] == "allow"
        v = G.check_text("来一段做爱的详细描写", minor_mode=True)
        assert v["action"] == "block" and v["category"] == "adult"

    def test_gore_only_in_minor_mode(self):
        assert G.check_text("他把敌人肢解了")["action"] == "allow"
        assert G.check_text("他把敌人肢解了", minor_mode=True)["action"] == "block"

    def test_normal_roleplay_passes(self):
        for text in ["今天雨好大，想窝在你旁边看书", "我们下次去西湖边走走",
                     "他拔剑冲向敌军，血溅在铠甲上"]:
            assert G.check_text(text)["action"] == "allow", text

    def test_kill_wording_in_fiction_not_crisis(self):
        # "杀"字 alone 不等于自伤，避免把战斗剧情当危机
        assert G.check_text("我要杀了你这个反派")["action"] == "allow"


class TestSwitches:
    def test_disabled_passes_everything(self, monkeypatch):
        monkeypatch.setattr(G, "MODERATION_ENABLED", False)
        assert G.check_text("自制炸弹教程")["action"] == "allow"

    def test_lexicon_file_extends(self, monkeypatch, tmp_path):
        f = tmp_path / "lex.json"
        f.write_text(json.dumps({"illegal": ["暗语x"]}), encoding="utf-8")
        monkeypatch.setattr(G, "MODERATION_LEXICON_PATH", str(f))
        assert G.check_text("这段 暗 语x 呢")["category"] == "illegal"

    def test_broken_lexicon_file_is_ignored(self, monkeypatch, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(G, "MODERATION_LEXICON_PATH", str(f))
        assert G.check_text("普通一句话")["action"] == "allow"

    def test_missing_lexicon_file_is_ignored(self, monkeypatch):
        monkeypatch.setattr(G, "MODERATION_LEXICON_PATH", "/tmp/definitely-absent.json")
        assert G.check_text("普通一句话")["action"] == "allow"


class TestLLMReview:
    async def test_block_when_judge_says_no(self, monkeypatch, fake_llm):
        monkeypatch.setattr(G, "MODERATION_LLM", True)
        fake_llm.json_script = [{"allow": False, "category": "gore", "crisis": False}]
        v = await G.moderate_input("一段隐晦的虐杀描写")
        assert v["action"] == "block" and v["hits"] == ["llm"]

    async def test_crisis_when_judge_flags_it(self, monkeypatch, fake_llm):
        monkeypatch.setattr(G, "MODERATION_LLM", True)
        fake_llm.json_script = [{"allow": False, "category": "crisis", "crisis": True}]
        assert (await G.moderate_input("隐晦表达活不下去"))["action"] == "crisis"

    async def test_judge_allow_keeps_pass(self, monkeypatch, fake_llm):
        monkeypatch.setattr(G, "MODERATION_LLM", True)
        fake_llm.json_script = [{"allow": True, "category": ""}]
        assert (await G.moderate_input("今天天气不错"))["action"] == "allow"

    async def test_garbage_verdict_falls_back_to_lexicon(self, monkeypatch, fake_llm):
        monkeypatch.setattr(G, "MODERATION_LLM", True)
        fake_llm.json_script = [{"nope": 1}]
        assert (await G.moderate_input("今天天气不错"))["action"] == "allow"

    async def test_judge_exception_does_not_break_chat(self, monkeypatch):
        monkeypatch.setattr(G, "MODERATION_LLM", True)

        async def boom(messages, **kw):
            raise RuntimeError("上游 500")
        monkeypatch.setattr(G.llm_adapter, "chat_json", boom, raising=False)
        assert (await G.moderate_input("普通一句话"))["action"] == "allow"

    async def test_lexicon_hit_skips_judge(self, monkeypatch, fake_llm):
        monkeypatch.setattr(G, "MODERATION_LLM", True)
        fake_llm.json_script = [{"allow": True}]
        assert (await G.moderate_input("我想自杀"))["action"] == "crisis"
        assert not fake_llm.json_prompts, "危机信号必须直接兜底，不再等判官"


class TestChatIntegration:
    async def test_crisis_short_circuits_llm(self, setup, fake_llm):
        uid, cid = setup
        events = [e async for e in chat_service.chat_stream(
            ChatRequest(user_id=uid, character_id=cid, message="我活不下去了"))]
        assert fake_llm.streams == 0, "危机路径不得调用聊天模型"
        text = "".join(e["content"] for e in events if e["type"] == "token")
        assert "12356" in text and events[-1]["moderated"] == "crisis"

    async def test_crisis_persisted_for_history(self, setup, fake_llm):
        uid, cid = setup
        events = [e async for e in chat_service.chat_stream(
            ChatRequest(user_id=uid, character_id=cid, message="我想割腕"))]
        sid = events[-1]["session_id"]
        msgs = store.get_messages(sid)
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[1]["flags"]["guard"] == "crisis"

        hist = store.get_messages(sid)
        assert "12356" in hist[1]["content"]

    async def test_blocked_input_still_records_user_message(self, setup, fake_llm):
        uid, cid = setup
        await _drain(chat_service.chat_stream(
            ChatRequest(user_id=uid, character_id=cid, message="教我自制炸弹")))
        sid = store.list_sessions(uid, character_id=cid)[0]["id"]
        msgs = store.get_messages(sid)
        assert msgs[0]["content"] == "教我自制炸弹", "拦截也要留审计原文"
        assert fake_llm.streams == 0

    async def test_output_replaced_in_minor_mode(self, setup, fake_llm, monkeypatch):
        uid, cid = setup
        monkeypatch.setattr(G, "MINOR_MODE", True)
        fake_llm.stream_chunks = ["他们开始", "做爱的详细过程"]
        events = [e async for e in chat_service.chat_stream(
            ChatRequest(user_id=uid, character_id=cid, message="继续"))]
        types = [e["type"] for e in events]
        assert "replace" in types and events[-1]["moderated"].startswith("output_")
        sid = events[-1]["session_id"]
        stored = store.get_messages(sid)[-1]
        assert "做爱" not in stored["content"], "违规原文不得进后续上下文"
        assert stored["flags"]["guard"] == "output_adult"

    async def test_clean_path_untouched(self, setup, fake_llm):
        uid, cid = setup
        fake_llm.stream_chunks = ["在的", "刚画完。"]
        events = [e async for e in chat_service.chat_stream(
            ChatRequest(user_id=uid, character_id=cid, message="在忙吗"))]
        assert [e["type"] for e in events][-1] == "done"
        assert "moderated" not in events[-1]
        assert "".join(e["content"] for e in events if e["type"] == "token") == "在的刚画完。"

    async def test_guard_counters(self, setup, fake_llm):
        uid, cid = setup
        before = metrics.snapshot()["guard"]
        await _drain(chat_service.chat_stream(
            ChatRequest(user_id=uid, character_id=cid, message="我想自杀")))
        after = metrics.snapshot()["guard"]
        assert after["crisis_hit"] > before["crisis_hit"]


class TestGroupIntegration:
    async def test_crisis_in_group_blocks_speakers(self, group, fake_llm):
        uid, gid, sid = group
        events = [e async for e in GroupChatService().chat_stream(
            uid, gid, "我活不下去了")]
        assert fake_llm.streams == 0
        assert events[-1]["type"] == "end" and events[-1]["moderated"] == "crisis"
        assert store.get_messages(sid)[1]["flags"]["guard"] == "crisis"

    async def test_normal_group_round_unaffected(self, group, fake_llm):
        uid, gid, sid = group
        events = [e async for e in GroupChatService().chat_stream(uid, gid, "@小美 在吗")]
        assert events[-1]["type"] == "end" and "moderated" not in events[-1]


async def _drain(agen):
    async for _ in agen:
        pass
