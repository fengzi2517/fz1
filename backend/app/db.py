"""SQLite 存储层（升级后真相源）

设计要点：
- WAL 模式：解决旧版文件读-改-写竞态（01 文档 §6.1）
- 单连接 + 可重入锁：FastAPI 同进程内安全共享；写事务用 BEGIN IMMEDIATE
- 内存库支持（db_path=":memory:"）用于测试
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE,
    pw_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    card_json TEXT NOT NULL DEFAULT '{}',
    profile_md TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    talkativeness INTEGER NOT NULL DEFAULT 50,
    voice_config TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_characters_user ON characters(user_id);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'char',          -- char / group
    character_id TEXT,
    group_id TEXT,
    parent_session_id TEXT,
    branched_at_message_id INTEGER,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',      -- active / archived / trashed
    distilled_upto INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_message_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, kind, status);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,                          -- user / assistant / system
    character_id TEXT,
    name TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    candidates TEXT NOT NULL DEFAULT '[]',       -- swipe 多候选
    active_candidate INTEGER NOT NULL DEFAULT 0,
    flags TEXT NOT NULL DEFAULT '{}',            -- interrupted / edited / pinned
    token_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
CREATE TABLE IF NOT EXISTS memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    scope TEXT NOT NULL,                         -- user / character / group
    character_id TEXT,
    group_id TEXT,
    block_label TEXT NOT NULL DEFAULT 'facts',   -- facts / preferences / relationship / events / profile
    content TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'active',       -- active / superseded / archived
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    embedding TEXT,                              -- JSON list[float] 或 NULL
    tokens TEXT NOT NULL DEFAULT '',             -- jieba 分词串（检索用）
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_recalled_at TEXT,
    recall_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memory_user ON memory_entries(user_id, scope, status);
CREATE INDEX IF NOT EXISTS idx_memory_char ON memory_entries(user_id, character_id, status);
CREATE TABLE IF NOT EXISTS memory_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    action TEXT NOT NULL,                        -- ADD/UPDATE/DELETE/NOOP/MERGE
    entry_id INTEGER,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    member_ids TEXT NOT NULL DEFAULT '[]',
    muted_ids TEXT NOT NULL DEFAULT '[]',
    scheduler TEXT NOT NULL DEFAULT '{}',        -- {mode, speakers_per_round, ...}
    announcement TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    summary_upto INTEGER NOT NULL DEFAULT 0,
    distilled_upto INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_groups_user ON groups(user_id);
CREATE TABLE IF NOT EXISTS relations (
    user_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    affinity REAL NOT NULL DEFAULT 0.5,
    emotion TEXT NOT NULL DEFAULT 'neutral',
    stage TEXT NOT NULL DEFAULT 'stranger',
    drift TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, character_id)
);
CREATE TABLE IF NOT EXISTS distill_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,                          -- chat / group
    range_start INTEGER NOT NULL,
    range_end INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',      -- pending / running / done / failed
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    done_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON distill_jobs(status, id);
CREATE TABLE IF NOT EXISTS recall_ledger (
    user_id TEXT NOT NULL,
    context_key TEXT NOT NULL,                   -- session_id 或 group_id
    entry_id INTEGER NOT NULL,
    last_round INTEGER NOT NULL DEFAULT 0,
    hits INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, context_key, entry_id)
);
CREATE TABLE IF NOT EXISTS user_profile (
    user_id TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------- 基础执行 ----------

    def query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid if cur.lastrowid else cur.rowcount

    def executescript(self, sql: str):
        with self._lock:
            self._conn.executescript(sql)

    @contextmanager
    def transaction(self):
        """写事务：BEGIN IMMEDIATE 避免并发写冲突，异常自动回滚"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self):
        with self._lock:
            self._conn.close()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(text: str, default: Any = None):
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


_db: Optional[Database] = None


def get_db() -> Database:
    global _db
    if _db is None:
        from app.config import DB_PATH
        _db = Database(str(DB_PATH))
    return _db


def set_db(db: Optional[Database]):
    """测试注入用"""
    global _db
    _db = db