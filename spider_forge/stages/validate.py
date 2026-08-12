"""內容封鎖、欄位品質與主題相關性驗證。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..state import SpiderForgeState

_BLOCK_PAGE_RES = tuple(
    re.compile(p, re.IGNORECASE | re.DOTALL)
    for p in (
        r"\baccess denied\b",
        r"\brequest (?:was )?blocked\b",
        r"\battention required\b",
        r"\bverify (?:that )?you are human\b",
        r"\bchecking your browser\b",
        r"\bjust a moment\b",
        r"\benable (?:java\s*script|javascript|cookies?)\b",
        r"\bcloudflare\b",
        r"\b(?:404 not found|error 50\d|service unavailable)\b",
        r"拒絕存取",
        r"請確認您不是機器人",
        r"請稍候",
        r"服務暫時無法使用",
    )
)


def _looks_like_block(item: dict) -> bool:
    blob = f"{item.get('title') or ''} {str(item.get('content') or '')[:800]}"
    return any(pattern.search(blob) for pattern in _BLOCK_PAGE_RES)


def _detect_block_page(items: list[dict], classify_fn=None) -> dict:
    """確定性優先偵測 block_page_200；只有可疑（部分命中）且啟用時才呼叫 Gemini 確認。
    Gemini 任何錯誤都 fail-open 判 content——誤殺好站比漏判 block 更傷成功率
    （漏判的 block 後面 validators 仍會以 content_soft_block 擋下）。"""
    n = len(items)
    if n == 0:
        return {"verdict": "unknown", "method": "no_items", "block_ratio": 0.0}
    block_hits = sum(1 for item in items if _looks_like_block(item))
    nonempty = [str(item.get("content") or "") for item in items if str(item.get("content") or "").strip()]
    unique_leads = len({content[:200] for content in nonempty})
    near_identical = bool(n >= 3 and nonempty and unique_leads <= max(1, n // 5))
    ratio = block_hits / n
    if ratio >= 0.5 or near_identical:
        return {
            "verdict": "block",
            "method": "deterministic",
            "block_ratio": round(ratio, 2),
            "near_identical": near_identical,
        }
    if 0 < ratio < 0.5 and classify_fn is not None:
        try:
            verdict = classify_fn((nonempty or [str(i.get("content") or "") for i in items])[:5])
            if verdict.get("verdict") == "block":
                return {
                    "verdict": "block",
                    "method": "gemini",
                    "reason": verdict.get("reason"),
                    "block_ratio": round(ratio, 2),
                }
            return {"verdict": "content", "method": "gemini", "block_ratio": round(ratio, 2)}
        except Exception as exc:
            return {
                "verdict": "content",
                "method": "gemini_error_failopen",
                "error": str(exc)[:200],
                "block_ratio": round(ratio, 2),
            }
    return {"verdict": "content", "method": "deterministic", "block_ratio": round(ratio, 2)}


def _gemini_block_classifier(cfg: dict | None):
    """只有站設定明確 provider=gemini 才回可呼叫的分類器；否則 None（純確定性，省額度）。"""
    if (cfg or {}).get("provider") != "gemini":
        return None
    from ..clients.page import classify_page

    model = str((cfg or {}).get("gemini_model") or "gemini-3.5-flash-lite")
    timeout = float((cfg or {}).get("gemini_timeout_s") or 45.0)
    return lambda leads: classify_page(leads, model=model, timeout_s=timeout)


def content_block_gate(state: SpiderForgeState) -> dict:
    """sandbox 後、欄位驗證前：抓到 200 但其實是 block/錯誤頁就歸 block_page_200，
    直接進 diagnose 不浪費欄位驗證與 repair 猜測（spec v2 §3.3）。"""
    test = state.get("test_result") or {}
    if not test.get("passed"):
        return {"block_page_detected": False}
    items: list[dict] = []
    try:
        path = Path(test.get("output_path", ""))
        if path.exists() and path.stat().st_size:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            items = loaded if isinstance(loaded, list) else []
    except Exception:
        items = []
    detection = _detect_block_page(items, _gemini_block_classifier(state.get("block_gate")))
    return {
        "block_page_detected": detection["verdict"] == "block",
        "block_detection": detection,
    }


def _item_preview(item: dict) -> dict:
    return {
        key: (value[:500] if isinstance(value, str) else value)
        for key, value in item.items()
        if key in {"title", "url", "content", "published_at", "source_record_id"}
    }


def validate_output(state: SpiderForgeState) -> dict:
    from ..shared.quality_rules import validate_items

    test = state.get("test_result") or {}
    if not test.get("passed"):
        reason = test.get("error") or test.get("stderr_tail", "")[-500:]
        return {
            "validation_result": {
                "pass": False,
                "flags": {"retrieval_ok": False},
                "item_count": 0,
                "issues": [f"crawl 未成功結束：{reason}"],
            },
            "status": "validating",
        }

    items = []
    try:
        path = Path(test.get("output_path", ""))
        if path.exists() and path.stat().st_size:
            items = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        items = []
    result = validate_items(items, state.get("validation") or {})
    result["issues"] = (
        [f"flag_fail:{key}" for key, passed in result["flags"].items() if not passed]
        + [f"{key}={value}" for key, value in result["reject_reasons"].items()]
    )
    result["item_samples"] = [_item_preview(item) for item in items[:3]]
    return {"validation_result": result, "status": "validating"}


def apply_topic_gate(state: SpiderForgeState) -> dict:
    """結構 gate 後的單一主題節點；預設 Gemini enforce，錯誤 fail-closed。"""
    from ..shared.topic import evaluate_topic_gate

    result = evaluate_topic_gate(state)
    result["status"] = "topic_validating"
    return result
