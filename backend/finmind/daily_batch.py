"""每日盤後批次（免費版·需求驅動）：只抓「有需求的股票」→ 算衍生欄位 → upsert chip_data。

⚠️ 設計前提：FinMind 帳號為 register（免費），**不續訂 Backer**。
  - 全市場查詢（不帶 data_id）→ 400，不可用；單檔＋日期區間查詢正常。
  - 額度帶 token 約 600 次/小時。

策略（0719 組長定案）：**全量更新＋撞額度等 55 分鐘續抓**
  - 目標池 = chip_data 既有全部代號（2,417 檔，含 ETF）；每檔 2 次請求
    （價量、三大法人，各一次日期區間拉滿）→ 全量一輪 ≈ 4,800 次請求。
  - **優先隊列**：未 verified 事件的股票 ∪ HOT_POOL 排在最前面——
    就算中途斷掉，評分要用的股票已經是新鮮的。
  - 額度處理（雙保險）：①主動——視窗內用量逼近 WINDOW_BUDGET 就睡
    WAIT_MINUTES 再繼續；②被動——收到 RateLimitError（429/402上限）同樣睡了重試同一檔。
  - 續抓天然免費：逐檔以 max(date) 判斷新鮮度，重啟後已完成的檔 0 請求跳過；
    每 25 檔 commit 一次，中斷最多重抓 25 檔。
  - 「新鮮」的比較基準是**最後交易日**（resolve_last_trading_day），不是系統日期——
    週末／連假跑批次時才不會把已經最新的股票全部誤判成待更新（詳見該函式）。
  - 全量一輪估時：4,800 請求 ÷ 580/視窗 ≈ 9 個視窗 ≈ 8～9 小時（含等待），
    掛 18:10 班隔天早上跑完；`--demand-only` 保留舊的輕量模式（30~130 次）。

衍生欄位慣例（與歷史全量逐列驗證一致，勿改）：
  - foreign/trust/dealer_net = buy-sell，單位股
  - foreign_consecutive_days = 外資淨額正負號連續天數（正連買、負連賣、0 無）
  - net_vs_avg20_volume_pct = (外資+投信+自營淨額) ÷ 近20日均量
  - volume_ratio_vs_avg20   = 當日量 ÷ 近20日均量（含當日滾動20日）
  - return_1d/3d/5d = 前向報酬 close[t+N]/close[t]-1（未到期 NULL → 狀態機）
  - sigma_20d = 近20筆日報酬樣本標準差（stdev, ddof=1；需21個收盤）

用法：
  python -m backend.finmind.daily_batch --verify 2330:2026-07-03  # 驗科目對應(單檔,免費可跑)
  python -m backend.finmind.daily_batch --ticker 2330 --dry-run    # 單檔試跑不寫入
  python -m backend.finmind.daily_batch                            # 正式：全量(撞額度等55分續抓)
  python -m backend.finmind.daily_batch --demand-only              # 輕量：只跑需求集
"""
from __future__ import annotations

import argparse
import os
import re
import statistics
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .client import FinMindClient, RateLimitError, aggregate_institutional

WARMUP_DAYS = 55            # 回看天數（曆日），供 avg20/sigma/連續天數續接
                            # （55 曆日 ≈ 37+ 交易日：即使連假也保證回填列有完整 21 根收盤算 sigma）
RETURN_BACKFILL_DAYS = 8    # 回填最近幾個交易日的前向報酬
WINDOW_BUDGET = 580         # 每視窗請求上限（register 約 600/hr，留安全邊際）
WAIT_MINUTES = 55           # 撞額度後等待分鐘數（組長定案），等完視窗歸零繼續
COMMIT_EVERY = 25           # 每 N 檔 commit 一次（中斷最多重抓 N 檔）
NEW_TICKER_HISTORY_DAYS = 90  # 不在 chip_data 的新檔：暖啟動抓近 90 曆日

# 熱門池：無事件也每日保鮮的權值股（與 scoring_Bright.importance CONFIG 的
# heavyweights 同清單，變動請兩邊同步）。事件股票由 events 表動態加入。
HOT_POOL = ["2330", "2317", "2454", "2308", "2382", "2412", "2881", "2882",
            "2891", "3711", "2303", "2886", "2884", "1216", "2002"]

TICKER_RE = re.compile(r"^[1-9]\d{3}$")  # 普通股（濾 ETF/權證）

# ---------- 目標交易日 ----------
TAIPEI = ZoneInfo("Asia/Taipei")
DATA_READY_HOUR = 16        # 台股 13:30 收盤，但三大法人約 15:00~16:00 才公布。
                            # 早於此時間把「今天」當目標，會拿到有價量、無法人的半套資料，
                            # 而 _process_ticker 對查無法人的日期填 0（見該函式 inst.get 預設值）
                            # → 污染 foreign_consecutive_days。故當日一律等 16:00 後才算數。
PROBE_TICKER = "2330"       # 探針股：用一次請求問「市場最後交易日」
PROBE_LOOKBACK_DAYS = 14    # 探針回看窗（跨得過一般連假）


def _calendar_bound(now: datetime | None = None) -> date:
    """日曆上界＝最晚「可能」有資料的交易日。只處理週末與當日資料未就緒，不含國定假日。"""
    now = now or datetime.now(TAIPEI)
    d = now.date()
    if now.hour < DATA_READY_HOUR:
        d -= timedelta(days=1)
    while d.weekday() >= 5:          # 5=六、6=日
        d -= timedelta(days=1)
    return d


def resolve_last_trading_day(client: FinMindClient) -> tuple[date, str]:
    """回傳 (最後一個應該有資料的交易日, 判定依據)。

    為什麼不用 date.today()：新鮮度判斷是 `db_max >= today`（見 _process_ticker）。
    週日跑批次時 today=週日，所有已抓到週五的股票都會被誤判成「不夠新」，
    各發一次請求去確認「沒有新資料」——2,400 檔就是 2,400 次無效請求（約 4 個視窗、4 小時）。

    日曆規則擋得掉週末，擋不掉國定假日（台股連假逐年不同，寫死清單會過期）。
    所以再花**一次**探針請求問 FinMind 台積電的最後一筆日期——這是市場的實際答案，
    連假、颱風假全自動涵蓋。成本固定 1 次，省下的是上千次。

    探針失敗（額度／網路）→ 退回日曆上界，行為不比原本差。
    """
    bound = _calendar_bound()
    try:
        rows = client.price_stock(
            PROBE_TICKER,
            (bound - timedelta(days=PROBE_LOOKBACK_DAYS)).isoformat(),
            bound.isoformat())
    except Exception as e:
        return bound, f"探針失敗（{type(e).__name__}），退回日曆規則"
    dates = [str(r["date"]) for r in rows if r.get("date")]
    if not dates:
        return bound, "探針查無資料，退回日曆規則"
    probed = date.fromisoformat(max(dates))

    # 第二道探針：價量出了不代表三大法人也出了（實測 2026-08-06 16:10 抓，
    # 價量有、法人無 → 該日 24.3% 的股票被寫進假的 0，且打斷 foreign_consecutive_days；
    # 對照常態只有 7-8%（冷門股真的沒法人進出））。
    # 為什麼不能只靠 DATA_READY_HOUR：那是固定時鐘，而公布時間會浮動。
    # 用同一檔探針問法人資料出了沒——沒出就退回前一個交易日，寧可晚一天也不要髒資料。
    # 查同一個回看窗（不是只查 probed 當天）——法人可能落後不只一天，
    # 只驗當天的話，退回的那天可能同樣沒有法人資料，等於沒修到。
    # 取「價量最大日」與「法人最大日」的較小者，兩邊都有資料才算數。
    try:
        inst_rows = client.institutional_stock(
            PROBE_TICKER,
            (bound - timedelta(days=PROBE_LOOKBACK_DAYS)).isoformat(),
            bound.isoformat())
    except Exception:
        return probed, f"探針{PROBE_TICKER}確認（法人探針失敗，未驗證）"
    inst_dates = [str(r["date"]) for r in inst_rows if r.get("date")]
    if inst_dates:
        inst_max = date.fromisoformat(max(inst_dates))
        if inst_max < probed:
            return inst_max, (f"探針{PROBE_TICKER}：價量到 {probed} 但三大法人只到 "
                              f"{inst_max}，取法人為準（避免寫入半套資料）")
    elif dates:
        # 整個回看窗都沒有法人資料 → FinMind 該 API 可能異常，不要盲目往前抓
        return probed, (f"⚠ 探針{PROBE_TICKER}：回看窗內查無任何三大法人資料，"
                        f"沿用價量判定 {probed}（請確認 FinMind 法人 API 是否異常）")
    if probed < bound:
        return probed, f"探針{PROBE_TICKER}：日曆上界 {bound} 非交易日（連假／休市）"
    return probed, f"探針{PROBE_TICKER}確認（含三大法人）"


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 未設定（請放進 .env）")
    return url


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------- 衍生欄位計算（純函式，與歷史全量同慣例，勿改） ----------
def compute_derived(series: list[dict]) -> list[dict]:
    """series 需按 date 升冪；每筆含 date, close, volume, foreign_net, trust_net, dealer_net,
    以及可選 seed_consecutive（序列第一筆之前的外資連續天數，用來續接）。
    回傳每筆補上衍生欄位。"""
    n = len(series)
    closes = [r["close"] for r in series]
    vols = [r["volume"] for r in series]

    # 日報酬（後向，供 sigma）
    daily = [None] + [
        (closes[i] / closes[i - 1] - 1) if closes[i - 1] else None for i in range(1, n)
    ]

    # 外資連續天數：從 seed 續接
    seed = series[0].get("seed_consecutive")
    cons: list[int] = []
    for i in range(n):
        fnet = series[i]["foreign_net"]
        s = 1 if fnet > 0 else (-1 if fnet < 0 else 0)
        if i == 0:
            prev = seed
            if prev is None:
                run = s
            elif s == 0:
                run = 0
            elif prev > 0 and s > 0:
                run = prev + 1
            elif prev < 0 and s < 0:
                run = prev - 1
            else:
                run = s
        else:
            prev = cons[-1]
            if s == 0:
                run = 0
            elif prev > 0 and s > 0:
                run = prev + 1
            elif prev < 0 and s < 0:
                run = prev - 1
            else:
                run = s
        cons.append(run)

    for i in range(n):
        r = series[i]
        r["foreign_consecutive_days"] = cons[i]

        # 近20日均量（含當日）
        if i >= 19:
            avg20 = sum(vols[i - 19:i + 1]) / 20
            r["volume_ratio_vs_avg20"] = (vols[i] / avg20) if avg20 else None
            inst_total = r["foreign_net"] + r["trust_net"] + r["dealer_net"]
            r["net_vs_avg20_volume_pct"] = (inst_total / avg20) if avg20 else None
        else:
            r["volume_ratio_vs_avg20"] = None
            r["net_vs_avg20_volume_pct"] = None

        # sigma：近20筆日報酬（需 i>=20，因 daily[0] 為 None）
        if i >= 20:
            window = [daily[j] for j in range(i - 19, i + 1) if daily[j] is not None]
            r["sigma_20d"] = statistics.stdev(window) if len(window) >= 2 else None
        else:
            r["sigma_20d"] = None

        # 前向報酬
        for k in (1, 3, 5):
            j = i + k
            if j < n and closes[i]:
                r[f"return_{k}d"] = closes[j] / closes[i] - 1
            else:
                r[f"return_{k}d"] = None
    return series


# ---------- 需求集 ----------
def demand_tickers(conn) -> tuple[list[str], dict]:
    """需求集 = 未 verified 事件的股票 ∪ HOT_POOL（僅普通股）。回傳 (排序清單, 統計)。"""
    with conn.cursor() as cur:
        # 展開全部代號而非只取 ->>0（2026-08-12 修）：舊版多股事件只抓首檔，
        # 次檔籌碼永不更新——審計實測 8 檔純次檔股全部斷更在 8/5。
        cur.execute(
            """select distinct t.v
               from events e, jsonb_array_elements_text(e.related_tickers) t(v)
               where (e.verify_state is distinct from 'verified')""")
        event_tks = {r[0] for r in cur.fetchall() if r[0] and TICKER_RE.match(r[0])}
    pool = set(HOT_POOL)
    out = sorted(event_tks | pool)
    return out, {"event": len(event_tks), "hot_pool": len(pool),
                 "total": len(out)}


# ---------- 驗證：單檔科目對應（register 可跑） ----------
def verify_ticker(client: FinMindClient, conn, ticker: str, the_date: str) -> None:
    print(f"[驗證] 抓 {ticker} {the_date} 三大法人（單檔），比對 DB 既有值 …")
    rows = client.institutional_stock(ticker, the_date, the_date)
    calc = aggregate_institutional(rows).get((ticker, the_date))
    with conn.cursor() as cur:
        cur.execute(
            "select foreign_net, trust_net, dealer_net from chip_data "
            "where ticker=%s and date=%s", (ticker, the_date))
        db = cur.fetchone()
    if not db:
        print(f"  DB 沒有 {ticker} {the_date}，換一個既有 (ticker,date) 驗證。")
        return
    if not calc:
        print("  FinMind 該日無此檔法人資料。")
        return
    ok = (calc["foreign_net"] == db[0] and calc["trust_net"] == db[1]
          and calc["dealer_net"] == db[2])
    print(f"  DB(f/t/d)={tuple(db)}  算={tuple(calc.values())}")
    print("[驗證結果]", "科目對應一致 ✅" if ok else
          "不一致 ✗ → 檢查 client.py 的 FOREIGN/TRUST/DEALER_NAMES")


# ---------- 單檔處理（抓 2 次 → 算衍生 → 回傳要寫的列） ----------
def _process_ticker(client: FinMindClient, conn, tk: str, today: date):
    """回傳 (要 upsert 的列, 狀態字串)。狀態：'updated' | 'fresh' | 'nodata'。
    可能拋 RateLimitError（由呼叫端長等重試）。"""
    with conn.cursor() as cur:
        cur.execute("select max(date) from chip_data where ticker=%s", (tk,))
        db_max = cur.fetchone()[0]

    if db_max is not None and db_max >= today:
        return [], "fresh"
    fetch_start = ((db_max + timedelta(days=1)) if db_max
                   else today - timedelta(days=NEW_TICKER_HISTORY_DAYS))

    # 兩次請求：價量 + 三大法人（單檔日期區間，register 可用）
    price = client.price_stock(tk, fetch_start.isoformat(), today.isoformat())
    if not price:
        return [], "nodata"
    inst = aggregate_institutional(
        client.institutional_stock(tk, fetch_start.isoformat(), today.isoformat()))

    new_rows = []
    for p in sorted(price, key=lambda x: x["date"]):
        ds = str(p["date"])
        close = _to_float(p.get("close"))
        vol = p.get("Trading_Volume", p.get("volume"))
        if close is None or vol is None:
            continue
        i = inst.get((tk, ds), {"foreign_net": 0, "trust_net": 0, "dealer_net": 0})
        new_rows.append({
            "ticker": tk, "date": ds, "close": close, "volume": int(vol),
            "foreign_net": i["foreign_net"], "trust_net": i["trust_net"],
            "dealer_net": i["dealer_net"],
        })
    if not new_rows:
        return [], "nodata"

    # DB 尾巴當暖機 + 正確 seed（暖機窗前一列的連續天數）
    series: list[dict] = []
    if db_max is not None:
        warmup_from = db_max - timedelta(days=WARMUP_DAYS)
        with conn.cursor() as cur:
            cur.execute(
                "select date, close, volume, foreign_net, trust_net, dealer_net "
                "from chip_data where ticker=%s and date>=%s order by date",
                (tk, warmup_from))
            tail = cur.fetchall()
            cur.execute(
                "select foreign_consecutive_days from chip_data "
                "where ticker=%s and date<%s order by date desc limit 1",
                (tk, warmup_from))
            seed_row = cur.fetchone()
        series = [{
            "ticker": tk, "date": r[0].isoformat(), "close": r[1], "volume": r[2],
            "foreign_net": r[3], "trust_net": r[4], "dealer_net": r[5],
        } for r in tail]
        if series and seed_row:
            series[0]["seed_consecutive"] = seed_row[0]
    series.extend(new_rows)
    compute_derived(series)

    # 要 upsert 的：新列 + 最近 RETURN_BACKFILL_DAYS 個既有列（回填到期報酬）。
    # 保護：既有回填列若衍生欄位落在暖機邊界算成 None（連假導致窗不足），
    # 跳過不寫，以免蓋掉 DB 既有的正確值；新列不受此限（新檔暖啟動本就部分 None）。
    out = []
    new_dates = {r["date"] for r in new_rows}
    keep_dates = sorted({r["date"] for r in series})[-(len(new_rows) + RETURN_BACKFILL_DAYS):]
    for r in series:
        if r["date"] not in keep_dates:
            continue
        if r["date"] not in new_dates and r["sigma_20d"] is None:
            continue  # 邊界回填列，保護 DB 值
        out.append(r)
    return out, "updated"


# ---------- 主流程（全量·撞額度等 55 分鐘續抓；--demand-only 為輕量模式） ----------
def run(tickers: list[str] | None = None, dry_run: bool = False,
        as_of: str | None = None, budget: int = WINDOW_BUDGET,
        demand_only: bool = False, wait_minutes: float = WAIT_MINUTES) -> None:
    client = FinMindClient()
    conn = psycopg.connect(_db_url(), connect_timeout=30)
    try:
        if as_of:
            today = datetime.strptime(as_of, "%Y-%m-%d").date()
            basis = "--as-of 指定"
        else:
            today, basis = resolve_last_trading_day(client)
        print(f"[目標交易日] {today}（{basis}）")

        # 佇列組裝：優先隊列（事件股∪熱門池）在前，其餘全池接後
        if tickers:
            queue, stat = sorted(set(tickers)), {"cli": len(set(tickers))}
        else:
            demand, stat = demand_tickers(conn)
            if demand_only:
                queue = demand
            else:
                with conn.cursor() as cur:
                    cur.execute("select distinct ticker from chip_data order by ticker")
                    universe = [r[0] for r in cur.fetchall()]
                rest = [t for t in universe if t not in set(demand)]
                queue = demand + rest
                stat["universe_rest"] = len(rest)
        mode = "需求集" if (tickers or demand_only) else "全量(優先隊列在前)"
        print(f"[{mode}] 佇列 {len(queue)} 檔 {stat}"
              f"｜視窗預算 {budget} 次、撞限等 {wait_minutes} 分")

        def wait_window(reason: str):
            """長等前：flush 未寫入的列＋關閉連線（Neon 會砍 idle-in-transaction 的連線）；
            醒來後重連。"""
            nonlocal conn, upsert_rows, window_used_at_start
            if not dry_run and upsert_rows:
                _upsert(conn, upsert_rows)
                conn.commit()
                upsert_rows = []
            conn.close()
            wake = datetime.now() + timedelta(minutes=wait_minutes)
            print(f"⏸ {reason}——等 {wait_minutes} 分（{wake:%H:%M} 續抓）"
                  f"｜進度 {i}/{len(queue)}", flush=True)
            time.sleep(wait_minutes * 60)
            conn = psycopg.connect(_db_url(), connect_timeout=30)
            window_used_at_start = client.requests_used

        upsert_rows: list[dict] = []
        updated = skipped_fresh = skipped_nodata = 0
        window_used_at_start = client.requests_used
        i = 0
        while i < len(queue):
            tk = queue[i]
            # 主動節流：視窗內用量逼近預算 → 先等再繼續
            if client.requests_used - window_used_at_start + 2 > budget:
                wait_window(f"視窗額度將盡（本視窗已用 {client.requests_used - window_used_at_start}）")
            try:
                rows, status = _process_ticker(client, conn, tk, today)
            except RateLimitError as e:
                wait_window(f"撞到 FinMind 上限（{e}）")
                continue  # 等完重試同一檔（i 不動）
            if status == "updated":
                upsert_rows.extend(rows)
                updated += 1
            elif status == "fresh":
                skipped_fresh += 1
            else:
                skipped_nodata += 1
            i += 1
            # 週期性 commit + 進度：中斷最多重抓 COMMIT_EVERY 檔（冪等）
            if not dry_run and upsert_rows and updated % COMMIT_EVERY == 0:
                _upsert(conn, upsert_rows)
                conn.commit()
                upsert_rows = []
            if i % 200 == 0:
                print(f"  進度 {i}/{len(queue)}｜更新 {updated}、已最新 {skipped_fresh}、"
                      f"無資料 {skipped_nodata}｜總請求 {client.requests_used}", flush=True)

        print(f"抓取完成：更新 {updated} 檔、已最新 {skipped_fresh}、無新資料 {skipped_nodata}"
              f"｜總請求數 {client.requests_used}")
        if dry_run:
            for r in upsert_rows[:6]:
                print("  DRY", {k: r.get(k) for k in
                      ("ticker", "date", "foreign_net", "foreign_consecutive_days",
                       "net_vs_avg20_volume_pct", "close", "return_1d", "return_5d",
                       "sigma_20d")})
            print(f"dry-run 不寫入（尾批 {len(upsert_rows)} 列）。")
            return
        if upsert_rows:
            _upsert(conn, upsert_rows)
            conn.commit()
        print("完成（含週期性 commit）。")
        _report_fake_zero_ratio(conn)
        # 當天自動仲裁：探針擋不住「出了一半」的邊緣情況，落庫的假零若不在
        # 這裡重抓確認，就會永久留存（本批次只抓 db_max+1 之後，不重看舊列）。
        # 只看最新交易日（~100 檔級，一檔一次呼叫），額度上限保護，失敗不弄死主流程。
        if not dry_run:
            try:
                from backend.finmind.repair import arbitrate, scan_latest_day
                zeros = scan_latest_day(conn)
                if zeros:
                    ok0, fx, sk = arbitrate(conn, client, zeros, cap=200, pause=0.2)
                    print(f"[品質] 當日假零仲裁：真零 {ok0}、修復 {fx}、跳過 {sk} 檔")
            except Exception as exc:
                print(f"[品質] 當日仲裁失敗（不影響批次）：{exc}")
    finally:
        conn.close()


def _report_fake_zero_ratio(conn, threshold_pct: float = 15.0) -> None:
    """假零值監控：當日「三大法人全零」比例超出基線就在 log 大聲示警。

    背景（2026-08-06 事故）：批次跑在法人資料只出了一半的時刻，抓不到的股票
    被以 0 補位寫入——DB 裡「真零」（當天法人真的沒動作，基線約 6–10%）與
    「假零」（資料還沒出）長得一模一樣，事後無從分辨，而市場驗證分會把假零
    讀成「法人無反應」，默默壓低分數。當時全零比飆到 25% 才被人工稽核抓到，
    刪了 569 列重抓。探針制擋「整天沒出」，本函式補「出了一半」的破口。
    只示警不中斷：資料已寫入，中斷救不回，要的是有人看見。"""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """select round(100.0 * count(*) filter
                         (where foreign_net = 0 and trust_net = 0 and dealer_net = 0)
                         / nullif(count(*), 0), 1) as pct, count(*) as n
                   from chip_data where date = (select max(date) from chip_data)""")
            row = cur.fetchone()
        pct = float(row[0] or 0)
        flag = "⚠️⚠️ 疑似半套資料（假零值），檢查法人資料完整性！" if pct > threshold_pct else "正常"
        print(f"[品質] 最新交易日三大法人全零比 {pct}%（{row[1]} 檔，基線 6–10%）：{flag}")
    except Exception as exc:
        print(f"[品質] 假零值監控本身失敗（不影響批次）：{exc}")


UPSERT_SQL = """
insert into chip_data (
  ticker, date, foreign_net, trust_net, dealer_net, foreign_consecutive_days,
  net_vs_avg20_volume_pct, close, volume, volume_ratio_vs_avg20,
  return_1d, return_3d, return_5d, sigma_20d, updated_at
) values (
  %(ticker)s, %(date)s, %(foreign_net)s, %(trust_net)s, %(dealer_net)s,
  %(foreign_consecutive_days)s, %(net_vs_avg20_volume_pct)s, %(close)s, %(volume)s,
  %(volume_ratio_vs_avg20)s, %(return_1d)s, %(return_3d)s, %(return_5d)s, %(sigma_20d)s, now()
)
on conflict (ticker, date) do update set
  foreign_net = excluded.foreign_net,
  trust_net = excluded.trust_net,
  dealer_net = excluded.dealer_net,
  foreign_consecutive_days = excluded.foreign_consecutive_days,
  net_vs_avg20_volume_pct = excluded.net_vs_avg20_volume_pct,
  close = excluded.close,
  volume = excluded.volume,
  volume_ratio_vs_avg20 = excluded.volume_ratio_vs_avg20,
  return_1d = excluded.return_1d,
  return_3d = excluded.return_3d,
  return_5d = excluded.return_5d,
  sigma_20d = excluded.sigma_20d,
  updated_at = now();
"""


def _upsert(conn, rows: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="chip_data 每日增量批次（免費版·需求驅動）")
    ap.add_argument("--verify", metavar="TICKER:DATE",
                    help="驗證某檔某日的三大法人科目對應（例 2330:2026-07-03）")
    ap.add_argument("--ticker", action="append", help="只跑指定股票（可多次），覆蓋佇列")
    ap.add_argument("--demand-only", action="store_true",
                    help="輕量模式：只跑需求集（事件股∪熱門池），不跑全池")
    ap.add_argument("--as-of", help="指定目標交易日 YYYY-MM-DD，覆蓋自動判定"
                                    "（平常不必給——不給就自動解析最後交易日）")
    ap.add_argument("--budget", type=int, default=WINDOW_BUDGET, help="每視窗請求數上限")
    ap.add_argument("--wait-minutes", type=float, default=WAIT_MINUTES,
                    help="撞額度後等待分鐘數（測試可設小數）")
    ap.add_argument("--dry-run", action="store_true", help="只計算不寫入")
    args = ap.parse_args()

    if args.verify:
        tk, _, d = args.verify.partition(":")
        client = FinMindClient()
        conn = psycopg.connect(_db_url(), connect_timeout=30)
        try:
            verify_ticker(client, conn, tk, d)
        finally:
            conn.close()
        return

    run(tickers=args.ticker, dry_run=args.dry_run, as_of=args.as_of,
        budget=args.budget, demand_only=args.demand_only,
        wait_minutes=args.wait_minutes)


if __name__ == "__main__":
    main()
