"""文本工具：token 估算、中文分词、相似度"""
import math
from typing import List

import jieba


def estimate_tokens(text: str) -> int:
    """近似 token：CJK 每字 1，其他每 4 字符 1"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f')
    return cjk + max(0, (len(text) - cjk)) // 4 + 1


def tokenize(text: str) -> List[str]:
    """jieba 分词，保留长度>1 的词（与旧版行为一致）"""
    return [w for w in jieba.lcut(text or "") if len(w) > 1 and w.strip()]


def token_overlap(a: str, b: str) -> float:
    """a 与 b 的词面重合率（以 a 为基准），用于词面去重兜底"""
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


def bm25_score(query_tokens: List[str], doc_tokens: List[str],
               df: dict, n_docs: int, avgdl: float) -> float:
    """轻量 BM25（k1=1.5, b=0.75），语料统计由调用方预计算"""
    if not doc_tokens or not query_tokens or n_docs <= 0:
        return 0.0
    k1, b = 1.5, 0.75
    tf = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1
    dl = len(doc_tokens)
    score = 0.0
    for q in set(query_tokens):
        f = tf.get(q, 0)
        if f == 0:
            continue
        idf = math.log(1 + (n_docs - df.get(q, 0) + 0.5) / (df.get(q, 0) + 0.5))
        score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / max(avgdl, 1e-6)))
    return score


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return -1.0
    return dot / (na * nb)


def day_decay(created_at: str, half_life_days: float = 30.0) -> float:
    """重要性中性的时间衰减（指数，半衰期默认 30 天）"""
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        return 1.0
    days = max(0.0, (datetime.now() - dt).total_seconds() / 86400)
    return 0.5 ** (days / half_life_days)
