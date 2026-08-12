"""Spider Forge v1 graph 的離線狀態轉移與安全契約測試。"""

from __future__ import annotations

from spider_forge import pipeline as graph
from spider_forge.shared import evidence as evidence_stage
from spider_forge.shared.prompts import DEFAULT_TARGET_SCHEMA


def t_minimal_request_gets_operational_defaults():
    result = graph.prepare_request({"site_url": "https://policy.example.com/news"})
    ok = (
        result["site_name"] == "policy.example.com"
        and result["source_prefix"] == "policy_example_com"
        and result["target_schema"]["fields"]["published_at"]["type"] == "iso8601_tz"
        and result["validation"]["allowed_domains"] == ["policy.example.com"]
        and result["validation"]["max_content_chars"] == 6000
        and result["topic_gate"]["mode"] == "enforce"
        and result["topic_gate"]["provider"] == "gemini"
        and result["max_retries"] == 2
        and result["constraints"]["max_pages"] == 2
    )
    return ok, (
        f"prefix={result['source_prefix']} retries={result['max_retries']} "
        f"domains={result['validation']['allowed_domains']}"
    )


def t_retry_budget_is_hard_capped_at_two():
    result = graph.prepare_request(
        {"site_url": "https://example.com/news", "max_retries": 99}
    )
    return result["max_retries"] == 2, f"max_retries={result['max_retries']}"


def t_browser_session_requires_opaque_context_ref():
    try:
        graph.prepare_request(
            {"site_url": "https://example.com/private", "access_mode": "browser_session"}
        )
        raised = False
    except ValueError:
        raised = True
    return raised, f"raised={raised}"


def t_prompt_safe_request_excludes_access_secret():
    prepared = graph.prepare_request(
        {
            "site_url": "https://example.com/private",
            "access_mode": "browser_session",
            "access_context_ref": "C:/secret/browser-state.json",
        }
    )
    state = {
        **prepared,
        "access_context_ref": "C:/secret/browser-state.json",
    }
    prompt_request = evidence_stage._prompt_safe_request(state)
    ok = (
        "access_context_ref" not in prompt_request
        and "access_mode" not in prompt_request
        and prompt_request["validation"]["allowed_domains"] == ["example.com"]
    )
    return ok, f"keys={sorted(prompt_request)}"


def t_recon_redacts_access_context_ref_from_errors():
    from spider_forge.clients import browser as browser_probe

    secret_path = "C:/secret/browser-state.json"
    original_probe = browser_probe.probe
    original_fetch = evidence_stage._fetch_sample

    def fail_probe(url, **kwargs):
        raise RuntimeError(f"cannot parse {secret_path}")

    browser_probe.probe = fail_probe
    evidence_stage._fetch_sample = lambda url, **kwargs: {
        "requested_url": url,
        "fetch_error": "offline test",
    }
    try:
        result = graph.recon(
            {
                "site_url": "https://example.com/private",
                "access_mode": "browser_session",
                "access_context_ref": secret_path,
            }
        )
    finally:
        browser_probe.probe = original_probe
        evidence_stage._fetch_sample = original_fetch
    error = result["recon_report"]["recon_error"]
    ok = secret_path not in error and "<access_context_ref>" in error
    return ok, f"error={error}"


def t_evidence_pack_is_internal_and_concrete():
    original = evidence_stage._fetch_sample
    evidence_stage._fetch_sample = lambda url, **_: {
        "requested_url": url,
        "status": 200,
        "content_type": "application/json",
        "body_excerpt": '{"items":[{"title":"x"}]}',
    }
    try:
        result = evidence_stage.collect_evidence(
            {
                "site_url": "https://example.com/news",
                "site_name": "Example",
                "source_prefix": "example",
                "target_schema": DEFAULT_TARGET_SCHEMA,
                "sample_urls": ["https://example.com/news/1"],
                "access_mode": "public",
                "constraints": {"max_pages": 2},
                "strategy_detail": {
                    "strategy": "api",
                    "chosen_api": "https://example.com/api/news",
                },
                "recon_report": {
                    "http_status": 200,
                    "title": "Example",
                    "aria_snapshot": "- link x",
                    "api_candidates": [
                        {
                            "method": "GET",
                            "url": "https://example.com/api/news",
                            "content_type": "application/json",
                        }
                    ],
                },
            }
        )
    finally:
        evidence_stage._fetch_sample = original
    pack = result["evidence_pack"]
    ok = (
        pack["origin"] == "live_recon"
        and pack["api_sample"]["status"] == 200
        and len(pack["detail_samples"]) == 1
        and "no_api_body_sample" not in pack["unresolved"]
    )
    return ok, f"origin={pack['origin']} unresolved={pack['unresolved']}"


def t_recon_keeps_plain_http_path_when_browser_is_blocked():
    from spider_forge.clients import browser as browser_probe

    original_probe = browser_probe.probe
    original_fetch = evidence_stage._fetch_sample
    browser_probe.probe = lambda url, **kwargs: {
        "url": url,
        "final_url": url,
        "canonical_url": url,
        "http_status": 403,
        "soft_block_detected": True,
        "api_candidates": [],
        "link_samples": [],
    }
    evidence_stage._fetch_sample = lambda url, **kwargs: {
        "requested_url": url,
        "final_url": "https://example.com/news/",
        "canonical_url": "https://example.com/news/",
        "status": 200,
        "safe_request_headers": {"User-Agent": "test"},
        "body_excerpt": "<html>news</html>",
        "link_samples": [{"url": "https://example.com/news/1", "text": "Article"}],
    }
    try:
        result = graph.recon(
            {"site_url": "https://example.com/news", "access_mode": "public"}
        )
    finally:
        browser_probe.probe = original_probe
        evidence_stage._fetch_sample = original_fetch
    report = result["recon_report"]
    ok = (
        report["access_assessment"] == "browser_blocked_http_ok"
        and report["http_entry_sample"]["status"] == 200
        and report["canonical_url"] == "https://example.com/news/"
    )
    return ok, (
        f"access={report['access_assessment']} "
        f"http={report['http_entry_sample'].get('status')}"
    )


def t_recon_marks_browser_required_when_plain_http_is_blocked():
    from spider_forge.clients import browser as browser_probe

    original_probe = browser_probe.probe
    original_fetch = evidence_stage._fetch_sample
    browser_probe.probe = lambda url, **kwargs: {
        "url": url,
        "final_url": url,
        "canonical_url": url,
        "http_status": 200,
        "soft_block_detected": False,
        "api_candidates": [],
        "link_samples": [
            {"url": "https://example.com/news/1", "text": "Article"}
        ],
    }
    evidence_stage._fetch_sample = lambda url, **kwargs: {
        "requested_url": url,
        "final_url": url,
        "canonical_url": url,
        "status": 403,
        "body_excerpt": "Access Denied",
        "link_samples": [],
    }
    try:
        result = graph.recon(
            {"site_url": "https://example.com/news", "access_mode": "public"}
        )
    finally:
        browser_probe.probe = original_probe
        evidence_stage._fetch_sample = original_fetch
    report = result["recon_report"]
    ok = (
        report["access_assessment"] == "browser_required_http_blocked"
        and report["http_status"] == 200
        and report["http_entry_sample"]["status"] == 403
    )
    return ok, (
        f"access={report['access_assessment']} "
        f"browser={report['http_status']} "
        f"http={report['http_entry_sample'].get('status')}"
    )


def t_evidence_pack_discovers_details_and_reuses_browser_api_body():
    original_fetch = evidence_stage._fetch_sample
    fetched: list[str] = []

    def fake_fetch(url, **kwargs):
        fetched.append(url)
        return {
            "requested_url": url,
            "final_url": url,
            "status": 200,
            "body_excerpt": "<article>full text</article>",
        }

    evidence_stage._fetch_sample = fake_fetch
    try:
        result = evidence_stage.collect_evidence(
            {
                "site_url": "https://example.com/news",
                "site_name": "Example",
                "source_prefix": "example",
                "target_schema": DEFAULT_TARGET_SCHEMA,
                "sample_urls": [],
                "access_mode": "public",
                "constraints": {"max_pages": 2},
                "validation": {
                    "allowed_domains": ["example.com"],
                    "article_url_patterns": [r"/news/\d+$"],
                    "excluded_url_patterns": [],
                },
                "strategy_detail": {
                    "strategy": "api",
                    "chosen_api": "https://example.com/api/news",
                },
                "recon_report": {
                    "http_status": 200,
                    "api_candidates": [
                        {
                            "method": "GET",
                            "url": "https://example.com/api/news",
                            "body_excerpt": '{"items":[{"title":"x","url":"/news/1"}]}',
                            "json_shape": "object keys=items",
                            "article_record_count": 1,
                        }
                    ],
                    "http_entry_sample": {
                        "status": 200,
                        "link_samples": [
                            {"url": "https://example.com/about", "text": "About"},
                            {"url": "https://example.com/news/1", "text": "One"},
                            {"url": "https://example.com/news/2", "text": "Two"},
                        ],
                    },
                },
            }
        )
    finally:
        evidence_stage._fetch_sample = original_fetch
    pack = result["evidence_pack"]
    ok = (
        pack["api_sample"]["capture_source"] == "browser_network"
        and pack["discovered_detail_urls"]
        == ["https://example.com/news/1", "https://example.com/news/2"]
        and fetched == pack["discovered_detail_urls"]
        and "no_api_body_sample" not in pack["unresolved"]
    )
    return ok, (
        f"details={pack['discovered_detail_urls']} "
        f"api_source={pack['api_sample'].get('capture_source')}"
    )


def t_evidence_uses_browser_dom_when_plain_http_is_blocked():
    original_browser_fetch = evidence_stage._fetch_browser_sample
    original_http_fetch = evidence_stage._fetch_sample
    browser_calls: list[str] = []

    def fake_browser_fetch(url):
        browser_calls.append(url)
        return {
            "requested_url": url,
            "final_url": url,
            "canonical_url": url,
            "status": 200,
            "capture_source": "public_browser",
            "body_excerpt": (
                '<main><article class="release">'
                '<time datetime="2026-07-08T10:00:00+10:00"></time>'
                '<div class="content">Full policy text</div>'
                "</article></main>"
            ),
            "body_truncated": False,
            "text_excerpt": "Full policy text",
            "aria_snapshot": "- article: Full policy text",
            "soft_block_detected": False,
        }

    evidence_stage._fetch_browser_sample = fake_browser_fetch
    evidence_stage._fetch_sample = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("browser-required 明細不得改用 plain HTTP")
    )
    detail_url = "https://example.com/news/1"
    try:
        result = evidence_stage.collect_evidence(
            {
                "site_url": "https://example.com/news",
                "site_name": "Example",
                "source_prefix": "example",
                "target_schema": DEFAULT_TARGET_SCHEMA,
                "sample_urls": [],
                "access_mode": "public",
                "constraints": {"max_pages": 2},
                "validation": {
                    "allowed_domains": ["example.com"],
                    "article_url_patterns": [r"/news/\d+$"],
                },
                "strategy": "dom",
                "strategy_detail": {
                    "strategy": "dom",
                    "chosen_api": "",
                },
                "recon_report": {
                    "http_status": 200,
                    "access_assessment": "browser_required_http_blocked",
                    "api_candidates": [],
                    "feed_candidates": [],
                    "link_samples": [
                        {"url": detail_url, "text": "Article"}
                    ],
                    "http_entry_sample": {
                        "status": 403,
                        "body_excerpt": "Access Denied",
                        "link_samples": [],
                    },
                },
            }
        )
    finally:
        evidence_stage._fetch_browser_sample = original_browser_fetch
        evidence_stage._fetch_sample = original_http_fetch
    pack = result["evidence_pack"]
    dom = pack["dom_samples"][0]
    blocked_is_usable = evidence_stage._usable_detail_sample(
        {"status": 403, "body_excerpt": "Access Denied"}
    )
    ok = (
        browser_calls == [detail_url]
        and dom["capture_source"] == "public_browser"
        and 'class="content"' in dom["body_excerpt"]
        and "no_detail_example" not in pack["unresolved"]
        and pack["requirements"] == ["browser_transport"]
        and "browser_transport_required" not in pack["unresolved"]
        and pack["replay_headers"]["entry"] is None
        and blocked_is_usable is False
    )
    return ok, (
        f"browser_calls={browser_calls} dom={len(dom['body_excerpt'])} "
        f"unresolved={pack['unresolved']}"
    )


def t_strategy_rejects_api_without_replayable_article_evidence():
    from spider_forge.clients import judge as judge_client

    original = judge_client.judge
    judge_client.judge = lambda **kwargs: {
        "strategy": "api",
        "confidence": 0.95,
        "reason": "URL resembles an API",
        "chosen_api": "https://example.com/api/navigation",
    }
    try:
        result = graph.strategy_decision(
            {
                "site_url": "https://example.com/news",
                "target_schema": DEFAULT_TARGET_SCHEMA,
                "recon_report": {
                    "api_candidates": [
                        {
                            "method": "GET",
                            "url": "https://example.com/api/navigation",
                            "body_excerpt": '{"menu":[]}',
                            "article_record_count": 0,
                        }
                    ],
                    "http_entry_sample": {"status": 200, "link_samples": []},
                },
            }
        )
    finally:
        judge_client.judge = original
    detail = result["strategy_detail"]
    ok = (
        result["strategy"] == "dom"
        and detail["chosen_api"] == ""
        and detail["evidence_enforced"] is True
    )
    return ok, f"strategy={result['strategy']} enforced={detail.get('evidence_enforced')}"


def t_strategy_prefers_replayable_structured_evidence_over_qwen_guess():
    from spider_forge.clients import judge as judge_client

    original = judge_client.judge
    judge_client.judge = lambda **kwargs: {
        "strategy": "dom",
        "confidence": 85,
        "reason": "HTML looks usable",
        "chosen_api": "",
    }
    feed_url = "https://example.com/rss/news"
    try:
        result = graph.strategy_decision(
            {
                "site_url": "https://example.com/news",
                "target_schema": DEFAULT_TARGET_SCHEMA,
                "validation": {},
                "recon_report": {
                    "api_candidates": [],
                    "feed_candidates": [
                        {
                            "method": "GET",
                            "url": feed_url,
                            "body_excerpt": "<rss>...</rss>",
                            "article_record_count": 20,
                            "structured_format": "rss_or_atom",
                        }
                    ],
                    "http_entry_sample": {"status": 200, "link_samples": []},
                },
            }
        )
    finally:
        judge_client.judge = original
    detail = result["strategy_detail"]
    ok = (
        result["strategy"] == "hybrid"
        and detail["chosen_api"] == feed_url
        and detail["confidence"] == 0.9
        and detail["evidence_enforced"] is True
    )
    return ok, f"strategy={result['strategy']} api={detail.get('chosen_api')}"


def t_strategy_uses_best_matching_feed_and_fetches_full_detail():
    from spider_forge.clients import judge as judge_client

    correct = "https://example.com/feed/?content_type=press-releases"
    unrelated = "https://example.com/feed/?content_type=regulatory-news"
    original = judge_client.judge
    judge_client.judge = lambda **kwargs: {
        "strategy": "api",
        "confidence": 0.85,
        "reason": "RSS is sufficient",
        "chosen_api": unrelated,
    }
    try:
        result = graph.strategy_decision(
            {
                "site_url": "https://example.com/press/press-releases/",
                "target_schema": {
                    "fields": {
                        "content": {
                            "type": "string",
                            "required": True,
                            "mode": "full",
                        }
                    },
                    "content_scope": "full",
                },
                "validation": {},
                "recon_report": {
                    "api_candidates": [],
                    "feed_candidates": [
                        {
                            "method": "GET",
                            "url": unrelated,
                            "body_excerpt": "<rss>...</rss>",
                            "article_record_count": 10,
                            "structured_format": "rss_or_atom",
                            "entry_link_overlap_count": 1,
                        },
                        {
                            "method": "GET",
                            "url": correct,
                            "body_excerpt": "<rss>...</rss>",
                            "article_record_count": 10,
                            "structured_format": "rss_or_atom",
                            "entry_link_overlap_count": 10,
                        },
                    ],
                    "http_entry_sample": {"status": 200, "link_samples": []},
                },
            }
        )
    finally:
        judge_client.judge = original
    detail = result["strategy_detail"]
    ok = (
        result["strategy"] == "hybrid"
        and detail["chosen_api"] == correct
        and detail["evidence_enforced"] is True
    )
    return ok, (
        f"strategy={result['strategy']} api={detail.get('chosen_api')} "
        f"enforced={detail.get('evidence_enforced')}"
    )


def t_strategy_uses_deterministic_html_when_links_prove_only_route():
    from spider_forge.clients import judge as judge_client

    original = judge_client.judge
    judge_client.judge = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("確定性 HTML 路徑不應呼叫 judge")
    )
    try:
        result = graph.strategy_decision(
            {
                "site_url": "https://example.com/news",
                "target_schema": DEFAULT_TARGET_SCHEMA,
                "validation": {
                    "allowed_domains": ["example.com"],
                    "article_url_patterns": [r"/news/\d+$"],
                },
                "recon_report": {
                    "access_assessment": "browser_required_http_blocked",
                    "api_candidates": [],
                    "feed_candidates": [],
                    "link_samples": [
                        {
                            "url": "https://example.com/news/1",
                            "text": "Article",
                        }
                    ],
                    "http_entry_sample": {
                        "status": 403,
                        "link_samples": [],
                    },
                },
            }
        )
    finally:
        judge_client.judge = original
    detail = result["strategy_detail"]
    ok = (
        result["strategy"] == "dom"
        and detail["chosen_api"] == ""
        and detail["confidence"] == 1.0
        and detail["decision_method"] == "deterministic"
    )
    return ok, (
        f"strategy={result['strategy']} chosen_api={detail['chosen_api']!r} "
        f"method={detail.get('decision_method')}"
    )


def t_recon_redacts_secrets_and_counts_article_json():
    from spider_forge.clients import browser as browser_probe

    safe = browser_probe._safe_post_data(
        '{"query":"news","csrfToken":"do-not-leak","nested":{"api_key":"secret"}}'
    )
    evidence = browser_probe._json_evidence(
        '{"items":[{"title":"Policy decision","url":"/news/1","published_at":"2026-01-01"}]}'
    )
    flat = browser_probe._json_evidence(
        '[42,1785146400,["/press/pr/date/2026/html/item.en.html"],'
        '{"Title":"Policy decision"}]'
    )
    index = browser_probe._json_evidence(
        '{"title":["0.0.1"],"date":["0.0.2"],"url":["0.0.3"],'
        + ",".join(f'"token{i}":["0"]' for i in range(100))
        + "}"
    )
    ok = (
        "do-not-leak" not in safe
        and "secret" not in safe
        and safe.count("<redacted>") == 2
        and evidence["article_record_count"] == 1
        and flat["article_record_count"] == 1
        and flat["record_detection"] == "flat_dataset_proxy"
        and index["article_record_count"] == 0
    )
    return ok, (
        f"redacted={safe.count('<redacted>')} "
        f"records={evidence['article_record_count']} "
        f"flat={flat['article_record_count']} index={index['article_record_count']}"
    )


def t_rss_is_structured_evidence_with_real_article_links():
    items = evidence_stage._feed_evidence(
        """<rss><channel><item>
        <guid>abc</guid><link>https://example.com/news/2026/one</link>
        <title>Rate decision</title>
        <description>Policy rate was unchanged.</description>
        <pubDate>Mon, 20 Jul 2026 10:00:00 +0100</pubDate>
        </item></channel></rss>"""
    )
    candidate = {
        "method": "GET",
        "url": "https://example.com/rss/news",
        "body_excerpt": "<rss>...</rss>",
        "structured_format": "rss_or_atom",
        "article_record_count": len(items),
    }
    ok = (
        len(items) == 1
        and items[0]["url"] == "https://example.com/news/2026/one"
        and items[0]["published_at"] == "Mon, 20 Jul 2026 10:00:00 +0100"
        and evidence_stage._is_replayable_article_api(candidate)
    )
    return ok, (
        f"items={len(items)} "
        f"replayable={evidence_stage._is_replayable_article_api(candidate)}"
    )


def t_document_declared_feed_precedes_unrelated_anchor_feed():
    correct = "https://example.com/feed/?content_type=press-releases"
    unrelated = "https://example.com/feed/?content_type=regulatory-news"
    _, _, declared = evidence_stage._html_evidence(
        (
            '<link rel="alternate" type="application/rss+xml" '
            f'href="{unrelated}" title="Regulatory news">'
            '<link rel="alternate" type="application/rss+xml" '
            f'href="{correct}" title="Press releases">'
            '<a href="https://example.com/2026/one/">Current press release</a>'
        ),
        "https://example.com/press/press-releases/",
    )
    original_fetch = evidence_stage._fetch_sample

    def fake_fetch(url, **kwargs):
        article_url = (
            "https://example.com/2026/one/"
            if url == correct
            else "https://example.com/2026/regulatory/"
        )
        return {
            "requested_url": url,
            "final_url": url,
            "status": 200,
            "feed_items": [{"title": url, "url": article_url}],
            "article_record_count": 1,
        }

    evidence_stage._fetch_sample = fake_fetch
    try:
        candidates = evidence_stage._discover_feed_candidates(
            {
                "final_url": "https://example.com/press/press-releases/",
                "declared_feed_links": declared,
                "link_samples": [
                    {
                        "url": "https://example.com/2026/one/",
                        "text": "Current press release",
                    }
                ],
            }
        )
    finally:
        evidence_stage._fetch_sample = original_fetch
    ok = (
        [row["url"] for row in candidates] == [correct, unrelated]
        and candidates[0]["discovery_source"] == "document_alternate"
        and candidates[1]["discovery_source"] == "document_alternate"
        and candidates[0]["entry_link_overlap_count"] == 1
        and candidates[1]["entry_link_overlap_count"] == 0
    )
    return ok, (
        f"order={[row['url'] for row in candidates]} "
        f"overlap={[row['entry_link_overlap_count'] for row in candidates]}"
    )


def t_detail_discovery_starts_from_chosen_feed():
    chosen = "https://example.com/feed/press"
    urls = evidence_stage._discover_detail_urls(
        {
            "site_url": "https://example.com/press/",
            "strategy_detail": {"chosen_api": chosen},
            "validation": {
                "allowed_domains": ["example.com"],
                "article_url_patterns": [r"/2026/"],
                "excluded_url_patterns": [],
            },
        },
        {
            "link_samples": [
                {"url": "https://example.com/2026/navigation-story/", "text": "Other"}
            ],
            "feed_candidates": [
                {
                    "url": "https://example.com/feed/other",
                    "feed_items": [
                        {"url": "https://example.com/2026/unrelated/", "title": "Wrong"}
                    ],
                },
                {
                    "url": chosen,
                    "feed_items": [
                        {"url": "https://example.com/2026/press-one/", "title": "One"},
                        {"url": "https://example.com/2026/press-two/", "title": "Two"},
                    ],
                },
            ],
            "http_entry_sample": {"link_samples": []},
        },
    )
    expected = [
        "https://example.com/2026/press-one/",
        "https://example.com/2026/press-two/",
    ]
    return urls == expected, f"urls={urls}"


def t_two_repairs_then_escalate():
    single_budget = graph.route_after_diagnose({"retry_count": 1, "max_retries": 1})
    first = graph.route_after_diagnose({"retry_count": 1, "max_retries": 2})
    second = graph.route_after_diagnose({"retry_count": 2, "max_retries": 2})
    exhausted = graph.route_after_diagnose(
        {"retry_count": 3, "max_retries": 2, "kimi_used": True}
    )
    ok = (single_budget, first, second, exhausted) == (
        "repair_code",
        "repair_code",
        "repair_code_kimi",
        "escalate_human",
    )
    return ok, f"routes={(single_budget, first, second, exhausted)}"


def t_known_scrapy_api_error_is_diagnosed_without_llm():
    from spider_forge.clients import judge as judge_client

    original = judge_client.judge
    judge_client.judge = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("judge must not be called")
    )
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
    ok = (
        result["diagnosis"]["error_signature"]
        == "scrapy_selectorlist_first_invalid"
        and result["retry_count"] == 1
    )
    return ok, f"diagnosis={result['diagnosis']['error_signature']}"


def t_topic_provider_outage_skips_code_repair():
    route = graph.route_after_validate(
        {
            "strategy": "api",
            "validation_result": {"pass": False},
            "topic_result": {
                "mode": "enforce",
                "provider": "gemini",
                "status": "gemini_unavailable",
            },
        }
    )
    return route == "escalate_human", f"route={route}"


def t_graph_has_short_loop_topology():
    compiled = graph.build_pipeline()
    nodes = set(compiled.get_graph().nodes)
    required = {
        "prepare_request",
        "recon",
        "collect_evidence",
        "generate_spider",
        "generation_preflight",
        "fixture_test",
        "sandbox_test",
        "validate_output",
        "topic_gate",
        "repair_code",
        "repair_code_kimi",
        "persist_spider",
    }
    removed_dead_nodes = {"generate_api", "generate_axtree", "heuristic_repair"}
    ok = required <= nodes and not (removed_dead_nodes & nodes)
    return ok, f"nodes={sorted(nodes)}"


TESTS = [
    t_minimal_request_gets_operational_defaults,
    t_retry_budget_is_hard_capped_at_two,
    t_browser_session_requires_opaque_context_ref,
    t_prompt_safe_request_excludes_access_secret,
    t_recon_redacts_access_context_ref_from_errors,
    t_evidence_pack_is_internal_and_concrete,
    t_recon_keeps_plain_http_path_when_browser_is_blocked,
    t_recon_marks_browser_required_when_plain_http_is_blocked,
    t_evidence_pack_discovers_details_and_reuses_browser_api_body,
    t_evidence_uses_browser_dom_when_plain_http_is_blocked,
    t_strategy_rejects_api_without_replayable_article_evidence,
    t_strategy_prefers_replayable_structured_evidence_over_qwen_guess,
    t_strategy_uses_best_matching_feed_and_fetches_full_detail,
    t_strategy_uses_deterministic_html_when_links_prove_only_route,
    t_recon_redacts_secrets_and_counts_article_json,
    t_rss_is_structured_evidence_with_real_article_links,
    t_document_declared_feed_precedes_unrelated_anchor_feed,
    t_detail_discovery_starts_from_chosen_feed,
    t_two_repairs_then_escalate,
    t_known_scrapy_api_error_is_diagnosed_without_llm,
    t_topic_provider_outage_skips_code_repair,
    t_graph_has_short_loop_topology,
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
