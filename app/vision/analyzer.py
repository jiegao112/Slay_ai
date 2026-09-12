from __future__ import annotations

import base64
import json
import re
from typing import TypeVar
from pathlib import Path

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ValidationError

from app.core.models import ModelFactory, get_model_factory
from app.core.prompts import PromptName, get_prompt
from app.schemas.game import CardCollection, SceneType
from app.schemas.vision import (
    CardCollectionRecognition,
    DeckCountRecognition,
    MonsterRecognition,
    PlayerHpRecognition,
    PotionRecognition,
    RelicRecognition,
    SceneRecognition,
    EventRecognition,
    EnergyRecognition,
    GoldRecognition,
    ShopCardsRecognition,
    CardRewardRecognition,
)
from app.vision.screenshot import ScreenController, get_screen_controller


SchemaT = TypeVar("SchemaT", bound=BaseModel)


class VisionAnalyzer:
    """把截图 + 提示词交给视觉模型，并用 Pydantic 校验 JSON 返回值。

    不使用 LangChain 的 with_structured_output：部分 Qwen OpenAI 兼容网关不会返回
    LangChain 期望的 parsed 字段，容易出现 content 为空导致的 ASGI 报错。
    """

    def __init__(
        self,
        models: ModelFactory | None = None,
        screen: ScreenController | None = None,
    ):
        self.models = models or get_model_factory()
        self.screen = screen or get_screen_controller()

    def _invoke_structured(
        self,
        prompt_name: PromptName,
        schema: type[SchemaT],
        region_name: str,
        *,
        offset_x: int = 0,
        offset_y: int = 0,
    ) -> SchemaT:
        screenshot = self.screen.screenshot_region(region_name, offset_x=offset_x, offset_y=offset_y)
        return self._invoke_image_data_url(prompt_name, schema, screenshot.data_url)

    def _invoke_image_data_url(
        self,
        prompt_name: PromptName,
        schema: type[SchemaT],
        image_data_url: str,
    ) -> SchemaT:
        prompt = get_prompt(prompt_name)
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        final_prompt = (
            f"{prompt}\n\n"
            "你必须只输出一个合法 JSON 对象，不要输出 Markdown，不要输出解释文字。\n"
            f"JSON 必须符合这个 schema：{schema_json}"
        )
        message = HumanMessage(
            content=[
                {"type": "text", "text": final_prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ]
        )
        response = self.models.vision_llm().invoke([message])
        content = self._message_text(response.content)
        try:
            return self._parse_json_content(content, schema)
        except (json.JSONDecodeError, ValidationError, ValueError):
            repaired = self._repair_json(content, schema)
            return self._parse_json_content(repaired, schema)

    def invoke_test_image(self, image_path: str | Path, prompt_name: PromptName, schema: type[SchemaT]) -> SchemaT:
        """给本地测试图片用的入口，不参与正常截图流程。"""
        data_url = self._image_file_to_data_url(Path(image_path))
        return self._invoke_image_data_url(prompt_name, schema, data_url)

    def recognize_scene(self) -> SceneRecognition:
        return self._invoke_structured(PromptName.scene, SceneRecognition, "scene")

    def read_deck_count(self) -> int:
        result = self._invoke_structured(PromptName.deck_count, DeckCountRecognition, "deck_count")
        return result.count

    def read_deck_page(self) -> CardCollection:
        return self._invoke_structured(PromptName.deck_cards, CardCollectionRecognition, "deck_page")

    def read_deck_row(self) -> CardCollection:
        return self._invoke_structured(PromptName.deck_cards, CardCollectionRecognition, "deck_row")

    def read_hand(self) -> CardCollection:
        return self._invoke_structured(PromptName.hand_cards, CardCollectionRecognition, "hand")

    def read_potion_descriptions(self) -> list[str]:
        result = self._invoke_structured(PromptName.potions, PotionRecognition, "potions")
        return result.items

    def read_relic(self, offset_x: int = 0, offset_y: int = 0) -> RelicRecognition:
        return self._invoke_structured(
            PromptName.relic,
            RelicRecognition,
            "relic",
            offset_x=offset_x,
            offset_y=offset_y,
        )

    def read_monsters(self) -> MonsterRecognition:
        return self._invoke_structured(PromptName.monsters, MonsterRecognition, "monsters")

    def read_player_hp(self) -> str:
        result = self._invoke_structured(PromptName.player_hp, PlayerHpRecognition, "player_hp")
        return result.hp

    def read_energy(self) -> str:
        result = self._invoke_structured(PromptName.energy, EnergyRecognition, "energy")
        return result.energy

    def read_event_screen(self) -> EventRecognition:
        return self._invoke_structured(PromptName.event_screen, EventRecognition, "event_screen")

    def read_gold(self) -> int:
        result = self._invoke_structured(PromptName.gold, GoldRecognition, "gold")
        return result.gold

    def read_boss_relic(self, slot: int) -> RelicRecognition:
        return self._invoke_structured(PromptName.relic, RelicRecognition, f"boss_relic_{slot}")

    def read_shop_relic(self, slot: int) -> RelicRecognition:
        return self._invoke_structured(PromptName.relic, RelicRecognition, f"shop_relic_{slot}")

    def read_shop_potion_description(self, slot: int) -> str:
        result = self._invoke_structured(PromptName.potions, PotionRecognition, f"shop_potion_{slot}")
        return result.items[0] if result.items else ""

    def read_shop_cards(self) -> dict[str, int]:
        result = self._invoke_structured(PromptName.shop_cards, ShopCardsRecognition, "shop_cards")
        return result.cards

    def read_card_reward_cards(self) -> list[str]:
        result = self._invoke_structured(
            PromptName.card_reward_cards,
            CardRewardRecognition,
            "card_reward_cards",
        )
        return result.cards

    def scene_or_unknown(self) -> SceneType:
        try:
            return self.recognize_scene().scene
        except Exception:
            return SceneType.unknown

    def _repair_json(self, bad_content: str, schema: type[SchemaT]) -> str:
        """让视觉模型把非标准输出修复成目标 JSON。"""
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        repair_prompt = (
            "下面内容本应是 JSON，但格式不合法或字段不符合要求。"
            "请只输出修复后的合法 JSON，不要输出解释。\n\n"
            f"目标 schema：{schema_json}\n\n"
            f"原始内容：{bad_content}"
        )
        response = self.models.vision_llm().invoke([HumanMessage(content=repair_prompt)])
        return self._message_text(response.content)

    @staticmethod
    def _parse_json_content(content: str, schema: type[SchemaT]) -> SchemaT:
        if not content.strip():
            raise ValueError("视觉模型返回内容为空")
        json_text = VisionAnalyzer._extract_json_object(content)
        return schema.model_validate_json(json_text)

    @staticmethod
    def _extract_json_object(content: str) -> str:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        if cleaned.startswith("{") and cleaned.endswith("}"):
            return cleaned

        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError(f"未找到 JSON 对象：{content[:200]}")
        return match.group(0)

    @staticmethod
    def _message_text(content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    parts.append(str(text))
            return "".join(parts)
        return str(content or "")

    @staticmethod
    def _image_file_to_data_url(path: Path) -> str:
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        suffix = path.suffix.lower()
        mime = "image/png"
        if suffix in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif suffix == ".webp":
            mime = "image/webp"
        return f"data:{mime};base64,{payload}"
