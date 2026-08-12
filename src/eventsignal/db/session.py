"""資料庫連線與 session（PostgreSQL + pgvector）。

原本是一行 docstring 的空殼——各管線段各自用 psycopg /
sqlalchemy.text 手刻 SQL、各自讀 .env，繞過這一層。本檔補上 API 層要用的最小連線。

【driver】用 psycopg v3（requirements.txt 裡那個、Bright 也用它）。
  ⚠️ Sunshine 的 summarize.py 走 SQLAlchemy create_engine("postgresql://…")，
  會用 psycopg2 方言 —— 團隊目前實際需要兩個 driver。

【.env】沿用 client.py:62 `_load_dotenv()` 的規則：從 cwd 往上找第一個 .env。
  應用程式的 .env 是 .env；DATABASE/ 只放 DDL、資料資產與本機備援設定。

"""

import os
import threading
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from eventsignal.core import config


def db_url() -> str:
    # .env 載入與必填檢查統一走 core/config（2026-08-08 收斂，原本這裡自刻一份）
    return config.require(
        "DATABASE_URL",
        hint="本地 docker：postgresql://<user>:<pw>@localhost:5432/<db>",
    )


@contextmanager
def get_conn():
    """每個請求一條連線（MVP 規模夠用；量大再換 psycopg_pool）。

    row_factory=dict_row → 查詢結果直接是 dict，方便丟給 Pydantic。
    """
    # ⚠️ 時區必須顯式設台北（P2-08）。Neon 預設 session timezone=GMT，
    # CURRENT_DATE 在台北 00:00–08:00 之間會慢一天——API 的「今日事件」
    # （tickers.py windows、demo.py 的未來日期過濾）在那 8 小時內會查錯日。
    with psycopg.connect(
        db_url(), row_factory=dict_row,
        options="-c timezone=Asia/Taipei",
    ) as conn:
        yield conn


# ─────────────────────────────────────────────────────────────────────────────
# 連線池（僅供 ② API 層使用；批次程序請繼續用上面的 get_conn()）
#
# 上面的 get_conn() 每次呼叫都 psycopg.connect()，而 db_url() 每次都會
# _load_dotenv() 走訪檔案系統找 .env。批次程序連線次數少、單次持有久，那個
# 成本無所謂；但 API 是每個 HTTP 請求付一次，而 DB 在 Neon（跨網路），成本是
# 檔案系統走訪 + TCP + TLS handshake + 認證。
#
# 【為什麼另開函式，而不是改造 get_conn()】
# session.py 被 6 個非 API 模組 import：run.py、ingestion.py、
# crawler_Arku/mops_crawler.py、embedding_Timyo/bge_m3.py、
# market_index/daily_batch.py、orchestration/flows.py。改造 get_conn() 會改變
# 那些程式的行為（連線改為來自池、離開 with 是歸還而非關閉）。這裡只做加法：
# 上面三個函式一行都沒動。
# 決策與被否決方案見 docs/superpowers/specs/2026-07-31-api-db-pool-and-cache-design.md
# ─────────────────────────────────────────────────────────────────────────────

_pool = None                      # psycopg_pool.ConnectionPool | None
_pool_lock = threading.Lock()


def _connection_pool_class():
    """延遲 import psycopg_pool。

    ⚠️ 這個延遲是必要的，不是風格偏好：session.py 被 6 個非 API 模組 import，
    在模組頂層 import psycopg_pool 會讓那些批次程式在尚未安裝該套件的環境下
    直接 ImportError —— 那正是本次要避免的「動到別人的程式」。
    """
    from psycopg_pool import ConnectionPool

    return ConnectionPool


def _pool_max_size() -> int:
    """池上限。Neon 有連線數上限，預設保守。設定錯誤時退回預設而不是炸掉。"""
    try:
        return max(1, int(os.environ.get("API_DB_POOL_MAX", "10")))
    except ValueError:
        return 10


def open_pool() -> None:
    """建立並開啟池。已開啟時為 no-op（可重複呼叫）。

    在 app 啟動時呼叫 → DATABASE_URL 沒設或 DB 連不上會立刻失敗，
    而不是等到第一個請求進來才炸（原本是後者）。
    """
    global _pool
    with _pool_lock:
        if _pool is not None:
            return
        pool_class = _connection_pool_class()
        _pool = pool_class(
            db_url(),
            min_size=1,
            max_size=_pool_max_size(),
            # 現有端點全部依賴 dict 形式的查詢結果，這個不能拿掉
            # options 與 get_conn() 同步：Neon 預設 session timezone=GMT，
            # 少了它，池化後的 API 在台北 00:00–08:00 之間 CURRENT_DATE 會慢一天（P2-08）
            kwargs={"row_factory": dict_row, "options": "-c timezone=Asia/Taipei"},
            # Neon 會關閉閒置連線；不檢查就會從池裡拿到死連線
            check=pool_class.check_connection,
            open=True,
        )


def close_pool() -> None:
    """關閉池並釋放。未開啟時為 no-op。"""
    global _pool
    with _pool_lock:
        if _pool is None:
            return
        _pool.close()
        _pool = None


@contextmanager
def get_pooled_conn():
    """從池取得連線（API 層專用）。離開 with 時歸還池，不關閉。

    池未開啟時自行開啟 —— 讓端點函式在測試中可獨立呼叫，不強制依賴啟動流程。
    """
    if _pool is None:
        open_pool()
    with _pool.connection() as conn:
        yield conn
