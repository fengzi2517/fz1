"""闲置会话巡检 - 超过 SESSION_IDLE_TIMEOUT 无新消息时自动触发记忆蒸馏

详细设计中的蒸馏触发条件是「用户主动结束会话 / 会话闲置超过 30 分钟」。
前端离开页面时会调用 /end，但用户直接杀掉 App 时不会触发，因此由后端定时兜底：
- 单聊：扫描 users/{uid}/characters/{cid}/sessions/{sid}/history.jsonl
- 群聊：扫描 users/{uid}/groups/{gid}/history.jsonl

蒸馏本身是增量的（summary.md / meta.json 里的 distilled_upto 游标），
因此重复扫描已蒸馏过的会话不会重复调用 LLM，也不会重复写入记忆。
"""
import asyncio
import time
from typing import Dict

from app.config import DATA_DIR, SESSION_IDLE_TIMEOUT
from app.services.distill_service import distill_service
from app.services.group_service import group_distill_service


class IdleSweeper:
    """闲置会话巡检器"""

    def __init__(
        self,
        idle_timeout: int = SESSION_IDLE_TIMEOUT,
        check_interval: int = 300,
    ):
        self.idle_timeout = idle_timeout
        self.check_interval = check_interval
        self._task = None

    # ---------- 扫描 ----------

    def _idle(self, history_path, now: float) -> bool:
        try:
            return now - history_path.stat().st_mtime >= self.idle_timeout
        except OSError:
            return False

    def _iter_users(self):
        users_dir = DATA_DIR / "users"
        if not users_dir.exists():
            return
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir():
                yield user_dir.name, user_dir

    async def sweep_once(self) -> Dict[str, int]:
        """执行一轮巡检，返回 {单聊蒸馏数, 群聊蒸馏数}"""
        now = time.time()
        chat_count = 0
        group_count = 0

        for user_id, user_dir in self._iter_users():
            # 单聊
            chars_dir = user_dir / "characters"
            if chars_dir.exists():
                for char_dir in chars_dir.iterdir():
                    sessions_dir = char_dir / "sessions"
                    if not sessions_dir.exists():
                        continue
                    for session_dir in sessions_dir.iterdir():
                        history = session_dir / "history.jsonl"
                        if not history.exists() or not self._idle(history, now):
                            continue
                        await distill_service.distill_session(
                            user_id, char_dir.name, session_dir.name
                        )
                        chat_count += 1

            # 群聊
            groups_dir = user_dir / "groups"
            if groups_dir.exists():
                for group_dir in groups_dir.iterdir():
                    history = group_dir / "history.jsonl"
                    if not history.exists() or not self._idle(history, now):
                        continue
                    await group_distill_service.distill_group(user_id, group_dir.name)
                    group_count += 1

        if chat_count or group_count:
            print(
                f"[IdleSweeper] 巡检完成：单聊 {chat_count} 个、群聊 {group_count} 个闲置会话已处理"
            )
        return {"chat": chat_count, "group": group_count}

    # ---------- 定时任务 ----------

    async def run_forever(self):
        while True:
            try:
                await self.sweep_once()
            except Exception as e:
                print(f"[IdleSweeper] 巡检失败: {e}")
            await asyncio.sleep(self.check_interval)

    def start(self):
        """随应用启动后台巡检任务"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever())
            print(
                f"[IdleSweeper] 已启动：空闲阈值 {self.idle_timeout}s，巡检间隔 {self.check_interval}s"
            )

    async def stop(self):
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None


idle_sweeper = IdleSweeper()
