"""对话服务 v2（P1-1 摘要回注 / P1-2 token 预算装配 / 每轮自动召回）

相对旧版：
- 上下文不再固定 20 条，而是按 token 预算分区装配（常驻设定/摘要/召回/历史）
- summary 回注：跨会话、跨天不再失忆（修 §3.2 断点、D3）
- recall 每轮自动执行（旧 16 触发词规则降级为无需再判——混合检索成本低）
- 关系状态注入（好感度/情绪），为表情差分与主动性提供依据
- 轮换显式化：预算超限或跨天时开新会话并带走旧会话摘要（修 D4）
"""
import logging
import uuid
from datetime import datetime
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from app.config import (
    CTX_WINDOW_TOKENS, CTX_BUDGET_PROFILE, CTX_BUDGET_SUMMARY, CTX_BUDGET_RECALL,
    CTX_BUDGET_HISTORY_MIN, MAX_HISTORY_MESSAGES, LLM_MAX_TOKENS, SESSION_MAX_MESSAGES,
)
from app.core.textutil import estimate_tokens
from app.models.schemas import ChatRequest
from app.services.distill_service import distill_service
from app.services.memory_service import memory_service
from app.store import store


logger = logging.getLogger("app.chat")

SYSTEM_PROMPT_V2 = """你是一个专业的角色扮演AI。你需要严格遵循给定的角色设定进行对话。

## 行为准则
1. 始终以角色身份说话，不要跳出角色
2. 回复要符合角色的性格、说话风格和背景设定
3. 不要提及自己是AI或大语言模型
4. 回复要自然、有代入感，避免生硬

## 关于记忆
系统会自动为你检索相关的历史记忆、会话摘要与用户档案（见上下文各板块）。
请自然地运用这些信息：记得用户的偏好与约定，延续之前的剧情与关系。
信息可能不完整，不要虚构未提供的细节，可以试探性地确认。
不要调用任何工具，直接以角色身份回复。"""


def rotate_reason(messages: List[dict]) -> Optional[str]:
    """轮换判据：条数超限或首条消息跨天（不再是"跨天即轮"）"""
    if len(messages) >= SESSION_MAX_MESSAGES:
        return "max_messages"
    if messages:
        first = (messages[0].get("created_at") or "")[:10]
        if first and first != datetime.now().strftime("%Y-%m-%d"):
            return "cross_day"
    return None


def assemble_context(
    character_md: str, user_profile: str, summary: str,
    memory_lines: str, relation_line: str,
    history: List[dict], current_message: str,
    window: int = CTX_WINDOW_TOKENS, max_output: int = LLM_MAX_TOKENS,
) -> Tuple[List[Dict], Dict]:
    """纯函数：按 token 预算装配消息列表（可单测）。
    预算优先级：常驻设定 > 摘要 > 召回 > 近期历史（保底 40%）。
    """
    budget = max(window - max_output - estimate_tokens(current_message) - 200, 512)
    b_profile = int(budget * CTX_BUDGET_PROFILE)
    b_summary = int(budget * CTX_BUDGET_SUMMARY)
    b_recall = int(budget * CTX_BUDGET_RECALL)
    used = 0
    blocks = []

    def take(label: str, text: str, cap: int):
        nonlocal used
        if not text:
            return
        t = estimate_tokens(text)
        if t > cap:
            # 按句边界截断（修 G9 截在句中）
            text = truncate_at_sentence(text, cap)
            t = estimate_tokens(text)
        if used + t > budget:
            return
        used += t
        blocks.append((label, text))

    if character_md:
        take("【角色设定】", character_md, b_profile // 2)
    if user_profile:
        take("【用户档案】", user_profile, b_profile // 2)
    take("【此前剧情摘要】", summary, b_summary)
    take("【相关的长期记忆】", memory_lines, b_recall)
    if relation_line:
        take("【你对用户当前的感觉】", relation_line, 80)

    # 历史保底预算 = 总预算 - 已用 - 召回预算保留位（召回若没用满则让给历史）
    used_by_recall = sum(1 for label, _ in blocks if label == "【相关的长期记忆】")
    b_history = int(budget * CTX_BUDGET_HISTORY_MIN)
    if not used_by_recall:
        b_history += b_recall
    hist_budget = max(b_history, budget - used)
    picked: List[dict] = []
    for m in reversed(history):
        if m["role"] not in ("user", "assistant"):
            continue
        cost = estimate_tokens(m["content"]) + 2
        if used + cost > hist_budget and len(picked) >= min(4, MAX_HISTORY_MESSAGES):
            break
        picked.append(m)
        used += cost
        if len(picked) >= MAX_HISTORY_MESSAGES * 3:
            break
    picked.reverse()

    messages: List[Dict] = [{"role": "system", "content": SYSTEM_PROMPT_V2}]
    for label, text in blocks:
        messages.append({"role": "system", "content": f"{label}\n{text}"})
    for m in picked:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": current_message})
    stats = {"budget": budget, "used": used, "history_turns": len(picked),
             "blocks": [b[0] for b in blocks]}
    return messages, stats


def truncate_at_sentence(text: str, token_cap: int) -> str:
    """从尾部保留，按句边界截断到 token_cap 以内"""
    if estimate_tokens(text) <= token_cap:
        return text
    keep = text
    while estimate_tokens(keep) > token_cap and len(keep) > 40:
        target = max(0, len(keep) - max(len(keep) // 3, 20))
        cut = -1
        for sep in "。！？；\n.!?;":
            i = keep.find(sep, target)
            if i != -1 and (cut == -1 or i < cut):
                cut = i
        keep = keep[cut + 1:] if cut != -1 else keep[target:]
    return ("…" + keep) if keep != text else text


class ChatService:

    def resolve_session(self, user_id: str, character_id: str,
                        session_id: Optional[str]) -> Tuple[dict, bool]:
        """返回 (session, rotated)。轮换时旧会话归档并蒸馏，开新会话。"""
        if session_id:
            sess = store.get_session(session_id)
            if sess and sess["user_id"] == user_id:
                return sess, False
        latest = store.latest_session(user_id, character_id)
        if not latest:
            return self._new_session(user_id, character_id), False
        messages = store.get_messages(latest["id"])
        if rotate_reason(messages):
            distill_service.request(user_id, latest["id"], "chat")
            store.set_session(latest["id"], status="archived")
            new_sess = self._new_session(user_id, character_id)
            # 新会话直接继承旧会话滚动摘要（跨会话连续）
            if latest.get("summary"):
                store.set_session(new_sess["id"], summary=latest["summary"])
                new_sess = store.get_session(new_sess["id"])
            return new_sess, True
        return latest, False

    def _new_session(self, user_id: str, character_id: str) -> dict:
        sid = store.create_session(user_id, "char", character_id=character_id)
        return store.get_session(sid)

    async def build_messages(self, user_id: str, character_id: str, session_id: str,
                             user_message: str) -> Tuple[List[Dict], Dict]:
        character = store.get_character(user_id, character_id) or {}
        sess = store.get_session(session_id) or {}
        hits = await memory_service.recall(
            user_id, character_id, user_message, context_key=session_id,
            round_no=store.count_messages(session_id))
        memory_lines = memory_service.format_hits(hits)
        relation_line = ""
        rel = store.get_relation(user_id, character_id)
        if rel and (rel["affinity"] != 0.5 or rel["emotion"] != "neutral"):
            relation_line = f"好感度 {rel['affinity']:.2f}，当前情绪 {rel['emotion']}，关系阶段 {rel['stage']}"
        history = store.get_messages(session_id, limit=MAX_HISTORY_MESSAGES * 3)
        return assemble_context(
            character_md=character.get("profile_md", ""),
            user_profile=memory_service.get_profile(user_id),
            summary=sess.get("summary", ""),
            memory_lines=memory_lines,
            relation_line=relation_line,
            history=history,
            current_message=user_message,
        )

    async def chat_stream(self, request: ChatRequest) -> AsyncGenerator[Dict, None]:
        user_id = request.user_id
        character_id = request.character_id
        session, rotated = self.resolve_session(
            user_id, character_id, request.session_id)
        session_id = session["id"]

        store.append_message(session_id, "user", request.message, name="用户")
        messages, stats = await self.build_messages(
            user_id, character_id, session_id, request.message)

        full_response = ""
        try:
            async for chunk in llm_stream(messages, request):
                full_response += chunk
                yield {"type": "token", "content": chunk, "session_id": session_id}
        except Exception as e:
            logger.exception("单聊流式失败 session=%s", session_id)
            yield {"type": "error", "message": str(e)}
            return

        if full_response.strip():
            mid = store.append_message(session_id, "assistant", full_response)
            # 攒够一段就蒸馏（sleep-time worker 异步执行，不阻塞对话）
            if store.count_messages(session_id) - session["distilled_upto"] >= 12:
                distill_service.request(user_id, session_id, "chat")
        yield {"type": "done", "session_id": session_id, "rotated": rotated,
               "context_stats": stats}

    async def end_session(self, user_id: str, session_id: str) -> Dict:
        job_id = distill_service.request(user_id, session_id, "chat")
        return {"status": "queued" if job_id else "up_to_date", "session_id": session_id,
                "job_id": job_id}


async def llm_stream(messages: List[Dict], request: ChatRequest) -> AsyncGenerator[str, None]:
    from app.llm.adapter import llm_adapter
    async for chunk in llm_adapter.chat_stream(
            messages, model=request.model, temperature=request.temperature):
        yield chunk


chat_service = ChatService()
