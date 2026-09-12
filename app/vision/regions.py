from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.core.paths import REGIONS_PATH
from app.schemas.game import Region, RegionConfig


class RegionStore:
    """读取截图区域和键鼠动作配置。

    你之后主要改 config/regions.json，不需要改工具代码。
    """

    def __init__(self, path: Path = REGIONS_PATH):
        self.path = path
        self.config = self._load()

    def _load(self) -> RegionConfig:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return RegionConfig.model_validate(raw)

    def region(self, name: str, offset_x: int = 0, offset_y: int = 0) -> Region:
        base = self.config.regions.get(name)
        if base is None:
            raise KeyError(f"未配置截图区域：{name}")
        return Region(
            left_top_x=base.left_top_x + offset_x,
            left_top_y=base.left_top_y + offset_y,
            right_bottom_x=base.right_bottom_x + offset_x,
            right_bottom_y=base.right_bottom_y + offset_y,
        )

    def action(self, name: str, default: int | float = 0) -> int | float:
        return self.config.actions.get(name, default)


@lru_cache(maxsize=1)
def get_region_store() -> RegionStore:
    return RegionStore()
