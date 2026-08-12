"""crawler 的可信執行與 Neon ingestion 邊界。

正式新聞入口只有 ``crawler``。本模組負責：
1. 以 Scrapy CLI 執行已核准 spider，取得 JSON feed；
2. 驗證最小輸出契約；
3. 正規化並冪等寫入 ``articles(status='pending')``。

Spider Forge Graph 不在這條路徑上；candidate 生成、修復、升版都不會被排程。
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eventsignal.db.session import get_conn

# eventsignal → src → <repo>
REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPO_ROOT / "crawler"           # Scrapy 專案；以子程序執行，不 import
INGEST_OUT = RUNTIME_ROOT / "out" / "ingest"   # jsonlines feed + log 落腳（gitignore）
STOPLIST_PATH = REPO_ROOT / "data" / "ticker_stoplist.json"

try:  # Windows 主控台預設 cp950 無法印中文/符號；統一 utf-8（同 app/run.py）
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

CODE_IN_TEXT = re.compile(r"[(（\[【]\s*(\d{4}[A-Za-z]?\d?)\s*[)）\]】]")

UPSERT_SQL = """
INSERT INTO articles (article_id, source, source_type, stance_flag, language,
                      content_scope, title, content, url, related_tickers,
                      published_at, fetched_at, status)
VALUES (%(article_id)s, %(source)s, %(source_type)s, %(stance_flag)s, %(language)s,
        %(content_scope)s, %(title)s, %(content)s, %(url)s, %(related_tickers)s,
        %(published_at)s, %(fetched_at)s, %(status)s)
ON CONFLICT (article_id) DO NOTHING;
"""


@dataclass(frozen=True)
class CrawlerSpec:
    name: str
    source: str
    source_prefix: str
    source_type: str
    content_scope: str
    allowed_domains: tuple[str, ...]
    arguments: tuple[str, ...] = ()


# 歷史別名 → registry 正名（P1-03）。預設設定、compose 與舊文件寫的是 cnyes_finance，
# 但 registry 只有 cnyes——預設排程一啟動就 KeyError。名稱以 registry 為準，
# 別名保留讓既有部署不炸。
SPIDER_ALIASES = {"cnyes_finance": "cnyes"}


def _resolve_spider(name: str) -> str:
    return SPIDER_ALIASES.get(name, name)


APPROVED_CRAWLERS = {
    "cnyes": CrawlerSpec(
        name="cnyes",
        source="鉅亨網",
        source_prefix="cnyes",
        source_type="media",
        content_scope="full",
        allowed_domains=("api.cnyes.com", "news.cnyes.com"),
    ),
    "federalreserve": CrawlerSpec(
        name="federalreserve",
        source="Federal Reserve Press Releases",
        source_prefix="federalreserve",
        source_type="official",
        content_scope="full",
        allowed_domains=("www.federalreserve.gov",),
    ),
    "cna": CrawlerSpec(
        name="cna",
        source="中央社財經",
        source_prefix="cna",
        source_type="media",
        content_scope="full",
        allowed_domains=("www.cna.com.tw", "cna.com.tw"),
    ),
    "udn": CrawlerSpec(
        name="udn",
        source="經濟日報",
        source_prefix="udn",
        source_type="media",
        content_scope="full",
        allowed_domains=("money.udn.com", "udn.com"),
    ),
    "moneydj": CrawlerSpec(
        name="moneydj",
        source="MoneyDJ理財網",
        source_prefix="moneydj",
        source_type="media",
        content_scope="full",
        allowed_domains=("www.moneydj.com", "moneydj.com"),
    ),
    "ettoday_fin": CrawlerSpec(
        name="ettoday_fin",
        source="ETtoday財經",
        source_prefix="ettoday_fin",
        source_type="media",
        content_scope="full",
        allowed_domains=("finance.ettoday.net", "ettoday.net", "www.ettoday.net"),
    ),
    "tw_stock_yahoo_com": CrawlerSpec(
        name="tw_stock_yahoo_com",
        source="tw.stock.yahoo.com",
        source_prefix="tw_stock_yahoo_com",
        source_type="media",
        content_scope="full",
        allowed_domains=("tw.stock.yahoo.com", "tw.news.yahoo.com"),
    ),
    "twse_mops_finance": CrawlerSpec(
        name="twse_mops_finance",
        source="公開資訊觀測站 MOPS",
        source_prefix="mops",
        source_type="official",
        content_scope="full",
        allowed_domains=("mops.twse.com.tw", "openapi.twse.com.tw"),
    ),
}

# 每日增量安全預設；回填時 CLI 以 since/companies 覆蓋。
DAILY_ARGS = {"days": "2"}


def clean_html(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    text = html.unescape(str(raw_html))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _load_stoplist() -> set[str]:
    if not STOPLIST_PATH.is_file():
        return set()
    payload = json.loads(STOPLIST_PATH.read_text(encoding="utf-8"))
    return {item["name"] for item in payload.get("停用清單", [])}


def _ticker_index() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT ticker, name FROM tickers").fetchall()

    by_name: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        ticker, name = row["ticker"], row["name"]
        by_name[name].append(ticker)

    stoplist = _load_stoplist()
    name_to_ticker = {
        name: tickers[0]
        for name, tickers in by_name.items()
        if len(tickers) == 1 and name not in stoplist
    }
    return {
        "names": sorted(name_to_ticker, key=len, reverse=True),
        "name_to_ticker": name_to_ticker,
        "codes": {row["ticker"] for row in rows},
    }


def extract_related_tickers(title: str, index: dict) -> list[str]:
    found = {code for code in CODE_IN_TEXT.findall(title) if code in index["codes"]}
    remaining = title
    for name in index["names"]:
        if name in remaining:
            found.add(index["name_to_ticker"][name])
            remaining = remaining.replace(name, "\t")
    return sorted(found)


def make_article_id(source_prefix: str, identity: str) -> str:
    digest = hashlib.sha256(identity.strip().encode()).hexdigest()[:16]
    return f"{source_prefix}_{digest}"


def normalize_article(
    item: dict[str, Any],
    *,
    source: str,
    source_prefix: str,
    ticker_index: dict | None = None,
    source_type: str = "media",
    content_scope: str | None = None,
) -> dict[str, Any] | None:
    title = " ".join(str(item.get("title", "")).split())
    url = str(item.get("url", "")).strip()
    content = clean_html(item.get("content"))
    published_at = item.get("published_at")
    # 相容既有 persistence 契約：normalize 本身只硬擋 title/url；
    # 正式 runtime ingestion 會在下方另做完整欄位 gate。
    if not title or not url:
        return None

    # 注意 .get(key) or ""：source_record_id 值為 null 時 .get(key, "") 會回 None
    # 而非 ""，str(None)="None" 會讓所有無 id 的文章撞成同一個 article_id（掉資料）。
    identity = str(item.get("source_record_id") or "").strip() or url
    scope = content_scope or ("full" if source_type == "official" else "summary_only")
    tickers = extract_related_tickers(title, ticker_index) if ticker_index else []
    return {
        "article_id": make_article_id(source_prefix, identity),
        "source": source,
        "source_type": source_type,
        "stance_flag": "neutral",
        "language": "zh-TW",
        "content_scope": scope,
        "title": title,
        "content": content,
        "url": url,
        "related_tickers": json.dumps(tickers, ensure_ascii=False),
        "published_at": published_at,
        "fetched_at": datetime.now(UTC).isoformat(),
        "status": "pending",
    }


def upsert_articles(articles: list[dict[str, Any]]) -> int:
    inserted = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for article in articles:
                cur.execute(UPSERT_SQL, article)
                inserted += cur.rowcount
        conn.commit()
    return inserted


def _read_new_records(path: Path, state: dict) -> list[dict[str, Any]]:
    """從 jsonlines feed 讀 state['offset'] 之後的完整行；未寫完的最後一行留待下輪。"""
    if not path.is_file():
        return []
    with path.open("rb") as handle:
        handle.seek(state["offset"])
        data = handle.read()
    last_nl = data.rfind(b"\n")
    if last_nl == -1:
        return []
    state["offset"] += last_nl + 1
    records: list[dict[str, Any]] = []
    for raw in data[: last_nl + 1].split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return records


def crawl_and_ingest(
    spider_name: str,
    *,
    since: str = "",
    companies: str = "",
    days: str | int = "",
    timeout: int = 900,
    dry_run: bool = False,
    batch_size: int = 50,
    poll_seconds: float = 3.0,
) -> dict[str, int]:
    """執行一支核准 spider，**邊爬邊分批冪等寫入 Neon**，並串流 log 供即時查看。

    回填：傳 ``since='2026-01-29'``（MOPS 另需 ``companies='2330,...'`` 或 ``companies=watchlist``）。
    每日增量：不傳 since（走 DAILY_ARGS 的 days=2）。回填長跑把 ``timeout`` 調大。
    產物：jsonlines feed 與 log 落在 ``crawler/out/ingest/``（gitignore）；
    log 可即時 tail 看進度，中途中斷時已批次寫入 DB 的部分保留（冪等，可直接重跑補齊）。
    """
    try:
        spec = APPROVED_CRAWLERS[_resolve_spider(spider_name)]
    except KeyError as exc:
        raise ValueError(
            f"未核准 crawler：{spider_name}；可用 {sorted(APPROVED_CRAWLERS)}"
        ) from exc
    if not (RUNTIME_ROOT / "scrapy.cfg").is_file():
        raise RuntimeError(f"crawler 不存在：{RUNTIME_ROOT}")

    args = {k: v for k, v in (a.split("=", 1) for a in spec.arguments)}
    if since:
        args["since"] = str(since)
    elif days != "":
        args["days"] = str(days)
    else:
        args.update(DAILY_ARGS)
    if companies:
        args["companies"] = str(companies)

    INGEST_OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    feed_path = INGEST_OUT / f"{spec.name}_{stamp}.jsonl"
    log_path = INGEST_OUT / f"{spec.name}_{stamp}.log"

    command = [sys.executable, "-m", "scrapy", "crawl", spec.name]
    for key, value in args.items():
        command.extend(("-a", f"{key}={value}"))
    command.extend(("-O", f"{feed_path}:jsonlines"))
    command.extend(("-s", f"LOG_FILE={log_path}", "-s", "LOG_LEVEL=INFO"))

    env = os.environ.copy()
    env["SPIDERFORGE_ALLOWED_DOMAINS"] = ",".join(spec.allowed_domains)

    index = None if dry_run else _ticker_index()
    print(f"  feed: {feed_path}")
    print(f"  log : {log_path}")
    print(f"  -> 即時看進度：Get-Content '{log_path}' -Wait -Tail 20")

    proc = subprocess.Popen(
        command, cwd=RUNTIME_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    state = {"offset": 0}
    fetched = valid = inserted = 0
    buffer: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    timed_out = False

    def consume(records: list[dict[str, Any]]) -> None:
        nonlocal fetched, valid, inserted, buffer
        for item in records:
            fetched += 1
            if not (item.get("content") and item.get("published_at")):
                continue
            row = normalize_article(
                item, source=spec.source, source_prefix=spec.source_prefix,
                ticker_index=index, source_type=spec.source_type,
                content_scope=spec.content_scope,
            )
            if row is None:
                continue
            valid += 1
            buffer.append(row)
        if len(buffer) >= batch_size:
            if not dry_run:
                inserted += upsert_articles(buffer)
            buffer = []
            print(f"  進度：已讀 {fetched}、有效 {valid}、寫入 {inserted}")

    try:
        while True:
            running = proc.poll() is None
            records = _read_new_records(feed_path, state)
            if records:
                consume(records)
            elif not running:
                break
            elif time.monotonic() > deadline:
                proc.terminate()
                timed_out = True
                break
            else:
                time.sleep(poll_seconds)
        consume(_read_new_records(feed_path, state))  # 收尾最後一批
        if buffer and not dry_run:
            inserted += upsert_articles(buffer)
    finally:
        if proc.poll() is None:
            proc.terminate()

    rc = proc.poll()
    ratio = (valid / fetched) if fetched else 0.0
    notes = []
    if timed_out:
        notes.append(f"已達 timeout {timeout}s 終止")
    if rc not in (0, None) and not timed_out:
        notes.append(f"scrapy 非零退出 rc={rc}，詳見 {log_path}")

    # ⚠️ 失敗必須拋例外，不能只印警告（P1-02）。
    # 這裡曾經無論 timeout／非零退出／零筆都回 dict「成功」收場——Prefect 的
    # retries 永遠不會觸發，排程面板一片綠，資料卻默默缺漏，沒有人會發現。
    # 判準：
    #   timeout / 非零退出        → 一律視為失敗（程序沒有正常走完）
    #   fetched == 0             → 也是失敗。新聞站不可能整輪零篇；零篇代表選擇器
    #                              壞了或被擋。寫入 0 筆則是正常的（重跑時全數已存在）。
    if timed_out:
        raise RuntimeError(
            f"{spec.name} 爬取失敗：timeout {timeout}s 終止"
            f"（已讀 {fetched}、寫入 {inserted}），詳見 {log_path}")
    if rc not in (0, None):
        raise RuntimeError(
            f"{spec.name} 爬取失敗：scrapy 非零退出 rc={rc}"
            f"（已讀 {fetched}、寫入 {inserted}），詳見 {log_path}")
    # 低頻官方來源豁免零篇檢查：央行不是新聞台，幾天沒有新聞稿是常態。
    # 8/11 實測 federalreserve 週一無新稿被誤判「爬取失敗」——誤報警本身
    # 也是一種狼來了，會讓真的失敗被忽略。媒體來源維持嚴格：零篇必為異常。
    LOW_FREQUENCY_OK = {"federalreserve"}
    if fetched == 0 and not dry_run and spec.name not in LOW_FREQUENCY_OK:
        raise RuntimeError(
            f"{spec.name} 爬取失敗：整輪 0 篇——新聞站不可能沒新聞，"
            f"多半是版面改版或被擋，詳見 {log_path}")
    if fetched and ratio < 0.8:
        notes.append(f"有效率偏低 {ratio:.0%}")
    if fetched == 0:
        notes.append(f"0 筆，檢查 log：{log_path}")
    suffix = ("　⚠️ " + "；".join(notes)) if notes else ""
    print(f"  {spec.name} 收尾：已讀 {fetched}、有效 {valid}、寫入 {inserted}{suffix}")
    return {"fetched": fetched, "valid": valid, "inserted": inserted}


BACKFILL_NEWS = [
    "cnyes", "federalreserve", "cna", "udn",
    "moneydj", "ettoday_fin", "tw_stock_yahoo_com",
]


def _print_target() -> None:
    from urllib.parse import urlparse

    from eventsignal.db.session import db_url

    u = urlparse(db_url())
    host = u.hostname or "?"
    where = "本機" if host in ("localhost", "127.0.0.1", "::1") else f"⚠️ 遠端（{host}）"
    print(f"[DB] 寫入目標：{where}  {host}:{u.port or 5432}/{(u.path or '').lstrip('/')}")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        description="crawler → Neon 抓取並冪等寫入（純 venv，不依賴 Docker）"
    )
    ap.add_argument("--spider", help=f"單一 spider；可用 {sorted(APPROVED_CRAWLERS)}")
    ap.add_argument("--all", action="store_true",
                    help="依序跑全部新聞 spider（不含 mops；mops 需 companies 另跑）")
    ap.add_argument("--since", default="", help="回填起始日 YYYY-MM-DD（不帶＝每日增量）")
    ap.add_argument("--companies", default="",
                    help="MOPS 歷史回填股票代號，逗號分隔，如 2330,2454")
    ap.add_argument("--days", default="", help="每日增量視窗天數（不帶 since 時生效，預設 2）")
    ap.add_argument("--timeout", type=int, default=900,
                    help="單支 spider subprocess 逾時秒數；回填長跑請調大（如 7200）")
    ap.add_argument("--dry-run", action="store_true", help="只抓取驗證契約、不寫 DB")
    args = ap.parse_args()

    if args.all:
        names = list(BACKFILL_NEWS)
    elif args.spider:
        names = [args.spider]
    else:
        raise SystemExit("請指定 --spider <name> 或 --all")

    if not args.dry_run:
        _print_target()

    total = {"fetched": 0, "valid": 0, "inserted": 0}
    for name in names:
        print(f"\n=== {name} 開始（since={args.since or '每日增量'}）===")
        try:
            res = crawl_and_ingest(
                name,
                since=args.since,
                companies=args.companies,
                days=args.days,
                timeout=args.timeout,
                dry_run=args.dry_run,
            )
            for key in total:
                total[key] += res[key]
            print(f"=== {name} 完成：抓取 {res['fetched']}、"
                  f"有效 {res['valid']}、寫入 {res['inserted']} ===")
        except Exception as exc:
            print(f"=== {name} 失敗：{exc} ===")
    print(f"\n[總計] 抓取 {total['fetched']}、"
          f"有效 {total['valid']}、寫入 {total['inserted']}")


if __name__ == "__main__":
    main()
