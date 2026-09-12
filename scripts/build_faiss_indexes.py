from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Iterable

import faiss
import numpy as np
import pymysql
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOTENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "indexes" / "faiss"
DEFAULT_EMBEDDING_MODEL = "qwen3.7-text-embedding"
TABLES = ("yiwu", "yaoshui", "guaiwu")


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


def build_embeddings() -> OpenAIEmbeddings:
    api_key = os.getenv("QWEN_API") or os.getenv("QUEN_API")
    base_url = os.getenv("QWEN_BASE_URL")
    model = os.getenv("QWEN_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)

    if not api_key:
        raise RuntimeError("缺少环境变量 QWEN_API。")
    if not base_url:
        raise RuntimeError("缺少环境变量 QWEN_BASE_URL。")

    return OpenAIEmbeddings(
        model=model,
        api_key=api_key,
        base_url=base_url,
        check_embedding_ctx_length=False,
    )


def fetch_rows(connection: pymysql.Connection, table_name: str) -> list[tuple[int, str]]:
    with connection.cursor() as cursor:
        cursor.execute(f"USE {database_name()}")
        cursor.execute(
            f"""
            SELECT id, miaoshu
            FROM {quote_mysql_identifier(table_name)}
            WHERE miaoshu IS NOT NULL AND TRIM(miaoshu) <> ''
            ORDER BY id
            """
        )
        return [(int(row[0]), str(row[1])) for row in cursor.fetchall()]


def batched(items: list[tuple[int, str]], batch_size: int) -> Iterable[list[tuple[int, str]]]:
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def embed_rows(
    embeddings: OpenAIEmbeddings,
    rows: list[tuple[int, str]],
    batch_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    ids: list[int] = []
    vectors: list[list[float]] = []

    for batch in batched(rows, batch_size):
        batch_ids = [row_id for row_id, _ in batch]
        texts = [miaoshu for _, miaoshu in batch]
        batch_vectors = embeddings.embed_documents(texts)

        ids.extend(batch_ids)
        vectors.extend(batch_vectors)

    vector_array = np.ascontiguousarray(np.asarray(vectors, dtype="float32"))
    id_array = np.ascontiguousarray(np.asarray(ids, dtype="int64"))
    return id_array, vector_array


def build_faiss_index(vectors: np.ndarray, ids: np.ndarray, normalize: bool) -> faiss.IndexIDMap2:
    if vectors.ndim != 2 or vectors.shape[0] == 0:
        raise ValueError("没有可建立索引的向量。")

    if normalize:
        faiss.normalize_L2(vectors)
        base_index: faiss.Index = faiss.IndexFlatIP(vectors.shape[1])
    else:
        base_index = faiss.IndexFlatL2(vectors.shape[1])

    index = faiss.IndexIDMap2(base_index)
    index.add_with_ids(vectors, ids)
    return index


def write_metadata(
    output_dir: Path,
    table_name: str,
    rows: list[tuple[int, str]],
    dimension: int,
    model: str,
    normalize: bool,
) -> None:
    metadata = {
        "table": table_name,
        "model": model,
        "dimension": dimension,
        "count": len(rows),
        "normalize": normalize,
        "id_field": "id",
        "text_field": "miaoshu",
        "ids": [row_id for row_id, _ in rows],
    }
    metadata_path = output_dir / f"{table_name}.meta.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def build_one_table_index(
    connection: pymysql.Connection,
    embeddings: OpenAIEmbeddings,
    table_name: str,
    output_dir: Path,
    batch_size: int,
    normalize: bool,
) -> None:
    rows = fetch_rows(connection, table_name)
    print(f"[INFO] {table_name}: 读取到 {len(rows)} 条非空 miaoshu。")
    if not rows:
        print(f"[WARN] {table_name}: 没有可索引的数据，已跳过。")
        return

    ids, vectors = embed_rows(embeddings, rows, batch_size)
    index = build_faiss_index(vectors, ids, normalize)

    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / f"{table_name}.faiss"
    faiss.write_index(index, str(index_path))
    write_metadata(
        output_dir=output_dir,
        table_name=table_name,
        rows=rows,
        dimension=vectors.shape[1],
        model=os.getenv("QWEN_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        normalize=normalize,
    )
    print(f"[INFO] {table_name}: 已保存索引 {index_path}")


def print_table_stats(connection: pymysql.Connection, table_name: str) -> None:
    rows = fetch_rows(connection, table_name)
    print(f"[DRY-RUN] {table_name}: {len(rows)} 条非空 miaoshu 可建立索引。")


def parse_args() -> argparse.Namespace:
    load_dotenv(DOTENV_PATH)

    parser = argparse.ArgumentParser(description="为 yiwu、yaoshui、guaiwu 建立 FAISS CPU 索引。")
    parser.add_argument(
        "--tables",
        nargs="+",
        choices=TABLES,
        default=list(TABLES),
        help="要建立索引的表名，默认全部。",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="索引输出目录。")
    parser.add_argument("--batch-size", type=int, default=20, help="Embedding 批量大小，Qwen 接口要求不超过 20。")
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="不做 L2 归一化；默认归一化并使用内积索引。",
    )
    parser.add_argument("--dry-run", action="store_true", help="只检查数据量，不调用 embedding，不写索引。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    connection = pymysql.connect(**mysql_config_from_env())

    try:
        if args.dry_run:
            for table_name in args.tables:
                print_table_stats(connection, table_name)
            return

        embeddings = build_embeddings()
        for table_name in args.tables:
            build_one_table_index(
                connection=connection,
                embeddings=embeddings,
                table_name=table_name,
                output_dir=args.output_dir,
                batch_size=args.batch_size,
                normalize=not args.no_normalize,
            )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
