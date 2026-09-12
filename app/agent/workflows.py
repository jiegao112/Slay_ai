from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.core.cancellation import AnalysisCancelled
from app.schemas.game import GameState, SceneType
from app.tools.game_tools import GameToolService


logger = logging.getLogger(__name__)
StatusCallback = Callable[[str], None]


class WorkflowRunner:
    """按界面执行基础采集流程。

    第一版采用“固定工作流 + Agent 可补查工具”的混合方案，避免 Agent 乱操作 UI。
    """

    def __init__(self, tools: GameToolService | None = None):
        self.tools = tools or GameToolService()

    def collect(
        self,
        scene: SceneType,
        status: StatusCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> GameState:
        logger.info("开始采集局面：scene=%s", scene.value)
        state = GameState(scene=scene)
        emit = status or (lambda _: None)
        self.tools.cancel_event = cancel_event

        if scene == SceneType.combat:
            self._combat(state, emit, cancel_event)
        elif scene == SceneType.campfire:
            self._campfire(state, emit, cancel_event)
        elif scene == SceneType.event:
            self._event(state, emit, cancel_event)
        elif scene == SceneType.card_reward:
            self._card_reward(state, emit, cancel_event)
        elif scene == SceneType.boss_relic:
            self._boss_relic(state, emit, cancel_event)
        elif scene == SceneType.start_event:
            self._start_event(state, emit, cancel_event)
        elif scene == SceneType.shop:
            self._shop(state, emit, cancel_event)
        else:
            self._unknown(state, emit, cancel_event)

        self._mark_low_confidence(state)
        logger.info("局面采集完成：scene=%s", scene.value)
        return state

    def _combat(self, state: GameState, emit: StatusCallback, cancel_event: threading.Event | None) -> None:
        self._check_cancel(cancel_event)
        emit("我先看看你手里能打什么")
        logger.info("战斗流程：识别手牌")
        state.hand = self.tools.get_hand()
        self._check_cancel(cancel_event)
        emit("再确认一下这回合有多少能量")
        logger.info("战斗流程：识别能量")
        state.energy = self.tools.get_energy()
        self._check_cancel(cancel_event)
        emit("再看看对面准备怎么为难你")
        logger.info("战斗流程：识别怪物")
        state.monsters = self.tools.get_monsters()
        self._check_cancel(cancel_event)
        emit("顺手确认一下血量和资源")
        logger.info("战斗流程：识别血量/药水/遗物")
        state.player_hp = self.tools.get_player_hp()
        self._check_cancel(cancel_event)
        state.potions = self.tools.get_potions()
        self._check_cancel(cancel_event)
        state.relics = self.tools.get_relics()

    def _campfire(self, state: GameState, emit: StatusCallback, cancel_event: threading.Event | None) -> None:
        self._check_cancel(cancel_event)
        emit("先不要动嗷，我看看你有什么牌")
        state.deck = self.tools.get_deck()
        self._check_cancel(cancel_event)
        emit("再看看遗物和血量，判断休息还是升级")
        state.relics = self.tools.get_relics()
        self._check_cancel(cancel_event)
        state.player_hp = self.tools.get_player_hp()

    def _event(self, state: GameState, emit: StatusCallback, cancel_event: threading.Event | None) -> None:
        self._check_cancel(cancel_event)
        emit("我先读一下事件内容和选项")
        state.event = self.tools.get_event()
        self._check_cancel(cancel_event)
        emit("再确认血量、金币和遗物，事件选择通常要看代价")
        state.player_hp = self.tools.get_player_hp()
        self._check_cancel(cancel_event)
        state.gold = self.tools.get_gold()
        self._check_cancel(cancel_event)
        state.relics = self.tools.get_relics()

    def _card_reward(self, state: GameState, emit: StatusCallback, cancel_event: threading.Event | None) -> None:
        self._check_cancel(cancel_event)
        emit("我先看看这次奖励给了哪几张牌")
        state.card_reward_choices = self.tools.get_card_reward_choices()
        self._check_cancel(cancel_event)
        emit("先不要动嗷，我看看你有什么牌")
        state.deck = self.tools.get_deck()
        self._check_cancel(cancel_event)
        emit("再看遗物，选牌要看已有配合")
        state.relics = self.tools.get_relics()

    def _boss_relic(self, state: GameState, emit: StatusCallback, cancel_event: threading.Event | None) -> None:
        self._check_cancel(cancel_event)
        emit("Boss 遗物要看牌组，我先翻一下牌库")
        state.deck = self.tools.get_deck()
        self._check_cancel(cancel_event)
        emit("再看看这三个 Boss 遗物各是什么")
        state.boss_relic_choices = self.tools.get_boss_relic_choices()
        self._check_cancel(cancel_event)
        emit("最后看已有遗物，判断副作用能不能接受")
        state.relics = self.tools.get_relics()

    def _start_event(self, state: GameState, emit: StatusCallback, cancel_event: threading.Event | None) -> None:
        self._check_cancel(cancel_event)
        emit("我先确认开局界面和当前血量")
        state.player_hp = self.tools.get_player_hp()
        state.uncertainties.append("第一版尚未单独识别涅奥奖励选项，需要后续增加初始事件选项区域。")

    def _shop(self, state: GameState, emit: StatusCallback, cancel_event: threading.Event | None) -> None:
        self._check_cancel(cancel_event)
        emit("商店先看牌组，判断缺什么")
        state.deck = self.tools.get_deck()
        self._check_cancel(cancel_event)
        emit("再识别商品、删牌价格和金币")
        state.shop = self.tools.get_shop()
        state.gold = state.shop.gold
        self._check_cancel(cancel_event)
        emit("最后看已有遗物，避免买重复或冲突的东西")
        state.relics = self.tools.get_relics()

    def _unknown(self, state: GameState, emit: StatusCallback, cancel_event: threading.Event | None) -> None:
        self._check_cancel(cancel_event)
        emit("我不太确定当前界面，先做保守的信息采集")
        state.player_hp = self.tools.get_player_hp()
        self._check_cancel(cancel_event)
        state.relics = self.tools.get_relics()

    @staticmethod
    def _check_cancel(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise AnalysisCancelled("用户已关闭本次分析。")

    @staticmethod
    def _mark_low_confidence(state: GameState) -> None:
        for potion in state.potions:
            if potion.confidence < 0.2:
                state.uncertainties.append(f"药水识别置信度较低：{potion.raw_description}")
        for relic in state.relics:
            if relic.confidence < 0.2:
                state.uncertainties.append(f"遗物识别置信度较低：{relic.raw_description}")
        for monster in state.monsters:
            if monster.confidence < 0.2:
                state.uncertainties.append(f"怪物识别置信度较低：{monster.raw_description}")
