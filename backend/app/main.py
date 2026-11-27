"""FastAPI 应用入口 v2：lifespan 统一启停 + request_id/指标中间件（P0-4）"""
import hmac
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    asr, auth, characters, chat, conversations, groups, memory, tts)
from app.config import (
    AUTH_ENABLED, CORS_ALLOW_ORIGIN_LIST, DATA_DIR, DB_PATH, HOST,
    IDLE_SWEEP_INTERVAL, LOG_LEVEL, METRICS_ENABLED, METRICS_TOKEN, PORT)
from app.core.metrics import metrics
from app.core.trace import RequestIdFilter, new_request_id, request_id_var
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
            "%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s"))
        handler.addFilter(RequestIdFilter())
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

# 凭证走 Authorization: Bearer，不用 cookie —— 关掉 allow_credentials，
# 否则任意站点带着用户 cookie 就能读到响应（P0-1 CORS 收敛）
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGIN_LIST,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_and_metrics(request: Request, call_next):
    """request_id 贯穿 + 每路由耗时/状态计数。

    SSE 端点的耗时是"到响应头"的时间，不含整段流式输出。
    """
    rid = request.headers.get("x-request-id") or new_request_id()
    token = request_id_var.set(rid)
    started = time.perf_counter()
    status = 500
    try:
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            route = getattr(request.scope.get("route"), "path", request.url.path)
            metrics.observe_request(request.method, route, status,
                                    (time.perf_counter() - started) * 1000)
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response
    except Exception:
        logger.exception("未捕获异常 %s %s", request.method, request.url.path)
        raise


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


@app.get("/metrics")
async def metrics_endpoint(request: Request):
    """运维指标。默认关闭；METRICS_TOKEN 未配置时即使开关打开也不暴露。"""
    if not METRICS_ENABLED or not METRICS_TOKEN:
        raise HTTPException(status_code=404)
    given = request.headers.get("x-metrics-token") or request.query_params.get("token")
    if not given or not hmac.compare_digest(str(given), METRICS_TOKEN):
        raise HTTPException(status_code=401)
    return metrics.snapshot()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
