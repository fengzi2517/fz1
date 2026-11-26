"""配置管理"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
if not DATA_DIR.is_absolute():
    DATA_DIR = (BASE_DIR / DATA_DIR).resolve()

# LLM 配置
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", LLM_MODEL)
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.8"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))

# 服务配置
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# 记忆配置
MAX_HISTORY_MESSAGES = 20  # 滑动窗口大小
RECALL_TOP_K = 10  # recall 返回条目数
RECALL_KEYWORD_OVERLAP_THRESHOLD = 0.6  # 去重阈值

# 会话蒸馏
# 空闲多久后自动蒸馏（秒），巡检间隔（秒）
SESSION_IDLE_TIMEOUT = int(os.getenv("SESSION_IDLE_TIMEOUT", "1800"))  # 30 分钟无消息自动结束
IDLE_SWEEP_INTERVAL = int(os.getenv("IDLE_SWEEP_INTERVAL", "300"))     # 每 5 分钟巡检一次
# 会话软轮换：单会话超过该消息数或跨天时，重新进入会开新会话并蒸馏旧会话
SESSION_MAX_MESSAGES = 60


# 语音识别（ASR）配置
# provider: local（本地 whisper.cpp）/ openai（OpenAI 兼容 /audio/transcriptions）/ disabled
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "local")
ASR_LANGUAGE = os.getenv("ASR_LANGUAGE", "zh")
# 本地 whisper.cpp
WHISPER_BIN = os.getenv(
    "WHISPER_BIN", str(BASE_DIR.parent / "tools" / "whisper" / "Release" / "whisper-cli.exe")
)
WHISPER_MODEL = os.getenv(
    "WHISPER_MODEL", str(BASE_DIR.parent / "tools" / "whisper" / "ggml-base.bin")
)
# OpenAI 兼容 ASR
ASR_API_BASE = os.getenv("ASR_API_BASE", "")
ASR_API_KEY = os.getenv("ASR_API_KEY", "")
ASR_MODEL = os.getenv("ASR_MODEL", "whisper-1")

# 语音合成（云端）
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "siliconflow")
TTS_API_BASE = os.getenv("TTS_API_BASE", "")
TTS_API_KEY = os.getenv("TTS_API_KEY", "")
TTS_MODEL = os.getenv("TTS_MODEL", "fnlp/MOSS-TTSD-v0.5")
TTS_VOICE = os.getenv("TTS_VOICE", "fnlp/MOSS-TTSD-v0.5:claire")
TTS_SPEED = float(os.getenv("TTS_SPEED", "1.0"))


def get_user_dir(user_id: str) -> Path:
    """获取用户数据目录"""
    return DATA_DIR / "users" / user_id


def get_character_dir(user_id: str, character_id: str) -> Path:
    """获取角色数据目录"""
    return get_user_dir(user_id) / "characters" / character_id


def get_session_dir(user_id: str, character_id: str, session_id: str) -> Path:
    """获取会话数据目录"""
    return get_character_dir(user_id, character_id) / "sessions" / session_id


def get_group_dir(user_id: str, group_id: str) -> Path:
    """获取群聊数据目录"""
    return get_user_dir(user_id) / "groups" / group_id


def ensure_dir(path: Path) -> Path:
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)
    return path
