"""市場驗證分數（籌碼面 0–100）：三訊號「一致性 × 強度」+ 一致性閘門 + 狀態機（SD §2.5）。

核心觀念：信心 = 訊號的「一致性 × 強度」，**不是平均**。
  - 三大法人（方向）：連續買/賣超天數 + 淨額占近20日均量比（跨股可比）
  - 股價反應（方向）：事件後 1/3/5 日累積報酬，用該股 σ 正規化（>1.5σ 才算明顯）
  - 成交量（強度放大器，非方向）：量比 → 放量放大、量縮縮減
合成：基準 50 → 法人 ±25、股價 ±20（依與預期方向一致與否加減）→ 量放大係數
  → 一致性閘門：法人與股價方向相反 → 強制壓回 40–55、標記「訊號分歧」。
direction_confidence=low 或 expected_direction=中性 → 不給分數，只列原始籌碼證據。

狀態機（分數隨資料累積更新）：
  observing（D0，僅當日資料）→ preliminary（D+1~3）→ verified（D+5 完整）

資料來源：chip_data（T16，每日盤後批次）。
產出：market_validation、validation_breakdown、chip_evidence、verify_state
  （欄位契約見 04_API_v2 §3；分數語意：80–100 明確認同 / 60–79 傾向認同 /
   45–59 觀望或分歧 / 20–44 傾向不認同 / 0–19 明確不認同）

⚠️ 前端顯示必附：「此分數描述已發生的市場反應，非預測、非機率」。
⚠️ 事件日 D0 = 消息「盤中可反應」的第一個交易日；若消息在收盤後發布，
   呼叫端應把 D0 設為次一交易日（本模組不自行判斷盤後）。

權重/閾值為透明的經驗起點（附錄 §E 範例校準），全部集中在 CONFIG，
供 T24 敏感度分析調整——參數外置原則。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date as Date

import psycopg

# ---------------- 參數外置（T24 敏感度分析就調這裡） ----------------
CONFIG = {
    "w_inst": 25.0,          # 法人最大貢獻 ±25
    "w_price": 20.0,         # 股價最大貢獻 ±20
    # 法人強度 = 0.3×(連續天數/5 封頂) + 0.7×(平均日淨額占均量 / 0.20 封頂)
    "inst_days_cap": 5,
    "inst_netpct_cap": 0.20,
    "inst_days_weight": 0.3,
    # 股價強度 = |z| / 2 封頂 1（z = 累積報酬 / (σ×√k)；1.5σ 以上視為明顯）
    "price_z_div": 2.0,
    # 股價視窗模式：False=取最長到期視窗（現行）；True=1/3/5 日 z 加權混合（0.2/0.3/0.5,
    # 僅用已到期視窗、權重重正規化）。T24 實測混合案跨天較平滑（>25分跳動 15%→11.5%）
    # 且不改變最終結論（D+5 Spearman 0.975、同帶率 90.2%）——是否切換由全組對齊決定。
    "price_blend": False,
    "price_blend_weights": {1: 0.2, 3: 0.3, 5: 0.5},
    # 量放大係數：ratio<=0.7 → 0.8；=1.0 → 1.0；>=2.5 → 1.15（線性內插）
    "amp_lo_ratio": 0.7, "amp_lo": 0.80,
    "amp_mid_ratio": 1.0, "amp_mid": 1.00,
    "amp_hi_ratio": 2.5, "amp_hi": 1.15,
    # 一致性閘門：法人與股價反向 → 壓回此區間並標記分歧
    "gate_lo": 40, "gate_hi": 55,
    # 訊號方向的「有感」門檻（太微弱不給方向，避免雜訊翻方向）
    "inst_min_netpct": 0.01,   # 平均日淨額占均量 <1% 視為無方向
    "price_min_z": 0.3,        # |z|<0.3σ 視為無方向
}

# 普通股過濾（T16 驗證結論：ETF/權證要濾）。
# 台股普通股為 1101–9999（首位非 0）；00 開頭（0050、006208…）是 ETF，一樣要濾。
TICKER_4DIGIT = re.compile(r"^[1-9]\d{3}$")


# ---------------- 資料結構 ----------------
@dataclass
class ChipRow:
    date: Date
    close: float
    volume: int
    foreign_net: int
    trust_net: int
    dealer_net: int
    foreign_consecutive_days: int
    net_vs_avg20_volume_pct: float | None
    volume_ratio_vs_avg20: float | None
    return_1d: float | None
    return_3d: float | None
    return_5d: float | None
    sigma_20d: float | None


@dataclass
class ValidationResult:
    market_validation: int | None          # 0–100；不給分時 None
    verify_state: str                      # observing | preliminary | verified
    divergence: bool
    validation_breakdown: dict = field(default_factory=dict)
    chip_evidence: list = field(default_factory=list)
    debug: dict = field(default_factory=dict)  # 拆解數字（可重現、非 API 欄位）


# ---------------- DB 讀取 ----------------
def _db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 未設定")
    return url


def load_chip_rows(conn, ticker: str, d0: Date, post_days: int = 5) -> list[ChipRow]:
    """取 D0 起（含）之後最多 post_days+1 個交易日的 chip_data。"""
    with conn.cursor() as cur:
        cur.execute(
            """select date, close, volume, foreign_net, trust_net, dealer_net,
                      foreign_consecutive_days, net_vs_avg20_volume_pct,
                      volume_ratio_vs_avg20, return_1d, return_3d, return_5d, sigma_20d
               from chip_data
               where ticker=%s and date>=%s and close>0
               order by date limit %s""",
            (ticker, d0, post_days + 1),
        )
        rows = cur.fetchall()
        # backend.db.session 的正式連線使用 dict_row；直接 *r 會展開成欄位名稱，
        # 直到數值運算才以 int + str 的形式爆掉。CLI 的預設 tuple row 仍保留相容。
        return [ChipRow(**r) if isinstance(r, dict) else ChipRow(*r) for r in rows]


# ---------------- 三訊號 ----------------
def inst_signal(rows: list[ChipRow]) -> tuple[int, float, dict]:
    """法人訊號：回傳 (方向 -1/0/+1, 強度 0–1, 明細)。
    方向看窗內平均日淨額占均量的正負；強度混合連續天數與淨額占比。"""
    c = CONFIG
    pcts = [r.net_vs_avg20_volume_pct for r in rows if r.net_vs_avg20_volume_pct is not None]
    mean_pct = sum(pcts) / len(pcts) if pcts else 0.0
    cons = rows[-1].foreign_consecutive_days if rows else 0

    if abs(mean_pct) < c["inst_min_netpct"]:
        sign = 0
    else:
        sign = 1 if mean_pct > 0 else -1

    strength = (
        c["inst_days_weight"] * min(abs(cons), c["inst_days_cap"]) / c["inst_days_cap"]
        + (1 - c["inst_days_weight"]) * min(abs(mean_pct) / c["inst_netpct_cap"], 1.0)
    )
    return sign, min(strength, 1.0), {"mean_net_pct": mean_pct, "consecutive": cons}


def price_signal(d0_row: ChipRow) -> tuple[int, float, int, dict]:
    """股價訊號：回傳 (方向, 強度, 已成熟天數 k, 明細)。
    用 D0 那列的前向報酬（return_1d/3d/5d，未到期為 None）+ σ 正規化。
    CONFIG["price_blend"]=True 時改用 1/3/5 日 z 加權混合（T24 平滑改良案）。"""
    c = CONFIG
    sigma = d0_row.sigma_20d
    if c.get("price_blend"):
        parts = []
        for k, ret in ((1, d0_row.return_1d), (3, d0_row.return_3d), (5, d0_row.return_5d)):
            if ret is not None and sigma and sigma > 0:
                w = c["price_blend_weights"][k]
                parts.append((ret / (sigma * k ** 0.5), w, k, ret))
        if not parts:
            return 0, 0.0, 0, {"return": None, "z": None, "k": 0}
        tw = sum(p[1] for p in parts)
        z = sum(p[0] * p[1] for p in parts) / tw
        kmax = max(p[2] for p in parts)
        ret = next(p[3] for p in parts if p[2] == kmax)
        sign = 0 if abs(z) < c["price_min_z"] else (1 if z > 0 else -1)
        return sign, min(abs(z) / c["price_z_div"], 1.0), kmax, \
            {"return": ret, "z": z, "k": kmax, "blend": True}
    for k, ret in ((5, d0_row.return_5d), (3, d0_row.return_3d), (1, d0_row.return_1d)):
        if ret is not None:
            if not sigma or sigma <= 0:
                return 0, 0.0, k, {"return": ret, "z": None, "k": k}
            z = ret / (sigma * (k ** 0.5))
            sign = 0 if abs(z) < c["price_min_z"] else (1 if z > 0 else -1)
            strength = min(abs(z) / c["price_z_div"], 1.0)
            return sign, strength, k, {"return": ret, "z": z, "k": k}
    return 0, 0.0, 0, {"return": None, "z": None, "k": 0}


def volume_amplifier(rows: list[ChipRow]) -> tuple[float, float]:
    """量放大係數：取窗內最大量比，分段線性映射到 [0.8, 1.15]。"""
    c = CONFIG
    ratios = [r.volume_ratio_vs_avg20 for r in rows if r.volume_ratio_vs_avg20 is not None]
    ratio = max(ratios) if ratios else 1.0
    if ratio <= c["amp_lo_ratio"]:
        amp = c["amp_lo"]
    elif ratio <= c["amp_mid_ratio"]:
        t = (ratio - c["amp_lo_ratio"]) / (c["amp_mid_ratio"] - c["amp_lo_ratio"])
        amp = c["amp_lo"] + t * (c["amp_mid"] - c["amp_lo"])
    elif ratio >= c["amp_hi_ratio"]:
        amp = c["amp_hi"]
    else:
        t = (ratio - c["amp_mid_ratio"]) / (c["amp_hi_ratio"] - c["amp_mid_ratio"])
        amp = c["amp_mid"] + t * (c["amp_hi"] - c["amp_mid"])
    return amp, ratio


# ---------------- 主計分 ----------------
def score_event(rows: list[ChipRow], expected_direction: str,
                direction_confidence: str = "high") -> ValidationResult:
    """對一個事件算市場驗證分數。rows = D0 起的 chip_data（升冪）。"""
    c = CONFIG
    if not rows:
        return ValidationResult(None, "observing", False,
                                validation_breakdown={"divergence": False},
                                chip_evidence=[],
                                debug={"note": "無 D0 之後的籌碼資料"})

    d0 = rows[0]
    i_sign, i_str, i_dbg = inst_signal(rows)
    p_sign, p_str, k, p_dbg = price_signal(d0)
    amp, vol_ratio = volume_amplifier(rows)

    # 狀態機
    if k >= 5:
        state = "verified"
    elif k >= 1:
        state = "preliminary"
    else:
        state = "observing"

    # 人話證據（不論給不給分數都要有——這是「可拆解」的呈現層）
    evidence = _build_evidence(i_dbg, p_dbg, vol_ratio, i_sign, p_sign)

    # 中性方向或低把握度：不給分數、不跑閘門，只列證據（SD §2.5）
    if expected_direction == "中性" or direction_confidence == "low":
        return ValidationResult(
            None, state, False,
            validation_breakdown=_build_breakdown(i_dbg, p_dbg, vol_ratio, False,
                                                  note="方向把握度低/中性，僅列原始數據"),
            chip_evidence=evidence,
            debug={"inst": i_dbg, "price": p_dbg, "vol_ratio": vol_ratio,
                   "skipped": "direction_confidence=low or 中性"},
        )

    e = 1 if expected_direction == "利多" else -1  # 利空 = -1

    contrib_inst = c["w_inst"] * i_str * (i_sign * e)
    contrib_price = c["w_price"] * p_str * (p_sign * e)
    raw = 50 + (contrib_inst + contrib_price) * amp

    # 極端值軟壓縮（附錄 §E「上限調整」）：>85 或 <15 的部分打 3 折，
    # 避免分數看起來像「百分之百確定」——分數描述的是已發生反應，不是保證。
    if raw > 85:
        raw = 85 + (raw - 85) * 0.3
    elif raw < 15:
        raw = 15 - (15 - raw) * 0.3

    # 一致性閘門：法人與股價「彼此」反向（都非 0）→ 壓回 40–55
    divergence = (i_sign != 0 and p_sign != 0 and i_sign == -p_sign)
    if divergence:
        score = max(c["gate_lo"], min(c["gate_hi"], round(raw)))
    else:
        score = max(0, min(100, round(raw)))

    return ValidationResult(
        int(score), state, divergence,
        validation_breakdown=_build_breakdown(i_dbg, p_dbg, vol_ratio, divergence),
        chip_evidence=evidence,
        debug={"inst_sign": i_sign, "inst_strength": round(i_str, 3),
               "contrib_inst": round(contrib_inst, 1),
               "price_sign": p_sign, "price_strength": round(p_str, 3),
               "contrib_price": round(contrib_price, 1),
               "amp": round(amp, 3), "vol_ratio": round(vol_ratio, 2),
               "raw": round(raw, 1), "k": k, **i_dbg, **p_dbg},
    )


def _fmt_pct(x: float) -> str:
    return f"{x*100:+.1f}%"


def _build_breakdown(i_dbg: dict, p_dbg: dict, vol_ratio: float,
                     divergence: bool, note: str | None = None) -> dict:
    cons = i_dbg["consecutive"]
    inst_txt = (f"外資連 {abs(cons)} 日{'買' if cons > 0 else '賣'}超，"
                f"日均淨額占均量 {_fmt_pct(i_dbg['mean_net_pct'])}") if cons else \
               f"法人日均淨額占均量 {_fmt_pct(i_dbg['mean_net_pct'])}"
    if p_dbg["return"] is not None:
        price_txt = (f"消息後 {p_dbg['k']} 日 {_fmt_pct(p_dbg['return'])}"
                     f"（{p_dbg['z']:+.1f}σ）" if p_dbg["z"] is not None
                     else f"消息後 {p_dbg['k']} 日 {_fmt_pct(p_dbg['return'])}")
    else:
        price_txt = "報酬未到期（觀察中）"
    out = {
        "foreign": inst_txt,
        "investment_trust": "投信動向併入法人合計（MVP）",
        "volume": f"最高量比為 20 日均量的 {vol_ratio:.1f} 倍",
        "price": price_txt,
        "divergence": divergence,
    }
    if note:
        out["note"] = note
    return out


def _build_evidence(i_dbg: dict, p_dbg: dict, vol_ratio: float,
                    i_sign: int, p_sign: int) -> list[dict]:
    ev = []
    cons = i_dbg["consecutive"]
    if cons:
        ev.append({"label": "外資",
                   "detail": f"連續{'買' if cons > 0 else '賣'}超 {abs(cons)} 天",
                   "direction": "看多" if cons > 0 else "看空"})
    ev.append({"label": "法人淨額",
               "detail": f"日均占 20 日均量 {_fmt_pct(i_dbg['mean_net_pct'])}",
               "direction": "看多" if i_sign > 0 else ("看空" if i_sign < 0 else "中性")})
    ev.append({"label": "成交量",
               "detail": f"量比 {vol_ratio:.1f} 倍",
               "direction": "中性"})
    if p_dbg["return"] is not None:
        ev.append({"label": "股價",
                   "detail": f"{p_dbg['k']} 日 {_fmt_pct(p_dbg['return'])}",
                   "direction": "看多" if p_sign > 0 else ("看空" if p_sign < 0 else "中性")})
    return ev


# ---------------- 事件表整合 ----------------
def score_and_update_event(conn, event_id: str, ticker: str, d0: Date,
                           expected_direction: str, direction_confidence: str = "high",
                           dry_run: bool = False) -> ValidationResult:
    """算分並回寫 events 表（market_validation / breakdown / evidence / verify_state）。"""
    if not TICKER_4DIGIT.match(ticker):
        raise ValueError(f"{ticker} 非 4 位數普通股代號（ETF/權證不做市場驗證）")
    rows = load_chip_rows(conn, ticker, d0)
    res = score_event(rows, expected_direction, direction_confidence)
    if not dry_run:
        import json
        with conn.cursor() as cur:
            cur.execute(
                """update events set market_validation=%s, validation_breakdown=%s,
                       chip_evidence=%s, verify_state=%s, updated_at=now()
                   where event_id=%s""",
                (res.market_validation, json.dumps(res.validation_breakdown, ensure_ascii=False),
                 json.dumps(res.chip_evidence, ensure_ascii=False), res.verify_state, event_id),
            )
        conn.commit()
    return res


# ---------------- CLI（demo / T24 驗證用） ----------------
def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="市場驗證分數 demo")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--date", required=True, help="事件 D0（YYYY-MM-DD，第一個可反應的交易日）")
    ap.add_argument("--direction", default="利多", choices=["利多", "利空", "中性"])
    ap.add_argument("--confidence", default="high", choices=["high", "low"])
    args = ap.parse_args()

    conn = psycopg.connect(_db_url(), connect_timeout=30)
    try:
        d0 = Date.fromisoformat(args.date)
        rows = load_chip_rows(conn, args.ticker, d0)
        res = score_event(rows, args.direction, args.confidence)
        print(f"=== {args.ticker} 事件 D0={args.date} 預期方向={args.direction} ===")
        print(f"市場驗證分數: {res.market_validation}  狀態: {res.verify_state}"
              f"  分歧: {res.divergence}")
        print("拆解:", res.validation_breakdown)
        print("證據:")
        for e in res.chip_evidence:
            print("  ", e)
        print("debug:", res.debug)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
