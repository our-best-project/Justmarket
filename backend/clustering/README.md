# clustering — 去重聚類

把講同一件事的多篇文章收斂成**一個事件物件**。整個產品最核心的一步：
使用者要的是「今天有 21 件事」，不是「今天有 567 篇報導」。

## 方法

讀 `status='vectorized'` → 週期性欄目過濾 → **Agglomerative（average linkage，
cos 相似度 > 0.65）** → **日期閘門**（群內相鄰成員日期差 > 3 天就拆）→
**ticker 主題閘門**（成員代號交集為空不合併）→ 組成事件物件 → 推進 `clustered`。

兩道閘門不是額外的保險，是必要條件：純語意相似會把「同主題但不同事件」併在一起
（同一家公司兩週前後的兩次法說會、同題材不同公司的兩則新聞）。

## 為什麼是這個方法（實測翻案紀錄）

| 版本 | 方法 | golden F1 |
|---|---|---|
| 原規格 | cos > 0.65 + 連通分量 | **0.043** — 傳遞鏈接讓全量資料崩潰成一個巨群 |
| 二版 | HDBSCAN | **0.224** — 誤合逐月家族（同一系列的月報被當成同一事件） |
| 現行 | Agglomerative(average) + 雙閘門 | **0.914**（standalone 0.977） |

演進與 benchmark 見 [`REPORT.md`](REPORT.md) §4。

黃金集 `golden_set.json`：64 篇 / 40 事件，刻意涵蓋兩種陷阱（同公司不同事件、
同題材不同公司）。評測：

```bash
uv run python -m backend.clustering.golden_eval
```

⚠️ 早期目標值 0.83 出自較簡單的標註版本，數字不可跨版比較。

## 週期性欄目不會被靜默丟棄

`assemble_events` 回傳 `ClusterResult(events, filtered)`，`filtered` 逐篇帶
`(article_id, periodic_type)`。呼叫端要據此把這些文章推進 `periodic` —— 否則它們會
永遠停在 `vectorized`，每輪重跑都被重撈一次。離線模式下管線會先寫出
`out/periodic_filtered.jsonl`。

## 產物

`out/`（gitignore，可由 `uv run python -m backend pipeline` 重建）：
`event_objects.jsonl`（最終事件物件）、`periodic_filtered.jsonl`（被濾文章的交接清單）。

## 規則

- **保留所有來源連結**：事件是多篇報導的收斂，不是取代。
- 群內疑似有獨家數字的那篇標 `has_unique_detail`。

## 下一棒

`../llm/` 從 `status='clustered'` 接手。
