"""语音识别 API - 设备端识别不可用时的后端兜底"""
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import DATA_DIR, ensure_dir
from app.services.asr_service import AsrUnavailableError, asr_service

router = APIRouter(prefix="/api/asr", tags=["asr"])

MAX_AUDIO_BYTES = 10 * 1024 * 1024


@router.get("/status")
async def asr_status():
    """查询后端识别能力（App 启动时可据此决定是否启用录音上传）"""
    return {"available": asr_service.available, "provider": asr_service.provider}


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...), language: str = ""):
    """上传 WAV（16k 单声道）并返回识别文本"""
    if not asr_service.available:
        raise HTTPException(status_code=503, detail="后端语音识别未配置")

    suffix = Path(file.filename or "audio.wav").suffix.lower()
    if suffix != ".wav":
        raise HTTPException(status_code=400, detail="仅支持 WAV 格式")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="音频内容为空")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="音频过大（上限 10MB）")

    tmp_path = ensure_dir(DATA_DIR / "tmp" / "asr") / f"{uuid.uuid4().hex}.wav"
    tmp_path.write_bytes(data)
    try:
        text = await asr_service.transcribe(tmp_path, language)
    except AsrUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"语音识别失败: {e}")
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    return {"text": text}
