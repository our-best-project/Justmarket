"""T24 評分行為合理性驗證：
  PART1 市場驗證分數 — 參數敏感度（全市場 in-memory）
  PART2 一致性閘門正確性斷言
  PART3 跨天平滑度（全市場統計）＋ 混合視窗改良方案對比
  PART4 T15 重要性 — 權重敏感度（3000 合成事件）
"""
import copy
import io
import itertools
import os
import statistics as st
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from datetime import date

import psycopg

from backend.scoring import importance as imp
from backend.scoring import market_validation as mv
from backend.scoring.market_validation import ChipRow, score_event

DB_URL = os.environ["DATABASE_URL"]  # 執行前先把 .env 的 DATABASE_URL 匯入環境變數
D0 = date.fromisoformat("2026-06-24")

# ---------- 一次載入全市場 D0..D0+5 資料 ----------
conn = psycopg.connect(DB_URL, connect_timeout=30)
cur = conn.cursor()
cur.execute(r"""select ticker, date, close, volume, foreign_net, trust_net, dealer_net,
                       foreign_consecutive_days, net_vs_avg20_volume_pct,
                       volume_ratio_vs_avg20, return_1d, return_3d, return_5d, sigma_20d
                from chip_data
                where date >= '2026-06-24' and date <= '2026-07-01'
                  and ticker ~ '^[1-9]\d{3}$' and close > 0
                order by ticker, date""")
data = {}
for row in cur.fetchall():
    tk = row[0]
    data.setdefault(tk, []).append(ChipRow(*row[1:]))
conn.close()
# 只留 D0 有列的
data = {tk: rows[:6] for tk, rows in data.items() if rows and rows[0].date == D0}
print(f"載入 {len(data)} 檔普通股（D0={D0}，D0..D+5 in-memory）\n")

def spearman(a, b):
    def rank(x):
        idx = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0]*len(x); i = 0
        while i < len(idx):
            j = i
            while j+1 < len(idx) and x[idx[j+1]] == x[idx[i]]: j += 1
            avg = (i+j)/2 + 1
            for k in range(i, j+1): r[idx[k]] = avg
            i = j+1
        return r
    ra, rb = rank(a), rank(b)
    ma, mb = st.mean(ra), st.mean(rb)
    cov = sum((x-ma)*(y-mb) for x, y in zip(ra, rb))
    va = sum((x-ma)**2 for x in ra); vb = sum((y-mb)**2 for y in rb)
    return cov/(va*vb)**0.5 if va and vb else float('nan')

def band(v):
    return 4 if v>=80 else 3 if v>=60 else 2 if v>=45 else 1 if v>=20 else 0

def scan(direction="利多"):
    out = {}
    for tk, rows in data.items():
        r = score_event(rows, direction)
        if r.market_validation is not None:
            out[tk] = r
    return out

# ---------- PART 1：參數敏感度 ----------
print("="*72)
print("PART 1｜市場驗證分數 參數敏感度（基準：D0=6/24 全市場假設利多）")
print("="*72)
BASE_CONFIG = copy.deepcopy(mv.CONFIG)
base = scan()
base_scores = {tk: r.market_validation for tk, r in base.items()}
tks = sorted(base_scores)
bvec = [base_scores[t] for t in tks]
print(f"基準：{len(tks)} 檔｜均值 {st.mean(bvec):.1f}｜分歧率 "
      f"{100*sum(1 for r in base.values() if r.divergence)/len(base):.1f}%\n")

variants = [
    ("w_inst 25→20",        {"w_inst": 20.0}),
    ("w_inst 25→30",        {"w_inst": 30.0}),
    ("w_price 20→15",       {"w_price": 15.0}),
    ("w_price 20→25",       {"w_price": 25.0}),
    ("inst_netpct_cap 0.20→0.15", {"inst_netpct_cap": 0.15}),
    ("inst_netpct_cap 0.20→0.25", {"inst_netpct_cap": 0.25}),
    ("price_z_div 2.0→1.5", {"price_z_div": 1.5}),
    ("price_z_div 2.0→2.5", {"price_z_div": 2.5}),
    ("inst_days_weight 0.3→0.2", {"inst_days_weight": 0.2}),
    ("inst_days_weight 0.3→0.4", {"inst_days_weight": 0.4}),
    ("閘門 40-55→35-55",     {"gate_lo": 35}),
    ("閘門 40-55→40-60",     {"gate_hi": 60}),
    ("量放大 1.15→1.25",     {"amp_hi": 1.25}),
]
print(f"{'參數擾動':<28}{'Spearman':>9}{'同帶%':>8}{'平均|Δ|':>9}{'最大|Δ|':>9}{'分歧率%':>9}")
for name, patch in variants:
    mv.CONFIG.update(patch)
    v = scan()
    mv.CONFIG.clear(); mv.CONFIG.update(copy.deepcopy(BASE_CONFIG))
    common = [t for t in tks if t in v]
    a = [base_scores[t] for t in common]
    b = [v[t].market_validation for t in common]
    rho = spearman(a, b)
    same_band = 100*sum(1 for x, y in zip(a, b) if band(x) == band(y))/len(common)
    mean_d = st.mean(abs(x-y) for x, y in zip(a, b))
    max_d = max(abs(x-y) for x, y in zip(a, b))
    div_rate = 100*sum(1 for r in v.values() if r.divergence)/len(v)
    print(f"{name:<28}{rho:>9.4f}{same_band:>8.1f}{mean_d:>9.2f}{max_d:>9d}{div_rate:>9.1f}")

# ---------- PART 2：閘門正確性斷言 ----------
print()
print("="*72)
print("PART 2｜一致性閘門正確性（全市場斷言）")
print("="*72)
viol_flag = viol_range = 0
div_list = []
for tk, r in base.items():
    i_sign = r.debug.get("inst_sign"); p_sign = r.debug.get("price_sign")
    should = (i_sign not in (0, None) and p_sign not in (0, None) and i_sign == -p_sign)
    if should != r.divergence: viol_flag += 1
    if r.divergence and not (40 <= r.market_validation <= 55): viol_range += 1
    if r.divergence: div_list.append((tk, r))
print(f"閘門旗標與訊號方向不一致: {viol_flag} 檔（應為 0）")
print(f"分歧但分數落在 40–55 之外: {viol_range} 檔（應為 0）")
print(f"分歧事件總數: {len(div_list)}（{100*len(div_list)/len(base):.1f}%）")
strong = [(tk, r) for tk, r in div_list
          if abs(r.debug.get("z") or 0) >= 1.5 and abs(r.debug.get("mean_net_pct") or 0) >= 0.10]
print(f"強背離案例（|z|≥1.5σ 且 法人占均量≥10%）: {len(strong)} 檔，例：")
for tk, r in strong[:5]:
    print(f"  {tk} {r.market_validation}分  {r.validation_breakdown['foreign']}｜{r.validation_breakdown['price']}")

# ---------- PART 3：跨天平滑度＋混合視窗對比 ----------
print()
print("="*72)
print("PART 3｜跨天平滑度：現行「取最長到期視窗」 vs 改良「1/3/5 加權混合」")
print("="*72)

def mask(rows, k):
    rs = copy.deepcopy(rows)
    if k < 1: rs[0].return_1d = None
    if k < 3: rs[0].return_3d = None
    if k < 5: rs[0].return_5d = None
    return rs[:1+k]

def blended_price_signal(d0row):
    """改良案：1/3/5 日 z 加權混合（w=0.2/0.3/0.5，僅用已到期視窗、權重重正規化）"""
    c = mv.CONFIG
    parts = []
    for k, ret, w in ((1, d0row.return_1d, 0.2), (3, d0row.return_3d, 0.3), (5, d0row.return_5d, 0.5)):
        if ret is not None and d0row.sigma_20d and d0row.sigma_20d > 0:
            parts.append((ret/(d0row.sigma_20d*k**0.5), w, k))
    if not parts:
        return 0, 0.0, 0
    tw = sum(w for _, w, _ in parts)
    z = sum(zv*w for zv, w, _ in parts)/tw
    kmax = max(k for _, _, k in parts)
    sign = 0 if abs(z) < c["price_min_z"] else (1 if z > 0 else -1)
    return sign, min(abs(z)/c["price_z_div"], 1.0), kmax

def score_blended(rows, direction="利多"):
    """與 score_event 同構，只換股價訊號為混合視窗（分析用副本）"""
    c = mv.CONFIG
    d0 = rows[0]
    i_sign, i_str, _ = mv.inst_signal(rows)
    p_sign, p_str, k = blended_price_signal(d0)
    amp, _ = mv.volume_amplifier(rows)
    e = 1 if direction == "利多" else -1
    raw = 50 + (c["w_inst"]*i_str*(i_sign*e) + c["w_price"]*p_str*(p_sign*e))*amp
    if raw > 85: raw = 85+(raw-85)*0.3
    elif raw < 15: raw = 15-(15-raw)*0.3
    div = (i_sign != 0 and p_sign != 0 and i_sign == -p_sign)
    if div: return max(c["gate_lo"], min(c["gate_hi"], round(raw))), div
    return max(0, min(100, round(raw))), div

def smoothness(score_fn):
    """全市場：D0→D+1→D+3→D+5 分數序列的最大跳動分佈"""
    max_jumps = []
    for tk, rows in data.items():
        prev = None; mx = 0
        for k in (0, 1, 3, 5):
            if len(rows) < 1+k: break
            s = score_fn(mask(rows, k))
            if s is None: continue
            if prev is not None: mx = max(mx, abs(s-prev))
            prev = s
        else:
            max_jumps.append(mx)
    return max_jumps

cur_fn = lambda rs: score_event(rs, "利多").market_validation
ble_fn = lambda rs: score_blended(rs, "利多")[0]
for label, fn in [("現行(最長到期視窗)", cur_fn), ("改良(1/3/5加權混合)", ble_fn)]:
    j = smoothness(fn)
    p95 = sorted(j)[int(0.95*len(j))]
    print(f"  {label:<22} n={len(j)}  平均最大跳動={st.mean(j):.1f}分  中位={st.median(j):.0f}"
          f"  P95={p95}  跳動>15分比例={100*sum(1 for x in j if x>15)/len(j):.1f}%"
          f"  >25分={100*sum(1 for x in j if x>25)/len(j):.1f}%")
# 最終分數一致性：混合案 vs 現行案在 D+5 完整資料下
fin_cur = {tk: cur_fn(rows) for tk, rows in data.items()}
fin_ble = {tk: ble_fn(rows) for tk, rows in data.items()}
common = [t for t in fin_cur if fin_cur[t] is not None and fin_ble[t] is not None]
rho = spearman([fin_cur[t] for t in common], [fin_ble[t] for t in common])
sb = 100*sum(1 for t in common if band(fin_cur[t]) == band(fin_ble[t]))/len(common)
print(f"  兩案在 D+5 完整資料下：Spearman={rho:.4f}  同帶率={sb:.1f}%（混合案不改變最終結論）")

# ---------- PART 4：T15 權重敏感度 ----------
print()
print("="*72)
print("PART 4｜T15 重要性 權重敏感度（3000 合成事件全組合）")
print("="*72)
POP = []
for sc, (src, status), cats, tkr, heat in itertools.product(
        [1, 2, 3, 5, 8],
        [("MOPS", "official_confirmed"), ("工商時報", "developing"),
         ("鉅亨網", "developing"), ("PTT", "rumor_unconfirmed"), ("某部落格", "developing")],
        [["法說"], ["財報"], ["政策"], ["營收"], ["法人動向"], ["技術突破"]],
        [[], ["2330"], ["4915"], ["2330", "2317"], ["1101", "2002", "2603"]],
        [None, 1, 2, 5]):   # 熱度：無資料 / 乏人問津 / 中性 / 快速發酵
    POP.append(dict(source_count=sc, members=[{"source": src}], status=status,
                    related_tickers=tkr, categories=cats, heat_sources_24h=heat))
print(f"合成事件母體：{len(POP)} 件（方案B：四維×熱度放大器）")

BASE_W = copy.deepcopy(imp.CONFIG["weights"])
def stars_all():
    return [imp.score_importance(**ev).stars for ev in POP]
def totals_all():
    return [imp.score_importance(**ev).total for ev in POP]

base_stars = stars_all()
base_tot = totals_all()
top5pct = set(sorted(range(len(POP)), key=lambda i: -base_tot[i])[:150])

print(f"\n{'權重擾動(±5pp,其餘按比例重配)':<34}{'星等不變%':>10}{'最大星移':>9}{'Top5%重疊':>10}")
for dim in ["breadth", "authority", "impact", "category"]:
    for delta in (+0.05, -0.05):
        w = copy.deepcopy(BASE_W)
        w[dim] = max(0.01, w[dim] + delta)
        rest = [k for k in w if k != dim]
        s = sum(w[k] for k in rest)
        scale = (1 - w[dim]) / s
        for k in rest: w[k] *= scale
        imp.CONFIG["weights"] = w
        stars_v = stars_all(); tot_v = totals_all()
        imp.CONFIG["weights"] = copy.deepcopy(BASE_W)
        same = 100*sum(1 for a, b in zip(base_stars, stars_v) if a == b)/len(POP)
        max_shift = max(abs(a-b) for a, b in zip(base_stars, stars_v))
        top_v = set(sorted(range(len(POP)), key=lambda i: -tot_v[i])[:150])
        overlap = 100*len(top5pct & top_v)/150
        sgn = "+" if delta > 0 else "−"
        print(f"{dim:<10}{sgn}5pp{'':<20}{same:>10.1f}{max_shift:>9d}{overlap:>10.1f}")

# 熱度放大器參數擾動（方案 B 新增）
print(f"\n{'熱度參數擾動':<34}{'星等不變%':>10}{'最大星移':>9}{'Top5%重疊':>10}")
BASE_HEAT = {k: imp.CONFIG[k] for k in
             ("heat_baseline_sources", "heat_lo", "heat_hi")}
for name, patch in [
    ("baseline 2.0→1.5", {"heat_baseline_sources": 1.5}),
    ("baseline 2.0→3.0", {"heat_baseline_sources": 3.0}),
    ("hi 1.15→1.10",     {"heat_hi": 1.10}),
    ("hi 1.15→1.20",     {"heat_hi": 1.20}),
    ("lo 0.90→0.85",     {"heat_lo": 0.85}),
]:
    imp.CONFIG.update(patch)
    stars_v = stars_all(); tot_v = totals_all()
    imp.CONFIG.update(BASE_HEAT)
    same = 100*sum(1 for a, b in zip(base_stars, stars_v) if a == b)/len(POP)
    max_shift = max(abs(a-b) for a, b in zip(base_stars, stars_v))
    top_v = set(sorted(range(len(POP)), key=lambda i: -tot_v[i])[:150])
    overlap = 100*len(top5pct & top_v)/150
    print(f"{name:<34}{same:>10.1f}{max_shift:>9d}{overlap:>10.1f}")
print("\n（判讀：星等不變% 越高、Top5% 重疊越高 → 結論對權重越不敏感）")
