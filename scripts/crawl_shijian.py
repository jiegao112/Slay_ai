from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path

import pymysql
from curl_cffi import requests
from dotenv import load_dotenv
from lxml import html


BASE_URL = "https://sts.huijiwiki.com"
EVENT_LOG_URL = f"{BASE_URL}/wiki/%E4%BA%8B%E4%BB%B6%E6%97%A5%E5%BF%97"
BMAX_BY_A = [11, 17, 9, 15]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOTENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class ShijianRecord:
    name: str
    celue: str = ""


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def build_session(cookie: str | None = None, proxy: str | None = None) -> requests.Session:
    session = requests.Session(impersonate="chrome")
    session.headers.update(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": BASE_URL,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
    )
    if cookie:
        session.headers["Cookie"] = cookie
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def fetch_tree(session: requests.Session, url: str, timeout: int) -> html.HtmlElement:
    response = session.get(url, timeout=timeout)
    if response.status_code == 403:
        raise RuntimeError(f"访问被站点拒绝: {url}。请检查 HUIJI_COOKIE 是否有效。")
    response.raise_for_status()
    response.encoding = (
        getattr(response, "apparent_encoding", None)
        or getattr(response, "charset_encoding", None)
        or response.encoding
    )
    return html.fromstring(response.text)


def xpath_first(tree: html.HtmlElement, xpath: str) -> html.HtmlElement | None:
    nodes = tree.xpath(xpath)
    if not nodes and "/tbody/" in xpath:
        nodes = tree.xpath(xpath.replace("/tbody/", "/"))
    return nodes[0] if nodes else None


def extract_shijian_names(session: requests.Session, timeout: int) -> list[str]:
    tree = fetch_tree(session, EVENT_LOG_URL, timeout)
    shijian: list[str] = []
    seen: set[str] = set()

    for a_index, bmax in enumerate(BMAX_BY_A, start=1):
        for b_index in range(1, bmax + 1):
            xpath = (
                '//*[@id="mw-content-text"]/div/'
                f"table[{a_index}]/tbody/tr/td/table/tbody/tr[3]/td/"
                f"div/div[{b_index}]/div/a/span"
            )
            node = xpath_first(tree, xpath)
            if node is None:
                print(f"[WARN] 未找到事件名称: a={a_index}, b={b_index}")
                continue

            name = clean_text(node.text_content())
            if not name:
                print(f"[WARN] 事件名称为空: a={a_index}, b={b_index}")
                continue

            if name not in seen:
                shijian.append(name)
                seen.add(name)

    return shijian


def mysql_config_from_env() -> dict[str, object]:
    return {
        "host": os.getenv("DB_HOST") or os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT") or os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("DB_USER") or os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("DB_PASSWORD") or os.getenv("MYSQL_PASSWORD", ""),
        "charset": "utf8mb4",
        "autocommit": True,
    }


def quote_mysql_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


def database_name() -> str:
    return quote_mysql_identifier(os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "slay_ai"))


def save_records_to_mysql(records: list[ShijianRecord]) -> None:
    connection = pymysql.connect(**mysql_config_from_env())
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS {database_name()} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cursor.execute(f"USE {database_name()}")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS shijian (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    celue TEXT
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            cursor.executemany(
                """
                INSERT INTO shijian (name, celue)
                VALUES (%s, %s)
                """,
                [(record.name, record.celue) for record in records],
            )
    finally:
        connection.close()


def check_mysql_connection() -> None:
    connection = pymysql.connect(**mysql_config_from_env())
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS {database_name()} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cursor.execute(f"USE {database_name()}")
            cursor.execute("SELECT 1")
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    load_dotenv(DOTENV_PATH)

    parser = argparse.ArgumentParser(description="爬取杀戮尖塔事件名称并写入 MySQL。")
    parser.add_argument("--timeout", type=int, default=20, help="请求超时时间，单位秒。")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个事件；0 表示全量。")
    parser.add_argument(
        "--cookie",
        default=os.getenv("HUIJI_COOKIE"),
        help="灰机 wiki 浏览器 Cookie；也可用环境变量 HUIJI_COOKIE。",
    )
    parser.add_argument(
        "--proxy",
        default=os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY"),
        help="HTTP/HTTPS 代理地址，例如 http://127.0.0.1:7890。",
    )
    parser.add_argument("--db-check", action="store_true", help="只检查 MySQL 连接并创建数据库，不爬取网页。")
    parser.add_argument("--no-db", action="store_true", help="只爬取事件名称，不写入 MySQL。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.db_check:
        check_mysql_connection()
        print("[INFO] MySQL 连接成功。")
        return

    session = build_session(cookie=args.cookie, proxy=args.proxy)

    try:
        names = extract_shijian_names(session, args.timeout)
    except (requests.exceptions.RequestException, RuntimeError) as exc:
        raise SystemExit(f"[ERROR] 事件日志页抓取失败: {exc}") from exc

    if args.limit > 0:
        names = names[: args.limit]

    records = [ShijianRecord(name=name) for name in names]
    print(f"[INFO] 共抓取到 {len(records)} 个事件名称。")

    if args.no_db:
        print("[INFO] 已跳过 MySQL 写入。")
        return

    save_records_to_mysql(records)
    print("[INFO] 已写入 MySQL 表 shijian。")


if __name__ == "__main__":
    main()
