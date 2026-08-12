"""Prompt 資產（T13）：system prompt、user prompt 組裝、輸出 schema 與驗證。

單一真相原則：
  - prompt 文字以 `docs/spec/07_Prompt範本.md` 為準，改 prompt 先改該檔再同步這裡。
  - 輸出欄位以 `04_API_v2.md` §3「LLM 段」為準（title / summary / occurred_at_* /
    status / categories / expected_direction / direction_confidence / confidence_note）。
  - OUTPUT_SCHEMA 是兩家供應商 Structured Output 的共同來源，
    由 client.py 轉成各家格式（OpenAI json_schema / Gemini responseSchema）。

prompt caching 備註：system prompt 是固定前綴，放在最前面即可吃到兩家的自動快取
（折扣只在付費層有意義，見 06_附錄 §C），不需要額外寫任何 code。

自我測試（不需網路、不需金鑰）：
    python -m eventsignal.llm.prompts
"""

import re
from datetime import UTC, datetime, timedelta, timezone

# occurred_at_iso 無時區時視為台北時間（台股新聞的自然預設）；
# 未來日期判定加 1 天容差，避免時區邊界的當日事件被誤殺。
TAIPEI_TZ = timezone(timedelta(hours=8))

# ── 列舉值（與 04_API_v2 §2 一致，改這裡前先改契約） ──────────────

CATEGORIES = [
    "財報", "法說", "政策", "關稅", "AI/科技",
    "產業供需", "營收", "法人動向", "技術突破",
]

EVENT_STATUSES = [
    "official_confirmed", "rumor_unconfirmed", "developing", "market_reacting",
]

DIRECTIONS = ["利多", "利空", "中性"]

CONFIDENCES = ["high", "low"]

# 標準產業別（36 種）——LLM 直接標「純產業消息」的產業（例:「半導體業回溫」不點名公司）。
# 正本是 tickers.industry 的值集，由 load_tickers.py 的 INDUSTRY_CANON（歸檔於 attic/_handoff/nash/）
# 正規化 FinMind 產業分類而來；改產業集合請先改 load_tickers 再同步這裡（兩處必須一致，
# 否則 summarize 的「LLM 產業 ∪ 個股查表產業」會出現拼寫不一致的重複）。2026-07-25 同步。
INDUSTRIES = [
    "光電業", "其他", "其他電子業", "化學工業",
    "化學生技醫療", "半導體業", "塑膠工業", "居家生活",
    "建材營造", "數位雲端", "文化創意業", "橡膠工業",
    "水泥工業", "汽車工業", "油電燃氣業", "玻璃陶瓷",
    "生技醫療業", "紡織纖維", "綠能環保", "航運業",
    "觀光餐旅", "貿易百貨", "資訊服務業", "農業科技業",
    "通信網路業", "造紙工業", "運動休閒", "金融保險",
    "鋼鐵工業", "電器電纜", "電子工業", "電子通路業",
    "電子零組件業", "電機機械", "電腦及週邊設備業", "食品工業",
]

REQUIRED_FIELDS = [
    "title", "summary", "occurred_at_text", "occurred_at_iso",
    "status", "categories", "expected_direction", "direction_confidence",
    "confidence_note", "related_tickers", "industries",
]

# 台股證券代號：4 位數字開頭（2330 / 0050 / 00835B / 2887Z1 特別股皆合法）
TICKER_RE = re.compile(r"^\d{4}")

# ── System prompt（同 07_Prompt範本 §1，固定前綴、吃 prompt caching） ──

SYSTEM_PROMPT = """你是台股新聞事件編輯。你會收到「同一個事件」的多篇報導(已由上游去重聚類)。
請整理成一則人能在 30 秒內讀懂的事件物件,並標上類別與預期方向。

【鐵則】
1. 只根據提供的報導內容,不得補充報導中沒有的資訊、不得臆測,
   不得給任何買賣建議、目標價或投資評論。
2. 數字若多篇衝突:以官方來源(公開資訊觀測站 MOPS、公司公告、政府新聞稿)為準;
   無官方來源時,取多數一致的數字,並在 confidence_note 註明「未經官方證實」。
3. 摘要中立、精準、白話,2–4 句,涵蓋「發生什麼 / 目前進展 / (若有)市場關注點」。
4. 嚴格只輸出 JSON,不要加 markdown、不要加解釋。

【類別定義】(categories,可多選)
- 財報:公司「已公布、已實現」的營收/獲利/EPS/毛利率等財務數字(季或年)
- 法說:法人說明會/業績發表會場合的內容,或正式財測數字(未來導向);
  公司的建廠、增資、投資、併購等「規劃宣布」不是法說(視內容多屬產業供需)
- 政策:政府法規、央行貨幣政策、補助
- 關稅:進出口關稅、貿易限制(屬政策子類,可同時標「政策」)
- AI/科技:AI、先進製程、新技術趨勢與應用
- 產業供需:供應鏈、產能、上下游供需
- 營收:單月營收公布(僅月營收;季/年財務數字歸「財報」)
- 法人動向:三大法人「實際」買賣超、外資評等與目標價調整;
  報導中的「法人預估/法人看好」只是觀點,不算法人動向
- 技術突破:單一公司的新產品、專利、研發成果

【消歧】
- 月營收→營收;季/年 EPS 與獲利→財報;財測展望→法說(常與財報同時出現→多標籤);
  關稅可同時標政策。
- AI/科技 vs 技術突破:主詞是產業/趨勢→AI/科技;主詞是特定公司+具體成果(新產品/專利/研發)→技術突破;
  兩者共存時可同標。

【相關個股】(related_tickers,個股代號陣列,可為空)
- 標出報導「明確提及、且與本事件直接相關」的台股個股代號(4 位數字,如 2330)。
- 判準:是事件的主角或直接受衝擊的公司才標;只是被順帶提到、拿來比較、或當背景的不標。
- 產業型消息(例:某政策衝擊整個航運業)——報導點名了哪幾家就標哪幾家,不要自行腦補
  整個產業的成分股。事件屬個股或屬產業,由「標到幾家」自然反映,不需你額外判斷。
- 純總經/政策消息若沒有明確個股(例:升息、CPI),回空陣列 []。
- 只輸出報導裡真實出現的代號,不臆測、不補報導沒有的公司。個股所屬產業由系統查表,
  你在 related_tickers 不必標產業;事件的產業別請填下方【產業別】欄。

【產業別】(industries,標準產業別陣列,可為空)
- 標出「本事件直接關聯」的產業別,從系統提供的標準產業清單(schema enum)挑,只能用清單內的名稱。
- 主要用途是純產業型消息:報導談整個產業、未點名特定公司(例:「半導體業近期回溫」「航運運價大漲」),
  此時 related_tickers 會是空陣列,但事件確實關於某產業——用這欄補上。
- 個股型消息若事件明確落在某產業,也可一併標(與個股查表結果重複無妨,系統會自動聯集去重)。
- 判準:事件主體就是這個產業、或整個產業受同一衝擊才標;純總經/政策(升息、CPI)無明確產業時回 []。
- 不可自創或用簡稱:要寫清單裡的全名(如「半導體」須寫「半導體業」、「觀光」須寫「觀光餐旅」)。

【事件時間定義】(occurred_at_text / occurred_at_iso)
- 事件時間 = 消息「成立」的時間:官方發布時間、或事情實際發生的時間;
  不是未來預定舉行的時間。「公告訂於 8/28 召開法說」→ 事件時間是公告發布日,
  occurred_at_text 可寫「7/27 公告,預定 8/28 法說」保留預定資訊。
- occurred_at_iso 不得晚於任何一篇報導的發布時間;無法判定就給 null(不要猜)。
- occurred_at_text 一律用絕對日期(如「7/27 14:00」),不要只寫「今天/昨天」——
  讀者看到時已不知道相對哪一天;日期從報導的「時間」欄推算。

【狀態定義】(status,擇一)
- official_confirmed:官方已正式發布(MOPS/公告/政府)。
  判準看每篇報導的「類型」欄:僅當來源含 official 或 gov 時才可標;
  媒體(media)轉述公司公告或說法——即使逐字引述——不算官方確認,
  依情況改標 developing 或 rumor_unconfirmed
- rumor_unconfirmed:媒體報導但無官方證實
- developing:事件仍在演進、有後續
- market_reacting:消息已出、市場正在反應

【預期方向定義】(expected_direction,擇一 + 把握度)
- 利多:事件若成真,通常推升相關個股股價(例:接到新訂單、解除制裁)
- 利空:事件若成真,通常壓低股價(例:被制裁、財測下修)。
  公司特定的負面「事實」(已實現的虧損、財測下修、裁員、砍單、不配發股利)一律利空,
  不因報導辭氣平淡、或帶有局部亮點而降為中性。
  反例:「Q4 轉盈,但全年每股虧損 0.16 元、不配發股利」→ 利空(全年虧損與不配息是
  已實現的負面事實,單季轉盈不翻轉方向);方向確定但幅度存疑時,標 利空 + low,不要改標中性
- 中性:方向不明或影響中性;「大盤/產業整體漲跌」對個別公司方向不明時可標中性,
  但公司特定的負面事實不適用本項
另輸出 direction_confidence: high | low
  —— 方向明確(近乎事實)標 high;方向需依個股/情境而定或模糊時標 low。
重要:預期方向是「近乎事實的標記」,不是情緒分析,不得據此給買賣建議。
      (下游的市場驗證在 direction_confidence=low 時會略過一致性判斷,只列原始籌碼。)"""

# User prompt 尾段：即使已用 Structured Output 強制 schema，
# 仍保留欄位說明讓模型知道每個欄位「該填什麼」（與 07 範本一致）。
OUTPUT_INSTRUCTION = """請輸出以下 JSON(只輸出 JSON):
{
  "title": "中立、具體的事件標題(20 字內)",
  "summary": "2–4 句摘要",
  "occurred_at_text": "人類可讀絕對時間,如『7/27 14:00』『7/26 起流傳』『7/27 公告,預定 8/28 法說』",
  "occurred_at_iso": "ISO8601 時間 或 null(無法判定時)",
  "status": "official_confirmed | rumor_unconfirmed | developing | market_reacting",
  "categories": ["從 9 類挑選,可多選"],
  "expected_direction": "利多 | 利空 | 中性",
  "direction_confidence": "high | low",
  "confidence_note": "多來源一致 / 或:單一來源未證實之簡述",
  "related_tickers": ["報導明確提及且與事件直接相關的個股代號,無則 []"],
  "industries": ["本事件直接關聯的標準產業別,從清單挑,純產業消息用這欄,無則 []"]
}"""


def build_user_prompt(articles: list[dict]) -> str:
    """把同一事件的多篇報導組成 user prompt（07 範本 §1 的 User prompt）。

    articles 每篇需含:source / source_type / published_at / title / content。
    媒體類的 content 是導言/摘要(非全文),官方類才是全文——prompt 照這個現實設計。
    變動內容放在固定前綴(system prompt)之後,才吃得到 prompt caching。
    """
    blocks = []
    for i, a in enumerate(articles, 1):
        blocks.append(
            f"【報導 {i}】來源:{a.get('source', '?')}｜類型:{a.get('source_type', '?')}"
            f"｜時間:{a.get('published_at', '?')}\n"
            f"標題:{a.get('title', '')}\n"
            f"內文:{a.get('content', '')}"
        )
    return (
        f"以下是關於同一事件的 {len(articles)} 篇報導:\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        + OUTPUT_INSTRUCTION
    )


# ── 輸出 schema（canonical JSON Schema，client.py 轉各家格式） ──────

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "中立、具體的事件標題(20 字內)"},
        "summary": {"type": "string", "description": "2–4 句白話摘要"},
        "occurred_at_text": {"type": "string", "description": "人類可讀時間"},
        "occurred_at_iso": {
            "type": ["string", "null"],
            "description": "ISO8601 時間,無法判定時為 null",
        },
        "status": {"type": "string", "enum": EVENT_STATUSES},
        "categories": {
            "type": "array",
            "items": {"type": "string", "enum": CATEGORIES},
            "description": "9 類多標籤,至少 1 個",
        },
        "expected_direction": {"type": "string", "enum": DIRECTIONS},
        "direction_confidence": {"type": "string", "enum": CONFIDENCES},
        "confidence_note": {"type": "string"},
        "related_tickers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "相關個股代號(4 位數字);報導明確提及且直接相關者,無則空陣列",
        },
        "industries": {
            "type": "array",
            "items": {"type": "string", "enum": INDUSTRIES},
            "description": "本事件直接關聯的標準產業別;純產業消息用此欄補,無則空陣列",
        },
    },
    "required": REQUIRED_FIELDS,
    "additionalProperties": False,
}


def validate_output(data: dict) -> list[str]:
    """驗證 LLM 輸出是否符合契約，回傳問題清單（空列表 = 通過）。

    即使已用 Structured Output，仍做一層防呆（雙保險）:
    enum 合法、categories 非空且不重複、occurred_at_iso 可解析。
    """
    problems: list[str] = []
    if not isinstance(data, dict):
        return [f"輸出不是 JSON 物件: {type(data).__name__}"]

    for field in REQUIRED_FIELDS:
        if field not in data:
            problems.append(f"缺少欄位: {field}")
    if problems:
        return problems

    for field in ("title", "summary", "occurred_at_text", "confidence_note"):
        if not isinstance(data[field], str) or not data[field].strip():
            problems.append(f"{field} 須為非空字串")

    if data["status"] not in EVENT_STATUSES:
        problems.append(f"status 非法值: {data['status']!r}")
    if data["expected_direction"] not in DIRECTIONS:
        problems.append(f"expected_direction 非法值: {data['expected_direction']!r}")
    if data["direction_confidence"] not in CONFIDENCES:
        problems.append(f"direction_confidence 非法值: {data['direction_confidence']!r}")

    cats = data["categories"]
    if not isinstance(cats, list) or not cats:
        problems.append("categories 須為非空陣列")
    else:
        illegal = [c for c in cats if c not in CATEGORIES]
        if illegal:
            problems.append(f"categories 含非法值: {illegal}")
        if len(set(cats)) != len(cats):
            problems.append(f"categories 有重複: {cats}")

    iso = data["occurred_at_iso"]
    if iso is not None:
        try:
            # Python 3.11+ 的 fromisoformat 已支援 'Z' 結尾與時區偏移
            parsed = datetime.fromisoformat(str(iso))
        except ValueError:
            problems.append(f"occurred_at_iso 無法解析: {iso!r}")
        else:
            # 把預定舉行日誤標成事件日是實測常見錯誤（21 筆未來日期事件,
            # 見 docs/事件時間修正建議_給品誠_Bright.md）。無時區視為台北時間,
            # 不能直接與 aware now 比較——naive/aware 混比會 TypeError。
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=TAIPEI_TZ)
            if parsed > datetime.now(UTC) + timedelta(days=1):
                problems.append(
                    f"occurred_at_iso 在未來: {iso!r}(疑似把預定舉行日當事件日)")

    tickers = data["related_tickers"]
    if not isinstance(tickers, list):
        problems.append("related_tickers 須為陣列（無相關個股時給空陣列）")
    else:
        bad = [t for t in tickers if not isinstance(t, str) or not TICKER_RE.match(t)]
        if bad:
            problems.append(f"related_tickers 含非法代號（須 4 位數字開頭）: {bad}")

    inds = data["industries"]
    if not isinstance(inds, list):
        problems.append("industries 須為陣列（無明確產業時給空陣列）")
    else:
        bad_ind = [x for x in inds if x not in INDUSTRIES]
        if bad_ind:
            problems.append(f"industries 含非法產業別（須為標準產業清單內）: {bad_ind}")
        if len(set(inds)) != len(inds):
            problems.append(f"industries 有重複: {inds}")

    return problems


# ─────────────────────────────────────────────────────────────
# 自我測試：不需網路、不需金鑰，驗證 schema 與驗證器本身
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    good = {
        "title": "台積電法說:Q2 毛利率展望優於市場預期",
        "summary": "台積電在今日法說會釋出第二季財測,毛利率展望優於市場原先預期。",
        "occurred_at_text": "6/30 14:00",
        "occurred_at_iso": "2026-06-30T14:00:00+08:00",
        "status": "official_confirmed",
        "categories": ["法說", "財報"],
        "expected_direction": "利多",
        "direction_confidence": "high",
        "confidence_note": "多來源一致",
        "related_tickers": ["2330"],
        "industries": ["半導體業"],
    }
    assert validate_output(good) == [], validate_output(good)

    # occurred_at_iso 允許 null；related_tickers / industries 允許空陣列（純政策消息）
    assert validate_output({**good, "occurred_at_iso": None}) == []
    assert validate_output({**good, "related_tickers": []}) == []
    assert validate_output({**good, "industries": []}) == []
    # 純產業消息：無個股但有產業
    assert validate_output({**good, "related_tickers": [], "industries": ["航運業"]}) == []

    # 各種壞輸出都要被抓到
    bad_cases = [
        ({**good, "status": "confirmed"}, "status"),
        ({**good, "categories": []}, "categories"),
        ({**good, "categories": ["法說", "法說"]}, "重複"),
        ({**good, "categories": ["不存在的類"]}, "非法值"),
        ({**good, "expected_direction": "看多"}, "expected_direction"),  # 那是 chip_direction 的字彙
        ({**good, "occurred_at_iso": "昨天下午"}, "occurred_at_iso"),
        # 未來日期防線:帶時區與不帶時區(naive 不能炸 TypeError)都要抓到
        ({**good, "occurred_at_iso": "2030-01-01T14:00:00+08:00"}, "在未來"),
        ({**good, "occurred_at_iso": "2030-01-01T14:00:00"}, "在未來"),
        ({k: v for k, v in good.items() if k != "direction_confidence"}, "缺少欄位"),
        ({**good, "related_tickers": ["台積電"]}, "非法代號"),   # 要代號不要公司名
        ({**good, "related_tickers": "2330"}, "陣列"),          # 要陣列不要裸字串
        ({**good, "industries": ["半導體"]}, "非法產業別"),      # 要全名「半導體業」
        ({**good, "industries": ["台積電"]}, "非法產業別"),      # 產業欄不放公司名
        ({**good, "industries": ["半導體業", "半導體業"]}, "重複"),
        ({k: v for k, v in good.items() if k != "industries"}, "缺少欄位"),
    ]
    for bad, keyword in bad_cases:
        probs = validate_output(bad)
        assert probs and any(keyword in p for p in probs), f"該抓沒抓到({keyword}): {probs}"

    prompt = build_user_prompt([
        {"source": "工商時報", "source_type": "media",
         "published_at": "2026-06-30T15:02:00+08:00",
         "title": "台積電法說釋樂觀展望", "content": "台積電今日舉行法說會……(導言)"},
    ])
    assert "【報導 1】" in prompt and "只輸出 JSON" in prompt

    print("prompts.py 自我測試通過")
    print(f"  system prompt:{len(SYSTEM_PROMPT)} 字(固定前綴,吃 caching)")
    print(f"  9 類:{CATEGORIES}")
    print(f"  {len(INDUSTRIES)} 種標準產業(LLM industries enum)")
