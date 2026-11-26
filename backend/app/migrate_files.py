"""旧文件存储 → SQLite 迁移脚本（P0-3 一次性工具）

用法：
    python -m app.migrate_files [--data-dir ./data] [--db ./data/roleplay.db] [--dry-run]

迁移映射（01 文档 §2 布局）：
    users/{uid}/profile.md                 → user_profile
    users/{uid}/long-term.md               → memory_entries(scope=user)
    users/{uid}/characters/{cid}/profile.md→ characters
    users/{uid}/characters/{cid}/memory.md → memory_entries(scope=character)
    .../sessions/{sid}/history.jsonl       → sessions + messages
    users/{uid}/groups/{gid}/meta.json     → groups（并建 kind=group 会话）
    users/{uid}/groups/{gid}/history.jsonl → messages
    users/{uid}/groups/{gid}/summary.md    → groups.summary
"""
import argparse
import json
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import ID_RE, hash_password  # noqa: E402
from app.utils.markdown_parser import (  # noqa: E402
    parse_front_matter, parse_markdown_by_date)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _jsonl(path: Path):
    for line in _read(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _date_entries(text: str):
    """markdown 按日期分节 → [(date, content)]；无时间戳格式时退化为 - 列表项"""
    out = []
    for sec in parse_markdown_by_date(text):
        date = sec.date.strftime("%Y-%m-%d") if getattr(sec, "date", None) else ""
        for entry in sec.entries:
            content = re.sub(r"^\d{2}:\d{2}\s+", "", entry.content).strip()
            if content:
                out.append((date, content))
    if out:
        return out
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ")):
            content = re.sub(r"^\d{2}:\d{2}\s+", "", line[2:]).strip()
            if content and not content.startswith("#"):
                out.append(("", content))
    return out


def migrate(data_dir: Path, db_path: Path, dry_run: bool = False) -> dict:
    from app.db import Database, set_db
    from app.store import store

    stats = {"users": 0, "characters": 0, "sessions": 0, "messages": 0,
             "memories": 0, "groups": 0, "skipped": []}
    db = Database(str(db_path))
    store.bind(db)
    if not dry_run:
        set_db(db)

    users_dir = data_dir / "users"
    if not users_dir.exists():
        print(f"目录不存在：{users_dir}")
        return stats

    for udir in sorted(users_dir.iterdir()):
        if not udir.is_dir():
            continue
        uid = udir.name
        if not ID_RE.match(uid):
            stats["skipped"].append(f"user {uid}: ID 不合法（路径穿越风险），需人工改名")
            continue
        if store.get_user(uid):
            stats["skipped"].append(f"user {uid}: 已存在，跳过")
            continue

        pw = secrets.token_urlsafe(9)
        store.db.execute(
            "INSERT INTO users(id, username, pw_hash, created_at) VALUES(?,?,?,?)",
            (uid, f"migrated_{uid}", hash_password(pw),
             "2024-01-01T00:00:00"))
        stats["users"] += 1
        print(f"用户 {uid} → 初始密码 {pw}（请尽快改密/注册新账号）")

        profile_md = _read(udir / "profile.md")
        if profile_md:
            _, body = parse_front_matter(profile_md)
            store.set_profile(uid, body)

        lt = _read(udir / "long-term.md")
        if lt:
            _, body = parse_front_matter(lt)
            for _date, item in _date_entries(body):
                store.add_memory(uid, "user", item, block_label="migrated")
                stats["memories"] += 1

        chars_dir = udir / "characters"
        if chars_dir.exists():
            for cdir in sorted(chars_dir.iterdir()):
                if not cdir.is_dir():
                    continue
                stats["characters"] += _migrate_character(
                    uid, cdir, store, stats)

        gdir_root = udir / "groups"
        if gdir_root.exists():
            for gdir in sorted(gdir_root.iterdir()):
                if not (gdir / "meta.json").exists():
                    continue
                stats["groups"] += _migrate_group(uid, gdir, store, stats)

    if dry_run:
        db.close()
    return stats


def _migrate_character(uid: str, cdir: Path, store, stats) -> int:
    cid = cdir.name
    if not ID_RE.match(cid):
        stats["skipped"].append(f"character {cid}: ID 不合法")
        return 0
    if store.get_character(uid, cid):
        return 0
    raw = _read(cdir / "profile.md")
    meta, body = parse_front_matter(raw) if raw else ({}, "")
    name = str(meta.get("name") or cid)
    store.create_character(
        uid, name, profile_md=body, description="",
        card_json={"raw_content": raw, "parsed": {"name": name},
                   "format": "internal_v2_migrated"},
        cid=cid)
    mem = _read(cdir / "memory.md")
    if mem:
        _, mbody = parse_front_matter(mem)
        for _date, item in _date_entries(mbody):
            store.add_memory(uid, "character", item, character_id=cid,
                             block_label="migrated")
            stats["memories"] += 1
    sdir_root = cdir / "sessions"
    if sdir_root.exists():
        for sdir in sorted(sdir_root.iterdir()):
            hist = sdir / "history.jsonl"
            if not hist.exists():
                continue
            stats["sessions"] += 1
            sid = sdir.name if ID_RE.match(sdir.name) else None
            sid = store.create_session(uid, "char", character_id=cid, sid=sid)
            meta_j = {}
            if (sdir / "meta.json").exists():
                try:
                    meta_j = json.loads(_read(sdir / "meta.json"))
                except json.JSONDecodeError:
                    meta_j = {}
            n = 0
            for msg in _jsonl(hist):
                role = msg.get("role")
                if role not in ("user", "assistant", "system"):
                    continue
                store.append_message(
                    sid, role, msg.get("content", ""),
                    name=msg.get("name", "用户" if role == "user" else ""),
                    )
                n += 1
            stats["messages"] += n
            if meta_j.get("summary"):
                store.set_session(sid, summary=str(meta_j["summary"]))
            if isinstance(meta_j.get("distilled_upto"), int):
                store.set_session(sid, distilled_upto=min(
                    meta_j["distilled_upto"], n))
    return 1


def _migrate_group(uid: str, gdir: Path, store, stats) -> int:
    gid = gdir.name
    if not ID_RE.match(gid):
        stats["skipped"].append(f"group {gid}: ID 不合法")
        return 0
    try:
        meta = json.loads(_read(gdir / "meta.json"))
    except json.JSONDecodeError:
        return 0
    if store.get_group(uid, gid):
        return 0
    member_ids = [c for c in meta.get("character_ids", []) if ID_RE.match(c)]
    store.create_group(uid, meta.get("name", "迁移群聊"),
                       member_ids or ["_placeholder"], gid=gid)
    group = store.get_group(uid, gid)
    sid = group["session_id"]
    for msg in _jsonl(gdir / "history.jsonl"):
        role = msg.get("role")
        if role not in ("user", "assistant", "system"):
            continue
        store.append_message(sid, role, msg.get("content", ""),
                             character_id=msg.get("character_id"),
                             name=msg.get("name", ""))
        stats["messages"] += 1
    summary = _read(gdir / "summary.md")
    if summary:
        _, sbody = parse_front_matter(summary)
        store.update_group(uid, gid, summary=sbody.strip())
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).resolve()
    db_path = Path(args.db) if args.db else data_dir / "roleplay.db"
    stats = migrate(data_dir, db_path, args.dry_run)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
