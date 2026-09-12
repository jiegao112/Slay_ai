from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.services.assist_service import AssistService, cancel_current_assist


router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": get_settings().app_name}


@router.get("/assist/stream")
async def assist_stream_get(request: Request) -> StreamingResponse:
    return _assist_stream(request)


@router.post("/assist/stream")
async def assist_stream_post(request: Request) -> StreamingResponse:
    return _assist_stream(request)


@router.post("/assist/cancel")
def assist_cancel() -> dict[str, bool]:
    return {"cancelled": cancel_current_assist()}


def _assist_stream(request: Request) -> StreamingResponse:
    service = AssistService()
    return StreamingResponse(
        service.stream_assist(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
