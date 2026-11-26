"""蒸馏 worker 循环：队列消费、孤儿任务回收、失败落库与再触发重试

`test_distill.py` 直接调 `run_job()`，这里补上真实 `start_worker/stop_worker`
的异步消费路径（P1 事务化蒸馏的运行时形态）。
"""
import asyncio

import pytest

from app.services import distill_service as ds_mod
from app.services.distill_service import distill_service
from app.services.idle_service import IdleSweeper
from app.store import store

IDLE_TS = "2020-01-01T00:00:00"


@pytest.fixture
def worker(monkeypatch):
    """干净 worker 状态 + 短轮询（生产 2s 会拖慢测试）"""
    monkeypatch.setattr(ds_mod, "WORKER_POLL_INTERVAL", 0.02)
    distill_service._worker_task = None
    distill_service._stop = asyncio.Event()
    yield distill_service
    distill_service._worker_task = None
    distill_service._stop = asyncio.Event()


@pytest.fixture
def session(memory_db):
    uid = store.create_user("dw", "hash")
    cid = store.create_character(uid, "阿强", profile_md="# 阿强")
    sid = store.create_session(uid, "char", character_id=cid)
    return uid, cid, sid


def add_messages(sid, n, idle=False):
    for i in range(n):
        store.append_message(sid, "user" if i % 2 == 0 else "assistant", f"聊{i}")
    if idle:
        store.db.execute("UPDATE sessions SET last_message_at=? WHERE id=?", (IDLE_TS, sid))


def job_row(jid):
    return store.db.query_one("SELECT * FROM distill_jobs WHERE id=?", (jid,))


async def drain(pending_before=None, timeout=3.0):
    """等 worker 把队列清空"""
    for _ in range(int(timeout / 0.01)):
        if store.jobs_pending() == 0:
            return True
        await asyncio.sleep(0.01)
    return False


class TestWorkerLoop:
    async def test_consumes_queued_job(self, worker, session, fake_llm):
        uid, cid, sid = session
        fake_llm.chat_content = "用户与阿强聊了天气。"
        add_messages(sid, 4)
        jid = distill_service.request(uid, sid, "chat")
        worker.start_worker()
        assert await drain(), "worker 未消费队列"
        job = job_row(jid)
        assert job["status"] == "done" and job["attempts"] == 1 and job["done_at"]
        s = store.get_session(sid)
        assert "用户与阿强聊了天气" in s["summary"]
        assert s["distilled_upto"] == 4

    async def test_reclaims_orphan_running_job_at_startup(self, worker, session, fake_llm):
        uid, cid, sid = session
        fake_llm.chat_content = "断点续跑。"
        add_messages(sid, 4)
        jid = store.enqueue_job(sid, "chat", 0, 4)
        # 模拟上一进程被 kill：任务卡在 running
        store.db.execute("UPDATE distill_jobs SET status='running' WHERE id=?", (jid,))
        assert store.jobs_pending() == 1
        worker.start_worker()  # start 里 reset_running_jobs
        assert await drain(), "孤儿任务未被回收"
        assert job_row(jid)["status"] == "done"
        assert store.get_session(sid)["distilled_upto"] == 4

    async def test_drains_multiple_sessions_fifo(self, worker, session, fake_llm):
        uid, cid, sid = session
        sid2 = store.create_session(uid, "char", character_id=cid)
        fake_llm.chat_content = "两路摘要。"
        add_messages(sid, 4)
        add_messages(sid2, 6)
        j1 = distill_service.request(uid, sid, "chat")
        j2 = distill_service.request(uid, sid2, "chat")
        worker.start_worker()
        assert await drain()
        assert job_row(j1)["status"] == job_row(j2)["status"] == "done"
        assert store.get_session(sid)["distilled_upto"] == 4
        assert store.get_session(sid2)["distilled_upto"] == 6

    async def test_stop_worker_ends_loop(self, worker, session, fake_llm):
        uid, cid, sid = session
        fake_llm.chat_content = "摘要。"
        worker.start_worker()
        await worker.stop_worker()
        assert worker._worker_task is None
        add_messages(sid, 4)
        jid = distill_service.request(uid, sid, "chat")
        await asyncio.sleep(0.1)
        assert job_row(jid)["status"] == "pending", "已停服的 worker 仍在领任务"

    async def test_loop_survives_job_exception(self, worker, session, fake_llm, monkeypatch):
        """一个任务抛错不能把整条队列打死"""
        uid, cid, sid = session
        fake_llm.chat_content = "第二批摘要。"
        real_run = distill_service.run_job
        calls = {"n": 0}

        async def flaky(job):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("LLM 挂了")
            await real_run(job)

        monkeypatch.setattr(distill_service, "run_job", flaky)
        add_messages(sid, 4)
        j1 = distill_service.request(uid, sid, "chat")
        worker.start_worker()
        for _ in range(200):
            if job_row(j1)["status"] in ("done", "failed"):
                break
            await asyncio.sleep(0.01)
        assert job_row(j1)["status"] == "failed"
        assert store.get_session(sid)["distilled_upto"] == 0  # 首个失败零污染
        j2 = store.enqueue_job(sid, "chat", 0, 6)
        add_messages(sid, 2)
        assert await drain(), "worker 已死"
        assert job_row(j2)["status"] == "done"
        assert store.get_session(sid)["distilled_upto"] == 6


class TestFailureRecovery:
    async def test_failed_job_leaves_no_partial_state(self, worker, session, fake_llm):
        uid, cid, sid = session
        fake_llm.chat_content = ""  # 摘要为空 → _plan_chat 抛错
        add_messages(sid, 4)
        jid = distill_service.request(uid, sid, "chat")
        worker.start_worker()
        for _ in range(200):
            if job_row(jid)["status"] == "failed":
                break
            await asyncio.sleep(0.01)
        job = job_row(jid)
        assert job["status"] == "failed"
        assert "摘要" in job["last_error"]
        s = store.get_session(sid)
        assert not s["summary"] and s["distilled_upto"] == 0
        assert store.jobs_pending() == 0
        assert not store.list_memories(uid)  # 记忆也未半截落地

    async def test_retry_after_failure_advances_cursor(self, worker, session, fake_llm):
        """failed 不挡重入：游标未推进 → 再触发同区间 → 本轮成功"""
        uid, cid, sid = session
        fake_llm.chat_content = ""
        add_messages(sid, 4)
        first = distill_service.request(uid, sid, "chat")
        worker.start_worker()
        for _ in range(200):
            if job_row(first)["status"] == "failed":
                break
            await asyncio.sleep(0.01)
        await worker.stop_worker()

        fake_llm.chat_content = "重试后的摘要。"
        second = distill_service.request(uid, sid, "chat")
        assert second != first
        assert (job_row(second)["range_start"], job_row(second)["range_end"]) == (0, 4)
        worker.start_worker()
        assert await drain()
        assert job_row(second)["status"] == "done"
        assert store.get_session(sid)["distilled_upto"] == 4

    async def test_idle_sweeper_requeues_failed_range(self, session, fake_llm):
        """无人再发消息时，闲置巡检负责重试失败的蒸馏"""
        uid, cid, sid = session
        add_messages(sid, 4, idle=True)
        store.enqueue_job(sid, "chat", 0, 4)
        store.db.execute(
            "UPDATE distill_jobs SET status='failed', last_error='boom' WHERE session_id=?",
            (sid,))
        stats = IdleSweeper(idle_timeout=1800).sweep_once()
        assert stats == {"chat": 1, "group": 0}
        assert store.jobs_pending() == 1


class TestStopIdempotent:
    async def test_stop_without_start(self, worker):
        await worker.stop_worker()
        assert worker._worker_task is None

    async def test_double_stop(self, worker, session, fake_llm):
        uid, cid, sid = session
        fake_llm.chat_content = "摘要。"
        add_messages(sid, 4)
        distill_service.request(uid, sid, "chat")
        worker.start_worker()
        worker.start_worker()  # 已在跑，不应重复建 task
        await worker.stop_worker()
        await worker.stop_worker()
        assert worker._worker_task is None
