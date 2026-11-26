"""P2-3 群聊调度器测试（纯逻辑）"""
import random

from app.services.group_service import GroupScheduler


def M(cid, ta=50, name=None, nickname=""):
    return {"id": cid, "name": name or cid, "nickname": nickname,
            "talkativeness": ta}


S = GroupScheduler()
MEMBERS = [M("c_mei", 20, "小美"), M("c_qiang", 90, "阿强"),
           M("c_ling", 50, "阿灵")]


class TestMention:
    def test_at_mention_wins_over_talkativeness(self):
        picked = S.pick(MEMBERS, "@小美 你怎么看", [], [], mode="auto")
        assert [m["id"] for m in picked] == ["c_mei"]

    def test_nickname_mention(self):
        members = MEMBERS + [M("c_cry", 10, "小翠", nickname="美美")]
        picked = S.pick(members, "美美 在吗", [], [], mode="auto")
        assert [m["id"] for m in picked] == ["c_cry"]

    def test_whole_word_name_hit(self):
        picked = S.pick(MEMBERS, "小美 过来一下", [], [], mode="auto")
        assert any(m["id"] == "c_mei" for m in picked)

    def test_mention_but_muted(self):
        picked = S.pick(MEMBERS, "@小美 说话", [], ["c_mei"], mode="auto")
        assert picked == [] or all(m["id"] != "c_mei" for m in picked)


class TestModes:
    def test_target_mode(self):
        picked = S.pick(MEMBERS, "随便", [], [], mode="target",
                        target_id="c_qiang")
        assert [m["id"] for m in picked] == ["c_qiang"]

    def test_target_muted_returns_empty(self):
        assert S.pick(MEMBERS, "x", [], ["c_qiang"], mode="target",
                      target_id="c_qiang") == []

    def test_all_mode_rotates_by_global_turns(self):
        order = [m["id"] for m in S.pick(MEMBERS, "x", [], [], mode="all",
                                         total_user_turns=0)]
        assert order == ["c_mei", "c_qiang", "c_ling"]
        order = [m["id"] for m in S.pick(MEMBERS, "x", [], [], mode="all",
                                         total_user_turns=4)]
        assert order == ["c_qiang", "c_ling", "c_mei"]

    def test_all_mode_skips_muted(self):
        order = [m["id"] for m in S.pick(MEMBERS, "x", [], ["c_qiang"],
                                         mode="all", total_user_turns=1)]
        assert order == ["c_ling", "c_mei"]

    def test_all_muted(self):
        assert S.pick(MEMBERS, "x", [], ["c_mei", "c_qiang", "c_ling"],
                      mode="auto") == []


class TestAutoWeights:
    def test_deterministic_with_seeded_rng(self):
        a = [m["id"] for m in S.pick(MEMBERS, "今天聊点什么", [], [], mode="auto",
                                     speakers=2, rng=random.Random(42))]
        b = [m["id"] for m in S.pick(MEMBERS, "今天聊点什么", [], [], mode="auto",
                                     speakers=2, rng=random.Random(42))]
        assert a == b

    def test_zero_talkativeness_stays_silent(self):
        quiet = [M("c_quiet", 0)]
        assert S.pick(quiet, "有人吗", [], [], mode="auto",
                      rng=random.Random(1)) == []

    def test_max_speakers_respected(self):
        many = [M(f"c{i}", 100) for i in range(5)]
        picked = S.pick(many, "聊聊聊聊", [], [], mode="auto", speakers=2,
                        rng=random.Random(7))
        assert len(picked) <= 2

    def test_no_duplicates(self):
        many = [M(f"c{i}", 100) for i in range(3)]
        picked = S.pick(many, "聊聊聊聊", [], [], mode="auto", speakers=3,
                        rng=random.Random(3))
        ids = [m["id"] for m in picked]
        assert len(ids) == len(set(ids))

    def test_recent_speakers_downweighted(self):
        """近 6 条 assistant 发言者权重×0.4：多次采样下更不容易再被选中"""
        many = [M("c_a", 100), M("c_b", 100)]
        history = [{"role": "assistant", "character_id": "c_a", "content": "x"}
                   for _ in range(3)]
        picks_b = 0
        for seed in range(60):
            picked = S.pick(many, "随便说说", history, [], mode="auto",
                            speakers=1, rng=random.Random(seed))
            if picked and picked[0]["id"] == "c_b":
                picks_b += 1
        assert picks_b > 30  # c_a 被显著压制


class TestMentionedHelper:
    def test_substring_at(self):
        assert S._mentioned(M("c_x", name="小红"), "@小红你好")

    def test_not_mentioned(self):
        assert not S._mentioned(M("c_x", name="小红"), "今天天气不错")
