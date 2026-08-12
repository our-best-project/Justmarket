"""各國大盤指數抓取：Yahoo chart API client。

用標準庫 urllib（零第三方依賴）——此 endpoint 只需一個 GET，不必動用 requests；
取數精神與 finmind_Bright/client.py 一致（自寫薄 REST client）。

Yahoo chart API 免 key、免費：GET query1.finance.yahoo.com/v8/finance/chart/<symbol>
回傳日線收盤 + meta（幣別/時區偏移）。symbol 例：^TWII、^GSPC、000300.SS。

當地交易日以 meta.gmtoffset（該市場 UTC 偏移秒數）換算，避免依賴 zoneinfo/tzdata
（Windows 無系統 IANA 時區庫，ZoneInfo 會找不到 Asia/Taipei）。日線 timestamp 為當地
收盤時刻、離午夜有數小時緩衝，用當前 offset 換算日期在實務上不受夏令時邊界影響。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime

CHART_API = "https://query1.finance.yahoo.com/v8/finance/chart/"
_HEADERS = {"User-Agent": "Mozilla/5.0"}  # 不帶 UA 會被 Yahoo 擋
_TIMEOUT = 30


class YahooFetchError(RuntimeError):
    """抓取或解析 Yahoo chart 回應失敗。"""


def fetch_index_history(yahoo_symbol: str, range_: str = "3mo") -> list[dict]:
    """抓單一指數的日線收盤序列，正序（舊→新）。

    range_ 預設 "3mo"：確保有足夠交易日回算 20 日報酬（A 股假期多，1mo 可能不足 21 筆）。
    回傳 [{"date": "2026-07-16", "close": 23618.17}, ...]，date 為該指數當地交易日。
    抓不到或格式異常 → raise YahooFetchError（呼叫端決定跳過或中止）。
    """
    sym = urllib.parse.quote(yahoo_symbol)  # ^TWII → %5ETWII
    url = f"{CHART_API}{sym}?range={urllib.parse.quote(range_)}&interval=1d"
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise YahooFetchError(f"{yahoo_symbol}: 請求失敗 {e}") from e

    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise YahooFetchError(f"{yahoo_symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise YahooFetchError(f"{yahoo_symbol}: 空 result")

    result = results[0]
    meta = result.get("meta") or {}
    gmtoffset = int(meta.get("gmtoffset") or 0)  # 該市場 UTC 偏移（秒）
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []

    rows: list[dict] = []
    for ts, close in zip(timestamps, closes):
        if close is None:  # 停牌/無成交日 Yahoo 給 null，跳過
            continue
        local_date = datetime.fromtimestamp(ts + gmtoffset, UTC).date()
        rows.append({"date": local_date.isoformat(), "close": float(close)})
    if not rows:
        raise YahooFetchError(f"{yahoo_symbol}: 無有效收盤（全 null）")
    return rows


if __name__ == "__main__":
    # 自測：抓幾個代表指數，印最新收盤（驗證 endpoint 與解析）
    for sym in ("^TWII", "^GSPC", "000300.SS", "^STOXX50E"):
        try:
            hist = fetch_index_history(sym)
            last = hist[-1]
            print(f"OK   {sym:12s} 筆數={len(hist):2d} 最新 {last['date']} close={last['close']}")
        except YahooFetchError as e:
            print(f"FAIL {sym:12s} {e}")
