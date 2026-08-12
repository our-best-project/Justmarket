"""Golden set 工具（T23 分類準確率驗證,MMJSUN 半場）:抽題 → 兩人獨立標 → 一致率 → 定案 → micro-F1。

流程（對齊 07 範本 §4 與 研究_品誠 §3.5）:
    1. sample     抽題,產生 questions.json + 兩份標註範本（A/B 各自獨立填,不要偷看對方）
    2. agreement  兩份標註的逐欄一致率 + Cohen's kappa + 分歧清單（拿去討論）
    3. merge      一致的自動定案;分歧的標 CONFLICT,討論後人工改掉
    4. score      golden_final vs 模型輸出報告,算 categories micro-F1（主指標,目標 ≥85%）
                  + per-label P/R + 混淆對 + status/方向 accuracy

用法（在 backend/ 下執行）:
    python -m eventsignal.llm.golden_set sample [--n 60]
    python -m eventsignal.llm.golden_set agreement
    python -m eventsignal.llm.golden_set merge
    python -m eventsignal.llm.golden_set score <實驗報告.json 可多個>
    python -m eventsignal.llm.golden_set --self-test

檔案都在本模組的 golden/ 子資料夾（標註規則見 golden/README.md）。
題目組成:預設 60 題 = 實測過的分層抽樣 40 題（直接沿用,已有雙模型×兩版 prompt 輸出可對照）
+ 隨機新抽 20 題（seed 固定可重現）。content 與測試時一樣截前 500 字——
標註者看到的輸入必須和模型看到的一致,F1 才公平。

一魚兩吃:定案後的 golden_final.json 同時是未來方案 G（向量+淺層分類頭）的免費訓練資料。
"""

import json
import random
import sys
from collections import Counter
from pathlib import Path

from eventsignal.llm import prompts

MODULE_DIR = Path(__file__).resolve().parent            # src/eventsignal/llm/
# repo 根：llm → eventsignal → src → <repo>。data/ 是共用語料的家。
# 舊版按目錄名 "workspace" 往上找；那層已在 2026-08 重構中移除。
REPO_ROOT = MODULE_DIR.parents[2]
GOLDEN_DIR = MODULE_DIR / "golden"
NEWS_JSON = REPO_ROOT / "data" / "cnyes_news_2026.json"
# 實測那批 40 題的原始報告。從未進版控（產物太大），只有跑過實測的機器上才有；
# 缺它時 cmd_sample 會 FileNotFoundError——questions.json 已在 golden/ 內，重抽才需要這支。
SAMPLE40_REPORT = GOLDEN_DIR / "llm_test_sample40_report.json"

QUESTIONS = GOLDEN_DIR / "questions.json"
ANNOT_A = GOLDEN_DIR / "annotations_A.json"
ANNOT_B = GOLDEN_DIR / "annotations_B.json"
FINAL = GOLDEN_DIR / "golden_final.json"

CONTENT_CHARS = 500          # 與 llm_test 腳本一致:標註者與模型看同樣的輸入
EXTRA_SEED = 43              # 新抽 20 題的 seed（40 題沿用實測那批,不重抽）
LABEL_FIELDS = ["categories", "status", "expected_direction", "direction_confidence"]


# ─────────────────────────── 1. sample ───────────────────────────

def cmd_sample(n: int = 60, force: bool = False) -> None:
    """產生 questions.json 與兩份空白標註範本。預設 60 = 實測 40 題 + 新抽 20 題。"""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for f in (ANNOT_A, ANNOT_B):
        if f.exists() and not force:
            sys.exit(f"❌ {f.name} 已存在(可能已開始標註),不覆蓋。確定要重來請加 --force")

    all_items = {it["newsId"]: it for it in json.load(open(NEWS_JSON, encoding="utf-8"))}
    # 前 40 題直接沿用實測批(從報告檔取 newsId,保證與模型跑過的題目一模一樣)
    tested_ids = [r["newsId"] for r in json.load(open(SAMPLE40_REPORT, encoding="utf-8"))]
    rest = [nid for nid in all_items if nid not in set(tested_ids)]
    extra_ids = random.Random(EXTRA_SEED).sample(rest, max(0, n - len(tested_ids)))

    questions = []
    for nid in tested_ids + extra_ids:
        it = all_items[nid]
        questions.append({
            "newsId": nid,
            "title": it["title"],
            "content": it["content"][:CONTENT_CHARS],
            "url": it["url"],
        })
    QUESTIONS.write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")

    template = [{
        "newsId": q["newsId"],
        "title": q["title"],
        "categories": [],               # 從 9 類挑,可多選(至少 1 個)
        "status": "",                   # 4 值擇一
        "expected_direction": "",       # 利多|利空|中性
        "direction_confidence": "",     # high|low
    } for q in questions]
    for f in (ANNOT_A, ANNOT_B):
        f.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ 產生 {len(questions)} 題(實測沿用 {len(tested_ids)} + 新抽 {len(extra_ids)})")
    print(f"   題目:{QUESTIONS}\n   標註:{ANNOT_A.name} / {ANNOT_B.name}(兩人各填一份,規則見 README.md)")


# ─────────────────────────── 2. agreement ───────────────────────────

def _load_annotations(path: Path) -> dict[int, dict]:
    """讀標註檔,驗證 enum 合法性;回傳 newsId → 標註。非法值直接列出來擋下。"""
    data = json.load(open(path, encoding="utf-8"))
    problems = []
    for r in data:
        nid = r["newsId"]
        if not r["categories"] or not all(c in prompts.CATEGORIES for c in r["categories"]):
            problems.append(f"{path.name} #{nid}: categories 非法或空 {r['categories']}")
        if r["status"] not in prompts.EVENT_STATUSES:
            problems.append(f"{path.name} #{nid}: status 非法 {r['status']!r}")
        if r["expected_direction"] not in prompts.DIRECTIONS:
            problems.append(f"{path.name} #{nid}: expected_direction 非法 {r['expected_direction']!r}")
        if r["direction_confidence"] not in prompts.CONFIDENCES:
            problems.append(f"{path.name} #{nid}: direction_confidence 非法 {r['direction_confidence']!r}")
    if problems:
        print("\n".join(problems))
        sys.exit(f"❌ {path.name} 有 {len(problems)} 個未填/非法欄位,先補完再算一致率")
    return {r["newsId"]: r for r in data}


def _kappa(pairs: list[tuple[str, str]]) -> float:
    """Cohen's kappa(單選欄位)。修正「隨便標也會有的巧合一致」。"""
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[l] * cb[l] for l in set(ca) | set(cb)) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def cmd_agreement() -> None:
    """兩份標註的一致率報告 + 分歧清單(討論定案用)。"""
    A, B = _load_annotations(ANNOT_A), _load_annotations(ANNOT_B)
    common = sorted(set(A) & set(B))
    titles = {r["newsId"]: r["title"] for r in json.load(open(QUESTIONS, encoding="utf-8"))}
    print(f"共同題數:{len(common)}\n")

    exact_cat = sum(set(A[k]["categories"]) == set(B[k]["categories"]) for k in common)
    jac = sum(len(set(A[k]["categories"]) & set(B[k]["categories"]))
              / len(set(A[k]["categories"]) | set(B[k]["categories"])) for k in common)
    print(f"categories 整組一致:{exact_cat}/{len(common)}｜Jaccard 平均:{jac/len(common):.2f}")
    for f in ("status", "expected_direction", "direction_confidence"):
        pairs = [(A[k][f], B[k][f]) for k in common]
        agree = sum(a == b for a, b in pairs)
        print(f"{f}:{agree}/{len(common)}(kappa {_kappa(pairs):.2f})")

    print("\n=== 分歧清單(拿去討論,定案後改 golden_final) ===")
    count = 0
    for k in common:
        diffs = []
        if set(A[k]["categories"]) != set(B[k]["categories"]):
            diffs.append(f"categories A:{A[k]['categories']} / B:{B[k]['categories']}")
        for f in ("status", "expected_direction", "direction_confidence"):
            if A[k][f] != B[k][f]:
                diffs.append(f"{f} A:{A[k][f]} / B:{B[k][f]}")
        if diffs:
            count += 1
            print(f"#{k} {titles.get(k, '')[:32]}")
            for d in diffs:
                print(f"    {d}")
    print(f"\n分歧題數:{count}/{len(common)}")


# ─────────────────────────── 3. merge ───────────────────────────

def cmd_merge() -> None:
    """一致的欄位自動定案;分歧的欄位寫成 CONFLICT 佔位,討論後人工改。"""
    A, B = _load_annotations(ANNOT_A), _load_annotations(ANNOT_B)
    questions = json.load(open(QUESTIONS, encoding="utf-8"))
    final, conflicts = [], 0
    for q in questions:
        k = q["newsId"]
        entry = {"newsId": k, "title": q["title"]}
        has_conflict = False
        for f in LABEL_FIELDS:
            a, b = A[k][f], B[k][f]
            same = set(a) == set(b) if f == "categories" else a == b
            entry[f] = a if same else {"CONFLICT": {"A": a, "B": b}}
            has_conflict |= not same
        conflicts += has_conflict
        final.append(entry)
    FINAL.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ {FINAL.name}:{len(final)} 題,其中 {conflicts} 題含 CONFLICT(討論後手動改成定案值)")


# ─────────────────────────── 4. score ───────────────────────────

def _load_final() -> dict[int, dict]:
    """讀定案檔;仍含 CONFLICT 的題目跳過並提醒。"""
    data = json.load(open(FINAL, encoding="utf-8"))
    clean, skipped = {}, 0
    for r in data:
        if any(isinstance(r[f], dict) for f in LABEL_FIELDS):
            skipped += 1
            continue
        clean[r["newsId"]] = r
    if skipped:
        print(f"⚠️ {skipped} 題仍含 CONFLICT,未納入計分\n")
    return clean


def cmd_score(report_files: list[str]) -> None:
    """golden_final vs 模型輸出:categories micro-F1(主指標)+ per-label + 混淆對 + 各欄 accuracy。"""
    gold = _load_final()
    for rf in report_files:
        preds = {r["newsId"]: r["output"] for r in json.load(open(rf, encoding="utf-8")) if r.get("ok")}
        common = sorted(set(gold) & set(preds))
        if not common:
            print(f"=== {Path(rf).name}:與 golden 無交集,跳過 ===\n")
            continue

        tp = fp = fn = 0
        per_label = {c: Counter() for c in prompts.CATEGORIES}   # tp/fp/fn per 類
        confusion = Counter()                                    # (gold獨有, pred獨有) 配對
        acc = Counter()
        for k in common:
            g, p = set(gold[k]["categories"]), set(preds[k]["categories"])
            tp += len(g & p); fp += len(p - g); fn += len(g - p)
            for c in g & p: per_label[c]["tp"] += 1
            for c in p - g: per_label[c]["fp"] += 1
            for c in g - p: per_label[c]["fn"] += 1
            for gc in g - p:
                for pc in p - g:
                    confusion[(gc, pc)] += 1
            for f in ("status", "expected_direction", "direction_confidence"):
                acc[f] += gold[k][f] == preds[k][f]

        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        print(f"=== {Path(rf).name}(交集 {len(common)} 題) ===")
        verdict = "✅ 達標" if f1 >= 0.85 else "❌ 未達標"
        print(f"categories micro-F1:{f1:.3f}(P {prec:.3f} / R {rec:.3f})目標 ≥0.85 → {verdict}")
        for f in ("status", "expected_direction", "direction_confidence"):
            print(f"{f} accuracy:{acc[f]}/{len(common)} = {acc[f]/len(common):.3f}")
        print("per-label(P/R,樣本數=tp+fn):")
        for c in prompts.CATEGORIES:
            d = per_label[c]
            support = d["tp"] + d["fn"]
            if support + d["fp"] == 0:
                continue
            lp = d["tp"] / (d["tp"] + d["fp"]) if d["tp"] + d["fp"] else 0.0
            lr = d["tp"] / support if support else 0.0
            print(f"  {c}:P {lp:.2f} / R {lr:.2f}(n={support})")
        if confusion:
            print("最常混淆(標準答案→模型答成):",
                  ", ".join(f"{g}→{p}×{n}" for (g, p), n in confusion.most_common(5)))
        print()


# ─────────────────────────── self-test ───────────────────────────

def _self_test() -> None:
    """驗證 kappa 與 micro-F1 的數學(手算對照),不碰任何檔案。"""
    # kappa:完全一致=1;A 全標 X、B 一半一半 → po=0.5, pe=0.5, kappa=0
    assert _kappa([("x", "x"), ("y", "y")]) == 1.0
    assert abs(_kappa([("x", "x"), ("x", "y")]) - 0.0) < 1e-9

    # micro-F1 手算:gold={A,B},{C};pred={A},{C,B} → tp=2, fp=1, fn=1
    # P=2/3, R=2/3, F1=2/3
    g = [{"A", "B"}, {"C"}]
    p = [{"A"}, {"C", "B"}]
    tp = sum(len(a & b) for a, b in zip(g, p))
    fp = sum(len(b - a) for a, b in zip(g, p))
    fn = sum(len(a - b) for a, b in zip(g, p))
    prec, rec = tp / (tp + fp), tp / (tp + fn)
    f1 = 2 * prec * rec / (prec + rec)
    assert (tp, fp, fn) == (2, 1, 1) and abs(f1 - 2 / 3) < 1e-9
    print("golden_set.py 自我測試通過(kappa + micro-F1 數學)")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--self-test" in args:
        _self_test()
    elif not args:
        print(__doc__)
    elif args[0] == "sample":
        n = int(args[args.index("--n") + 1]) if "--n" in args else 60
        cmd_sample(n, force="--force" in args)
    elif args[0] == "agreement":
        cmd_agreement()
    elif args[0] == "merge":
        cmd_merge()
    elif args[0] == "score":
        if len(args) < 2:
            sys.exit("用法:score <實驗報告.json 可多個>")
        cmd_score(args[1:])
    else:
        sys.exit(f"未知子命令:{args[0]}(可用:sample / agreement / merge / score)")
