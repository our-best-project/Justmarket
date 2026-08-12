# embedding — 向量化

文章標題（＋前段內文）→ BGE-M3 → **1024 維向量**，寫回 pgvector 欄位。

## 定案參數

| 項目 | 值 | 為什麼 |
|---|---|---|
| 模型 | `BAAI/bge-m3` | 繁中表現足夠，且同一模型涵蓋中英混雜的財經用語 |
| 維度 | 1024 | 模型原生輸出，不降維 |
| 輸入 | 標題 + 內文前 500 字 | 財經新聞的重點集中在前段；截斷同時大幅加速 CPU 推論 |

參數定了就寫死。聚類閾值是對著這組參數調出來的，改任何一個都要重跑
`clustering/golden_eval.py` 才知道有沒有退步。

## 怎麼跑

```bash
uv run python -m eventsignal embed
```

讀 `articles.status='pending'` → 算向量 → 寫回 → 推進 `vectorized`。

參數：`--device cuda|cpu|auto`、`--batch N`、`--source db|json`。
`--source json` 是離線模式，讀 `data/cnyes_news_2026.json`，不碰資料庫。

模型權重不進 repo，由 sentence-transformers 自己快取到家目錄
（容器內是 `HF_HOME`，掛在 `huggingface-cache` volume）。

## 產物

`out/`（gitignore）：向量快取 `.npy` 與對齊用的中繼資料，以 `article_id` 對齊。
重算一次 CPU 約 40 分鐘，所以快取值得留著。

## 下一棒

`../clustering/` 從 `status='vectorized'` 接手。
