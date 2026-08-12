"""spec v2 §3.5：repair 回饋升級——階段定位 + recon-vs-run diff + reject_reasons。

驗收重點：修復模型拿到的是「哪一階段壞了、recon 看到 vs run 跑出什麼、前幾大拒絕原因」，
而不是一坨原始 log 要它猜。

跑法（從 /）：
    python -m spider_forge.tests.test_repair_feedback
"""

from __future__ import annotations

from spider_forge.shared import repair as repair_stage
from spider_forge.shared.prompts import DEFAULT_TARGET_SCHEMA


def t_stage_crawl_did_not_finish_flags_transport():
    feedback = repair_stage._repair_feedback(
        {
            "test_result": {"passed": False, "exit_code": 1, "item_count": 0},
            "validation_result": {"flags": {"retrieval_ok": False}},
            "recon_report": {
                "access_assessment": "browser_blocked_http_ok",
                "http_entry_sample": {"status": 200},
            },
        }
    )
    ok = (
        feedback["failure_stage"] == "crawl_did_not_finish"
        and any("plain HTTP 拿到 200" in note for note in feedback["diff_notes"])
    )
    return ok, f"stage={feedback['failure_stage']} notes={len(feedback['diff_notes'])}"


def t_stage_discovery_diffs_recon_links_vs_zero_items():
    feedback = repair_stage._repair_feedback(
        {
            "test_result": {"passed": True, "item_count": 0},
            "validation_result": {
                "flags": {
                    "discovery_ok": False,
                    "retrieval_ok": False,
                    "extraction_ok": False,
                    "quality_ok": False,
                },
                "valid_count": 0,
            },
            "recon_report": {"link_samples": [{"url": "u"}] * 7},
            "evidence_pack": {
                "entry_observation": {"link_samples": [{"url": "u"}] * 7},
                "api_sample": {"body_excerpt": '{"items":[]}'},
            },
        }
    )
    ok = (
        feedback["failure_stage"] == "discovery"
        and feedback["recon_vs_run"]["recon_article_links_seen"] == 7
        and any("沒對上 recon 結構" in note for note in feedback["diff_notes"])
    )
    return ok, f"stage={feedback['failure_stage']} diff={feedback['diff_notes']}"


def t_stage_extraction_surfaces_top_reject_reason():
    feedback = repair_stage._repair_feedback(
        {
            "test_result": {"passed": True, "item_count": 12},
            "validation_result": {
                "flags": {
                    "discovery_ok": True,
                    "retrieval_ok": True,
                    "extraction_ok": False,
                    "quality_ok": False,
                },
                "reject_reasons": {"url_not_article": 8, "date_naive_no_tz": 3},
                "rejected_samples": [{"index": 0, "reasons": ["url_not_article"]}],
                "valid_count": 1,
            },
            "recon_report": {},
        }
    )
    ok = (
        feedback["failure_stage"] == "extraction"
        and list(feedback["top_reject_reasons"])[0] == "url_not_article"
        and feedback["top_reject_reasons"]["url_not_article"] == 8
        and any("url_not_article" in note for note in feedback["diff_notes"])
        and len(feedback["rejected_samples"]) == 1
    )
    return ok, f"stage={feedback['failure_stage']} top={list(feedback['top_reject_reasons'])[:1]}"


def t_stage_quality_when_only_quality_flag_fails():
    feedback = repair_stage._repair_feedback(
        {
            "test_result": {"passed": True, "item_count": 6},
            "validation_result": {
                "flags": {
                    "discovery_ok": True,
                    "retrieval_ok": True,
                    "extraction_ok": True,
                    "quality_ok": False,
                },
                "reject_reasons": {},
                "valid_count": 3,
                "unique_valid_count": 2,
            },
            "recon_report": {},
        }
    )
    ok = feedback["failure_stage"] == "quality" and "pagination" in feedback["stage_hint"]
    return ok, f"stage={feedback['failure_stage']}"


def t_repair_prompt_embeds_localization_block():
    prompt = repair_stage._repair_prompt(
        {
            "test_result": {"passed": True, "item_count": 12},
            "validation_result": {
                "flags": {
                    "discovery_ok": True,
                    "retrieval_ok": True,
                    "extraction_ok": False,
                    "quality_ok": False,
                },
                "reject_reasons": {"url_not_article": 8},
            },
            "recon_report": {},
            "evidence_pack": {},
            "diagnosis": {"error_signature": "json_path_wrong"},
            "spider_code": "class X: pass",
            "target_schema": DEFAULT_TARGET_SCHEMA,
            "source_prefix": "x",
            "site_name": "X",
        }
    )
    ok = (
        "【失敗定位】" in prompt
        and '"failure_stage": "extraction"' in prompt
        and "url_not_article" in prompt
    )
    return ok, f"has_block={'【失敗定位】' in prompt}"


TESTS = [
    t_stage_crawl_did_not_finish_flags_transport,
    t_stage_discovery_diffs_recon_links_vs_zero_items,
    t_stage_extraction_surfaces_top_reject_reason,
    t_stage_quality_when_only_quality_flag_fails,
    t_repair_prompt_embeds_localization_block,
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
