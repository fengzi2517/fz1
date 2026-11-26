"""token 估算 / 分词 / BM25 / 余弦 / 衰减测试"""
from datetime import datetime, timedelta

from app.core.textutil import (
    bm25_score, cosine, day_decay, estimate_tokens, token_overlap, tokenize)


class TestTokens:
    def test_cjk_one_char_one_token(self):
        # 4 个汉字 + 1 固定开销
        assert estimate_tokens("你好世界") == 5

    def test_latin_div_four(self):
        assert estimate_tokens("abcdefgh") == 3

    def test_monotonic(self):
        assert estimate_tokens("你好世界你好世界") > estimate_tokens("你好")

    def test_mixed(self):
        assert estimate_tokens("你好 abcd") >= 3

    def test_empty(self):
        assert estimate_tokens("") == 0


class TestTokenize:
    def test_chinese_words(self):
        toks = tokenize("我 喜欢 咖啡")
        assert "喜欢" in toks and "咖啡" in toks

    def test_single_chars_dropped(self):
        toks = tokenize("我 喜欢 咖啡")
        assert all(len(t) > 1 for t in toks)

    def test_english_words(self):
        assert "coffee" in tokenize("I love coffee")


class TestBm25:
    def test_relevant_doc_scores_higher(self):
        d1 = ["咖啡", "喜欢"]
        d2 = ["天气", "很好"]
        df = {"咖啡": 1, "喜欢": 1, "天气": 1, "很好": 1}
        q = ["喜欢", "咖啡"]
        assert bm25_score(q, d1, df, 2, 2.0) > bm25_score(q, d2, df, 2, 2.0)

    def test_no_overlap_zero(self):
        assert bm25_score(["猫"], ["狗"], {"狗": 1}, 1, 1.0) == 0

    def test_term_freq_saturates(self):
        df = {"咖啡": 1}
        a = bm25_score(["咖啡"], ["咖啡"] * 10, df, 1, 10.0)
        b = bm25_score(["咖啡"], ["咖啡"] * 40, df, 1, 40.0)
        assert b < a * 3  # k1 饱和，非线性增长


class TestCosine:
    def test_same_vector(self):
        assert abs(cosine([1, 2, 3], [1, 2, 3]) - 1.0) < 1e-6

    def test_orthogonal(self):
        assert cosine([1, 0], [0, 1]) == 0

    def test_zero_vector(self):
        # 零向量返回负哨兵（调用方按 c > 0 过滤）
        assert cosine([0, 0], [1, 1]) < 0


def test_day_decay():
    now = datetime.now().isoformat(timespec="seconds")
    assert abs(day_decay(now) - 1.0) < 0.01
    half = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    assert 0.4 < day_decay(half) < 0.6


def test_token_overlap():
    assert token_overlap("我喜欢咖啡", "他喜欢咖啡") > 0
    assert token_overlap("aaa", "bbb") == 0
