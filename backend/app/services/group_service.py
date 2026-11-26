"""群聊服务 - 群聊元数据、消息存储、多角色编排与记忆蒸馏"""
import asyncio
import json
import uuid
from datetime import datetime
from typing import AsyncGenerator, Dict, List, Optional

import aiofiles
import jieba

from app.config import ensure_dir, get_group_dir, get_user_dir
from app.models.schemas import GroupMember, GroupProfile
from app.services.character_service import character_service
from app.services.memory_service import memory_service
from app.llm.adapter import llm_adapter
from app.llm.prompts import (
    GROUP_SYSTEM_PROMPT,
    GROUP_CONTEXT_TEMPLATE,
    SUMMARY_PROMPT,
)
from app.utils.markdown_parser import parse_front_matter

MAX_GROUP_MEMBERS = 4
# 最近上下文窗口：更大，让角色能看到较完整的近期群聊
GROUP_HISTORY_WINDOW = 30
# 从更早的历史里按关键词召回多少条
GROUP_RECALL_TOP_K = 6


class GroupService:
    """群聊的创建、查询与消息存储"""

    async def create_group(
        self, user_id: str, name: str, character_ids: List[str]
    ) -> GroupProfile:
        if not character_ids:
            raise ValueError("群聊至少需要一个角色")
        if len(character_ids) > MAX_GROUP_MEMBERS:
            raise ValueError(f"群聊最多支持 {MAX_GROUP_MEMBERS} 个角色")

        group_id = f"g_{uuid.uuid4().hex[:12]}"
        await self._write_meta(user_id, group_id, name, character_ids)
        return await self.get_group(user_id, group_id)

    async def list_groups(self, user_id: str) -> List[GroupProfile]:
        groups_dir = get_user_dir(user_id) / "groups"
        if not groups_dir.exists():
            return []
        groups = []
        for d in sorted(groups_dir.iterdir(), key=lambda p: p.name):
            if d.is_dir() and (d / "meta.json").exists():
                profile = await self.get_group(user_id, d.name)
                if profile:
                    groups.append(profile)
        return groups

    async def get_group(self, user_id: str, group_id: str) -> Optional[GroupProfile]:
        meta_path = get_group_dir(user_id, group_id) / "meta.json"
        if not meta_path.exists():
            return None
        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
            meta = json.loads(await f.read())

        members: List[GroupMember] = []
        valid_ids = []
        for cid in meta.get("character_ids", []):
            ch = await character_service.get_character(user_id, cid)
            if ch:
                valid_ids.append(cid)
                members.append(GroupMember(
                    character_id=cid,
                    name=ch.name,
                    description=ch.description or "",
                ))

        created_at = None
        raw_created = meta.get("created_at")
        if isinstance(raw_created, str):
            try:
                created_at = datetime.fromisoformat(raw_created)
            except ValueError:
                created_at = None

        return GroupProfile(
            group_id=group_id,
            name=meta.get("name", group_id),
            character_ids=valid_ids,
            members=members,
            created_at=created_at,
        )

    async def delete_group(self, user_id: str, group_id: str) -> bool:
        import shutil
        path = get_group_dir(user_id, group_id)
        if path.exists():
            shutil.rmtree(path)
            return True
        return False

    async def append_message(
        self,
        user_id: str,
        group_id: str,
        role: str,
        content: str,
        character_id: Optional[str] = None,
        name: str = "",
    ):
        path = ensure_dir(get_group_dir(user_id, group_id)) / "history.jsonl"
        record = {
            "role": role,
            "content": content,
            "character_id": character_id,
            "name": name,
            "timestamp": datetime.now().isoformat(),
        }
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            await f.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def get_history(
        self, user_id: str, group_id: str, limit: int = 50
    ) -> List[dict]:
        path = get_group_dir(user_id, group_id) / "history.jsonl"
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
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return messages

    async def count_history(self, user_id: str, group_id: str) -> int:
        """群聊历史总条数（用于判断概要是否落后）"""
        path = get_group_dir(user_id, group_id) / "history.jsonl"
        if not path.exists():
            return 0
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            lines = await f.readlines()
        return sum(1 for line in lines if line.strip())

    async def search_history(
        self,
        user_id: str,
        group_id: str,
        query: str,
        exclude_last: int = GROUP_HISTORY_WINDOW,
        top_k: int = GROUP_RECALL_TOP_K,
    ) -> List[str]:
        """在较早的群聊记录里按关键词召回相关片段（让角色「记得」更多群聊内容）"""
        path = get_group_dir(user_id, group_id) / "history.jsonl"
        if not path.exists():
            return []
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            lines = await f.readlines()

        older = lines[:-exclude_last] if len(lines) > exclude_last else []
        if not older:
            return []

        keywords = [w for w in jieba.lcut(query) if len(w) > 1]
        if not keywords:
            return []

        scored = []
        for idx, line in enumerate(older):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = msg.get("content", "")
            score = sum(1 for kw in keywords if kw in content)
            if score <= 0:
                continue
            speaker = "用户" if msg.get("role") == "user" else (msg.get("name") or "角色")
            scored.append((score, idx, f"{speaker}: {content}"))

        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [item[2] for item in scored[:top_k]]

    async def _write_meta(
        self, user_id: str, group_id: str, name: str, character_ids: List[str]
    ):
        path = ensure_dir(get_group_dir(user_id, group_id)) / "meta.json"
        meta = {
            "group_id": group_id,
            "name": name,
            "character_ids": character_ids,
            "created_at": datetime.now().isoformat(),
        }
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(meta, ensure_ascii=False, indent=2))


class GroupChatService:
    """群聊编排：用户发言后，成员轮流回应"""

    async def chat_stream(
        self,
        user_id: str,
        group_id: str,
        message: str,
        reply_mode: str = "round_robin",
        target_character_id: Optional[str] = None,
        interrupt_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[Dict, None]:
        """
        生成群聊发言事件流。
        interrupt_event 被 set 时，会尽快结束当前角色的生成并停止剩余角色，
        用于 WebSocket 下用户中途插话。
        """
        group = await group_service.get_group(user_id, group_id)
        if not group or not group.members:
            yield {"type": "error", "message": "群聊不存在或没有有效成员"}
            return

        # 0. 指定角色发言时，先校验目标，避免把用户消息写进历史后才报错
        target = None
        if reply_mode == "target":
            target = next(
                (m for m in group.members if m.character_id == target_character_id),
                None,
            )
            if target is None:
                yield {"type": "error", "message": "指定的角色不在这个群聊里"}
                return

        # 1. 先取历史（不含本条用户消息），再写入用户消息
        history = await group_service.get_history(
            user_id, group_id, limit=GROUP_HISTORY_WINDOW
        )
        await group_service.append_message(user_id, group_id, "user", message, name="用户")
        # 群聊变长时后台补概要/记忆，让每个角色都能看到更早的全部群聊
        await group_distill_service.maybe_refresh(user_id, group_id)

        members_desc = "、".join(
            f"{m.name}（{(m.description or '')[:30]}）" for m in group.members
        )
        history_text = self._render_history(history)
        # 群聊概要：由历次蒸馏生成，覆盖较早的聊天内容
        summary_text = await group_distill_service.get_summary(user_id, group_id)
        # 更早的群聊片段：按用户这句话召回，模拟角色「翻看群记录」
        older_lines = await group_service.search_history(user_id, group_id, message)

        # 2. 轮换发言起点：按用户已发言轮次轮转，避免总是同一个人先开口；
        #    指定角色时只让该角色回应
        if target is not None:
            ordered = [target]
        else:
            user_turns = sum(1 for h in history if h.get("role") == "user")
            ordered = self._rotate(group.members, user_turns)

        # 3. 逐角色流式发言
        for member in ordered:
            if interrupt_event is not None and interrupt_event.is_set():
                yield {"type": "interrupted", "reason": "user_message"}
                break

            profile = await memory_service.read_character_profile(
                user_id, member.character_id
            )
            # 每个角色自己的记忆（包含与用户相关的事、群聊里发生的事）
            own_memories = await memory_service.recall(
                user_id, member.character_id, message, top_k=6
            )
            memory_lines = [
                f"- {r.date} {r.content}" for r in own_memories if r.content
            ]

            context_parts = []
            if summary_text:
                context_parts.append(f"【群聊概要】\n{summary_text.strip()}")
            if older_lines:
                context_parts.append(
                    "【更早的群聊片段（与当前话题相关）】\n" + "\n".join(older_lines)
                )
            if memory_lines:
                context_parts.append(
                    "【你记得的关于用户的事】\n" + "\n".join(memory_lines)
                )
            context_parts.append(f"【最近的群聊】\n{history_text or '（暂无历史）'}")

            messages = [
                {"role": "system", "content": GROUP_SYSTEM_PROMPT.format(members=members_desc)},
            ]
            if profile:
                messages.append({"role": "system", "content": f"【你的角色设定】\n{profile}"})
            messages.append({
                "role": "system",
                "content": GROUP_CONTEXT_TEMPLATE.format(
                    context="\n\n".join(context_parts),
                    speaker=member.name,
                ),
            })
            messages.append({"role": "user", "content": message})

            yield {
                "type": "start",
                "character_id": member.character_id,
                "name": member.name,
            }

            full = ""
            cut = False
            try:
                async for chunk in llm_adapter.chat_stream(messages, max_tokens=300):
                    if interrupt_event is not None and interrupt_event.is_set():
                        cut = True
                        break
                    full += chunk
                    yield {
                        "type": "token",
                        "content": chunk,
                        "character_id": member.character_id,
                        "name": member.name,
                    }
            except Exception as e:
                yield {"type": "error", "message": f"{member.name} 发言失败: {e}"}
                continue

            if full.strip():
                await group_service.append_message(
                    user_id, group_id, "assistant", full.strip(),
                    character_id=member.character_id, name=member.name,
                )
            yield {
                "type": "done",
                "character_id": member.character_id,
                "name": member.name,
            }
            if cut:
                # 用户插话：已保存的角色回复到此为止，剩余角色不再发言
                yield {
                    "type": "interrupted",
                    "character_id": member.character_id,
                    "name": member.name,
                    "reason": "user_message",
                }
                break

        yield {"type": "end"}

    def _render_history(self, history: List[dict]) -> str:
        lines = []
        for h in history:
            if h.get("role") == "user":
                lines.append(f"用户: {h.get('content', '')}")
            else:
                lines.append(f"{h.get('name') or '角色'}: {h.get('content', '')}")
        return "\n".join(lines)

    def _rotate(self, members: List[GroupMember], offset: int) -> List[GroupMember]:
        if not members:
            return []
        start = offset % len(members)
        return members[start:] + members[:start]


group_service = GroupService()
group_chat_service = GroupChatService()


class GroupDistillService:
    """群聊记忆蒸馏：按角色视角分别提取与用户相关的长期记忆"""

    def __init__(self):
        # 正在后台补概要的群聊，避免同一群聊并发重复蒸馏
        self._refreshing: set = set()
        self._tasks: set = set()

    async def maybe_refresh(self, user_id: str, group_id: str):
        """
        群聊变长且概要明显落后时，后台补一次蒸馏。
        用户长时间泡在群里、不触发 /end 也能让每个角色「看到」更早的全部群聊。
        """
        key = f"{user_id}:{group_id}"
        if key in self._refreshing:
            return
        total = await group_service.count_history(user_id, group_id)
        if total <= 0:
            return
        covered = await self._read_summary_cursor(user_id, group_id)
        if total - covered <= GROUP_HISTORY_WINDOW:
            return

        self._refreshing.add(key)
        task = asyncio.create_task(self._refresh(key, user_id, group_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _refresh(self, key: str, user_id: str, group_id: str):
        try:
            await self.distill_group(user_id, group_id)
        finally:
            self._refreshing.discard(key)

    async def _read_summary_cursor(self, user_id: str, group_id: str) -> int:
        """概要已覆盖到第几条"""
        path = get_group_dir(user_id, group_id) / "summary.md"
        if not path.exists():
            return 0
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            meta, _ = parse_front_matter(await f.read())
        try:
            return int(meta.get("summary_upto", 0) or 0)
        except (TypeError, ValueError):
            return 0

    async def distill_group(self, user_id: str, group_id: str):
        try:
            group = await group_service.get_group(user_id, group_id)
            if not group or not group.members:
                return

            history = await group_service.get_history(user_id, group_id, limit=2000)
            cursor = await self._read_cursor(user_id, group_id)
            if len(history) <= cursor:
                return

            new_messages = history[cursor:]
            if len(new_messages) < 2:
                return

            transcript = "\n".join(
                f"{'用户' if m.get('role') == 'user' else (m.get('name') or '角色')}: {m.get('content', '')}"
                for m in new_messages
            )

            # 群聊概要：让所有角色都能「记得」更早的群聊内容
            await self._update_summary(user_id, group_id, history)

            for member in group.members:
                extracted = await self._extract(member.name, transcript)
                for item in extracted.get("user_preferences", []):
                    item = item.strip()
                    if not item:
                        continue
                    if not await memory_service.is_duplicate(item, "long-term", user_id):
                        await memory_service.append_long_term(user_id, item)
                for item in extracted.get("character_relationship", []):
                    item = item.strip()
                    if not item:
                        continue
                    if not await memory_service.is_duplicate(
                        item, "character-memory", user_id, member.character_id
                    ):
                        await memory_service.append_character_memory(
                            user_id, member.character_id, item
                        )
                # 群聊见闻：每个角色独立记住群里发生的事
                for item in extracted.get("group_events", []):
                    item = item.strip()
                    if not item:
                        continue
                    entry = f"（群聊见闻）{item}"
                    if not await memory_service.is_duplicate(
                        entry, "character-memory", user_id, member.character_id
                    ):
                        await memory_service.append_character_memory(
                            user_id, member.character_id, entry
                        )

            await self._write_cursor(user_id, group_id, len(history))

        except Exception as e:
            print(f"[GroupDistillService] 群聊蒸馏失败: {e}")

    # ---------- 群聊概要 ----------

    async def _update_summary(self, user_id: str, group_id: str, history: List[dict]):
        """增量生成群聊概要，写入 groups/{id}/summary.md"""
        path = get_group_dir(user_id, group_id) / "summary.md"
        upto = 0
        body = ""
        if path.exists():
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                meta, body = parse_front_matter(await f.read())
            try:
                upto = int(meta.get("summary_upto", 0) or 0)
            except (TypeError, ValueError):
                upto = 0

        if len(history) <= upto:
            return
        new_messages = history[upto:]
        if len(new_messages) < 2:
            return

        transcript = "\n".join(
            f"{'用户' if m.get('role') == 'user' else (m.get('name') or '角色')}: {m.get('content', '')}"
            for m in new_messages
        )
        try:
            response = await llm_adapter.chat(
                messages=[{
                    "role": "user",
                    "content": SUMMARY_PROMPT.format(history=transcript),
                }],
                temperature=0.4,
            )
        except Exception as e:
            print(f"[GroupDistillService] 群聊概要生成失败: {e}")
            return

        summary = (response.get("content") or "").strip()
        if not summary:
            return

        body = body.strip() or "# 群聊概要"
        body += (
            f"\n\n## 第 {upto + 1}-{len(history)} 条\n\n{summary}\n"
        )
        front_matter = (
            f"---\n"
            f"group_id: {group_id}\n"
            f"user_id: {user_id}\n"
            f"summary_upto: {len(history)}\n"
            f"updated_at: {datetime.now().isoformat()}\n"
            f"---\n\n"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(front_matter + body)

    async def get_summary(self, user_id: str, group_id: str) -> str:
        """读取群聊概要正文（供每轮发言注入）"""
        path = get_group_dir(user_id, group_id) / "summary.md"
        if not path.exists():
            return ""
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            _, body = parse_front_matter(await f.read())
        body = body.strip()
        if len(body) > 4000:
            body = body[-4000:]
        return body

    async def _extract(self, character_name: str, transcript: str) -> Dict:
        from app.llm.prompts import GROUP_MEMORY_EXTRACT_PROMPT
        try:
            response = await llm_adapter.chat(
                messages=[{
                    "role": "user",
                    "content": GROUP_MEMORY_EXTRACT_PROMPT.format(
                        character_name=character_name, transcript=transcript
                    ),
                }],
                temperature=0.3,
            )
            content = response.get("content", "")
            return self._parse_json(content)
        except Exception as e:
            print(f"[GroupDistillService] 提取失败({character_name}): {e}")
            return {"user_preferences": [], "character_relationship": []}

    def _parse_json(self, content: str) -> Dict:
        import re
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {"user_preferences": [], "character_relationship": []}

    async def _read_cursor(self, user_id: str, group_id: str) -> int:
        meta_path = get_group_dir(user_id, group_id) / "meta.json"
        if not meta_path.exists():
            return 0
        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
            meta = json.loads(await f.read())
        try:
            return int(meta.get("distilled_upto", 0) or 0)
        except (TypeError, ValueError):
            return 0

    async def _write_cursor(self, user_id: str, group_id: str, upto: int):
        meta_path = get_group_dir(user_id, group_id) / "meta.json"
        if not meta_path.exists():
            return
        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
            meta = json.loads(await f.read())
        meta["distilled_upto"] = upto
        meta["distilled_at"] = datetime.now().isoformat()
        async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(meta, ensure_ascii=False, indent=2))


group_distill_service = GroupDistillService()
