"""语音识别服务 - 本地 whisper.cpp / OpenAI 兼容接口

设备端 SpeechRecognizer 在不少系统镜像/国产 ROM 上不可用，
这里提供后端识别兜底：App 录 WAV 上传，后端转文字后返回。
"""
import asyncio
import os
from pathlib import Path

import httpx

from app.config import (
    ASR_PROVIDER,
    ASR_LANGUAGE,
    WHISPER_BIN,
    WHISPER_MODEL,
    ASR_API_BASE,
    ASR_API_KEY,
    ASR_MODEL,
)


class AsrUnavailableError(RuntimeError):
    """ASR 未配置或不可用"""


class AsrService:
    def __init__(self):
        self.provider = (ASR_PROVIDER or "disabled").lower()

    @property
    def available(self) -> bool:
        if self.provider == "local":
            return Path(WHISPER_BIN).exists() and Path(WHISPER_MODEL).exists()
        if self.provider in ("openai", "siliconflow"):
            return bool(ASR_API_BASE and ASR_API_KEY)
        return False

    async def transcribe(self, audio_path: Path, language: str = "") -> str:
        if not self.available:
            raise AsrUnavailableError("后端语音识别未配置")
        lang = language or ASR_LANGUAGE
        if self.provider == "local":
            return await self._transcribe_local(audio_path, lang)
        return await self._transcribe_openai(audio_path, lang)

    # ---------- 本地 whisper.cpp ----------

    async def _transcribe_local(self, audio_path: Path, language: str) -> str:
        out_prefix = audio_path.with_suffix("")
        cmd = [
            WHISPER_BIN,
            "-m", WHISPER_MODEL,
            "-f", str(audio_path),
            "-l", language,
            "-otxt",
            "-of", str(out_prefix),
            "-nt",          # 不输出时间戳
            "-np",          # 不打印进度
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise AsrUnavailableError("语音识别超时")

        txt_path = Path(str(out_prefix) + ".txt")
        text = ""
        if txt_path.exists():
            text = txt_path.read_text(encoding="utf-8", errors="ignore").strip()
            try:
                txt_path.unlink()
            except OSError:
                pass

        if not text and proc.returncode != 0:
            detail = (stderr or b"").decode("utf-8", errors="ignore")[-200:]
            raise AsrUnavailableError(f"语音识别失败: {detail or proc.returncode}")
        return text

    # ---------- OpenAI 兼容接口 ----------

    async def _transcribe_openai(self, audio_path: Path, language: str) -> str:
        url = ASR_API_BASE.rstrip("/") + "/audio/transcriptions"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                with open(audio_path, "rb") as f:
                    resp = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {ASR_API_KEY}"},
                        files={"file": (audio_path.name, f, "audio/wav")},
                        data={"model": ASR_MODEL, "language": language},
                    )
        except httpx.HTTPError as e:
            raise AsrUnavailableError(f"语音识别请求失败: {e}")

        if resp.status_code != 200:
            raise AsrUnavailableError(
                f"语音识别服务返回 {resp.status_code}: {resp.text[:120]}"
            )
        try:
            return (resp.json().get("text") or "").strip()
        except ValueError:
            raise AsrUnavailableError("语音识别返回内容无法解析")


asr_service = AsrService()
