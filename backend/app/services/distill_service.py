"""记忆蒸馏服务 - 会话结束后异步提取长期记忆

支持增量蒸馏：summary.md 的 front matter 记录 `distilled_upto`（已处理到第几条消息），
再次蒸馏时只处理新增消息，避免重复调用 LLM 与重复写入记忆。
"""
import json
from datetime import datetime
from typing import Dict, Optional, Tuple
import aiofiles

from app.config import get_session_dir
from app.services.memory_service import memory_service
from app.utils.markdown_parser import parse_front_matter
from app.llm.adapter import llm_adapter
from app.llm.prompts import SUMMARY_PROMPT, EXTRACT_MEMORY_PROMPT


class DistillService:
    """会话记忆蒸馏服务"""

    async def distill_session(self, user_id: str, character_id: str, session_id: str):
        """
        增量蒸馏会话：
        1. 读取完整对话历史，跳过已处理部分
        2. 对新增片段生成摘要，追加到 summary.md
        3. 从片段摘要中提取需要长期记住的信息
        4. 去重后追加到长期记忆和角色记忆
        """
        try:
            history = await memory_service.get_recent_history(
                user_id, character_id, session_id, limit=2000
            )
            if not history:
                return

            meta, segment_count = await self._read_summary_meta(
                user_id, character_id, session_id
            )
            offset = int(meta.get("distilled_upto", 0) or 0)
            if len(history) <= offset:
                # 没有新消息，无需重复蒸馏
                return

            new_messages = history[offset:]
            # 一问一答都不到，继续攒着
            if len(new_messages) < 2:
                return

            history_text = "\n".join([
                f"{'用户' if m.role == 'user' else '角色'}: {m.content}"
                for m in new_messages
            ])

            summary = await self._generate_summary(history_text)
            if not summary.strip():
                return

            new_offset = len(history)
            await self._append_summary(
                user_id, character_id, session_id,
                summary=summary,
                start=offset + 1,
                end=new_offset,
                segment_index=segment_count + 1,
                distilled_upto=new_offset,
            )

            memories = await self._extract_memories(summary)

            for item in memories.get("user_preferences", []):
                item = item.strip()
                if not item:
                    continue
                if not await memory_service.is_duplicate(item, "long-term", user_id):
                    await memory_service.append_long_term(user_id, item)

            for item in memories.get("character_relationship", []):
                item = item.strip()
                if not item:
                    continue
                if not await memory_service.is_duplicate(
                    item, "character-memory", user_id, character_id
                ):
                    await memory_service.append_character_memory(
                        user_id, character_id, item
                    )

        except Exception as e:
            # 蒸馏失败不影响主流程，记录日志
            print(f"[DistillService] 蒸馏失败: {e}")

    async def _generate_summary(self, history_text: str) -> str:
        """调用 LLM 生成会话摘要"""
        response = await llm_adapter.chat(
            messages=[{"role": "user", "content": SUMMARY_PROMPT.format(history=history_text)}],
            temperature=0.5,
        )
        return response.get("content", "")

    async def _extract_memories(self, summary: str) -> Dict:
        """从摘要中提取长期记忆"""
        response = await llm_adapter.chat(
            messages=[{"role": "user", "content": EXTRACT_MEMORY_PROMPT.format(summary=summary)}],
            temperature=0.3,
        )
        content = response.get("content", "")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # 尝试从文本中提取 JSON
            import re
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            return {"user_preferences": [], "character_relationship": []}

    async def _read_summary_meta(
        self, user_id: str, character_id: str, session_id: str
    ) -> Tuple[dict, int]:
        """读取 summary.md 的元数据与已写入的片段数"""
        path = get_session_dir(user_id, character_id, session_id) / "summary.md"
        if not path.exists():
            return {}, 0
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
        meta, _ = parse_front_matter(content)
        try:
            segment_count = int(meta.get("segment_count", 0) or 0)
        except (TypeError, ValueError):
            segment_count = 0
        return meta, segment_count

    async def _append_summary(
        self,
        user_id: str,
        character_id: str,
        session_id: str,
        summary: str,
        start: int,
        end: int,
        segment_index: int,
        distilled_upto: int,
    ):
        """把新片段摘要追加到 summary.md，并更新蒸馏游标"""
        path = get_session_dir(user_id, character_id, session_id) / "summary.md"
        existing_body = ""
        if path.exists():
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                content = await f.read()
            _, existing_body = parse_front_matter(content)

        front_matter = (
            f"---\n"
            f"session_id: {session_id}\n"
            f"user_id: {user_id}\n"
            f"character_id: {character_id}\n"
            f"distilled_at: {datetime.now().isoformat()}\n"
            f"distilled_upto: {distilled_upto}\n"
            f"segment_count: {segment_index}\n"
            f"---\n\n"
        )

        body = existing_body.strip()
        if not body:
            body = "# 会话摘要\n"
        body += f"\n## 片段 {segment_index}（第 {start}-{end} 条）\n\n{summary.strip()}\n"

        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(front_matter + body)

    async def get_distilled_upto(
        self, user_id: str, character_id: str, session_id: str
    ) -> int:
        """对外暴露：查询某会话已蒸馏到第几条消息"""
        meta, _ = await self._read_summary_meta(user_id, character_id, session_id)
        try:
            return int(meta.get("distilled_upto", 0) or 0)
        except (TypeError, ValueError):
            return 0


# 全局单例
distill_service = DistillService()
