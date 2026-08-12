"""LLM 產出品質稽核：抽驗新鮮批次，抓「確定性防線攔不到」的語意類問題。

定位（重要）：summarize.py 的三道 guard（幻覺 ticker、官方章、未來日期）已在寫回前
擋掉確定性錯誤，本腳本**不重複那些**，專抓需要語意判斷、只能靠人複核的可疑樣態。
每一項都印出案例讓人自己判，不自動改資料。

用法（從 repo 根目錄 執行）：
    python ../scripts/audit_llm_output.py                # 近 2 小時的產出
    python ../scripts/audit_llm_output.py --hours 24
    python ../scripts/audit_llm_output.py --quiet        # 只印摘要，給監控迴圈用
"""
from __future__ import annotations

import argparse
import os
import pathlib

import psycopg

# 公司特定的負面「事實」——出現在標題卻沒標利空，通常是漏判
# （品誠 0803 prompt 已補強，這裡持續驗收是否真的收斂）
BEARISH_WORDS = ["虧損", "下修", "裁員", "衰退", "砍單", "不配發", "重挫", "暴跌",
                 "違約", "停工", "召回", "撤照", "解散", "跳票"]
# 法遵紅線：平台不得出現的投顧字眼（轉述外資目標價屬灰區，另外標示）
FORBIDDEN = ["飆股", "明牌", "保證獲利", "建議買", "建議賣", "必漲", "必跌",
             "買點", "賣點", "上車"]
GREY = ["目標價", "喊到", "上看"]


def _db_url() -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    for folder in [pathlib.Path.cwd(), *pathlib.Path.cwd().parents]:
        env = folder / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("DATABASE_URL 未設定")


def audit(conn, hours: int, quiet: bool = False) -> list[str]:
    """回傳警示清單（空 = 這批乾淨）。quiet 時不印個別案例。"""
    # 「新產出」不能用 events.updated_at 判——評分段也會更新它，會把幾個月前的
    # 舊事件算成本輪產出（實測誤報 37 筆假官方章，實為防線上線前的存量）。
    # 正解：看成員文章是否剛被推進 summarized，那只有 LLM 段會做。
    fresh = (f"exists (select 1 from articles a where a.event_id = events.event_id "
             f"and a.status in ('summarized','scored') "
             f"and a.updated_at > now() - interval '{hours} hours')")
    base = f"title is not null and {fresh}"
    q1 = lambda s: conn.execute(s).fetchone()[0]
    n = q1(f"select count(*) from events where {base}")
    alerts: list[str] = []
    print(f"── 稽核近 {hours} 小時產出：{n} 筆")
    if not n:
        return alerts

    def show(title: str, sql: str, params=None, threshold_pct: float | None = None,
             sample_sql: str | None = None):
        cnt = conn.execute(sql, params).fetchone()[0]
        pct = cnt / n * 100
        flag = ""
        if threshold_pct is not None and pct > threshold_pct:
            flag = " ⚠️"
            alerts.append(f"{title} {cnt} 筆({pct:.0f}%)")
        print(f"   {title:<26} {cnt:>4} ({pct:>4.1f}%){flag}")
        if cnt and sample_sql and not quiet:
            for r in conn.execute(sample_sql, params).fetchall()[:4]:
                print(f"        · {' | '.join(str(x)[:38] for x in r)}")

    # 1) 利空漏判：標題有公司特定負面事實卻非利空
    like = " or ".join(["title like %s"] * len(BEARISH_WORDS))
    pats = [f"%{w}%" for w in BEARISH_WORDS]
    show("利空疑似漏判",
         f"select count(*) from events where {base} and expected_direction!='利空' and ({like})",
         pats, threshold_pct=2.0,
         sample_sql=f"select expected_direction, left(title,40) from events "
                    f"where {base} and expected_direction!='利空' and ({like}) limit 4")

    # 2) 法遵禁詞（紅線）與灰區
    like_f = " or ".join(["title like %s or summary like %s"] * len(FORBIDDEN))
    pats_f = [x for w in FORBIDDEN for x in (f"%{w}%", f"%{w}%")]
    show("法遵禁詞(紅線)",
         f"select count(*) from events where {base} and ({like_f})", pats_f,
         threshold_pct=0.0,
         sample_sql=f"select left(title,44) from events where {base} and ({like_f}) limit 4")
    like_g = " or ".join(["title like %s or summary like %s"] * len(GREY))
    pats_g = [x for w in GREY for x in (f"%{w}%", f"%{w}%")]
    show("目標價轉述(灰區)",
         f"select count(*) from events where {base} and ({like_g})", pats_g,
         threshold_pct=3.0,
         sample_sql=f"select left(title,44) from events where {base} and ({like_g}) limit 3")

    # 3) 標題超規（契約 20 字內）
    show("標題 >25 字",
         f"select count(*) from events where {base} and length(title)>25", None,
         threshold_pct=5.0,
         sample_sql=f"select length(title), left(title,44) from events "
                    f"where {base} and length(title)>25 limit 3")

    # 4) 中性率過高＝方向判斷退化成安全牌
    show("中性事件", f"select count(*) from events where {base} and expected_direction='中性'",
         None, threshold_pct=60.0)

    # 5) 分類垃圾桶化：單一分類佔比過高
    top = conn.execute(
        f"""select c, count(*) from events, jsonb_array_elements_text(categories) c
            where {base} group by 1 order by 2 desc limit 1""").fetchone()
    if top:
        pct = top[1] / n * 100
        print(f"   {'最常用分類 ' + top[0]:<26} {top[1]:>4} ({pct:>4.1f}%)"
              + (" ⚠️" if pct > 60 else ""))
        if pct > 60:
            alerts.append(f"分類集中於「{top[0]}」{pct:.0f}%")

    # 6) ticker 不在 tickers 表（外國代號碰撞／ETF 孤兒）
    show("ticker 查無此號",
         f"""select count(distinct event_id) from events, jsonb_array_elements_text(related_tickers) t
             where {base}
               and not exists (select 1 from tickers k where k.ticker = t)""", None,
         threshold_pct=5.0,
         sample_sql=f"""select distinct t, left(title,34) from events, jsonb_array_elements_text(related_tickers) t
             where {base}
               and not exists (select 1 from tickers k where k.ticker = t) limit 4""")

    # 7) 三道 guard 的把關結果（應為 0；非 0 代表防線破口）
    show("⚑未來日期(guard應擋)",
         f"select count(*) from events where {base} and occurred_at_iso::date > current_date",
         None, threshold_pct=0.0)
    show("⚑假官方章(guard應擋)",
         f"""select count(*) from events where {base}
             and status='official_confirmed'
             and not exists (select 1 from articles a where a.event_id=events.event_id
                             and a.source_type in ('official','gov'))""",
         None, threshold_pct=0.0)
    # 註：_verify_tickers 只剔除「原文未提及」的代號，不限數量——大盤文合法提及
    # 20+ 檔會通過。<=20 的限制只在 API 時間軸查詢，故這裡是觀察指標不是防線破口。
    show("ticker>20檔(大盤級)",
         f"select count(*) from events where {base} and jsonb_array_length(related_tickers)>20",
         None, threshold_pct=3.0)
    return alerts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=2)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    conn = psycopg.connect(_db_url(), connect_timeout=30)
    alerts = audit(conn, args.hours, args.quiet)
    print("\n" + ("⚠️ 需注意：" + "；".join(alerts) if alerts else "✅ 本批未見異常樣態"))
    conn.close()


if __name__ == "__main__":
    main()
