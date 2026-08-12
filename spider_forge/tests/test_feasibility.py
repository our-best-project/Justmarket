"""spec v2 §3.2/§3.6：feasibility_triage 確定性分流 + escalate_human 非阻塞死信歸檔。

跑法（從 /）：
    python -m spider_forge.tests.test_feasibility
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from spider_forge import pipeline as graph


def _cleanup(path_str: str | None) -> None:
    if path_str and Path(path_str).exists():
        Path(path_str).unlink()


# ════════════════════════ (a) policy_kill：確定性挑戰頁 → KILL → 不進生成 ════════════════════════


def t_policy_kill_classifies_and_routes_to_escalate():
    state = {
        "site_url": "https://paywall.example.com/news",
        "validation": {"allowed_domains": ["paywall.example.com"]},
        "recon_report": {
            "http_status": 403,
            "title": "Access Denied — Please verify you are human",
            "soft_block_detected": True,
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [],
            "http_entry_sample": {"status": 403, "link_samples": []},
        },
    }
    result = graph.feasibility_triage(state)
    merged = {**state, **result}
    route = graph.route_after_triage(merged)
    ok = (
        result["feasibility"]["class"] == "KILL_policy_kill"
        and result["failure_class"] == "KILL_policy_kill"
        and route == "escalate_human"
    )
    return ok, f"class={result['feasibility']['class']} route={route}"


def t_kill_route_structurally_never_reaches_generation():
    """KILL 路徑絕不進生成：feasibility_triage 只能到 escalate_human/strategy_decision，
    generate_spider 只能從 collect_evidence 進來——兩者結構上不相交。
    """
    compiled = graph.build_pipeline()
    edges = compiled.get_graph().edges
    triage_targets = {e.target for e in edges if e.source == "feasibility_triage"}
    generate_sources = {e.source for e in edges if e.target == "generate_spider"}
    ok = (
        triage_targets == {"strategy_decision", "escalate_human"}
        and generate_sources == {"collect_evidence"}
    )
    return ok, f"triage_targets={sorted(triage_targets)} generate_sources={sorted(generate_sources)}"


def t_policy_kill_escalates_and_writes_dead_letter_without_touching_generate():
    state = {
        "site_url": "https://paywall.example.com/news",
        "source_prefix": "paywallex",
        "run_id": "test-policykill-0001",
        "validation": {"allowed_domains": ["paywall.example.com"]},
        "recon_report": {
            "http_status": 403,
            "soft_block_detected": True,
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [],
            "http_entry_sample": {"status": 403, "link_samples": []},
        },
    }
    triage = graph.feasibility_triage(state)
    merged = {**state, **triage}
    route = graph.route_after_triage(merged)
    dead_letter_path = None
    try:
        escalated = graph.escalate_human(merged)
        dead_letter_path = escalated.get("dead_letter_path")
        record = json.loads(Path(dead_letter_path).read_text(encoding="utf-8"))
        ok = (
            route == "escalate_human"
            and escalated["status"] == "escalated"
            and dead_letter_path
            and Path(dead_letter_path).exists()
            and record["failure_class"] == "KILL_policy_kill"
            and "spider_code" not in merged  # generate_spider 從未被呼叫，state 沒有它會寫入的欄位
        )
    finally:
        _cleanup(dead_letter_path)
    return ok, f"route={route} path={dead_letter_path}"


# ════════════════════════ (b) 乾淨 api 證據 → FEASIBLE_API → 往 strategy ════════════════════════


def t_clean_api_evidence_is_feasible_and_routes_to_strategy():
    state = {
        "site_url": "https://example.com/news",
        "validation": {"allowed_domains": ["example.com"]},
        "recon_report": {
            "http_status": 200,
            "soft_block_detected": False,
            "api_candidates": [
                {
                    "method": "GET",
                    "url": "https://example.com/api/news",
                    "body_excerpt": '{"items":[{"title":"x","url":"/news/1"}]}',
                    "article_record_count": 3,
                }
            ],
            "feed_candidates": [],
            "link_samples": [{"url": "https://example.com/news/1", "text": "x"}],
            "http_entry_sample": {
                "status": 200,
                "link_samples": [{"url": "https://example.com/news/1", "text": "x"}],
            },
        },
    }
    result = graph.feasibility_triage(state)
    merged = {**state, **result}
    route = graph.route_after_triage(merged)
    ok = (
        result["feasibility"]["class"] == "FEASIBLE_API"
        and "failure_class" not in result
        and route == "strategy_decision"
    )
    return ok, f"class={result['feasibility']['class']} route={route}"


# ════════════════════════ (c) discovery_empty → KILL → 死信 ════════════════════════


def t_discovery_empty_kills_and_writes_dead_letter():
    state = {
        "site_url": "https://empty.example.com/news",
        "source_prefix": "emptyex",
        "run_id": "test-empty-0001",
        "validation": {"allowed_domains": ["empty.example.com"]},
        "recon_report": {
            "http_status": 200,
            "soft_block_detected": False,
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [],
            "http_entry_sample": {"status": 200, "link_samples": []},
        },
    }
    triage = graph.feasibility_triage(state)
    merged = {**state, **triage}
    route = graph.route_after_triage(merged)
    dead_letter_path = None
    try:
        escalated = graph.escalate_human(merged)
        dead_letter_path = escalated.get("dead_letter_path")
        record = json.loads(Path(dead_letter_path).read_text(encoding="utf-8"))
        ok = (
            triage["feasibility"]["class"] == "KILL_discovery_empty"
            and route == "escalate_human"
            and escalated["status"] == "escalated"
            and Path(dead_letter_path).exists()
            and record["failure_class"] == "KILL_discovery_empty"
            and record["site_url"] == state["site_url"]
            and record["status"] == "dead_letter"
        )
    finally:
        _cleanup(dead_letter_path)
    return ok, f"class={triage['feasibility']['class']} route={route} path={dead_letter_path}"


def t_discovery_not_empty_when_recon_itself_failed():
    """recon 自己出錯（recon_error/navigation_error）是不確定訊號，不能被判 KILL。"""
    state = {
        "site_url": "https://flaky.example.com/news",
        "validation": {"allowed_domains": ["flaky.example.com"]},
        "recon_report": {
            "http_status": None,
            "recon_error": "playwright timeout",
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [],
            "http_entry_sample": {},
        },
    }
    result = graph.feasibility_triage(state)
    ok = result["feasibility"]["class"] in {"FEASIBLE_API", "FEASIBLE_HTML"}
    return ok, f"class={result['feasibility']['class']}"


def t_sample_urls_override_prevents_false_discovery_kill():
    """使用者提供 sample_urls 時，即使 recon 沒抓到任何連結，也不能誤殺。"""
    state = {
        "site_url": "https://knownsamples.example.com/news",
        "sample_urls": ["https://knownsamples.example.com/news/1"],
        "validation": {"allowed_domains": ["knownsamples.example.com"]},
        "recon_report": {
            "http_status": 200,
            "soft_block_detected": False,
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [],
            "http_entry_sample": {"status": 200, "link_samples": []},
        },
    }
    result = graph.feasibility_triage(state)
    ok = result["feasibility"]["class"] == "FEASIBLE_HTML"
    return ok, f"class={result['feasibility']['class']}"


# ════════════════════════ 其餘兩個 KILL 分類（完整性補測，非硬性驗收但強化正確性）════════════════════════


def t_signature_required_kills_when_only_locked_post_and_no_fallback():
    state = {
        "site_url": "https://locked.example.com/news",
        "validation": {"allowed_domains": ["locked.example.com"]},
        "recon_report": {
            "http_status": 200,
            "soft_block_detected": False,
            "api_candidates": [
                {
                    "method": "POST",
                    "url": "https://locked.example.com/api/news",
                    "request_post_data": "nonce=<redacted>&page=1",
                    "article_record_count": 0,
                }
            ],
            "feed_candidates": [],
            "link_samples": [],
            "http_entry_sample": {"status": 200, "link_samples": []},
        },
    }
    result = graph.feasibility_triage(state)
    ok = result["feasibility"]["class"] == "KILL_signature_required"
    return ok, f"class={result['feasibility']['class']}"


def t_js_required_kills_when_only_browser_rendered_links_exist():
    state = {
        "site_url": "https://spa.example.com/news",
        "validation": {"allowed_domains": ["spa.example.com"]},
        "recon_report": {
            "http_status": 200,
            "soft_block_detected": False,
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [{"url": "https://spa.example.com/news/1", "text": "Article"}],
            "http_entry_sample": {"status": 200, "link_samples": []},
        },
    }
    result = graph.feasibility_triage(state)
    ok = result["feasibility"]["class"] == "KILL_js_required"
    return ok, f"class={result['feasibility']['class']}"


def t_public_browser_path_survives_plain_http_block():
    state = {
        "site_url": "https://browser.example.com/news",
        "validation": {"allowed_domains": ["browser.example.com"]},
        "recon_report": {
            "http_status": 200,
            "access_assessment": "browser_required_http_blocked",
            "soft_block_detected": False,
            "api_candidates": [],
            "feed_candidates": [],
            "link_samples": [
                {
                    "url": "https://browser.example.com/news/1",
                    "text": "Article",
                }
            ],
            "http_entry_sample": {"status": 403, "link_samples": []},
        },
    }
    result = graph.feasibility_triage(state)
    merged = {**state, **result}
    route = graph.route_after_triage(merged)
    ok = (
        result["feasibility"]["class"] == "FEASIBLE_BROWSER"
        and "failure_class" not in result
        and route == "strategy_decision"
    )
    return ok, f"class={result['feasibility']['class']} route={route}"


# ════════════════════════ (d) escalate_human：死信格式正確、不阻塞 ════════════════════════


def t_escalate_writes_dead_letter_without_blocking():
    state = {
        "site_url": "https://retrylimit.example.com/news",
        "source_prefix": "retrylimitex",
        "run_id": "test-retrylimit-0001",
        "retry_count": 3,
        "kimi_used": True,
        "error_signature_history": ["http_403", "json_path_wrong", "http_403"],
        "diagnosis": {"error_signature": "http_403", "failure_type": "transport_blocked"},
        "topic_result": {"status": "ok"},
    }
    dead_letter_path = None
    try:
        t0 = time.time()
        result = graph.escalate_human(state)
        elapsed = time.time() - t0
        dead_letter_path = result.get("dead_letter_path")
        record = json.loads(Path(dead_letter_path).read_text(encoding="utf-8"))
        required_keys = {
            "ts",
            "site_url",
            "source_prefix",
            "failure_class",
            "triage_reason",
            "recon_evidence",
            "tried",
            "suggested_action",
            "status",
        }
        ok = (
            result["status"] == "escalated"
            and elapsed < 2.0  # 非阻塞：不應卡在 interrupt() 等待 resume
            and required_keys <= set(record)
            and record["tried"]["retry_count"] == 3
            and record["tried"]["kimi_used"] is True
            and record["failure_class"] == "repair_exhausted"
        )
    finally:
        _cleanup(dead_letter_path)
    return ok, f"status={result.get('status')} elapsed={elapsed:.3f}s"


def t_escalate_marks_topic_provider_outage_distinctly():
    state = {
        "site_url": "https://topicoutage.example.com/news",
        "source_prefix": "topicoutagex",
        "run_id": "test-topicoutage-0001",
        "topic_result": {"mode": "enforce", "provider": "gemini", "status": "gemini_unavailable"},
    }
    dead_letter_path = None
    try:
        result = graph.escalate_human(state)
        dead_letter_path = result.get("dead_letter_path")
        record = json.loads(Path(dead_letter_path).read_text(encoding="utf-8"))
        ok = (
            result["failure_class"] == "topic_provider_unavailable"
            and record["failure_class"] == "topic_provider_unavailable"
            and "主題閘門" in record["triage_reason"]
        )
    finally:
        _cleanup(dead_letter_path)
    return ok, f"failure_class={result.get('failure_class')}"


TESTS = [
    t_policy_kill_classifies_and_routes_to_escalate,
    t_kill_route_structurally_never_reaches_generation,
    t_policy_kill_escalates_and_writes_dead_letter_without_touching_generate,
    t_clean_api_evidence_is_feasible_and_routes_to_strategy,
    t_discovery_empty_kills_and_writes_dead_letter,
    t_discovery_not_empty_when_recon_itself_failed,
    t_sample_urls_override_prevents_false_discovery_kill,
    t_signature_required_kills_when_only_locked_post_and_no_fallback,
    t_js_required_kills_when_only_browser_rendered_links_exist,
    t_public_browser_path_survives_plain_http_block,
    t_escalate_writes_dead_letter_without_blocking,
    t_escalate_marks_topic_provider_outage_distinctly,
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
