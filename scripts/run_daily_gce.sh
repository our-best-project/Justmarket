#!/bin/bash
# EventSignal 每日盤後管線（GCE 排程開機後由 startup-script 呼叫）。
#
# 【兩個關鍵設計決定】
# 1. FinMind 用 --demand-only 而非全量：只抓「未定案事件的股票 ∪ 權值股熱門池」。
#    全量 2,500 檔 × 2 請求 ÷ 580/視窗 ≈ 8 小時（大半在等額度），每天燒 8 小時 VM
#    不划算；而市場驗證分數只需要「有事件的股票」新鮮即可——這正是 daily_batch
#    當初設計 --demand-only 的用意。要全量補歷史時再手動跑一次即可。
# 2. 跑完立刻關機：VM 按秒計費。排程只負責開機，關機由本腳本自己負責，
#    這樣「跑多久算多久」，不會因為排程停機時間抓太寬而白燒。
#
# 安全網：即使本腳本異常中斷，GCE 排程仍有每日 06:00 強制停機（見 resource-policy）。

cd ~/es || exit 1

# 不可直接 `. ./.env`：Neon 連線字串含 ? 與 &，bash source 會截斷
~/es/.venv/bin/python - <<'GENENV' > ~/es/.env.sh
import pathlib
out = []
for line in pathlib.Path(".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    out.append(f"{k.strip()}='" + v.strip().replace("'", "'\\''") + "'")
print("\n".join(out))
GENENV
set -a; . ~/es/.env.sh; set +a
export PYTHONUTF8=1
# 節流：預設 3 秒是為 AI Studio 免費層的 RPM（15–30/分）設計的；Vertex AI 走
# GCP 配額，上限高得多，3 秒純屬浪費（實測每事件 7.4 秒中有 3 秒在乾等）。
# 降到 0.5 秒約提升 2 倍吞吐、VM 時數同步減半；撞到 429 時 client 本身有
# 指數退避重試接住，安全。品誠的 docstring 也註明「排程層另有節流時可調 0」。
export LLM_SLEEP_SECONDS=0.5
PY=~/es/.venv/bin/python
LOG=~/es/daily.log

echo "════════ $(date '+%F %T %Z') 每日盤後管線啟動 ════════" >> "$LOG"

# 前置驗證：連不上就別空跑，但**仍要關機**（否則 VM 整晚空轉）
if ! $PY -c "import os,psycopg; psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=30).close()" >> "$LOG" 2>&1; then
    echo "❌ Neon 連線失敗，中止" >> "$LOG"
    sudo shutdown -h +1
    exit 1
fi

# ── 五段全包（2026-08-06 起）：原本 ①②③ 在組長機器上，那台沒開整條管線就
#    安靜停止產出。整併到這台後不再依賴任何人的電腦，也沒有跨機器的時序協調問題。
#
# 順序不可調換：爬蟲產出 pending → 向量化轉 vectorized → 分群轉 clustered
# → LLM 轉 summarized → 評分轉 scored。每段只處理上一段的產出。

# ── 全部步驟由 backend/daily.py 調度（版控內、有守護測試） ─────────
# ⚠️ 不要在這裡加步驟。這支殼腳本曾漏掉向量化步驟導致 ②③ 每輪空轉
# （2026-08-11 事故）——步驟清單活在 backend/daily.py 的 STEPS，
# tests/test_daily_steps.py 斷言其完整性；殼腳本只負責環境與開關機。
$PY -u -m backend daily >> "$LOG" 2>&1
echo "── backend daily 全步驟完成 $(date '+%T')" >> "$LOG"

$PY - >> "$LOG" 2>&1 <<'STATS'
import os, psycopg
conn = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30)
q = lambda s: conn.execute(s).fetchone()[0]
print("今日結束狀態：")
print("  已 LLM 事件:", q("select count(*) from events where title is not null"))
print("  待 LLM:", q("""select count(*) from events e where e.title is null
      and exists(select 1 from articles a where a.event_id=e.event_id and a.status='clustered')"""))
print("  前端可渲染:", q("""select count(*) from events where title is not null
      and categories is not null and importance_stars is not null
      and related_tickers is not null and jsonb_array_length(related_tickers)>0"""))
print("  chip_data 最新交易日:", q("select max(date) from chip_data"))
conn.close()
STATS

echo "════════ $(date '+%F %T') 完成，1 分鐘後關機 ════════" >> "$LOG"
sudo shutdown -h +1
