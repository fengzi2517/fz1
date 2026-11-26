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
MAX_HISTORY_MESSAGES = 20  # 滑动窗口大小（降级为兜底值，优先使用 token 预算装配）
RECALL_TOP_K = 10  # recall 返回条目数
RECALL_KEYWORD_OVERLAP_THRESHOLD = 0.6  # 去重阈值（词面兜底，语义消解见冲突管线）

# 会话蒸馏
# 空闲多久后自动蒸馏（秒），巡检间隔（秒）
SESSION_IDLE_TIMEOUT = int(os.getenv("SESSION_IDLE_TIMEOUT", "1800"))  # 30 分钟无消息自动结束
IDLE_SWEEP_INTERVAL = int(os.getenv("IDLE_SWEEP_INTERVAL", "300"))     # 每 5 分钟巡检一次
# 会话软轮换：单会话超过该消息数或跨天时，重新进入会开新会话并蒸馏旧会话
SESSION_MAX_MESSAGES = 60

# ===== 升级项（docs/upgrade/03_升级方案.md）=====

# 鉴权：AUTH_ENABLED=false 时保留 ?user_id= 自报模式（开发兼容，默认关闭该后门）
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() != "false"
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
AUTH_TOKEN_TTL = int(os.getenv("AUTH_TOKEN_TTL", "604800"))  # 7 天
if AUTH_ENABLED and not AUTH_SECRET:
    # 首次启动自动生成并持久化随机密钥
    import os as _os
    _secret_file = DATA_DIR / "auth_secret.key"
    if _secret_file.exists():
        AUTH_SECRET = _secret_file.read_text().strip()
        try:
            _os.chmod(_secret_file, 0o600)  # 收紧历史版本按默认 umask 写下的密钥
        except OSError:
            pass
    else:
        import secrets as _secrets
        _secret_file.parent.mkdir(parents=True, exist_ok=True)
        AUTH_SECRET = _secrets.token_hex(32)
        # write_text() 沿用进程 umask（0002 时得到 0664），同组用户读到签名密钥即可伪造任意用户 token
        with _os.fdopen(_os.open(_secret_file, _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL, 0o600),
                        "w") as f:
            f.write(AUTH_SECRET)

# 数据库（升级后真相源；Markdown 文件降级为导出/审计视图）
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "roleplay.db"))).resolve()

# 上下文 token 预算（近似估算：CJK 每字≈1 token，其他每 4 字符≈1 token）
CTX_WINDOW_TOKENS = int(os.getenv("CTX_WINDOW_TOKENS", "32000"))
CTX_BUDGET_PROFILE = float(os.getenv("CTX_BUDGET_PROFILE", "0.25"))   # 常驻设定
CTX_BUDGET_SUMMARY = float(os.getenv("CTX_BUDGET_SUMMARY", "0.15"))   # 摘要
CTX_BUDGET_RECALL = float(os.getenv("CTX_BUDGET_RECALL", "0.15"))    # 记忆召回
CTX_BUDGET_HISTORY_MIN = float(os.getenv("CTX_BUDGET_HISTORY_MIN", "0.40"))  # 近期历史保底

# Embedding（可选；未配置时混合检索退化为 BM25+时间+重要性）
EMBED_BASE_URL = os.getenv("EMBED_BASE_URL", "")
EMBED_API_KEY = os.getenv("EMBED_API_KEY", "")
EMBED_MODEL = os.getenv("EMBED_MODEL", "")

# 蒸馏专用模型（sleep-time 思路：与聊天模型分离，可用更廉价模型）
DISTILL_MODEL = os.getenv("DISTILL_MODEL", LLM_MODEL)

# 混合检索参数（借鉴 SillyTavern：块 400 字符 / 阈值 0.25 / Insert 3 / Retain 5）
RECALL_BLOCK_CHARS = 400
RECALL_VEC_THRESHOLD = float(os.getenv("RECALL_VEC_THRESHOLD", "0.25"))
RECALL_INJECT_TOP_K = int(os.getenv("RECALL_INJECT_TOP_K", "6"))

# 群聊调度
GROUP_MEMBER_LIMIT = int(os.getenv("GROUP_MEMBER_LIMIT", "6"))
GROUP_SPEAKERS_PER_ROUND = int(os.getenv("GROUP_SPEAKERS_PER_ROUND", "2"))
GROUP_PARALLEL_REPLY = os.getenv("GROUP_PARALLEL_REPLY", "true").lower() != "false"


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
