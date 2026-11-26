"""FastAPI 应用入口"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, characters, memory, groups, asr, tts, conversations
from app.config import HOST, PORT, DATA_DIR, IDLE_SWEEP_INTERVAL
from app.services.idle_service import IdleSweeper

app = FastAPI(title="AI 角色扮演聊天工具", version="0.1.0")

# CORS 配置（开发阶段允许所有来源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat.router)
app.include_router(characters.router)
app.include_router(memory.router)
app.include_router(groups.router)
app.include_router(asr.router)
app.include_router(tts.router)
app.include_router(conversations.router)


_sweeper = IdleSweeper(check_interval=IDLE_SWEEP_INTERVAL)


@app.on_event("startup")
async def startup():
    """启动时确保数据目录存在，并启动闲置会话巡检"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[Startup] 数据目录: {DATA_DIR}")
    _sweeper.start()


@app.on_event("shutdown")
async def shutdown():
    await _sweeper.stop()


@app.get("/")
async def root():
    return {"status": "running", "service": "AI Roleplay Chat"}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
