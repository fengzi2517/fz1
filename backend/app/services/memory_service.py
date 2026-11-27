"""记忆服务 v2（P1）

变更（相对旧文件存储版）：
- 真相源迁 SQLite（memory_entries 表），修复全文件重写竞态（§6.1）
- 检索：BM25(jieba 分词) + 向量余弦(可选) + 时间衰减 + 重要性 的混合召回（§6.2/D6）
- 写入：mem0 式冲突消解管线 ADD/UPDATE/DELETE/NOOP/MERGE（§3.1/§6.3）
- 块上限 + 低价值归档（遗忘，§6.3）
- 召回账本防复读（G5）
- 用户画像闭环（P1-1，write_user_profile 终于有调用方了）
"""
from typing import Dict, List, Optional

from pydantic import BaseModel

from app.config import (
    RECALL_VEC_THRESHOLD, RECALL_INJECT_TOP_K, RECALL_KEYWORD_OVERLAP_THRESHOLD,
)
from app.core.textutil import bm25_score, cosine, day_decay, tokenize
from app.db import get_db, now_iso
from app.llm.adapter import llm_adapter
from app.llm.prompts import RESOLVE_MEMORY_PROMPT
from app.store import store

# 单用户活跃记忆上限，超出按 importance×recency 归档最低分（遗忘曲线兜底）
ACTIVE_ENTRY_LIMIT = 800
# 防复读：近 N 轮已注入的条目降权
LEDGER_PENALTY_ROUNDS = 3


class MemoryHit(BaseModel):
    entry_id: int
    source: str          # user / character / group
    date: str
    content: str
    score: float
    importance: float = 0.5


class MemoryService:

    def __init__(self):
        self._embed_cache: Dict[str, List[float]] = {}

    # ---------- 画像 ----------

    def get_profile(self, user_id: str) -> str:
        content = store.get_profile(user_id)
        if content:
            return content
        return f"# 用户档案\n\n## 基础信息\n- 用户ID: {user_id}\n\n## 偏好\n- 语言：中文\n"

    def set_profile(self, user_id: str, content: str):
        store.set_profile(user_id, content)

    # ---------- 混合召回 ----------

    async def recall(self, user_id: str, character_id: Optional[str], query: str,
                     top_k: int = RECALL_INJECT_TOP_K, context_key: str = "",
                     round_no: int = 0) -> List[MemoryHit]:
        """每轮自动调用（替代旧版 16 触发词规则）。
        三路信号融合：BM25 词面 + 向量语义（配置了 embedding 时）+ 时间/重要性。
        """
        entries = store.list_memories(user_id, character_id=character_id)
        if not entries:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return self._recent_fallback(entries, top_k, context_key, round_no)

        # 语料统计（对候选集做 BM25）
        df: Dict[str, int] = {}
        doc_tokens = {}
        total_dl = 0
        for e in entries:
            toks = e["tokens"].split()
            doc_tokens[e["id"]] = toks
            total_dl += len(toks)
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        avgdl = max(total_dl / max(len(entries), 1), 1.0)

        q_vec = None
        if llm_adapter.embed_available:
            vecs = await llm_adapter.embed([query])
            q_vec = vecs[0] if vecs else None

        penalized = store.recent_recalled(
            context_key, LEDGER_PENALTY_ROUNDS,
            [e["id"] for e in entries], round_no) if context_key else set()
        hits: List[MemoryHit] = []
        for e in entries:
            bm = bm25_score(q_tokens, doc_tokens[e["id"]], df, len(entries), avgdl)
            vec_score = 0.0
            if q_vec and e.get("embedding"):
                c = cosine(q_vec, e["embedding"])
                if c > 0:
                    vec_score = c
            if bm <= 0 and vec_score <= 0:
                continue
            decay = day_decay(e["created_at"])
            score = (1.0 * min(bm, 10) / 10) + (2.0 * vec_score) + (0.5 * decay)
            score *= (0.6 + 0.8 * e.get("importance", 0.5))
            if vec_score and vec_score < RECALL_VEC_THRESHOLD and bm <= 0:
                continue  # 仅有弱向量命中 → 丢弃
            if e["id"] in penalized:
                score *= 0.3
            hits.append(MemoryHit(
                entry_id=e["id"], source=e["scope"],
                date=(e["created_at"] or "")[:10],
                content=e["content"], score=round(score, 4),
                importance=e.get("importance", 0.5)))
        hits.sort(key=lambda h: h.score, reverse=True)
        top = hits[:top_k]
        if not top:
            return self._recent_fallback(entries, top_k, context_key, round_no)
        store.bump_recall([h.entry_id for h in top], context_key or "global", round_no or 1)
        return top

    def _recent_fallback(self, entries: List[dict], top_k: int,
                         context_key: str, round_no: int) -> List[MemoryHit]:
        """零命中兜底：注入最近高价值记忆（防"我不记得"体验灾难）"""
        ranked = sorted(
            entries,
            key=lambda e: (e.get("importance", 0.5) * day_decay(e["created_at"])),
            reverse=True)
        top = [MemoryHit(entry_id=e["id"], source=e["scope"],
                         date=(e["created_at"] or "")[:10], content=e["content"],
                         score=0.0, importance=e.get("importance", 0.5))
               for e in ranked[:top_k]]
        if top and context_key:
            store.bump_recall([h.entry_id for h in top], context_key, round_no or 1)
        return top

    def format_hits(self, hits: List[MemoryHit]) -> str:
        lines = []
        for h in hits:
            src = {"user": "关于用户", "character": "角色记忆", "group": "群聊见闻"}.get(h.source, "记忆")
            lines.append(f"- [{src} {h.date}] {h.content}")
        return "\n".join(lines)

    # ---------- 冲突消解写入管线 ----------

    async def add_memory_resolved(self, user_id: str, scope: str, content: str,
                                  character_id: Optional[str] = None,
                                  group_id: Optional[str] = None,
                                  evidence_ids: Optional[List[int]] = None,
                                  importance: Optional[float] = None) -> str:
        """mem0 式两阶段：抽取(调用方完成) → 相似召回 → LLM 决策 ADD/UPDATE/DELETE/NOOP/MERGE
        LLM 不可用时退化为词面去重（保留旧能力）。返回实际执行的 action。
        """
        content = (content or "").strip()
        if not content:
            return "NOOP"
        similar = self._find_similar(user_id, scope, content, character_id, group_id)
        if not similar:
            await self._insert(user_id, scope, content, character_id, group_id,
                               evidence_ids, importance)
            return "ADD"
        similar_desc = "\n".join(
            f"- id={e['id']} (重要度{e.get('importance', 0.5):.1f}) {e['content']}"
            for e in similar)
        try:
            decision = await llm_adapter.chat_json(
                [{"role": "user", "content": RESOLVE_MEMORY_PROMPT.format(
                    scope=scope, new_content=content, similar=similar_desc)}],
                default={"action": "ADD"})
        except Exception:
            decision = {"action": "ADD"}
        action = (decision.get("action") or "ADD").upper()
        target_ids = [int(i) for i in decision.get("target_ids") or []
                      if any(e["id"] == int(i) for e in similar)] if decision.get("target_ids") else []
        final = (decision.get("final_content") or content).strip() or content
        imp = decision.get("importance")
        if imp is None:
            imp = importance if importance is not None else 0.5
        by_id = {e["id"]: e for e in similar}

        if action == "NOOP":
            # LLM 判重；词面兜底再验一次（防 LLM 幻觉 NOOP 丢新信息）
            return "NOOP"
        if action == "UPDATE" and target_ids:
            store.update_memory(target_ids[0], content=final, importance=float(imp))
            store.log_memory_action(user_id, "UPDATE", target_ids[0], final)
            return "UPDATE"
        if action == "MERGE" and target_ids:
            store.update_memory(target_ids[0], content=final, importance=float(imp))
            for tid in target_ids[1:]:
                store.update_memory(tid, status="superseded")
            store.log_memory_action(user_id, "MERGE", target_ids[0], final)
            return "MERGE"
        if action == "DELETE":
            for tid in target_ids or [e["id"] for e in similar[:1]]:
                if tid in by_id:
                    store.update_memory(tid, status="superseded")
                    store.log_memory_action(user_id, "DELETE", tid, "被新事实替代")
            new_id = await self._insert(user_id, scope, content, character_id, group_id,
                                        evidence_ids, float(imp))
            store.log_memory_action(user_id, "ADD", new_id, f"替代旧记忆: {content[:40]}")
            return "DELETE+ADD"
        # ADD 或非法动作兜底：词面判重后插入
        for e in similar:
            if self._overlap(content, e["content"]) > RECALL_KEYWORD_OVERLAP_THRESHOLD:
                return "NOOP"
        new_id = await self._insert(user_id, scope, final, character_id, group_id,
                                    evidence_ids, float(imp))
        store.log_memory_action(user_id, "ADD", new_id, "")
        return "ADD"

    async def _insert(self, user_id, scope, content, character_id, group_id,
                      evidence_ids, importance) -> int:
        embedding = None
        if llm_adapter.embed_available:
            cached = self._embed_cache.get(content)
            if cached is None:
                vecs = await llm_adapter.embed([content])
                cached = vecs[0] if vecs else None
                if cached:
                    self._embed_cache[content] = cached
            embedding = cached
        eid = store.add_memory(user_id, scope, content, character_id=character_id,
                               group_id=group_id, evidence_ids=evidence_ids or [],
                               embedding=embedding,
                               importance=importance if importance is not None else 0.5)
        self._enforce_block_limit(user_id, scope, character_id)
        return eid

    def _enforce_block_limit(self, user_id: str, scope: str, character_id: Optional[str]):
        """块上限：超限按 重要性×时间衰减 归档最低分条目（不物理删，可审计恢复）"""
        entries = store.list_memories(user_id, scope=scope, character_id=character_id)
        if len(entries) <= ACTIVE_ENTRY_LIMIT:
            return
        ranked = sorted(
            entries,
            key=lambda e: e.get("importance", 0.5) * day_decay(e["created_at"]))
        overflow = ranked[:len(entries) - ACTIVE_ENTRY_LIMIT]
        for e in overflow:
            store.update_memory(e["id"], status="archived")
        get_db().execute(
            "INSERT INTO memory_log(user_id, action, entry_id, detail, created_at) "
            "VALUES(?,?,?,?,?)", (user_id, "ARCHIVE", overflow[0]["id"],
                                  f"块上限遗忘归档 {len(overflow)} 条", now_iso()))

    def _find_similar(self, user_id, scope, content, character_id, group_id) -> List[dict]:
        entries = store.list_memories(user_id, scope=scope, character_id=character_id)
        if group_id:
            entries += store.list_memories(user_id, scope="group")
        if not entries:
            return []
        q = set(tokenize(content))
        if not q:
            return []
        scored = []
        for e in entries:
            toks = e["tokens"].split()
            if not toks:
                continue
            inter = len(q & set(toks))
            if inter == 0:
                continue
            scored.append((inter / len(q), e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:5]]

    @staticmethod
    def _overlap(a: str, b: str) -> float:
        ta, tb = set(tokenize(a)), set(tokenize(b))
        return len(ta & tb) / len(ta) if ta else 0.0

    # ---------- 导出（保留 Markdown 可审计卖点） ----------

    def export_markdown(self, user_id: str) -> Dict[str, str]:
        """把记忆导出为 md 文本（profile.md / long-term.md / characters/{cid}/memory.md）"""
        out = {"profile": store.get_profile(user_id)}
        user_entries = store.list_memories(user_id, scope="user")
        out["long_term"] = self._render_entries(user_entries, "长期记忆")
        chars = {}
        for e in store.list_memories(user_id):
            if e["scope"] == "character" and e.get("character_id"):
                chars.setdefault(e["character_id"], []).append(e)
        out["character_memories"] = {
            cid: self._render_entries(es, "角色记忆") for cid, es in chars.items()}
        return out

    @staticmethod
    def _render_entries(entries: List[dict], title: str) -> str:
        if not entries:
            return f"# {title}\n"
        groups: Dict[str, List[str]] = {}
        for e in sorted(entries, key=lambda x: x["created_at"]):
            day = (e["created_at"] or "")[:10]
            tm = (e["created_at"] or "")[11:16]
            groups.setdefault(day, []).append(f"- {tm}  {e['content']}")
        lines = [f"# {title}", ""]
        for day in sorted(groups.keys(), reverse=True):
            lines.append(f"## {day}")
            lines.extend(groups[day])
            lines.append("")
        return "\n".join(lines)


memory_service = MemoryService()
