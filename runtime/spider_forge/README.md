# Spider Forge runtime

這是 Spider Forge 唯一的可變資料根目錄。Docker 內對應
`/data/spider_forge`，不把執行產物寫回原始碼。

```text
requests/urls.txt                 使用者輸入；每行一個 URL
runs/<run_id>/pipeline.log        該次完整控制面 log
runs/<run_id>/sandbox.stdout.log  candidate stdout
runs/<run_id>/sandbox.stderr.log  Scrapy log／錯誤
runs/<run_id>/items.json          sandbox 抓到的資料
runs/<run_id>/result.json         最終結構化結果
artifacts/candidates/             各 run 的候選 spider
artifacts/active/                 通過所有 gate 的最新 spider
artifacts/versions/               promotion 前的舊版本
records/runs.jsonl                跨 run 實況帳本
records/promotions.jsonl          promote／rollback 帳本
records/dead_letter/              失敗與人工處理證據
models/                           選用的離線 topic artifacts
```

`urls.txt` 是個人執行輸入，不進 Git。第一次使用：

```powershell
Copy-Item .\runtime\spider_forge\requests\urls.example.txt `
  .\runtime\spider_forge\requests\urls.txt
notepad .\runtime\spider_forge\requests\urls.txt
```
