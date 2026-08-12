"""Gemini topic classifier client.

Only sends a compact public-news feature (title + first 50 content characters).
The API key is read from ``LLM_API_KEY`` and is never included in prompts,
results, exceptions, or the Scrapy sandbox environment.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import requests

API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
TRANSIENT_HTTP = {429, 500, 502, 503, 504}


class GeminiTopicError(RuntimeError):
    """Missing credentials, transport failure, or invalid structured output."""


def _load_env() -> None:
    """Load the nearest .env without adding a python-dotenv dependency."""
    folders = [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]
    seen: set[Path] = set()
    for folder in folders:
        if folder in seen:
            continue
        seen.add(folder)
        env_file = folder / ".env"
        if not env_file.is_file():
            continue
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
        return


def _response_schema(indices: list[int]) -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": len(indices),
                "maxItems": len(indices),
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "minimum": min(indices),
                            "maximum": max(indices),
                        },
                        "decision": {
                            "type": "string",
                            "enum": ["accept", "reject", "review"],
                        },
                        "labels": {
                            "type": "array",
                            "maxItems": 2,
                            "items": {
                                "type": "string",
                                "enum": ["finance", "public_policy"],
                            },
                        },
                        "evidence": {
                            "type": "string",
                            "description": "最多 30 個中文字的直接判斷依據",
                        },
                    },
                    "required": ["index", "decision", "labels", "evidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def _prompt(rows: list[dict]) -> str:
    return (
        "你是財經與公共政策新聞的高精度分類閘門。\n"
        "finance：金融市場、投資、公司營運或財報、產業經濟、總體經濟、"
        "銀行保險、匯率利率、能源商品、國際貿易。\n"
        "public_policy：已提出或施行的政府政策、法律、監管、預算、稅制、"
        "央行措施、制裁、貿易／產業／勞動／能源政策。\n"
        "健康、娛樂、生活、體育、犯罪、純政治人物動態、選舉攻防，"
        "以及只順帶提到公司或市場者，必須 reject。\n"
        "只有主題直接相關才 accept；資訊不足才 review。"
        "accept 至少一個 label；reject 的 labels 必須為空；"
        "review 可列候選 label。每個 index 恰好輸出一次。\n"
        "輸入只包含標題與內文前 50 字：\n"
        + json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    )


def _extract_output_text(payload: dict) -> str:
    for step in reversed(payload.get("steps") or []):
        if step.get("type") != "model_output":
            continue
        for block in step.get("content") or []:
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                return block["text"]
    raise GeminiTopicError("Gemini 回應缺少 model_output text")


def classify_batch(
    rows: list[dict],
    *,
    model: str = DEFAULT_MODEL,
    timeout_s: float = 60.0,
    max_retries: int = 3,
    post_fn: Callable = requests.post,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict:
    """Classify 1–20 compact rows with Gemini Structured Output."""
    if not 1 <= len(rows) <= 20:
        raise ValueError("Gemini topic batch 必須介於 1 到 20 筆")
    indices = [int(row["index"]) for row in rows]
    if len(indices) != len(set(indices)):
        raise ValueError("Gemini topic batch index 不得重複")

    compact_rows = [
        {
            "index": index,
            "title": " ".join(str(row.get("title") or "").split()),
            "content_lead": " ".join(
                str(row.get("content_lead") or "").split()
            )[:50],
        }
        for index, row in zip(indices, rows)
    ]

    _load_env()
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise GeminiTopicError("Gemini 金鑰缺漏（env LLM_API_KEY）")

    body = {
        "model": model,
        "input": _prompt(compact_rows),
        "store": False,
        "generation_config": {
            "thinking_level": "minimal",
            "max_output_tokens": max(512, len(rows) * 100),
        },
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": _response_schema(indices),
        },
    }

    response = None
    delay = 2.0
    for attempt in range(max_retries + 1):
        try:
            response = post_fn(
                API_URL,
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=timeout_s,
            )
            if response.status_code < 400:
                break
            if response.status_code not in TRANSIENT_HTTP:
                raise GeminiTopicError(f"Gemini HTTP {response.status_code}")
            if attempt == max_retries:
                raise GeminiTopicError(
                    f"Gemini HTTP {response.status_code}，重試耗盡"
                )
            retry_after = response.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else delay
            except (TypeError, ValueError):
                wait = delay
            sleep_fn(min(wait, 60.0))
            delay *= 2
        except requests.RequestException as exc:
            if attempt == max_retries:
                raise GeminiTopicError("Gemini 連線失敗，重試耗盡") from exc
            sleep_fn(delay)
            delay *= 2

    if response is None:
        raise GeminiTopicError("Gemini 未回傳 response")
    try:
        payload = response.json()
    except ValueError as exc:
        raise GeminiTopicError("Gemini 回應不是合法 JSON") from exc
    if payload.get("status") != "completed":
        raise GeminiTopicError(
            f"Gemini interaction 未完成：{payload.get('status', 'unknown')}"
        )
    try:
        parsed = json.loads(_extract_output_text(payload))
    except json.JSONDecodeError as exc:
        raise GeminiTopicError("Gemini Structured Output 不是合法 JSON") from exc
    return {
        "model": model,
        "items": parsed.get("items") or [],
        "usage": payload.get("usage") or {},
        "interaction_id": payload.get("id"),
    }
