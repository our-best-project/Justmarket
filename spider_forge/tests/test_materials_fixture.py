"""生成材料精簡與正式離線樣本閘門測試。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from spider_forge import pipeline
from spider_forge.shared import repair
from spider_forge.shared.fixture import build_fixture_spec
from spider_forge.shared.materials import (
    compile_generation_materials,
)
from spider_forge.shared.sandbox import run_fixture_candidate


def t_material_compiler_removes_dom_noise_and_unselected_sources():
    pack = {
        "replay_exchange": {
            "request": {"url": "https://example.com/feed/selected"},
            "response": {
                "content_type": "application/rss+xml",
                "body_excerpt": "<rss>" + ("selected " * 2000) + "</rss>",
            },
        },
        "api_sample": {
            "url": "https://example.com/feed/selected",
            "structured_format": "rss_or_atom",
        },
        "api_context_samples": [
            {
                "url": "https://example.com/feed/unselected",
                "body_excerpt": "UNSELECTED-SOURCE",
            }
        ],
        "dom_samples": [
            {
                "requested_url": "https://example.com/news/1",
                "body_excerpt": (
                    "<html><head><style>STYLE-NOISE</style>"
                    "<script>SCRIPT-NOISE</script></head><body>"
                    '<article class="story" data-tracking="REMOVE-ME">'
                    "<h1>Article title</h1><p>Useful body</p>"
                    "</article></body></html>"
                ),
            }
        ],
    }
    materials = compile_generation_materials(pack)
    serialized = json.dumps(materials, ensure_ascii=False)
    replay_body = materials["replay_exchange"]["response"][
        "body_excerpt"
    ]
    ok = (
        "SCRIPT-NOISE" not in serialized
        and "STYLE-NOISE" not in serialized
        and "REMOVE-ME" not in serialized
        and "UNSELECTED-SOURCE" not in serialized
        and "Article title" in serialized
        and 'class=\\"story\\"' in serialized
        and len(replay_body) <= 4500
        and materials["material_budget"][
            "unselected_feed_candidates_included"
        ]
        == 0
    )
    return ok, (
        f"chars={materials['material_budget']['serialized_chars']} "
        f"replay={len(replay_body)}"
    )


_GOOD_CANDIDATE = '''
import scrapy

class ArticleItem(scrapy.Item):
    title = scrapy.Field()
    url = scrapy.Field()
    content = scrapy.Field()
    published_at = scrapy.Field()
    source_record_id = scrapy.Field()

class ExampleSpider(scrapy.Spider):
    name = "example_com"
    source_prefix = "example_com"
    source = "Example"
    source_type = "media"
    content_scope = "summary_only"
    allowed_domains = ["example.com"]

    def start_requests(self):
        yield scrapy.Request("https://example.com/news", callback=self.parse)

    def parse(self, response):
        for row in response.css("li.story"):
            href = row.css("a::attr(href)").get()
            published = row.css("time::attr(datetime)").get()
            yield response.follow(
                href,
                callback=self.parse_detail,
                meta={"published_at": published},
            )

    def parse_detail(self, response):
        item = ArticleItem()
        item["title"] = response.css("h1::text").get()
        item["url"] = response.url
        item["content"] = " ".join(response.css("div.body p::text").getall())
        item["published_at"] = response.meta["published_at"]
        item["source_record_id"] = response.url.rsplit("/", 1)[-1]
        yield item
'''


def _fixture() -> dict:
    return {
        "listing": {
            "url": "https://example.com/news",
            "body": (
                "<ul>"
                '<li class="story"><a href="/news/one">One</a>'
                '<time datetime="2026-07-29T09:00:00+00:00"></time></li>'
                '<li class="story"><a href="/news/two">Two</a>'
                '<time datetime="2026-07-29T10:00:00+00:00"></time></li>'
                "</ul>"
            ),
            "content_type": "text/html",
        },
        "detail_samples": [
            {
                "requested_url": "https://example.com/news/one",
                "body_excerpt": (
                    "<main><h1>First policy update</h1><div class=\"body\">"
                    "<p>The central bank published a detailed market policy "
                    "update with verified figures and implementation dates.</p>"
                    "</div></main>"
                ),
            },
            {
                "requested_url": "https://example.com/news/two",
                "body_excerpt": (
                    "<main><h1>Second policy update</h1><div class=\"body\">"
                    "<p>The authority released another detailed financial "
                    "policy report with sufficient factual article content.</p>"
                    "</div></main>"
                ),
            },
        ],
        "browser_required": False,
        "min_listing_outputs": 2,
        "min_content_chars": 40,
        "expected_attributes": {
            "name": "example_com",
            "source_prefix": "example_com",
            "source": "Example",
            "source_type": "media",
            "content_scope": "summary_only",
        },
    }


def t_fixture_runner_executes_candidate_in_crawler_subprocess():
    with tempfile.TemporaryDirectory() as temp_dir:
        candidate = Path(temp_dir) / "candidate.py"
        candidate.write_text(_GOOD_CANDIDATE, encoding="utf-8")
        execution = run_fixture_candidate(
            str(candidate),
            _fixture(),
            allowed_domains=["example.com"],
        )
    result = json.loads(execution.stdout)
    ok = (
        execution.ok
        and result["passed"] is True
        and result["detail_request_count"] == 2
        and result["parsed_item_count"] == 2
    )
    return ok, f"exit={execution.exit_code} result={result}"


def t_pure_api_fixture_does_not_require_html_detail_callbacks():
    fixture = build_fixture_spec(
        {
            "site_url": "https://example.com/api/news",
            "site_name": "Example",
            "source_prefix": "example_com",
            "strategy": "api",
            "target_schema": {
                "source_type": "media",
                "content_scope": "summary_only",
            },
            "validation": {"min_valid_items": 5},
            "evidence_pack": {
                "strategy": {"strategy": "api"},
                "api_sample": {"article_record_count": 8},
                "replay_exchange": {
                    "request": {
                        "url": "https://example.com/api/news"
                    },
                    "response": {
                        "content_type": "application/json",
                        "body_excerpt": '{"items":[]}',
                    },
                },
                "dom_samples": [
                    {
                        "requested_url": "https://example.com/news/1",
                        "body_excerpt": "<article>unused</article>",
                    }
                ],
            },
        }
    )
    ok = (
        fixture["detail_samples"] == []
        and fixture["min_listing_outputs"] == 5
    )
    return ok, (
        f"details={len(fixture['detail_samples'])} "
        f"minimum={fixture['min_listing_outputs']}"
    )


def t_fixture_failure_is_diagnosed_without_judge_call():
    result = repair.diagnose_failure(
        {
            "generation_preflight": {"passed": True, "errors": []},
            "fixture_result": {
                "passed": False,
                "errors": ["missing_detail_request:https://example.com/one"],
                "callback_errors": [],
                "fixture_source": "browser_dom",
            },
            "retry_count": 0,
            "error_signature_history": [],
        }
    )
    ok = (
        result["failure_class"] == "selector_schema"
        and result["retry_count"] == 1
        and result["diagnosis"]["error_signature"]
        == "fixture_gate_failed"
    )
    return ok, f"diagnosis={result['diagnosis']}"


def t_graph_routes_preflight_and_fixture_before_live_crawl():
    preflight_pass = pipeline.route_after_preflight(
        {"generation_preflight": {"passed": True}}
    )
    preflight_fail = pipeline.route_after_preflight(
        {"generation_preflight": {"passed": False}}
    )
    fixture_pass = pipeline.route_after_fixture(
        {"fixture_result": {"passed": True}}
    )
    fixture_fail = pipeline.route_after_fixture(
        {"fixture_result": {"passed": False}}
    )
    ok = (
        preflight_pass,
        preflight_fail,
        fixture_pass,
        fixture_fail,
    ) == (
        "fixture_test",
        "diagnose_failure",
        "sandbox_test",
        "diagnose_failure",
    )
    return ok, (
        f"routes={(preflight_pass, preflight_fail, fixture_pass, fixture_fail)}"
    )


TESTS = [
    t_material_compiler_removes_dom_noise_and_unselected_sources,
    t_fixture_runner_executes_candidate_in_crawler_subprocess,
    t_pure_api_fixture_does_not_require_html_detail_callbacks,
    t_fixture_failure_is_diagnosed_without_judge_call,
    t_graph_routes_preflight_and_fixture_before_live_crawl,
]
