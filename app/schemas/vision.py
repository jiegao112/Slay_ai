from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.game import CardCollection, SceneType


class SceneRecognition(BaseModel):
    scene: SceneType = SceneType.unknown
    reason: str = ""


class DeckCountRecognition(BaseModel):
    count: int = Field(ge=0)


class CardCollectionRecognition(CardCollection):
    pass


class PotionRecognition(BaseModel):
    items: list[str] = Field(default_factory=list)


class RelicRecognition(BaseModel):
    miaoshu: str = "空"
    cengshu: int = -1


class MonsterRecognitionItem(BaseModel):
    miaoshu: str
    hp: str = ""
    shanghai: str = ""


class MonsterRecognition(BaseModel):
    monsters: list[MonsterRecognitionItem] = Field(default_factory=list)


class PlayerHpRecognition(BaseModel):
    hp: str = ""


class EnergyRecognition(BaseModel):
    energy: str = ""


class EventRecognition(BaseModel):
    name: str = ""
    text: str = ""
    xuanxiang: list[str] = Field(default_factory=list)


class GoldRecognition(BaseModel):
    gold: int = Field(ge=0)


class ShopCardsRecognition(BaseModel):
    cards: dict[str, int] = Field(default_factory=dict)


class CardRewardRecognition(BaseModel):
    cards: list[str] = Field(default_factory=list)
