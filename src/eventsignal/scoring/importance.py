"""重要性排序（消息面）：四維加權 × 熱度放大器 → ★1–5，可拆解理由（SD §2.4、附錄 §D）。

四維（權重為經驗起點、須用代理驗證檢視）：
  來源廣度 30%｜多少獨立來源報導（重大事件被多家報導）
  來源權威 25%｜量「離第一手多遠」（A 方案）：official 且官方在 members 100 >
               僅主流轉述 85 > 僅一般轉述 72；非 official 走來源分級 70/55/50/30
  影響範圍 30%｜大盤/總經 > 產業 > 多檔個股 > 權值單檔 > 一般單檔
  事件類型 15%｜法說/財報/政策（歷史上最牽動股價）> 例行揭露

熱度（放大器，不佔權重；方案 B 會議定案，取代原「新穎性」維度）：
  事件成立後 24h 內的跟進家數 → 放大係數 0.90–1.15。
  乏人問津縮減、快速發酵放大——與市場驗證分數的「量能放大器」同一設計語言。
  （原新穎性在真實資料 88% 滿分退化成常數；「制式重複」由上游週期過濾＋去重處理，
   「話題再現」閘門列第二階段。資料來源：articles.event_id + published_at/fetched_at。）

鐵律：**每個分數都要能拆解出理由**——輸出 importance_reasons（人話）
與 breakdown（每維原始分×權重＋熱度係數），不能只給星等。

對「預設使用者」打分，第一版不做個人化（SD §2.4）。
所有門檻/對照表集中在 CONFIG，供代理驗證（01_PRD §6：2–3 人挑 top-5 算重疊率）後調整。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------- 參數外置（代理驗證後調這裡） ----------------
CONFIG = {
    "weights": {  # 方案 B（會議定案）：新穎性移除，四維重配
        "breadth": 0.30, "authority": 0.25, "impact": 0.30, "category": 0.15,
    },
    # 來源廣度：獨立來源數 → 0–100（查表，透明可講）
    "breadth_table": {1: 25, 2: 45, 3: 60, 4: 70, 5: 80, 6: 85, 7: 92},
    "breadth_max_at": 8,  # ≥8 家 → 100
    # 來源權威：來源名稱關鍵字 → 分級（取事件內最高級）
    "authority_tiers": [
        (100, ("mops", "公開資訊觀測站", "證交所", "櫃買", "金管會", "央行")),
        (70,  ("工商時報", "經濟日報", "中央社")),
        (55,  ("鉅亨", "anue", "自由財經", "聯合", "yahoo")),
        (30,  ("telegram", "分析師", "論壇", "ptt", "dcard")),
    ],
    "authority_default": 50,          # 未知來源
    # A 方案（會議定案）：official_confirmed 不再無條件滿分，改看 members 分級
    "authority_official_status": 100,     # 第一手：官方來源（MOPS 等）就在 members
    "authority_official_mainstream": 85,  # 二手：僅主流媒體轉述官方消息
    "authority_official_general": 72,     # 二手：僅一般媒體轉述官方消息
    # 影響範圍
    "impact_macro": 100,       # 政策/關稅（總經、全市場）
    "impact_industry": 75,     # 產業供需 / AI科技（產業面）
    "impact_multi3": 70,       # ≥3 檔個股
    "impact_multi2": 60,       # 2 檔
    "impact_heavyweight": 65,  # 單檔但屬權值股
    "impact_single": 45,       # 一般單檔
    "impact_none": 35,         # 無明確標的
    # 權值股清單（台灣50前段、影響大盤的個股；可換成用 tickers 表市值判斷）
    "heavyweights": {"2330", "2317", "2454", "2308", "2382", "2412", "2881", "2882",
                     "2891", "3711", "2303", "2886", "2884", "1216", "2002"},
    # 事件類型 → 分數（9 類，附錄 §F；多標籤取最高）
    "category_scores": {
        "法說": 90, "財報": 85, "政策": 85, "關稅": 80,
        "產業供需": 70, "AI/科技": 65, "營收": 60, "技術突破": 55, "法人動向": 40,
    },
    "category_default": 50,
    # 熱度放大器（方案 B）：24h 內跟進家數 vs 基準 → 係數 0.90–1.15
    # 分段線性，鏡射 market_validation.volume_amplifier 的樣式。
    "heat_window_hours": 24,
    "heat_baseline_sources": 2.0,  # 24h 內 2 家 = 中性（經驗起點，事件累積後校準）
    "heat_lo_ratio": 0.5, "heat_lo": 0.90,   # ≤基準×0.5 → 0.90（乏人問津）
    "heat_mid_ratio": 1.0, "heat_mid": 1.00,
    "heat_hi_ratio": 2.5, "heat_hi": 1.15,   # ≥基準×2.5 → 1.15（快速發酵）
    # 加權總分 → 星等
    "star_cuts": [(80, 5), (65, 4), (50, 3), (35, 2)],  # 其餘 → 1
}


@dataclass
class ImportanceResult:
    stars: int                      # 1–5（DB 欄位 importance_stars；API 名 stars）
    total: float                    # 加權總分 0–100（內部）
    reasons: list = field(default_factory=list)   # importance_reasons（人話，可拆解）
    breakdown: dict = field(default_factory=dict)  # 每維 {score, weighted}（內部/除錯）


# ---------------- 五維各自的純函式（0–100） ----------------
def score_breadth(source_count: int) -> tuple[int, str]:
    c = CONFIG
    n = max(1, int(source_count or 1))
    if n >= c["breadth_max_at"]:
        s = 100
    else:
        s = c["breadth_table"].get(n, 25)
    return s, f"{n} 家獨立來源報導" + ("（廣泛關注）" if s >= 80 else
                                  ("（多方報導）" if s >= 60 else "（來源有限）"))


def score_authority(members: list[dict] | None, status: str | None) -> tuple[int, str]:
    c = CONFIG
    if status == "official_confirmed":
        # A 方案：官方確認後再看 members 分「離第一手多遠」。
        # 語意與 MMJSUNtest/routeA_simulation.py 的參考實作一致：
        # 全部 source 併成小寫字串做子字串比對（爬蟲入庫是大寫 "MOPS"，必須 .lower()）。
        sources = " ".join(str(m.get("source", "")).lower() for m in (members or []))
        if any(k.lower() in sources for k in c["authority_tiers"][0][1]):
            return c["authority_official_status"], "第一手：官方來源在場（MOPS 等）"
        if any(k.lower() in sources for k in c["authority_tiers"][1][1]):
            return c["authority_official_mainstream"], "二手：主流媒體轉述官方"
        return c["authority_official_general"], "二手：一般媒體轉述官方"
    # 取「有對到分級的來源」中的最高級；全都對不到才用預設。
    # （不能從預設 50 起跳取 max，否則 PTT 這種低分級永遠蓋不下去）
    best: int | None = None
    best_label = ""
    for m in members or []:
        src = str(m.get("source", "")).lower()
        for tier_score, keywords in c["authority_tiers"]:
            if any(k.lower() in src for k in keywords):
                if best is None or tier_score > best:
                    best, best_label = tier_score, m.get("source", "")
                break
    if best is None:
        best, best_label = c["authority_default"], "一般來源"
    # 未證實謠言：權威上限壓到社群級（消息本身未經證實，來源再多也不該高分）
    if status == "rumor_unconfirmed":
        best = min(best, 30)
        return best, "消息未經證實（謠言階段），權威性受限"
    if best >= 100:
        return best, f"含官方來源（{best_label}）"
    if best >= 70:
        return best, f"主流財經媒體（{best_label}）"
    if best >= 55:
        return best, f"一般財經媒體（{best_label}）"
    if best <= 30:
        return best, "僅社群/分析師來源，未經證實"
    return best, "來源等級一般"


def score_impact(related_tickers: list[str] | None, categories: list[str] | None) -> tuple[int, str]:
    c = CONFIG
    cats = set(categories or [])
    tks = [str(t) for t in (related_tickers or [])]
    if cats & {"政策", "關稅"}:
        return c["impact_macro"], "總經/政策層級，影響全市場"
    if cats & {"產業供需", "AI/科技"}:
        return c["impact_industry"], "產業層級，影響整條供應鏈"
    if len(tks) >= 3:
        return c["impact_multi3"], f"牽動 {len(tks)} 檔個股"
    if len(tks) == 2:
        return c["impact_multi2"], "牽動 2 檔個股"
    if len(tks) == 1:
        if tks[0] in c["heavyweights"]:
            return c["impact_heavyweight"], f"權值股 {tks[0]}（單檔但足以牽動大盤）"
        return c["impact_single"], f"單一個股 {tks[0]}"
    return c["impact_none"], "無明確標的"


def score_category(categories: list[str] | None) -> tuple[int, str]:
    c = CONFIG
    cats = categories or []
    if not cats:
        return c["category_default"], "類型未標"
    best = max(cats, key=lambda x: c["category_scores"].get(x, c["category_default"]))
    s = c["category_scores"].get(best, c["category_default"])
    return s, f"事件類型「{best}」" + ("（高影響類型）" if s >= 80 else
                                 ("（中影響類型）" if s >= 60 else "（例行揭露類型）"))


def heat_amplifier(sources_24h: int | float | None) -> tuple[float, str]:
    """熱度放大器（方案 B）：24h 內跟進家數 → 係數 0.90–1.15＋人話理由。

    None（無資料）→ 1.0，不放大也不懲罰。分段線性與
    market_validation.volume_amplifier 同構。"""
    c = CONFIG
    if sources_24h is None:
        return 1.0, "熱度資料不足，不調整"
    ratio = max(0.0, float(sources_24h)) / c["heat_baseline_sources"]
    if ratio <= c["heat_lo_ratio"]:
        amp = c["heat_lo"]
    elif ratio <= c["heat_mid_ratio"]:
        t = (ratio - c["heat_lo_ratio"]) / (c["heat_mid_ratio"] - c["heat_lo_ratio"])
        amp = c["heat_lo"] + t * (c["heat_mid"] - c["heat_lo"])
    elif ratio >= c["heat_hi_ratio"]:
        amp = c["heat_hi"]
    else:
        t = (ratio - c["heat_mid_ratio"]) / (c["heat_hi_ratio"] - c["heat_mid_ratio"])
        amp = c["heat_mid"] + t * (c["heat_hi"] - c["heat_mid"])
    n = int(sources_24h)
    if amp >= 1.10:
        why = f"{c['heat_window_hours']}h 內 {n} 家跟進（快速發酵）"
    elif amp <= 0.95:
        why = f"{c['heat_window_hours']}h 內僅 {n} 家報導（乏人問津）"
    else:
        why = f"{c['heat_window_hours']}h 內 {n} 家跟進（一般熱度）"
    return round(amp, 3), why


# ---------------- 主計分 ----------------
def score_importance(source_count: int,
                     members: list[dict] | None,
                     status: str | None,
                     related_tickers: list[str] | None,
                     categories: list[str] | None,
                     prior_similar_count: int = 0,
                     heat_sources_24h: int | float | None = None) -> ImportanceResult:
    """四維加權 × 熱度放大器 → ★1–5。每維皆回人話理由（鐵律：可拆解）。

    prior_similar_count 已棄用（方案 B 移除新穎性）——僅為相容既有呼叫端
    （如 MMJSUNtest/routeA_simulation.py）保留，值被忽略。
    heat_sources_24h：事件成立後 24h 內跟進家數；None → 放大器 1.0。"""
    w = CONFIG["weights"]
    dims = {
        "breadth": (score_breadth(source_count), w["breadth"], "來源廣度"),
        "authority": (score_authority(members, status), w["authority"], "來源權威"),
        "impact": (score_impact(related_tickers, categories), w["impact"], "影響範圍"),
        "category": (score_category(categories), w["category"], "事件類型"),
    }
    total = 0.0
    reasons: list[str] = []
    breakdown: dict = {}
    for key, ((s, why), weight, label) in dims.items():
        total += s * weight
        breakdown[key] = {"score": s, "weight": weight, "weighted": round(s * weight, 1)}
        reasons.append(f"[{label} {s}分×{int(weight*100)}%] {why}")

    amp, heat_why = heat_amplifier(heat_sources_24h)
    total = min(100.0, max(0.0, total * amp))
    breakdown["heat"] = {"amp": amp, "sources_24h": heat_sources_24h}
    reasons.append(f"[熱度 ×{amp:g}] {heat_why}")

    stars = 1
    for cut, st in CONFIG["star_cuts"]:
        if total >= cut:
            stars = st
            break
    return ImportanceResult(stars=stars, total=round(total, 1),
                            reasons=reasons, breakdown=breakdown)


# ---------------- DB 整合 ----------------
def fetch_heat_sources(conn, event_id: str) -> int | None:
    """事件成立後 24h 內的不重複跟進家數（熱度放大器輸入）。

    視窗錨定「最早成員文章時間」（事件成立＝首篇；occurred_at 可 null 故不依賴）。
    時間欄用 COALESCE(published_at, fetched_at)（published_at 可 null、fetched_at 必有）。
    該事件查無任何文章 → 回 None（缺資料不懲罰，放大器取 1.0）。"""
    # 明確指定 tuple_row：本函式用 row[0] 取值，但呼叫端傳進來的連線可能是
    # app/db/session.py 的 dict_row（專案統一入口）——那時 [0] 會是 KeyError。
    # 在游標層宣告自己要什麼，兩種連線都能用。
    from psycopg.rows import tuple_row

    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """with t as (select coalesce(published_at, fetched_at) as ts, source
                          from articles where event_id = %s)
               select count(distinct source) from t
               where ts <= (select min(ts) from t) + make_interval(hours => %s)""",
            (event_id, CONFIG["heat_window_hours"]),
        )
        n = cur.fetchone()[0]
    return n if n and n > 0 else None


def score_and_update_importance(conn, event_id: str, dry_run: bool = False) -> ImportanceResult:
    """從 events 表讀事件 → 打分 → 回寫 importance_stars / importance_reasons。"""
    import json

    from psycopg.rows import tuple_row  # 下面用 tuple 解包，與連線的 row_factory 脫鉤

    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            """select source_count, members, status, related_tickers, categories
               from events where event_id=%s""", (event_id,))
        row = cur.fetchone()
    if not row:
        raise ValueError(f"event {event_id} 不存在")
    source_count, members, status, tickers, categories = row
    heat = fetch_heat_sources(conn, event_id)
    res = score_importance(source_count or 1, members, status, tickers, categories,
                           heat_sources_24h=heat)
    if not dry_run:
        with conn.cursor() as cur:
            cur.execute(
                """update events set importance_stars=%s, importance_reasons=%s,
                       updated_at=now() where event_id=%s""",
                (res.stars, json.dumps(res.reasons, ensure_ascii=False), event_id))
        conn.commit()
    return res


# ---------------- CLI demo（驗收用：5 個範例事件丟群組） ----------------
DEMO_EVENTS = [
    dict(name="台積電法說：上修全年展望（官方+多家快速跟進）",
         source_count=8,
         members=[{"source": "MOPS"}, {"source": "工商時報"}, {"source": "鉅亨網"}],
         status="official_confirmed", related_tickers=["2330"],
         categories=["法說", "財報"], heat_sources_24h=8),
    dict(name="美對中半導體出口新限制（政策、全市場）",
         source_count=6,
         members=[{"source": "經濟日報"}, {"source": "鉅亨網"}],
         status="developing", related_tickers=["2330", "2454", "3711"],
         categories=["關稅", "政策"], heat_sources_24h=6),
    dict(name="鴻海 6 月營收公布（例行揭露）",
         source_count=3,
         members=[{"source": "MOPS"}, {"source": "鉅亨網"}],
         status="official_confirmed", related_tickers=["2317"],
         categories=["營收"], heat_sources_24h=3),
    dict(name="外資連 5 日買超台積電（少量跟進）",
         source_count=2,
         members=[{"source": "Yahoo財經"}],
         status="market_reacting", related_tickers=["2330"],
         categories=["法人動向"], heat_sources_24h=2),
    dict(name="某小型股取得新專利（單一來源、未證實、乏人問津）",
         source_count=1,
         members=[{"source": "PTT"}],
         status="rumor_unconfirmed", related_tickers=["4915"],
         categories=["技術突破"], heat_sources_24h=1),
]


def main() -> None:
    print("=" * 66)
    print("T15 重要性評分（四維×熱度）— 5 個範例事件（驗收：丟群組看合不合直覺）")
    print("=" * 66)
    for ev in DEMO_EVENTS:
        r = score_importance(ev["source_count"], ev["members"], ev["status"],
                             ev["related_tickers"], ev["categories"],
                             heat_sources_24h=ev["heat_sources_24h"])
        print(f"\n★{'★' * (r.stars - 1)}{'☆' * (5 - r.stars)}  {r.stars} 星"
              f"（加權 {r.total} 分）｜{ev['name']}")
        for line in r.reasons:
            print("   ", line)


if __name__ == "__main__":
    main()
