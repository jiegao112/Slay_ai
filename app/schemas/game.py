from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class SceneType(StrEnum):
    combat = "combat"
    campfire = "campfire"
    event = "event"
    card_reward = "card_reward"
    boss_relic = "boss_relic"
    start_event = "start_event"
    shop = "shop"
    unknown = "unknown"


class Region(BaseModel):
    left_top_x: int = 0
    left_top_y: int = 0
    right_bottom_x: int = 0
    right_bottom_y: int = 0

    @model_validator(mode="after")
    def validate_corners(self) -> "Region":
        if self.right_bottom_x <= self.left_top_x:
            raise ValueError("right_bottom_x 必须大于 left_top_x")
        if self.right_bottom_y <= self.left_top_y:
            raise ValueError("right_bottom_y 必须大于 left_top_y")
        return self

    @property
    def width(self) -> int:
        return self.right_bottom_x - self.left_top_x

    @property
    def height(self) -> int:
        return self.right_bottom_y - self.left_top_y


class RegionConfig(BaseModel):
    regions: dict[str, Region] = Field(default_factory=dict)
    actions: dict[str, int | float] = Field(default_factory=dict)


class CardCollection(BaseModel):
    kapai_num: dict[str, int] = Field(default_factory=dict)
    kapai_miaoshu: dict[str, str] = Field(default_factory=dict)
    kapai_cost: dict[str, str] = Field(default_factory=dict)

    def merge(self, other: "CardCollection") -> "CardCollection":
        """合并卡牌识别结果：数量累加，效果和费用只保留第一次出现的版本。"""
        merged_num = dict(self.kapai_num)
        merged_desc = dict(self.kapai_miaoshu)
        merged_cost = dict(self.kapai_cost)

        for name, count in other.kapai_num.items():
            merged_num[name] = merged_num.get(name, 0) + count
        for name, desc in other.kapai_miaoshu.items():
            merged_desc.setdefault(name, desc)
        for name, cost in other.kapai_cost.items():
            merged_cost.setdefault(name, cost)

        return CardCollection(kapai_num=merged_num, kapai_miaoshu=merged_desc, kapai_cost=merged_cost)


class RagHit(BaseModel):
    id: int | None = None
    name: str = ""
    description: str = ""
    effect: str = ""
    strategy: str = ""
    fenzu: int | None = None
    source: str = ""
    score: float = 0.0
    confidence: float = 0.0


class PotionInfo(BaseModel):
    raw_description: str
    name: str = ""
    effect: str = ""
    confidence: float = 0.0


class RelicInfo(BaseModel):
    raw_description: str
    name: str = ""
    effect: str = ""
    cengshu: int = -1
    confidence: float = 0.0


class MonsterInfo(BaseModel):
    raw_description: str
    hp: str = ""
    shanghai: str = ""
    name: str = ""
    strategy: str = ""
    fenzu: int | None = None
    confidence: float = 0.0


class EventInfo(BaseModel):
    name: str = ""
    text: str = ""
    xuanxiang: list[str] = Field(default_factory=list)
    strategy: str = ""


class CardInfo(BaseModel):
    name: str
    cost: str = ""
    effect: str = ""


class ShopCardInfo(BaseModel):
    name: str
    price: int
    cost: str = ""
    effect: str = ""


class ShopInfo(BaseModel):
    cards: list[ShopCardInfo] = Field(default_factory=list)
    relics: list[RelicInfo] = Field(default_factory=list)
    potions: list[PotionInfo] = Field(default_factory=list)
    remove_card_price: int | None = None
    gold: int | None = None


class GameState(BaseModel):
    scene: SceneType = SceneType.unknown
    deck: CardCollection | None = None
    hand: CardCollection | None = None
    card_reward_choices: list[CardInfo] = Field(default_factory=list)
    potions: list[PotionInfo] = Field(default_factory=list)
    relics: list[RelicInfo] = Field(default_factory=list)
    boss_relic_choices: list[RelicInfo] = Field(default_factory=list)
    monsters: list[MonsterInfo] = Field(default_factory=list)
    event: EventInfo | None = None
    shop: ShopInfo | None = None
    player_hp: str | None = None
    energy: str | None = None
    gold: int | None = None
    screen: dict[str, Any] = Field(default_factory=dict)
    uncertainties: list[str] = Field(default_factory=list)
