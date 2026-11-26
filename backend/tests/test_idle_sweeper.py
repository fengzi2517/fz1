"""闲置巡检（P0 生命周期）：仅对闲置超阈且有未蒸馏增量的会话入队，异常不杀循环"""
import asyncio
from datetime import datetime, timedelta

import pytest

from app.db import get_db
from app.services.idle_service import IdleSweeper
from app.store import store

IDLE_TS = "2020-01-01T00:00:00"


def _user() -> str:
    return store.create_user("sweep_user", "x" * 32)


def _chat_session(uid, n=4, upto=0, idle=True, status="active"):
    cid = store.create_character(uid, "阿强", profile_md="设定")
    sid = store.create_session(uid, "char", character_id=cid)
    store.set_session(sid, status=status)
    for i in range(n):
        store.append_message(sid, "assistant" if i % 2 else "user", f"第{i}句内容")
    store.set_session(sid, distilled_upto=upto,
                      last_message_at=IDLE_TS if idle else datetime.now().isoformat(timespec="seconds"))
    return sid


def _group_session(uid, n=4, idle=True):
    c1 = store.create_character(uid, "阿强", profile_md="设定")
    c2 = store.create_character(uid, "小美", profile_md="设定")
    gid = store.create_group(uid, "二人组", [c1, c2])
    sid = store.get_group(uid, gid)["session_id"]
    for i in range(n):
        store.append_message(sid, "user", f"群消息{i}")
    if idle:
        store.set_session(sid, last_message_at=IDLE_TS)
    return sid


def _jobs():
    return [dict(r) for r in get_db().query("SELECT * FROM distill_jobs ORDER BY id")]


def test_sweep_enqueues_idle_chat_session():
    uid = _user()
    sid = _chat_session(uid)
    assert IdleSweeper(idle_timeout=60).sweep_once() == {"chat": 1, "group": 0}
    jobs = _jobs()
    assert len(jobs) == 1
    assert jobs[0]["session_id"] == sid and jobs[0]["kind"] == "chat"
    assert (jobs[0]["range_start"], jobs[0]["range_end"]) == (0, 4)


def test_sweep_skips_recently_active_session():
    uid = _user()
    _chat_session(uid, idle=False)
    assert IdleSweeper(idle_timeout=3600).sweep_once() == {"chat": 0, "group": 0}
    assert store.jobs_pending() == 0


def test_sweep_skips_fully_distilled_session():
    uid = _user()
    _chat_session(uid, n=4, upto=4)
    assert IdleSweeper(idle_timeout=60).sweep_once() == {"chat": 0, "group": 0}


def test_sweep_skips_increment_below_threshold():
    """只剩 1 条新消息时不打扰蒸馏（与 distill 阈值一致）"""
    uid = _user()
    _chat_session(uid, n=4, upto=3)
    assert IdleSweeper(idle_timeout=60).sweep_once() == {"chat": 0, "group": 0}


def test_sweep_range_starts_at_cursor():
    uid = _user()
    _chat_session(uid, n=6, upto=4)
    assert IdleSweeper(idle_timeout=60).sweep_once() == {"chat": 1, "group": 0}
    assert (_jobs()[0]["range_start"], _jobs()[0]["range_end"]) == (4, 6)


def test_sweep_includes_archived_but_not_trashed():
    uid = _user()
    _chat_session(uid, status="archived")
    trashed = _chat_session(uid, status="trashed")
    assert IdleSweeper(idle_timeout=60).sweep_once() == {"chat": 1, "group": 0}
    assert all(j["session_id"] != trashed for j in _jobs())


def test_sweep_classifies_group_sessions():
    uid = _user()
    sid = _group_session(uid)
    assert IdleSweeper(idle_timeout=60).sweep_once() == {"chat": 0, "group": 1}
    assert _jobs()[0]["kind"] == "group" and _jobs()[0]["session_id"] == sid


def test_sweep_second_run_does_not_duplicate_jobs():
    uid = _user()
    _chat_session(uid)
    sweeper = IdleSweeper(idle_timeout=60)
    sweeper.sweep_once()
    sweeper.sweep_once()
    assert store.jobs_pending() == 1
    assert len(_jobs()) == 1


def test_sweep_cutoff_respects_idle_timeout():
    uid = _user()
    sid = _chat_session(uid, idle=False)
    store.set_session(sid, last_message_at=(datetime.now() - timedelta(minutes=10))
                      .isoformat(timespec="seconds"))
    assert IdleSweeper(idle_timeout=600).sweep_once() == {"chat": 1, "group": 0}
    assert IdleSweeper(idle_timeout=3600).sweep_once() == {"chat": 0, "group": 0}


def test_sweep_is_empty_db_safe():
    assert IdleSweeper(idle_timeout=1).sweep_once() == {"chat": 0, "group": 0}


@pytest.mark.asyncio
async def test_sweeper_loop_survives_errors_and_stops():
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("db down")

    sweeper = IdleSweeper(idle_timeout=1, check_interval=0.01)
    sweeper.sweep_once = boom
    sweeper.start()
    first_task = sweeper._task
    sweeper.start()                      # 幂等：不重复起循环
    assert sweeper._task is first_task
    await asyncio.sleep(0.06)
    assert len(calls) >= 2               # 巡检异常不终止循环
    await sweeper.stop()
    assert sweeper._task is None
    stopped_at = len(calls)
    await asyncio.sleep(0.05)
    assert len(calls) == stopped_at      # stop 后彻底安静


@pytest.mark.asyncio
async def test_sweeper_stop_without_start_is_safe():
    await IdleSweeper(check_interval=0.01).stop()


@pytest.mark.asyncio
async def test_sweeper_loop_enqueues_real_idle_session():
    uid = _user()
    _chat_session(uid)
    sweeper = IdleSweeper(idle_timeout=60, check_interval=0.01)
    sweeper.start()
    await asyncio.sleep(0.05)
    await sweeper.stop()
    assert store.jobs_pending() == 1
