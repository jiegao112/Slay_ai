"""
scripts/search_faiss.py

读入 indexes/faiss 下的 FAISS 索引，输入一段话，搜索最近的向量。

用法：
    # 直接修改本文件顶部的配置区（INDEX_NAME / QUERY_TEXT / TOP_K），然后运行：
    python scripts/search_faiss.py

    # 也可以用命令行参数覆盖配置区：
    python scripts/search_faiss.py --index guaiwu --query "一只会喷火的怪物" --top-k 10

说明：
    - 搜索方式：线性搜索，使用 faiss 的 IndexFlatL2（暴力扫描索引中的全部向量）。
    - 相似度：欧式距离（L2）。注意 faiss 的 IndexFlatL2.search() 返回的是平方欧式距离，
      脚本会对结果开方，展示的是真正的欧式距离，越小越相似。
    - 索引：indexes/faiss/ 下的三个索引，可选 yiwu（遗物）/ yaoshui（药水）/ guaiwu（怪物）。
      磁盘索引可能是内积索引（建索引时归一化），脚本会用索引中的原始向量在内存中重建
      一个 IndexIDMap2(IndexFlatL2)，保证用 faiss 线性搜索时返回欧式距离。
    - Embedding 模型：固定使用 qwen3.7-text-embedding；
      API Key 与 URL 从环境（.env）中的 QWEN_API（兼容 QUEN_API）和 QWEN_BASE_URL 读取。
    - 会尝试从 MySQL 读取命中项的原文（miaoshu）一起展示；数据库不可用时自动降级为只显示 id。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import faiss
import numpy as np
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FAISS_DIR = PROJECT_ROOT / "indexes" / "faiss"
DOTENV_PATH = PROJECT_ROOT / ".env"
EMBEDDING_MODEL = "qwen3.7-text-embedding"
INDEX_NAMES = ("yiwu", "yaoshui", "guaiwu")

# ===================== 配置区：直接修改这里 =====================
INDEX_NAME = "guaiwu"          # 在哪个索引里搜索：yiwu / yaoshui / guaiwu
QUERY_TEXT = "这是一只趴伏在地面的黄绿色甲虫状怪物，体型低矮圆润。其背部覆盖着类似岩石或甲壳的粗糙质感外壳，呈现出明亮的柠檬黄色与暗绿色交织的斑纹，头部伸出两根细长的粉色触角，姿态静止。"   # 要搜索的一段话
TOP_K = 5                      # 返回最近的前 N 个
SHOW_TEXT = True               # 是否尝试从数据库读取命中项原文一起展示
# ==============================================================


def build_embeddings() -> OpenAIEmbeddings:
    """固定使用 qwen3.7-text-embedding；API Key / URL 取自 QWEN_API 与 QWEN_BASE_URL。"""
    api_key = os.getenv("QWEN_API") or os.getenv("QUEN_API")
    base_url = os.getenv("QWEN_BASE_URL")
    if not api_key:
        raise RuntimeError("缺少环境变量 QWEN_API（或 QUEN_API），请在 .env 中配置。")
    if not base_url:
        raise RuntimeError("缺少环境变量 QWEN_BASE_URL，请在 .env 中配置。")
    return OpenAIEmbeddings(
        model=EMBEDDING_MODEL,
        api_key=api_key,
        base_url=base_url,
        check_embedding_ctx_length=False,
    )


def load_index(index_name: str) -> tuple[object, dict]:
    """读取 FAISS 索引与元数据，返回 (search_index, meta)。

    - search_index 始终是基于欧式距离（L2）的线性索引 IndexIDMap2(IndexFlatL2)，
      可直接调用 faiss 的 search() 做暴力线性搜索，命中结果为外部 id。
    - 若磁盘索引本身不是 L2 度量（例如建索引时做了 L2 归一化、底层是内积索引），
      会用它存储的原始向量在内存中重建一个 L2 线性索引。
    """
    index_path = FAISS_DIR / f"{index_name}.faiss"
    meta_path = FAISS_DIR / f"{index_name}.meta.json"
    if not index_path.exists():
        raise FileNotFoundError(f"找不到索引文件：{index_path}")
    if not meta_path.exists():
        raise FileNotFoundError(f"找不到元数据文件：{meta_path}")

    index = faiss.read_index(str(index_path))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # IndexIDMap/IndexIDMap2 包装了内部基础索引；直接对基础索引取向量最稳妥
    base = getattr(index, "index", index)
    vectors = np.asarray(base.reconstruct_n(0, base.ntotal), dtype="float32").reshape(base.ntotal, -1)
    ids = faiss.vector_to_array(index.id_map)

    if len(vectors) != meta.get("count", len(vectors)):
        print(f"[WARN] 索引向量数 {len(vectors)} 与元数据 count={meta.get('count')} 不一致。")

    # 用 faiss 重建欧式距离（L2）的线性索引：IndexFlatL2 即暴力线性搜索
    search_index = faiss.IndexIDMap2(faiss.IndexFlatL2(vectors.shape[1]))
    search_index.add_with_ids(vectors, ids)

    return search_index, meta


def load_texts_from_db(table_name: str, ids: np.ndarray) -> dict[int, str]:
    """从 MySQL 读取命中 id 对应的原文（miaoshu）；数据库不可用时返回空字典。"""
    try:
        import pymysql
    except ImportError:
        return {}

    conn = None
    try:
        conn = pymysql.connect(
            host=os.getenv("DB_HOST") or os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT") or os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("DB_USER") or os.getenv("MYSQL_USER", "root"),
            password=os.getenv("DB_PASSWORD") or os.getenv("MYSQL_PASSWORD", ""),
            charset="utf8mb4",
            autocommit=True,
        )
        db = os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "slay_ai")
        placeholders = ", ".join(["%s"] * len(ids))
        with conn.cursor() as cursor:
            cursor.execute(f"USE `{db}`")
            cursor.execute(
                f"SELECT id, miaoshu FROM `{table_name}` WHERE id IN ({placeholders})",
                tuple(int(i) for i in ids),
            )
            return {int(row[0]): str(row[1]) for row in cursor.fetchall()}
    except Exception as exc:  # 数据库不可用时降级，不影响搜索
        print(f"[WARN] 读取数据库原文失败，将只显示 id：{exc}")
        return {}
    finally:
        if conn is not None:
            conn.close()


def main() -> None:
    # 强制 UTF-8 输出，避免 Windows 控制台按本地代码页（如 GBK）打印中文乱码
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    load_dotenv(DOTENV_PATH)

    parser = argparse.ArgumentParser(description="读入 FAISS 索引，线性搜索最近的向量（欧式距离）。")
    parser.add_argument("--index", default=INDEX_NAME, help=f"索引名，可选：{' / '.join(INDEX_NAMES)}")
    parser.add_argument("--query", default=QUERY_TEXT, help="要搜索的一段话")
    parser.add_argument("--top-k", type=int, default=TOP_K, help="返回最近的前 N 个")
    parser.add_argument("--no-text", action="store_true", help="不从数据库读取原文")
    args = parser.parse_args()

    if args.index not in INDEX_NAMES:
        raise SystemExit(f"未知索引名：{args.index}，可选：{' / '.join(INDEX_NAMES)}")
    if args.top_k <= 0:
        raise SystemExit("--top-k 必须大于 0")
    if not args.query.strip():
        raise SystemExit("查询文本不能为空。")

    search_index, meta = load_index(args.index)
    embeddings = build_embeddings()

    print(f"[INFO] 索引：{args.index}（表 {meta.get('table', args.index)}），"
          f"向量数 {search_index.ntotal}，维度 {meta.get('dimension', search_index.d)}")
    print(f"[INFO] Embedding 模型：{EMBEDDING_MODEL}")
    print(f"[INFO] 查询：{args.query}")

    # 用 qwen3.7-text-embedding 编码查询文本
    query_vec = np.asarray(embeddings.embed_query(args.query), dtype="float32")
    if meta.get("normalize"):  # 建索引时做了 L2 归一化，查询向量也做同样的归一化
        faiss.normalize_L2(query_vec.reshape(1, -1))

    # faiss 线性搜索（IndexFlatL2 暴力扫描全部向量），返回 (平方欧式距离, 外部 id)
    squared_distances, hit_ids = search_index.search(query_vec.reshape(1, -1), args.top_k)

    # 平方距离开方得到欧式距离；过滤掉 faiss 补位产生的 -1
    results = [
        (float(np.sqrt(d)), int(i))
        for d, i in zip(squared_distances[0], hit_ids[0])
        if i != -1
    ]
    if not results:
        print("没有找到结果。")
        return

    texts: dict[int, str] = {}
    if SHOW_TEXT and not args.no_text:
        texts = load_texts_from_db(args.index, np.asarray([i for _, i in results], dtype=np.int64))

    print("\n搜索结果（欧式距离，越小越相似）：")
    for rank, (dist, row_id) in enumerate(results, start=1):
        text = texts.get(row_id, "")
        suffix = f"  原文：{text}" if text else ""
        print(f"  #{rank:<3} id={row_id:<5} 欧式距离={dist:.6f}{suffix}")


if __name__ == "__main__":
    main()
