# RBA 單站逐關人工驗證

日期：2026-07-29

目標：`https://www.rba.gov.au/media-releases/`

來源：澳洲儲備銀行 Media Releases

## 測試規則

- 不執行完整 pipeline；一次只呼叫一個節點。
- 每關先檢查輸出是否真能支撐下一關，不能因資料結構合法就判通過。
- 發現第一個無效、矛盾或不足的材料就停止，修正該關後從該關重跑。
- 第一階段只判斷材料是否足以由人寫出爬蟲；後續經使用者授權，追加單獨
  `generate` 與離線預檢。仍不執行 live sandbox、不升版、不發布 spider。
- 活站內容會變動，因此只保存關鍵證據與判決，不把完整頁面複製進版本庫。

## 請求契約

- `source_prefix`: `rba_media`
- `target_schema`: 官方來源、保存完整公告；不得沿用媒體摘要預設
- `allowed_domains`: `www.rba.gov.au`, `rba.gov.au`
- 文章 URL：`/media-releases/<年份>/mr-<兩位年>-<兩位序號>.html`
- 排除列表頁：`/media-releases/`、`/media-releases/index.html`
- 最少有效文章：5
- 最長文章年齡：365 天
- 來源時區：`Australia/Sydney`
- 主題閘門：本次人工材料審查不執行

## 關卡紀錄

| 關卡 | 狀態 | 人工判決 |
|---|---|---|
| `prepare_request` | 修正後通過 | 第一次因未提供目標契約而得到 `media + summary_only + 6000 字`；修正輸入後為 `official + full + 20000 字`，可支撐後續偵查。 |
| `recon` | 修正後通過 | 第一次出現 Playwright 200、plain HTTP 403，卻標成 `browser_public_ok`。新增對稱分類後重跑，正確得到 `browser_required_http_blocked`，並找到大量符合規則的公告連結。RSS 只有連結、沒有 body，未冒充可重播 feed。 |
| `feasibility_triage` | 修正後通過 | 第一次把「瀏覽器公開成功、plain HTTP 被擋」誤判為 `KILL_js_required`。保留真正純 JS KILL，只對已證實公開瀏覽器路徑回 `FEASIBLE_BROWSER`；重跑後理由與證據一致。 |
| `strategy_decision` | 修正後通過 | 第一次雖選 HTML 路徑，卻輸出 `confidence=85`、顛倒阻擋方向，並把列表頁塞入 `chosen_api`。改為在「無可重播結構化來源＋已有真實文章連結」時確定性選 `dom`，重跑得到 `chosen_api=""`、`confidence=1.0`，且未呼叫 Ollama。 |
| `collect_evidence` | 修正後通過 | 第一次把明細頁的 plain HTTP 403 當成可用樣本；改由已證實的公開瀏覽器路徑取頁。第二次因只找 `<main>` 而漏掉實際的 `[role="main"]`；擴充主內容定位並保存精簡 DOM。第三次發現無時區日期被硬套台灣 `+08:00`；改由請求契約提供 `Australia/Sydney`。最後把已知的瀏覽器傳輸條件移出 `unresolved`、放入 `requirements`。重跑取得兩篇 HTTP 200 明細 DOM、正確日期規則、`requirements=["browser_transport"]`、`unresolved=[]`。 |
| 材料是否足以人工寫 spider | 通過 | 列表可用 `.rss-mr-list article.rss-mr-item a[itemprop="url"]`；明細可用 `.rss-mr-title`、`.rss-mr-date[datetime]`、`.rss-mr-content`；文章 URL 規則、完整正文邊界、來源時區與必須使用瀏覽器傳輸皆有兩篇真實樣本支持。無須依 AXTree 猜 selector，也沒有呼叫產碼模型。 |
| `generate_spider` | 修正後通過 | 第一輪誤用 `CLOSESPIDER_PAGECOUNT=2`、`pytz` 與不必要的 page handle；修正材料排序與契約後，第二輪漏把 Playwright 套到入口 request；補上獨立 `runspider` 與入口／明細傳輸契約後，第三輪仍漏掉部分高階契約。這證明不能靠重抽收斂，應由確定性預檢逐項回饋。 |
| `generation_preflight` | 通過 | AST／契約預檢確認語法、Spider 類別、高階欄位、專案外 import、Playwright handlers/reactor、入口與明細 request、時區依賴及禁止設定；安全修正後 `errors=[]`。 |
| `fixture_test` | 正式重播通過 | 保存的列表 DOM 產生 36 個明細 request，兩篇明細皆產出完整 item，日期為 `+10:00`，正式 runner 回傳 `passed=true`。 |

## 本站結論

- 產碼材料優先順序：可重播的 Network API JSON／RSS → 精簡 DOM → AXTree 輔助。
- RBA 本次沒有取得可重播的 JSON 或 RSS body，因此採 `dom`；AXTree 只用來交叉確認文章、日期與主內容語意。
- 精簡 DOM 保留 tag、class、id、`itemprop`、`datetime` 等 selector 證據，並移除 script、style、svg、iframe、template、事件屬性與 inline style。
- 四輪 DeepSeek 合計使用 43,235 個輸入 token、3,892 個輸出 token；被採用的
  第三輪為 9,529／986。這證明產碼前確定性預檢必須先於 Phase 5，不能靠重抽模型。
- 本次到正式 fixture gate 即停止；沒有 live sandbox、升版或發布 RBA spider。
