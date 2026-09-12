from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.models import ModelFactory, get_model_factory
from app.core.prompts import PromptName, get_prompt
from app.schemas.game import GameState, SceneType
from app.tools.game_tools import GameToolService


SCENE_PROMPTS: dict[SceneType, PromptName] = {
    SceneType.combat: PromptName.combat_decision,
    SceneType.campfire: PromptName.campfire_decision,
    SceneType.event: PromptName.event_decision,
    SceneType.card_reward: PromptName.card_reward_decision,
    SceneType.boss_relic: PromptName.boss_relic_decision,
    SceneType.start_event: PromptName.start_event_decision,
    SceneType.shop: PromptName.shop_decision,
    SceneType.unknown: PromptName.unknown_decision,
}


class ReasoningAgent:
    """带工具的推理 Agent，并支持流式输出。"""

    def __init__(
        self,
        models: ModelFactory | None = None,
        tool_service: GameToolService | None = None,
    ):
        self.models = models or get_model_factory()
        self.tool_service = tool_service or GameToolService()

    async def stream_decision(self, state: GameState) -> AsyncIterator[str]:
        prompt = self._decision_prompt(state)

        async for chunk in self._stream_plain(prompt):
            cleaned = self._clean_visible_text(chunk)
            if cleaned:
                yield cleaned

    async def _stream_plain(self, system_prompt: str) -> AsyncIterator[str]:
        llm = self.models.reasoning_llm()
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content="请给出下一步建议。"),
        ]
        async for chunk in llm.astream(messages):
            content = getattr(chunk, "content", "")
            if isinstance(content, str):
                yield content

    @staticmethod
    def _decision_prompt(state: GameState) -> str:
        state_json = json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2)
        prompt_name = SCENE_PROMPTS.get(state.scene, PromptName.unknown_decision)
        return get_prompt(prompt_name, state=state_json)

    @staticmethod
    def _clean_visible_text(text: str) -> str:
        """兜底清理 Markdown 痕迹，避免悬浮窗显示 #/**/代码块。"""
        if not text:
            return ""
        text = text.replace("```", "")
        text = text.replace("**", "")
        text = text.replace("__", "")
        text = text.replace("*", "")
        text = re.sub(r"^#{1,6}\s*", "", text)
        text = re.sub(r"(?m)^-\s+", "", text)
        return text
