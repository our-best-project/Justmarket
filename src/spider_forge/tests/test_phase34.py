"""第三、四階段驗收：提前停止、證據重播、模型分級與執行指標。"""

from __future__ import annotations

import importlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from spider_forge import pipeline
from spider_forge.clients import browser, coder, judge
from spider_forge.output import artifacts
from spider_forge.runs import ledger
from spider_forge.shared import evidence, generation, repair
from spider_forge.shared.prompts import DEFAULT_TARGET_SCHEMA

batch = importlib.import_module("spider_forge.runs.batch")


def _base_state() -> dict:
    return {
        "site_url": "https://example.com/news",
        "site_name": "example.com",
        "source_prefix": "example_com",
        "target_schema": DEFAULT_TARGET_SCHEMA,
        "sample_urls": [],
        "constraints": {"max_pages": 2},
        "validation": {
            "allowed_domains": ["example.com"],
            "min_valid_items": 1,
        },
        "access_mode": "public",
    }


def t_recon_does_not_fetch_robots():
    requested: list[str] = []
    original_fetch = evidence._fetch_sample
    original_probe = browser.probe

    def fetch(url, **kwargs):
        requested.append(url)
        return {
            "requested_url": url,
            "final_url": url,
            "status": 200,
            "body_excerpt": "<html><body>news</body></html>",
            "link_samples": [],
        }

    browser.probe = lambda url, **kwargs: {
        "url": url,
        "final_url": url,
        "http_status": 200,
        "api_candidates": [],
        "link_samples": [],
    }
    evidence._fetch_sample = fetch
    try:
        result = pipeline.recon(_base_state())
    finally:
        evidence._fetch_sample = original_fetch
        browser.probe = original_probe

    robots_requests = [
        url for url in requested if url.rstrip("/").endswith("robots.txt")
    ]
    ok = (
        not robots_requests
        and "robots_policy" not in result["recon_report"]
    )
    return ok, f"requested={requested}"


def t_kill_path_makes_zero_judge_and_coder_calls():
    calls = {"judge": 0, "coder": 0}
    originals = {
        "recon": pipeline.recon,
        "escalate_human": pipeline.escalate_human,
        "judge": judge.judge,
        "complete": coder.complete,
    }

    def fake_recon(state):
        return {
            "recon_report": {
                "url": state["site_url"],
                "final_url": state["site_url"],
                "canonical_url": state["site_url"],
                "http_status": None,
                "api_candidates": [],
                "feed_candidates": [],
                "link_samples": [],
                "http_entry_sample": {},
                "access_assessment": "browser_public_ok",
            },
            "status": "reconning",
        }

    def fake_escalate(state):
        return {
            "failure_class": state["failure_class"],
            "status": "escalated",
        }

    def count_judge(**kwargs):
        calls["judge"] += 1
        raise AssertionError("提前停止後不應呼叫 judge")

    def count_coder(*args, **kwargs):
        calls["coder"] += 1
        raise AssertionError("提前停止後不應呼叫 coder")

    try:
        pipeline.recon = fake_recon
        pipeline.escalate_human = fake_escalate
        judge.judge = count_judge
        coder.complete = count_coder
        graph = pipeline.build_pipeline()
        result = graph.invoke(
            {"site_url": "https://example.com/private", "run_id": "kill-test"},
            config={"configurable": {"thread_id": "kill-test"}},
        )
    finally:
        for name in ("recon", "escalate_human"):
            setattr(pipeline, name, originals[name])
        judge.judge = originals["judge"]
        coder.complete = originals["complete"]

    ok = (
        result["status"] == "escalated"
        and result["failure_class"] == "KILL_discovery_empty"
        and calls == {"judge": 0, "coder": 0}
    )
    return ok, f"status={result['status']} calls={calls}"


def t_policy_failure_stops_before_second_repair():
    result = repair.diagnose_failure(
        {
            **_base_state(),
            "test_result": {
                "passed": False,
                "stderr_tail": "HTTP 402 Payment Required",
            },
            "validation_result": {"pass": False},
            "retry_count": 0,
            "error_signature_history": [],
        }
    )
    route = pipeline.route_after_diagnose(result)
    ok = (
        result["retry_count"] == 1
        and result["failure_class"] == "policy_kill"
        and route == "escalate_human"
    )
    return ok, f"class={result['failure_class']} retry={result['retry_count']} route={route}"


def t_request_evidence_redacts_secrets():
    safe = browser._safe_request_headers(
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Referer": "https://example.com/news?token=secret&section=market",
            "Authorization": "Bearer must-not-leak",
            "Cookie": "sid=must-not-leak",
            "X-CSRF-Token": "must-not-leak",
        }
    )
    ok = (
        safe["accept"] == "application/json"
        and safe["content-type"] == "application/json"
        and "section=market" in safe["referer"]
        and "secret" not in safe["referer"]
        and "authorization" not in safe
        and "cookie" not in safe
        and "x-csrf-token" not in safe
    )
    return ok, f"headers={safe}"


def t_evidence_pack_keeps_exact_replay_exchange():
    candidate = {
        "method": "POST",
        "url": "https://example.com/api/news",
        "status": 200,
        "content_type": "application/json",
        "request_headers": {
            "content-type": "application/json",
            "referer": "https://example.com/news",
        },
        "request_post_data": '{"page":1,"size":20}',
        "response_headers": {"content-type": "application/json"},
        "body_excerpt": (
            '{"items":[{"id":"n1","title":"Market decision",'
            '"published_at":"2026-07-29T09:00:00+08:00"}]}'
        ),
        "body_truncated": False,
        "article_record_count": 1,
        "json_shape": {"items": "list"},
    }
    state = {
        **_base_state(),
        "recon_report": {
            "final_url": "https://example.com/news",
            "canonical_url": "https://example.com/news",
            "http_status": 200,
            "api_candidates": [candidate],
            "feed_candidates": [],
            "link_samples": [],
            "http_entry_sample": {
                "status": 200,
                "body_excerpt": "<html></html>",
                "safe_request_headers": {"accept": "text/html"},
            },
            "access_assessment": "browser_public_ok",
        },
        "strategy": "api",
        "strategy_detail": {
            "strategy": "api",
            "chosen_api": candidate["url"],
        },
    }
    result = evidence.collect_evidence(state)
    exchange = result["evidence_pack"]["replay_exchange"]
    ok = (
        exchange["request"]["method"] == "POST"
        and exchange["request"]["url"] == candidate["url"]
        and exchange["request"]["headers"] == candidate["request_headers"]
        and exchange["request"]["body"] == candidate["request_post_data"]
        and exchange["response"]["status"] == 200
        and exchange["response"]["headers"] == candidate["response_headers"]
        and exchange["response"]["body_excerpt"] == candidate["body_excerpt"]
    )
    return ok, f"exchange={exchange}"


def t_generation_prompt_uses_compiled_materials_without_noise():
    exchange = {
        "request": {
            "method": "POST",
            "url": "https://example.com/api/news",
            "headers": {"content-type": "application/json"},
            "body": '{"page":1}',
        },
        "response": {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body_excerpt": '{"items":[{"title":"Market decision"}]}',
            "body_truncated": False,
        },
    }
    dom_samples = [
        {
            "requested_url": "https://example.com/news/1",
            "body_excerpt": (
                '<main><article class="release">DOM-MARKER</article></main>'
            ),
            "aria_snapshot": "ARIA-NOISE",
        }
    ]
    captured = {}
    original_generate = generation._safe_generate
    original_provider = generation.GENERATION_PROVIDER

    def fake_generate(prompt, provider):
        captured["prompt"] = prompt
        captured["provider"] = provider
        return "print('candidate')", None

    try:
        generation.GENERATION_PROVIDER = "initial-tier"
        generation._safe_generate = fake_generate
        generation.generate_spider(
            {
                **_base_state(),
                "evidence_pack": {
                    "replay_exchange": exchange,
                    "dom_samples": dom_samples,
                    "request": {
                        "constraints": {"source_timezone": "Australia/Sydney"}
                    },
                    "published_at_probe": {
                        "source_timezone": "Australia/Sydney",
                        "needs_timezone_completion": True,
                    },
                    "requirements": ["browser_transport"],
                    "unresolved": [],
                    "entry_observation": {
                        "access_assessment": "browser_required_http_blocked",
                        "aria_snapshot": "ENTRY-ARIA",
                    },
                    "other": "supporting evidence",
                },
            }
        )
    finally:
        generation._safe_generate = original_generate
        generation.GENERATION_PROVIDER = original_provider

    prompt = captured["prompt"]
    ok = (
        captured["provider"] == "initial-tier"
        and '"url": "https://example.com/api/news"' in prompt
        and '"body_excerpt": "{\\"items\\":[{\\"title\\":\\"Market decision\\"}]}"'
        in prompt
        and "Australia/Sydney" in prompt
        and '"requirements": ["browser_transport"]' in prompt
        and "DOM-MARKER" in prompt
        and "ARIA-NOISE" not in prompt
        and "ENTRY-ARIA" not in prompt
        and "supporting evidence" not in prompt
        and "CLOSESPIDER_PAGECOUNT" in prompt
        and "zoneinfo.ZoneInfo" in prompt
        and "入口與明細的每一個必要 request" in prompt
        and "DOWNLOAD_HANDLERS" in prompt
        and "TWISTED_REACTOR" in prompt
        and len(prompt) < 20000
    )
    return ok, f"provider={captured['provider']} prompt_chars={len(prompt)}"


def t_generation_preflight_applies_only_safe_fix_and_rejects_missing_contract():
    candidate = '''
import scrapy
from zoneinfo import ZoneInfo

class ArticleItem(scrapy.Item):
    title = scrapy.Field()

class ExampleSpider(scrapy.Spider):
    name = "example_com"
    source = "example.com"
    source_type = "media"
    content_scope = "summary_only"
    custom_settings = {
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "ROBOTSTXT_OBEY": True,
        "CLOSESPIDER_PAGECOUNT": 0,  # zero still changes Scrapy behavior
    }

    def start_requests(self):
        yield scrapy.Request("https://example.com/news", meta={"playwright": True})

    def parse(self, response):
        ZoneInfo("Australia/Sydney")
        yield scrapy.Request("https://example.com/news/1", meta={"playwright": True})
'''
    state = {
        **_base_state(),
        "spider_code": candidate,
        "evidence_pack": {
            "requirements": ["browser_transport"],
            "published_at_probe": {
                "needs_timezone_completion": True,
                "source_timezone": "Australia/Sydney",
            },
        },
    }
    accepted = generation.preflight_generated_code(state)
    rejected = generation.preflight_generated_code(
        {**state, "spider_code": candidate.replace('    source = "example.com"\n', "")}
    )
    accepted_check = accepted["generation_preflight"]
    rejected_errors = rejected["generation_preflight"]["errors"]
    ok = (
        accepted_check["passed"] is True
        and accepted_check["deterministic_fixes"]
        and "ROBOTSTXT_OBEY" in accepted["spider_code"]
        and "CLOSESPIDER_PAGECOUNT" not in accepted["spider_code"]
        and "source_prefix = 'example_com'" in accepted["spider_code"]
        and rejected["generation_preflight"]["passed"] is False
        and "class_attribute_mismatch:source" in rejected_errors
    )
    return ok, (
        f"accepted={accepted_check} rejected_errors={rejected_errors}"
    )


def t_repair_functions_use_their_configured_provider_tiers():
    providers = []
    original_generate = repair._safe_generate
    original_repair = repair.REPAIR_PROVIDER
    original_final = repair.FINAL_REPAIR_PROVIDER

    def fake_generate(prompt, provider):
        providers.append(provider)
        return "print('repaired')", None

    state = {
        **_base_state(),
        "spider_code": "print('old')",
        "evidence_pack": {},
        "diagnosis": {},
        "test_result": {"passed": False},
        "validation_result": {"pass": False},
    }
    try:
        repair.REPAIR_PROVIDER = "repair-tier"
        repair.FINAL_REPAIR_PROVIDER = "final-tier"
        repair._safe_generate = fake_generate
        repair.repair_code(state)
        repair.repair_code_kimi(state)
    finally:
        repair._safe_generate = original_generate
        repair.REPAIR_PROVIDER = original_repair
        repair.FINAL_REPAIR_PROVIDER = original_final

    return providers == ["repair-tier", "final-tier"], f"providers={providers}"


def t_ledger_reports_first_pass_repairs_and_tokens():
    rows = [
        {
            "source_prefix": "one",
            "status": "success",
            "retry_count": 0,
            "repair_count": 0,
            "first_pass_success": True,
            "coder_tokens": 10,
        },
        {
            "source_prefix": "two",
            "status": "success",
            "retry_count": 2,
            "repair_count": 2,
            "first_pass_success": False,
            "coder_tokens": 20,
        },
        {
            "source_prefix": "three",
            "status": "error",
            "retry_count": None,
            "repair_count": None,
            "first_pass_success": False,
            "coder_tokens": 5,
        },
    ]
    original_log = ledger._LOG
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger._LOG = Path(temp_dir) / "runs.jsonl"
            ledger._LOG.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            summary = ledger.summarize()
    finally:
        ledger._LOG = original_log

    ok = (
        summary["success_rate"] == 0.6667
        and summary["first_try_success"] == 1
        and summary["first_pass_rate"] == 0.3333
        and summary["repairs_total"] == 2
        and summary["coder_tokens_total"] == 35
    )
    return ok, f"summary={summary}"


def t_batch_persists_replayable_evidence():
    original_run_dir = batch.run_dir
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            batch.run_dir = lambda run_id: root / run_id
            (root / "evidence-test").mkdir()
            payload = {"replay_exchange": {"request": {"method": "GET"}}}
            saved = batch._save_evidence("evidence-test", payload)
            loaded = json.loads(Path(saved).read_text(encoding="utf-8"))
    finally:
        batch.run_dir = original_run_dir

    return loaded == payload, f"saved={saved}"


def t_offline_pipeline_reaches_certified_output_without_repairs():
    calls = {"judge": 0, "coder": 0}
    original_recon = pipeline.recon
    original_preflight = pipeline.preflight_generated_code
    original_fixture = pipeline.fixture_test
    original_sandbox = pipeline.sandbox_test
    original_judge = judge.judge
    original_complete = coder.complete
    original_paths = {
        "_ACTIVE_DIR": artifacts._ACTIVE_DIR,
        "_CANDIDATE_DIR": artifacts._CANDIDATE_DIR,
        "_VERSION_DIR": artifacts._VERSION_DIR,
        "_PROMO_LOG": artifacts._PROMO_LOG,
    }
    candidate = {
        "method": "GET",
        "url": "https://example.com/api/news",
        "status": 200,
        "content_type": "application/json",
        "request_headers": {
            "accept": "application/json",
            "referer": "https://example.com/news",
        },
        "response_headers": {"content-type": "application/json"},
        "body_excerpt": (
            '{"items":[{"id":"n1","title":"Market decision",'
            '"published_at":"2026-07-29T09:00:00+08:00"}]}'
        ),
        "body_truncated": False,
        "article_record_count": 1,
        "json_shape": {"items": "list"},
    }

    def fake_recon(state):
        return {
            "recon_report": {
                "url": state["site_url"],
                "final_url": state["site_url"],
                "canonical_url": state["site_url"],
                "http_status": 200,
                "api_candidates": [candidate],
                "feed_candidates": [],
                "link_samples": [],
                "http_entry_sample": {
                    "status": 200,
                    "body_excerpt": "<html><body>news</body></html>",
                    "safe_request_headers": {"accept": "text/html"},
                },
                "access_assessment": "browser_public_ok",
            },
            "status": "reconning",
        }

    def fake_judge(**kwargs):
        calls["judge"] += 1
        return {
            "strategy": "api",
            "chosen_api": candidate["url"],
            "confidence": 0.99,
            "reason": "captured response",
        }

    def fake_complete(prompt, **kwargs):
        calls["coder"] += 1
        return (
            "```python\n"
            "import scrapy\n\n"
            "class ExampleSpider(scrapy.Spider):\n"
            "    name = 'example_com'\n"
            "    start_urls = ['https://example.com/api/news']\n"
            "```"
        )

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts._ACTIVE_DIR = root / "active"
            artifacts._CANDIDATE_DIR = root / "candidates"
            artifacts._VERSION_DIR = root / "versions"
            artifacts._PROMO_LOG = root / "promotions.jsonl"

            def fake_sandbox(state):
                run_id = state["run_id"]
                candidate_path = root / "candidates" / run_id / "example_com_spider.py"
                candidate_path.parent.mkdir(parents=True, exist_ok=True)
                candidate_path.write_text(state["spider_code"], encoding="utf-8")
                items_path = root / "runs" / run_id / "items.json"
                items_path.parent.mkdir(parents=True, exist_ok=True)
                items_path.write_text(
                    json.dumps(
                        [
                            {
                                "title": "Market policy decision",
                                "url": "https://example.com/news/1",
                                "content": (
                                    "This market policy report contains enough "
                                    "verified content for deterministic validation."
                                ),
                                "published_at": datetime.now(UTC).isoformat(),
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
                return {
                    "test_result": {
                        "passed": True,
                        "exit_code": 0,
                        "timed_out": False,
                        "item_count": 1,
                        "output_path": str(items_path),
                    },
                    "candidate_path": str(candidate_path),
                    "status": "validating",
                }

            def fake_preflight(state):
                return {
                    "generation_preflight": {
                        "passed": True,
                        "errors": [],
                        "deterministic_fixes": [],
                    },
                    "status": "testing",
                }

            def fake_fixture(state):
                return {
                    "fixture_result": {
                        "passed": True,
                        "errors": [],
                        "callback_errors": [],
                    },
                    "status": "testing",
                }

            pipeline.recon = fake_recon
            pipeline.preflight_generated_code = fake_preflight
            pipeline.fixture_test = fake_fixture
            pipeline.sandbox_test = fake_sandbox
            judge.judge = fake_judge
            coder.complete = fake_complete
            graph = pipeline.build_pipeline()
            result = graph.invoke(
                {
                    "site_url": "https://example.com/news",
                    "run_id": "offline-e2e",
                    "validation": {
                        "allowed_domains": ["example.com"],
                        "min_valid_items": 1,
                        "max_age_days": 30,
                    },
                    "topic_gate": {"mode": "off"},
                },
                config={"configurable": {"thread_id": "offline-e2e"}},
            )
            active_exists = Path(result.get("spider_path", "")).is_file()
    finally:
        pipeline.recon = original_recon
        pipeline.preflight_generated_code = original_preflight
        pipeline.fixture_test = original_fixture
        pipeline.sandbox_test = original_sandbox
        judge.judge = original_judge
        coder.complete = original_complete
        for name, value in original_paths.items():
            setattr(artifacts, name, value)

    ok = (
        result["status"] == "success"
        and result["retry_count"] == 0
        and result["validation_result"]["pass"] is True
        and active_exists
        and calls == {"judge": 1, "coder": 1}
    )
    return ok, (
        f"status={result['status']} retry={result['retry_count']} "
        f"calls={calls} active={active_exists}"
    )


TESTS = [
    t_recon_does_not_fetch_robots,
    t_kill_path_makes_zero_judge_and_coder_calls,
    t_policy_failure_stops_before_second_repair,
    t_request_evidence_redacts_secrets,
    t_evidence_pack_keeps_exact_replay_exchange,
    t_generation_prompt_uses_compiled_materials_without_noise,
    t_generation_preflight_applies_only_safe_fix_and_rejects_missing_contract,
    t_repair_functions_use_their_configured_provider_tiers,
    t_ledger_reports_first_pass_repairs_and_tokens,
    t_batch_persists_replayable_evidence,
    t_offline_pipeline_reaches_certified_output_without_repairs,
]


def main() -> int:
    failed = 0
    for check in TESTS:
        try:
            ok, detail = check()
        except Exception as exc:
            ok, detail = False, f"EXCEPTION {exc}"
        print(f"[{'PASS' if ok else 'FAIL'}] {check.__name__}: {detail}")
        failed += not ok
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
