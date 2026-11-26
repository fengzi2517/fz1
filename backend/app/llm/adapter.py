"""LLM 适配器 - 封装 OpenAI 兼容协议"""
import json
import httpx
from typing import List, Dict, AsyncGenerator, Optional

from app.config import (
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_FALLBACK_MODEL,
    LLM_TEMPERATURE, LLM_MAX_TOKENS,
)


class LLMAdapter:
    """OpenAI 兼容协议的 LLM 适配器"""

    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=LLM_BASE_URL,
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    async def chat_stream(
        self,
        messages: List[Dict],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict]] = None,
    ) -> AsyncGenerator[str, None]:
        """流式对话，逐 token 产出"""
        payload = {
            "model": model or LLM_MODEL,
            "messages": messages,
            "temperature": temperature if temperature is not None else LLM_TEMPERATURE,
            "max_tokens": max_tokens or LLM_MAX_TOKENS,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            async with self.client.stream(
                "POST", "/chat/completions", json=payload
            ) as response:
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
                        # 处理文本内容
                        if "content" in delta and delta["content"]:
                            yield delta["content"]
                        # 处理工具调用（返回完整的 tool_calls JSON）
                        if "tool_calls" in delta and delta["tool_calls"]:
                            yield json.dumps({"tool_calls": delta["tool_calls"]})
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except httpx.HTTPStatusError as e:
            # 主模型失败，尝试备用模型
            if model != LLM_FALLBACK_MODEL and LLM_FALLBACK_MODEL != model:
                async for chunk in self.chat_stream(
                    messages, model=LLM_FALLBACK_MODEL,
                    temperature=temperature, max_tokens=max_tokens, tools=tools,
                ):
                    yield chunk
            else:
                raise

    async def chat(
        self,
        messages: List[Dict],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict]] = None,
    ) -> Dict:
        """非流式对话，返回完整响应"""
        payload = {
            "model": model or LLM_MODEL,
            "messages": messages,
            "temperature": temperature if temperature is not None else LLM_TEMPERATURE,
            "max_tokens": max_tokens or LLM_MAX_TOKENS,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            response = await self.client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]
        except httpx.HTTPStatusError:
            if model != LLM_FALLBACK_MODEL and LLM_FALLBACK_MODEL != model:
                return await self.chat(
                    messages, model=LLM_FALLBACK_MODEL,
                    temperature=temperature, max_tokens=max_tokens, tools=tools,
                )
            raise

    async def close(self):
        await self.client.aclose()


# 全局单例
llm_adapter = LLMAdapter()
