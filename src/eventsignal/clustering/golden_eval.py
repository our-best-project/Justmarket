"""T23 golden set 驗證：現行聚類（Agglo average + 日期/ticker 雙閘門）對人工標註的 P/R/F1。

README 驗收目標 F1≈0.83（原值為初版 0.65 閾值在早期 50 篇 golden set 的實測，
該標註未留存；本檔對 golden_set.json 重建版評測，見該檔 annotation_note）。
歷史數字（同一份 golden、induced 口徑）：初版連通分量 0.043、二版 HDBSCAN 0.224、
現行定案 0.914（REPORT §3）。

指標：pairwise Precision / Recall / F1——golden set 內兩兩文章配對，
「系統判同事件」對「人工判同事件」。

兩種跑法都報：
  A. standalone：只對 golden set 的 N 篇聚類（對齊研究_庭右.md 的原協定）。
  B. induced：全語料 4486 篇聚類後，把分群限縮到 golden set（貼近正式管線行為）。

用法（在 backend/ 下，需 embedding_Timyo/out/ 向量快取）：
    python -m eventsignal.clustering.golden_eval
"""
import json
import os
import sys
from itertools import combinations

import numpy as np

from eventsignal.clustering.dedup import assemble_events, classify_periodic
from eventsignal.embedding.bge_m3 import OUT_DIR as EMB_OUT
from eventsignal.embedding.bge_m3 import fetch_pending_articles

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_PATH = os.path.join(HERE, "golden_set.json")


def load_corpus() -> tuple[list[dict], np.ndarray]:
    """讀凍結語料 + 向量快取，組成與 run.py 同構的 articles（逐列對齊 vectors）。"""
    raw = fetch_pending_articles()
    raw_by_id = {r["newsId"]: r for r in raw if r.get("newsId") is not None}
    vectors = np.load(os.path.join(EMB_OUT, "embeddings.npy"))
    with open(os.path.join(EMB_OUT, "meta.jsonl"), encoding="utf-8") as f:
        meta = [json.loads(line) for line in f]
    if len(meta) != len(vectors):
        raise SystemExit(f"快取不一致：meta {len(meta)} vs 向量 {len(vectors)}，請重跑向量化")
    articles = [{"id": m["newsId"], "title": m.get("title", ""), "date": m.get("date"),
                 "content": raw_by_id.get(m["newsId"], {}).get("content", ""),
                 "url": raw_by_id.get(m["newsId"], {}).get("url", "")} for m in meta]
    return articles, vectors


def pairwise_prf(gold_groups: list[list], pred_groups: list[list], universe: set) -> dict:
    """pairwise P/R/F1。不在任何 pred 群的文章視為自成單篇（週期過濾丟棄者即如此）。"""
    def pairs(groups):
        s = set()
        for g in groups:
            for a, b in combinations(sorted(x for x in g if x in universe), 2):
                s.add((a, b))
        return s

    gp, pp = pairs(gold_groups), pairs(pred_groups)
    tp = len(gp & pp)
    p = tp / len(pp) if pp else 1.0
    r = tp / len(gp) if gp else 1.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": p, "recall": r, "f1": f1,
            "gold_pairs": len(gp), "pred_pairs": len(pp), "tp": tp}


def eval_partition(tag: str, gold_groups: list[list], events: list[dict], universe: set) -> dict:
    pred = [[m["article_id"] for m in e["members"]] for e in events]
    covered = {aid for g in pred for aid in g}
    dropped = sorted(universe - covered)          # 被週期過濾丟掉的 golden 文章
    m = pairwise_prf(gold_groups, pred, universe)
    print(f"  {tag}: P={m['precision']:.3f}  R={m['recall']:.3f}  F1={m['f1']:.3f}"
          f"  (gold配對 {m['gold_pairs']}、預測配對 {m['pred_pairs']}、命中 {m['tp']}"
          f"{f'、週期過濾丟失 {len(dropped)} 篇: {dropped}' if dropped else ''})")
    return m


def show_errors(gold_groups: list[list], events: list[dict], universe: set,
                titles: dict, limit: int = 12) -> None:
    """列出誤合（不同事件被聚在一起）與漏合（同事件被拆開）的配對，供人工檢視。"""
    gold_pair = {frozenset(p) for g in gold_groups for p in combinations(g, 2)}
    pred = [[m["article_id"] for m in e["members"] if m["article_id"] in universe]
            for e in events]
    pred_pair = {frozenset(p) for g in pred for p in combinations(g, 2)}
    merged = [p for p in pred_pair - gold_pair]
    split = [p for p in gold_pair - pred_pair]
    print(f"  誤合 {len(merged)} 對：")
    for p in merged[:limit]:
        a, b = sorted(p)
        print(f"    ✗ {titles[a][:34]} ↔ {titles[b][:34]}")
    print(f"  漏合 {len(split)} 對：")
    for p in split[:limit]:
        a, b = sorted(p)
        print(f"    ○ {titles[a][:34]} ↔ {titles[b][:34]}")


def main() -> None:
    golden = json.load(open(GOLDEN_PATH, encoding="utf-8"))
    gold_groups = [e["ids"] for e in golden["events"]]
    gold_ids = [i for g in gold_groups for i in g]
    universe = set(gold_ids)
    assert len(gold_ids) == len(universe), "golden set 有重複 id"

    articles, vectors = load_corpus()
    by_id = {a["id"]: a for a in articles}
    missing = universe - set(by_id)
    if missing:
        raise SystemExit(f"golden id 不在語料/快取中：{sorted(missing)}")
    titles = {i: by_id[i]["title"] for i in universe}

    n_multi = sum(1 for g in gold_groups if len(g) > 1)
    print(f"golden set：{len(gold_ids)} 篇 / {len(gold_groups)} 事件"
          f"（多篇 {n_multi}、單篇 {len(gold_groups) - n_multi}）  目標 F1≈0.83")

    # 週期過濾對 golden 的影響（正式管線會直接丟棄這些篇）
    flags = classify_periodic([titles[i] for i in gold_ids],
                              [by_id[i].get("date") for i in gold_ids])
    filtered = [i for i, f in zip(gold_ids, flags) if f]
    if filtered:
        print(f"⚠ {len(filtered)} 篇 golden 文章被週期規則判週期性（standalone 模式下）："
              f"{[titles[i][:24] for i in filtered]}")

    # A. standalone：只聚 golden set
    rows = [by_id[i] for i in gold_ids]
    idx = {a["id"]: r for r, a in enumerate(articles)}
    sub_vec = vectors[[idx[i] for i in gold_ids]]
    print("\n[A] standalone（只聚 golden 篇，對齊原 50 篇協定）")
    evs, _ = assemble_events(rows, sub_vec)
    eval_partition("agglo+雙閘門", gold_groups, evs, universe)

    # B. induced：全語料聚類後限縮到 golden
    print("\n[B] induced（全語料 4486 篇聚類後限縮，貼近正式管線）")
    best_events, _ = assemble_events(articles, vectors)
    eval_partition("agglo+雙閘門", gold_groups, best_events, universe)

    print("\n[B 誤差明細]（正式定案組態）")
    show_errors(gold_groups, best_events, universe, titles)


if __name__ == "__main__":
    main()
