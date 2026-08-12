# scoring — 重要性 ★1–5 與市場驗證 0–100

兩個**獨立**的數字。鐵律只有一條：**每個分數都要能拆解出理由**，不能只給星等。
分開的理由見 [`docs/decisions.md`](../../docs/decisions.md) 第 4 條。

## importance.py — 重要性 ★1–5（排序用）

五維加權：

| 維度 | 權重 |
|---|---|
| 來源廣度 | 25% |
| 來源權威 | 20% |
| 影響範圍 | 25% |
| 事件類型 | 15% |
| 新穎程度 | 15% |

加權後對映星等，並回傳 `importance_reasons` —— 每一顆星都要說得出是哪幾維撐起來的。

## market_validation.py — 市場驗證 0–100（市場信不信）

三個訊號，各自算「一致性 × 強度」：

- **法人**：±25（三大法人買賣超方向與 `expected_direction` 是否一致）
- **股價**：±20（報酬 z 分數方向）
- **成交量**：不獨立計分，當**放大係數**

然後兩道後處理：

1. **一致性閘門**：法人與股價方向相反時，把分數壓回 40–55。
   市場自己都沒共識，就不該給出高信心的數字。
2. **狀態機**：觀察中（D0）→ 初步（D+1~3）→ 已驗證（D+5）。
   報酬視窗要時間到期，D0 當天給不出結論是事實，不是缺陷。

`direction_confidence = low` 時略過閘門、只列原始數據。前端顯示一律附
「描述已發生反應，非預測、非機率」。

## prescreen.py — 篩選層

在 LLM 之前先擋掉不值得處理的事件，省 token 也省雜訊。

## 怎麼跑

```bash
uv run python -m backend pipeline --stages scoring
```

單獨跑某一支：

```bash
uv run python -m backend.scoring.market_validation
```

## 驗收方式

- 同一事件跨天的分數要平滑（不能 D+1 是 80、D+3 掉到 20）。
- 分歧閘門要抓得到已知的背離案例（法人大買但股價跌）。
- 參數敏感度：`scripts/t24_sensitivity.py` 對全市場掃過各參數的擾動，
  看 Spearman 相關與同帶率。分數對單一權重過度敏感就是調過頭了。

## 上下游

讀 `status='summarized'` 的事件與 `../finmind/` 落庫的 `chip_data`，
做完把 articles 推進 `scored`。這是管線最後一段。
