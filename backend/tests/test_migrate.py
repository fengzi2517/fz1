"""文件存储 → SQLite 迁移脚本测试"""
import json
from pathlib import Path

from app.migrate_files import migrate
from app.store import store


def _legacy_tree(root: Path):
    u = root / "users" / "u_old"
    c = u / "characters" / "c_old"
    (c / "sessions" / "s_1").mkdir(parents=True)
    (u / "groups" / "g_1").mkdir(parents=True)
    (u / "long-term.md").write_text(
        "---\nentry_count: 2\n---\n\n## 2026-11-01\n- 10:00  用户喜欢日料\n"
        "- 10:05  用户养了一只鹦鹉\n", encoding="utf-8")
    (u / "profile.md").write_text("---\n---\n\n# 用户档案\n- 坐标：杭州\n",
                                  encoding="utf-8")
    (c / "profile.md").write_text(
        "---\ncharacter_id: c_old\nname: 老角色\n---\n\n# 老角色\n\n温柔的图书管理员\n",
        encoding="utf-8")
    (c / "memory.md").write_text("---\n---\n\n## 2026-11-02\n- 20:00  用户说喜欢安静\n",
                                 encoding="utf-8")
    (c / "sessions" / "s_1" / "history.jsonl").write_text(
        json.dumps({"role": "user", "content": "今天书店人多吗"}) + "\n" +
        json.dumps({"role": "assistant", "content": "还好，不算多"}) + "\n",
        encoding="utf-8")
    (u / "groups" / "g_1" / "meta.json").write_text(
        json.dumps({"group_id": "g_1", "name": "老友群",
                    "character_ids": ["c_old"]}), encoding="utf-8")
    (u / "groups" / "g_1" / "history.jsonl").write_text(
        json.dumps({"role": "user", "content": "大家好", "name": "用户"}) + "\n" +
        json.dumps({"role": "assistant", "content": "嗨！",
                    "character_id": "c_old", "name": "老角色"}) + "\n",
        encoding="utf-8")
    (u / "groups" / "g_1" / "summary.md").write_text("--\n--\n\n打了招呼。\n",
                                                     encoding="utf-8")


def test_migrate_full(memory_db, tmp_path):
    data_dir = tmp_path / "legacy"
    _legacy_tree(data_dir)
    stats = migrate(data_dir, tmp_path / "new.db")
    assert stats == {"users": 1, "characters": 1, "sessions": 1, "messages": 4,
                     "memories": 3, "groups": 1, "skipped": []}
    uid = "u_old"
    assert store.get_profile(uid).strip().startswith("# 用户档案")
    assert {e["content"] for e in store.list_memories(uid, scope="user")} == \
        {"用户喜欢日料", "用户养了一只鹦鹉"}
    ch = store.get_character(uid, "c_old")
    assert ch["name"] == "老角色" and "图书管理员" in ch["profile_md"]
    assert store.list_memories(uid, scope="character", character_id="c_old")
    sess = store.get_session("s_1")  # 原 session id 保留
    assert sess and len(store.get_messages("s_1")) == 2
    g = store.get_group(uid, "g_1")
    assert g["name"] == "老友群" and g["member_ids"] == ["c_old"]
    assert "打了招呼" in g["summary"]
    assert len(store.get_messages(g["session_id"])) == 2


def test_migrate_skips_bad_ids(memory_db, tmp_path):
    bad = tmp_path / "users" / "..%2Fevil"
    bad.mkdir(parents=True)
    stats = migrate(tmp_path, tmp_path / "n.db")
    assert stats["users"] == 0


def test_migrate_idempotent(memory_db, tmp_path):
    data_dir = tmp_path / "legacy"
    _legacy_tree(data_dir)
    migrate(data_dir, tmp_path / "new.db")
    stats2 = migrate(data_dir, tmp_path / "new.db")
    assert stats2["users"] == 0 or all("已存在" in s for s in stats2["skipped"])
