# finmind — 籌碼每日批次

每日盤後從 **FinMind** 抓三大法人買賣超、股價、成交量，算好衍生欄位落進 `chip_data`，
給 `../scoring/market_validation.py` 當佐證。這條是旁支，不進向量化、不進事件管線。

## 免費層的全量設計

FinMind 免費註冊層（register）就夠用，不需要付費方案。做法：

- 全池 2,417 檔**逐檔**抓（單檔 + 日期區間），撞到額度就等 55 分鐘再續。
- 全量一輪約 4,800 次請求、8–9 小時 —— 掛 18:10 那班，隔天早上跑完。
- **優先隊列**：事件關聯股 ∪ 熱門池排最前。中途斷掉也不影響當天的評分。
- **續抓天然免費**：逐檔以 `max(date)` 判斷新鮮度、每 25 檔 commit，
  重啟後已完成的檔直接跳過。

`--demand-only` 是輕量模式，只跑需求集（30–130 次請求），每日管線用的就是它。

## 衍生欄位

在評分之前先算好，讓分數可以拆解：

- 連續買／賣超天數
- 淨額占近 20 日均量比
- 量比（對 20 日均量）
- 1／3／5 日報酬
- 20 日波動 σ

## D0 怎麼定

`resolve_d0()`：事件發生在台北 13:30 收盤**前** → 當日；收盤**後** → 次日。
週末與休市由 `chip_data` 的實際交易日自然吸收。

## 怎麼跑

需要 `DATABASE_URL` 與 `FINMIND_TOKEN`（個人的，免費註冊即有）。

```bash
uv run python -m eventsignal.finmind.daily_batch --verify 2330:2026-07-03
```

```bash
uv run python -m eventsignal.finmind.daily_batch --ticker 2330 --dry-run
```

```bash
uv run python -m eventsignal finmind
```

冪等，重跑無害；額度將盡會自動中止，下一班續跑。

## jobs.py

`daily_after_market_job()` 是完整的盤後流程：籌碼增量 → 重評尚未 verified 的事件
（分數隨報酬視窗到期而更新）。冪等、例外全捕捉不外拋。
正式排程由 `../orchestration/` 的 Prefect flow 呼叫，不再自己掛 scheduler。

手動觸發：

```bash
uv run python -m eventsignal.finmind.jobs
```

FinMind 盤後資料偶爾延遲（實例：2026-07-10 當天無資料）。增量批次遇到「無新日期」
會直接結束，所以多排一班補撿是安全的。
