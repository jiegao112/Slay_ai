from __future__ import annotations

import json
import logging
import threading
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.core.cancellation import AnalysisCancelled
from app.rag.game_data import GameDataRepository
from app.rag.retriever import get_retriever
from app.schemas.game import (
    CardCollection,
    CardInfo,
    EventInfo,
    MonsterInfo,
    PotionInfo,
    RelicInfo,
    ShopCardInfo,
    ShopInfo,
)
from app.vision.analyzer import VisionAnalyzer
from app.vision.screenshot import ScreenController


logger = logging.getLogger(__name__)


class NoInput(BaseModel):
    """LangChain tool 的空输入 schema。"""


class GameToolService:
    """推理 Agent 可调用的游戏状态采集工具集合。"""

    def __init__(
        self,
        vision: VisionAnalyzer | None = None,
        screen: ScreenController | None = None,
        cancel_event: threading.Event | None = None,
    ):
        self.vision = vision or VisionAnalyzer()
        self.screen = screen or self.vision.screen
        self.game_data = GameDataRepository()
        self.cancel_event = cancel_event

    def get_deck(self) -> CardCollection:
        """获取牌库：读数量、按 D 打开牌库、逐行滚动识别并合并。"""
        self._check_cancel()
        logger.info("工具 get_deck：开始识别牌库数量")
        total_count = self.vision.read_deck_count()
        self._check_cancel()
        deck = CardCollection()

        self.screen.press("d")
        try:
            logger.info("工具 get_deck：打开牌库，识别第一页，total_count=%s", total_count)
            deck = self.vision.read_deck_page()
            self._check_cancel()
            extra_rows = self._extra_deck_rows(total_count)
            scroll_delta = int(self.screen.regions.action("deck_scroll_delta", -480))

            for index in range(extra_rows):
                self._check_cancel()
                logger.info("工具 get_deck：滚动识别第 %s 行增量", index + 1)
                self.screen.scroll(scroll_delta)
                row_cards = self.vision.read_deck_row()
                deck = deck.merge(row_cards)
        finally:
            self.screen.press("esc")

        return self._enrich_cards(deck)

    def get_hand(self) -> CardCollection:
        self._check_cancel()
        logger.info("工具 get_hand：开始识别手牌")
        return self._enrich_cards(self.vision.read_hand())

    def get_potions(self) -> list[PotionInfo]:
        self._check_cancel()
        logger.info("工具 get_potions：开始识别药水")
        descriptions = self.vision.read_potion_descriptions()
        retriever = get_retriever("yaoshui")
        potions: list[PotionInfo] = []
        for desc in descriptions:
            self._check_cancel()
            hit = retriever.best(desc)
            potions.append(
                PotionInfo(
                    raw_description=desc,
                    name=hit.name if hit else "",
                    effect=hit.effect if hit else "",
                    confidence=hit.confidence if hit else 0.0,
                )
            )
        return potions

    def get_relics(self) -> list[RelicInfo]:
        self._check_cancel()
        logger.info("工具 get_relics：开始识别遗物")
        relics: list[RelicInfo] = []
        retriever = get_retriever("yiwu")
        step_x = int(self.screen.regions.action("relic_step_x", 72))
        step_y = int(self.screen.regions.action("relic_step_y", 0))
        max_count = int(self.screen.regions.action("relic_max_count", 40))

        for index in range(max_count):
            self._check_cancel()
            logger.info("工具 get_relics：识别第 %s 个遗物位置", index + 1)
            raw = self.vision.read_relic(offset_x=step_x * index, offset_y=step_y * index)
            if raw.miaoshu.strip() == "空":
                logger.info("工具 get_relics：第 %s 个位置为空，停止", index + 1)
                break

            hit = retriever.best(raw.miaoshu)
            relics.append(
                RelicInfo(
                    raw_description=raw.miaoshu,
                    name=hit.name if hit else "",
                    effect=hit.effect if hit else "",
                    cengshu=raw.cengshu,
                    confidence=hit.confidence if hit else 0.0,
                )
            )

        return relics

    def get_boss_relic_choices(self) -> list[RelicInfo]:
        """Boss 遗物选择界面：三个位置分别截图识别。"""
        self._check_cancel()
        choices: list[RelicInfo] = []
        retriever = get_retriever("yiwu")
        for slot in range(1, 4):
            self._check_cancel()
            raw = self.vision.read_boss_relic(slot)
            if raw.miaoshu.strip() == "空":
                continue
            hit = retriever.best(raw.miaoshu)
            choices.append(
                RelicInfo(
                    raw_description=raw.miaoshu,
                    name=hit.name if hit else "",
                    effect=hit.effect if hit else "",
                    cengshu=raw.cengshu,
                    confidence=hit.confidence if hit else 0.0,
                )
            )
        return choices

    def get_monsters(self) -> list[MonsterInfo]:
        self._check_cancel()
        logger.info("工具 get_monsters：开始识别怪物")
        raw = self.vision.read_monsters()
        retriever = get_retriever("guaiwu")
        monsters: list[MonsterInfo] = []
        strategy_seen_fenzu: set[int] = set()
        for item in raw.monsters:
            self._check_cancel()
            hit = retriever.best(item.miaoshu)
            fenzu = hit.fenzu if hit else None
            strategy = hit.strategy if hit else ""
            if fenzu is not None:
                if fenzu in strategy_seen_fenzu:
                    strategy = ""
                else:
                    strategy_seen_fenzu.add(fenzu)
            monsters.append(
                MonsterInfo(
                    raw_description=item.miaoshu,
                    hp=item.hp,
                    shanghai=item.shanghai,
                    name=hit.name if hit else "",
                    strategy=strategy,
                    fenzu=fenzu,
                    confidence=hit.confidence if hit else 0.0,
                )
            )
        return monsters

    def get_player_hp(self) -> str:
        self._check_cancel()
        logger.info("工具 get_player_hp：开始识别血量")
        return self.vision.read_player_hp()

    def get_energy(self) -> str:
        self._check_cancel()
        logger.info("工具 get_energy：开始识别能量")
        return self.vision.read_energy()

    def get_gold(self) -> int:
        self._check_cancel()
        return self.vision.read_gold()

    def get_event(self) -> EventInfo:
        self._check_cancel()
        raw = self.vision.read_event_screen()
        return EventInfo(
            name=raw.name,
            text=raw.text,
            xuanxiang=raw.xuanxiang,
            strategy=self.game_data.get_event_strategy(raw.name),
        )

    def get_card_reward_choices(self) -> list[CardInfo]:
        """奖励选牌界面：识别候选卡名，再查 kapai 表补费用和效果。"""
        self._check_cancel()
        choices: list[CardInfo] = []
        for name in self.vision.read_card_reward_cards():
            self._check_cancel()
            record = self.game_data.get_card(name)
            choices.append(
                CardInfo(
                    name=name,
                    cost=record.cost if record else "",
                    effect=record.effect if record else "",
                )
            )
        return choices

    def get_shop(self) -> ShopInfo:
        self._check_cancel()
        cards = self._shop_cards()
        self._check_cancel()
        relics = self._shop_relics()
        self._check_cancel()
        potions = self._shop_potions()
        return ShopInfo(
            cards=cards["cards"],
            relics=relics,
            potions=potions,
            remove_card_price=cards["remove_card_price"],
            gold=self.get_gold(),
        )

    @staticmethod
    def _extra_deck_rows(total_count: int) -> int:
        if total_count <= 15:
            return 0
        return ((total_count - 15) // 5) + 1

    def _enrich_cards(self, cards: CardCollection) -> CardCollection:
        """视觉只读卡名；费用和效果从 kapai 表按 name 补齐。"""
        effects = dict(cards.kapai_miaoshu)
        costs = dict(cards.kapai_cost)
        for name in cards.kapai_num:
            self._check_cancel()
            record = self.game_data.get_card(name)
            if record is None:
                continue
            effects.setdefault(name, record.effect)
            costs.setdefault(name, record.cost)
        return CardCollection(kapai_num=cards.kapai_num, kapai_miaoshu=effects, kapai_cost=costs)

    def _shop_cards(self) -> dict[str, list[ShopCardInfo] | int | None]:
        self._check_cancel()
        raw_cards = self.vision.read_shop_cards()
        remove_card_price = raw_cards.pop("删除卡牌", None)
        shop_cards: list[ShopCardInfo] = []
        for name, price in raw_cards.items():
            self._check_cancel()
            record = self.game_data.get_card(name)
            shop_cards.append(
                ShopCardInfo(
                    name=name,
                    price=int(price),
                    cost=record.cost if record else "",
                    effect=record.effect if record else "",
                )
            )
        return {"cards": shop_cards, "remove_card_price": remove_card_price}

    def _shop_relics(self) -> list[RelicInfo]:
        self._check_cancel()
        relics: list[RelicInfo] = []
        retriever = get_retriever("yiwu")
        for slot in range(1, 4):
            self._check_cancel()
            raw = self.vision.read_shop_relic(slot)
            if raw.miaoshu.strip() == "空":
                continue
            hit = retriever.best(raw.miaoshu)
            relics.append(
                RelicInfo(
                    raw_description=raw.miaoshu,
                    name=hit.name if hit else "",
                    effect=hit.effect if hit else "",
                    cengshu=raw.cengshu,
                    confidence=hit.confidence if hit else 0.0,
                )
            )
        return relics

    def _shop_potions(self) -> list[PotionInfo]:
        self._check_cancel()
        potions: list[PotionInfo] = []
        retriever = get_retriever("yaoshui")
        for slot in range(1, 4):
            self._check_cancel()
            desc = self.vision.read_shop_potion_description(slot)
            if not desc or desc.strip() == "空":
                continue
            hit = retriever.best(desc)
            potions.append(
                PotionInfo(
                    raw_description=desc,
                    name=hit.name if hit else "",
                    effect=hit.effect if hit else "",
                    confidence=hit.confidence if hit else 0.0,
                )
            )
        return potions

    def _check_cancel(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise AnalysisCancelled("用户已关闭本次分析。")


def _jsonable(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    elif isinstance(value, list):
        value = [item.model_dump() if hasattr(item, "model_dump") else item for item in value]
    return json.dumps(value, ensure_ascii=False, indent=2)


def build_langchain_tools(service: GameToolService | None = None) -> list[StructuredTool]:
    """构建给推理模型补查信息用的 LangChain tools。"""
    tool_service = service or GameToolService()

    return [
        StructuredTool.from_function(
            name="get_deck",
            description="识别并返回当前牌库中的卡牌数量和描述。",
            func=lambda: _jsonable(tool_service.get_deck()),
            args_schema=NoInput,
        ),
        StructuredTool.from_function(
            name="get_hand",
            description="识别并返回当前手牌中的卡牌数量和描述。",
            func=lambda: _jsonable(tool_service.get_hand()),
            args_schema=NoInput,
        ),
        StructuredTool.from_function(
            name="get_potions",
            description="识别药水图标，并通过 RAG 返回药水名称和作用。",
            func=lambda: _jsonable(tool_service.get_potions()),
            args_schema=NoInput,
        ),
        StructuredTool.from_function(
            name="get_relics",
            description="逐个识别遗物图标，并通过 RAG 返回遗物名称、作用和层数。",
            func=lambda: _jsonable(tool_service.get_relics()),
            args_schema=NoInput,
        ),
        StructuredTool.from_function(
            name="get_monsters",
            description="识别怪物外观和血量，并通过 RAG 返回怪物名称与策略。",
            func=lambda: _jsonable(tool_service.get_monsters()),
            args_schema=NoInput,
        ),
        StructuredTool.from_function(
            name="get_player_hp",
            description="识别并返回玩家当前血量，例如 67/71。",
            func=lambda: tool_service.get_player_hp(),
            args_schema=NoInput,
        ),
        StructuredTool.from_function(
            name="get_energy",
            description="识别并返回当前能量，例如 2/3。",
            func=lambda: tool_service.get_energy(),
            args_schema=NoInput,
        ),
        StructuredTool.from_function(
            name="get_gold",
            description="识别并返回玩家当前金币数量。",
            func=lambda: str(tool_service.get_gold()),
            args_schema=NoInput,
        ),
        StructuredTool.from_function(
            name="get_event",
            description="识别事件名称、正文、选项，并按事件名称查询策略。",
            func=lambda: _jsonable(tool_service.get_event()),
            args_schema=NoInput,
        ),
        StructuredTool.from_function(
            name="get_card_reward_choices",
            description="识别战斗结束奖励卡牌名称，并按卡名查询费用和效果。",
            func=lambda: _jsonable(tool_service.get_card_reward_choices()),
            args_schema=NoInput,
        ),
        StructuredTool.from_function(
            name="get_boss_relic_choices",
            description="识别 Boss 遗物三选，并返回名称、效果和置信度。",
            func=lambda: _jsonable(tool_service.get_boss_relic_choices()),
            args_schema=NoInput,
        ),
        StructuredTool.from_function(
            name="get_shop",
            description="识别商店中的卡牌、遗物、药水、删牌价格和金币。",
            func=lambda: _jsonable(tool_service.get_shop()),
            args_schema=NoInput,
        ),
    ]
