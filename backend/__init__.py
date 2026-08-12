"""backend —— 就市論事 Justmarket 的後端套件。

一條主線，五個處理段，兩個對外面：

    crawler/（Scrapy）→ ingestion → embedding → clustering → llm → scoring
                                                                      ↓
                                              api（只讀 FastAPI）→ frontend/

各段之間不直接傳資料，一律用 `articles.status` 當交接棒：
`pending → vectorized → clustered → summarized → scored`。
每段只處理上一格狀態的資料、做完推進下一格 —— 當機可重跑、單段可重跑。

子套件對照（資料夾名就是 import 名）：

| 模組 | 負責 | 不負責 |
|---|---|---|
| `api` | HTTP 只讀端點 | 任何運算；分數在管線算好存庫 |
| `core` | 設定載入、進程內 TTL 快取 | 業務邏輯 |
| `db` | 連線與連線池 | SQL 語句（各模組自帶） |
| `ingestion` | 執行 Scrapy、驗證、標 ticker、冪等寫入 articles | 抓網頁本身（那是 crawler/） |
| `embedding` | 標題+前段 → BGE-M3 1024 維 | 相似度判斷 |
| `clustering` | 向量 → 事件（去重聚類） | 事件內容生成 |
| `llm` | 摘要、九類分類、方向、status | 分數 |
| `scoring` | 重要性 ★1–5、市場驗證 0–100 | 買賣建議（本系統一律不給） |
| `finmind` | 三大法人／股價／量的每日批次 | 評分規則 |
| `market_index` | 八國大盤日線 | 個股 |
| `orchestration` | Prefect 三條每日 flow | 步驟內容 |
| `pipeline` | 把上面各段串成一輪 | 排程時機 |

入口只有一個：`python -m backend --help`。
"""
