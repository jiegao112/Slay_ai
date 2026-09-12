from __future__ import annotations

import argparse
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, unquote, urljoin, urlparse

import pymysql
from curl_cffi import requests
from dotenv import load_dotenv
from lxml import html


BASE_URL = "https://sts.huijiwiki.com"
COLLECTION_URL = f"{BASE_URL}/wiki/%E9%81%97%E7%89%A9%E6%94%B6%E8%97%8F"

# a ranges from 1 to 8. The source request wrote "36.34"; this is treated as
# two values so the list aligns with the eight table indexes.
BMAX_BY_A = [4, 36, 36, 34, 30, 18, 20, 1]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIR = PROJECT_ROOT / "img" / "yiwu"
DOTENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class YiwuRecord:
    name: str
    xiaoguo: str
    image_path: Path | None


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def safe_filename(name: str, fallback: str = "yiwu") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned or fallback


def image_extension(image_url: str, content_type: str | None) -> str:
    suffix = Path(unquote(urlparse(image_url).path)).suffix
    if suffix:
        return suffix

    if content_type:
        media_type = content_type.split(";", 1)[0].strip().lower()
        return {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/svg+xml": ".svg",
        }.get(media_type, "")

    return ""


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
        session.proxies = {
            "http": proxy,
            "https": proxy,
        }
    return session


def fetch_tree(session: requests.Session, url: str, timeout: int) -> html.HtmlElement:
    response = session.get(url, timeout=timeout)
    if response.status_code == 403:
        raise RuntimeError(
            f"访问被站点拒绝: {url}。灰机 wiki 可能限制了当前网络或非浏览器客户端。"
        )
    response.raise_for_status()
    response.encoding = getattr(response, "apparent_encoding", None) or getattr(
        response, "charset_encoding", None
    ) or response.encoding
    return html.fromstring(response.text)


def xpath_first(tree: html.HtmlElement, xpath: str) -> html.HtmlElement | None:
    nodes = tree.xpath(xpath)
    if not nodes and "/tbody/" in xpath:
        nodes = tree.xpath(xpath.replace("/tbody/", "/"))
    return nodes[0] if nodes else None


def extract_yiwu_names(session: requests.Session, timeout: int) -> list[str]:
    tree = fetch_tree(session, COLLECTION_URL, timeout)
    yiwu: list[str] = []
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
                print(f"[WARN] 未找到遗物名称: a={a_index}, b={b_index}")
                continue

            name = clean_text(node.text_content())
            if not name:
                print(f"[WARN] 遗物名称为空: a={a_index}, b={b_index}")
                continue

            if name not in seen:
                yiwu.append(name)
                seen.add(name)

    return yiwu


def extract_xiaoguo(tree: html.HtmlElement) -> str:
    node = xpath_first(tree, '//*[@id="mw-content-text"]/div/table[1]/tbody/tr[5]/td')
    if node is None:
        return ""
    return clean_text(node.text_content())


def extract_image_url(tree: html.HtmlElement, page_url: str) -> str | None:
    node = xpath_first(tree, '//*[@id="mw-content-text"]/div/table[1]/tbody/tr[3]/td/a/img')
    if node is None:
        return None

    raw_url = (
        node.get("src")
        or node.get("data-src")
        or node.get("data-original")
    )
    if not raw_url:
        return None

    if raw_url.startswith("//"):
        raw_url = f"https:{raw_url}"

    return urljoin(page_url, raw_url)


def download_image(
    session: requests.Session,
    image_url: str,
    name: str,
    timeout: int,
) -> Path:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    response = session.get(image_url, timeout=timeout)
    response.raise_for_status()

    extension = image_extension(image_url, response.headers.get("Content-Type"))
    image_path = IMAGE_DIR / f"{safe_filename(name)}{extension}"
    image_path.write_bytes(response.content)
    return image_path


def crawl_yiwu_records(
    session: requests.Session,
    yiwu: Iterable[str],
    timeout: int,
    sleep_seconds: float,
) -> list[YiwuRecord]:
    records: list[YiwuRecord] = []

    for index, name in enumerate(yiwu, start=1):
        page_url = f"{BASE_URL}/wiki/{quote(name)}"
        print(f"[INFO] ({index}) 抓取 {name}: {page_url}")

        try:
            tree = fetch_tree(session, page_url, timeout)
            xiaoguo = extract_xiaoguo(tree)
            image_url = extract_image_url(tree, page_url)
            image_path = None
            if image_url:
                try:
                    image_path = download_image(session, image_url, name, timeout)
                except requests.exceptions.RequestException as exc:
                    print(f"[WARN] 图片下载失败 {name}: {exc}")
            else:
                print(f"[WARN] 未找到图片: {name}")
        except (requests.exceptions.RequestException, RuntimeError) as exc:
            print(f"[ERROR] 抓取失败 {name}: {exc}")
            records.append(YiwuRecord(name=name, xiaoguo="", image_path=None))
            continue

        records.append(YiwuRecord(name=name, xiaoguo=xiaoguo, image_path=image_path))

        if sleep_seconds > 0:
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


def save_records_to_mysql(records: list[YiwuRecord]) -> None:
    connection = pymysql.connect(**mysql_config_from_env())
    database = quote_mysql_identifier(
        os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "slay_ai")
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS {database} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cursor.execute(f"USE {database}")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS yiwu (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    xiaoguo TEXT,
                    miaoshu TEXT
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            cursor.executemany(
                """
                INSERT INTO yiwu (name, xiaoguo, miaoshu)
                VALUES (%s, %s, %s)
                """,
                [(record.name, record.xiaoguo, "") for record in records],
            )
    finally:
        connection.close()


def check_mysql_connection() -> None:
    connection = pymysql.connect(**mysql_config_from_env())
    database = quote_mysql_identifier(
        os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "slay_ai")
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS {database} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cursor.execute(f"USE {database}")
            cursor.execute("SELECT 1")
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    load_dotenv(DOTENV_PATH)

    parser = argparse.ArgumentParser(description="爬取杀戮尖塔遗物数据并写入 MySQL。")
    parser.add_argument("--timeout", type=int, default=20, help="请求超时时间，单位秒。")
    parser.add_argument("--sleep", type=float, default=0.2, help="每个详情页之间的等待秒数。")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个遗物；0 表示全量。")
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
    parser.add_argument("--no-db", action="store_true", help="只爬取和下载图片，不写入 MySQL。")
    return parser.parse_args()


def main() -> None:

    args = parse_args()

    if args.db_check:
        check_mysql_connection()
        print("[INFO] MySQL 连接成功。")
        return

    session = build_session(cookie=args.cookie, proxy=args.proxy)

    try:
        yiwu = extract_yiwu_names(session, args.timeout)
    except (requests.exceptions.RequestException, RuntimeError) as exc:
        raise SystemExit(f"[ERROR] 遗物收藏页抓取失败: {exc}") from exc

    if args.limit > 0:
        yiwu = yiwu[: args.limit]

    print(f"[INFO] 共抓取到 {len(yiwu)} 个遗物名称。")

    records = crawl_yiwu_records(session, yiwu, args.timeout, args.sleep)
    print(f"[INFO] 共生成 {len(records)} 条记录。")

    if args.no_db:
        print("[INFO] 已跳过 MySQL 写入。")
        return

    save_records_to_mysql(records)
    print("[INFO] 已写入 MySQL 表 yiwu。")


if __name__ == "__main__":
    main()
