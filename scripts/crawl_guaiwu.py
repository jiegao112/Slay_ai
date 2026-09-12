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
COLLECTION_URL = f"{BASE_URL}/wiki/%E6%95%8C%E4%BA%BA%E5%9B%BE%E9%89%B4"
TR_INDEXES = [3, 5, 7]
C_MAX_BY_A_AND_B = [
    [9, 3, 3],
    [9, 3, 3],
    [7, 3, 3],
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIR = PROJECT_ROOT / "img" / "guaiwu"
DOTENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class GuaiwuRecord:
    name: str
    miaoshu: str
    celue: str
    fenzu: int
    image_path: Path | None


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def safe_filename(name: str, fallback: str = "guaiwu") -> str:
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
        session.proxies = {"http": proxy, "https": proxy}
    return session


def fetch_tree(session: requests.Session, url: str, timeout: int) -> html.HtmlElement:
    response = session.get(url, timeout=timeout)
    if response.status_code == 403:
        raise RuntimeError(f"Access denied by site: {url}. Check HUIJI_COOKIE.")
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


def extract_guaiwu_pages(session: requests.Session, timeout: int) -> list[str]:
    tree = fetch_tree(session, COLLECTION_URL, timeout)
    guaiwu: list[str] = []
    seen: set[str] = set()

    for a_index, cmax_values in enumerate(C_MAX_BY_A_AND_B, start=1):
        for b_position, b_index in enumerate(TR_INDEXES):
            cmax = cmax_values[b_position]
            for c_index in range(1, cmax + 1):
                xpath = (
                    '//*[@id="mw-content-text"]/div/'
                    f"table[{a_index}]/tbody/tr/td/table/tbody/tr[{b_index}]/td/"
                    f"div/div[{c_index}]/div/a"
                )
                node = xpath_first(tree, xpath)
                if node is None:
                    print(f"[WARN] Missing enemy page link: a={a_index}, b={b_index}, c={c_index}")
                    continue

                name = clean_text(node.text_content())
                if not name:
                    print(f"[WARN] Empty enemy page link: a={a_index}, b={b_index}, c={c_index}")
                    continue

                if name not in seen:
                    guaiwu.append(name)
                    seen.add(name)

    return guaiwu


def split_monster_id(raw_name: str) -> str | None:
    chinese_colon = "\uff1a"
    delimiter = ":" if ":" in raw_name else chinese_colon if chinese_colon in raw_name else None
    if delimiter is None:
        return None

    key, value = raw_name.split(delimiter, 1)
    if clean_text(key) != "Monster id":
        return None

    name = clean_text(value)
    return name or None


def extract_table_first_row(tree: html.HtmlElement, table_index: int) -> html.HtmlElement | None:
    xpath = f'//*[@id="mw-content-text"]/div/table[{table_index}]/tbody/tr[1]'
    return xpath_first(tree, xpath)


def extract_first_td(row_node: html.HtmlElement) -> html.HtmlElement | None:
    nodes = row_node.xpath("./td")
    return nodes[0] if nodes else None


def extract_image_url(tree: html.HtmlElement, page_url: str, table_index: int) -> str | None:
    xpath = f'//*[@id="mw-content-text"]/div/table[{table_index}]/tbody/tr[2]/td/img'
    node = xpath_first(tree, xpath)
    if node is None:
        fallback_xpath = f'//*[@id="mw-content-text"]/div/table[{table_index}]/tbody/tr[2]/td//img'
        node = xpath_first(tree, fallback_xpath)
    if node is None:
        return None

    raw_url = node.get("src") or node.get("data-src") or node.get("data-original")
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


def crawl_group_records(
    session: requests.Session,
    page_name: str,
    fenzu: int,
    timeout: int,
) -> list[GuaiwuRecord]:
    page_url = f"{BASE_URL}/wiki/{quote(page_name)}"
    tree = fetch_tree(session, page_url, timeout)
    records: list[GuaiwuRecord] = []
    table_index = 1

    while True:
        row1_node = extract_table_first_row(tree, table_index)
        if row1_node is None:
            print(f"[INFO] Finished page {page_name}: table[{table_index}] not found.")
            break

        td_node = extract_first_td(row1_node)
        if td_node is None:
            print(f"[INFO] Skip page {page_name} table[{table_index}]: tr[1] has no td")
            table_index += 1
            continue

        raw_header = clean_text(td_node.text_content())
        name = split_monster_id(raw_header)
        if name is None:
            print(f"[INFO] Skip page {page_name} table[{table_index}]: {raw_header}")
            table_index += 1
            continue

        image_url = extract_image_url(tree, page_url, table_index)
        image_path = None
        if image_url:
            try:
                image_path = download_image(session, image_url, name, timeout)
            except requests.exceptions.RequestException as exc:
                print(f"[WARN] Image download failed for {name}: {exc}")
        else:
            print(f"[WARN] Missing image for {name}")

        records.append(
            GuaiwuRecord(
                name=name,
                miaoshu="",
                celue="",
                fenzu=fenzu,
                image_path=image_path,
            )
        )
        print(f"[INFO] Added monster from page {page_name} table[{table_index}]: {name}")
        table_index += 1

    return records


def crawl_guaiwu_records(
    session: requests.Session,
    guaiwu: Iterable[str],
    timeout: int,
    sleep_seconds: float,
) -> list[GuaiwuRecord]:
    records: list[GuaiwuRecord] = []

    for page_index, page_name in enumerate(guaiwu, start=1):
        fenzu = page_index
        print(f"[INFO] Crawling enemy group page[{page_index}], fenzu={fenzu}: {page_name}")
        try:
            group_records = crawl_group_records(session, page_name, fenzu, timeout)
        except (requests.exceptions.RequestException, RuntimeError) as exc:
            print(f"[ERROR] Failed enemy group {page_name}: {exc}")
            continue

        if not group_records:
            print(f"[WARN] No Monster id rows found on page: {page_name}")
        records.extend(group_records)

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


def database_name() -> str:
    return quote_mysql_identifier(os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "slay_ai"))


def save_records_to_mysql(records: list[GuaiwuRecord]) -> None:
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
                CREATE TABLE IF NOT EXISTS guaiwu (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    miaoshu TEXT,
                    celue TEXT,
                    fenzu INT NOT NULL
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """
            )
            cursor.executemany(
                """
                INSERT INTO guaiwu (name, miaoshu, celue, fenzu)
                VALUES (%s, %s, %s, %s)
                """,
                [(record.name, record.miaoshu, record.celue, record.fenzu) for record in records],
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

    parser = argparse.ArgumentParser(description="Crawl Slay the Spire enemy data into MySQL.")
    parser.add_argument("--timeout", type=int, default=20, help="Request timeout in seconds.")
    parser.add_argument("--sleep", type=float, default=0.2, help="Delay between enemy group pages.")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N enemy group pages; 0 means all.")
    parser.add_argument("--cookie", default=os.getenv("HUIJI_COOKIE"), help="Huiji wiki Cookie or HUIJI_COOKIE env var.")
    parser.add_argument(
        "--proxy",
        default=os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY"),
        help="HTTP/HTTPS proxy, for example http://127.0.0.1:7890.",
    )
    parser.add_argument("--db-check", action="store_true", help="Only check MySQL connection.")
    parser.add_argument("--no-db", action="store_true", help="Crawl and download images without writing MySQL.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.db_check:
        check_mysql_connection()
        print("[INFO] MySQL connection OK.")
        return

    session = build_session(cookie=args.cookie, proxy=args.proxy)

    try:
        guaiwu = extract_guaiwu_pages(session, args.timeout)
    except (requests.exceptions.RequestException, RuntimeError) as exc:
        raise SystemExit(f"[ERROR] Enemy atlas crawl failed: {exc}") from exc

    if args.limit > 0:
        guaiwu = guaiwu[: args.limit]

    print(f"[INFO] Found {len(guaiwu)} enemy group pages.")

    records = crawl_guaiwu_records(session, guaiwu, args.timeout, args.sleep)
    print(f"[INFO] Built {len(records)} monster records.")

    if args.no_db:
        print("[INFO] Skipped MySQL write.")
        return

    save_records_to_mysql(records)
    print("[INFO] Wrote MySQL table guaiwu.")


if __name__ == "__main__":
    main()
