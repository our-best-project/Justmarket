"""事件示意圖挑選（分類池 + event_id 確定性雜湊）。

【為什麼不進資料庫】
輪替不需要儲存：`sha1(event_id) % len(pool)` 就能做到「同一事件永遠同一張圖」，
零 schema 改動、零回填、新事件自動有圖。存一張對應表反而要維護「新事件誰配圖」。
要為特定事件指定特定圖時，才在 events 加 nullable 的 image_key（有值優先、沒值走 hash）。

【為什麼是 sha1 不是內建 hash()】
Python 的 `hash(str)` 受 PYTHONHASHSEED 影響，每次行程重啟結果都不同 —— 那會變成
「重整頁面就跳圖」，正是這個設計要避免的。sha1 跨行程、跨機器穩定。
（這裡不是密碼學用途，sha1 夠用。）

【已知限制】
目前只有 5 張圖、10 個分類，多數池只有 1-2 張，輪替效果很弱（同分類事件多半拿到同一張）。
補圖只要把檔案放進 static/images/ 並在 manifest.json 的 images 加一筆，程式不用動。
"""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

# app/services/image_picker.py → app/static/images/
IMAGES_DIR = Path(__file__).resolve().parent.parent / "static" / "images"
MANIFEST = IMAGES_DIR / "manifest.json"

# 對外的 URL 前綴，對應 main.py 的 app.mount("/static", ...)
URL_PREFIX = "/static/images"


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, list[dict]], dict[str, str], str]:
    """讀 manifest 並展開成 (池名→圖清單, 分類→池名, 預設池名)。

    lru_cache：manifest 是啟動後就不變的靜態設定，不必每個請求重讀。
    改了 manifest 要重啟 API 才會生效（--reload 會自動重啟）。
    """
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))

    pools: dict[str, list[dict]] = {}
    for img in raw["images"]:
        for pool_name in img["pools"]:
            pools.setdefault(pool_name, []).append(img)

    # 池內依檔名排序：讓 hash→索引的對應不受 manifest 中的書寫順序影響，
    # 否則有人在 images 陣列中間插一筆，所有事件的配圖就會整批位移。
    for pool in pools.values():
        pool.sort(key=lambda i: i["file"])

    return pools, raw["category_to_pool"], raw["default_pool"]


def pick_image(event_id: str, categories: list[str] | None, origin: str = "") -> dict | None:
    """回傳 {url, alt, credit, source_url}；找不到可用的圖回 None。

    origin：API 自己的來源（例 "http://localhost:8000"），用來組出絕對 URL。
    前端跑在別的 port（4175），拿到相對路徑會去它自己的 host 找圖而 404，所以必須絕對。
    傳空字串則回相對路徑（同源部署時適用）。

    前端對 image 為 null 有處理（frontend/ 前端直接回空字串、
    列表列退回序號方塊），所以回 None 是安全的，不會破版。
    """
    pools, cat2pool, default_pool = _load()

    # 依 manifest 中 category_to_pool 的「書寫順序」決定優先，不是事件 categories 的順序。
    # 實測教訓：真實事件的 categories 幾乎都是 ["財報","法說","AI/科技",...] —— 財報永遠排在前面，
    # 若取「事件的第一個分類」，半導體池永遠選不到，圖會全部擠在 finance/industry。
    # manifest 把特異性高的分類（生技、AI/科技）寫在前面，泛用的（財報、營收）寫在後面。
    cats = set(categories or [])
    pool_name = next(
        (pool for cat, pool in cat2pool.items() if cat in cats),
        default_pool,
    )
    pool = pools.get(pool_name) or pools.get(default_pool) or []
    if not pool:
        return None

    digest = hashlib.sha1(event_id.encode("utf-8")).hexdigest()
    img = pool[int(digest, 16) % len(pool)]

    return {
        "url": f"{origin}{URL_PREFIX}/{img['file']}",
        "alt": img["alt"],
        "credit": img["credit"],
        "source_url": img["source_url"],
    }
