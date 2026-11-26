"""语音合成 API - 返回 mp3 音频流，App 用原生播放器直接播放"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.services.tts_service import TtsUnavailableError, tts_service

router = APIRouter(prefix="/api/tts", tags=["tts"])


@router.get("/status")
async def tts_status():
    return {
        "available": tts_service.available,
        "provider": tts_service.provider,
        "model": tts_service.model,
        "voice": tts_service.voice,
    }


@router.get("/speech")
async def tts_speech(
    text: str = Query(..., min_length=1),
    voice: str = "",
    speed: float = 0.0,
):
    """合成语音，返回 audio/mpeg"""
    try:
        audio = await tts_service.synthesize(text, voice=voice, speed=speed)
    except TtsUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"语音合成失败: {e}")

    if not audio:
        raise HTTPException(status_code=400, detail="文本为空")

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
