"""角色服务 v2（SQLite 真相源 + 原始人设保留）

修 01 文档 §6.8：旧版 LLM 解析后丢弃原始文本、编辑再解析是有损往返。
新版 card_json 永远保留原始 raw_content 与解析结果，profile_md 仅为注入视图。
"""
import json
from datetime import datetime
from typing import List, Optional

from app.core.security import new_id
from app.llm.adapter import llm_adapter
from app.llm.prompts import PARSE_CHARACTER_PROMPT
from app.store import store

FALLBACK_FIELDS = ["description", "personality", "speaking_style",
                   "background", "relationship"]


class CharacterService:

    async def create_character(self, user_id: str, name: str, raw_content: str,
                               nickname: str = "", talkativeness: int = 50,
                               cid: Optional[str] = None) -> dict:
        parsed = await self._parse_profile(raw_content)
        profile_md = self._render_profile_md(parsed)
        cid = store.create_character(
            user_id, name, profile_md=profile_md,
            description=parsed.get("description", ""), tags=parsed.get("tags", []),
            card_json={"raw_content": raw_content, "parsed": parsed,
                       "format": "internal_v2"},
            nickname=nickname, talkativeness=talkativeness, cid=cid)
        return store.get_character(user_id, cid)

    async def update_character(self, user_id: str, cid: str, name: str,
                               raw_content: str, **extra) -> Optional[dict]:
        if not store.get_character(user_id, cid):
            return None
        parsed = await self._parse_profile(raw_content)
        fields = {"name": name, "profile_md": self._render_profile_md(parsed),
                  "description": parsed.get("description", ""),
                  "tags": parsed.get("tags", []),
                  "card_json": {"raw_content": raw_content, "parsed": parsed,
                                "format": "internal_v2"}}
        fields.update({k: v for k, v in extra.items()
                       if k in ("nickname", "talkativeness", "voice_config")})
        store.update_character(user_id, cid, **fields)
        return store.get_character(user_id, cid)

    def get_character(self, user_id: str, cid: str) -> Optional[dict]:
        return store.get_character(user_id, cid)

    def list_characters(self, user_id: str) -> List[dict]:
        return store.list_characters(user_id)

    def delete_character(self, user_id: str, cid: str) -> bool:
        return store.delete_character(user_id, cid)

    async def _parse_profile(self, raw_content: str) -> dict:
        try:
            response = await llm_adapter.chat(
                [{"role": "user",
                  "content": PARSE_CHARACTER_PROMPT.format(raw_content=raw_content)}],
                model="distill", temperature=0.3)
            parsed = llm_extract_json(response.get("content", ""))
            if parsed:
                return parsed
        except Exception:
            pass
        return {"description": raw_content[:200], "personality": "",
                "speaking_style": "", "background": raw_content,
                "relationship": "", "tags": []}

    @staticmethod
    def _render_profile_md(p: dict) -> str:
        return (
            f"## 基本设定\n{p.get('description', '')}\n\n"
            f"## 性格\n{p.get('personality', '')}\n\n"
            f"## 说话风格\n{p.get('speaking_style', '')}\n\n"
            f"## 背景故事\n{p.get('background', '')}\n\n"
            f"## 关系设定\n{p.get('relationship', '')}"
        )


def llm_extract_json(text: str) -> dict:
    from app.llm.adapter import extract_json
    return extract_json(text) or {}


character_service = CharacterService()
