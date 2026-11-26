"""闲置巡检 v2（DB 版）：为睡眠中的用户兜底触发蒸馏入队

旧版遍历文件系统，新版直接 SQL 查询：
- active/archived 会话中，消息数 > distilled_upto 且最后一条消息闲置超阈 → 入队蒸馏
- 蒸馏本身由 DistillService worker 异步执行（sleep-time 模式）
"""
import asyncio
import logging
from typing import Dict

from app.config import SESSION_IDLE_TIMEOUT
from app.db import get_db
from app.services.distill_service import distill_service

logger = logging.getLogger("app.idle")


class IdleSweeper:
    def __init__(self, idle_timeout: int = SESSION_IDLE_TIMEOUT,
                 check_interval: int = 300):
        self.idle_timeout = idle_timeout
        self.check_interval = check_interval
        self._task = None

    def sweep_once(self) -> Dict[str, int]:
        rows = get_db().query(
            "SELECT s.id, s.user_id, s.kind, s.distilled_upto, "
            "  (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS n, "
            "  COALESCE(s.last_message_at, s.created_at) AS last_at "
            "FROM sessions s WHERE s.status IN ('active','archived')")
        chat = group = 0
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(seconds=self.idle_timeout)).isoformat(timespec="seconds")
        for r in rows:
            if r["n"] - r["distilled_upto"] < 2:
                continue
            if (r["last_at"] or "") > cutoff:
                continue
            distill_service.request(r["user_id"], r["id"],
                                    "group" if r["kind"] == "group" else "chat")
            if r["kind"] == "group":
                group += 1
            else:
                chat += 1
        if chat or group:
            logger.info("闲置巡检入队：单聊 %s、群聊 %s", chat, group)
        return {"chat": chat, "group": group}

    async def run_forever(self):
        while True:
            try:
                self.sweep_once()
            except Exception:
                logger.exception("闲置巡检失败（循环继续）")
            await asyncio.sleep(self.check_interval)

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever())

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
