"""LLM Gateway 单测（P0-3）：退避重试、备用模型回退、流式首 token 语义、JSON 兜底、embedding 开关"""
import json

import httpx
import pytest

from app.llm import adapter as gw
from app.llm.adapter import extract_json, llm_adapter

REQ = httpx.Request("POST", "https://llm.test/v1/chat/completions")


def ok(payload):
    return {"choices": [{"message": {"role": "assistant", "content": payload}}]}


class FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self.request = REQ
        self._body = body if body is not None else ok("回答")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=REQ, response=self)

    def json(self):
        return self._body


class FakePostClient:
    """按脚本返回响应；脚本项为 FakeResp 或异常"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def post(self, url, json=None):
        self.calls.append(json)
        assert self.script, "响应脚本已用尽"
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeStreamResp:
    def __init__(self, lines=(), status_code=200, enter_error=None, break_after=None):
        self.lines = list(lines)
        self.status_code = status_code
        self.enter_error = enter_error
        self.break_after = break_after
        self.request = REQ

    def __await__(self):  # pragma: no cover - 防御
        raise TypeError("stream() 返回的对象不应被 await")

    async def __aenter__(self):
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=REQ, response=self)

    async def aiter_lines(self):
        for i, line in enumerate(self.lines):
            if self.break_after is not None and i >= self.break_after:
                raise httpx.ReadError("mid-stream break")
            yield line


class FakeStreamClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def stream(self, method, url, json=None):
        self.calls.append(json)
        assert self.script, "流式脚本已用尽"
        return self.script.pop(0)


def sse(chunks):
    out = [f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}" for c in chunks]
    return out + ["data: [DONE]", ""]


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(gw, "BACKOFF_BASE", 0.0)


def use_post(monkeypatch, script):
    fake = FakePostClient(script)
    monkeypatch.setattr(llm_adapter, "client", fake)
    return fake


def use_stream(monkeypatch, script):
    fake = FakeStreamClient(script)
    monkeypatch.setattr(llm_adapter, "client", fake)
    return fake


# ---------- extract_json ----------

def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert extract_json('前置说明\n```json\n{"action": "ADD"}\n```\n后缀') == {"action": "ADD"}


def test_extract_json_embedded_in_prose():
    assert extract_json('我决定：{"action": "NOOP", "importance": 0.2} 就这样') == {
        "action": "NOOP", "importance": 0.2}


def test_extract_json_nested_braces():
    assert extract_json('xx {"a": {"b": [1, 2]}} yy') == {"a": {"b": [1, 2]}}


@pytest.mark.parametrize("bad", ["", "   ", "完全没有 JSON", "{坏掉的"])
def test_extract_json_returns_none(bad):
    assert extract_json(bad) is None


# ---------- 模型路由 ----------

def test_resolve_model(monkeypatch):
    monkeypatch.setattr(gw, "DISTILL_MODEL", "distill-m")
    monkeypatch.setattr(gw, "LLM_MODEL", "chat-m")
    assert gw._resolve_model("distill") == "distill-m"
    assert gw._resolve_model(None) == "chat-m"
    assert gw._resolve_model("explicit") == "explicit"


# ---------- 非流式调用 ----------

@pytest.mark.asyncio
async def test_chat_payload_is_not_streaming(monkeypatch):
    fake = use_post(monkeypatch, [FakeResp()])
    msg = await llm_adapter.chat([{"role": "user", "content": "hi"}], max_tokens=123)
    assert msg["content"] == "回答"
    payload = fake.calls[0]
    assert payload["stream"] is False          # 非流式却带 stream=true 会拿到 SSE 文本
    assert payload["max_tokens"] == 123
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert "response_format" not in payload


@pytest.mark.asyncio
async def test_chat_json_mode_attaches_parsed(monkeypatch):
    fake = use_post(monkeypatch, [FakeResp(body=ok('{"action": "UPDATE"}'))])
    msg = await llm_adapter.chat([{"role": "user", "content": "x"}], json_mode=True)
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    assert msg["parsed"] == {"action": "UPDATE"}


@pytest.mark.asyncio
async def test_chat_json_mode_unparsable_has_no_parsed(monkeypatch):
    use_post(monkeypatch, [FakeResp(body=ok("不是 JSON"))])
    msg = await llm_adapter.chat([{"role": "user", "content": "x"}], json_mode=True)
    assert "parsed" not in msg


# ---------- 重试与回退 ----------

@pytest.mark.asyncio
async def test_retry_on_429_then_success(monkeypatch):
    fake = use_post(monkeypatch, [FakeResp(429), FakeResp(503), FakeResp(body=ok("终于成了"))])
    msg = await llm_adapter.chat([{"role": "user", "content": "x"}])
    assert msg["content"] == "终于成了"
    assert len(fake.calls) == 3


@pytest.mark.asyncio
async def test_fallback_model_after_primary_exhausted(monkeypatch):
    monkeypatch.setattr(gw, "LLM_MODEL", "primary")
    monkeypatch.setattr(gw, "LLM_FALLBACK_MODEL", "backup")
    boom = httpx.ConnectError("dns down")
    fake = use_post(monkeypatch, [boom, boom, boom, FakeResp(body=ok("备用的答复"))])
    msg = await llm_adapter.chat([{"role": "user", "content": "x"}])
    assert msg["content"] == "备用的答复"
    assert [c["model"] for c in fake.calls] == ["primary", "primary", "primary", "backup"]


@pytest.mark.asyncio
async def test_all_models_exhausted_raises(monkeypatch):
    monkeypatch.setattr(gw, "LLM_FALLBACK_MODEL", "backup")
    use_post(monkeypatch, [FakeResp(500)] * gw.MAX_RETRIES
             + [FakeResp(500)] * gw.MAX_RETRIES)
    with pytest.raises(httpx.HTTPStatusError):
        await llm_adapter.chat([{"role": "user", "content": "x"}])


@pytest.mark.asyncio
async def test_client_error_is_not_retried(monkeypatch):
    fake = use_post(monkeypatch, [FakeResp(401)])
    with pytest.raises(httpx.HTTPStatusError):
        await llm_adapter.chat([{"role": "user", "content": "x"}])
    assert len(fake.calls) == 1               # 4xx 非重试状态码立即失败


# ---------- 流式 ----------

@pytest.mark.asyncio
async def test_stream_yields_delta_content(monkeypatch):
    fake = use_stream(monkeypatch, [FakeStreamResp(sse(["你", "好"]))])
    out = [c async for c in llm_adapter.chat_stream([{"role": "user", "content": "x"}])]
    assert out == ["你", "好"]
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_stream_retries_before_first_token(monkeypatch):
    fake = use_stream(monkeypatch,
                      [FakeStreamResp(enter_error=httpx.ConnectError("nope")),
                       FakeStreamResp(sse(["补"]))])
    out = [c async for c in llm_adapter.chat_stream([{"role": "user", "content": "x"}])]
    assert out == ["补"]
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_stream_failure_after_first_token_does_not_fall_back(monkeypatch):
    """修 01 §6.4：中途失败切备用模型会导致重复输出"""
    monkeypatch.setattr(gw, "LLM_FALLBACK_MODEL", "backup")
    fake = use_stream(monkeypatch, [FakeStreamResp(sse(["前半"]), break_after=1)])
    got = []
    with pytest.raises(httpx.ReadError):
        async for c in llm_adapter.chat_stream([{"role": "user", "content": "x"}]):
            got.append(c)
    assert got == ["前半"]
    assert len(fake.calls) == 1               # 不回退、不重放


@pytest.mark.asyncio
async def test_stream_falls_back_to_second_model_when_primary_dead(monkeypatch):
    monkeypatch.setattr(gw, "LLM_MODEL", "primary")
    monkeypatch.setattr(gw, "LLM_FALLBACK_MODEL", "backup")
    boom = httpx.ConnectError("dead")
    attempts = gw.MAX_RETRIES + 1          # while attempt <= retries → MAX_RETRIES 次重试
    fake = use_stream(monkeypatch,
                      [FakeStreamResp(enter_error=boom)] * attempts
                      + [FakeStreamResp(sse(["备"]))])
    out = [c async for c in llm_adapter.chat_stream([{"role": "user", "content": "x"}])]
    assert out == ["备"]
    assert [c["model"] for c in fake.calls] == ["primary"] * attempts + ["backup"]


@pytest.mark.asyncio
async def test_stream_raises_last_error_when_all_fail(monkeypatch):
    monkeypatch.setattr(gw, "LLM_FALLBACK_MODEL", "")
    boom = httpx.ConnectError("all dead")
    fake = use_stream(monkeypatch, [FakeStreamResp(enter_error=boom)] * (gw.MAX_RETRIES + 1))
    with pytest.raises(httpx.ConnectError):
        [c async for c in llm_adapter.chat_stream([{"role": "user", "content": "x"}])]
    assert len(fake.calls) == gw.MAX_RETRIES + 1


# ---------- chat_json ----------

@pytest.mark.asyncio
async def test_chat_json_uses_structured_response(monkeypatch):
    fake = use_post(monkeypatch, [FakeResp(body=ok('{"action": "MERGE"}'))])
    assert await llm_adapter.chat_json([{"role": "user", "content": "x"}]) == {
        "action": "MERGE"}
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_chat_json_falls_back_to_plain_call(monkeypatch):
    monkeypatch.setattr(gw, "DISTILL_MODEL", "cheap-m")
    fake = use_post(monkeypatch,
                    [FakeResp(400), FakeResp(body=ok("文字里带 {\"action\": \"ADD\"}"))])
    got = await llm_adapter.chat_json([{"role": "user", "content": "x"}], default={"a": 1})
    assert got == {"action": "ADD"}
    assert len(fake.calls) == 2
    assert fake.calls[1]["model"] == "cheap-m"
    assert "response_format" not in fake.calls[1]


@pytest.mark.asyncio
async def test_chat_json_returns_default_when_unparsable(monkeypatch):
    use_post(monkeypatch, [FakeResp(body=ok("纯文本")), FakeResp(body=ok("还是纯文本"))])
    got = await llm_adapter.chat_json([{"role": "user", "content": "x"}],
                                      default={"action": "ADD"})
    assert got == {"action": "ADD"}


# ---------- embedding ----------

def test_embed_available_requires_all_three(monkeypatch):
    monkeypatch.setattr(gw, "EMBED_BASE_URL", "")
    assert llm_adapter.embed_available is False


def test_embed_available_true(monkeypatch):
    monkeypatch.setattr(gw, "EMBED_BASE_URL", "https://e.test/v1")
    monkeypatch.setattr(gw, "EMBED_API_KEY", "k")
    monkeypatch.setattr(gw, "EMBED_MODEL", "m")
    assert llm_adapter.embed_available is True


@pytest.mark.asyncio
async def test_embed_returns_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(gw, "EMBED_BASE_URL", "")
    assert await llm_adapter.embed(["x"]) is None


class FakeEmbedClient:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        self.calls.append((url, json))
        if self.exc:
            raise self.exc
        return self.resp


@pytest.mark.asyncio
async def test_embed_orders_by_index_and_swallows_errors(monkeypatch):
    monkeypatch.setattr(gw, "EMBED_BASE_URL", "https://e.test/v1")
    monkeypatch.setattr(gw, "EMBED_API_KEY", "k")
    monkeypatch.setattr(gw, "EMBED_MODEL", "m")
    body = {"data": [{"index": 1, "embedding": [0.5, 0.5]}, {"index": 0, "embedding": [1.0, 0.0]}]}
    resp = FakeResp(body=body)
    resp.raise_for_status = lambda: None
    holder = {}

    def make_client(*a, **kw):
        holder["c"] = FakeEmbedClient(resp=resp)
        return holder["c"]

    monkeypatch.setattr(gw.httpx, "AsyncClient", make_client)
    vecs = await llm_adapter.embed(["a", "b"])
    assert vecs == [[1.0, 0.0], [0.5, 0.5]]
    assert holder["c"].calls[0][1] == {"model": "m", "input": ["a", "b"]}

    def make_boom(*a, **kw):
        return FakeEmbedClient(exc=httpx.ConnectError("down"))

    monkeypatch.setattr(gw.httpx, "AsyncClient", make_boom)
    assert await llm_adapter.embed(["a"]) is None
