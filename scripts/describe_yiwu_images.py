from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import re
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOTENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_IMAGE_DIR = PROJECT_ROOT / "img" / "yiwu"
DEFAULT_MODEL = "qwen3.5-flash"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

PROMPT = """请只描述图片中的主体，不要描述背景、边框、透明底、网页压缩痕迹或无关装饰。
要求：
1. 用中文输出。
2. 描述主体的外观、颜色、形状、材质感和显著特征。
3. 输出一段 30 到 80 字的客观描述。"""


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


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


def image_name_from_path(image_path: Path) -> str:
    return image_path.stem


def image_to_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    image_base64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{image_base64}"


def iter_image_paths(image_dir: Path) -> list[Path]:
    if not image_dir.exists():
        raise FileNotFoundError(f"图片目录不存在: {image_dir}")

    return sorted(
        path
        for path in image_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def build_llm() -> ChatOpenAI:
    api_key = os.getenv("QUEN_API")
    base_url = os.getenv("QWEN_BASE_URL")
    model = os.getenv("QWEN_MODEL", DEFAULT_MODEL)

    if not api_key:
        raise RuntimeError("缺少环境变量 QUEN_API。")
    if not base_url:
        raise RuntimeError("缺少环境变量 QWEN_BASE_URL。")

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
    )


def describe_image(llm: ChatOpenAI, image_path: Path) -> str:
    message = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": image_to_data_url(image_path)},
                },
            ],
        }
    ]
    response = llm.invoke(message)
    content = response.content

    if isinstance(content, str):
        return clean_text(content)

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return clean_text(" ".join(parts))

    return clean_text(str(content))


def update_miaoshu(connection: pymysql.Connection, name: str, miaoshu: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(f"USE {database_name()}")
        affected = cursor.execute(
            "UPDATE yiwu SET miaoshu = %s WHERE name = %s",
            (miaoshu, name),
        )
    return affected


def parse_args() -> argparse.Namespace:
    load_dotenv(DOTENV_PATH)

    parser = argparse.ArgumentParser(description="使用 Qwen 多模态模型描述遗物图片，并写入 yiwu.miaoshu。")
    parser.add_argument("--image-dir", type=Path, default=DEFAULT_IMAGE_DIR, help="图片目录，默认 img/yiwu。")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 张图片；0 表示全量。")
    parser.add_argument("--dry-run", action="store_true", help="只生成描述并打印，不写入数据库。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    image_paths = iter_image_paths(args.image_dir)
    if args.limit > 0:
        image_paths = image_paths[: args.limit]

    print(f"[INFO] 共发现 {len(image_paths)} 张遗物图片。")
    if not image_paths:
        return

    llm = build_llm()
    connection = None if args.dry_run else pymysql.connect(**mysql_config_from_env())

    try:
        for index, image_path in enumerate(image_paths, start=1):
            name = image_name_from_path(image_path)
            print(f"[INFO] ({index}/{len(image_paths)}) 生成描述: {name}")
            miaoshu = describe_image(llm, image_path)

            if args.dry_run:
                print(f"[DRY-RUN] {name}: {miaoshu}")
                continue

            assert connection is not None
            affected = update_miaoshu(connection, name, miaoshu)
            if affected:
                print(f"[INFO] 已更新数据库: {name}")
            else:
                print(f"[WARN] 数据库中未找到 name={name}，已跳过。")
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    main()
