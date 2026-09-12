from __future__ import annotations

from dataclasses import dataclass

from app.rag.database import RagDatabase


@dataclass(frozen=True)
class CardRecord:
    name: str
    cost: str = ""
    effect: str = ""


class GameDataRepository:
    """按名称直接查询游戏数据库。

    这类信息不需要 RAG：卡牌、事件已经有确定名称，直接按 name 查表最稳。
    """

    def __init__(self, db: RagDatabase | None = None):
        self.db = db or RagDatabase()

    def get_card(self, name: str) -> CardRecord | None:
        try:
            with self.db.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT name, COALESCE(cost, ''), COALESCE(xiaoguo, '') "
                        "FROM `kapai` WHERE name = %s LIMIT 1",
                        (name,),
                    )
                    row = cursor.fetchone()
        except Exception:
            return None

        if row is None:
            return None
        return CardRecord(name=str(row[0] or name), cost=str(row[1] or ""), effect=str(row[2] or ""))

    def get_event_strategy(self, name: str) -> str:
        try:
            with self.db.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT COALESCE(celue, '') FROM `shijian` WHERE name = %s LIMIT 1",
                        (name,),
                    )
                    row = cursor.fetchone()
        except Exception:
            return ""

        return str(row[0] or "") if row else ""

