"""请求上下文：request_id 贯穿日志与响应头（P0-4）

contextvar 而非 threadlocal：uvicorn 单进程协程模型下，线程本地会在
await 交叉处串号，日志里的 request_id 就成了假线索。
"""
import contextvars
import logging
import uuid

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


class RequestIdFilter(logging.Filter):
    """给每条日志补 request_id，供 "%(request_id)s" 占位符使用"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True
