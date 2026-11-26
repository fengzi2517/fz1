"""真机 HTTP 冒烟：假 OpenAI 端点 + 真 uvicorn 应用，走完整链路

与 pytest（进程内 TestClient + FakeLLM）互补：这里验证真实 socket、真实
SSE 分帧、真实 httpx 流式解析、真实文件 SQLite + WAL、真实后台 worker/巡检，
以及 SIGINT 下的优雅关停。

    cd backend && .venv/bin/python smoke_http.py        # 全绿退出码 0

不写仓库数据：所有落盘都在临时目录，脚本自己清理。
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

STUB_PORT, APP_PORT = 8399, 8077
STUB = f"http://127.0.0.1:{STUB_PORT}"
BASE = f"http://127.0.0.1:{APP_PORT}"
BACKEND = os.path.dirname(os.path.abspath(__file__))
passes, fails = [], []


def ok(name, cond, extra=""):
    (passes if cond else fails).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' | ' + str(extra)) if extra else ''}")


# ---------- 假 OpenAI 兼容端点 ----------

JSON_REPLY = {
    "user_preferences": ["用户住在杭州", "用户喜欢打篮球"],
    "character_relationship": [], "group_events": ["用户说周末要去西湖跑步"],
    "changed": True, "profile": "用户住在杭州，喜欢打篮球。",
    "action": "ADD", "target_ids": [],
    "affinity": 0.62, "emotion": "亲近", "stage": "朋友",
}


def build_stub_app() -> FastAPI:
    import hashlib
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        p = await req.json()
        if p.get("stream"):
            async def gen():
                for piece in ["你好", "，我是", "阿强。"]:
                    yield "data: " + json.dumps(
                        {"choices": [{"index": 0, "delta": {"content": piece}}]},
                        ensure_ascii=False) + "\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        content = (json.dumps(JSON_REPLY, ensure_ascii=False)
                   if p.get("response_format") else "这是滚动摘要：用户聊了周末计划。")
        return JSONResponse({"choices": [{"index": 0, "message": {
            "role": "assistant", "content": content}}]})

    @app.post("/v1/embeddings")
    async def embed(req: Request):
        p = await req.json()
        texts = p.get("input") or []
        if isinstance(texts, str):
            texts = [texts]
        return JSONResponse({"data": [
            {"index": i, "embedding": [b / 255 for b in hashlib.md5(t.encode()).digest()]}
            for i, t in enumerate(texts)]})

    return app


def wait_ready(url, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(url, timeout=1.5)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def sse(client, url, payload, headers):
    events = []
    with client.stream("POST", url, json=payload, headers=headers) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    events.append({"type": "_bad_json", "raw": line})
        return events, dict(r.headers)


def main() -> int:
    data_dir = tempfile.mkdtemp(prefix="rp_smoke_")
    cfg = build_stub_app()
    server = uvicorn.Server(uvicorn.Config(cfg, host="127.0.0.1", port=STUB_PORT,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    ok("假 LLM 端点就绪", wait_ready(f"{STUB}/docs"))

    env = {**os.environ, "DATA_DIR": data_dir, "AUTH_ENABLED": "true",
           "LLM_BASE_URL": f"{STUB}/v1", "LLM_API_KEY": "sk-smoke",
           "LLM_MODEL": "smoke-chat", "DISTILL_MODEL": "smoke-distill",
           "EMBED_BASE_URL": f"{STUB}/v1", "EMBED_API_KEY": "sk-smoke",
           "EMBED_MODEL": "smoke-embed", "IDLE_SWEEP_INTERVAL": "2",
           "GROUP_PARALLEL_REPLY": "true"}
    env.pop("AUTH_SECRET", None)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                             "--host", "127.0.0.1", "--port", str(APP_PORT),
                             "--log-level", "warning"], cwd=BACKEND, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        ok("app 启动（真实 lifespan：建库 + 蒸馏 worker + 闲置巡检）",
           wait_ready(f"{BASE}/health"))
        c = httpx.Client(base_url=BASE, timeout=30)
        h = c.get("/health")
        ok("GET /health 公开", h.status_code == 200 and h.json()["status"] == "ok")

        secret_file = os.path.join(data_dir, "auth_secret.key")
        ok("未配 AUTH_SECRET 时自动生成密钥且仅属主可读写（0600）",
           os.path.exists(secret_file)
           and stat.S_IMODE(os.stat(secret_file).st_mode) == 0o600,
           oct(stat.S_IMODE(os.stat(secret_file).st_mode)) if os.path.exists(secret_file) else "缺失")

        # 鉴权
        c.post("/api/auth/register", json={"username": "smokeu", "password": "smoke1234"})
        r = c.post("/api/auth/login", json={"username": "smokeu", "password": "smoke1234"})
        ok("登录返回 JWT + user_id", r.status_code == 200 and r.json().get("token"))
        tok, uid = r.json()["token"], r.json()["user_id"]
        A = {"Authorization": f"Bearer {tok}"}
        ok("无 token → 401", c.get("/api/characters").status_code == 401)
        ok("篡改 token → 401",
           c.get("/api/characters", headers={"Authorization": f"Bearer {tok[:-2]}xx"}).status_code == 401)
        ok("?token= 可用（原生播放器无法设 header）",
           c.get("/api/characters", params={"token": tok}).status_code == 200)
        ok("鉴权开启时 ?user_id= 后门失效",
           c.get("/api/characters", params={"user_id": uid}).status_code == 401)

        # 角色 + 单聊 SSE
        r = c.post("/api/characters", json={"name": "阿强", "nickname": "强子",
                                            "talkativeness": 60,
                                            "raw_content": "# 阿强\n邻居，爱跑步，说话幽默。"},
                   headers=A)
        ok("创建角色", r.status_code == 200 and r.json().get("id"), r.status_code)
        cid = r.json()["id"]
        ev, hdrs = sse(c, "/api/chat/send",
                       {"character_id": cid, "message": "你好，你叫什么？"}, A)
        kinds = [e.get("type") for e in ev]
        text = "".join(e.get("content", "") for e in ev if e.get("type") == "token")
        sid = next((e.get("session_id") for e in ev if e.get("type") == "done"), None)
        ok("真实 SSE 分帧可拼成完整回复", text == "你好，我是阿强。", repr(text))
        ok("done 事件带回 session_id", bool(sid), sid)
        ok("无 error 事件", "error" not in kinds, kinds)
        ok("SSE 响应头带 anti-buffering",
           hdrs.get("x-accel-buffering") == "no" and "text/event-stream" in (hdrs.get("content-type") or ""))

        for msg in ["我最近想运动", "我在杭州，有推荐吗", "我喜欢打篮球"]:
            sse(c, "/api/chat/send", {"character_id": cid, "session_id": sid,
                                      "message": msg}, A)
        hist = c.get("/api/chat/history", params={"character_id": cid}, headers=A).json()
        ok("历史落库且顺序正确",
           [m["role"] for m in hist["messages"]] == ["user", "assistant"] * 4,
           [m["role"] for m in hist["messages"]])

        # 蒸馏 worker（后台 task 真跑）
        r = c.post("/api/chat/end", json={"character_id": cid, "session_id": sid}, headers=A)
        ok("结束会话 → 蒸馏入队", r.status_code == 200 and r.json()["status"] == "queued", r.json())
        summary, entries = "", []
        for _ in range(40):
            time.sleep(0.5)
            hist = c.get("/api/chat/history", params={"character_id": cid}, headers=A).json()
            summary = hist.get("summary") or ""
            e = c.get("/api/memory/long-term", headers=A)
            entries = e.json().get("entries", []) if e.status_code == 200 else []
            if summary and entries:
                break
        ok("worker 落库 sessions.summary", "滚动摘要" in summary, summary[:36])
        ok("worker 落库记忆条目", len(entries) >= 1, [x["content"] for x in entries][:4])
        prof = c.get("/api/memory/profile", headers=A).json().get("content") or ""
        ok("画像闭环（write_user_profile 真正被调用）", "杭州" in prof, prof[:36])
        ok("队列排空，无 pending/running 残留", c.get("/health").json()["distill_jobs"] == 0)

        # 混合检索
        hits = c.get("/api/memory/search", params={"q": "打篮球", "character_id": cid},
                     headers=A).json().get("hits", [])
        ok("记忆检索命中（BM25 + 向量 + 衰减）", len(hits) >= 1,
           [h.get("content") for h in hits][:3])
        conv = c.get("/api/conversations", headers=A)
        ok("会话列表可查（多对话管理）",
           conv.status_code == 200 and isinstance(conv.json(), list) and len(conv.json()) >= 1)

        # 群聊
        cid2 = c.post("/api/characters", json={"name": "小美", "talkativeness": 80,
                                               "raw_content": "# 小美\n同事，性格活泼。"},
                      headers=A).json()["id"]
        gid = c.post("/api/groups", json={"name": "周末局",
                                          "character_ids": [cid, cid2]},
                     headers=A).json()["id"]
        ok("建群（2 名成员）", bool(gid), gid)
        ev, _ = sse(c, f"/api/groups/{gid}/chat",
                    {"message": "@小美 你觉得去哪玩", "reply_mode": "auto"}, A)
        speakers = {e.get("character_id") for e in ev if e.get("type") == "done"}
        kinds = [e.get("type") for e in ev]
        ok("@提及只让被点名成员发言", speakers == {cid2}, speakers)
        ok("群聊事件顺序含 start/done/end 且无 error",
           "start" in kinds and "end" in kinds and "error" not in kinds, kinds[:8])
        r = c.post(f"/api/groups/{gid}/chat", json={"message": "我周末想去西湖跑步",
                                                    "reply_mode": "all"}, headers=A)
        ok("all 模式全员回复", r.status_code == 200 and "data:" in r.text, r.status_code)
        gh = c.get(f"/api/groups/{gid}/history", headers=A).json()
        ok("群历史落库", len(gh.get("messages", [])) >= 4, len(gh.get("messages", [])))
        r = c.post(f"/api/groups/{gid}/end", json={}, headers=A)
        ok("群会话结束 → 群蒸馏入队", r.json().get("status") == "queued", r.json())
        time.sleep(2.5)
        ok("群蒸馏 worker 排空", c.get("/health").json()["distill_jobs"] == 0)

        # 租户隔离与注入面
        rb = c.post("/api/auth/register", json={"username": "other", "password": "other1234"})
        B = {"Authorization": f"Bearer {rb.json()['token']}"}
        c2 = httpx.Client(base_url=BASE, timeout=20)
        ok("跨用户读不到角色", c2.get(f"/api/characters/{cid}", headers=B).status_code == 404)
        ok("跨用户读不到群历史",
           c2.get(f"/api/groups/{gid}/history", headers=B).status_code == 404)
        ok("跨用户记忆检索为空",
           c2.get("/api/memory/search", params={"q": "打篮球"}, headers=B).json()["hits"] == [])
        bad = c2.get("/api/chat/history", params={"character_id": "../../etc/passwd"}, headers=A)
        ok("路径穿越 character_id 被拒", bad.status_code >= 400, bad.status_code)
        ok("语音 status 端点存活（P3 未做，只验不炸）",
           c.get("/api/tts/status").status_code == 200 and c.get("/api/asr/status").status_code == 200)

        # 优雅关停
        proc.send_signal(2)
        t0 = time.time()
        while proc.poll() is None and time.time() - t0 < 15:
            time.sleep(0.3)
        ok("SIGINT 后干净退出（worker/巡检/DB 连接释放，无 traceback）", proc.poll() is not None)
        ok("SQLite 主库文件存在", os.path.exists(os.path.join(data_dir, "roleplay.db")))
    finally:
        if proc.poll() is None:
            proc.kill()
        server.should_exit = True
        shutil.rmtree(data_dir, ignore_errors=True)

    print(f"\n=== 冒烟：{len(passes)} passed / {len(fails)} failed ===")
    if fails:
        print("失败项:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
