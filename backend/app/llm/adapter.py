"""LLM Gateway（P0-3 重写）

修复旧版问题（01 文档 §6.4）：
- fallback 条件恒真/恒假的逻辑缺陷
- 流式中途失败切备用模型导致的重复输出：仅在首 token 产出前允许回退
- 429/5xx/网络错误指数退避重试
- JSON 结构化输出辅助（尽力 request json_object，失败则稳健抠取）
- 蒸馏/聊天模型分离（DISTILL_MODEL，sleep-time 思路）
"""
import asyncio
import json
import re
from typing import AsyncGenerator, Dict, List, Optional

import httpx

from app.config import (
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_FALLBACK_MODEL,
    LLM_TEMPERATURE, LLM_MAX_TOKENS, DISTILL_MODEL,
    EMBED_BASE_URL, EMBED_API_KEY, EMBED_MODEL,
)

RETRY_STATUS = {429, 500, 502, 503}
MAX_RETRIES = 3
BACKOFF_BASE = 0.5


def _resolve_model(model: Optional[str]) -> str:
    if model == "distill":
        return DISTILL_MODEL
    return model or LLM_MODEL


def extract_json(content: str) -> Optional[dict]:
    """从 LLM 输出中稳健提取 JSON：直接解析 → 代码块 → 最外层大括号"""
    if not content:
        return None
    text = content.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


class LLMGateway:
    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=LLM_BASE_URL,
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    # ---------- 非流式 ----------

    async def chat(
        self,
        messages: List[Dict],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict]] = None,
        json_mode: bool = False,
    ) -> Dict:
        payload = self._payload(messages, model, temperature, max_tokens, tools,
                                stream=False)
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = await self._post_with_retry(payload)
        msg = data["choices"][0]["message"]
        if json_mode and isinstance(msg.get("content"), str):
            parsed = extract_json(msg["content"])
            if parsed is not None:
                msg["parsed"] = parsed
        return msg

    async def chat_json(self, messages: List[Dict], model: Optional[str] = None,
                        temperature: float = 0.3, default: Optional[dict] = None) -> dict:
        """结构化输出：先试 json_mode，解析失败再普通调用兜底一次"""
        try:
            msg = await self.chat(messages, model=model, temperature=temperature,
                                  json_mode=True)
            parsed = msg.get("parsed") or extract_json(msg.get("content") or "")
            if parsed is not None:
                return parsed
        except Exception:
            pass
        msg = await self.chat(messages, model=model or "distill", temperature=temperature)
        return extract_json(msg.get("content") or "") or (default or {})

    # ---------- 流式 ----------

    async def chat_stream(
        self,
        messages: List[Dict],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict]] = None,
    ) -> AsyncGenerator[str, None]:
        """流式对话。首 token 前失败可重试/回退备用模型；
        首 token 后失败直接抛错（修旧版"中途切换导致重复输出"）。
        """
        plans = [self._payload(messages, model, temperature, max_tokens, tools)]
        primary = plans[0]["model"]
        if LLM_FALLBACK_MODEL and LLM_FALLBACK_MODEL != primary:
            plans.append(self._payload(messages, LLM_FALLBACK_MODEL,
                                       temperature, max_tokens, tools))
        last_exc: Optional[Exception] = None
        for idx, payload in enumerate(plans):
            retries = MAX_RETRIES if idx == 0 else 1
            attempt = 0
            while attempt <= retries:
                emitted = False
                try:
                    async for chunk in self._stream_attempt(payload):
                        emitted = True
                        yield chunk
                    return
                except Exception as e:
                    if emitted:
                        raise
                    last_exc = e
                    attempt += 1
                    if attempt <= retries:
                        await asyncio.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
        raise last_exc or RuntimeError("LLM 流式调用失败")

    async def _stream_attempt(self, payload: dict) -> AsyncGenerator[str, None]:
        async with self.client.stream("POST", "/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"]
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta.get("content"):
                    yield delta["content"]

    # ---------- 内部 ----------

    def _payload(self, messages, model, temperature, max_tokens, tools,
                 stream: bool = True) -> dict:
        payload = {
            "model": _resolve_model(model),
            "messages": messages,
            "temperature": temperature if temperature is not None else LLM_TEMPERATURE,
            "max_tokens": max_tokens or LLM_MAX_TOKENS,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    async def _post_with_retry(self, payload: dict) -> dict:
        last_err: Optional[Exception] = None
        models = [payload["model"]]
        if LLM_FALLBACK_MODEL and LLM_FALLBACK_MODEL != payload["model"]:
            models.append(LLM_FALLBACK_MODEL)
        for m in models:
            p = dict(payload)
            p["model"] = m
            for attempt in range(MAX_RETRIES):
                try:
                    resp = await self.client.post("/chat/completions", json=p)
                    if resp.status_code in RETRY_STATUS:
                        last_err = httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}", request=resp.request, response=resp)
                        await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))
                        continue
                    resp.raise_for_status()
                    return resp.json()
                except httpx.TransportError as e:
                    last_err = e
                    await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))
        raise last_err or RuntimeError("LLM 调用失败")

    # ---------- Embedding（可选能力） ----------

    @property
    def embed_available(self) -> bool:
        return bool(EMBED_BASE_URL and EMBED_API_KEY and EMBED_MODEL)

    async def embed(self, texts: List[str]) -> Optional[List[List[float]]]:
        if not self.embed_available or not texts:
            return None
        try:
            async with httpx.AsyncClient(
                    base_url=EMBED_BASE_URL,
                    headers={"Authorization": f"Bearer {EMBED_API_KEY}"},
                    timeout=30.0) as client:
                resp = await client.post("/embeddings",
                                         json={"model": EMBED_MODEL, "input": texts})
                resp.raise_for_status()
                data = sorted(resp.json()["data"], key=lambda d: d["index"])
                return [d["embedding"] for d in data]
        except Exception:
            return None

    async def close(self):
        await self.client.aclose()


llm_adapter = LLMGateway()
