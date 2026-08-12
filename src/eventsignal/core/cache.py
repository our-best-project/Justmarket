"""端點層的進程內 TTL 快取（② API 層專用）。

歸位路徑：src/eventsignal/core/cache.py

【為什麼是進程內快取，不是 HTTP Cache-Control / ETag】
成本在與 Neon 的網路來回，不在 JSON 序列化。要算出 ETag 就得先查完 DB，
擋不掉真正的瓶頸；進程內快取擋在打 DB 之前，多人同時看首頁只打一次 DB。
被否決的替代方案見 docs/superpowers/specs/2026-07-31-api-db-pool-and-cache-design.md。

【並行】FastAPI 對同步端點（本專案端點都是 def 而非 async def）會丟進 threadpool，
所以會被多執行緒並行存取。字典存取以 Lock 保護，但 compute() 刻意在鎖外呼叫——
持鎖計算會讓所有請求排隊等同一次 DB 查詢完成，慢查詢時反而更糟。代價是快取
失效的瞬間可能重複計算數次（cache stampede），最差退化成「和沒有快取一樣」。

【不快取失敗】compute() 拋例外時不寫入快取，否則一次 DB 故障會被凍結整個 TTL。
"""
import os
import threading
import time
from collections.abc import Callable, Hashable
from typing import Any, TypeVar

T = TypeVar("T")

_DEFAULT_TTL_SECONDS = 60.0


def cache_ttl_seconds() -> float:
    """端點快取 TTL（秒）。0 或負數＝停用快取。

    ⚠️ 這要設成**真正的環境變數**（啟動 uvicorn 前 export/set），不能寫在 .env——
    快取物件在模組 import 時建立，早於 db_url() 觸發 .env 載入。
    demo 時想立刻看到 DB 改動就設 API_CACHE_TTL_SECONDS=0。
    """
    raw = os.environ.get("API_CACHE_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS))
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_TTL_SECONDS


class TTLCache:
    """鍵值快取，每個鍵各自計時。ttl_seconds <= 0 等於停用。"""

    def __init__(self, ttl_seconds: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        # clock 可注入 → 測試推進假時鐘即可，不必真的 sleep
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[Hashable, tuple[float, Any]] = {}

    def get_or_compute(self, key: Hashable, compute: Callable[[], T]) -> T:
        if self._ttl <= 0:
            return compute()

        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and self._clock() - entry[0] < self._ttl:
                return entry[1]

        value = compute()   # 刻意在鎖外，見模組 docstring

        with self._lock:
            # 計時從「值可用」的那一刻起算，而不是從查詢開始
            self._entries[key] = (self._clock(), value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
