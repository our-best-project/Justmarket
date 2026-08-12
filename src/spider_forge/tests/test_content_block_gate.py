"""spec v2 §3.3：content-vs-block gate + intent-match(wrong_section)。

驗收重點：
- 抓到 200 但整批是挑戰/錯誤/雷同頁 → block_page_200 → 進 diagnose，不做欄位驗證。
- 確定性優先；只有可疑才呼叫 Gemini；Gemini 出錯 fail-open 判 content（不誤殺好站）。
- 主題整批漂走且結構有過 → wrong_section（沿用主題閘門既有 Gemini 輸出，不另呼叫）。

跑法（從 /）：
    python -m spider_forge.tests.test_content_block_gate
"""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

from spider_forge import pipeline as graph
from spider_forge.shared import repair as repair_stage
from spider_forge.stages import validate as validate_stage

_REAL = "台股今日收紅，權值股領漲，法人買超金額創近月新高，市場觀望聯準會利率決策。"


# ════════════════════════ 確定性偵測 ════════════════════════


def t_looks_like_block_matches_challenge_not_real_news():
    block = validate_stage._looks_like_block(
        {"title": "Just a moment...", "content": "Checking your browser before accessing."}
    )
    real = validate_stage._looks_like_block({"title": "台股收盤", "content": _REAL})
    return block and not real, f"block={block} real={real}"


def t_detect_block_when_majority_are_challenge_pages():
    items = [
        {"title": "Attention Required! Cloudflare", "content": "verify you are human"},
        {"title": "Access Denied", "content": "拒絕存取"},
        {"title": "台股", "content": _REAL},
    ]
    detection = validate_stage._detect_block_page(items)
    ok = detection["verdict"] == "block" and detection["method"] == "deterministic"
    return ok, f"detection={detection}"


def t_detect_block_when_contents_near_identical():
    items = [{"title": f"t{i}", "content": "同一段被重複的樣板內容" * 5} for i in range(5)]
    detection = validate_stage._detect_block_page(items)
    ok = detection["verdict"] == "block" and detection.get("near_identical") is True
    return ok, f"detection={detection}"


def t_detect_content_when_varied_real_articles():
    items = [
        {"title": "t1", "content": _REAL + "A"},
        {"title": "t2", "content": _REAL + "B 半導體出口成長"},
        {"title": "t3", "content": _REAL + "C 央行升息一碼"},
    ]
    detection = validate_stage._detect_block_page(items)
    ok = detection["verdict"] == "content" and detection["method"] == "deterministic"
    return ok, f"detection={detection}"


def t_ambiguous_uses_injected_gemini_and_can_flip_to_block():
    items = [
        {"title": "Access Denied", "content": "拒絕存取"},  # 1/4 命中 → ratio 0.25 可疑
        {"title": "t2", "content": _REAL + "B"},
        {"title": "t3", "content": _REAL + "C"},
        {"title": "t4", "content": _REAL + "D"},
    ]
    detection = validate_stage._detect_block_page(
        items, classify_fn=lambda leads: {"verdict": "block", "reason": "整批像錯誤頁"}
    )
    ok = detection["verdict"] == "block" and detection["method"] == "gemini"
    return ok, f"detection={detection}"


def t_ambiguous_gemini_error_fails_open_to_content():
    items = [
        {"title": "Access Denied", "content": "拒絕存取"},
        {"title": "t2", "content": _REAL + "B"},
        {"title": "t3", "content": _REAL + "C"},
        {"title": "t4", "content": _REAL + "D"},
    ]

    def boom(_leads):
        raise RuntimeError("gemini 連線失敗")

    detection = validate_stage._detect_block_page(items, classify_fn=boom)
    ok = detection["verdict"] == "content" and detection["method"] == "gemini_error_failopen"
    return ok, f"detection={detection}"


# ════════════════════════ content_block_gate 節點 + 路由 ════════════════════════


def _write_items(items: list[dict]) -> str:
    path = Path(tempfile.gettempdir()) / f"sf_block_{uuid.uuid4().hex[:8]}.json"
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return str(path)


def t_gate_flags_block_and_routes_to_diagnose():
    out = _write_items(
        [
            {"title": "Just a moment...", "content": "checking your browser"},
            {"title": "Attention Required", "content": "cloudflare verify you are human"},
        ]
    )
    try:
        result = validate_stage.content_block_gate(
            {"strategy": "dom", "test_result": {"passed": True, "output_path": out}}
        )
        route = graph.route_after_block_gate(result)
        ok = result["block_page_detected"] is True and route == "diagnose_failure"
    finally:
        Path(out).unlink(missing_ok=True)
    return ok, f"detected={result['block_page_detected']} route={route}"


def t_gate_passes_clean_run_to_validate():
    out = _write_items(
        [
            {"title": "t1", "content": _REAL + "A 台積電"},
            {"title": "t2", "content": _REAL + "B 鴻海"},
            {"title": "t3", "content": _REAL + "C 聯發科"},
        ]
    )
    try:
        result = validate_stage.content_block_gate(
            {"strategy": "dom", "test_result": {"passed": True, "output_path": out}}
        )
        route = graph.route_after_block_gate(result)
        ok = result["block_page_detected"] is False and route == "validate_output"
    finally:
        Path(out).unlink(missing_ok=True)
    return ok, f"detected={result['block_page_detected']} route={route}"


def t_gate_skips_unfinished_crawl():
    unfinished = validate_stage.content_block_gate(
        {"strategy": "dom", "test_result": {"passed": False}}
    )
    ok = unfinished["block_page_detected"] is False
    return ok, f"unfinished={unfinished}"


def t_diagnose_block_page_is_repairable_not_kill():
    result = repair_stage.diagnose_failure(
        {
            "block_page_detected": True,
            "block_detection": {"method": "deterministic", "block_ratio": 0.8},
            "retry_count": 0,
            "error_signature_history": [],
        }
    )
    route = graph.route_after_diagnose(result)
    ok = (
        result["failure_class"] == "block_page_200"
        and result["diagnosis"]["error_signature"] == "block_page_200"
        and result["retry_count"] == 1
        and route == "repair_code"  # 可修復一次（換傳輸），非直接死信
    )
    return ok, f"class={result['failure_class']} route={route} retry={result['retry_count']}"


# ════════════════════════ intent-match → wrong_section ════════════════════════


def t_wrong_section_from_topic_drift():
    from spider_forge.clients import judge as judge_client

    original = judge_client.judge
    judge_client.judge = lambda **kwargs: {
        "failure_type": "topic_mismatch",
        "suggested_fix": "換區塊",
        "error_signature": "topic_off",
    }
    try:
        result = repair_stage.diagnose_failure(
            {
                "test_result": {"passed": True, "item_count": 20, "stderr_tail": ""},
                "validation_result": {
                    "pass": False,
                    "flags": {
                        "discovery_ok": True,
                        "retrieval_ok": True,
                        "extraction_ok": True,
                        "quality_ok": True,
                    },
                },
                "topic_result": {
                    "status": "scored",
                    "gate_pass": False,
                    "relevant_ratio": 0.1,
                },
                "retry_count": 0,
                "error_signature_history": [],
            }
        )
    finally:
        judge_client.judge = original
    route = graph.route_after_diagnose(result)
    feedback = repair_stage._repair_feedback({**result, "test_result": {"passed": True}})
    ok = (
        result["failure_class"] == "wrong_section"
        and route == "repair_code"
        and feedback["failure_stage"] == "wrong_section"
    )
    return ok, f"class={result['failure_class']} route={route} stage={feedback['failure_stage']}"


def t_repair_feedback_block_page_stage():
    feedback = repair_stage._repair_feedback(
        {
            "block_page_detected": True,
            "failure_class": "block_page_200",
            "test_result": {"passed": True},
            "validation_result": {},
            "recon_report": {},
        }
    )
    ok = feedback["failure_stage"] == "block_page" and "replay_headers" in feedback["stage_hint"]
    return ok, f"stage={feedback['failure_stage']}"


# ════════════════════════ graph 結構 + Gemini client（注入）════════════════════════


def t_graph_wires_block_gate_between_sandbox_and_validate():
    compiled = graph.build_pipeline()
    edges = compiled.get_graph().edges
    sandbox_targets = {e.target for e in edges if e.source == "sandbox_test"}
    block_targets = {e.target for e in edges if e.source == "content_block_gate"}
    ok = (
        sandbox_targets == {"content_block_gate"}
        and block_targets == {"validate_output", "diagnose_failure"}
    )
    return ok, f"sandbox->{sorted(sandbox_targets)} block->{sorted(block_targets)}"


def t_gemini_page_client_parses_structured_verdict():
    from spider_forge.clients import page as gemini_page_client

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {"type": "text", "text": json.dumps({"verdict": "block", "reason": "挑戰頁"})}
                        ],
                    }
                ],
                "usage": {"total_tokens": 12},
                "id": "int-1",
            }

    import os

    os.environ.setdefault("LLM_API_KEY", "test-key")
    result = gemini_page_client.classify_page(
        ["拒絕存取", "verify you are human"],
        post_fn=lambda *a, **k: FakeResp(),
        sleep_fn=lambda _s: None,
    )
    ok = result["verdict"] == "block" and result["reason"] == "挑戰頁"
    return ok, f"verdict={result['verdict']}"


TESTS = [
    t_looks_like_block_matches_challenge_not_real_news,
    t_detect_block_when_majority_are_challenge_pages,
    t_detect_block_when_contents_near_identical,
    t_detect_content_when_varied_real_articles,
    t_ambiguous_uses_injected_gemini_and_can_flip_to_block,
    t_ambiguous_gemini_error_fails_open_to_content,
    t_gate_flags_block_and_routes_to_diagnose,
    t_gate_passes_clean_run_to_validate,
    t_gate_skips_unfinished_crawl,
    t_diagnose_block_page_is_repairable_not_kill,
    t_wrong_section_from_topic_drift,
    t_repair_feedback_block_page_stage,
    t_graph_wires_block_gate_between_sandbox_and_validate,
    t_gemini_page_client_parses_structured_verdict,
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
