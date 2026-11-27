"""DAO 层：所有 SQLite 读写集中于此（服务层不直接写 SQL）"""
from typing import List, Optional

from app.core.textutil import estimate_tokens, tokenize
from app.db import Database, dumps, get_db, loads, now_iso
from app.core.security import new_id


class Store:
    def __init__(self, db: Optional[Database] = None):
        self._db = db

    @property
    def db(self) -> Database:
        return self._db if self._db is not None else get_db()

    def bind(self, db: Database):
        self._db = db

    # ---------- users & profile ----------

    def create_user(self, username: str, pw_hash: str) -> str:
        uid = new_id("u")
        self.db.execute(
            "INSERT INTO users(id, username, pw_hash, created_at) VALUES(?,?,?,?)",
            (uid, username, pw_hash, now_iso()))
        self.db.execute(
            "INSERT OR IGNORE INTO user_profile(user_id, content, updated_at) VALUES(?,?,?)",
            (uid, "", now_iso()))
        return uid

    def get_user_by_username(self, username: str) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM users WHERE username=?", (username,))
        return dict(row) if row else None

    def get_user(self, user_id: str) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM users WHERE id=?", (user_id,))
        return dict(row) if row else None

    def get_profile(self, user_id: str) -> str:
        row = self.db.query_one("SELECT content FROM user_profile WHERE user_id=?", (user_id,))
        return row["content"] if row else ""

    def set_profile(self, user_id: str, content: str):
        self.db.execute(
            "INSERT INTO user_profile(user_id, content, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at",
            (user_id, content, now_iso()))

    # ---------- characters ----------

    def create_character(self, user_id: str, name: str, profile_md: str = "",
                         description: str = "", tags: List[str] = None,
                         card_json: dict = None, nickname: str = "",
                         talkativeness: int = 50, cid: Optional[str] = None) -> str:
        cid = cid or new_id("c")
        now = now_iso()
        self.db.execute(
            "INSERT INTO characters(id, user_id, name, nickname, card_json, profile_md, "
            "description, tags, talkativeness, voice_config, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, user_id, name, nickname, dumps(card_json or {}), profile_md,
             description, dumps(tags or []), talkativeness, dumps({}), now, now))
        return cid

    def get_character(self, user_id: str, cid: str) -> Optional[dict]:
        row = self.db.query_one(
            "SELECT * FROM characters WHERE id=? AND user_id=?", (cid, user_id))
        if not row:
            return None
        d = dict(row)
        d["tags"] = loads(d["tags"], [])
        d["card_json"] = loads(d["card_json"], {})
        d["voice_config"] = loads(d["voice_config"], {})
        return d

    def list_characters(self, user_id: str) -> List[dict]:
        rows = self.db.query(
            "SELECT * FROM characters WHERE user_id=? ORDER BY created_at", (user_id,))
        out = []
        for row in rows:
            d = dict(row)
            d["tags"] = loads(d["tags"], [])
            d["card_json"] = loads(d["card_json"], {})
            d["voice_config"] = loads(d["voice_config"], {})
            out.append(d)
        return out

    def update_character(self, user_id: str, cid: str, **fields):
        allowed = {"name", "nickname", "card_json", "profile_md", "description",
                   "tags", "talkativeness", "voice_config"}
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k in ("card_json", "tags", "voice_config"):
                v = dumps(v)
            sets.append(f"{k}=?")
            params.append(v)
        if not sets:
            return
        sets.append("updated_at=?")
        params.extend([now_iso(), cid, user_id])
        self.db.execute(
            f"UPDATE characters SET {','.join(sets)} WHERE id=? AND user_id=?",
            tuple(params))

    def delete_character(self, user_id: str, cid: str) -> bool:
        """删除角色及其会话（记忆条目保留为 archived，审计可恢复）"""
        with self.db.transaction() as conn:
            sess = conn.execute(
                "SELECT id FROM sessions WHERE user_id=? AND character_id=?",
                (user_id, cid)).fetchall()
            for s in sess:
                conn.execute("DELETE FROM messages WHERE session_id=?", (s["id"],))
                conn.execute("DELETE FROM sessions WHERE id=?", (s["id"],))
            conn.execute("DELETE FROM characters WHERE id=? AND user_id=?", (cid, user_id))
            conn.execute(
                "UPDATE memory_entries SET status='archived', updated_at=? "
                "WHERE user_id=? AND character_id=? AND status='active'",
                (now_iso(), user_id, cid))
        return True

    # ---------- sessions & messages ----------

    def create_session(self, user_id: str, kind: str = "char", character_id: str = None,
                       group_id: str = None, sid: Optional[str] = None,
                       parent_session_id: str = None, branched_at: int = None,
                       title: str = "") -> str:
        sid = sid or new_id("s")
        self.db.execute(
            "INSERT INTO sessions(id, user_id, kind, character_id, group_id, "
            "parent_session_id, branched_at_message_id, title, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (sid, user_id, kind, character_id, group_id, parent_session_id,
             branched_at, title or sid, now_iso()))
        return sid

    def get_session(self, sid: str) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM sessions WHERE id=?", (sid,))
        return dict(row) if row else None

    def list_sessions(self, user_id: str, character_id: str = None,
                      include_trashed: bool = False) -> List[dict]:
        sql = "SELECT * FROM sessions WHERE user_id=?"
        params: list = [user_id]
        if character_id:
            sql += " AND character_id=?"
            params.append(character_id)
        if not include_trashed:
            sql += " AND status != 'trashed'"
        sql += " ORDER BY COALESCE(last_message_at, created_at) DESC"
        return [dict(r) for r in self.db.query(sql, tuple(params))]

    def latest_session(self, user_id: str, character_id: str) -> Optional[dict]:
        row = self.db.query_one(
            "SELECT * FROM sessions WHERE user_id=? AND character_id=? AND status='active' "
            "ORDER BY COALESCE(last_message_at, created_at) DESC, ROWID DESC LIMIT 1",
            (user_id, character_id))
        return dict(row) if row else None

    def set_session(self, sid: str, **fields):
        allowed = {"status", "title", "summary", "distilled_upto", "last_message_at"}
        sets, params = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                params.append(v)
        if sets:
            params.append(sid)
            self.db.execute(f"UPDATE sessions SET {','.join(sets)} WHERE id=?", tuple(params))

    def append_message(self, session_id: str, role: str, content: str,
                       character_id: str = None, name: str = "",
                       flags: dict = None) -> int:
        """单事务内分配 seq 并插入（修旧版竞态）"""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS mx FROM messages WHERE session_id=?",
                (session_id,)).fetchone()
            seq = row["mx"] + 1
            cur = conn.execute(
                "INSERT INTO messages(session_id, seq, role, character_id, name, "
                "content, flags, token_count, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (session_id, seq, role, character_id, name, content,
                 dumps(flags or {}), estimate_tokens(content), now_iso()))
            conn.execute(
                "UPDATE sessions SET last_message_at=? WHERE id=?", (now_iso(), session_id))
            return cur.lastrowid

    def get_messages(self, session_id: str, limit: int = 2000, offset_after: int = 0) -> List[dict]:
        """seq>offset_after 的消息，取最后 limit 条"""
        rows = self.db.query(
            "SELECT * FROM messages WHERE session_id=? AND seq>? ORDER BY seq",
            (session_id, offset_after))
        out = [self._msg_dict(r) for r in rows]
        return out[-limit:]

    def count_messages(self, session_id: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id=?", (session_id,))
        return row["n"] if row else 0

    def first_message_time(self, session_id: str) -> Optional[str]:
        row = self.db.query_one(
            "SELECT created_at FROM messages WHERE session_id=? ORDER BY seq LIMIT 1",
            (session_id,))
        return row["created_at"] if row else None

    def update_message_content(self, session_id: str, mid: int, content: str):
        self.db.execute(
            "UPDATE messages SET content=?, token_count=?, flags=json_set(flags,'$.edited',1) "
            "WHERE id=? AND session_id=?",
            (content, estimate_tokens(content), mid, session_id))

    def set_candidates(self, session_id: str, mid: int, candidates: List[str], active: int):
        self.db.execute(
            "UPDATE messages SET candidates=?, active_candidate=?, content=? "
            "WHERE id=? AND session_id=?",
            (dumps(candidates), active,
             candidates[active] if 0 <= active < len(candidates) else "", mid, session_id))

    def mark_flag(self, session_id: str, mid: int, key: str, value=1):
        row = self.db.query_one("SELECT flags FROM messages WHERE id=? AND session_id=?",
                                (mid, session_id))
        flags = loads(row["flags"], {}) if row else {}
        flags[key] = value
        self.db.execute("UPDATE messages SET flags=? WHERE id=?", (dumps(flags), mid))

    @staticmethod
    def _msg_dict(row) -> dict:
        d = dict(row)
        d["candidates"] = loads(d.get("candidates"), [])
        d["flags"] = loads(d.get("flags"), {})
        return d

    # ---------- memory ----------

    def add_memory(self, user_id: str, scope: str, content: str,
                   character_id: str = None, group_id: str = None,
                   block_label: str = "facts", importance: float = 0.5,
                   evidence_ids: List[int] = None, embedding: List[float] = None,
                   conn=None) -> int:
        now = now_iso()
        sql = ("INSERT INTO memory_entries(user_id, scope, character_id, group_id, "
               "block_label, content, importance, evidence_ids, embedding, tokens, "
               "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)")
        params = (user_id, scope, character_id, group_id, block_label, content,
                  importance, dumps(evidence_ids or []),
                  dumps(embedding) if embedding else None,
                  " ".join(tokenize(content)), now, now)
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self.db.execute(sql, params)

    def list_memories(self, user_id: str, scope: str = None, character_id: str = None,
                      status: str = "active", limit: int = 5000) -> List[dict]:
        sql = "SELECT * FROM memory_entries WHERE user_id=?"
        params: list = [user_id]
        if scope:
            sql += " AND scope=?"
            params.append(scope)
        if character_id:
            sql += " AND (character_id=? OR scope='user')"
            params.append(character_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [self._mem_dict(r) for r in self.db.query(sql, tuple(params))]

    def get_memory(self, entry_id: int) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM memory_entries WHERE id=?", (entry_id,))
        return self._mem_dict(row) if row else None

    def update_memory(self, entry_id: int, content: str = None, importance: float = None,
                      status: str = None, embedding: List[float] = None):
        sets, params = ["updated_at=?"], [now_iso()]
        if content is not None:
            sets.append("content=?")
            params.append(content)
            sets.append("tokens=?")
            params.append(" ".join(tokenize(content)))
        if importance is not None:
            sets.append("importance=?")
            params.append(importance)
        if status is not None:
            sets.append("status=?")
            params.append(status)
        if embedding is not None:
            sets.append("embedding=?")
            params.append(dumps(embedding))
        params.append(entry_id)
        self.db.execute(f"UPDATE memory_entries SET {','.join(sets)} WHERE id=?",
                        tuple(params))

    def bump_recall(self, entry_ids: List[int], context_key: str, round_no: int):
        if not entry_ids:
            return
        now = now_iso()
        with self.db.transaction() as conn:
            for eid in entry_ids:
                owner = conn.execute(
                    "SELECT user_id FROM memory_entries WHERE id=?", (eid,)).fetchone()
                conn.execute(
                    "UPDATE memory_entries SET last_recalled_at=?, recall_count=recall_count+1 "
                    "WHERE id=?", (now, eid))
                if owner:
                    conn.execute(
                        "INSERT INTO recall_ledger(user_id, context_key, entry_id, last_round, hits) "
                        "VALUES(?,?,?,?,1) ON CONFLICT(user_id, context_key, entry_id) "
                        "DO UPDATE SET last_round=excluded.last_round, hits=hits+1",
                        (owner["user_id"], context_key, eid, round_no))

    def recent_recalled(self, context_key: str, within_rounds: int,
                        entry_ids: List[int], current_round: int = 0) -> set:
        """召回账本：最近 within_rounds 轮内已注入过的条目（防同一记忆反复浮现）"""
        if not entry_ids:
            return set()
        marks = ",".join("?" * len(entry_ids))
        rows = self.db.query(
            f"SELECT entry_id FROM recall_ledger WHERE context_key=? "
            f"AND last_round >= ? AND entry_id IN ({marks})",
            (context_key, current_round - within_rounds, *entry_ids))
        return {r["entry_id"] for r in rows}

    def log_memory_action(self, user_id: str, action: str, entry_id: int, detail: str = ""):
        self.db.execute(
            "INSERT INTO memory_log(user_id, action, entry_id, detail, created_at) "
            "VALUES(?,?,?,?,?)", (user_id, action, entry_id, detail, now_iso()))

    def memory_log(self, user_id: str, limit: int = 200) -> List[dict]:
        rows = self.db.query(
            "SELECT * FROM memory_log WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit))
        return [dict(r) for r in rows]

    @staticmethod
    def _mem_dict(row) -> dict:
        d = dict(row)
        d["evidence_ids"] = loads(d.get("evidence_ids"), [])
        d["embedding"] = loads(d.get("embedding"), None)
        return d

    # ---------- groups ----------

    def create_group(self, user_id: str, name: str, member_ids: List[str],
                     gid: Optional[str] = None) -> str:
        gid = gid or new_id("g")
        sid = self.create_session(user_id, kind="group", group_id=gid)
        self.db.execute(
            "INSERT INTO groups(id, user_id, name, member_ids, created_at) VALUES(?,?,?,?,?)",
            (gid, user_id, name, dumps(member_ids), now_iso()))
        self.db.execute("UPDATE sessions SET group_id=?, character_id=NULL WHERE id=?",
                        (gid, sid))
        return gid

    def get_group(self, user_id: str, gid: str) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM groups WHERE id=? AND user_id=?", (gid, user_id))
        if not row:
            return None
        d = dict(row)
        d["member_ids"] = loads(d["member_ids"], [])
        d["muted_ids"] = loads(d["muted_ids"], [])
        d["scheduler"] = loads(d["scheduler"], {})
        sess = self.db.query_one(
            "SELECT id FROM sessions WHERE user_id=? AND kind='group' AND group_id=? "
            "AND status='active' ORDER BY created_at DESC LIMIT 1", (user_id, gid))
        d["session_id"] = sess["id"] if sess else None
        return d

    def list_groups(self, user_id: str) -> List[dict]:
        rows = self.db.query("SELECT * FROM groups WHERE user_id=? ORDER BY created_at",
                             (user_id,))
        out = []
        for row in rows:
            d = dict(row)
            d["member_ids"] = loads(d["member_ids"], [])
            d["muted_ids"] = loads(d["muted_ids"], [])
            d["scheduler"] = loads(d["scheduler"], {})
            sess = self.db.query_one(
                "SELECT id FROM sessions WHERE user_id=? AND kind='group' AND group_id=? "
                "AND status='active' ORDER BY created_at DESC LIMIT 1", (user_id, d["id"]))
            d["session_id"] = sess["id"] if sess else None
            out.append(d)
        return out

    def update_group(self, user_id: str, gid: str, **fields):
        allowed = {"name", "member_ids", "muted_ids", "scheduler", "announcement",
                   "summary", "summary_upto", "distilled_upto"}
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k in ("member_ids", "muted_ids", "scheduler"):
                v = dumps(v)
            sets.append(f"{k}=?")
            params.append(v)
        if sets:
            params.extend([gid, user_id])
            self.db.execute(f"UPDATE groups SET {','.join(sets)} WHERE id=? AND user_id=?",
                            tuple(params))

    def delete_group(self, user_id: str, gid: str) -> bool:
        with self.db.transaction() as conn:
            sess = conn.execute(
                "SELECT id FROM sessions WHERE user_id=? AND group_id=?",
                (user_id, gid)).fetchall()
            for s in sess:
                conn.execute("DELETE FROM messages WHERE session_id=?", (s["id"],))
                conn.execute("DELETE FROM sessions WHERE id=?", (s["id"],))
            n = conn.execute("DELETE FROM groups WHERE id=? AND user_id=?",
                             (gid, user_id)).rowcount
            conn.execute(
                "UPDATE memory_entries SET status='archived' WHERE user_id=? AND group_id=? "
                "AND status='active'", (user_id, gid))
        return n > 0

    def group_total_user_turns(self, session_id: str) -> int:
        """全局用户轮次（修 G2：不受窗口截断影响）"""
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id=? AND role='user'",
            (session_id,))
        return row["n"] if row else 0

    # ---------- relations ----------

    def get_relation(self, user_id: str, cid: str) -> dict:
        row = self.db.query_one(
            "SELECT * FROM relations WHERE user_id=? AND character_id=?", (user_id, cid))
        if not row:
            return {"affinity": 0.5, "emotion": "neutral", "stage": "stranger", "drift": {}}
        d = dict(row)
        d["drift"] = loads(d.get("drift"), {})
        return d

    def set_relation(self, user_id: str, cid: str, **fields):
        cur = self.get_relation(user_id, cid)
        cur.update({k: v for k, v in fields.items()
                    if k in ("affinity", "emotion", "stage", "drift")})
        self.db.execute(
            "INSERT INTO relations(user_id, character_id, affinity, emotion, stage, drift, updated_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id, character_id) DO UPDATE SET "
            "affinity=excluded.affinity, emotion=excluded.emotion, stage=excluded.stage, "
            "drift=excluded.drift, updated_at=excluded.updated_at",
            (user_id, cid, cur["affinity"], cur["emotion"], cur["stage"],
             dumps(cur["drift"]), now_iso()))

    # ---------- distill jobs ----------

    def enqueue_job(self, session_id: str, kind: str, start: int, end: int) -> int:
        row = self.db.query_one(
            "SELECT id FROM distill_jobs WHERE session_id=? AND status IN ('pending','running') "
            "AND range_start=? AND range_end=?", (session_id, start, end))
        if row:
            return row["id"]
        return self.db.execute(
            "INSERT INTO distill_jobs(session_id, kind, range_start, range_end, created_at) "
            "VALUES(?,?,?,?,?)", (session_id, kind, start, end, now_iso()))

    def claim_job(self) -> Optional[dict]:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM distill_jobs WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return None
            conn.execute("UPDATE distill_jobs SET status='running', attempts=attempts+1 WHERE id=?",
                         (row["id"],))
            return dict(row)

    def finish_job(self, job_id: int, ok: bool, error: str = ""):
        self.db.execute(
            "UPDATE distill_jobs SET status=?, last_error=?, done_at=? WHERE id=?",
            ("done" if ok else "failed", error, now_iso(), job_id))

    def reset_running_jobs(self):
        """进程重启后把 running 孤儿任务放回队列"""
        self.db.execute(
            "UPDATE distill_jobs SET status='pending' WHERE status='running'")

    def jobs_pending(self) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM distill_jobs WHERE status IN ('pending','running')")
        return row["n"] if row else 0


store = Store()