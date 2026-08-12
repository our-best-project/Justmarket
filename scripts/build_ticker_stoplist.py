# 2026-08-08 自 _handoff/crawler/ 救援至此：這是 ticker_stoplist.json 宣稱的產出
# 程式在 repo 裡的唯一副本（data/ticker_stoplist.json 的 _note 指向 scripts/，
# 但 scripts/ 原本沒有這支）。路徑引用未改，執行前確認 cwd 與資料路徑。
"""產出 related_tickers 的「公司名停用清單」（資料驅動，非人工憑感覺列）。

【問題】用 tickers 表的 2709 個公司名直接比對新聞，假陽性嚴重：
  實測 naive 匹配下 9.9% 的新聞命中 >6 家公司、單篇最多命中 58 家。
  原因是很多公司名剛好是常用詞：大量(3167)、全台(3038)、全新(2455)、世界(5347)、
  新興(2605)、大陸(2526)、聯合(4129)、卓越(2496)…

【判別式】真公司的名字偶爾會跟自己的代號同時出現；常用詞不會。
  實測驗證：群聯 100%、穩懋 100%、雙鴻 98.3%、大立光 97.4%（真公司）
       vs  大陸 0%、聯合 0%、卓越 0%、數字 0%、超級 0%（常用詞）

【規則】命中篇數 >= MIN_FIRE 且 代號共現率 < MIN_RATE → 進停用清單。
  低頻命中的名字不列入判斷：常用詞必然高頻，所以低頻名字不是常用詞（且影響小）。

【副作用（已知且可接受）】此規則會一併排除「過時代號」：
  日月光(2311) 共現率 0% —— 因為新聞講的日月光是「日月光投控(3711)」，2311 是舊代號。
  合庫(5854) 同理（現存的是合庫金 5880）。排除它們反而正確：不該把新聞標到舊代號上。

用法：
    python scripts/build_ticker_stoplist.py            # 產出 artifact
    python scripts/build_ticker_stoplist.py --show     # 印出完整清單
"""

import argparse
import json
import os
from collections import Counter
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[1]
NEWS = REPO_ROOT / "data" / "cnyes_news_2026.json"
OUT = REPO_ROOT / "data" / "ticker_stoplist.json"
DOTENV = REPO_ROOT / ".env"

MIN_FIRE = 30      # 命中篇數門檻：低於此不列入判斷（樣本太少，且低頻＝非常用詞）
MIN_RATE = 0.02    # 代號共現率門檻：低於此視為常用詞


def db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    for line in DOTENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL 未設定")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="印出完整停用清單")
    args = ap.parse_args()

    with psycopg.connect(db_url()) as conn:
        rows = conn.execute("SELECT ticker, name FROM tickers").fetchall()
    name2t = {n: t for t, n in rows}
    names_sorted = sorted(name2t, key=len, reverse=True)   # 最長匹配

    news = json.loads(NEWS.read_text(encoding="utf-8"))

    fire, cooccur = Counter(), Counter()
    for a in news:
        text = (a.get("title") or "") + "\n" + (a.get("content") or "")
        remain, hit = text, set()
        for n in names_sorted:
            if n in remain:
                hit.add(n)
                remain = remain.replace(n, "\t")   # 挖掉，避免子字串重複命中
        for n in hit:
            fire[n] += 1
            if name2t[n] in text:
                cooccur[n] += 1

    stop = []
    for n, f in fire.items():
        if f >= MIN_FIRE:
            rate = cooccur[n] / f
            if rate < MIN_RATE:
                stop.append({"name": n, "ticker": name2t[n], "fires": f, "cooccur_rate": round(rate, 4)})
    stop.sort(key=lambda x: -x["fires"])

    artifact = {
        "產出方式": "scripts/build_ticker_stoplist.py（資料驅動，勿手改）",
        "語料": str(NEWS.relative_to(REPO_ROOT)),
        "語料篇數": len(news),
        "規則": f"命中篇數 >= {MIN_FIRE} 且 代號共現率 < {MIN_RATE} → 停用",
        "門檻": {"MIN_FIRE": MIN_FIRE, "MIN_RATE": MIN_RATE},
        "停用數": len(stop),
        "評估過的名稱數": sum(1 for f in fire.values() if f >= MIN_FIRE),
        "停用清單": stop,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"語料 {len(news)} 篇 / tickers {len(name2t)} 檔")
    print(f"命中 >= {MIN_FIRE} 篇的名稱: {artifact['評估過的名稱數']} 個")
    print(f"其中共現率 < {MIN_RATE:.0%} → 停用: {len(stop)} 個")
    print(f"→ {OUT.relative_to(REPO_ROOT)}")
    if args.show or True:
        print("\n停用清單（按命中篇數）:")
        for s in stop:
            print(f"  {s['name']:<10} {s['ticker']:<8} {s['fires']:>4} 篇  共現率 {s['cooccur_rate']:>6.1%}")


if __name__ == "__main__":
    main()
