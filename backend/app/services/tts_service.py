"""语音合成服务 - 硅基流动 MOSS-TTSD（OpenAI 兼容 /audio/speech）"""
import hashlib
from pathlib import Path

import httpx

from app.config import (
    DATA_DIR,
    TTS_PROVIDER,
    TTS_API_BASE,
    TTS_API_KEY,
    TTS_MODEL,
    TTS_VOICE,
    TTS_SPEED,
    ensure_dir,
)

# 单次合成最大字符数，避免超长回复合成过久
MAX_TEXT_CHARS = 300


class TtsUnavailableError(RuntimeError):
    """TTS 未配置或不可用"""


class TtsService:
    def __init__(self):
        self.provider = (TTS_PROVIDER or "disabled").lower()
        self.model = TTS_MODEL
        self.voice = TTS_VOICE
        self.speed = TTS_SPEED

    @property
    def available(self) -> bool:
        return self.provider in ("siliconflow", "openai") and bool(
            TTS_API_BASE and TTS_API_KEY
        )

    async def synthesize(
        self, text: str, voice: str = "", speed: float = 0.0
    ) -> bytes:
        if not self.available:
            raise TtsUnavailableError("云端语音合成未配置")
        text = (text or "").strip()
        if not text:
            return b""
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS]

        use_voice = voice or self.voice
        use_speed = speed if speed and speed > 0 else self.speed
        cache_key = hashlib.sha1(
            f"{self.model}|{use_voice}|{use_speed}|{text}".encode("utf-8")
        ).hexdigest()
        cache_path = ensure_dir(DATA_DIR / "cache" / "tts") / f"{cache_key}.mp3"
        if cache_path.exists():
            return cache_path.read_bytes()

        url = TTS_API_BASE.rstrip("/") + "/audio/speech"
        payload = {
            "model": self.model,
            "input": text,
            "voice": use_voice,
            "response_format": "mp3",
            "speed": use_speed,
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {TTS_API_KEY}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json=payload,
                )
        except httpx.HTTPError as e:
            raise TtsUnavailableError(f"语音合成请求失败: {e}")

        if resp.status_code != 200:
            raise TtsUnavailableError(
                f"语音合成服务返回 {resp.status_code}: {resp.text[:120]}"
            )
        if not resp.content:
            raise TtsUnavailableError("语音合成返回空音频")

        cache_path.write_bytes(resp.content)
        return resp.content


tts_service = TtsService()
