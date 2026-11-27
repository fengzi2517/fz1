"""群聊服务 v2（P2-3/4/5/6/7）

- 元数据/消息全部走 SQLite（store），群=一条 active group 会话
- 调度器 GroupScheduler：@提及/昵称命中 → talkativeness 加权随机 → 兜底；
  mode=auto(智能)/all(全员轮转,兼容旧行为)/target(指定)；轮转基准用全局轮次（修 G2）
- 并行生成：被选成员 asyncio 并行，事件交错流式输出，事件均带 character_id（修 G4）
- 私有视角：自己的话渲染为"你"，注入个人记忆与关系状态（修 G3 轻量版、P2-6）
- 打断半截话标记 interrupted，不进蒸馏（修 G8）
- 成员上限可配、mute、加退群事件消息（P2-7）
"""
import asyncio
import logging
import random
from typing import AsyncGenerator, Dict, List, Optional

from app.config import GROUP_MEMBER_LIMIT, GROUP_SPEAKERS_PER_ROUND, GROUP_PARALLEL_REPLY
from app.core.guard import moderate_input, refusal_for
from app.core.textutil import tokenize
from app.services.distill_service import distill_service
from app.services.memory_service import memory_service
from app.store import store


logger = logging.getLogger("app.group")

GROUP_HISTORY_WINDOW = 30
GROUP_RECALL_TOP_K = 6

GROUP_SYSTEM_PROMPT = """你们正在进行一场多角色群聊，参与者如下：
{members}

## 规则
1. 你只能以【当前发言者】的身份说话，不要替其他角色发言，也不要写别人的台词
2. 不要提及自己是 AI 或大语言模型
3. 每次发言控制在 1~3 句、80 字以内，像真人在群里聊天一样自然
4. 可以回应其他角色或用户刚才说的话，但不要复述原文
5. 不要用「某某说：」这种格式，直接说你的台词
6. 你们共享同一个群聊记录，你可以自然地提起之前群里聊过的内容
7. 如果话题与你无关，宁可只发一个短语气词，不要硬聊"""

GROUP_CONTEXT_TEMPLATE = """{context}

【当前发言者】{speaker}
请只输出 {speaker} 这一轮要说的话。"""


class GroupService:

    def create_group(self, user_id: str, name: str, character_ids: List[str]) -> dict:
        if not character_ids:
            raise ValueError("群聊至少需要一个角色")
        if len(character_ids) > GROUP_MEMBER_LIMIT:
            raise ValueError(f"群聊最多支持 {GROUP_MEMBER_LIMIT} 个角色")
        for cid in character_ids:
            if not store.get_character(user_id, cid):
                raise ValueError(f"角色不存在: {cid}")
        gid = store.create_group(user_id, name, character_ids)
        return store.get_group(user_id, gid)

    def add_member(self, user_id: str, gid: str, cid: str) -> dict:
        group = self._require(user_id, gid)
        if cid not in group["member_ids"]:
            if len(group["member_ids"]) >= GROUP_MEMBER_LIMIT:
                raise ValueError(f"群聊最多支持 {GROUP_MEMBER_LIMIT} 个角色")
            if not store.get_character(user_id, cid):
                raise ValueError("角色不存在")
            store.update_group(user_id, gid, member_ids=group["member_ids"] + [cid])
            group = store.get_group(user_id, gid)
        if group.get("session_id"):
            name = store.get_character(user_id, cid)["name"]
            store.append_message(group["session_id"], "system", f"{name} 加入了群聊")
        return group

    def remove_member(self, user_id: str, gid: str, cid: str) -> dict:
        group = self._require(user_id, gid)
        members = [m for m in group["member_ids"] if m != cid]
        if not members:
            raise ValueError("群聊至少保留一个角色")
        store.update_group(user_id, gid, member_ids=members)
        if group.get("session_id"):
            name = store.get_character(user_id, cid)["name"]
            store.append_message(group["session_id"], "system", f"{name} 退出了群聊")
        return store.get_group(user_id, gid)

    def set_muted(self, user_id: str, gid: str, muted_ids: List[str]) -> dict:
        group = self._require(user_id, gid)
        store.update_group(user_id, gid,
                           muted_ids=[m for m in muted_ids if m in group["member_ids"]])
        return store.get_group(user_id, gid)

    def list_groups(self, user_id: str) -> List[dict]:
        return store.list_groups(user_id)

    def get_group(self, user_id: str, gid: str) -> Optional[dict]:
        return store.get_group(user_id, gid)

    def delete_group(self, user_id: str, gid: str) -> bool:
        return store.delete_group(user_id, gid)

    def _require(self, user_id: str, gid: str) -> dict:
        group = store.get_group(user_id, gid)
        if not group:
            raise ValueError("群聊不存在")
        return group


class GroupScheduler:
    """发言调度（纯逻辑，可单测）"""

    def pick(self, members: List[dict], message: str, history: List[dict],
             muted_ids: List[str], mode: str = "auto",
             target_id: Optional[str] = None, speakers: int = GROUP_SPEAKERS_PER_ROUND,
             total_user_turns: int = 0,
             rng: Optional[random.Random] = None) -> List[dict]:
        rng = rng or random
        active = [m for m in members if m["id"] not in muted_ids]
        if mode == "target":
            return [m for m in active if m["id"] == target_id]
        if not active:
            return []
        if mode == "all":
            # 兼容旧行为：全员轮转，但起点用全局用户轮次（修 G2）
            start = total_user_turns % len(active)
            return active[start:] + active[:start]

        # auto：提及优先（点名必达）
        mentioned = [m for m in active if self._mentioned(m, message)]
        if mentioned:
            return mentioned[:max(1, speakers)]
        k = min(speakers, len(active))
        recent = {h.get("character_id") for h in history[-6:] if h["role"] == "assistant"}
        pool = list(active)
        picked: List[dict] = []
        for _ in range(k):
            if not pool:
                break
            weights = [max(1, int(m.get("talkativeness", 50))) *
                       (0.4 if m["id"] in recent else 1.0) for m in pool]
            chosen = rng.choices(pool, weights=weights, k=1)[0]
            # 主动性判定：talkativeness 低于 50 的角色按概率沉默（真人群有人不爱说话）
            ta = int(chosen.get("talkativeness", 50))
            if rng.random() > ta / 100 * 1.4:
                pool.remove(chosen)
                continue
            picked.append(chosen)
            pool.remove(chosen)
        return picked

    @staticmethod
    def _mentioned(member: dict, message: str) -> bool:
        tokens = set(tokenize(message))
        for n in (member.get("name", ""), member.get("nickname", "")):
            if not n:
                continue
            if f"@{n}" in message or n in tokens or f"@{n}" in tokens:
                return True
        return False


class GroupChatService:

    scheduler = GroupScheduler()

    async def chat_stream(self, user_id: str, group_id: str, message: str,
                          reply_mode: str = "auto",
                          target_character_id: Optional[str] = None,
                          interrupt_event: Optional[asyncio.Event] = None,
                          ) -> AsyncGenerator[Dict, None]:
        group = store.get_group(user_id, group_id)
        if not group or not group.get("session_id"):
            yield {"type": "error", "message": "群聊不存在"}
            return
        sid = group["session_id"]
        members = [m for m in (store.get_character(user_id, cid)
                               for cid in group["member_ids"]) if m]
        if not members:
            yield {"type": "error", "message": "群聊没有有效成员"}
            return
        if target_character_id and target_character_id not in group["member_ids"]:
            yield {"type": "error", "message": "指定的角色不在这个群聊里"}
            return

        history_msgs = store.get_messages(sid, limit=GROUP_HISTORY_WINDOW)
        user_turns = store.group_total_user_turns(sid)  # 本轮之前的全局轮次（修 G2）
        store.append_message(sid, "user", message, name="用户")

        verdict = await moderate_input(message)
        if verdict["action"] != "allow":
            logger.info("群聊输入被审核拦截 group=%s category=%s",
                        group_id, verdict["category"])
            text = refusal_for(verdict)
            store.append_message(sid, "assistant", text, name="内容安全",
                                 flags={"guard": verdict["category"]})
            yield {"type": "token", "content": text, "name": "内容安全"}
            yield {"type": "end", "scheduled": [], "moderated": verdict["category"]}
            return

        cfg = group.get("scheduler") or {}
        mode = reply_mode if reply_mode in ("auto", "all", "target") else cfg.get("mode", "auto")
        picked = self.scheduler.pick(
            members, message, history_msgs, group.get("muted_ids", []),
            mode=mode, target_id=target_character_id,
            speakers=int(cfg.get("speakers_per_round", GROUP_SPEAKERS_PER_ROUND)),
            total_user_turns=user_turns)
        if not picked:
            yield {"type": "end", "scheduled": []}
            self._maybe_distill(user_id, group, sid)
            return

        common = {
            "summary": (group.get("summary") or "")[-4000:],
            "older": self._search_older(sid, message),
            "announcement": group.get("announcement") or "",
            "history": history_msgs,
        }
        if GROUP_PARALLEL_REPLY and mode != "all" and len(picked) > 1:
            async for event in self._run_parallel(user_id, sid, picked, message,
                                                  common, members, interrupt_event):
                yield event
        else:
            async for event in self._run_sequential(user_id, sid, picked, message,
                                                    common, members, interrupt_event):
                yield event

        yield {"type": "end", "scheduled": [m["id"] for m in picked]}
        self._maybe_distill(user_id, group, sid)

    # ---------- 顺序模式 ----------

    async def _run_sequential(self, user_id, sid, picked, message, common,
                              members, interrupt_event):
        for member in picked:
            if interrupt_event is not None and interrupt_event.is_set():
                yield {"type": "interrupted", "reason": "user_message"}
                return
            messages = await self._build_member_messages(
                user_id, member, message, common, members)
            yield {"type": "start", "character_id": member["id"], "name": member["name"]}
            full = ""
            cut = False
            try:
                async for chunk in self._stream(messages):
                    if interrupt_event is not None and interrupt_event.is_set():
                        cut = True
                        break
                    full += chunk
                    yield {"type": "token", "content": chunk,
                           "character_id": member["id"], "name": member["name"]}
            except Exception as e:
                logger.exception("群成员发言失败 session=%s character=%s",
                                 sid, member.get("id"))
                yield {"type": "error", "message": f"{member['name']} 发言失败: {e}",
                       "character_id": member["id"]}
                continue
            if full.strip():
                self._save(sid, member, full, interrupted=cut)
            yield {"type": "done", "character_id": member["id"], "name": member["name"]}
            if cut:
                yield {"type": "interrupted", "character_id": member["id"],
                       "name": member["name"], "reason": "user_message"}
                return

    # ---------- 并行模式 ----------

    async def _run_parallel(self, user_id, sid, picked, message, common,
                            members, interrupt_event):
        queue: asyncio.Queue = asyncio.Queue()

        async def worker(member):
            messages = await self._build_member_messages(
                user_id, member, message, common, members)
            await queue.put(("ev", {"type": "start", "character_id": member["id"],
                                    "name": member["name"]}))
            full = ""
            cut = False
            try:
                async for chunk in self._stream(messages):
                    if interrupt_event is not None and interrupt_event.is_set():
                        cut = True
                        break
                    full += chunk
                    await queue.put(("ev", {"type": "token", "content": chunk,
                                            "character_id": member["id"],
                                            "name": member["name"]}))
            except Exception as e:
                await queue.put(("ev", {"type": "error",
                                        "message": f"{member['name']} 发言失败: {e}",
                                        "character_id": member["id"]}))
                await queue.put(("finish", None))
                return
            if full.strip():
                self._save(sid, member, full, interrupted=cut)
            await queue.put(("ev", {"type": "done", "character_id": member["id"],
                                    "name": member["name"]}))
            if cut:
                await queue.put(("ev", {"type": "interrupted",
                                        "character_id": member["id"],
                                        "name": member["name"],
                                        "reason": "user_message"}))
            await queue.put(("finish", None))

        tasks = [asyncio.create_task(worker(m)) for m in picked]
        finished = 0
        try:
            while finished < len(picked):
                kind, payload = await queue.get()
                if kind == "finish":
                    finished += 1
                    continue
                yield payload
                if kind == "ev" and payload.get("type") == "interrupted" \
                        and interrupt_event is not None:
                    interrupt_event.set()  # 让其余 worker 也尽快停止
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    # ---------- 上下文组装 ----------

    async def _build_member_messages(self, user_id, member, message, common,
                                     members) -> List[Dict]:
        hits = await memory_service.recall(
            user_id, member["id"], message, top_k=GROUP_RECALL_TOP_K,
            context_key=f"group:{member['id']}", round_no=1)
        memory_lines = memory_service.format_hits(hits)
        rel = store.get_relation(user_id, member["id"])
        parts = []
        if common.get("announcement"):
            parts.append(f"【群公告/场景设定】\n{common['announcement']}")
        if common.get("summary"):
            parts.append(f"【群聊概要】\n{common['summary'].strip()}")
        if common.get("older"):
            parts.append("【更早的群聊片段（与当前话题相关）】\n" + "\n".join(common["older"]))
        if memory_lines:
            parts.append(f"【你记得的关于用户的事】\n{memory_lines}")
        if rel and (rel["affinity"] != 0.5 or rel["emotion"] != "neutral"):
            parts.append(f"【你对用户当前的感觉】\n好感度 {rel['affinity']:.2f}，"
                         f"情绪 {rel['emotion']}，阶段 {rel['stage']}")
        members_desc = "、".join(
            f"{m['name']}（{(m.get('profile_md') or '')[:30]}）" for m in members)
        history_text = self._render_history_for(common.get("history") or [], member)
        parts.append(f"【最近的群聊（你是 {member['name']}）】\n{history_text}")
        return [
            {"role": "system", "content": GROUP_SYSTEM_PROMPT.format(members=members_desc)},
            {"role": "system", "content": f"【你的角色设定】\n{member.get('profile_md', '')}"},
            {"role": "system", "content": GROUP_CONTEXT_TEMPLATE.format(
                context="\n\n".join(parts), speaker=member["name"])},
            {"role": "user", "content": message},
        ]

    @staticmethod
    def _render_history_for(history: List[dict], member) -> str:
        lines = []
        for h in history:
            if h["role"] == "user":
                lines.append(f"用户: {h['content']}")
            elif h["role"] == "system":
                lines.append(h["content"])
            elif h.get("character_id") == member["id"]:
                lines.append(f"你: {h['content']}")
            else:
                lines.append(f"{h.get('name') or '角色'}: {h['content']}")
        return "\n".join(lines) or "（暂无历史）"

    @staticmethod
    async def _stream(messages):
        from app.llm.adapter import llm_adapter
        async for chunk in llm_adapter.chat_stream(messages, max_tokens=200):
            yield chunk

    @staticmethod
    def _save(sid: str, member: dict, text: str, interrupted: bool = False):
        store.append_message(sid, "assistant", text.strip(),
                             character_id=member["id"], name=member["name"],
                             flags={"interrupted": 1} if interrupted else None)

    def _search_older(self, sid: str, query: str,
                      exclude_last: int = GROUP_HISTORY_WINDOW) -> List[str]:
        all_msgs = store.get_messages(sid, limit=100000)
        older = all_msgs[:-exclude_last] if len(all_msgs) > exclude_last else []
        if not older:
            return []
        kws = tokenize(query)
        if not kws:
            return []
        scored = []
        for idx, m in enumerate(older):
            if m["role"] == "system":
                continue
            hit = sum(1 for kw in kws if kw in m["content"])
            if hit > 0:
                speaker = "用户" if m["role"] == "user" else (m.get("name") or "角色")
                scored.append((hit, idx, f"{speaker}: {m['content']}"))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [s[2] for s in scored[:GROUP_RECALL_TOP_K]]

    def _maybe_distill(self, user_id: str, group: dict, sid: str):
        total = store.count_messages(sid)
        if total - group.get("distilled_upto", 0) > GROUP_HISTORY_WINDOW:
            distill_service.request(user_id, sid, "group")


group_service = GroupService()
group_chat_service = GroupChatService()
