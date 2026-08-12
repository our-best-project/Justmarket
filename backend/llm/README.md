# llm — 摘要、分類、方向

對每個事件**一次 LLM 呼叫**，輸出：

- 摘要
- 九類分類（多標籤）
- 預期方向（利多／利空／中性）＋ `direction_confidence`（high／low）
- 事件 status（消息確認度，如 `official_confirmed`）
- `confidence_note`、`occurred_at_text` / `occurred_at_iso`

`direction_confidence` **不能漏**：它是 `../scoring/market_validation.py` 一致性閘門的
開關，`low` 時不給驗證分數。`expected_direction` 標反會讓驗證結論整個相反，
抽檢重點放這裡。

## 兩個 status 不要搞混

本模組是唯一同時碰到兩者的地方：

| | 是什麼 | 值 |
|---|---|---|
| `articles.status` | 管線交接棒 | 讀 `clustered`，做完推進 `summarized` |
| 事件 `status` | 消息確認度，是 LLM 的輸出欄位之一 | `official_confirmed` 等 |

## 檔案

| 檔案 | 是什麼 |
|---|---|
| `prompts.py` | prompt 資產、輸出 schema、驗證。schema 的唯一來源 |
| `client.py` | Gemini／OpenAI／Vertex 同介面：Structured Output、429 退避、金鑰載入 |
| `summarize.py` | 管線步驟：純函式批次層 + DB 寫回層 |
| `samples.py` | 三則手測樣本事件（不同難度），兼回歸題庫 |
| `golden_set.py` | 分類準確率評測：抽題 → 兩人標註 → 一致率 → 定案 → micro-F1 |
| `regression.py` | 回歸案例跑分 |
| `golden/` | 題目與標註（標註規則見其 README） |

## 怎麼跑

```bash
uv run python -m backend.llm.prompts
```

```bash
uv run python -m backend.llm.summarize --self-test
```

上面兩個免金鑰、免網路，改完程式先跑它們。要打真 API：

```bash
uv run python -m backend.llm.client
```

正式管線步驟（需 `DATABASE_URL`）：

```bash
uv run python -m backend pipeline --stages llm
```

## 環境變數

| 變數 | 說明 |
|---|---|
| `LLM_PROVIDER` | `gemini`（預設）｜`openai`｜`vertex`（production） |
| `LLM_API_KEY` | vertex 時可留空，改用 `GCP_PROJECT` |
| `LLM_MODEL` | 覆蓋預設模型 |
| `LLM_SLEEP_SECONDS` | 免費層節流間隔，預設 3 秒。排程層若已做全域節流就設 0，別雙重節流 |

⚠️ `vertex` 走 `google-auth`。那個套件沒裝的話會默默退回 gcloud CLI，
連 gcloud 都沒有才會失敗 —— 所以它是明列的相依套件，不是可選的。

## 設計前提

媒體來源只給標題＋導言／摘要，**不是全文**（著作權界線，見 `../crawler_legacy/README.md`）。
prompt 是照這個現實設計的，換成全文輸入要重新校準。

## 下一棒

`../scoring/` 從 `status='summarized'` 接手。
