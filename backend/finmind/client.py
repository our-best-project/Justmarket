"""FinMind API 客戶端：三大法人買賣超 / 股價量（全市場，需 Backer 以上方案）。

只負責「抓原始資料」，不算衍生欄位（那是 daily_batch 的事）。
Backer 能力：不帶 data_id、只給日期，一次拿回該日全市場。
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

FINMIND_API = "https://api.finmindtrade.com/api/v4/data"

# 三大法人資料集裡 name 欄的實際值 → 我們的三大分類
# 外資 = 外資及陸資 + 外資自營商；自營商 = 自行買賣 + 避險；投信 = 投信
FOREIGN_NAMES = {"Foreign_Investor", "Foreign_Dealer_Self"}
TRUST_NAMES = {"Investment_Trust"}
DEALER_NAMES = {"Dealer_self", "Dealer_Hedging"}


class RateLimitError(RuntimeError):
    """FinMind 呼叫額度用盡（429 或 402 上限訊息）——呼叫端應等待視窗重置後重試。"""


def _get_token() -> str:
    """讀 FINMIND_TOKEN，並清掉夾帶的換行/空白（已知這個 token 常帶隱形換行）。"""
    raw = os.environ.get("FINMIND_TOKEN", "")
    token = "".join(raw.split())  # 移除所有空白字元，含中間的 \n
    if not token:
        raise RuntimeError("FINMIND_TOKEN 未設定（請放進 .env）")
    return token


class FinMindClient:
    def __init__(self, token: str | None = None, max_retry: int = 3, pause: float = 0.4):
        self.token = token or _get_token()
        self.max_retry = max_retry
        self.pause = pause
        self.session = requests.Session()
        self.requests_used = 0  # 本程序內已發出的請求數（額度節流用）

    def _fetch(self, dataset: str, start_date: str, end_date: str,
               data_id: str | None = None) -> list[dict[str, Any]]:
        params = {
            "dataset": dataset,
            "start_date": start_date,
            "end_date": end_date,
            "token": self.token,
        }
        if data_id:
            params["data_id"] = data_id

        last_err: Exception | None = None
        for attempt in range(1, self.max_retry + 1):
            try:
                self.requests_used += 1
                resp = self.session.get(FINMIND_API, params=params, timeout=60)
                if resp.status_code == 402:
                    # 402 有兩種：方案等級不足 vs 呼叫次數達上限——用訊息區分
                    try:
                        msg = resp.json().get("msg", "")
                    except Exception:
                        msg = resp.text[:120]
                    low = str(msg).lower()
                    if "upper limit" in low or "上限" in low or "limit" in low:
                        raise RateLimitError(f"FinMind 額度上限（{msg}）")
                    raise RuntimeError(f"FinMind 回 402：{msg}（需更高方案）")
                if resp.status_code == 400:
                    # 帶出 FinMind 的實際訊息（常見：方案等級不足、參數錯誤）
                    try:
                        msg = resp.json().get("msg", resp.text[:120])
                    except Exception:
                        msg = resp.text[:120]
                    if "level" in str(msg).lower():
                        raise RuntimeError(
                            f"FinMind 方案等級不足（{msg}）——全市場查詢需 Backer；"
                            "register 只能帶 data_id 逐檔查（daily_batch 已為此設計）")
                    raise RuntimeError(f"FinMind 回 400：{msg}")
                if resp.status_code == 429:
                    if attempt >= self.max_retry:
                        raise RateLimitError("FinMind 429：速率上限（重試已用盡）")
                    time.sleep(30 * attempt)  # 短暫退避再試；仍撞 → 交給呼叫端長等
                    continue
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("status") != 200:
                    raise RuntimeError(f"FinMind 回應非 200：{payload.get('msg')}")
                time.sleep(self.pause)  # 客氣一點，避免撞額度
                return payload.get("data", [])
            except RateLimitError:
                raise  # 額度錯誤直接上拋，由呼叫端決定長等策略（等 55 分鐘續抓）
            except Exception as e:
                last_err = e
                time.sleep(2 * attempt)
        raise RuntimeError(f"FinMind 取數失敗（{dataset} {start_date}~{end_date}）：{last_err}")

    # ---- 全市場（不帶 data_id）⚠️ Backer 以上專屬；register 會 400 ----
    def price_market(self, date: str) -> list[dict[str, Any]]:
        """某一天全市場股價量（TaiwanStockPrice）。⚠️ 需 Backer；register 帳號會 400。"""
        return self._fetch("TaiwanStockPrice", date, date)

    def institutional_market(self, date: str) -> list[dict[str, Any]]:
        """某一天全市場三大法人買賣超（長格式）。⚠️ 需 Backer；register 帳號會 400。"""
        return self._fetch("TaiwanStockInstitutionalInvestorsBuySell", date, date)

    # ---- 單檔＋日期區間（register 免費即可；daily_batch 需求驅動設計的主力）----
    def price_stock(self, stock_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return self._fetch("TaiwanStockPrice", start_date, end_date, data_id=stock_id)

    def institutional_stock(self, stock_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return self._fetch(
            "TaiwanStockInstitutionalInvestorsBuySell", start_date, end_date, data_id=stock_id
        )


def aggregate_institutional(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, int]]:
    """把長格式三大法人資料聚合成 {(stock_id, date): {foreign_net, trust_net, dealer_net}}。

    net = buy - sell，單位：股（FinMind 原始就是股）。
    """
    out: dict[tuple[str, str], dict[str, int]] = {}
    for r in rows:
        key = (str(r["stock_id"]), str(r["date"]))
        acc = out.setdefault(key, {"foreign_net": 0, "trust_net": 0, "dealer_net": 0})
        net = int(r.get("buy", 0)) - int(r.get("sell", 0))
        name = r.get("name", "")
        if name in FOREIGN_NAMES:
            acc["foreign_net"] += net
        elif name in TRUST_NAMES:
            acc["trust_net"] += net
        elif name in DEALER_NAMES:
            acc["dealer_net"] += net
        # 其他 name（少數）忽略
    return out
