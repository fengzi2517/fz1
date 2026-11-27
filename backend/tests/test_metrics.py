"""P0-4 可观测：request_id 贯穿、/metrics 鉴权与口径、LLM/蒸馏计数"""
import asyncio
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.metrics import Metrics, metrics, percentile
from app.core.trace import RequestIdFilter, request_id_var
from app.llm import adapter as gw
from app.services.distill_service import distill_service
from app.store import store

REQ = httpx.Request("POST", "https://llm.test/v1/chat/completions")


class FakeResp:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.request = REQ

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=REQ, response=self)

    def json(self):
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


class FakeClient:
    """按脚本吐出状态码；脚本项为状态码或异常"""

    def __init__(self, script):
        self.script = list(script)
        self.models = []

    async def post(self, url, json=None):
        self.models.append(json["model"])
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)


@pytest.fixture
def fresh_workers():
    distill_service._worker_task = None
    distill_service._stop = asyncio.Event()
    yield
    distill_service._worker_task = None
    distill_service._stop = asyncio.Event()


class TestPercentile:
    def test_empty(self):
        assert percentile([], 95) == 0.0

    def test_nearest_rank(self):
        s = [float(i) for i in range(1, 11)]
        assert percentile(s, 50) == 5.0
        assert percentile(s, 95) == 10.0
        assert percentile(s, 0) == 1.0


class TestMetricsObject:
    def test_request_classified_by_status(self):
        m = Metrics()
        m.observe_request("GET", "/api/chat/history", 200, 12.0)
        m.observe_request("GET", "/api/chat/history", 401, 3.0)
        m.observe_request("POST", "/api/chat/send", 500, 40.0)
        snap = m.snapshot()
        assert snap["requests"] == {"GET /api/chat/history 2xx": 1,
                                    "GET /api/chat/history 4xx": 1,
                                    "POST /api/chat/send 5xx": 1}
        assert snap["latency_ms"]["GET /api/chat/history"]["n"] == 2
        assert snap["latency_ms"]["POST /api/chat/send"]["max"] == 40.0

    def test_latency_samples_are_bounded(self):
        m = Metrics()
        for i in range(600):
            m.observe_request("GET", "/x", 200, float(i))
        assert m.snapshot()["latency_ms"]["GET /x"]["n"] == 512

    def test_distill_success_rate(self):
        m = Metrics()
        assert m.snapshot()["distill"]["success_rate"] is None
        m.observe_distill(True)
        m.observe_distill(False)
        assert m.snapshot()["distill"] == {"ok": 1, "fail": 1, "total": 2,
                                           "success_rate": 0.5}

    def test_unknown_counter_names_are_ignored(self):
        m = Metrics()
        m.observe_llm("nope")
        m.observe_guard("nope")
        assert m.snapshot()["llm"]["calls"] == 0
        assert m.snapshot()["guard"]["input_blocked"] == 0


class TestRequestID:
    def test_generated_when_absent(self, fake_llm, fresh_workers):
        with TestClient(main.app) as client:
            rid = client.get("/health").headers.get("X-Request-ID")
        assert rid and len(rid) == 12
        assert all(c in "0123456789abcdef" for c in rid)

    def test_inbound_id_respected(self, fake_llm, fresh_workers):
        with TestClient(main.app) as client:
            r = client.get("/health", headers={"X-Request-ID": "trace-abc"})
        assert r.headers["X-Request-ID"] == "trace-abc"

    def test_ids_differ_per_request(self, fake_llm, fresh_workers):
        with TestClient(main.app) as client:
            a = client.get("/health").headers["X-Request-ID"]
            b = client.get("/health").headers["X-Request-ID"]
        assert a != b

    def test_filter_injects_contextvar_value(self):
        token = request_id_var.set("s00000000001")
        try:
            rec = logging.LogRecord("x", logging.INFO, __file__, 1, "hi", None, None)
            assert RequestIdFilter().filter(rec) is True
            assert rec.request_id == "s00000000001"
        finally:
            request_id_var.reset(token)

    def test_app_handler_can_format_foreign_records(self):
        """格式串含 %(request_id)s，而 uvicorn/jieba 的日志本身没这字段——filter 必须先跑"""
        main.setup_logging("INFO")
        handler = next(h for h in logging.getLogger().handlers
                       if getattr(h, "_app_fmt", False))
        rec = logging.LogRecord("uvicorn.access", logging.INFO, "f.py", 1, "GET /", None, None)
        for f in handler.filters:
            f.filter(rec)
        assert "[uvicorn.access] [-] GET /" in handler.format(rec)


class TestMetricsEndpoint:
    def test_404_when_disabled(self, fake_llm, fresh_workers, monkeypatch):
        monkeypatch.setattr(main, "METRICS_ENABLED", False)
        monkeypatch.setattr(main, "METRICS_TOKEN", "secret-token")
        with TestClient(main.app) as client:
            r = client.get("/metrics", headers={"X-Metrics-Token": "secret-token"})
        assert r.status_code == 404

    def test_404_when_token_unset(self, fake_llm, fresh_workers, monkeypatch):
        monkeypatch.setattr(main, "METRICS_ENABLED", True)
        monkeypatch.setattr(main, "METRICS_TOKEN", "")
        with TestClient(main.app) as client:
            assert client.get("/metrics").status_code == 404
            assert client.get("/metrics", headers={"X-Metrics-Token": "x"}).status_code == 404

    def test_401_on_wrong_or_empty_token(self, fake_llm, fresh_workers, monkeypatch):
        monkeypatch.setattr(main, "METRICS_ENABLED", True)
        monkeypatch.setattr(main, "METRICS_TOKEN", "right")
        with TestClient(main.app) as client:
            assert client.get("/metrics", headers={"X-Metrics-Token": "wrong"}).status_code == 401
            assert client.get("/metrics", params={"token": ""}).status_code == 401
            assert client.get("/metrics").status_code == 401

    def test_snapshot_counts_requests_so_far(self, fake_llm, fresh_workers, monkeypatch):
        monkeypatch.setattr(main, "METRICS_ENABLED", True)
        monkeypatch.setattr(main, "METRICS_TOKEN", "right")
        with TestClient(main.app) as client:
            client.get("/health")
            client.get("/metrics", headers={"X-Metrics-Token": "right"})
            # 自己那次请求的耗时在响应体生成之后才记账，故要读第二次
            snap = client.get("/metrics", headers={"X-Metrics-Token": "right"}).json()
        assert {"requests", "latency_ms", "llm", "distill", "guard"} <= set(snap)
        assert snap["requests"]["GET /health 2xx"] >= 1
        assert snap["latency_ms"]["GET /metrics"]["n"] >= 1

    def test_query_token_supported_for_scrapers(self, fake_llm, fresh_workers, monkeypatch):
        monkeypatch.setattr(main, "METRICS_ENABLED", True)
        monkeypatch.setattr(main, "METRICS_TOKEN", "right")
        with TestClient(main.app) as client:
            assert client.get("/metrics", params={"token": "right"}).status_code == 200


class TestCORSHardening:
    def test_no_credentialed_any_origin(self, fake_llm, fresh_workers):
        """业务口用 Bearer 而非 cookie，不能再同时允许任意源 + 携带凭证"""
        with TestClient(main.app) as client:
            pre = client.options("/api/characters", headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET"})
            got = client.get("/health", headers={"Origin": "https://evil.example"})
        assert pre.status_code in (200, 400, 401)
        assert got.headers.get("access-control-allow-credentials") is None


class TestLLMCounters:
    def test_calls_errors_retries(self, monkeypatch):
        monkeypatch.setattr(gw, "LLM_FALLBACK_MODEL", "")
        g = gw.LLMGateway()
        g.client = FakeClient([500, 429, 200])
        before = metrics.snapshot()["llm"]
        asyncio.run(g._post_with_retry({"model": "m1", "messages": []}))
        after = metrics.snapshot()["llm"]
        assert after["calls"] - before["calls"] == 3
        assert after["errors"] - before["errors"] == 2
        assert after["retries"] - before["retries"] == 2, "两次失败后都还会再试，故 retries=2"
        assert after["fallback"] == before["fallback"]

    def test_fallback_to_backup_model_counted(self, monkeypatch):
        monkeypatch.setattr(gw, "LLM_FALLBACK_MODEL", "m2")
        g = gw.LLMGateway()
        g.client = FakeClient([500, 500, 500, 200])
        before = metrics.snapshot()["llm"]
        asyncio.run(g._post_with_retry({"model": "m1", "messages": []}))
        after = metrics.snapshot()["llm"]
        assert after["fallback"] - before["fallback"] == 1
        assert after["errors"] - before["errors"] == 3


class TestDistillCounters:
    async def test_worker_counts_success_and_failure(self, monkeypatch, fake_llm):
        from app.services import distill_service as ds_mod
        monkeypatch.setattr(ds_mod, "WORKER_POLL_INTERVAL", 0.02)
        distill_service._worker_task = None
        distill_service._stop = asyncio.Event()

        uid = store.create_user("mx", "hash")
        cid = store.create_character(uid, "阿强", profile_md="# 阿强")
        sid = store.create_session(uid, "char", character_id=cid)
        for i in range(4):
            store.append_message(sid, "user" if i % 2 == 0 else "assistant", f"聊{i}")

        before = metrics.snapshot()["distill"]
        fake_llm.chat_content = "用户与阿强聊了几句。"
        distill_service.request(uid, sid, "chat")
        distill_service.start_worker()
        try:
            for _ in range(300):
                if store.jobs_pending() == 0:
                    break
                await asyncio.sleep(0.01)
        finally:
            await distill_service.stop_worker()

        after = metrics.snapshot()["distill"]
        assert after["ok"] - before["ok"] >= 1, "蒸馏成功必须计入成功率分子"
