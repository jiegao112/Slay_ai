"""
scripts/crawl_kapai.py

爬取杀戮尖塔卡牌数据（红色牌 / 绿色牌 / 蓝色牌 / 紫色牌 / 无色牌），写入 MySQL 表 kapai。

用法：
    python scripts/crawl_kapai.py            # 爬取并写入 MySQL
    python scripts/crawl_kapai.py --no-db    # 只爬取打印，不写数据库
    python scripts/crawl_kapai.py --db-check # 只检查 MySQL 连接

字段提取规则（每个 wikitable，tr[x] 从 1 递增到没有数据为止）：
    tr[x]/td[1]/a  -> name      卡牌名
    tr[x]/td[6]    -> cost      费用
    tr[x]/td[7]    -> xiaoguo   效果
    tr[x]/td[8]    -> cost+     升级费用
    tr[x]/td[9]    -> xiaoguo+  升级效果
每行产出两条记录：基础卡 (name, cost, xiaoguo) 与升级卡 (name+"+", cost+, xiaoguo+)。
例如某行提取为 “神话, 2, 在本场战斗中升级你的手牌, 1, 在本场战斗中升级你的手牌”：
    神话,  2, 在本场战斗中升级你的手牌
    神话+, 1, 在本场战斗中升级你的手牌

效果文本中的 tooltip span 会被展平为纯文本，例如：
    <td>在本场战斗中<span class="huiji-tt" ...><b>升级</b></span>你的所有牌。<br>
        <span class="huiji-tt" ...><b>消耗</b></span>。</td>
    会得到 “在本场战斗中升级你的所有牌。消耗。”

数据库连接与灰机 wiki 的 Cookie 均从 .env 读取（DB_* 与 HUIJI_COOKIE）。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pymysql
from curl_cffi import requests
from dotenv import load_dotenv
from lxml import html

BASE_URL = "https://sts.huijiwiki.com"
PAGES = (
    ("红色牌", f"{BASE_URL}/wiki/%E7%BA%A2%E8%89%B2%E7%89%8C"),
    ("绿色牌", f"{BASE_URL}/wiki/%E7%BB%BF%E8%89%B2%E7%89%8C"),
    ("蓝色牌", f"{BASE_URL}/wiki/%E8%93%9D%E8%89%B2%E7%89%8C"),
    ("紫色牌", f"{BASE_URL}/wiki/%E7%B4%AB%E8%89%B2%E7%89%8C"),
    ("无色牌", f"{BASE_URL}/wiki/%E6%97%A0%E8%89%B2%E7%89%8C"),
)

# 卡牌表格定位：优先题目给定的路径，找不到再按 wikitable 类名兜底
CARD_TABLE_XPATHS = (
    '//*[@id="mw-content-text"]/div/div[2]/div/div/table',
    '//*[@id="mw-content-text"]//table[contains(@class, "wikitable")]',
)

# 行内各列的 xpath（tr 相对路径），与题目一致
NAME_XPATH = "./td[1]/a"
COST_COL, XIAOGUO_COL = 6, 7          # 基础卡
COST_PLUS_COL, XIAOGUO_PLUS_COL = 8, 9  # 升级卡

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOTENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class KapaiRecord:
    name: str
    cost: str
    xiaoguo: str


def cell_text(node: html.HtmlElement | None) -> str:
    """提取单元格纯文本：展平嵌套元素（tooltip span、br 等），并去掉所有空白字符。

    例如 <td>在本场战斗中<span ...>升级</span>你的所有牌。<br><span ...>消耗</span>。</td>
    会得到 “在本场战斗中升级你的所有牌。消耗。”。
    """
    if node is None:
        return ""
    return re.sub(r"\s+", "", node.text_content())


def build_session(
    cookie: str | None = None,
    proxy: str | None = None,
    impersonate: str = "chrome120",
) -> requests.Session:
    # 注意：灰机 wiki 有 Cloudflare 防护，实测最新版 "chrome" 指纹会被质询（403 Just a moment），
    # "chrome120" 指纹可通过；若哪天也被拦，可用 --impersonate 换其他指纹。
    session = requests.Session(impersonate=impersonate)
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
    """抓取页面；遇到 Cloudflare 质询（403）时按 20s/45s 退避重试，仍失败则抛错。"""
    waits = (20, 45)
    for attempt in range(len(waits) + 1):
        response = session.get(url, timeout=timeout)
        if response.status_code != 403:
            response.raise_for_status()
            break
        if attempt < len(waits):
            print(f"[WARN] {url} 被站点暂时拦截（403），{waits[attempt]} 秒后重试（第 {attempt + 1} 次）……")
            time.sleep(waits[attempt])
        else:
            raise RuntimeError(f"访问被站点拒绝: {url}。请检查 HUIJI_COOKIE 是否过期，或稍后重试。")
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


def find_card_table(tree: html.HtmlElement) -> html.HtmlElement | None:
    for xpath in CARD_TABLE_XPATHS:
        node = xpath_first(tree, xpath)
        if node is not None:
            return node
    return None


def row_at(table: html.HtmlElement, x: int) -> html.HtmlElement | None:
    row = xpath_first(table, f"./tbody/tr[{x}]")
    return row


def cell_of(row: html.HtmlElement, col: int) -> html.HtmlElement | None:
    return xpath_first(row, f"./td[{col}]")


def extract_records_from_table(
    table: html.HtmlElement,
    page_label: str,
    limit: int = 0,
) -> list[KapaiRecord]:
    """tr[x] 从 1 递增，直到没有数据为止；跳过表头/空行（没有 td[1]/a 的行）。"""
    records: list[KapaiRecord] = []
    x = 1
    while True:
        row = row_at(table, x)
        if row is None:
            break

        name = cell_text(xpath_first(row, NAME_XPATH))
        if not name:  # 表头行（th）或空行
            x += 1
            continue

        cost = cell_text(cell_of(row, COST_COL))
        xiaoguo = cell_text(cell_of(row, XIAOGUO_COL))
        records.append(KapaiRecord(name=name, cost=cost, xiaoguo=xiaoguo))

        cost_plus = cell_text(cell_of(row, COST_PLUS_COL))
        xiaoguo_plus = cell_text(cell_of(row, XIAOGUO_PLUS_COL))
        if cost_plus or xiaoguo_plus:
            records.append(KapaiRecord(name=f"{name}+", cost=cost_plus, xiaoguo=xiaoguo_plus))
        else:
            print(f"[WARN] {page_label} tr[{x}] {name} 缺少升级费用/效果，未生成 {name}+ 记录。")

        if limit > 0 and len(records) >= limit:
            print(f"[INFO] {page_label}: 已达到 --limit {limit}，提前结束本页。")
            break
        x += 1

    return records


def crawl_all_pages(
    session: requests.Session,
    timeout: int,
    sleep_seconds: float,
    limit: int,
) -> list[KapaiRecord]:
    records: list[KapaiRecord] = []
    for index, (page_label, page_url) in enumerate(PAGES, start=1):
        print(f"[INFO] ({index}/{len(PAGES)}) 抓取页面 {page_label}: {page_url}")
        try:
            tree = fetch_tree(session, page_url, timeout)
        except (requests.exceptions.RequestException, RuntimeError) as exc:
            print(f"[ERROR] 页面抓取失败 {page_label}: {exc}")
            continue

        table = find_card_table(tree)
        if table is None:
            print(f"[WARN] {page_label}: 未找到卡牌表格，已跳过。")
            continue

        page_records = extract_records_from_table(table, page_label, limit)
        print(f"[INFO] {page_label}: 提取 {len(page_records)} 条记录。")
        records.extend(page_records)

        if sleep_seconds > 0 and index < len(PAGES):
            time.sleep(sleep_seconds)

    return records


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


def save_records_to_mysql(records: list[KapaiRecord]) -> None:
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
                CREATE TABLE IF NOT EXISTS kapai (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    cost VARCHAR(32),
                    xiaoguo TEXT
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            cursor.executemany(
                """
                INSERT INTO kapai (name, cost, xiaoguo)
                VALUES (%s, %s, %s)
                """,
                [(record.name, record.cost, record.xiaoguo) for record in records],
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

    parser = argparse.ArgumentParser(description="爬取杀戮尖塔卡牌数据并写入 MySQL 表 kapai。")
    parser.add_argument("--timeout", type=int, default=20, help="请求超时时间，单位秒。")
    parser.add_argument("--sleep", type=float, default=2.0, help="页面之间的等待秒数（灰机 wiki 有 Cloudflare 限流，不宜过快）。")
    parser.add_argument("--limit", type=int, default=0, help="每页只提取前 N 条记录；0 表示全量。")
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
    parser.add_argument(
        "--impersonate",
        default="chrome120",
        help="curl_cffi 的浏览器指纹；灰机 wiki 实测 chrome120 可通过，新版 chrome 指纹会被 Cloudflare 质询。",
    )
    parser.add_argument("--db-check", action="store_true", help="只检查 MySQL 连接并创建数据库，不爬取网页。")
    parser.add_argument("--no-db", action="store_true", help="只爬取打印，不写入 MySQL。")
    return parser.parse_args()


def main() -> None:
    # 强制 UTF-8 输出，避免 Windows 控制台按本地代码页（如 GBK）打印中文乱码
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    args = parse_args()

    if args.db_check:
        check_mysql_connection()
        print("[INFO] MySQL 连接成功。")
        return

    session = build_session(cookie=args.cookie, proxy=args.proxy, impersonate=args.impersonate)
    records = crawl_all_pages(session, args.timeout, args.sleep, args.limit)
    print(f"[INFO] 共生成 {len(records)} 条卡牌记录。")

    for record in records[:10]:
        print(f"       {record.name} | {record.cost} | {record.xiaoguo[:60]}")
    if len(records) > 10:
        print(f"       ... 其余 {len(records) - 10} 条省略。")

    if args.no_db:
        print("[INFO] 已跳过 MySQL 写入。")
        return

    save_records_to_mysql(records)
    print("[INFO] 已写入 MySQL 表 kapai。")


if __name__ == "__main__":
    main()
