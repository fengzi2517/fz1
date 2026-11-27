"""蒸馏服务 v2（P1-6 事务化 + P1-1 画像闭环 + P2-6 关系更新）

管线：新消息 → 摘要 → 记忆提取 → 冲突消解决策（只读召回+LLM）→ 关系/画像更新
     → 【单个 BEGIN IMMEDIATE 事务】写摘要、推进游标、落记忆/画像/关系
LLM 全部在事务外执行；事务失败则 job 重试，不产生半截状态（修 §3.1 蒸馏丢记忆）。
"""
import asyncio
import logging
from typing import List, Optional

from app.core.metrics import metrics
from app.db import dumps, get_db, now_iso
from app.llm.adapter import llm_adapter
from app.llm.prompts import (
    SUMMARY_PROMPT, EXTRACT_MEMORY_PROMPT, GROUP_MEMORY_EXTRACT_PROMPT,
    PROFILE_UPDATE_PROMPT, RELATION_UPDATE_PROMPT, RESOLVE_MEMORY_PROMPT,
)
from app.services.memory_service import memory_service
from app.store import store

WORKER_POLL_INTERVAL = 2.0

logger = logging.getLogger("app.distill")


class DistillService:

    def __init__(self):
        self._worker_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    # ---------- 触发入口（四路触发保留，改为入队） ----------

    def request(self, user_id: str, session_id: str, kind: str = "chat") -> int:
        total = store.count_messages(session_id)
        sess = store.get_session(session_id)
        cursor = sess["distilled_upto"] if sess else 0
        if total - cursor < 2:
            return 0
        return store.enqueue_job(session_id, kind, cursor, total)

    # ---------- worker ----------

    def start_worker(self):
        if self._worker_task is None or self._worker_task.done():
            store.reset_running_jobs()
            self._stop.clear()
            self._worker_task = asyncio.create_task(self._loop())
            logger.info("蒸馏 worker 已启动（轮询 %ss）", WORKER_POLL_INTERVAL)

    async def stop_worker(self):
        self._stop.set()
        if self._worker_task:
            try:
                await asyncio.wait_for(self._worker_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._worker_task.cancel()
            self._worker_task = None
            logger.info("蒸馏 worker 已停止")

    async def _loop(self):
        while not self._stop.is_set():
            job = store.claim_job()
            if job is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=WORKER_POLL_INTERVAL)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                await self.run_job(job)
                store.finish_job(job["id"], ok=True)
                metrics.observe_distill(True)
                logger.info("蒸馏完成 job=%s session=%s 区间=(%s,%s]",
                            job["id"], job["session_id"],
                            job["range_start"], job["range_end"])
            except Exception as e:
                # 事务未提交 → 状态零污染；游标未推进，下次触发或巡检会重试
                store.finish_job(job["id"], ok=False, error=str(e)[:300])
                metrics.observe_distill(False)
                logger.exception("蒸馏失败 job=%s session=%s: %s",
                                 job["id"], job["session_id"], e)

    # ---------- 核心管线 ----------

    async def run_job(self, job: dict):
        session_id = job["session_id"]
        kind = job["kind"]
        start, end = job["range_start"], job["range_end"]
        sess = store.get_session(session_id)
        if not sess:
            return
        user_id = sess["user_id"]
        messages = [m for m in store.get_messages(session_id, limit=100000)
                    if start < m["seq"] <= end]
        # 打断的半截话不进蒸馏（修 G8）：过滤后为空就跳过本批，绝不回捞
        transcript_msgs = [m for m in messages
                           if not m.get("flags", {}).get("interrupted")]
        if not transcript_msgs:
            return
        transcript = self._render_transcript(transcript_msgs, with_name=(kind == "group"))
        if kind == "group":
            plan = await self._plan_group(user_id, sess, transcript, messages)
        else:
            plan = await self._plan_chat(user_id, sess, transcript)
        self._apply_plan(sess, kind, end, plan)

    def _render_transcript(self, msgs: List[dict], with_name: bool) -> str:
        lines = []
        for m in msgs:
            if m["role"] == "user":
                lines.append(f"用户: {m['content']}")
            elif with_name and m.get("name"):
                lines.append(f"{m['name']}: {m['content']}")
            else:
                lines.append(f"角色: {m['content']}")
        return "\n".join(lines)

    async def _plan_chat(self, user_id: str, sess: dict, transcript: str) -> dict:
        summary = (await llm_adapter.chat(
            [{"role": "user", "content": SUMMARY_PROMPT.format(history=transcript)}],
            model="distill", temperature=0.5)).get("content", "").strip()
        if not summary:
            raise RuntimeError("摘要生成失败")
        extracted = await llm_adapter.chat_json(
            [{"role": "user", "content": EXTRACT_MEMORY_PROMPT.format(summary=summary)}],
            default={"user_preferences": [], "character_relationship": []})
        evidence_ids = self._evidence(sess, transcript)
        memory_ops = []
        for item in extracted.get("user_preferences", []) or []:
            memory_ops.append(await self._decide(
                user_id, "user", item.strip(), sess.get("character_id"), None))
        for item in extracted.get("character_relationship", []) or []:
            if item.strip():
                memory_ops.append(await self._decide(
                    user_id, "character", item.strip(), sess.get("character_id"), None))
        # 画像闭环（修 write_user_profile 无调用方）
        profile_ops = []
        current = memory_service.get_profile(user_id)
        try:
            pu = await llm_adapter.chat_json(
                [{"role": "user", "content": PROFILE_UPDATE_PROMPT.format(
                    profile=current, summary=summary)}],
                default={"changed": False, "profile": current})
            if pu.get("changed") and pu.get("profile") and pu["profile"] != current:
                profile_ops.append(pu["profile"].strip())
        except Exception:
            pass
        # 关系状态更新
        relation = None
        cid = sess.get("character_id")
        if cid:
            try:
                cur = store.get_relation(user_id, cid)
                rel = await llm_adapter.chat_json(
                    [{"role": "user", "content": RELATION_UPDATE_PROMPT.format(
                        character_name=self._char_name(user_id, cid),
                        affinity=cur["affinity"], emotion=cur["emotion"],
                        stage=cur["stage"], turn=transcript[-1200:])}],
                    default={})
                if rel.get("affinity") is not None or rel.get("emotion"):
                    relation = {"character_id": cid,
                                "affinity": min(1.0, max(0.0, float(rel.get("affinity", cur["affinity"])))),
                                "emotion": str(rel.get("emotion", cur["emotion"]))[:20],
                                "stage": str(rel.get("stage", cur["stage"]))[:20]}
            except Exception:
                pass
        return {"summary": summary, "memory_ops": memory_ops,
                "profile_ops": profile_ops, "relation": relation, "evidence_ids": evidence_ids}

    async def _plan_group(self, user_id: str, sess: dict, transcript: str,
                          messages: List[dict]) -> dict:
        group = store.get_group(user_id, sess["group_id"])
        if not group:
            raise RuntimeError("群不存在")
        summary = (await llm_adapter.chat(
            [{"role": "user", "content": SUMMARY_PROMPT.format(history=transcript)}],
            model="distill", temperature=0.4)).get("content", "").strip()
        if not summary:
            raise RuntimeError("群聊概要生成失败")
        memory_ops = []
        for cid in group["member_ids"]:
            name = self._char_name(user_id, cid)
            extracted = await llm_adapter.chat_json(
                [{"role": "user", "content": GROUP_MEMORY_EXTRACT_PROMPT.format(
                    character_name=name, transcript=transcript)}],
                default={})
            for item in extracted.get("user_preferences", []) or []:
                if item.strip():
                    memory_ops.append(await self._decide(user_id, "user", item.strip(), None, None))
            for item in extracted.get("character_relationship", []) or []:
                if item.strip():
                    memory_ops.append(await self._decide(
                        user_id, "character", item.strip(), cid, None))
            for item in extracted.get("group_events", []) or []:
                if item.strip():
                    memory_ops.append(await self._decide(
                        user_id, "character", f"（群聊见闻）{item.strip()}", cid, sess["group_id"]))
        return {"summary": summary, "group_id": sess["group_id"],
                "memory_ops": memory_ops, "profile_ops": [], "relation": None,
                "evidence_ids": self._evidence(sess, transcript)}

    async def _decide(self, user_id: str, scope: str, content: str,
                      character_id: Optional[str], group_id: Optional[str]) -> dict:
        """只读阶段完成冲突消解决策，写阶段在事务里执行"""
        similar = memory_service._find_similar(user_id, scope, content, character_id, group_id)
        if not similar:
            return {"action": "ADD", "content": content, "scope": scope,
                    "character_id": character_id, "group_id": group_id, "importance": 0.5}
        similar_desc = "\n".join(
            f"- id={e['id']} (重要度{e.get('importance', 0.5):.1f}) {e['content']}" for e in similar)
        decision = await llm_adapter.chat_json(
            [{"role": "user", "content": RESOLVE_MEMORY_PROMPT.format(
                scope=scope, new_content=content, similar=similar_desc)}],
            default={"action": "NOOP"})
        action = (decision.get("action") or "NOOP").upper()
        if action not in ("ADD", "UPDATE", "DELETE", "MERGE", "NOOP"):
            action = "NOOP"
        return {"action": action, "content": content, "scope": scope,
                "character_id": character_id, "group_id": group_id,
                "final_content": (decision.get("final_content") or content).strip() or content,
                "target_ids": [int(i) for i in decision.get("target_ids") or []
                               if any(e["id"] == int(i) for e in similar)],
                "importance": float(decision.get("importance") or 0.5)}

    def _apply_plan(self, sess: dict, kind: str, upto: int, plan: dict):
        """单事务落库：游标与全部记忆/摘要/画像/关系写入原子提交"""
        session_id = sess["id"]
        user_id = sess["user_id"]
        with get_db().transaction() as conn:
            header = "## 群聊概要" if kind == "group" else "## 会话摘要"
            if kind == "group":
                row = conn.execute("SELECT summary FROM groups WHERE id=?",
                                   (plan["group_id"],)).fetchone()
                old = (row["summary"] if row else "") or header
                conn.execute(
                    "UPDATE groups SET summary=?, summary_upto=?, distilled_upto=? WHERE id=?",
                    (self._roll(old, plan["summary"], upto), upto, upto, plan["group_id"]))
            else:
                row = conn.execute("SELECT summary FROM sessions WHERE id=?",
                                   (session_id,)).fetchone()
                old = (row["summary"] if row else "") or header
                conn.execute("UPDATE sessions SET summary=?, distilled_upto=? WHERE id=?",
                             (self._roll(old, plan["summary"], upto), upto, session_id))
            for op in plan["memory_ops"]:
                self._apply_memory_op(conn, user_id, op, plan.get("evidence_ids") or [])
            for profile in plan["profile_ops"]:
                conn.execute(
                    "INSERT INTO user_profile(user_id, content, updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET content=excluded.content, "
                    "updated_at=excluded.updated_at", (user_id, profile, now_iso()))
            rel = plan.get("relation")
            if rel:
                conn.execute(
                    "INSERT INTO relations(user_id, character_id, affinity, emotion, stage, drift, updated_at) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id, character_id) DO UPDATE SET "
                    "affinity=excluded.affinity, emotion=excluded.emotion, stage=excluded.stage, "
                    "updated_at=excluded.updated_at",
                    (user_id, rel["character_id"], rel["affinity"], rel["emotion"],
                     rel["stage"], dumps({}), now_iso()))

    def _apply_memory_op(self, conn, user_id: str, op: dict, evidence_ids: List[int]):
        action = op["action"]
        if action == "NOOP":
            return
        content = op.get("final_content") or op["content"]
        if action == "ADD":
            conn.execute(
                "INSERT INTO memory_entries(user_id, scope, character_id, group_id, block_label, "
                "content, importance, evidence_ids, tokens, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, op["scope"], op.get("character_id"), op.get("group_id"), "facts",
                 content, op.get("importance", 0.5), dumps(evidence_ids),
                 " ".join(self._tok(content)), now_iso(), now_iso()))
        elif action == "UPDATE" and op.get("target_ids"):
            conn.execute(
                "UPDATE memory_entries SET content=?, importance=?, updated_at=? WHERE id=?",
                (content, op.get("importance", 0.5), now_iso(), op["target_ids"][0]))
            self._log(conn, user_id, "UPDATE", op["target_ids"][0], content)
        elif action == "MERGE" and op.get("target_ids"):
            conn.execute(
                "UPDATE memory_entries SET content=?, importance=?, updated_at=? WHERE id=?",
                (content, op.get("importance", 0.5), now_iso(), op["target_ids"][0]))
            for tid in op["target_ids"][1:]:
                conn.execute("UPDATE memory_entries SET status='superseded', updated_at=? WHERE id=?",
                             (now_iso(), tid))
            self._log(conn, user_id, "MERGE", op["target_ids"][0], content)
        elif action == "DELETE":
            for tid in op.get("target_ids", []):
                conn.execute("UPDATE memory_entries SET status='superseded', updated_at=? WHERE id=?",
                             (now_iso(), tid))
                self._log(conn, user_id, "DELETE", tid, "被新事实替代")
            conn.execute(
                "INSERT INTO memory_entries(user_id, scope, character_id, group_id, block_label, "
                "content, importance, evidence_ids, tokens, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (user_id, op["scope"], op.get("character_id"), op.get("group_id"), "facts",
                 op["content"], op.get("importance", 0.5), dumps(evidence_ids),
                 " ".join(self._tok(op["content"])), now_iso(), now_iso()))

    @staticmethod
    def _log(conn, user_id, action, entry_id, detail):
        conn.execute(
            "INSERT INTO memory_log(user_id, action, entry_id, detail, created_at) VALUES(?,?,?,?,?)",
            (user_id, action, entry_id, detail[:200], now_iso()))

    @staticmethod
    def _tok(text: str):
        from app.core.textutil import tokenize
        return tokenize(text)

    def _char_name(self, user_id: str, cid: str) -> str:
        ch = store.get_character(user_id, cid)
        return ch["name"] if ch else cid

    def _evidence(self, sess: dict, transcript: str) -> List[int]:
        """取本轮参与摘要的消息 id 作为证据链（最近 40 条封顶）"""
        rows = get_db().query(
            "SELECT id FROM messages WHERE session_id=? AND content!=\"\" ORDER BY seq DESC LIMIT 40",
            (sess["id"],))
        return [r["id"] for r in rows]

    @staticmethod
    def _roll(old_summary: str, new_segment: str, upto: int) -> str:
        """滚动摘要：追加片段，保留尾部（注入时再按预算截）"""
        body = (old_summary or "").strip()
        if body.startswith("#"):
            body = body.split("\n", 1)[-1].strip() if "\n" in body else body
        seg = f"\n\n（第 {upto} 条为止）\n{new_segment}"
        merged = (body + seg)[-12000:]
        return merged.strip()

    # ---------- 兼容旧接口 ----------

    async def distill_session(self, user_id: str, character_id: str, session_id: str):
        """旧调用点兼容：同步跑完一次蒸馏（测试/巡检可用）"""
        job_id = self.request(user_id, session_id, "chat")
        if not job_id:
            return
        job = store.claim_job()
        if job and job["id"] == job_id:
            await self.run_job(job)
            store.finish_job(job_id, ok=True)


distill_service = DistillService()
