"""管線健康記分板：每輪結束後驗證「不變量」，空轉再也不能裝成功。

【為什麼需要】一週內兩次「零產出被當成功」：
  8/10  LLM 段選中 1,105 個事件、實際處理 0 個（TTL 誤殺）——log 印「成功 0、失敗 0」照樣走完
  8/10~ 向量化步驟自腳本缺失，②③ 每輪空轉——log 印警告後照樣「管線結束」
共同根因：每一段只回報自己「有沒有跑」，沒有人驗證「該發生的事有沒有發生」。
本模組把跨段的不變量集中成一張記分板，接在每日管線最後執行。

【設計】只讀不寫、報告不中斷（健康檢查弄死管線是本末倒置）。
每項檢查回傳 OK/WARN/ALARM；有 ALARM 時 exit code 2、WARN 時 1，
讓外層腳本與 log 搜尋（grep ALARM）都抓得到。

  python -m eventsignal.pipeline_health          # 隨時可跑，不限於管線尾
"""
import sys
from datetime import date, datetime, timedelta, timezone

from eventsignal.core import config

TAIPEI = timezone(timedelta(hours=8))

OK, WARN, ALARM = "OK", "WARN", "ALARM"


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5


def check(conn) -> list[tuple[str, str, str]]:
    """回傳 [(等級, 檢查名, 說明)]。等級：OK / WARN / ALARM。"""
    now_tw = datetime.now(TAIPEI)
    today = now_tw.date()
    weekday = _is_weekday(today)
    out: list[tuple[str, str, str]] = []
    cur = conn.cursor()

    def add(level, name, msg):
        out.append((level, name, msg))

    # ── 交接棒不變量 ──────────────────────────────────────────
    cur.execute("select status, count(*) from articles group by 1")
    st = {s: n for s, n in cur.fetchall()}

    pending = st.get("pending", 0)
    add(WARN if pending > 800 else OK, "pending 排空",
        f"{pending} 篇（每輪跑完應降到低位；持續累積＝②向量化沒在做事）")

    vec = st.get("vectorized", 0)
    add(WARN if vec > 0 else OK, "vectorized 不滯留",
        f"{vec} 篇（>0 表示 ② 跑了但 ③ 分群沒接手）")

    cur.execute("select count(*) from events where title is not null and importance_stars is null")
    stuck = cur.fetchone()[0]
    add(ALARM if stuck > 0 else OK, "摘要必有星等",
        f"{stuck} 個事件已摘要未評分（應恆為 0——評分段全域收尾保證）")

    clustered = st.get("clustered", 0)
    add(WARN if clustered > 4000 else OK, "clustered 待選區",
        f"{clustered} 篇（未達門檻屬正常；無限增長＝篩選/TTL 停擺）")

    # ── 每日產出（平日才有硬要求） ────────────────────────────
    cur.execute("""select count(*) from articles
                   where fetched_at > now() - interval '24 hours'""")
    fetched = cur.fetchone()[0]
    lvl = ALARM if (weekday and fetched == 0) else (WARN if (weekday and fetched < 100) else OK)
    add(lvl, "爬蟲有產出", f"近 24h 抓入 {fetched} 篇（平日 <100 可疑、=0 異常）")

    cur.execute("""select count(*) from events
                   where title is not null
                     and updated_at > now() - interval '24 hours'""")
    new_events = cur.fetchone()[0]
    lvl = ALARM if (weekday and new_events == 0) else OK
    add(lvl, "LLM 有產出", f"近 24h 摘要/更新 {new_events} 個事件（平日 =0 ＝ ⑤ 空轉）")

    # ── 資料新鮮度 ────────────────────────────────────────────
    cur.execute("select max(date) from chip_data")
    chip_max = cur.fetchone()[0]
    stale = (today - chip_max).days if chip_max else 999
    add(WARN if stale > 4 else OK, "籌碼新鮮度",
        f"chip_data 最新 {chip_max}（{stale} 天前；含週末緩衝 4 天）")

    cur.execute("select max(date) from market_index_daily where index_code='TAIEX'")
    idx_max = cur.fetchone()[0]
    stale = (today - idx_max).days if idx_max else 999
    add(WARN if stale > 4 else OK, "指數新鮮度", f"TAIEX 最新 {idx_max}（{stale} 天前）")

    # ── 資料品質 ──────────────────────────────────────────────
    cur.execute("""select round(100.0 * count(*) filter
                     (where foreign_net=0 and trust_net=0 and dealer_net=0)
                     / nullif(count(*),0), 1)
                   from chip_data where date = (select max(date) from chip_data)""")
    zero_pct = float(cur.fetchone()[0] or 0)
    add(WARN if zero_pct > 15 else OK, "假零值比例",
        f"最新交易日三大法人全零比 {zero_pct}%（基線 6–10%，>15% 疑半套資料）")

    return out


def main() -> None:
    import psycopg

    # Windows cp950 主控台印不出 ⚠/★ 會 UnicodeEncodeError——記分板不准死在排版上
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    config.load_env()
    with psycopg.connect(config.require("DATABASE_URL")) as conn:
        results = check(conn)

    width = max(len(name) for _, name, _ in results)
    worst = 0
    print("═" * 62)
    print(f"管線健康記分板  {datetime.now(TAIPEI):%Y-%m-%d %H:%M} 台北")
    print("═" * 62)
    for level, name, msg in results:
        mark = {"OK": "  OK  ", "WARN": "⚠ WARN", "ALARM": "★ALARM"}[level]
        print(f"[{mark}] {name:<{width}}  {msg}")
        worst = max(worst, {"OK": 0, "WARN": 1, "ALARM": 2}[level])
    print("═" * 62)
    if worst:
        print(f"結論：{'有異常需要處理' if worst == 2 else '有警告值得看一眼'}（grep ALARM/WARN 可定位）")
    else:
        print("結論：全部通過")
    sys.exit(worst)


if __name__ == "__main__":
    main()
