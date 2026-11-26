"""SSE 工具"""
import json
from typing import AsyncGenerator, Dict

from fastapi.responses import StreamingResponse


def sse_response(gen: AsyncGenerator[Dict, None]) -> StreamingResponse:
    async def event_generator():
        try:
            async for event in gen:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {err}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "Connection": "keep-alive",
                                      "X-Accel-Buffering": "no"})
