from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pymysql

from app.core.config import Settings, get_settings
from app.rag.documents import RagDocument


def quote_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


class RagDatabase:
    """从 MySQL 读取药水、遗物、怪物资料。

    MySQL 连接信息全部来自 .env，兼容 DB_* 与 MYSQL_* 两套命名。
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @contextmanager
    def connection(self) -> Iterator[pymysql.Connection]:
        conn = pymysql.connect(
            host=self.settings.mysql_host,
            port=self.settings.mysql_port,
            user=self.settings.mysql_user,
            password=self.settings.mysql_password,
            database=self.settings.mysql_database,
            charset="utf8mb4",
            autocommit=True,
        )
        try:
            yield conn
        finally:
            conn.close()

    def load_documents(self, source: str) -> list[RagDocument]:
        if source not in {"yiwu", "yaoshui", "guaiwu"}:
            raise ValueError(f"未知 RAG 数据源：{source}")

        try:
            with self.connection() as conn:
                if source == "guaiwu":
                    return self._load_guaiwu(conn)
                return self._load_item_table(conn, source)
        except Exception:
            # 数据库不可用时交给上层用 FAISS id 降级。
            return []

    def _load_item_table(self, conn: pymysql.Connection, table: str) -> list[RagDocument]:
        sql = (
            f"SELECT id, name, COALESCE(miaoshu, ''), COALESCE(xiaoguo, '') "
            f"FROM {quote_identifier(table)}"
        )
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return [
                RagDocument(
                    id=int(row[0]),
                    name=str(row[1] or ""),
                    description=str(row[2] or ""),
                    effect=str(row[3] or ""),
                    source=table,
                )
                for row in cursor.fetchall()
            ]

    def _load_guaiwu(self, conn: pymysql.Connection) -> list[RagDocument]:
        sql = (
            "SELECT id, name, COALESCE(miaoshu, ''), COALESCE(celue, ''), fenzu "
            "FROM `guaiwu`"
        )
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return [
                RagDocument(
                    id=int(row[0]),
                    name=str(row[1] or ""),
                    description=str(row[2] or ""),
                    strategy=str(row[3] or ""),
                    fenzu=int(row[4]) if row[4] is not None else None,
                    source="guaiwu",
                )
                for row in cursor.fetchall()
            ]
