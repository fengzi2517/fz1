"""角色服务 - 负责人设导入解析与管理"""
import json
import uuid
from datetime import datetime
from typing import List, Optional
import aiofiles

from app.config import get_user_dir, get_character_dir, ensure_dir
from app.models.schemas import CharacterProfile
from app.llm.adapter import llm_adapter
from app.llm.prompts import PARSE_CHARACTER_PROMPT


class CharacterService:
    """角色人设管理服务"""

    async def create_character(self, user_id: str, name: str, raw_content: str) -> CharacterProfile:
        """创建角色：LLM 解析人设文本，写入 profile.md"""
        character_id = f"c_{uuid.uuid4().hex[:12]}"
        ensure_dir(get_character_dir(user_id, character_id))

        profile = await self._parse_profile(character_id, name, raw_content)

        # 写入 profile.md
        await self._write_profile(user_id, character_id, name, profile, raw_content)

        return profile

    async def _parse_profile(
        self, character_id: str, name: str, raw_content: str
    ) -> CharacterProfile:
        """调用 LLM 把人设文本解析为结构化档案（不落盘）"""
        # 调用 LLM 解析结构化人设
        try:
            response = await llm_adapter.chat(
                messages=[{"role": "user", "content": PARSE_CHARACTER_PROMPT.format(raw_content=raw_content)}],
                temperature=0.3,
            )
            parsed = self._parse_json_response(response.get("content", ""))
        except Exception:
            # LLM 解析失败，用原始内容兜底
            parsed = {
                "description": raw_content[:200],
                "personality": "",
                "speaking_style": "",
                "background": raw_content,
                "relationship": "",
                "tags": [],
            }

        return CharacterProfile(
            character_id=character_id,
            name=name,
            description=parsed.get("description", ""),
            personality=parsed.get("personality", ""),
            speaking_style=parsed.get("speaking_style", ""),
            background=parsed.get("background", ""),
            relationship=parsed.get("relationship", ""),
            tags=parsed.get("tags", []),
            created_at=datetime.now(),
        )

    async def get_character(self, user_id: str, character_id: str) -> Optional[CharacterProfile]:
        """获取角色信息"""
        path = get_character_dir(user_id, character_id) / "profile.md"
        if not path.exists():
            return None
        from app.utils.markdown_parser import parse_front_matter
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
        meta, body = parse_front_matter(content)
        return CharacterProfile(
            character_id=character_id,
            name=meta.get("name") or character_id,
            description=self._section(body, "基本设定"),
            personality=self._section(body, "性格"),
            speaking_style=self._section(body, "说话风格"),
            background=self._section(body, "背景故事"),
            relationship=self._section(body, "关系设定"),
            tags=meta.get("tags") or [],
            created_at=self._parse_datetime(meta.get("created_at")),
            raw_content=body.strip(),
        )

    @staticmethod
    def _section(body: str, title: str) -> str:
        """提取 markdown 中某个二级标题下的内容"""
        import re
        match = re.search(
            rf"^##\s+{title}\s*$(.*?)(?=^##\s|\Z)",
            body,
            re.MULTILINE | re.DOTALL,
        )
        return match.group(1).strip() if match else ""

    @staticmethod
    def _parse_datetime(value) -> Optional[datetime]:
        """兼容 YAML 已解析为 datetime 或原始字符串两种情况"""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None

    async def list_characters(self, user_id: str) -> List[CharacterProfile]:
        """列出用户的所有角色"""
        chars_dir = get_user_dir(user_id) / "characters"
        if not chars_dir.exists():
            return []
        characters = []
        for char_dir in chars_dir.iterdir():
            if char_dir.is_dir():
                profile = await self.get_character(user_id, char_dir.name)
                if profile:
                    characters.append(profile)
        return characters

    async def update_character(self, user_id: str, character_id: str, name: str, raw_content: str) -> CharacterProfile:
        """更新角色人设：就地重写 profile.md，不新建角色目录"""
        path = get_character_dir(user_id, character_id) / "profile.md"
        if not path.exists():
            return None

        profile = await self._parse_profile(character_id, name, raw_content)
        await self._write_profile(user_id, character_id, name, profile, raw_content)
        return profile

    async def delete_character(self, user_id: str, character_id: str) -> bool:
        """删除角色（归档而非真删，M0 简化为直接删除目录）"""
        import shutil
        path = get_character_dir(user_id, character_id)
        if path.exists():
            shutil.rmtree(path)
            return True
        return False

    async def _write_profile(self, user_id: str, character_id: str, name: str, profile: CharacterProfile, raw_content: str):
        """写入角色 profile.md"""
        path = ensure_dir(get_character_dir(user_id, character_id)) / "profile.md"
        now = datetime.now().isoformat()
        front_matter = (
            f"---\n"
            f"character_id: {character_id}\n"
            f"name: {name}\n"
            f"user_id: {user_id}\n"
            f"created_at: {now}\n"
            f"tags: {profile.tags}\n"
            f"---\n\n"
        )
        body = (
            f"# {name}\n\n"
            f"## 基本设定\n{profile.description}\n\n"
            f"## 性格\n{profile.personality}\n\n"
            f"## 说话风格\n{profile.speaking_style}\n\n"
            f"## 背景故事\n{profile.background}\n\n"
            f"## 关系设定\n{profile.relationship}\n"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(front_matter + body)

    def _parse_json_response(self, text: str) -> dict:
        """从 LLM 响应中提取 JSON"""
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 尝试从代码块中提取
        import re
        match = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        # 尝试提取第一个 { ... }
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {}


# 全局单例
character_service = CharacterService()
