"""进程内轻量指标（P0-4）：不引第三方监控栈，SQLite 单机部署够用

口径：
- 每个 route 保留最近 LATENCY_SAMPLES 次耗时样本，分位数按样本直接排序取
  （非滑动窗口聚合，重启即清零）
- LLM 侧只记次数：calls=每次 HTTP 尝试，errors=每次失败尝试，
  retries=失败后仍会继续重试的次数，fallback=备用模型被启用的次数，
  json_degraded=json_mode 不可用退化为普通调用的次数
- token 用量在流式响应里拿不到 usage 字段，不编造
"""
import threading
from collections import deque
from typing import Any, Dict, List

LATENCY_SAMPLES = 512


def percentile(samples: List[float], pct: float) -> float:
    """最近邻法分位数：样本少时比插值更可解释"""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, int(round(pct / 100.0 * (len(ordered) - 1)))))
    return float(ordered[idx])


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._requests: Dict[str, int] = {}
        self._latency: Dict[str, deque] = {}
        self._llm = {"calls": 0, "errors": 0, "retries": 0, "fallback": 0,
                     "json_degraded": 0}
        self._distill = {"ok": 0, "fail": 0}
        self._guard = {"input_blocked": 0, "output_blocked": 0, "crisis_hit": 0}

    def observe_request(self, method: str, route: str, status: int, ms: float):
        key = f"{method} {route}"
        with self._lock:
            code_key = f"{key} {status // 100}xx"
            self._requests[code_key] = self._requests.get(code_key, 0) + 1
            self._latency.setdefault(key, deque(maxlen=LATENCY_SAMPLES)).append(ms)

    def observe_llm(self, kind: str, n: int = 1):
        with self._lock:
            if kind in self._llm:
                self._llm[kind] += n

    def observe_distill(self, ok: bool):
        with self._lock:
            self._distill["ok" if ok else "fail"] += 1

    def observe_guard(self, kind: str):
        with self._lock:
            if kind in self._guard:
                self._guard[kind] += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            latency = {
                route: {"n": len(samples),
                        "p50": round(percentile(list(samples), 50), 1),
                        "p95": round(percentile(list(samples), 95), 1),
                        "max": round(max(samples), 1)}
                for route, samples in self._latency.items()
            }
            distill_total = self._distill["ok"] + self._distill["fail"]
            return {
                "requests": dict(self._requests),
                "latency_ms": latency,
                "llm": dict(self._llm),
                "distill": {**self._distill,
                            "total": distill_total,
                            "success_rate": (round(self._distill["ok"] / distill_total, 4)
                                             if distill_total else None)},
                "guard": dict(self._guard),
            }


metrics = Metrics()
