from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from io import BytesIO

import pyautogui
from PIL import Image

from app.schemas.game import Region
from app.vision.regions import RegionStore, get_region_store


@dataclass(frozen=True)
class Screenshot:
    region: Region
    data_url: str


class ScreenController:
    """负责截图和键鼠输入。

    这里故意只做很薄的一层封装，避免业务工具里直接散落 pyautogui 调用。
    """

    def __init__(self, regions: RegionStore | None = None):
        self.regions = regions or get_region_store()
        pyautogui.PAUSE = float(self.regions.action("keyboard_pause_seconds", 0.1))

    def screenshot_region(self, name: str, offset_x: int = 0, offset_y: int = 0) -> Screenshot:
        region = self.regions.region(name, offset_x=offset_x, offset_y=offset_y)
        time.sleep(float(self.regions.action("screenshot_pause_seconds", 0.1)))
        image = pyautogui.screenshot(
            region=(region.left_top_x, region.left_top_y, region.width, region.height)
        )
        return Screenshot(region=region, data_url=self._to_data_url(image))

    def press(self, key: str) -> None:
        pyautogui.press(key)

    def scroll(self, clicks: int) -> None:
        pyautogui.scroll(clicks)

    @staticmethod
    def _to_data_url(image: Image.Image) -> str:
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        payload = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{payload}"


def get_screen_controller() -> ScreenController:
    return ScreenController()
