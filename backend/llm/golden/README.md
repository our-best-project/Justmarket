# Golden Set 標註規則（T23 分類準確率驗證）

> 兩人**各自獨立**填 `annotations_A.json` / `annotations_B.json`，填完前不要對答案。
> 工具用法見 `backend/llm/golden_set.py` 檔頭。
> 標註依據 = `docs/spec/07_Prompt範本.md` **v2** 的定義（本檔是它的標註者摘要版）。

## 每題填四個欄位

| 欄位 | 合法值 | 一句話 |
|---|---|---|
| `categories` | 9 類多選，至少 1 個 | 這則新聞是哪（幾）類事件 |
| `status` | `official_confirmed` / `rumor_unconfirmed` / `developing` / `market_reacting` | 消息確認度 |
| `expected_direction` | `利多` / `利空` / `中性` | 事件若成真，通常推股價往哪邊 |
| `direction_confidence` | `high` / `low` | 方向明確標 high；依個股/情境而定或模糊標 low |

## 9 類與消歧（v2 重點）

財報＝已公布的季/年數字｜營收＝**單月**營收｜法說＝**法說會/業績發表會場合**或正式財測
（⚠️ 建廠、增資、投資、併購「規劃宣布」**不是法說**，多屬產業供需）｜政策＝法規/央行/補助｜
關稅＝可同標政策｜AI/科技＝主詞是產業/趨勢｜技術突破＝主詞是特定公司＋具體成果（可與 AI/科技同標）｜
產業供需＝供應鏈/產能/上下游｜法人動向＝三大法人**實際**買賣超、外資評等/目標價
（⚠️「法人預估/看好」只是觀點，**不算**）。

## status 判準（v2 重點）

- `official_confirmed` **只看來源類型**：本 golden set 題目全部來自鉅亨網（media），
  所以理論上**沒有任何一題**該標 official_confirmed——除非團隊對下面待定規則另有決議。
- 媒體轉述公司公告/說法 → `developing`（事件在演進）或 `rumor_unconfirmed`（純傳聞）。
- 盤勢/資金流向的「市場正在反應」→ `market_reacting`。

## ⚠️ 兩條待團隊定案的規則（標註前先跟 Haoche 對齊，定案後補進這裡）

1. **體系外新聞的歸屬**：併購/IPO/上市、大盤行情、獲獎、人事——9 類沒有明確的家。
   暫行方案：標「最接近的一類」（多為產業供需），不硬湊多標籤。→ 定案：＿＿＿
2. **政府動作經媒體報導的 status**：「金管會核准 XX」由鉅亨報導，標 `official_confirmed`
   還是 `developing`？暫行方案：照 v2 字面（media 來源 → 不標 official_confirmed）。→ 定案：＿＿＿

## 注意

- `content` 已截前 500 字＝模型看到的輸入。**只根據看得到的內容標**，不要腦補查資料。
- 標不下去的題目先照暫行方案標，並記下題號帶去討論——不要留空（工具會擋）。
- 定案後的 `golden_final.json` 同時是方案 G 的訓練資料，請保持格式乾淨。
