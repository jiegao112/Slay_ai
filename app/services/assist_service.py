from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections.abc import AsyncIterator

from fastapi import Request

from app.agent.reasoner import ReasoningAgent
from app.agent.workflows import WorkflowRunner
from app.core.cancellation import AnalysisCancelled
from app.tools.game_tools import GameToolService
from app.vision.analyzer import VisionAnalyzer


ASSIST_LOCK = threading.Lock()
CURRENT_CANCEL_LOCK = threading.Lock()
CURRENT_CANCEL_EVENT: threading.Event | None = None


def cancel_current_assist() -> bool:
    """取消当前正在进行的分析；前端关闭按钮可调用。"""
    with CURRENT_CANCEL_LOCK:
        if CURRENT_CANCEL_EVENT is None:
            return False
        CURRENT_CANCEL_EVENT.set()
        return True


def _set_current_cancel_event(cancel_event: threading.Event) -> None:
    global CURRENT_CANCEL_EVENT
    with CURRENT_CANCEL_LOCK:
        CURRENT_CANCEL_EVENT = cancel_event


def _clear_current_cancel_event(cancel_event: threading.Event) -> None:
    global CURRENT_CANCEL_EVENT
    with CURRENT_CANCEL_LOCK:
        if CURRENT_CANCEL_EVENT is cancel_event:
            CURRENT_CANCEL_EVENT = None


class AssistService:
    """一次点击悬浮球后的完整后端流程。"""

    def __init__(self):
        self.vision = VisionAnalyzer()
        self.tools = GameToolService(vision=self.vision)
        self.workflow = WorkflowRunner(self.tools)
        self.reasoner = ReasoningAgent(tool_service=self.tools)

    async def stream_assist(self, request: Request) -> AsyncIterator[str]:
        if not ASSIST_LOCK.acquire(blocking=False):
            yield self._comment("busy")
            yield self._sse("error", "已有一次分析正在进行，请稍等它结束后再试。")
            yield self._sse("done", "")
            return

        cancel_event = threading.Event()
        _set_current_cancel_event(cancel_event)
        result_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        def collect_status(message: str) -> None:
            result_queue.put(("status", message))

        def run_collect() -> None:
            try:
                scene = self.vision.scene_or_unknown()
                state = self.workflow.collect(scene, collect_status, cancel_event)
                result_queue.put(("state", state))
            except AnalysisCancelled:
                result_queue.put(("cancelled", ""))
            except Exception as error:
                result_queue.put(("error", error))
            finally:
                _clear_current_cancel_event(cancel_event)
                ASSIST_LOCK.release()

        worker = threading.Thread(target=run_collect, daemon=True)
        worker.start()

        completed = False
        try:
            yield self._comment("connected")
            yield self._sse("status", "我先看一下当前局面...")

            state = None
            while worker.is_alive() or not result_queue.empty():
                if await request.is_disconnected():
                    cancel_event.set()
                    return
                if cancel_event.is_set():
                    yield self._sse("done", "")
                    completed = True
                    return

                try:
                    kind, value = result_queue.get_nowait()
                except queue.Empty:
                    yield self._comment("ping")
                    await asyncio.sleep(1)
                    continue

                if kind == "status":
                    yield self._sse("status", str(value))
                    continue

                if kind == "cancelled":
                    yield self._sse("done", "")
                    completed = True
                    return

                if kind == "error":
                    error = value
                    yield self._sse("error", f"{type(error).__name__}: {error}")
                    yield self._sse("done", "")
                    completed = True
                    return

                state = value
                break

            if state is None:
                yield self._sse("error", "未能采集到当前局面。")
                yield self._sse("done", "")
                completed = True
                return

            yield self._sse("status", "局面看完了，我整理一下建议")
            async for token in self.reasoner.stream_decision(state):
                if await request.is_disconnected():
                    cancel_event.set()
                    return
                if cancel_event.is_set():
                    yield self._sse("done", "")
                    completed = True
                    return
                yield self._sse("token", token)

            yield self._sse("done", "")
            completed = True
        finally:
            if not completed:
                cancel_event.set()

    @staticmethod
    def _sse(event: str, data: object) -> str:
        payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        return f"event: {event}\ndata: {payload}\n\n"

    @staticmethod
    def _comment(message: str) -> str:
        return f": {message}\n\n"
