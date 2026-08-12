"""二版 HDBSCAN 聚類：已被翻案的歷史方法，保留供對照/回歸。

⚠️ 非現行定案，勿接進正式管線。現行方法是 dedup.py 的
   Agglomerative(average) + 日期/ticker 雙閘門（golden induced F1=0.914）。

本檔重現 REPORT §4.1 的二版管線語意（原始實作為
experiments_everyone/timyo/cluster_events.py，該檔依賴實驗時期的
classification.jsonl 快取、已無法直接重跑，故在此以現行 API 重建；
原始腳本已於 2026-07-20 刪除，需要時自 git 歷史取回 commit 0b49ddd 之前的版本）：

  1. 先濾掉週期性欄目（§4.1：「對 --exclude-periodic 濾後的真事件向量聚類」）。
  2. HDBSCAN(min_cluster_size=3, min_samples=1, metric=euclidean)。
     向量先 L2 正規化 → euclidean 與 cosine 同序，故用 euclidean 不失語意。
  3. label = -1 的 noise **不構成事件**，只單獨計數（cluster_events.py:92-93
     `if lab == -1: continue`）——這是與現行 dedup.py 的關鍵語意差異：
     現行法讓每篇孤兒自成單篇事件，二版則直接把它們排除在事件清單外。
  4. 事件依篇數由多到少排序（cluster_events.py:108）。

翻案原因（REPORT §3，golden set induced 口徑）：
    HDBSCAN mcs3          F1=0.224（P=0.151 / R=0.429，全語料最大群 37 篇）
    Agglo avg + 雙閘門     F1=0.914（P=0.949 / R=0.881，最大群 10 篇）
密度聚類會把「內容像、時間遠」的家族（逐月營收、兩季法說）誤合成同一事件——
密度可達性沒有時間概念，而這正是新聞事件的關鍵維度。

⚠️ 與 REPORT §4.1 數字的可比性：§4.1 的掃描表（mcs=3 → 396 事件 / 1556 noise / 45%）
   量測於**第 2 輪**週期性規則（1023 篇週期性、3463 篇進聚類）。本檔用的是現行
   classify_periodic（**第 4 輪**，1031 篇週期性、3455 篇），故數字會略有出入，
   趨勢與量級可比、逐位數不可比。實測見下方 __main__ 的全語料模式。

用法：
    from backend.clustering.legacy_clusterers import cluster_into_events_hdbscan
    events, noise = cluster_into_events_hdbscan(ids, vectors, titles=titles, dates=dates)
    # events = [[3, 7, 12], ...]（依篇數排序）  noise = [5, 9, ...]（不成事件的孤兒）

重跑（在 backend/ 下）：
    python -m backend.clustering.legacy_clusterers          # 合成自我測試
    python -m backend.clustering.legacy_clusterers --corpus # 全語料 §4.1 對照
"""

import numpy as np

from backend.clustering.dedup import classify_periodic, normalize_vectors

HDBSCAN_MIN_CLUSTER_SIZE = 3     # REPORT §4.1 掃描定案值（二版時期建議值）
HDBSCAN_MIN_SAMPLES = 1


def cluster_hdbscan(
    vectors: np.ndarray,
    min_cluster_size: int = HDBSCAN_MIN_CLUSTER_SIZE,
    min_samples: int = HDBSCAN_MIN_SAMPLES,
) -> np.ndarray:
    """HDBSCAN 密度聚類。回傳每列 label（-1 = noise 獨立單篇）。

    向量先 L2 正規化 → euclidean 與 cosine 同序，故用 euclidean 不失語意。
    """
    from sklearn.cluster import HDBSCAN

    # sklearn HDBSCAN 需 >=2 樣本（n=1 直接 ValueError）；單篇無從成群 → noise
    if len(vectors) < 2:
        return np.full(len(vectors), -1)

    clusterer = HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric="euclidean",
        copy=True,
    )
    return clusterer.fit_predict(normalize_vectors(vectors))


def cluster_into_events_hdbscan(
    article_ids: list,
    vectors: np.ndarray,
    titles: list[str] | None = None,
    dates: list[str] | None = None,
    *,
    exclude_periodic: bool = True,
    min_cluster_size: int = HDBSCAN_MIN_CLUSTER_SIZE,
    min_samples: int = HDBSCAN_MIN_SAMPLES,
) -> tuple[list[list], list]:
    """二版管線：週期性過濾 → HDBSCAN → 事件清單（noise 不計入事件）。

    與 dedup.cluster_into_events 的契約差異（刻意保留，這正是二版的語意）：
      - 回傳 (events, noise_ids) 兩段，而非單一事件清單；
      - noise 不會被包成單篇事件，因此 events 攤平後 != 輸入 ids。

    Args:
        article_ids: 文章 id，順序需與 vectors 逐列對應。
        vectors: 形狀 (N, 1024) 的向量陣列。
        titles: 各篇標題；提供才能做週期性過濾。
        dates: 各篇日期（YYYY-MM-DD），供資料驅動週期偵測。
        exclude_periodic: 是否先濾掉週期性欄目（需 titles）。§4.1 語意為 True。
        min_cluster_size / min_samples: HDBSCAN 參數（§4.1 建議 3 / 1）。

    Returns:
        (events, noise_ids)：events 依篇數由多到少排序；noise_ids 為
        label=-1 的孤兒，週期性欄目**不在**其中（它們在更前面就被濾掉）。
    """
    if len(article_ids) != len(vectors):
        raise ValueError(f"id 數量（{len(article_ids)}）與向量列數（{len(vectors)}）不一致")
    if len(article_ids) == 0:
        return [], []

    ids = list(article_ids)
    vecs = np.asarray(vectors, dtype=np.float32)

    # 1. 週期性過濾（§4.1：對濾後的真事件向量聚類）
    if exclude_periodic and titles is not None:
        keep = [i for i, p in enumerate(classify_periodic(titles, dates)) if not p]
        ids = [ids[i] for i in keep]
        vecs = vecs[keep]
        if len(ids) == 0:
            return [], []

    # 2. HDBSCAN
    labels = cluster_hdbscan(vecs, min_cluster_size, min_samples)

    # 3. noise 不成事件，只計數（cluster_events.py:92-93）
    by_label: dict[int, list] = {}
    noise_ids = []
    for pos, label in enumerate(labels):
        if int(label) == -1:
            noise_ids.append(ids[pos])
        else:
            by_label.setdefault(int(label), []).append(ids[pos])

    # 4. 依篇數由多到少排序（cluster_events.py:108）
    events = sorted(by_label.values(), key=len, reverse=True)
    return events, noise_ids


# ─────────────────────────────────────────────────────────────
# 自我測試：本檔在主管線無呼叫者，若無測試會靜默腐爛
#   （這正是拆檔前的狀態——歷史演算法零覆蓋）
# ─────────────────────────────────────────────────────────────
def _self_test() -> None:
    rng = np.random.default_rng(42)

    # (1) 原始 labels：三組相近向量（4/3/3 篇）應聚成 3 群
    base_a, base_b, base_c = rng.normal(0, 1, size=(3, 1024))
    vectors = np.vstack([
        base + rng.normal(0, 0.01, size=(k, 1024))
        for base, k in [(base_a, 4), (base_b, 3), (base_c, 3)]
    ])
    labels = cluster_hdbscan(vectors)
    print(f"[HDBSCAN labels] {labels}")
    assert len({int(x) for x in labels if x != -1}) == 3, f"應聚成 3 群：{labels}"
    assert (labels[:4] == labels[0]).all() and labels[0] != -1, f"前 4 篇應同群：{labels}"

    # (2) 邊界：n=1 → noise（sklearn HDBSCAN 對單樣本會 ValueError，故提前短路）
    assert cluster_hdbscan(rng.normal(0, 1, size=(1, 1024))).tolist() == [-1]

    # (3) 事件組裝：noise 不成事件（二版關鍵語意，與現行 dedup 相反）。
    #     在 10 篇 4/3/3 之外加 1 篇離群 → 該篇應落在 noise 而非自成單篇事件。
    outlier = rng.normal(0, 1, size=(1, 1024))
    ids11 = [f"a{i}" for i in range(11)]
    events, noise = cluster_into_events_hdbscan(
        ids11, np.vstack([vectors, outlier]), exclude_periodic=False,
    )
    print(f"[事件組裝] events={[len(e) for e in events]}  noise={noise}")
    assert [len(e) for e in events] == [4, 3, 3], f"應為 4/3/3 且依篇數排序：{events}"
    assert noise == ["a10"], f"離群篇應落 noise 而非事件：{noise}"
    flat = [i for e in events for i in e]
    assert "a10" not in flat, "noise 不得出現在事件清單中（二版語意）"

    # (4) 週期性過濾確實生效（〈台幣〉日報為凌駕欄目）
    ev, ns = cluster_into_events_hdbscan(
        [1, 2], rng.normal(0, 1, size=(2, 1024)),
        titles=["〈台幣〉開盤升值", "群創新廠動工"], dates=["2026-01-01"] * 2,
    )
    assert ev == [] and ns == [2], f"週期篇應被濾除、剩 1 篇落 noise：{ev} / {ns}"

    print("自我測試通過")


def _corpus_check() -> None:
    """全語料模式：印出 §4.1 對照數字（需 embedding/out/ 向量快取）。"""
    from backend.clustering.golden_eval import load_corpus

    articles, vectors = load_corpus()
    events, noise = cluster_into_events_hdbscan(
        [a["id"] for a in articles], vectors,
        titles=[a.get("title", "") for a in articles],
        dates=[a.get("date") for a in articles],
    )
    total = sum(len(e) for e in events) + len(noise)
    print(f"\n[§4.1 對照] mcs={HDBSCAN_MIN_CLUSTER_SIZE} min_samples={HDBSCAN_MIN_SAMPLES}")
    print(f"  進聚類篇數：{total}（全語料 {len(articles)} － 週期性 {len(articles) - total}）")
    print(f"  事件數：{len(events)}   noise：{len(noise)}（{len(noise) / total:.0%}）")
    print(f"  最大事件：{max(len(e) for e in events)} 篇")
    print("  REPORT §4.1 歷史值（第 2 輪規則，3463 篇）：事件 396 / noise 1556 / 45%")


if __name__ == "__main__":
    import sys

    _self_test()
    if "--corpus" in sys.argv:
        _corpus_check()
