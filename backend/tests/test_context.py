"""P1-2 token 预算上下文装配测试（纯函数）"""
from app.services.chat_service import (
    assemble_context, rotate_reason, truncate_at_sentence)


def test_summary_reinjected():
    msgs, stats = assemble_context(
        character_md="# 角色", user_profile="# 档案", summary="之前他们去了海边",
        memory_lines="", relation_line="", history=[], current_message="你好")
    joined = "\n".join(m["content"] for m in msgs)
    assert "之前他们去了海边" in joined
    assert "【此前剧情摘要】" in stats["blocks"]


def test_budget_respected_small_window():
    big = "很长的设定" * 3000
    msgs, stats = assemble_context(
        character_md=big, user_profile=big, summary=big, memory_lines=big,
        relation_line="", history=[{"role": "user", "content": "x" * 50} for _ in range(50)],
        current_message="好", window=2000, max_output=200)
    total = sum(len(m["content"]) for m in msgs)
    # 2000 token 窗口，全 CJK ≈ 1 char/token → 粗估上限（含标签开销）
    assert total < 2600
    assert stats["used"] <= stats["budget"] + 10


def test_history_backfill_when_recall_unused():
    history = [{"role": "user", "content": f"第{i}句"} for i in range(40)]
    msgs, stats = assemble_context(
        character_md="设定", user_profile="", summary="", memory_lines="",
        relation_line="", history=history, current_message="现在",
        window=4000, max_output=200)
    roles = [m for m in msgs if m["role"] in ("user", "assistant")]
    contents = [m["content"] for m in roles]
    assert "现在" == contents[-1]
    assert any("第39句" in c for c in contents)  # 近期历史保留


def test_current_message_always_present():
    msgs, _ = assemble_context(
        character_md="", user_profile="", summary="", memory_lines="",
        relation_line="", history=[], current_message="最后一句")
    assert msgs[-1] == {"role": "user", "content": "最后一句"}


def test_relation_line_included():
    msgs, stats = assemble_context(
        character_md="设定", user_profile="", summary="", memory_lines="",
        relation_line="好感度 0.80，当前情绪 开心", history=[], current_message="在吗")
    assert "【你对用户当前的感觉】" in stats["blocks"]


class TestTruncateAtSentence:
    def test_keeps_tail_on_sentence_boundary(self):
        text = ("第一句话结束了。第二句话也很长，中间有逗号，最后还是结束了！"
                "第三句超短。第四句又是很长的一段内容，包含很多字符用来填满预算，终于结束了。")
        out = truncate_at_sentence(text, 10)
        assert out.startswith("…")
        assert len(out) < len(text)

    def test_no_truncation_when_short(self):
        assert truncate_at_sentence("短句。", 100) == "短句。"


class TestRotateReason:
    def test_empty_never_rotates(self):
        assert rotate_reason([]) is None

    def test_max_messages(self):
        from app.config import SESSION_MAX_MESSAGES
        msgs = [{"created_at": "2026-11-26T10:00:00"}] * SESSION_MAX_MESSAGES
        assert rotate_reason(msgs) == "max_messages"

    def test_cross_day(self):
        msgs = [{"created_at": "2020-01-01T10:00:00"}]
        assert rotate_reason(msgs) == "cross_day"

    def test_today_ok(self):
        from datetime import datetime
        msgs = [{"created_at": datetime.now().isoformat(timespec="seconds")}]
        assert rotate_reason(msgs) is None
