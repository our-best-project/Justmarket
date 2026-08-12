# 人工驗證候選 Spider

這裡集中保存逐關人工驗證後，方便審閱與比較的獨立 spider。

這些檔案：

- 不會被正式 crawler runtime、排程或 catalog 自動載入。
- 不依賴 Spider Forge 專案內模組，可用 `scrapy runspider` 獨立執行。
- 已通過確定性預檢與保存材料的離線驗證。
- 尚未通過 live sandbox、升版與發布，因此不是正式啟用版本。

## 候選

| 檔案 | 來源 | 離線驗證 | 詳細紀錄 |
|---|---|---|---|
| `rba_media_candidate.py` | 澳洲儲備銀行公告 | 36 個明細請求，2/2 DOM 樣本通過 | `../RBA_STAGE_WALKTHROUGH.md` |
| `boc_press_candidate.py` | 加拿大央行新聞稿 | 10 個明細請求，2/2 DOM 樣本通過 | `../BOC_STAGE_WALKTHROUGH.md` |
