"""记忆服务 - 负责记忆文件的读写与检索"""
import json
import aiofiles
from datetime import datetime
from typing import List, Optional
import jieba

from app.config import (
    get_user_dir, get_character_dir, get_session_dir,
    ensure_dir, RECALL_TOP_K, RECALL_KEYWORD_OVERLAP_THRESHOLD,
    MAX_HISTORY_MESSAGES,
)
from app.models.schemas import Message, RecallResult
from app.utils.markdown_parser import parse_markdown_by_date, parse_front_matter


class MemoryService:
    """记忆服务"""

    # ========== 用户档案 ==========

    async def read_user_profile(self, user_id: str) -> str:
        """读取用户档案 md 正文"""
        path = get_user_dir(user_id) / "profile.md"
        if not path.exists():
            return self._default_user_profile(user_id)
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
        _, body = parse_front_matter(content)
        return body

    async def write_user_profile(self, user_id: str, content: str):
        """写入用户档案"""
        path = ensure_dir(get_user_dir(user_id)) / "profile.md"
        front_matter = f"---\nuser_id: {user_id}\nupdated_at: {datetime.now().isoformat()}\n---\n\n"
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(front_matter + content)

    # ========== 长期记忆 ==========

    async def append_long_term(self, user_id: str, entry: str):
        """追加一条长期记忆（entry 为单条内容）"""
        path = ensure_dir(get_user_dir(user_id)) / "long-term.md"
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")

        # 读取现有条目
        existing_entries = []  # [(date_str, time_str, content), ...]
        entry_count = 0
        if path.exists():
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                text = await f.read()
            meta, _ = parse_front_matter(text)
            entry_count = int(meta.get("entry_count", 0))
            sections = parse_markdown_by_date(text)
            for sec in sections:
                for e in sec.entries:
                    existing_entries.append((sec.date.isoformat(), e.time, e.content))

        # 追加新条目
        existing_entries.append((date_str, time_str, entry))
        entry_count += 1

        # 按日期倒序重建
        new_body = self._rebuild_memory_body(existing_entries)

        # 写入文件
        front_matter = (
            f"---\n"
            f"user_id: {user_id}\n"
            f"last_updated: {now.date().isoformat()}\n"
            f"entry_count: {entry_count}\n"
            f"---\n\n"
            f"# 长期记忆\n\n"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(front_matter + new_body)

    async def get_long_term_recent(self, user_id: str, limit: int = 20) -> List[str]:
        """获取最近的长期记忆条目（用于去重）"""
        path = get_user_dir(user_id) / "long-term.md"
        if not path.exists():
            return []
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
        sections = parse_markdown_by_date(content)
        entries = []
        for sec in sections:
            for e in sec.entries:
                entries.append(e.content)
                if len(entries) >= limit:
                    return entries
        return entries

    # ========== 角色记忆 ==========

    async def read_character_profile(self, user_id: str, character_id: str) -> str:
        """读取角色人设 md 正文"""
        path = get_character_dir(user_id, character_id) / "profile.md"
        if not path.exists():
            return ""
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
        _, body = parse_front_matter(content)
        return body

    async def append_character_memory(self, user_id: str, character_id: str, entry: str):
        """追加一条角色记忆"""
        path = ensure_dir(get_character_dir(user_id, character_id)) / "memory.md"
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")

        # 读取现有条目
        existing_entries = []
        if path.exists():
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                text = await f.read()
            sections = parse_markdown_by_date(text)
            for sec in sections:
                for e in sec.entries:
                    existing_entries.append((sec.date.isoformat(), e.time, e.content))

        # 追加新条目
        existing_entries.append((date_str, time_str, entry))

        # 按日期倒序重建
        new_body = self._rebuild_memory_body(existing_entries)

        front_matter = (
            f"---\n"
            f"character_id: {character_id}\n"
            f"user_id: {user_id}\n"
            f"last_updated: {now.date().isoformat()}\n"
            f"---\n\n"
            f"# 角色记忆\n\n"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(front_matter + new_body)

    async def get_character_memory_recent(self, user_id: str, character_id: str, limit: int = 20) -> List[str]:
        """获取最近的角色记忆条目"""
        path = get_character_dir(user_id, character_id) / "memory.md"
        if not path.exists():
            return []
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
        sections = parse_markdown_by_date(content)
        entries = []
        for sec in sections:
            for e in sec.entries:
                entries.append(e.content)
                if len(entries) >= limit:
                    return entries
        return entries

    # ========== 对话历史 ==========

    async def append_message(self, user_id: str, character_id: str, session_id: str, message: Message):
        """追加消息到 history.jsonl"""
        path = ensure_dir(get_session_dir(user_id, character_id, session_id)) / "history.jsonl"
        if message.timestamp is None:
            message.timestamp = datetime.now()
        line = message.model_dump_json() + "\n"
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            await f.write(line)

    async def get_recent_history(self, user_id: str, character_id: str, session_id: str, limit: int = MAX_HISTORY_MESSAGES) -> List[Message]:
        """获取最近的对话历史（滑动窗口）"""
        path = get_session_dir(user_id, character_id, session_id) / "history.jsonl"
        if not path.exists():
            return []
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            lines = await f.readlines()
        messages = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                ts = data.get("timestamp")
                if ts:
                    data["timestamp"] = datetime.fromisoformat(ts)
                messages.append(Message(**data))
            except (json.JSONDecodeError, ValueError):
                continue
        return messages

    async def get_latest_session(self, user_id: str, character_id: str) -> Optional[str]:
        """获取该角色最近一次有对话记录的会话 ID"""
        sessions_dir = get_character_dir(user_id, character_id) / "sessions"
        if not sessions_dir.exists():
            return None
        candidates = []
        for d in sessions_dir.iterdir():
            if not d.is_dir():
                continue
            history = d / "history.jsonl"
            if history.exists():
                candidates.append((history.stat().st_mtime, d.name))
        if not candidates:
            return None
        return max(candidates)[1]

    # ========== recall 检索 ==========

    async def recall(self, user_id: str, character_id: str, query: str, top_k: int = RECALL_TOP_K) -> List[RecallResult]:
        """按需检索长期记忆和角色记忆"""
        keywords = [w for w in jieba.lcut(query) if len(w) > 1]
        if not keywords:
            return []

        results = []
        today = datetime.now().date()

        # 检索长期记忆
        lt_path = get_user_dir(user_id) / "long-term.md"
        if lt_path.exists():
            async with aiofiles.open(lt_path, "r", encoding="utf-8") as f:
                content = await f.read()
            results.extend(self._search_in_content(content, keywords, today, "long-term"))

        # 检索角色记忆
        cm_path = get_character_dir(user_id, character_id) / "memory.md"
        if cm_path.exists():
            async with aiofiles.open(cm_path, "r", encoding="utf-8") as f:
                content = await f.read()
            results.extend(self._search_in_content(content, keywords, today, "character-memory"))

        # 按分数排序，取 top_k
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    def _search_in_content(self, content: str, keywords: List[str], today, source: str) -> List[RecallResult]:
        """在 md 内容中按关键词检索"""
        sections = parse_markdown_by_date(content)
        results = []
        for sec in sections:
            days_diff = (today - sec.date).days
            recency_weight = 1.0 / (1 + days_diff)
            for entry in sec.entries:
                score = sum(1 for kw in keywords if kw in entry.content)
                if score > 0:
                    results.append(RecallResult(
                        source=source,
                        date=sec.date.isoformat(),
                        content=entry.content,
                        score=score * recency_weight,
                    ))
        return results

    async def get_recent_memories(self, user_id: str, character_id: str, limit: int = 8) -> List[RecallResult]:
        """
        取最近若干条记忆（关键词检索无命中时的兜底）。
        用户问「你还记得…」但关键词太泛时，直接注入最近的记忆，避免 AI 回答「不记得」。
        """
        candidates = []
        sources = [
            ("character-memory", get_character_dir(user_id, character_id) / "memory.md"),
            ("long-term", get_user_dir(user_id) / "long-term.md"),
        ]
        for source, path in sources:
            if not path.exists():
                continue
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                content = await f.read()
            sections = parse_markdown_by_date(content)  # 已按日期倒序
            for sec in sections:
                for entry in reversed(sec.entries):  # 同一天内取最新的
                    candidates.append(RecallResult(
                        source=source,
                        date=sec.date.isoformat(),
                        content=entry.content,
                        score=0.0,
                    ))
        return candidates[:limit]

    # ========== 去重 ==========

    async def is_duplicate(self, new_entry: str, memory_type: str, user_id: str, character_id: Optional[str] = None) -> bool:
        """检查新条目是否与最近条目重复"""
        if memory_type == "long-term":
            recent = await self.get_long_term_recent(user_id, limit=20)
        else:
            recent = await self.get_character_memory_recent(user_id, character_id, limit=20)

        new_keywords = set(jieba.lcut(new_entry))
        if not new_keywords:
            return False

        for entry in recent:
            entry_keywords = set(jieba.lcut(entry))
            if not entry_keywords:
                continue
            overlap = len(new_keywords & entry_keywords) / len(new_keywords)
            if overlap > RECALL_KEYWORD_OVERLAP_THRESHOLD:
                return True
        return False

    # ========== 工具方法 ==========

    def _rebuild_memory_body(self, entries: List[tuple]) -> str:
        """
        根据条目列表重建记忆正文（按日期倒序，同日期按时间正序）。
        entries: [(date_str, time_str, content), ...]
        """
        from collections import OrderedDict
        grouped = OrderedDict()
        for date_str, time_str, content in entries:
            if date_str not in grouped:
                grouped[date_str] = []
            grouped[date_str].append((time_str, content))

        # 按日期倒序
        sorted_dates = sorted(grouped.keys(), reverse=True)

        lines = []
        for d in sorted_dates:
            lines.append(f"## {d}")
            # 同日期按时间正序
            items = sorted(grouped[d], key=lambda x: x[0])
            for time_str, content in items:
                lines.append(f"- {time_str}  {content}")
            lines.append("")

        return "\n".join(lines)

    def _default_user_profile(self, user_id: str) -> str:
        """默认用户档案"""
        return f"""# 用户档案

## 基础信息
- 用户ID: {user_id}

## 偏好
- 语言：中文
"""


# 全局单例
memory_service = MemoryService()
