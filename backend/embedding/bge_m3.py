"""BGE-M3 向量化：標題(+前段內文) → 1024 維向量（本地、開源）。

定案參數（依 T07 experiments 結論，寫死不隨手改）：
  - 模型：BAAI/bge-m3（本地開源，中英跨語言，1024 維）
  - 前處理：標題 + 內文前 500 字（MAX_CHARS=500），normalize_embeddings=True
  - 去重：先依 newsId 移除完全重複紀錄

資料來源：
  - db（預設）：查 articles WHERE status='pending'，成功後推進 'vectorized'
  - json：讀凍結樣本 data/cnyes_news_2026.json，供離線回歸

輸出：out/embeddings.npy（N×1024 float32）＋ meta.jsonl（每篇 newsId/title/date）。
（聚類階段的產物在 clustering_Timyo/out/，各模組各自管自己的輸出。）

用法：
  python -m backend.embedding.bge_m3 --limit 50
  python -m backend.embedding.bge_m3 --device auto
  python -m backend.embedding.bge_m3 --source json
"""
import argparse
import json
import os
import sys
import time

import numpy as np

# Windows 主控台預設 cp950，強制 UTF-8 避免中文/特殊空白字元爆掉
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ─────────────────────────────────────────────────────────────
# 路徑與定案參數
# ─────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
# embedding → backend → <repo>
_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(_ROOT, "data", "cnyes_news_2026.json")
OUT_DIR = os.path.join(HERE, "out")   # 本階段產物：向量快取 + 中繼資料（聚類產物見 clustering/out）

MODEL_NAME = "BAAI/bge-m3"   # 定案模型，1024 維
MAX_CHARS = 500              # 內文截斷字元數（前段通常即重點；CPU 上大幅加速）


# ─────────────────────────────────────────────────────────────
# 1. 讀取待向量化文章
# ─────────────────────────────────────────────────────────────
def fetch_pending_articles_db() -> list[dict]:
    """DB 版：撈 status='pending' 的文章。

    回傳的 dict 帶 article_id（articles 的 PK）——下游 save() 會把它寫進 meta，
    讓向量快取能用 PK 對齊資料庫。離線版用的 newsId 在 DB 裡不存在。
    """
    from backend.db.session import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT article_id, title, content, url, source, related_tickers, "
            "published_at::date::text AS date "
            "FROM articles WHERE status = 'pending' ORDER BY published_at"
        ).fetchall()
    print(f"[data] 從 DB 撈到 {len(rows)} 篇 status='pending' 的文章")
    return [dict(r) for r in rows]


def mark_vectorized(article_ids: list[str]) -> int:
    """把已向量化的文章推進 status='vectorized'（交接棒交給聚類段）。"""
    from backend.db.session import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE articles SET status='vectorized', updated_at=now() "
                "WHERE article_id = ANY(%s) AND status='pending'",
                (article_ids,),
            )
            n = cur.rowcount
        conn.commit()
    return n


def fetch_pending_articles(path: str = DATA) -> list[dict]:
    """離線版：讀凍結樣本 JSON、依 newsId 去重（無 DB 環境時的來源）。"""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    seen: set = set()
    data: list[dict] = []
    for r in raw:
        nid = r.get("newsId")
        # 缺 newsId 者無法判定重複，一律保留（否則第一筆 None 之後全被誤丟）
        if nid is not None:
            if nid in seen:
                continue
            seen.add(nid)
        data.append(r)
    print(f"[data] 原始 {len(raw)} 筆 → 去重後 {len(data)} 筆"
          f"（移除 {len(raw) - len(data)} 筆完全重複）  來源={os.path.basename(path)}")
    return data


def build_text(rec: dict) -> str:
    """前處理：標題 + 內文前 500 字（定案 MAX_CHARS）。"""
    title = (rec.get("title") or "").strip()
    content = (rec.get("content") or "").strip()
    # 標題全文 + 內文前 MAX_CHARS 字（定案：內文前段即重點）
    return title + "\n" + content[:MAX_CHARS]


# ─────────────────────────────────────────────────────────────
# 2. 向量化（定案：BGE-M3，1024 維，正規化）
# ─────────────────────────────────────────────────────────────
def _resolve_device(requested: str | None = None) -> str:
    """auto 會優先使用 CUDA；明確要求 cuda 卻不可用時 fail fast。"""
    configured = (requested or os.environ.get("EMBEDDING_DEVICE", "auto")).lower()
    if configured not in {"auto", "cpu", "cuda"}:
        raise ValueError("device 只接受 auto、cpu、cuda")
    if configured == "cpu":
        return "cpu"

    import torch

    available = torch.cuda.is_available()
    if configured == "cuda" and not available:
        raise RuntimeError("EMBEDDING_DEVICE=cuda，但容器內 torch.cuda.is_available()=False")
    return "cuda" if available else "cpu"


def embed_articles(
    texts: list[str],
    batch_size: int = 16,
    device: str | None = None,
) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    resolved_device = _resolve_device(device)
    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME, device=resolved_device)
    get_dim = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
    print(
        f"[model] 載入 {MODEL_NAME}  device={resolved_device} "
        f"維度={get_dim()}  {time.time() - t0:.1f}s"
    )

    t0 = time.time()
    emb = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,   # 正規化 → 內積即 cosine
        show_progress_bar=True,
        convert_to_numpy=True,
    )
    dt = time.time() - t0
    print(f"[encode] shape={emb.shape}  dtype={emb.dtype}  "
          f"{dt:.1f}s  ({len(texts) / dt:.1f} 篇/秒)")
    return emb.astype(np.float32)


# ─────────────────────────────────────────────────────────────
# 3. 輸出檔案（DB 就緒後改為寫回 pgvector + status 推進）
# ─────────────────────────────────────────────────────────────
def save(emb: np.ndarray, data: list[dict], out_dir: str = OUT_DIR) -> None:
    if emb.ndim != 2 or emb.shape[0] == 0:
        raise ValueError(f"沒有可輸出的向量（emb.shape={emb.shape}），請檢查輸入資料")
    os.makedirs(out_dir, exist_ok=True)
    npy_path = os.path.join(out_dir, "embeddings.npy")
    meta_path = os.path.join(out_dir, "meta.jsonl")

    np.save(npy_path, emb)
    with open(meta_path, "w", encoding="utf-8") as f:
        for r in data:
            # article_id 是 DB 版的對齊鍵（articles 的 PK）；newsId 是離線 JSON 版的。
            # 兩個都寫進去，下游用哪個來源都對得回來。
            f.write(json.dumps(
                {"article_id": r.get("article_id"), "newsId": r.get("newsId"),
                 "title": r.get("title"), "date": r.get("date")},
                ensure_ascii=False,
            ) + "\n")
    print(f"[save] {npy_path}  ({emb.shape[0]}×{emb.shape[1]})")
    print(f"[save] {meta_path}")


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("db", "json"), default="db",
                    help="db=正式 articles pending；json=離線凍結樣本")
    ap.add_argument("--limit", type=int, default=None, help="只處理前 N 筆（先跑子集驗證）")
    ap.add_argument("--batch", type=int, default=16, help="編碼 batch size")
    ap.add_argument("--device", choices=("auto", "cpu", "cuda"), default=None,
                    help="預設讀 EMBEDDING_DEVICE，未設時 auto")
    ap.add_argument("--no-advance", action="store_true",
                    help="DB 模式只產向量，不推進 articles.status")
    args = ap.parse_args()

    data = fetch_pending_articles_db() if args.source == "db" else fetch_pending_articles()

    # 離線子集驗證輸出到獨立目錄；DB 子集仍是正式交接批次，
    # 必須讓 run.py 從 out/ 接得到。
    out_dir = OUT_DIR
    if args.limit is not None:
        data = data[: args.limit]
        if args.source == "json":
            out_dir = OUT_DIR + "_subset"
        cache_note = "正式 DB 交接快取" if args.source == "db" else "不覆蓋全量快取"
        print(f"[limit] 只處理前 {len(data)} 筆 → 輸出至 {os.path.basename(out_dir)}/（{cache_note}）")

    if not data:
        print("[error] 沒有待向量化的文章，結束")
        return

    texts = [build_text(r) for r in data]
    emb = embed_articles(texts, batch_size=args.batch, device=args.device)
    save(emb, data, out_dir=out_dir)
    if args.source == "db" and not args.no_advance:
        article_ids = [r["article_id"] for r in data]
        advanced = mark_vectorized(article_ids)
        print(f"[status] {advanced} 篇 pending → vectorized")


if __name__ == "__main__":
    main()
