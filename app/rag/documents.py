from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RagDocument:
    id: int
    name: str
    description: str
    effect: str = ""
    strategy: str = ""
    fenzu: int | None = None
    source: str = ""

    @property
    def searchable_text(self) -> str:
        return "\n".join(
            part
            for part in [self.name, self.description, self.effect, self.strategy]
            if part
        )

    @property
    def rerank_text(self) -> str:
        return f"名称：{self.name}\n描述：{self.description}\n效果：{self.effect}\n策略：{self.strategy}"
