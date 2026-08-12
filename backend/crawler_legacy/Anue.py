"""媒體爬蟲：工商時報/經濟日報/鉅亨 Anue（只存標題+導言/摘要+發佈時間+連結）。

目前只實作鉅亨 Anue（來源：history/anue_scraping.ipynb 轉寫，
邏輯未變更，只做結構化＋CLI 化）。工商時報/經濟日報尚未實作。

⚠️ 兩個已知問題（轉寫時原樣保留，需團隊決定，不擅自改）：
  1. 界線：README 第一站訂「媒體只存 標題+導言/摘要，不轉存全文」，但本程式
     沿用 notebook 行為＝優先取 API 的 content（全文），摘要只是 fallback。
     若要守界線，把 --scope 設 summary_only（見 fetch_day）。
  2. 分頁：notebook 原本固定 page=1&limit=50，單日超過 50 篇會靜默漏掉。
     本檔預設 --max-pages 1 維持原行為；設 >1 才會翻頁（行為改變，需知情同意）。

輸出：與 notebook 相同的 JSON 陣列（newsId/url/title/content/date/publishAt）。
     ⚠️ 尚未寫入 articles 表——那是下一棒（README 第三站 T10）。
     articles 表還需要 source/source_type/stance_flag/content_scope/language/
     related_tickers 等欄位，本程式都還沒產出。

用法：
    python -m backend.crawler_legacy.Anue --start 2026-01-01 --end 2026-01-07 \
        --out ../data/cnyes_news_sample.json
    python -m backend.crawler_legacy.Anue --start 2026-01-01 --scope summary_only
"""

import argparse
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from .base import clean_html

TZ = ZoneInfo("Asia/Taipei")
BASE_URL = "https://api.cnyes.com/media/api/v1/newslist/category/tw_stock_news"
NEWS_BASE_URL = "https://news.cnyes.com/news/id/"
PAGE_LIMIT = 50


def fetch_day(
    day: datetime,
    scope: str = "full",
    max_pages: int = 1,
    delay: float = 0.3,
    session: requests.Session | None = None,
) -> list[dict]:
    """抓單日新聞。回傳 notebook 格式的 dict 陣列；失敗回空陣列（不中斷整批）。

    scope: "full"（原 notebook 行為，優先全文）或 "summary_only"（只取摘要，守 README 界線）。
    max_pages: 1 = 原行為（只抓第一頁）；>1 才翻頁。
    """
    sess = session or requests
    start_ts = int(day.timestamp())
    end_ts = start_ts + 86400
    date_str = day.strftime("%Y-%m-%d")
    out: list[dict] = []

    for page in range(1, max_pages + 1):
        url = f"{BASE_URL}?page={page}&limit={PAGE_LIMIT}&startAt={start_ts}&endAt={end_ts}"
        try:
            resp = sess.get(url, timeout=20)
            if resp.status_code != 200:
                print(f"  [!] {date_str} p{page} API error (status {resp.status_code})")
                break
            items = resp.json().get("items", {}).get("data", [])
        except Exception as e:
            print(f"  [!] {date_str} p{page} failed: {e}")
            break

        for item in items:
            news_id = item.get("newsId")
            if scope == "summary_only":
                raw = item.get("summary") or ""
            else:
                raw = item.get("content") or item.get("summary") or ""
            out.append({
                "newsId": news_id,
                "url": NEWS_BASE_URL + str(news_id),
                "title": (item.get("title") or "").strip(),
                "content": clean_html(raw),
                "date": date_str,
                "publishAt": item.get("publishAt"),
            })

        if len(items) < PAGE_LIMIT:
            break          # 這天抓完了
        if delay:
            time.sleep(delay)

    return out


def scrape_range(
    start: datetime,
    end: datetime,
    scope: str = "full",
    max_pages: int = 1,
    delay: float = 0.3,
) -> list[dict]:
    """逐日抓取 [start, end]，回傳全部文章。"""
    all_news: list[dict] = []
    day = start
    with requests.Session() as sess:
        while day <= end:
            got = fetch_day(day, scope=scope, max_pages=max_pages, delay=delay, session=sess)
            all_news.extend(got)
            print(f"  {day.strftime('%Y-%m-%d')} | {len(got)} articles")
            day += timedelta(days=1)
            if delay:
                time.sleep(delay)
    return all_news


def _parse_day(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=TZ)


def main() -> None:
    p = argparse.ArgumentParser(description="鉅亨 Anue 台股新聞爬蟲")
    p.add_argument("--start", default="2026-01-01", help="起始日 YYYY-MM-DD")
    p.add_argument("--end", default=None, help="結束日 YYYY-MM-DD（預設今天）")
    p.add_argument("--out", default="cnyes_news.json", help="輸出 JSON 路徑")
    p.add_argument("--scope", choices=["full", "summary_only"], default="full",
                   help="full=原 notebook 行為（優先全文）；summary_only=守 README 界線")
    p.add_argument("--max-pages", type=int, default=1,
                   help="每日最多翻幾頁（1=原行為，可能漏掉當日第 50 篇之後的新聞）")
    p.add_argument("--delay", type=float, default=0.3, help="每次請求間隔秒數")
    args = p.parse_args()

    start = _parse_day(args.start)
    end = _parse_day(args.end) if args.end else datetime.now(TZ)

    print(f"--- Anue scraper {start:%Y-%m-%d} → {end:%Y-%m-%d} "
          f"(scope={args.scope}, max_pages={args.max_pages}) ---")
    news = scrape_range(start, end, scope=args.scope, max_pages=args.max_pages, delay=args.delay)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(news, f, ensure_ascii=False, indent=4)
    print(f"\nDone: {len(news)} articles → {args.out}")


if __name__ == "__main__":
    main()
