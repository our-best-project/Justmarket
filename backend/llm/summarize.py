"""LLM 管線步驟（T13/T14）：對每個去重後事件一次呼叫,寫回事件欄位並推進 status。

管線位置（SD §2.3）:
    讀 articles.status='clustered'（依 event_id 分組）
    → 一次 LLM 呼叫產出 摘要+9類+方向+direction_confidence+事件status+confidence_note+時間
    → 寫回 events 對應欄位（04_API_v2 §3「LLM 段」）
    → 把該事件成員文章的 articles.status 推進 'summarized'

⚠️ 兩個 status 別搞混（本模組是唯一同時碰兩者的人）:
    articles.status = 管線交接棒（clustered → summarized,本檔負責推進）
    事件 status     = 消息確認度（official_confirmed 等,是 LLM 的輸出欄位之一）

分層設計（比照 clustering_Timyo/dedup.py:核心不碰 DB,方便單測與重跑）:
    - summarize_event / summarize_batch:純函式,吃資料回資料
    - run_db_step():DB 讀寫層,等 Nash 的 T03 建表完成即可直接使用

節流（06_附錄 §C 免費層策略）:免費層瓶頸是 RPM,批次內每次呼叫之間
sleep LLM_SLEEP_SECONDS 秒（預設 3,建議 2–4）;單一事件失敗不中斷整批,
失敗事件留在 'clustered' 下一輪自動重跑。

用法:
    python -m backend.llm.summarize             # 跑 DB 步驟（需 T03 表 + DATABASE_URL）
    python -m backend.llm.summarize --limit 5   # 只處理前 5 個事件（試跑）
    python -m backend.llm.summarize --self-test # 免金鑰/免 DB 自我測試
"""

import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# 預設節流間隔（秒）。免費層 RPM 約 15–30 → 2–4 秒是安全區間;
# 若 Nash 排程層已另做全域節流,此值可調 0（與排程對齊,勿雙重節流）。
DEFAULT_SLEEP_SECONDS = 3.0

# LLM 產出、要寫回 events 表的欄位（= 04_API_v2 §3 填寫模組為「LLM」的列）。
# categories 與 related_tickers 是 JSONB，寫回時另外 json.dumps，故迴圈取值時排除。
EVENT_FIELDS = [
    "title", "summary", "occurred_at_text", "occurred_at_iso",
    "status", "categories", "expected_direction", "direction_confidence",
    "confidence_note", "related_tickers",
]
_JSONB_FIELDS = {"categories", "related_tickers"}


def summarize_event(articles: list[dict], client) -> dict:
    """對一個事件（多篇成員報導）做一次 LLM 呼叫,回傳驗證過的事件欄位。

    Args:
        articles: 成員文章,每篇含 source / source_type / published_at / title / content。
        client: 具 generate(system, user, schema) -> 結果（含 .data）的客戶端
                （client.create_client() 的產物;測試時可用假物件替身）。
    """
    from backend.llm import prompts

    if not articles:
        raise ValueError("事件沒有成員文章,無法摘要")
    result = client.generate(
        prompts.SYSTEM_PROMPT,
        prompts.build_user_prompt(articles),
        prompts.OUTPUT_SCHEMA,
    )
    return result.data


def summarize_batch(
    events: list[dict],
    client,
    sleep_seconds: float | None = None,
) -> tuple[list[dict], list[dict]]:
    """整批處理多個事件,呼叫間節流;單一事件失敗不中斷整批。

    Args:
        events: [{"event_id": str, "articles": [...]}, ...]
        sleep_seconds: 呼叫間隔;None 時讀 LLM_SLEEP_SECONDS（預設 3 秒）。

    Returns:
        (成功清單, 失敗清單):
        成功項 = {"event_id", "fields"(dict)};失敗項 = {"event_id", "error"(str)}。
    """
    if sleep_seconds is None:
        sleep_seconds = float(os.getenv("LLM_SLEEP_SECONDS", DEFAULT_SLEEP_SECONDS))

    done: list[dict] = []
    failed: list[dict] = []
    for i, event in enumerate(events):
        event_id = event.get("event_id", f"(第 {i + 1} 筆,無 event_id)")
        try:
            fields = summarize_event(event["articles"], client)
            done.append({"event_id": event_id, "fields": fields})
        except Exception as exc:   # 失敗隔離:記下原因,事件留在上一格狀態可重跑
            failed.append({"event_id": event_id, "error": str(exc)})
        if sleep_seconds > 0 and i < len(events) - 1:
            time.sleep(sleep_seconds)
    return done, failed


# ─────────────────────────────────────────────────────────────
# DB 層:等 T03（articles / events 表）就緒後即可直接使用
# ─────────────────────────────────────────────────────────────

def _ticker_mentioned(ticker: str, name: str | None, corpus: str) -> bool:
    """單檔判準：股號字面、公司名、或金控集團別名出現在原文即算提及。

    金控別名：新聞常寫子公司（兆豐銀/國泰人壽/中信證）而 tickers.name 是
    金控名（兆豐金）。名稱以「金」結尾時，用字根+常見子公司後綴補判——
    要求帶後綴的完整形式，避免「開發」這類常用詞裸字根誤判。
    """
    if ticker in corpus:
        return True
    if name and name in corpus:
        return True
    if name and name.endswith("金"):
        stem = name[:-1]
        return any(stem + sfx in corpus for sfx in ("金", "銀", "證", "投信", "人壽", "產險"))
    return False


def enforce_official_guard(fields: dict, articles: list[dict]) -> bool:
    """官方章防線（純函式,regression.py 回歸跑器與 run_db_step 共用同一套判準）:
    成員無 official/gov 來源時,把 official_confirmed 就地降級 developing。
    回傳是否有降級。

    為什麼需要：prompt 已寫明「媒體轉述——即使逐字引述——不算官方確認」,
    但 flash-lite 級模型系統性忽略,實測 69% 的 official_confirmed 成員清一色
    媒體（LLM段健檢報告 §B）。確定性後驗,不再依賴 prompt。
    """
    if fields.get("status") != "official_confirmed":
        return False
    if any("official" in (a.get("source_type") or "") or "gov" in (a.get("source_type") or "")
           for a in articles):
        return False
    fields["status"] = "developing"
    return True


EVENT_TIME_MAX_LOOKBACK_DAYS = 60


def enforce_event_time_guard(fields: dict, articles: list[dict]) -> bool:
    """事件時間下界防線（純函式,與 enforce_official_guard 同模式）:
    occurred_at_iso 早於「最早報導發布時間」超過 60 天 → 視為誤標,改用最早報導時間。
    回傳是否有修正。

    為什麼門檻是 60 天而不是 0：新聞報導前幾天發生的事是**常態**——實測 212 筆
    落在 0-3 天（如「長榮航 12 月營收」1/09 發生、1/10 見報）,那是正確的。
    真正的誤標是差距數月到數年（實測 4 筆,最誇張標成 2021-12-31,而報導在 2026-03）。
    門檻抓寬只修確定錯誤的,寧可漏放也不誤殺合理落差。

    prompt 已寫「occurred_at_iso 不得晚於任何一篇報導的發布時間」（上界）,
    validate_output 也擋了未來日期,但下界沒人檢查——validate_output 看不到
    articles,無從比對,故下界只能在這裡做。
    """
    iso = fields.get("occurred_at_iso")
    if not iso or not articles:
        return False
    from datetime import datetime, timedelta

    def _parse(v):
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            return d.replace(tzinfo=None)
        except (ValueError, TypeError):
            return None

    ev = _parse(iso)
    pubs = [p for p in (_parse(a.get("published_at")) for a in articles) if p]
    if ev is None or not pubs:
        return False
    earliest = min(pubs)
    if ev >= earliest - timedelta(days=EVENT_TIME_MAX_LOOKBACK_DAYS):
        return False
    fields["occurred_at_iso"] = earliest.isoformat()
    fields["occurred_at_text"] = f"{earliest:%-m/%-d} 見報" if os.name != "nt" \
        else f"{earliest.month}/{earliest.day} 見報"
    return True


def _verify_tickers(conn, tickers: list[str], articles: list[dict]) -> tuple[list[str], list[str]]:
    """LLM 給的 related_tickers 交叉驗證：只保留「成員文章原文（標題或內文）
    有提到」的代號——比對股號字面或 tickers.name 公司名。回 (保留, 剔除)。

    為什麼需要：prompt 已寫明「報導明確提及…無則 []」，但實測 LLM 仍有三種失控——
    ① 供應鏈腦補（可口可樂事件標 5009 榮剛/1802 台玻，5 篇原文零提及）
    ② 數字區間展開（一筆事件標 601 檔，2330 連號到 2999）
    ③ 大盤文標一串權值股。
    ①②會污染時間軸/市場反應圖（掛錯股票的籌碼），確定性驗證從源頭擋掉。
    這裡的判準是「有無被提及」（防幻覺），與 crawler 段 extract_related_tickers 的
    「事件關於誰」（只看標題）目的不同，故允許比對內文。
    """
    if not tickers:
        return [], []
    corpus = "\n".join(f"{a.get('title') or ''}\n{a.get('content') or ''}" for a in articles)
    names = {r["ticker"]: r["name"] for r in conn.execute(
        "SELECT ticker, name FROM tickers WHERE ticker = ANY(%(t)s)",
        {"t": list(tickers)},
    ).fetchall()}
    kept, dropped = [], []
    # dict.fromkeys 去重保序：LLM 偶爾輸出重複代號（實測 ['2888','2888']），
    # 不去重會原樣落庫、前端 badge 重複顯示
    for t in dict.fromkeys(tickers):
        # 值域防線（2026-08-11 DB 稽核新增）：台股代號最長 4 碼＋可選字母。
        # LLM 會把原文提及的外國股票代號也標進來（實測比亞迪 002594、NAVER
        # 035420.KS、三星生物 207940）——「原文有提及」擋不住它們，格式可以。
        # 4 碼不在主檔的（下市股）不在此擋，留給 names 查無→仍需原文提及的舊防線。
        if "." in t or (t.isdigit() and len(t) >= 5):
            dropped.append(t)
            continue
        (kept if _ticker_mentioned(t, names.get(t), corpus) else dropped).append(t)
    return kept, dropped


def _industries_for(conn, tickers: list[str]) -> list[str]:
    """個股代號 → 所屬法定產業（去重、穩定排序）。查不到產業的代號略過。

    B 方案的核心：事件的產業標籤由成員個股查 tickers.industry 得出，
    而非 LLM 判斷——查表對齊 tickers 值域，免產業幻覺、成分變動免回填。
    """
    if not tickers:
        return []
    rows = conn.execute(
        "SELECT DISTINCT industry FROM tickers "
        "WHERE ticker = ANY(%(t)s) AND industry IS NOT NULL",
        {"t": list(tickers)},
    ).fetchall()
    # run_db_step 的連線是 dict_row——不能用 r[0] 整數索引
    return sorted(r["industry"] for r in rows)


def run_db_step(database_url: str | None = None, limit: int | None = None,
                event_ids: list[str] | None = None) -> tuple[int, int]:
    """完整管線步驟:撈 clustered → LLM → 寫回 events → 推進 summarized。

    每個事件一個交易:寫回 events 與推進 articles.status 一起成功或一起不動,
    失敗事件維持 'clustered',下一輪排程自動重試。

    event_ids: 只處理這些事件（篩選層 scoring_Bright/prescreen.select_events_for_llm
        的產出,由 run.py 傳入;None = 維持原行為,處理全部 clustered 事件）。
        名單外的事件不動、維持 'clustered',之後來源數上升會自動再被選中。

    Returns:
        (成功事件數, 失敗事件數)
    """
    import json

    import psycopg
    from psycopg.rows import dict_row

    from backend.llm.client import _load_dotenv, create_client

    # 2026-08-08 起本檔改走 psycopg v3（工程審查缺口 2：全 codebase 曾同時需要
    # SQLAlchemy(psycopg 方言) 與 psycopg 兩套 driver，只因本檔歷史上先用了前者。
    # 介面與交易語意不變：每事件一個交易、失敗 rollback 維持 clustered）。
    _load_dotenv()
    database_url = database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("找不到 DATABASE_URL(.env),見 docs/onboarding/上手索引.md §3")

    client = create_client()

    # autocommit=True＋每事件 conn.transaction()：psycopg 慣用法——
    # 讀取不佔長交易（Neon 有 idle_in_transaction timeout），寫入才開交易。
    conn = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)

    # 撈出所有待處理文章,依 event_id 組回事件（去重段已回填 articles.event_id）
    rows = conn.execute(
        "SELECT event_id, source, source_type, published_at, title, content "
        "FROM articles "
        "WHERE status = 'clustered' AND event_id IS NOT NULL "
        "ORDER BY max(published_at) OVER (PARTITION BY event_id) DESC, "
        "event_id, published_at"
    ).fetchall()

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["event_id"], []).append({
            "source": row["source"],
            "source_type": row["source_type"],
            "published_at": str(row["published_at"]),
            "title": row["title"],
            "content": row["content"],
        })

    if event_ids is not None:
        # 篩選層名單:保序（呼叫端已按優先級排）,剔除不在 grouped 的（可能已被處理）
        selected = [eid for eid in event_ids if eid in grouped]
    else:
        selected = list(grouped)
    event_ids = selected[:limit] if limit else selected
    print(f"待摘要事件:{len(event_ids)} 個（clustered 文章 {len(rows)} 篇）")

    sleep_seconds = float(os.getenv("LLM_SLEEP_SECONDS", DEFAULT_SLEEP_SECONDS))

    def _persist(c, event_id: str, fields: dict) -> None:
        """單一事件的 DB 寫回（一個事件一個交易）。獨立成函式是為了斷線重試。"""
        with c.transaction():
            # 事件產業別 = 個股查表 ∪ LLM 直接標的產業（兩路互補）:
            #   查表:related_tickers → tickers.industry（個股消息自動歸產業,免 LLM 幻覺,
            #        成分變動免回填）。
            #   LLM:純產業消息未點名個股(related_tickers 空)時,靠 LLM 的 industries 補。
            # 兩邊同源於 36 種標準產業集,union 去重後拼寫一致（見 prompts.INDUSTRIES）。
            # 幻覺防線：LLM 給的代號須在成員文章原文出現過（見 _verify_tickers docstring）
            fields["related_tickers"], dropped = _verify_tickers(
                c, fields["related_tickers"], grouped[event_id])
            if dropped:
                print(f"  ⚠ {event_id} 剔除原文未提及的代號: {dropped}")
            if enforce_official_guard(fields, grouped[event_id]):
                print(f"  ⚠ {event_id} 成員無官方來源,official_confirmed 降級 developing")
            if enforce_event_time_guard(fields, grouped[event_id]):
                print(f"  ⚠ {event_id} 事件時間早於報導 60 天以上,已改用最早報導時間")
            looked_up = _industries_for(c, fields["related_tickers"])
            industries = sorted(set(looked_up) | set(fields.get("industries", [])))
            res = c.execute(
                "UPDATE events SET "
                "  title = %(title)s, summary = %(summary)s, "
                "  occurred_at_text = %(occurred_at_text)s, "
                "  occurred_at_iso = CAST(%(occurred_at_iso)s AS TIMESTAMPTZ), "
                "  status = %(status)s, "
                "  categories = CAST(%(categories)s AS JSONB), "
                "  expected_direction = %(expected_direction)s, "
                "  direction_confidence = %(direction_confidence)s, "
                "  confidence_note = %(confidence_note)s, "
                "  related_tickers = CAST(%(related_tickers)s AS JSONB), "
                "  industries = CAST(%(industries)s AS JSONB), "
                "  updated_at = now() "
                "WHERE event_id = %(event_id)s", {
                **{k: fields[k] for k in EVENT_FIELDS if k not in _JSONB_FIELDS},
                "categories": json.dumps(fields["categories"], ensure_ascii=False),
                "related_tickers": json.dumps(fields["related_tickers"], ensure_ascii=False),
                "industries": json.dumps(industries, ensure_ascii=False),
                "event_id": event_id,
            })
            if res.rowcount == 0:   # events 沒這列=去重段沒建,rollback、文章留在 clustered
                raise RuntimeError(f"events 表沒有 {event_id},上游去重段尚未建立事件列")
            c.execute(
                "UPDATE articles SET status = 'summarized' "
                "WHERE event_id = %(event_id)s AND status = 'clustered'",
                {"event_id": event_id})

    ok = fail = 0
    for i, event_id in enumerate(event_ids):
        try:
            fields = summarize_event(grouped[event_id], client)
            try:
                _persist(conn, event_id, fields)
            except psycopg.OperationalError:
                # ⚠️ 2026-08-11 事故（423 件連環失敗）：整批共用一條 Neon 連線跑數小時，
                # 連線一斷、之後每件都 "the connection is lost"。斷線＝環境問題不是
                # 資料問題——重連再試一次，別浪費已經打完的 LLM 呼叫。
                print(f"  ↻ {event_id} DB 連線中斷——重連後重試")
                try:
                    conn.close()
                except Exception:
                    pass
                conn = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)
                _persist(conn, event_id, fields)
            ok += 1
            print(f"  ✅ {event_id}: {fields['title']}")
        except Exception as exc:
            fail += 1
            print(f"  ❌ {event_id}: {exc}（維持 clustered,下輪重試）")
        if sleep_seconds > 0 and i < len(event_ids) - 1:
            time.sleep(sleep_seconds)

    conn.close()
    print(f"完成:成功 {ok}、失敗 {fail}")
    return ok, fail


if __name__ == "__main__":
    # argparse 而不是手刻 sys.argv:原版只認 --self-test,其他任何參數（包括 --help）
    # 全部掉進 else 直接 run_db_step(limit=None) 全量真跑——燒 API 額度＋改資料庫,
    # 而且畫面什麼都不顯示。argparse 讓未知參數報錯退出、--help 安全顯示說明。
    import argparse

    parser = argparse.ArgumentParser(
        description="LLM 摘要管線（T13/T14）:讀 clustered 事件 → LLM → 寫回 events。"
                    "不帶參數＝全量跑 DB 步驟（需 DATABASE_URL 與 LLM 金鑰,會寫資料庫）。")
    parser.add_argument("--self-test", action="store_true",
                        help="免金鑰/免 DB 自我測試（假客戶端驗證批次流程）")
    parser.add_argument("--limit", type=int, default=None,
                        help="只處理前 N 個事件（試跑用;不給＝全量）")
    args = parser.parse_args()

    if args.self_test:
        # ── 自我測試:不需網路/金鑰/DB,用假客戶端驗證批次流程 ──
        from dataclasses import dataclass

        @dataclass
        class _FakeResult:
            data: dict

        class _FakeClient:
            """回傳固定合法輸出的替身（驗證批次組裝與失敗隔離,不驗模型品質）。"""
            def generate(self, system_prompt, user_prompt, schema):
                assert "台股新聞事件編輯" in system_prompt   # 固定前綴有進來
                assert "【報導 1】" in user_prompt
                return _FakeResult(data={
                    "title": "測試事件標題",
                    "summary": "這是一段測試摘要。共兩句話。",
                    "occurred_at_text": "今天 14:00",
                    "occurred_at_iso": "2026-06-30T14:00:00+08:00",
                    "status": "official_confirmed",
                    "categories": ["法說"],
                    "expected_direction": "利多",
                    "direction_confidence": "high",
                    "confidence_note": "多來源一致",
                    "related_tickers": ["2330"],
                    "industries": ["半導體業"],
                })

        class _BrokenClient(_FakeClient):
            def generate(self, *args, **kwargs):
                raise RuntimeError("模擬 API 掛掉")

        from backend.llm.samples import SAMPLE_EVENTS

        done, failed = summarize_batch(SAMPLE_EVENTS, _FakeClient(), sleep_seconds=0)
        assert len(done) == 3 and not failed, (done, failed)
        assert set(EVENT_FIELDS) <= set(done[0]["fields"]), done[0]

        # 失敗隔離:整批壞掉也不該丟例外,而是回報每筆失敗原因
        done, failed = summarize_batch(SAMPLE_EVENTS, _BrokenClient(), sleep_seconds=0)
        assert not done and len(failed) == 3 and "模擬 API 掛掉" in failed[0]["error"]

        print("summarize.py 自我測試通過（批次組裝 + 失敗隔離）")
    else:
        # 預設跑 DB 步驟（需 T03 表 + DATABASE_URL + LLM 金鑰）
        run_db_step(limit=args.limit)
