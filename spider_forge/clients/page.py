"""Gemini page-authenticity classifier（spec v2 §3.3 content-vs-block）。

只在確定性規則抓不到、但可疑的情況下才呼叫（保護額度）：判斷抓到的「文章」其實是不是
挑戰頁 / 錯誤頁 / 空殼頁。只送每筆內容前 200 字的精簡特徵，金鑰讀 ``LLM_API_KEY``，
永不進 prompt、結果、例外或沙盒環境。結構與重試邏輯沿用已驗證的 gemini_topic_client。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable

import requests

from .topic import (
    API_URL,
    TRANSIENT_HTTP,
    GeminiTopicError,
    _extract_output_text,
    _load_env,
)

DEFAULT_MODEL = "gemini-3.5-flash-lite"


class GeminiPageError(GeminiTopicError):
    """頁面真偽分類失敗（缺金鑰、傳輸失敗、或結構化輸出不合法）。"""


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["content", "block"]},
        "reason": {"type": "string", "description": "最多 30 個中文字的直接依據"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}


def _prompt(content_leads: list[str]) -> str:
    return (
        "你在判斷一批抓取結果是『真的新聞內文』還是『被擋的頁面』。\n"
        "block：Cloudflare/人機驗證/『請稍候』/『checking your browser』/登入牆/"
        "404/5xx 錯誤頁/空白樣板/整批內容幾乎一模一樣。\n"
        "content：實際有各自不同的新聞正文。\n"
        "只要整批看起來是被擋或非文章就回 block，否則回 content。\n"
        "輸入是每筆內容前 200 字：\n"
        + json.dumps(content_leads, ensure_ascii=False, separators=(",", ":"))
    )


def classify_page(
    content_leads: list[str],
    *,
    model: str = DEFAULT_MODEL,
    timeout_s: float = 45.0,
    max_retries: int = 2,
    post_fn: Callable = requests.post,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict:
    """回 {'verdict': 'content'|'block', 'reason': str, 'usage': {...}}。"""
    leads = [str(text or "")[:200] for text in content_leads if str(text or "").strip()][:5]
    if not leads:
        raise ValueError("classify_page 需要至少一筆非空 content_lead")

    _load_env()
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise GeminiPageError("Gemini 金鑰缺漏（env LLM_API_KEY）")

    body = {
        "model": model,
        "input": _prompt(leads),
        "store": False,
        "generation_config": {"thinking_level": "minimal", "max_output_tokens": 256},
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": _RESPONSE_SCHEMA,
        },
    }

    response = None
    delay = 2.0
    for attempt in range(max_retries + 1):
        try:
            response = post_fn(
                API_URL,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=body,
                timeout=timeout_s,
            )
            if response.status_code < 400:
                break
            if response.status_code not in TRANSIENT_HTTP:
                raise GeminiPageError(f"Gemini HTTP {response.status_code}")
            if attempt == max_retries:
                raise GeminiPageError(f"Gemini HTTP {response.status_code}，重試耗盡")
            sleep_fn(min(delay, 30.0))
            delay *= 2
        except requests.RequestException as exc:
            if attempt == max_retries:
                raise GeminiPageError("Gemini 連線失敗，重試耗盡") from exc
            sleep_fn(delay)
            delay *= 2

    if response is None:
        raise GeminiPageError("Gemini 未回傳 response")
    try:
        payload = response.json()
    except ValueError as exc:
        raise GeminiPageError("Gemini 回應不是合法 JSON") from exc
    if payload.get("status") != "completed":
        raise GeminiPageError(f"Gemini interaction 未完成：{payload.get('status', 'unknown')}")
    try:
        parsed = json.loads(_extract_output_text(payload))
    except json.JSONDecodeError as exc:
        raise GeminiPageError("Gemini Structured Output 不是合法 JSON") from exc
    verdict = parsed.get("verdict")
    if verdict not in {"content", "block"}:
        raise GeminiPageError(f"verdict 不合法：{verdict!r}")
    return {
        "verdict": verdict,
        "reason": str(parsed.get("reason") or "")[:120],
        "usage": payload.get("usage") or {},
        "interaction_id": payload.get("id"),
    }
