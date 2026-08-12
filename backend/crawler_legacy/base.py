"""爬蟲清洗基底：抓國內財經+官方，標準化成文章物件、標 related_tickers（04 SD §2.1）。

已實作：
  - clean_html：各來源共用的 HTML 清洗
  - load_ticker_index / extract_related_tickers：related_tickers 標註（三層策略，見下）

未實作：文章物件標準化（→ articles 表欄位，暫由 scripts/seed_articles.py 代勞）。

═══ related_tickers 標註策略 ═══

規格 03_SD §2.1 一句「用公司名↔代號對照表標」把這題講得像查表，實測不是：
拿 tickers 表 2709 個公司名 naive 匹配 5409 篇鉅亨新聞 →
9.9% 的新聞命中 >6 家公司、單篇最多命中 58 家。這是 NER 問題，不是查表。

★★ 最重要的一條：**只掃標題，不掃內文** ★★

  `related_tickers` 的語意是「這個事件**關於**哪些公司」，不是「內文**提到**哪些公司」。
  掃內文會把兩者混為一談，實際後果（實測）：
    - `GET /tickers/2330/events` 查台積電 → 回傳「聯發科」「金融業防詐」「國銀擴張海外」
      （那些新聞內文提到台積電）
    - 一則 7 篇報導的頒獎公關新聞被標了 16 家公司

  標題是編輯挑出來的主角，訊號強得多。實測對照（4486 篇）：

  |                | 標題+內文 | **只用標題** |
  |----------------|----------|-------------|
  | 有標到         | 91.2%    | 66.3%       |
  | 恰好 1 家      | 39.0%    | **58.0%**   |
  | >4 家（雜訊）  | 13.3%    | **0.1%**    |
  | 單篇最多       | **58 家** | **5 家**    |

  用 25pp 涵蓋率換掉 99% 雜訊。而「恰好 1 家 58%」正是 03_SD §2.1 說的
  「第一版只處理**明確提到單一公司**」—— 規格的保守作法是對的。

  → 呼叫端請傳**標題**，不要傳 title+content（見 scripts/seed_articles.py）。

═══ 兩層策略（信心遞減）═══

  Layer 1  標題明寫代號「(2330)」 → 直接採用    精確度最高
  Layer 2  標題命中公司名且該名可信 → 採用      需過三道濾網
  Layer 3  其餘                     → []        合法空值，不猜

Layer 2 的三道濾網（每道都是實測結論，不是憑感覺）：
  a. 最長匹配：長名先掃、掃到就挖掉。否則「中鋼構」會同時命中「中鋼」、
     「日月光投控」會同時命中「日月光」。
  b. 停用清單（data/ticker_stoplist.json，由 scripts/build_ticker_stoplist.py 產出）：
     排除「名字剛好是常用詞」者——大陸(2526)、聯合(4129)、卓越(2496)、全台(3038)、
     超級(911606)…判別式＝代號共現率（真公司偶爾寫「群聯(8299)」，常用詞不會）。
  c. 同名歧義：5 個名字對到 2 個代號（神達→2315/3706、永信→1716/3705、
     致伸、研揚、億豐——全是「舊公司 + 控股公司」同名），名稱層無法判斷是哪個，
     一律排除，只靠 Layer 1 的明寫代號。
"""

import html
import json
import re
from collections import defaultdict
from pathlib import Path

# 台股證券代號：4 位數字開頭（2330 / 0050 / 00835B / 2887Z1 特別股）
CODE_IN_TEXT = re.compile(r"[(（\[【]\s*(\d{4}[A-Za-z]?\d?)\s*[)）\]】]")

# crawler_legacy → backend → <repo>
_STOPLIST_PATH = Path(__file__).resolve().parents[2] / "data" / "ticker_stoplist.json"


def clean_html(raw_html: str | None) -> str:
    """把 API 回傳的 HTML 字串清成純文字。

    1) 還原 HTML entity（&amp; / &nbsp; …）　2) 去掉殘留標籤。
    來源：history/anue_scraping.ipynb 原始實作，行為未變更。
    """
    if not raw_html:
        return ""
    clean_text = html.unescape(raw_html)
    clean_text = re.sub(r"<.*?>", "", clean_text)
    return clean_text.strip()


def load_stoplist(path: Path | None = None) -> set[str]:
    """讀停用清單（公司名剛好是常用詞者）。檔案不存在時回空集合，不報錯。"""
    p = path or _STOPLIST_PATH
    if not p.exists():
        return set()
    data = json.loads(p.read_text(encoding="utf-8"))
    return {item["name"] for item in data.get("停用清單", [])}


def load_ticker_index(rows, stoplist=None) -> dict:
    """把 tickers 表的 [(ticker, name)] 建成比對索引。

    回傳 dict：
      names       依長度遞減排序的可用公司名（已濾掉停用詞與同名歧義）
      name2ticker 公司名 → 代號
      codes       全部有效代號（Layer 1 驗證用，不受停用清單影響）
      excluded    {"stoplist": [...], "ambiguous": [...]} 供稽核
    """
    stop = stoplist if stoplist is not None else load_stoplist()

    by_name = defaultdict(list)
    for ticker, name in rows:
        by_name[name].append(ticker)

    ambiguous = sorted(n for n, ts in by_name.items() if len(ts) > 1)
    name2ticker = {n: ts[0] for n, ts in by_name.items() if len(ts) == 1 and n not in stop}

    return {
        "names": sorted(name2ticker, key=len, reverse=True),
        "name2ticker": name2ticker,
        "codes": {t for t, _ in rows},
        "excluded": {
            "stoplist": sorted(n for n in by_name if n in stop),
            "ambiguous": ambiguous,
        },
    }


def extract_related_tickers(text: str, index: dict) -> list[str]:
    """從文字抽出 related_tickers（兩層策略，見模組 docstring）。

    ⚠️ `text` 請傳**標題**，不要傳 title+content。
       related_tickers 是「事件關於誰」，不是「內文提到誰」——理由與實測見模組 docstring。

    回傳代號清單（去重、排序）。抽不到就回 []——不猜。
    """
    if not text:
        return []

    found = set()

    # Layer 1：文中明寫代號，且該代號存在於 tickers 表
    for code in CODE_IN_TEXT.findall(text):
        if code in index["codes"]:
            found.add(code)

    # Layer 2：公司名最長匹配（掃到就挖掉，避免子字串重複命中）
    remain = text
    for name in index["names"]:
        if name in remain:
            found.add(index["name2ticker"][name])
            remain = remain.replace(name, "\t")

    return sorted(found)
