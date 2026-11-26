"""FastAPI 应用入口 v2：lifespan 统一启停（P0-4）"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    asr, auth, characters, chat, conversations, groups, memory, tts)
from app.config import (
    AUTH_ENABLED, DATA_DIR, DB_PATH, HOST, IDLE_SWEEP_INTERVAL, LOG_LEVEL, PORT)
from app.db import get_db
from app.services.distill_service import distill_service
from app.services.idle_service import IdleSweeper
from app.store import store
import app.db as db_module


def setup_logging(level: str = LOG_LEVEL) -> int:
    """统一日志格式，幂等（uvicorn reload 会重复导入本模块）"""
    resolved = getattr(logging, level, logging.INFO)
    root = logging.getLogger()
    if not any(getattr(h, "_app_fmt", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        handler._app_fmt = True
        root.addHandler(handler)
    root.setLevel(resolved)
    for noisy in ("jieba", "httpx._client", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, resolved))
    return resolved


setup_logging()
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    get_db()  # 惰性建库建表
    logger.info("数据库: %s | 鉴权: %s | 日志级别: %s",
                DB_PATH, "开" if AUTH_ENABLED else "关(开发模式)", LOG_LEVEL)
    distill_service.start_worker()
    sweeper = IdleSweeper(check_interval=IDLE_SWEEP_INTERVAL)
    sweeper.start()
    logger.info("后台任务已启动：蒸馏 worker + 闲置巡检(%ss)", IDLE_SWEEP_INTERVAL)
    yield
    await sweeper.stop()
    await distill_service.stop_worker()
    db_module.get_db().close()
    logger.info("后台任务与数据库连接已释放")


app = FastAPI(title="AI 角色扮演聊天工具", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(characters.router)
app.include_router(memory.router)
app.include_router(groups.router)
app.include_router(asr.router)
app.include_router(tts.router)
app.include_router(conversations.router)


@app.get("/")
async def root():
    return {"status": "running", "service": "AI Roleplay Chat", "version": "2.0.0"}


@app.get("/health")
async def health():
    return {"status": "ok", "distill_jobs": store.jobs_pending()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
