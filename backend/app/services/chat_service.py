"""对话服务 - 编排 LLM 对话、Prompt 组装"""
import uuid
from datetime import datetime
from typing import List, Dict, AsyncGenerator, Optional

from app.config import MAX_HISTORY_MESSAGES
from app.models.schemas import Message, ChatRequest, RecallResult
from app.services.memory_service import memory_service
from app.llm.adapter import llm_adapter
from app.llm.prompts import SYSTEM_PROMPT, RECALL_TRIGGER_KEYWORDS


class ChatService:
    """对话编排服务"""

    def should_recall_by_rules(self, message: str) -> bool:
        """规则预判是否需要 recall（降低延迟，省掉 LLM 决策轮）"""
        for kw in RECALL_TRIGGER_KEYWORDS:
            if kw in message:
                return True
        return False

    async def build_messages(
        self,
        user_id: str,
        character_id: str,
        session_id: str,
        user_message: str,
        recall_results: Optional[List[RecallResult]] = None,
    ) -> List[Dict]:
        """组装对话消息列表"""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # 角色人设
        character_profile = await memory_service.read_character_profile(user_id, character_id)
        if character_profile:
            messages.append({"role": "system", "content": f"【角色设定】\n{character_profile}"})

        # 用户档案
        user_profile = await memory_service.read_user_profile(user_id)
        if user_profile:
            messages.append({"role": "system", "content": f"【用户档案】\n{user_profile}"})

        # 对话历史（滑动窗口）
        history = await memory_service.get_recent_history(
            user_id, character_id, session_id, limit=MAX_HISTORY_MESSAGES
        )
        for msg in history:
            if msg.role in ("user", "assistant"):
                messages.append({"role": msg.role, "content": msg.content})

        # recall 结果（如果有）
        if recall_results:
            recall_text = self._format_recall_results(recall_results)
            messages.append({"role": "system", "content": f"【回忆起的相关信息】\n{recall_text}"})

        # 当前用户输入
        messages.append({"role": "user", "content": user_message})

        return messages

    def _format_recall_results(self, results: List[RecallResult]) -> str:
        """格式化 recall 结果"""
        lines = []
        for r in results:
            source_label = "长期记忆" if r.source == "long-term" else "角色记忆"
            lines.append(f"- [{source_label} {r.date}] {r.content}")
        return "\n".join(lines)

    async def chat_stream(
        self, request: ChatRequest
    ) -> AsyncGenerator[Dict, None]:
        """
        流式对话主流程。
        产出事件：{"type": "token", "content": "..."} 或 {"type": "done", ...}
        """
        user_id = request.user_id
        character_id = request.character_id
        session_id = request.session_id or f"s_{uuid.uuid4().hex[:12]}"

        # 1. 写入用户消息
        user_msg = Message(
            role="user",
            content=request.message,
            timestamp=datetime.now(),
        )
        await memory_service.append_message(user_id, character_id, session_id, user_msg)

        # 2. 规则预判 recall（命中关键词直接检索，不依赖 LLM 工具调用）
        recall_results = None
        if self.should_recall_by_rules(request.message):
            recall_results = await memory_service.recall(
                user_id, character_id, request.message
            )
            # 关键词太泛（如「你还记得我叫什么」）可能零命中，兜底注入最近记忆
            if not recall_results:
                recall_results = await memory_service.get_recent_memories(
                    user_id, character_id, limit=8
                )

        # 3. 组装 Prompt
        messages = await self.build_messages(
            user_id, character_id, session_id, request.message, recall_results
        )

        # 4. 流式调用 LLM（不传 tools，避免某些模型返回空 content）
        full_response = ""
        try:
            async for chunk in llm_adapter.chat_stream(
                messages,
                model=request.model,
                temperature=request.temperature,
            ):
                full_response += chunk
                yield {"type": "token", "content": chunk, "session_id": session_id}
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return

        # 5. 写入 AI 回复
        if full_response.strip():
            ai_msg = Message(
                role="assistant",
                content=full_response,
                timestamp=datetime.now(),
            )
            await memory_service.append_message(user_id, character_id, session_id, ai_msg)

        # 6. 完成
        yield {"type": "done", "session_id": session_id}

    async def end_session(self, user_id: str, character_id: str, session_id: str) -> Dict:
        """结束会话（触发蒸馏由后台任务处理）"""
        from app.services.distill_service import distill_service
        import asyncio
        asyncio.create_task(distill_service.distill_session(user_id, character_id, session_id))
        return {"status": "distilling", "session_id": session_id}


# 全局单例
chat_service = ChatService()
