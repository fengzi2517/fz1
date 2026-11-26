"""P0「生命周期与日志」：后台任务与失败路径必须留下可运维的日志

动机：此前只有 main.py 有 logger，IdleSweeper 用 print，而蒸馏 worker 失败
只写 DB 不打日志——线上排查时完全看不见蒸馏在静默重试。
"""
import asyncio
import logging

import httpx
import pytest

from app.llm.adapter import llm_adapter
from app.main import setup_logging
from app.services import distill_service as ds_mod
from app.services.distill_service import distill_service
from app.services.idle_service import IdleSweeper
from app.store import store

IDLE_TS = "2020-01-01T00:00:00"


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setattr(ds_mod, "WORKER_POLL_INTERVAL", 0.02)
    distill_service._worker_task = None
    distill_service._stop = asyncio.Event()
    yield distill_service
    distill_service._worker_task = None
    distill_service._stop = asyncio.Event()


@pytest.fixture
def sess(memory_db):
    uid = store.create_user("lg", "hash")
    cid = store.create_character(uid, "阿强", profile_md="# 阿强")
    return uid, cid, store.create_session(uid, "char", character_id=cid)


def add(sid, n, idle=False):
    for i in range(n):
        store.append_message(sid, "user" if i % 2 == 0 else "assistant", f"聊{i}")
    if idle:
        store.db.execute("UPDATE sessions SET last_message_at=? WHERE id=?", (IDLE_TS, sid))


class TestSetupLogging:
    def test_applies_requested_level(self):
        assert setup_logging("DEBUG") == logging.DEBUG
        assert logging.getLogger().level == logging.DEBUG
        setup_logging("INFO")

    def test_unknown_level_falls_back_to_info(self):
        assert setup_logging("nonsense") == logging.INFO

    def test_idempotent_no_duplicate_handlers(self):
        root = logging.getLogger()
        before = len([h for h in root.handlers if getattr(h, "_app_fmt", False)])
        setup_logging("INFO")
        setup_logging("INFO")
        after = len([h for h in root.handlers if getattr(h, "_app_fmt", False)])
        assert before == after == 1, "重复导入不应叠加 handler（uvicorn reload 场景）"

    def test_noisy_third_party_loggers_quiet(self):
        setup_logging("INFO")
        assert logging.getLogger("jieba").level >= logging.WARNING
        assert logging.getLogger("httpcore").level >= logging.WARNING


class TestDistillWorkerLogs:
    async def test_success_logged(self, worker, sess, fake_llm, caplog):
        uid, cid, sid = sess
        fake_llm.chat_content = "摘要。"
        add(sid, 4)
        jid = distill_service.request(uid, sid, "chat")
        caplog.set_level(logging.INFO, logger="app.distill")
        worker.start_worker()
        for _ in range(300):
            if store.jobs_pending() == 0:
                break
            await asyncio.sleep(0.01)
        assert any("蒸馏完成" in r.getMessage() and str(jid) in r.getMessage()
                   for r in caplog.records), [r.getMessage() for r in caplog.records]

    async def test_failure_logged_with_error_level(self, worker, sess, fake_llm, caplog):
        uid, cid, sid = sess
        fake_llm.chat_content = ""  # 空摘要 → 抛错
        add(sid, 4)
        jid = distill_service.request(uid, sid, "chat")
        caplog.set_level(logging.INFO, logger="app.distill")
        worker.start_worker()
        for _ in range(300):
            row = store.db.query_one("SELECT status FROM distill_jobs WHERE id=?", (jid,))
            if row and row["status"] == "failed":
                break
            await asyncio.sleep(0.01)
        errs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errs, "失败必须留下 ERROR 日志（原先静默）"
        assert "蒸馏失败" in errs[0].getMessage() and str(jid) in errs[0].getMessage()
        assert errs[0].exc_info, "应带 traceback 便于定位 LLM/事务失败点"


class TestIdleSweeperLogs:
    def test_enqueue_logged_not_printed(self, sess, caplog, capsys):
        uid, cid, sid = sess
        add(sid, 4, idle=True)
        caplog.set_level(logging.INFO, logger="app.idle")
        IdleSweeper(idle_timeout=1800).sweep_once()
        assert any("闲置巡检入队" in r.getMessage() for r in caplog.records)
        assert "IdleSweeper" not in capsys.readouterr().out, "不应再走 print"

    async def test_sweep_exception_keeps_level_error(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger="app.idle")
        sweeper = IdleSweeper(idle_timeout=1800, check_interval=0.01)

        def boom():
            raise RuntimeError("db 锁住")
        monkeypatch.setattr(sweeper, "sweep_once", boom)

        sweeper.start()
        await asyncio.sleep(0.08)
        # 必须走 start()/stop() 配对：stop() 只回收 self._task，
        # 自己 create_task 会让 stop() 空转、后续 await 死等 check_interval
        await sweeper.stop()
        errs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errs and "闲置巡检失败" in errs[0].getMessage()
        assert sweeper._task is None


class TestGatewayLogs:
    class _Resp:
        def __init__(self, status, payload=None):
            self.status_code, self._payload = status, payload or {}
            self.request = None

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("boom", request=None, response=self)

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, statuses):
            self.statuses, self.calls = statuses, []

        async def post(self, url, json=None):
            self.calls.append(json)
            s = self.statuses[min(len(self.calls) - 1, len(self.statuses) - 1)]
            if isinstance(s, Exception):
                raise s
            return TestGatewayLogs._Resp(s, {"choices": [{"message": {"content": "ok"}}]})

    async def test_retry_emits_warning(self, monkeypatch, caplog):
        caplog.set_level(logging.WARNING, logger="app.llm")
        monkeypatch.setattr(llm_adapter, "client",
                            self._Client([429, httpx.ConnectError("down"), 200]))
        msg = await llm_adapter.chat([{"role": "user", "content": "hi"}])
        assert msg["content"] == "ok"
        msgs = [r.getMessage() for r in caplog.records]
        assert any("返回 429" in m for m in msgs), msgs
        assert any("网络错误 ConnectError" in m for m in msgs), msgs

    async def test_logs_never_leak_api_key(self, monkeypatch, caplog):
        caplog.set_level(logging.DEBUG, logger="app.llm")
        monkeypatch.setattr(llm_adapter, "client", self._Client([500, 200]))
        await llm_adapter.chat([{"role": "user", "content": "别记下来"}])
        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert "别记下来" not in blob, "日志不应包含对话内容"
