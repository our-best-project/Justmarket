"""MOPS 重大訊息爬蟲骨架（Justmarket 管線的進貨員）

資料來源：TWSE OpenAPI（免金鑰、回 JSON）
    https://openapi.twse.com.tw/v1/opendata/t187ap04_L   ← 上市公司重大訊息

流程三步：抓(fetch) → 轉成契約格式(transform) → 寫入 articles(load)
寫入時 status='pending'，交棒給向量化站（Timyo）。

執行前：
    pip install -r backend/requirements.txt
    確認資料庫在跑、articles 表已建（DATABASE/postgres/03_create_articles.sql）

用法（從 backend/ 執行，才吃得到 .env 的 DATABASE_URL）：
    python -m backend.crawler_legacy.mops_crawler --inspect
    python -m backend.crawler_legacy.mops_crawler --dry-run
    python -m backend.crawler_legacy.mops_crawler

⚠️ 誠實標註：t187ap04_L 回傳的欄位名稱（如「公司代號」「主旨」）以中文命名，
   實際 key 可能與本骨架假設略有出入。第一次執行請先跑 --inspect
   印出真實欄位，再對照調整 FIELD_* 常數。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from typing import Any

import requests

from backend.db.session import get_conn

# ─────────────────────────── 設定區 ───────────────────────────

API_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"

# t187ap04_L 的欄位名（2026-07-16 以 --inspect 實測核對）
FIELD_TICKER = "公司代號"
FIELD_COMPANY = "公司名稱"
FIELD_TITLE = "主旨 "        # ⚠️ 尾端空白是 API 真實欄位名（實測確認），不是筆誤，勿「修正」
FIELD_CONTENT = "說明"
FIELD_DATE = "發言日期"       # 民國年格式，如 1150714。⚠️ 不用「出表日期」（TWSE 產表日，會晚一天）、
                             #    也不用「事實發生日」（事情發生≠消息公開）

# ─────────────────────────── 三步驟 ───────────────────────────


def fetch() -> list[dict[str, Any]]:
    """步驟 1：抓。呼叫 TWSE OpenAPI，回傳原始 JSON 列表。"""
    resp = requests.get(API_URL, timeout=30)
    resp.raise_for_status()  # 非 200 直接丟例外，寧可早死早發現
    return resp.json()


def _roc_to_iso(roc_date: str) -> str | None:
    """民國年字串(1150713) → ISO 日期(2026-07-13)。格式不符回 None。"""
    s = (roc_date or "").strip()
    if len(s) == 7 and s.isdigit():
        year = int(s[:3]) + 1911
        return f"{year}-{s[3:5]}-{s[5:7]}"
    return None


def transform(raw: dict[str, Any]) -> dict[str, Any] | None:
    """步驟 2：轉。把一筆 MOPS 原始資料轉成 SD §1.1 文章物件（契約格式）。

    title 為空時回 None（整筆跳過）：NOT NULL 擋不住空字串，
    且空 title 會讓內容雜湊退化（同公司同日多則公告撞 id）——寧缺勿錯。
    """
    ticker = str(raw.get(FIELD_TICKER, "")).strip()
    # TWSE 會在長標題硬塞 \r\n 折行（--dry-run 實測），正規化成單行：
    # split() 按任何空白切、單一空格接回——確定性轉換，雜湊建立在乾淨值上
    title = " ".join(str(raw.get(FIELD_TITLE, "")).split())
    if not title:
        print(f"[transform] 跳過一筆：title 為空（代號={ticker or '?'}）")
        return None
    # content 保留段落結構（1.2.3. 分項），只統一換行格式（\r\n 與 \n 混用，實測）
    content = str(raw.get(FIELD_CONTENT, "")).strip().replace("\r\n", "\n")
    # 雜湊專用版：摧毀所有折行/多餘空白，串成一行。
    # 理由：TWSE 會在文字中硬塞折行且位置可能變動（title 實測如此，content 同病）；
    # 折行若進雜湊，同一則公告在不同批次可能算出不同 id → 冪等性從側門被擊穿。
    # 「入庫的值」與「雜湊的原料」分開：存可讀版（content），雜湊吃確定性版（content_for_hash）。
    content_for_hash = " ".join(content.split())
    pub_date = _roc_to_iso(str(raw.get(FIELD_DATE, "")))

    # article_id：來源縮寫 + 內容雜湊「代號|日期|主旨|內文(正規化)」
    # （同一則公告重抓會得到同一個 id → 天然防重複）
    # ⚠️ 2026-07-17 教訓：只用「代號|日期|主旨」不夠——同公司同日可發多則同主旨公告
    #    （實測：同日處分群聯/順達科/南亞科三檔，主旨一字不差），撞 id 會讓真公告被
    #    DO NOTHING 安靜丟掉，故摻入內文。動到本行 = 動到所有 article_id，
    #    只能在資料庫可整表重灌（開發期）時修改。
    digest = hashlib.sha256(f"{ticker}|{pub_date}|{title}|{content_for_hash}".encode()).hexdigest()[:16]

    return {
        "article_id": f"mops_{digest}",
        "source": "MOPS",
        "source_type": "official",   # 官方公告
        "stance_flag": "neutral",
        "language": "zh-TW",
        "content_scope": "full",     # 官方=存全文（契約規定，media 才是 summary_only）
        "title": title,
        "content": content,
        "related_tickers": json.dumps([ticker] if ticker else []),
        "published_at": pub_date,    # TODO: 精確到時分（欄位裡若有發言時間可併入）
        "fetched_at": datetime.now(UTC).isoformat(),
        "url": "https://mops.twse.com.tw/mops/frontend/t05sr01_1",  # TODO: 之後拼出單則連結
        "status": "pending",         # 交接棒起點，永遠是 pending
    }


UPSERT_SQL = """
INSERT INTO articles (article_id, source, source_type, stance_flag, language,
                      content_scope, title, content, related_tickers,
                      published_at, fetched_at, url, status)
VALUES (%(article_id)s, %(source)s, %(source_type)s, %(stance_flag)s, %(language)s,
        %(content_scope)s, %(title)s, %(content)s, %(related_tickers)s,
        %(published_at)s, %(fetched_at)s, %(url)s, %(status)s)
ON CONFLICT (article_id) DO NOTHING;  -- 重複抓到同一則 → 安靜跳過，不覆蓋管線狀態
"""


def load(articles: list[dict[str, Any]]) -> int:
    """步驟 3：放上輸送帶。寫入 articles 表，回傳實際新增筆數。"""
    inserted = 0
    with get_conn() as conn, conn.cursor() as cur:
        for art in articles:
            cur.execute(UPSERT_SQL, art)
            inserted += cur.rowcount  # DO NOTHING 時 rowcount=0
    return inserted


# ─────────────────────────── 進入點 ───────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="MOPS 重大訊息爬蟲")
    parser.add_argument("--inspect", action="store_true",
                        help="只印出 API 第一筆原始資料的欄位，不寫入 DB")
    parser.add_argument("--dry-run", action="store_true",
                        help="抓+轉但不寫入 DB，印前 3 筆轉換結果")
    args = parser.parse_args()

    raw_items = fetch()
    print(f"[fetch] 取得 {len(raw_items)} 筆原始資料")
    if not raw_items:
        sys.exit("[fetch] API 回空陣列，停止")

    if args.inspect:
        print(json.dumps(raw_items[0], ensure_ascii=False, indent=2))
        sys.exit(0)

    articles = [a for a in (transform(r) for r in raw_items) if a is not None]
    if len(articles) < len(raw_items):
        print(f"[transform] 有效 {len(articles)} 筆（跳過 {len(raw_items) - len(articles)} 筆）")

    if args.dry_run:
        for a in articles[:3]:
            print(json.dumps(a, ensure_ascii=False, indent=2))
        sys.exit(0)

    n = load(articles)
    print(f"[load] 新增 {n} 筆（重複跳過 {len(articles) - n} 筆），status=pending")


if __name__ == "__main__":
    main()
