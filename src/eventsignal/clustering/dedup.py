"""去重聚類：週期性過濾 + Agglomerative(average) + 日期/ticker 雙閘門（SD §2.2 / T23 實測定案）。

本模組只負責「聚類」這一階段：輸入已向量化的文章（id + 標題 + 1024 維向量），
輸出事件分群結果。向量化由 embedding_Timyo/bge_m3.py 負責，這裡不載模型、
不碰 DB，方便單獨測試與重跑（run.py 調度時把兩階段串起來）。

定案方法（依 T23 golden set 實測，REPORT §3；induced F1=0.914）：
  1. 週期性過濾：先把每日/每周固定產出的欄目（〈台幣〉日報、籌碼日報、營收速報、
     盤勢評論…）濾掉，否則這些高度相似的欄目會把真事件洗版。
  2. Agglomerative 聚類（average linkage，cosine 距離 < 0.35）：合併需「與整群
     平均相似」，治連通分量的傳遞鏈接（A像B、B像C ≠ A像C）。
  3. 日期閘門：群內相鄰成員日期差 > 3 天即拆——治逐月業績/兩季法說跨期誤合。
  4. ticker 主題閘門：事件主題＝成員代號「交集」，交集為空不合併（全員無代號者
     自由通行）——治金控月報這類同題材不同公司誤合。
  5. 日期閘門補跑：步驟 4 會重組成員，而步驟 3 的「相鄰差 <= gap」性質不被子集
     繼承，故需再過一次（REPORT §4.3；全語料修正 8 群跨期誤合）。

  ⚠️ 歷史包袱：初版「Cosine >0.65 + 連通分量」全量巨群崩潰（golden F1=0.043），
     已自本檔刪除——union-find 連通分量的等價實作見
     embedding_Timyo/dedup_experiment.py:185 `cluster_by_threshold`（相似度矩陣
     那半邊簽章不同：該檔是 build_similarity_matrix(model, articles)，本檔刪掉的
     是 build_similarity_matrix(vectors)）；要一字不差的原版請取 commit 043010a。
     二版 HDBSCAN 誤合逐月家族（golden F1=0.224），移至 legacy_clusterers.py，
     該檔重建了 REPORT §4.1 的完整二版管線可重跑。本檔只保留單一現行路徑。

用法：
    from eventsignal.clustering.dedup import cluster_into_events
    events = cluster_into_events(ids, vectors, titles=titles, dates=dates)
    # events = [[3, 7, 12], [5], ...]  每個子列表 = 一個事件的文章 id
"""

import hashlib
import re
from collections import defaultdict
from datetime import date as _date
from datetime import datetime as _datetime
from typing import Any, NamedTuple

import numpy as np

# ─────────────────────────────────────────────────────────────
# 定案參數
# ─────────────────────────────────────────────────────────────
AGGLO_DISTANCE_THRESHOLD = 0.35  # average-linkage cosine 距離閾值（=相似度 0.65；REPORT §1.2：平原 0.34–0.36 皆穩）
DATE_GATE_DAYS = 3               # 日期閘門：群內相鄰成員日期差 > 3 天即拆（REPORT §1.2：gap 3–5 皆穩）
DATE_SPAN_CAP_DAYS = 7           # 總跨度上限：相鄰差擋不住「天天出刊」的連續欄目（相鄰永遠 1 天，
                                 # 鏈式延伸永不觸發），首尾差超過即另起新段（2026-08-02 實測補上）

HAS_UNIQUE_DETAIL_MIN = 3        # 那篇「獨家數字」token 至少要幾個才標 has_unique_detail

# 資料驅動週期偵測門檻
MIN_DATES = 4   # 同模板需跨幾個不同日期才算週期
MIN_COUNT = 4   # 同模板需出現幾次

DATA_DRIVEN_TYPE = "資料驅動模板"   # 非關鍵字命中、而是靠跨日重複樣態判出的週期性

# ─────────────────────────────────────────────────────────────
# 週期性過濾規則（原定案於 experiments_everyone/timyo/periodic_classify.py，
#  規則移入本檔後該腳本已刪除；四輪演進見 REPORT §4.2）
# ─────────────────────────────────────────────────────────────

# 0) 最優先定期欄目（凌駕事件白名單）：欄目本身每日/每周固定產出，
#    即使標題順帶提到某公司法說、財報，整篇仍是欄目而非單一事件。
_PERIODIC_OVERRIDE = [
    # MoneyDJ 書名號行情欄（2026-08-02 全語料實證：每欄「一天一篇」——《美股》120 篇
    # 跨 119 日、《油價》120 篇跨 117 日…）。這批欄目天天出刊，日期閘門只驗相鄰差
    # 而失效（相鄰永遠 1 天），實測《油價》滾成 79 篇跨 46 天的巨型事件。
    # 白名單制、不用裸 ^《.+》：MoneyDJ 也用《產業名》前綴標一般個股新聞，
    # 且《DJ在線》是深度專欄常載真事件，均不可誤殺。
    ("海外/商品行情欄", re.compile(
        r"^《(歐股|美股|美債|油價|農產品|台股盤後|日股|日股早盤|貴金屬|金屬"
        r"|貴金屬/金屬|日韓股|韓股|港股|台股|匯市|債市|強勢美股特報|美股主題週報)》")),
    ("台股盤勢", re.compile(r"〈台股風向球〉")),
    ("每周回顧", re.compile(r"本周大事回顧|本週大事回顧")),
    # 以下三欄自 _PERIODIC_RULES 升級（2026-07 全語料實證：25 篇靠白名單逃過過濾，
    # 其中這三欄 11 篇是純欄目誤放行——〈台幣〉日報聚成假事件、盤勢評論灌進最大事件）。
    # 籌碼日報/熱門股不可升級：〈央行理監事會〉、〈熱門股〉佳世達入股 等真事件靠白名單救回。
    ("匯市日報", re.compile(r"〈台幣〉")),
    ("選股/薦股", re.compile(r"【量大強漲股整理】")),   # 僅格式標記欄；一般選股關鍵字仍讓白名單優先
    # 盤勢評論：每日大盤展望＋選股點名評論欄。升級代價（已量化）：偶有夾帶真事件資訊的
    # 專欄文被濾（語料 6 篇中 1 篇，該事件另有 7 篇成員，事件仍在）。
    ("盤勢評論", re.compile(
        r"資金輪動|籌碼換手|資金卡位|資金集中|主流股|拉積盤|接盤俠"
        r"|該追還是該收|該收手還是換手|後市怎麼看|存活指南|耐震名單|名單曝光"
        r"|(台股|大盤)[^，。！？]{0,20}(爆量|天量|巨量|量縮|洗盤|換手|創高|新高|震盪|千點|漲點"
        r"|翻黑|翻多|重挫|噴漲|大反攻|失守|守住|背離|乖離|補跌|急殺|熔斷|上影線|連[0-9一二三四五六七]紅)"
    )),
]

# 1) 事件白名單（優先於一般欄目關鍵字；這些即使有括號也是真事件）
_EVENT_WHITELIST = re.compile(
    r"法說|財報|法人說明會|理監事會|併購|收購|入股|處分|重訊|停牌|下市"
    r"|上市掛牌|IPO|除權息|減資|增資|私募|違約交割|股東會"
)

# 2) 明確週期性欄目關鍵字 → 類型
_PERIODIC_RULES = [
    ("營收速報", re.compile(r"營收速報|營收快報")),
    ("熱門股/焦點股", re.compile(r"〈熱門股〉|〈焦點股〉")),
    # 早盤/尾盤/盤前 不可用裸詞：這三個字只標示「時段」，個股在該時段的即時行情
    # （群創早盤亮燈漲停、鴻海尾盤爆量急拉）是真事件，裸詞會整批誤丟成每日欄目。
    # 需與大盤語境鄰接才算盤勢欄，雙向皆收（「台股早盤…」「早盤台股…」）。
    # 錨點含指數量級詞（千點/萬點/指數）：盤勢欄常省略「台股」二字直接寫
    # 「早盤大漲近千點」，而個股行情不以千點計，故這些詞同樣指向大盤。
    ("台股盤勢", re.compile(
        r"〈台股(盤後|盤中|開盤|收盤|風向球)〉|台股(盤後|盤中|開盤|收盤)"
        r"|(台股|大盤|加權|千點|萬點|指數)[^，。！？]{0,12}(早盤|尾盤|盤前)"
        r"|(早盤|尾盤|盤前)[^，。！？]{0,12}(台股|大盤|加權|千點|萬點|指數)"
    )),
    ("選股/薦股", re.compile(r"選股策略|強漲股|飆股|布局策略|操作策略|存股")),
    ("航運指數", re.compile(r"〈航運指數〉|SCFI|BDI|運價指數")),
    # 籌碼日報：每日三大法人買賣超行情回顧，動詞與金額間常夾字，用寬鬆鄰接。
    ("籌碼日報", re.compile(
        r"三大法人|外資買賣超|投信買賣超|融資融券"
        r"|(外資|投信|自營商)[^，。！？]{0,12}(買超|賣超|調節|甩賣|回補|提款|敲進|倒貨|加碼|大買|大砍|小買)"
        r"|(買超|賣超)[^，。！？]{0,8}(億元|萬張)"
    )),
    ("大盤指數", re.compile(r"〈美股〉|道瓊|那斯達克|標普|費半|收黑|收紅|四大指數")),
]


def _to_date_str(value) -> str | None:
    """任意日期表示 → 'YYYY-MM-DD'；無值回 None。

    邊界正規化：離線 JSON 給字串，DB 給 datetime.date/datetime——後者不可切片，
    舊版直接 `value[:10]` 會在 DB 接上時炸 TypeError。統一在入口轉一次，
    下游（週期偵測、日期閘門、date_start/end）就只需面對字串。
    """
    if value is None or value == "":
        return None
    if isinstance(value, _datetime):      # datetime 是 date 的子類，須先判
        return value.date().isoformat()
    if isinstance(value, _date):
        return value.isoformat()
    return str(value)[:10]


def _normalize_template(title: str) -> str:
    """把標題去股號/數字/年月日 → 模板字串，用於資料驅動的週期偵測。"""
    t = re.sub(r"\([0-9]{3,6}[-A-Za-z]*\)", "", title)  # 股號
    t = re.sub(r"[0-9]+[\.,]?[0-9]*", "#", t)             # 數字
    t = re.sub(r"[年月日]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def periodic_type(title: str) -> str | None:
    """單篇標題的週期性類型；非週期性回傳 None（僅關鍵字規則，不含資料驅動）。"""
    for name, rx in _PERIODIC_OVERRIDE:
        if rx.search(title):
            return name
    if _EVENT_WHITELIST.search(title):
        return None
    for name, rx in _PERIODIC_RULES:
        if rx.search(title):
            return name
    return None


def periodic_types(titles: list[str], dates: list[str] | None = None) -> list[str | None]:
    """每篇的週期性類型；非週期性回傳 None（週期性判定的單一事實來源）。

    優先序：凌駕欄目 > 事件白名單 > 明確欄目關鍵字 > 資料驅動模板。
    前三層由 periodic_type() 判（純關鍵字），第四層需全批資料才能算，故留在這裡。
    dates 用於資料驅動偵測（同模板跨 >=MIN_DATES 日且 >=MIN_COUNT 次 → 週期）；
    未提供 dates 時，僅套用關鍵字規則。

    回傳類型而非布林，是為了讓呼叫端能把「為什麼被濾掉」寫進 articles.status 的
    交接資訊（見 assemble_events 的 ClusterResult.filtered）。
    """
    n = len(titles)
    dates = dates or [""] * n

    # 資料驅動：模板 → 出現的不同日期集合
    tmpl_dates: dict[str, set] = defaultdict(set)
    tmpl_count: dict[str, int] = defaultdict(int)
    templates = [_normalize_template(t) for t in titles]
    for tm, d in zip(templates, dates):
        tmpl_dates[tm].add(_to_date_str(d) or "")
        tmpl_count[tm] += 1
    periodic_tmpl = {
        tm for tm in tmpl_dates
        if len(tmpl_dates[tm]) >= MIN_DATES and tmpl_count[tm] >= MIN_COUNT
    }

    result: list[str | None] = []
    for title, tm in zip(titles, templates):
        kw = periodic_type(title)           # 凌駕欄目 / 白名單 / 欄目關鍵字
        if kw is not None:
            result.append(kw)
        elif _EVENT_WHITELIST.search(title):
            result.append(None)             # 白名單同時擋掉資料驅動層
        elif tm in periodic_tmpl:
            result.append(DATA_DRIVEN_TYPE)
        else:
            result.append(None)
    return result


def classify_periodic(titles: list[str], dates: list[str] | None = None) -> list[bool]:
    """判定每篇是否為週期性欄目（True＝週期性，應在聚類前濾掉）。

    薄包裝：語意等同 periodic_types() 的「有沒有類型」，供只關心去留的呼叫端使用。
    """
    return [t is not None for t in periodic_types(titles, dates)]


# ─────────────────────────────────────────────────────────────
# 定案聚類：Agglomerative(average) + 日期閘門 + ticker 主題閘門
# ─────────────────────────────────────────────────────────────
def normalize_vectors(vectors: np.ndarray) -> np.ndarray:
    """L2 正規化（公開：legacy_clusterers.py 共用，正規化語意須與定案路徑一致）。"""
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0          # 零向量防呆
    return vectors / norms


def cluster_agglomerative(
    vectors: np.ndarray,
    distance_threshold: float = AGGLO_DISTANCE_THRESHOLD,
) -> np.ndarray:
    """Average-linkage 聚類。回傳每列群 label（0..k-1，單篇自成一群）。

    合併需「與整群平均距離 < 閾值」——連通分量(single linkage)只要跟任一
    成員像就整串鏈進來，全量資料會滾成巨群；average linkage 治此病，
    且距離閾值 0.35 = 相似度 0.65，保留初版閾值的可解釋性。
    """
    from sklearn.cluster import AgglomerativeClustering

    if len(vectors) < 2:
        return np.zeros(len(vectors), dtype=int)
    return AgglomerativeClustering(
        n_clusters=None, distance_threshold=distance_threshold,
        metric="cosine", linkage="average",
    ).fit_predict(normalize_vectors(vectors))


def _date_ordinal(s: str | None) -> int | None:
    """'YYYY-MM-DD...' → 序數日；無法解析回 None（閘門對 None 不設限）。"""
    try:
        return _date(int(s[:4]), int(s[5:7]), int(s[8:10])).toordinal()
    except Exception:
        return None


def _split_by_date_gap(group: list[int], ords: list, gap: int,
                       span_cap: int | None = DATE_SPAN_CAP_DAYS) -> list[list[int]]:
    """群內依日期切段：相鄰成員（依日期排序）差 > gap 天、或段內首尾差 > span_cap
    天即拆成不同事件。

    相鄰差治「內容像、時間遠」的跨期誤合（逐月營收、兩季法說、多次加碼）；
    總跨度上限治「天天出刊」的連續欄目——相鄰永遠 1 天、鏈式延伸永不觸發
    相鄰差（實測《油價》滾成 79 篇跨 46 天）。
    無日期成員排最後、跟前段，不因缺值被單獨拆出。
    """
    mem = sorted(group, key=lambda k: (ords[k] is None, ords[k]))
    out, cur = [], [mem[0]]
    seg_start = ords[mem[0]]
    for prev, nxt in zip(mem, mem[1:]):
        split = (ords[prev] is not None and ords[nxt] is not None
                 and ords[nxt] - ords[prev] > gap)
        if (not split and span_cap is not None
                and seg_start is not None and ords[nxt] is not None
                and ords[nxt] - seg_start > span_cap):
            split = True
        if split:
            out.append(cur)
            cur = []
            seg_start = ords[nxt]
        cur.append(nxt)
    out.append(cur)
    return out


def _ticker_subject(cluster: list[int], tickers: list[set]) -> set | None:
    """事件主題代號＝「有代號成員」的交集（共同主角）；全員無代號 → None（自由）。

    用交集而非聯集：綜述文（「13家金控合賺千億」提到一堆代號）併入某家後，
    主題收斂成該家代號，不會再把其他家黏進來。
    """
    ts = [tickers[k] for k in cluster if tickers[k]]
    if not ts:
        return None
    subject = set(ts[0])
    for t in ts[1:]:
        subject &= t
    return subject


def _split_by_ticker_subject(
    group: list[int], tickers: list[set], norm_vectors: np.ndarray, min_sim: float,
) -> list[list[int]]:
    """群內約束式 average-linkage 再聚類：主題交集為空的兩群不合併。

    治「同題材不同公司」誤合（金控同月獲利、同日多家法說）——標題語意近、
    日期同月，向量與日期閘門都擋不住，只有代號能分。全員無代號的群
    （總經/大盤新聞）自由通行，交給向量與日期決定。
    """
    if len(group) <= 1:
        return [group]
    sub = norm_vectors[group]
    sim = sub @ sub.T
    pos = {k: x for x, k in enumerate(group)}
    clusters = [[k] for k in group]
    while True:
        best, best_sim = None, min_sim
        for a in range(len(clusters)):
            sa = _ticker_subject(clusters[a], tickers)
            for b in range(a + 1, len(clusters)):
                sb = _ticker_subject(clusters[b], tickers)
                if sa is not None and sb is not None and not (sa & sb):
                    continue
                s = float(np.mean([[sim[pos[x], pos[y]] for y in clusters[b]]
                                   for x in clusters[a]]))
                if s >= best_sim:
                    best, best_sim = (a, b), s
        if best is None:
            break
        a, b = best
        clusters[a] += clusters[b]
        del clusters[b]
    return clusters


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────
class FilteredArticle(NamedTuple):
    """被週期性過濾丟掉的一篇，附上原因。

    存在理由：這些文章不會出現在任何事件裡，若沒有回傳側通道，DB 落地後它們的
    status 會永遠停在 'vectorized'，每輪重跑都被重新撈出、重新向量化、重新丟掉。
    呼叫端拿到後應把 status 推進到 'periodic'（periodic_type 存為佐證）。
    """
    article_id: Any
    periodic_type: str


def _cluster_core(
    article_ids: list,
    vectors: np.ndarray,
    titles: list[str] | None,
    dates: list | None,
    tickers: list[set] | None,
    exclude_periodic: bool,
    distance_threshold: float,
    date_gate_days: int | None,
) -> tuple[list[list], list[FilteredArticle]]:
    """聚類主體：回傳（事件的 id 群, 被週期過濾的文章）。

    cluster_into_events 與 assemble_events 共用此核心——前者只取事件、後者兩者都要。
    共用而非各寫一份，是為了讓「哪些被濾掉」與「哪些進了事件」永遠互補，不會漂移。
    """
    if len(article_ids) != len(vectors):
        raise ValueError(f"id 數量（{len(article_ids)}）與向量列數（{len(vectors)}）不一致")
    if tickers is not None and len(tickers) != len(article_ids):
        raise ValueError(f"tickers 數量（{len(tickers)}）與文章數（{len(article_ids)}）不一致")
    if len(article_ids) == 0:
        return [], []

    ids = list(article_ids)
    vecs = np.asarray(vectors, dtype=np.float32)
    dts = [_to_date_str(d) for d in (dates if dates is not None else [None] * len(ids))]
    tks = list(tickers) if tickers is not None else None
    filtered: list[FilteredArticle] = []

    # 1. 週期性過濾
    if exclude_periodic and titles is not None:
        ptypes = periodic_types(titles, dts)
        keep = [i for i, p in enumerate(ptypes) if p is None]
        filtered = [FilteredArticle(ids[i], ptypes[i])
                    for i in range(len(ids)) if ptypes[i] is not None]
        ids = [ids[i] for i in keep]
        vecs = vecs[keep]
        dts = [dts[i] for i in keep]
        if tks is not None:
            tks = [tks[i] for i in keep]
        if len(ids) == 0:
            return [], filtered

    # 2. Agglomerative（average linkage）→ 位置索引收群
    labels = cluster_agglomerative(vecs, distance_threshold)
    by_label: dict[int, list[int]] = {}
    for pos, label in enumerate(labels):
        by_label.setdefault(int(label), []).append(pos)
    parts = list(by_label.values())

    # 3. 日期閘門：拆「內容像、時間遠」的跨期誤合
    ords = [_date_ordinal(d) for d in dts]
    if date_gate_days is not None:
        parts = [seg for g in parts for seg in _split_by_date_gap(g, ords, date_gate_days)]

    # 4. ticker 主題閘門：拆「同題材不同公司」誤合
    if tks is not None:
        norm = normalize_vectors(vecs)
        parts = [seg for g in parts
                 for seg in _split_by_ticker_subject(g, tks, norm, 1.0 - distance_threshold)]

        # 5. 日期閘門補跑：步驟 3 只保證「切段後段內相鄰差 <= gap」，此性質不被子集繼承
        #    （段 01-01/01-04/01-07 相鄰差皆 3，抽掉中間那篇後首尾相隔 6）。
        #    ticker 閘門會重組成員，故需再過一次；對已合規的群為 no-op。
        if date_gate_days is not None:
            parts = [seg for g in parts for seg in _split_by_date_gap(g, ords, date_gate_days)]

    return [[ids[p] for p in g] for g in parts], filtered


def cluster_into_events(
    article_ids: list[int],
    vectors: np.ndarray,
    titles: list[str] | None = None,
    dates: list | None = None,
    *,
    tickers: list[set] | None = None,
    exclude_periodic: bool = True,
    distance_threshold: float = AGGLO_DISTANCE_THRESHOLD,
    date_gate_days: int | None = DATE_GATE_DAYS,
) -> list[list[int]]:
    """把已向量化的文章收斂成事件（Agglomerative + 日期/ticker 雙閘門）。

    Args:
        article_ids: 文章 id，順序需與 vectors 逐列對應。
        vectors: 形狀 (N, 1024) 的向量陣列（BGE-M3 產出，已正規化亦可）。
        titles: 各篇標題；提供才能做週期性過濾。
        dates: 各篇日期（字串或 date/datetime，入口統一正規化），供週期偵測與日期閘門。
        tickers: 各篇代號集合（與 ids 逐列對應）；None 時跳過 ticker 閘門。
        exclude_periodic: 是否先濾掉週期性欄目（需 titles）。定案為 True。
        distance_threshold: average-linkage cosine 距離閾值（定案 0.35）。
        date_gate_days: 日期閘門天數（定案 3）；None 時跳過日期閘門。

    Returns:
        事件清單，每個事件是一組文章 id。週期性欄目不納入任何事件——需要知道
        「哪些被濾掉、為什麼」請改用 assemble_events（回傳 ClusterResult.filtered）。
    """
    return _cluster_core(
        article_ids, vectors, titles, dates, tickers,
        exclude_periodic, distance_threshold, date_gate_days,
    )[0]


# ─────────────────────────────────────────────────────────────
# 事件物件組裝：把 id 群升級成可寫進 events 表的事件物件
#   對應 events 表去重聚類段：event_id / representative_title /
#   source_count / members[{source,title,url,has_unique_detail}] / related_tickers
# ─────────────────────────────────────────────────────────────

# 台股代號：內文常見「群創 (3481-TW)」；標題偶見「(2330)」
_TICKER_RE = re.compile(r"\(([0-9]{3,6})(-[A-Za-z]{1,4})?\)")
# 鉅亨編輯慣用語「今 (2026) 年／去 (114) 年」：括號內的西元/民國年會被上面誤抓成代號
_YEAR_IDIOM_RE = re.compile(r"[今去前明後隔]\s*\([0-9]{3,4}\)\s*年")
# 與年份撞號的代號區間——2027 大成鋼、2025 千興、2002 中鋼都是真代號，不能用數值排除
_YEARISH_RE = re.compile(r"(19|20)[0-9]{2}")

# 「獨家數字」偵測：帶金融單位的數字（金額/張數/百分比/點數…），避開純年份
_DETAIL_RE = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:%|％|億元|億|萬張|萬|張|點|倍|美元|元)")


def _extract_tickers(text: str) -> list[str]:
    """從文字（標題/內文）抓台股代號，去重保序。

    括號年份與真代號撞號，兩層防禦（依 4486 篇真資料樣態定）：
      1. 先剝掉「今 (2026) 年」慣用語（含變體前綴 今/去/前/明/後/隔 與民國年）。
      2. 殘餘變體如「去 (2025)4 月」沒有「年」字可認：裸年份形碼（無 -TW 後綴）
         需同文另有帶後綴的同碼佐證（鉅亨慣例首次提及帶後綴）才算代號。
    代價：全文只以裸碼出現的年份形真代號會漏（語料 4486 篇僅 1 例，
    且落在週期性欄目標題，本就會被過濾）。
    """
    hits = _TICKER_RE.findall(_YEAR_IDIOM_RE.sub(" ", text or ""))
    suffixed = {code for code, suf in hits if suf}
    seen, out = set(), []
    for code, suf in hits:
        if not suf and _YEARISH_RE.fullmatch(code) and code not in suffixed:
            continue
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _extract_details(text: str) -> set[str]:
    """抓帶金融單位的數字 token，正規化空白後回傳集合。"""
    return {re.sub(r"\s+", "", tok) for tok in _DETAIL_RE.findall(text or "")}


def _representative_id(group: list, id_to_row: dict, norm_vectors: np.ndarray):
    """群內離群心最近的一篇當代表（單篇群直接回傳該篇）。"""
    if len(group) == 1:
        return group[0]
    rows = [id_to_row[i] for i in group]
    sub = norm_vectors[rows]
    centroid = sub.mean(axis=0)
    centroid /= (np.linalg.norm(centroid) + 1e-9)
    return group[int(np.argmax(sub @ centroid))]


class ClusterResult(NamedTuple):
    """assemble_events 的完整產出：進了事件的、與被週期過濾丟掉的。

    兩者互補且不重疊，涵蓋輸入的每一篇——呼叫端據此推進 articles.status，
    不必再用「總篇數減事件成員數」反推被濾數量（那只給得出數字，給不出 id）。
    """
    events: list[dict]
    filtered: list[FilteredArticle]


def assemble_events(
    articles: list[dict],
    vectors: np.ndarray,
    *,
    exclude_periodic: bool = True,
    distance_threshold: float = AGGLO_DISTANCE_THRESHOLD,
    date_gate_days: int | None = DATE_GATE_DAYS,
    default_source: str = "鉅亨網",
) -> ClusterResult:
    """聚類 + 組裝成事件物件（可直接對應 events 表去重聚類段）。

    Args:
        articles: 每篇一個 dict，需含 "id"、"title"；可含 "url"、"content"、
                  "date"、"source"、"related_tickers"。順序與 vectors 逐列對應。
                  related_tickers 省略或 None → 以內文 regex 補抽；給空 list 則
                  尊重上游判定「本篇無相關個股」，不補抽。
        vectors: 形狀 (N, 1024) 向量陣列。
        其餘同 cluster_into_events。

    Returns:
        ClusterResult(events, filtered)：
          events — 事件物件清單（依 source_count 由多到少排序），每筆：
            event_id / representative_title / source_count /
            members[{source,title,url,has_unique_detail}] / related_tickers /
            date_start / date_end
          filtered — 被週期性過濾的 FilteredArticle(article_id, periodic_type)
    """
    # id 是全程組裝的鍵：None 或重複會讓 by_id/id_to_row 靜默覆蓋、文章錯置消失，fail fast
    seen_ids: set = set()
    for a in articles:
        aid = a.get("id")
        if aid is None:
            raise ValueError("文章缺少 id（None）：上游（如缺 newsId 的紀錄）需先補唯一 id 再聚類")
        if aid in seen_ids:
            raise ValueError(f"文章 id 重複：{aid!r}，id 需唯一否則事件成員會互相覆蓋")
        seen_ids.add(aid)

    by_id = {a["id"]: a for a in articles}
    id_to_row = {a["id"]: i for i, a in enumerate(articles)}
    norm_vectors = normalize_vectors(vectors)

    ids = [a["id"] for a in articles]
    titles = [a.get("title", "") for a in articles]
    dates = [_to_date_str(a.get("date")) for a in articles]
    # 每篇代號先抽一次：聚類的 ticker 閘門與事件 related_tickers 共用。
    # 用 `is None` 而非 falsy：空 list 是上游明確標記「無相關個股」的結論，
    # 用 `or` 會讓它被內文 regex 復活，把上游的判斷靜默覆寫掉。
    tickers_by_id = {}
    for a in articles:
        declared = a.get("related_tickers")
        tickers_by_id[a["id"]] = (
            list(declared) if declared is not None
            else _extract_tickers(a.get("title", "") + " " + (a.get("content") or ""))
        )

    groups, filtered = _cluster_core(
        ids, vectors, titles, dates,
        [set(tickers_by_id[i]) for i in ids],
        exclude_periodic, distance_threshold, date_gate_days,
    )

    events = []
    for group in groups:
        # has_unique_detail：每事件只標「獨家數字最多的那一篇」（對齊 SD/README 的單數語意）。
        # 單篇事件不標（單一來源無可比）；獨家數字太少（< 門檻）也不標，避免濫標。
        details = {aid: _extract_details((by_id[aid].get("content") or "") + " " + by_id[aid].get("title", ""))
                   for aid in group}
        scoop_id = None
        if len(group) > 1:
            uniq_count = {
                aid: len(details[aid] - set().union(*[details[o] for o in group if o != aid]))
                for aid in group
            }
            best = max(group, key=lambda aid: uniq_count[aid])   # 同分取輸入順序最前
            if uniq_count[best] >= HAS_UNIQUE_DETAIL_MIN:
                scoop_id = best
        rep_id = _representative_id(group, id_to_row, norm_vectors)

        members, tickers, member_dates = [], [], []
        for aid in group:
            a = by_id[aid]
            for t in tickers_by_id[aid]:
                if t not in tickers:
                    tickers.append(t)
            member_date = _to_date_str(a.get("date"))
            if member_date:
                member_dates.append(member_date)

            members.append({
                "article_id": aid,
                "source": a.get("source") or default_source,
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "has_unique_detail": aid == scoop_id,
            })

        member_dates.sort()
        date_start = member_dates[0] if member_dates else None
        date_end = member_dates[-1] if member_dates else None
        primary = tickers[0] if tickers else "NA"
        ymd = (date_start or "").replace("-", "") or "00000000"

        events.append({
            "event_id_stub": f"evt_{ymd}_{primary}",   # 序號於排序後補上
            "representative_title": by_id[rep_id].get("title", ""),
            # ⚠️ 是「不同來源數」，不是文章篇數。
            # 這欄位曾經有兩種定義：這裡寫 len(group)（篇數），run.py 的增量靠攏
            # 卻寫 count(distinct source)（來源數）。同一欄位兩種語意，而
            # scoring_Bright/importance.py 把它當「幾家媒體報導」在算權威度，
            # 於是同一家媒體發三篇就能把星等灌高。統一為來源數。
            "source_count": len({m["source"] for m in members}),
            "members": members,
            "related_tickers": tickers,
            "date_start": date_start,
            "date_end": date_end,
        })

    # 依報導篇數排序，並在 (日期, 代號) 後補成員集合的雜湊 → 完整 event_id
    #
    # ⚠️ 這裡曾經是「每批從 01 重數的流水號」，那會跨批次撞號：
    # 第二批只要有同一天同一代號的事件，就會產生同樣的 evt_YYYYMMDD_2330_01，
    # 再被 run.py 的 ON CONFLICT DO UPDATE 覆寫掉前一批的 members／embedding。
    # 實測 production 已有 3,908 個事件的 members 與實際掛載文章脫節，
    # 其中 2,813 個的 LLM 摘要是根據一份後來被換掉的名單寫出來的。
    #
    # 改用成員 article_id 集合的雜湊：不同故事必然不同 id（不再互相覆寫），
    # 完全相同的一批重跑則得到相同 id（維持冪等，ON CONFLICT 仍是安全的）。
    # 跨批次的「同故事跟進報導」由 run.py 的增量靠攏處理，不靠 id 相同。
    events.sort(key=lambda e: e["source_count"], reverse=True)
    for e in events:
        stub = e.pop("event_id_stub")
        member_ids = sorted(str(m["article_id"]) for m in e["members"])
        digest = hashlib.sha1("".join(member_ids).encode("utf-8")).hexdigest()[:8]
        e["event_id"] = f"{stub}_{digest}"
    # event_id 移到最前面（純美觀）
    return ClusterResult([{"event_id": e.pop("event_id"), **e} for e in events], filtered)


# ─────────────────────────────────────────────────────────────
# 自我測試：不需下載模型
#   (1) 週期性分類規則（純字串）
#   (2) Agglomerative 對手工向量的聚類
#   (3) 事件物件組裝（has_unique_detail / event_id / members）
# 端到端（含 BGE-M3）驗證請跑 embedding_Timyo/dedup_experiment.py。
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # (1) 週期性分類
    sample_titles = [
        "〈台幣〉2026開紅盤股匯齊揚收31.419元 周線連二升",   # 週期：匯市日報
        "外資買超120億元迎開門紅 掃貨力積電10萬張",           # 週期：籌碼日報
        "台積電法說會：Q2毛利率展望優於市場預期",              # 事件（白名單）
        "群創前進CES亮相次世代顯示技術",                       # 事件
        "〈台幣〉台積電法說前夕股匯雙漲 中止連5貶收31.63元",   # 週期：匯市日報凌駕「法說」白名單
        "台股連7紅！台積電財報護體，震盪就是千載難逢的買點！",  # 週期：盤勢評論凌駕「財報」白名單
        "【量大強漲股整理】台積電法說會繳出亮眼成績",           # 週期：選股欄凌駕「法說」白名單
        "〈熱門股〉佳世達入股東科衝刺高階影音市場",             # 事件：「入股」白名單救回熱門股欄
    ]
    flags = classify_periodic(sample_titles, dates=["2026-01-01"] * 8)
    print("[週期性分類]", flags)
    assert flags == [True, True, False, False, True, True, True, False], f"週期性分類不符：{flags}"

    # (2) HDBSCAN 聚類：三組相近向量（4/3/3 篇）
    rng = np.random.default_rng(42)

    def _noisy(base: np.ndarray, k: int) -> np.ndarray:
        return base + rng.normal(0, 0.01, size=(k, base.shape[0]))

    base_a, base_b, base_c = rng.normal(0, 1, size=(3, 1024))
    vectors = np.vstack([_noisy(base_a, 4), _noisy(base_b, 3), _noisy(base_c, 3)])
    ids = [101, 102, 103, 104, 201, 202, 203, 301, 302, 303]

    events = cluster_into_events(ids, vectors)   # 無 titles → 純聚類
    print(f"聚出 {len(events)} 個事件（預期 3 個）：")
    for i, event in enumerate(events):
        print(f"  事件 #{i}：{sorted(event)}")

    expected = [[101, 102, 103, 104], [201, 202, 203], [301, 302, 303]]
    assert sorted(map(sorted, events)) == expected, f"聚類結果不符預期：{events}"

    # (3) 事件物件組裝：兩事件（台積電法說 3 篇 / 鴻海印度廠 3 篇）。
    #     收緊後：每事件只標「獨家數字最多且達門檻(>=3)」的那一篇。
    #     503 有 3 個群內獨有數字（1200元/58%/12萬張）→ 應被標；501/502 不足門檻 → 不標。
    articles = [
        {"id": 501, "source": "經濟日報", "title": "台積電法說會毛利率優預期", "url": "http://a/1",
         "content": "台積電 (2330-TW) 法人說明會展望正向", "date": "2026-02-01"},
        {"id": 502, "source": "鉅亨網", "title": "台積電法說釋利多", "url": "http://a/2",
         "content": "台積電毛利率53%", "date": "2026-02-01"},   # 僅 1 個獨家數字，低於門檻
        {"id": 503, "source": "Reuters", "title": "TSMC Q2 gross margin beats estimates", "url": "http://a/3",
         "content": "外資上調台積電目標價至1200元，Q2毛利率58%，出貨增12萬張", "date": "2026-02-01"},
        {"id": 601, "source": "中央社", "title": "鴻海印度新廠動工", "url": "http://b/1",
         "content": "鴻海 (2317-TW) 印度廠破土", "date": "2026-03-05"},
        {"id": 602, "source": "MoneyDJ", "title": "鴻海印度廠強化蘋果供應鏈", "url": "http://b/2",
         "content": "鴻海擴大 iPhone 組裝", "date": "2026-03-05"},
        {"id": 603, "source": "Reuters", "title": "Foxconn breaks ground on India plant", "url": "http://b/3",
         "content": "Foxconn India assembly", "date": "2026-03-05"},
    ]
    base_x, base_y = rng.normal(0, 1, size=(2, 1024))
    vecs = np.vstack([base_x + rng.normal(0, 0.01, size=(3, 1024)),
                      base_y + rng.normal(0, 0.01, size=(3, 1024))])
    evs, evs_filtered = assemble_events(articles, vecs, exclude_periodic=False)
    assert len(evs) == 2, f"應聚成 2 事件：{[e['source_count'] for e in evs]}"
    assert evs_filtered == [], f"關閉週期過濾時不應有被濾文章：{evs_filtered}"
    tsmc = next(e for e in evs if any(m["article_id"] == 503 for m in e["members"]))
    print(f"[事件物件] event_id={tsmc['event_id']}  來源數={tsmc['source_count']}  tickers={tsmc['related_tickers']}")
    # event_id 尾碼是成員集合的雜湊（不是流水號）——只驗前綴與格式，不寫死雜湊值
    assert re.fullmatch(r"evt_20260201_2330_[0-9a-f]{8}", tsmc["event_id"]),         f"event_id 格式不符：{tsmc['event_id']}"
    # source_count = 不同來源數。這三篇來自經濟日報／鉅亨網／Reuters → 3
    assert tsmc["source_count"] == 3 and tsmc["related_tickers"] == ["2330"]
    # 鴻海那組：中央社／MoneyDJ／Reuters 三篇但 Reuters 重複一次 → 仍是 3 家
    hon = next(e for e in evs if any(m["article_id"] == 601 for m in e["members"]))
    assert hon["source_count"] == 3, f"來源數應為 3：{hon['source_count']}"
    assert len(hon["members"]) == 3, "成員仍是 3 篇——source_count 算的是來源不是篇數"
    uniq = [m["article_id"] for m in tsmc["members"] if m["has_unique_detail"]]
    print(f"  has_unique_detail 的文章（收緊後應僅 [503]）：{uniq}")
    assert uniq == [503], f"收緊後應只標 503：{uniq}"

    # (4) 邊界：週期過濾後只剩 1 篇 → 單篇自成事件（原本 HDBSCAN n=1 會 ValueError）
    single = cluster_into_events(
        [9, 10], rng.normal(0, 1, size=(2, 1024)),
        titles=["〈台幣〉開盤升值", "群創新廠動工"], dates=["2026-01-01"] * 2,
    )
    assert single == [[10]], f"過濾後單篇應自成事件：{single}"

    # (5) 邊界：id 為 None / 重複 → fail fast（否則 by_id 靜默覆蓋、文章錯置消失）
    for bad in ([{"id": None, "title": "無id文章"}, {"id": 1, "title": "正常文章"}],
                [{"id": 1, "title": "文章甲"}, {"id": 1, "title": "文章乙"}]):
        try:
            assemble_events(bad, rng.normal(0, 1, size=(2, 1024)), exclude_periodic=False)
            raise AssertionError(f"id 異常應拋 ValueError：{bad}")
        except ValueError:
            pass

    # (6) 括號年份 vs 真代號：慣用語剝除＋裸年份形碼需同文後綴佐證
    txt = ("台積電 (2330-TW) 今 (2026) 年資本支出創高；大成鋼 (2027-TW) 漲停，"
           "尾盤大成鋼 (2027) 爆量；市場自去 (2025) 谷底回升；"
           "高股息 ETF (00929) 買氣旺；櫃買中心去 (114) 年啟動創櫃板。")
    got = _extract_tickers(txt)
    # 2330/2027 帶後綴為真代號（裸 (2027) 有同文佐證不丟）；(2026)年/(114)年 是慣用語；
    # 裸 (2025) 無同文後綴佐證 → 丟；00929 非年份形 → 留
    assert got == ["2330", "2027", "00929"], f"代號抽取不符：{got}"

    # (7) 雙閘門：向量幾乎相同的 4 篇，靠日期/ticker 拆對
    #     a,b 同代號隔 1 天 → 同事件；c 同期不同代號 → ticker 閘門拆；
    #     d 同代號但隔 19 天 → 日期閘門拆
    base_g = rng.normal(0, 1, size=1024)
    gvecs = base_g + rng.normal(0, 0.001, size=(4, 1024))
    gates = cluster_into_events(
        ["a", "b", "c", "d"], gvecs,
        dates=["2026-01-01", "2026-01-02", "2026-01-02", "2026-01-21"],
        tickers=[{"1101"}, {"1101"}, {"2330"}, {"1101"}],
        exclude_periodic=False,
    )
    assert sorted(map(sorted, gates)) == [["a", "b"], ["c"], ["d"]], f"雙閘門不符：{gates}"
    # 無代號成員自由通行（總經文不因缺代號被拆）
    free = cluster_into_events(
        ["a", "b"], gvecs[:2], dates=["2026-01-01", "2026-01-02"],
        tickers=[{"1101"}, set()], exclude_periodic=False,
    )
    assert sorted(map(sorted, free)) == [["a", "b"]], f"自由通行不符：{free}"

    # (8) 閘門順序：ticker 閘門抽掉中間成員後，殘餘子集不得違反日期閘門。
    #     a(01-01,1101) b(01-04,2330) c(01-07,1101)：相鄰差皆 3 天 → 日期閘門放行；
    #     ticker 閘門把 b 拆走後，剩 a,c 相隔 6 天——正是日期閘門要治的跨期誤合，
    #     故 ticker 閘門後需重跑日期閘門，三篇應各自成事件。
    gvecs3 = base_g + rng.normal(0, 0.001, size=(3, 1024))
    reorder = cluster_into_events(
        ["a", "b", "c"], gvecs3,
        dates=["2026-01-01", "2026-01-04", "2026-01-07"],
        tickers=[{"1101"}, {"2330"}, {"1101"}],
        exclude_periodic=False,
    )
    assert sorted(map(sorted, reorder)) == [["a"], ["b"], ["c"]], \
        f"ticker 閘門後殘餘子集違反日期閘門：{reorder}"

    # (9) 盤勢裸詞須綁大盤語境：個股的即時行情是真事件，不是每日欄目。
    #     「早盤/尾盤/盤前」單獨出現時，個股標題（群創早盤亮燈漲停）曾被整批誤丟。
    for t in ["群創早盤亮燈漲停", "鴻海尾盤爆量急拉", "台積電盤前競價走高"]:
        assert periodic_type(t) is None, f"個股即時事件被誤判週期（{t}）：{periodic_type(t)}"
    # 綁到大盤語境的仍須判週期（不指定命中哪條規則——盤勢評論等其他欄目規則
    # 也可能先攔下，契約是「被濾掉」而非「由哪條濾」）
    for t in ["台股早盤開高走低", "早盤台股衝上兩萬點", "盤前重點：台股今日關注焦點"]:
        assert periodic_type(t) is not None, f"大盤盤勢欄應判週期（{t}）"

    # (10) date 型別：DB 取出的是 datetime.date/datetime，不是字串。
    #      舊版 a["date"][:10] 直接 TypeError: not subscriptable。
    import datetime as _dt
    for dval, expect in ((_dt.date(2026, 2, 1), "2026-02-01"),
                         (_dt.datetime(2026, 2, 1, 9, 30), "2026-02-01"),
                         ("2026-02-01T09:30:00", "2026-02-01")):
        evs_d, _ = assemble_events(
            [{"id": 1, "title": "甲", "date": dval}, {"id": 2, "title": "乙", "date": dval}],
            rng.normal(0, 1, size=(2, 1024)), exclude_periodic=False,
        )
        assert all(e["date_start"] == expect for e in evs_d), \
            f"date 型別 {type(dval).__name__} 未正規化：{[e['date_start'] for e in evs_d]}"

    # (11) related_tickers=[] 是上游的「已判定：無相關個股」，不是缺值。
    #      舊版用 `or` 判斷，空 list 被內文 regex 復活成 ['2330']，覆寫上游判斷。
    arts_rt = [{"id": 1, "title": "甲 (2330) 法說", "content": "台積電 (2330-TW)",
                "related_tickers": [], "date": "2026-02-01"},
               {"id": 2, "title": "乙", "content": "x", "related_tickers": [], "date": "2026-02-01"}]
    evs_rt, _ = assemble_events(arts_rt, rng.normal(0, 1, size=(2, 1024)), exclude_periodic=False)
    assert all(e["related_tickers"] == [] for e in evs_rt), \
        f"空 related_tickers 被 regex 復活：{[e['related_tickers'] for e in evs_rt]}"
    # 省略欄位（None）才走 regex 補抽
    arts_none = [dict(a, related_tickers=None) for a in arts_rt]
    evs_none, _ = assemble_events(arts_none, rng.normal(0, 1, size=(2, 1024)), exclude_periodic=False)
    assert any("2330" in e["related_tickers"] for e in evs_none), \
        f"缺值時應以 regex 補抽代號：{[e['related_tickers'] for e in evs_none]}"

    # (12) 被濾週期文章的回傳側通道：呼叫端要能把它們的 status 標成 'periodic'，
    #      否則 DB 落地後這些文章永遠卡在 'vectorized'，每輪重跑都被撈出來。
    arts_f = [{"id": 1, "title": "〈台幣〉開盤升值", "date": "2026-01-01"},
              {"id": 2, "title": "群創新廠動工", "date": "2026-01-01"},
              {"id": 3, "title": "台股早盤開高走低", "date": "2026-01-01"}]
    evs_f, filtered_f = assemble_events(arts_f, rng.normal(0, 1, size=(3, 1024)))
    assert sorted(f.article_id for f in filtered_f) == [1, 3], f"被濾清單不符：{filtered_f}"
    assert {f.periodic_type for f in filtered_f} == {"匯市日報", "台股盤勢"}, \
        f"被濾原因不符：{[(f.article_id, f.periodic_type) for f in filtered_f]}"
    # 每篇文章恰好落在「某事件成員」或「被濾清單」其一，不重不漏
    assigned = [m["article_id"] for e in evs_f for m in e["members"]] + [f.article_id for f in filtered_f]
    assert sorted(assigned) == [1, 2, 3], f"文章去向不完備：{assigned}"

    print("自我測試通過")
