"""spec v2 §4：失敗分類法 + provider_failure 不計修復額度。

驗收重點：
- 供應商逾時/5xx（generation_error）→ failure_class=provider_failure，**不吃 retry_count**，
  改記 provider_retry_count，並有界重試供應商；累計超過上限才死信。
- live 付費牆（402）→ policy_kill，確定性短路（不叫診斷模型）→ 死信。
- 其餘可修復類 → 照原 retry_count 修復迴圈；分類結果進 diagnosis/failure_class 供死信記錄。

跑法（從 /）：
    python -m spider_forge.tests.test_failure_taxonomy
"""

from __future__ import annotations

import json
from pathlib import Path

from spider_forge import pipeline as graph


def _no_judge():
    """讓 judge 一被呼叫就爆，用來證明確定性/短路路徑沒有動用診斷模型。"""
    from spider_forge.clients import judge as judge_client

    original = judge_client.judge
    judge_client.judge = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("judge must not be called on this path")
    )
    return judge_client, original


def _cleanup(path_str: str | None) -> None:
    if path_str and Path(path_str).exists():
        Path(path_str).unlink()


# ════════════════════════ provider_failure：不吃修復額度 ════════════════════════


def t_provider_failure_does_not_consume_repair_budget():
    judge_client, original = _no_judge()
    try:
        result = graph.diagnose_failure(
            {
                "generation_error": "kimi read timeout after 300s",
                "retry_count": 1,
                "provider_retry_count": 0,
                "error_signature_history": ["json_path_wrong"],
                "test_result": {},
                "validation_result": {"pass": False},
            }
        )
    finally:
        judge_client.judge = original
    ok = (
        result["failure_class"] == "provider_failure"
        and result["diagnosis"]["error_signature"] == "provider_failure"
        and "retry_count" not in result  # 刻意不回傳：修復額度不被供應商問題消耗
        and result["provider_retry_count"] == 1
        and result["error_signature_history"][-1] == "provider_failure"
    )
    return ok, (
        f"class={result['failure_class']} "
        f"retry_count_in_result={'retry_count' in result} "
        f"provider_retry={result['provider_retry_count']}"
    )


def t_provider_failure_routes_bounded_retry_then_escalate():
    under_deepseek = graph.route_after_diagnose(
        {"failure_class": "provider_failure", "provider_retry_count": 1, "kimi_used": False}
    )
    under_kimi = graph.route_after_diagnose(
        {"failure_class": "provider_failure", "provider_retry_count": 2, "kimi_used": True}
    )
    over_budget = graph.route_after_diagnose(
        {"failure_class": "provider_failure", "provider_retry_count": 3, "kimi_used": True}
    )
    ok = (under_deepseek, under_kimi, over_budget) == (
        "repair_code",
        "repair_code_kimi",
        "escalate_human",
    )
    return ok, f"routes={(under_deepseek, under_kimi, over_budget)}"


def t_provider_failure_reads_failure_class_from_diagnosis_too():
    """route 也能從 diagnosis.failure_class 取類別（state 頂層與 diagnosis 任一即可）。"""
    route = graph.route_after_diagnose(
        {
            "diagnosis": {"failure_class": "provider_failure"},
            "provider_retry_count": 0,
            "kimi_used": False,
        }
    )
    return route == "repair_code", f"route={route}"


# ════════════════════════ policy_kill：live 402 付費牆 → 死信 ════════════════════════


def t_live_paywall_402_classifies_policy_kill_without_llm():
    judge_client, original = _no_judge()
    try:
        result = graph.diagnose_failure(
            {
                "retry_count": 0,
                "error_signature_history": [],
                "test_result": {
                    "stderr_tail": "DEBUG: Crawled (402) <GET https://paywall.example.com/news>"
                },
                "validation_result": {"pass": False},
            }
        )
    finally:
        judge_client.judge = original
    route = graph.route_after_diagnose({**result, **result})
    ok = (
        result["failure_class"] == "policy_kill"
        and result["diagnosis"]["error_signature"] == "http_402_paywall"
        and route == "escalate_human"
    )
    return ok, f"class={result['failure_class']} route={route}"


def t_bare_401_403_is_not_policy_kill():
    """灰色登入牆（401/403）不是付費牆——D2 規定照樣試，不可被判 policy_kill。"""
    from spider_forge.clients import judge as judge_client

    original = judge_client.judge
    judge_client.judge = lambda **kwargs: {
        "failure_type": "transport_blocked",
        "suggested_fix": "補 header 重試",
        "error_signature": "http_403",
    }
    try:
        result = graph.diagnose_failure(
            {
                "retry_count": 0,
                "error_signature_history": [],
                "test_result": {
                    "stderr_tail": "DEBUG: Crawled (403) <GET https://login.example.com/news>"
                },
                "validation_result": {"pass": False},
            }
        )
    finally:
        judge_client.judge = original
    route = graph.route_after_diagnose(result)
    ok = (
        result["failure_class"] != "policy_kill"
        and result["failure_class"] == "transport_blocked"
        and result["retry_count"] == 1
        and route == "repair_code"
    )
    return ok, f"class={result['failure_class']} route={route} retry={result['retry_count']}"


# ════════════════════════ 可修復類：照原迴圈、帶分類 ════════════════════════


def t_known_code_error_carries_class_and_still_repairs():
    judge_client, original = _no_judge()
    try:
        result = graph.diagnose_failure(
            {
                "test_result": {
                    "stderr_tail": (
                        "AttributeError: 'SelectorList' object has no attribute 'first'"
                    )
                },
                "validation_result": {"pass": False},
                "retry_count": 0,
                "error_signature_history": [],
            }
        )
    finally:
        judge_client.judge = original
    route = graph.route_after_diagnose(result)
    ok = (
        result["diagnosis"]["error_signature"] == "scrapy_selectorlist_first_invalid"
        and result["failure_class"] == "code_error"
        and result["retry_count"] == 1  # 可修復類照吃修復額度
        and route == "repair_code"
    )
    return ok, f"class={result['failure_class']} route={route} retry={result['retry_count']}"


def t_repairable_default_selector_schema_from_judge():
    from spider_forge.clients import judge as judge_client

    original = judge_client.judge
    judge_client.judge = lambda **kwargs: {
        "failure_type": "extraction_wrong",
        "suggested_fix": "修 selector",
        "error_signature": "json_path_wrong",
    }
    try:
        result = graph.diagnose_failure(
            {
                "test_result": {"stderr_tail": "0 items", "item_count": 0},
                "validation_result": {"pass": False, "reject_reasons": {"content_empty": 5}},
                "retry_count": 1,
                "error_signature_history": ["json_path_wrong"],
            }
        )
    finally:
        judge_client.judge = original
    second = graph.route_after_diagnose({**result})
    ok = (
        result["failure_class"] == "selector_schema"
        and result["retry_count"] == 2
        and second == "repair_code_kimi"  # retry_count 已到 2 → 換 Kimi
    )
    return ok, f"class={result['failure_class']} retry={result['retry_count']} route={second}"


def t_repairable_path_unchanged_when_no_failure_class():
    """沒有 failure_class 的舊狀態（回歸保護）：仍走原 retry_count 迴圈。"""
    first = graph.route_after_diagnose({"retry_count": 1, "max_retries": 2})
    second = graph.route_after_diagnose({"retry_count": 2, "max_retries": 2})
    exhausted = graph.route_after_diagnose(
        {"retry_count": 3, "max_retries": 2, "kimi_used": True}
    )
    ok = (first, second, exhausted) == ("repair_code", "repair_code_kimi", "escalate_human")
    return ok, f"routes={(first, second, exhausted)}"


# ════════════════════════ 死信：live policy_kill 記錄正確 ════════════════════════


def t_escalate_records_live_policy_kill_as_kill_class():
    state = {
        "site_url": "https://paywall.example.com/news",
        "source_prefix": "paywallex",
        "run_id": "test-livepaywall-0001",
        "failure_class": "policy_kill",
        "diagnosis": {"failure_class": "policy_kill", "error_signature": "http_402_paywall"},
        "retry_count": 1,
    }
    dead_letter_path = None
    try:
        result = graph.escalate_human(state)
        dead_letter_path = result.get("dead_letter_path")
        record = json.loads(Path(dead_letter_path).read_text(encoding="utf-8"))
        ok = (
            result["failure_class"] == "policy_kill"
            and record["failure_class"] == "policy_kill"
            and "不建議自動重排" in record["suggested_action"]
        )
    finally:
        _cleanup(dead_letter_path)
    return ok, f"failure_class={result.get('failure_class')} action={record['suggested_action'][:16]}"


TESTS = [
    t_provider_failure_does_not_consume_repair_budget,
    t_provider_failure_routes_bounded_retry_then_escalate,
    t_provider_failure_reads_failure_class_from_diagnosis_too,
    t_live_paywall_402_classifies_policy_kill_without_llm,
    t_bare_401_403_is_not_policy_kill,
    t_known_code_error_carries_class_and_still_repairs,
    t_repairable_default_selector_schema_from_judge,
    t_repairable_path_unchanged_when_no_failure_class,
    t_escalate_records_live_policy_kill_as_kill_class,
]


def main() -> int:
    failed = 0
    for test in TESTS:
        try:
            ok, detail = test()
        except Exception as exc:
            ok, detail = False, f"EXCEPTION {exc}"
        print(f"[{'PASS' if ok else 'FAIL'}] {test.__name__}: {detail}")
        failed += not ok
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
