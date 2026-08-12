"""在隔離程序內以保存的 response fixture 執行未知候選 callback。

stdin 接收 JSON bundle，stdout 只輸出 JSON 結果。此模組不連網、不讀控制層模組。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import scrapy
from scrapy.http import HtmlResponse, XmlResponse


def _load_spider(code: str, folder: Path) -> type[scrapy.Spider]:
    candidate = folder / "candidate.py"
    candidate.write_text(code, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "spiderforge_fixture_candidate", candidate
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate_import_spec_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    spiders = [
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, scrapy.Spider)
        and value is not scrapy.Spider
    ]
    if len(spiders) != 1:
        raise RuntimeError(
            f"candidate_spider_class_count:{len(spiders)}"
        )
    return spiders[0]


def _response(
    request: scrapy.Request,
    body: str,
    content_type: str,
) -> HtmlResponse | XmlResponse:
    response_class = (
        XmlResponse
        if "xml" in content_type.lower() or "rss" in content_type.lower()
        else HtmlResponse
    )
    return response_class(
        url=request.url,
        request=request,
        headers={"Content-Type": content_type},
        body=body.encode("utf-8"),
        encoding="utf-8",
    )


def _outputs(value: Iterable | None) -> list:
    if value is None:
        return []
    if isinstance(value, (scrapy.Request, scrapy.Item, dict)):
        return [value]
    return list(value)


def _same_url(left: str, right: str) -> bool:
    return left.split("#", 1)[0].rstrip("/") == right.split(
        "#", 1
    )[0].rstrip("/")


def _transport_ok(
    requests: list[scrapy.Request],
    browser_required: bool,
) -> bool:
    return all(
        request.meta.get("playwright") is True
        if browser_required
        else request.meta.get("playwright") is not True
        for request in requests
    )


def _duplicate_ratio(content: str) -> float:
    words = content.split()
    if len(words) < 24:
        return 1.0
    grams = [
        tuple(words[index : index + 8])
        for index in range(len(words) - 7)
    ]
    return len(set(grams)) / len(grams)


def _validate_item(
    raw_item,
    *,
    min_content_chars: int,
) -> tuple[dict | None, str | None]:
    item = dict(raw_item)
    missing = [
        field
        for field in ("title", "url", "content", "published_at")
        if not item.get(field)
    ]
    if missing:
        return None, f"missing_fields:{missing}"
    try:
        published = datetime.fromisoformat(str(item["published_at"]))
    except ValueError:
        return None, "published_at_invalid"
    if published.tzinfo is None or published.utcoffset() is None:
        return None, "published_at_missing_timezone"
    content = " ".join(str(item["content"]).split())
    title = " ".join(str(item["title"]).split())
    if len(content) < min_content_chars:
        return None, f"content_too_short:{len(content)}"
    folded_title = title.casefold()
    folded_content = content.casefold()
    if (
        title
        and folded_content.startswith(folded_title)
        and folded_content.count(folded_title) > 1
    ):
        return None, "content_repeats_title"
    duplicate_ratio = _duplicate_ratio(content)
    if duplicate_ratio < 0.55:
        return None, f"content_duplicate_ratio:{duplicate_ratio:.3f}"
    return item, None


def validate_bundle(bundle: dict) -> dict:
    fixture = bundle["fixture"]
    with tempfile.TemporaryDirectory(prefix="spiderforge-fixture-") as tmp:
        spider_class = _load_spider(
            str(bundle.get("spider_code") or ""),
            Path(tmp),
        )
        spider = spider_class()
        start_requests = list(spider.start_requests())
        browser_required = bool(fixture.get("browser_required"))
        errors: list[str] = []
        callback_errors: list[str] = []
        if not start_requests:
            errors.append("missing_start_request")
            return {
                "passed": False,
                "start_request_count": 0,
                "detail_request_count": 0,
                "parsed_item_count": 0,
                "errors": errors,
                "callback_errors": callback_errors,
            }
        if not _transport_ok(start_requests, browser_required):
            errors.append("start_request_transport_mismatch")

        first_request = start_requests[0]
        listing = fixture["listing"]
        listing_request = first_request.replace(url=listing["url"])
        listing_response = _response(
            listing_request,
            str(listing.get("body") or ""),
            str(listing.get("content_type") or "text/html"),
        )
        callback = first_request.callback or spider.parse
        try:
            listing_outputs = _outputs(callback(listing_response))
        except Exception as exc:
            listing_outputs = []
            callback_errors.append(
                f"{callback.__name__}:{type(exc).__name__}:{str(exc)[:500]}"
            )
        detail_requests = [
            output
            for output in listing_outputs
            if isinstance(output, scrapy.Request)
        ]
        direct_items = [
            output
            for output in listing_outputs
            if isinstance(output, (scrapy.Item, dict))
        ]
        if not _transport_ok(detail_requests, browser_required):
            errors.append("detail_request_transport_mismatch")

        minimum = int(fixture.get("min_listing_outputs") or 1)
        if len(detail_requests) + len(direct_items) < minimum:
            errors.append(
                "listing_output_too_few:"
                f"{len(detail_requests) + len(direct_items)}<{minimum}"
            )

        parsed_items: list[dict] = []
        for direct in direct_items:
            item, error = _validate_item(
                direct,
                min_content_chars=int(
                    fixture.get("min_content_chars") or 40
                ),
            )
            if error:
                errors.append(f"listing_item:{error}")
            elif item:
                parsed_items.append(item)

        for sample in fixture.get("detail_samples") or []:
            sample_url = str(
                sample.get("final_url")
                or sample.get("requested_url")
                or ""
            )
            request = next(
                (
                    candidate
                    for candidate in detail_requests
                    if _same_url(candidate.url, sample_url)
                ),
                None,
            )
            if request is None:
                errors.append(f"missing_detail_request:{sample_url}")
                continue
            response = _response(
                request,
                str(sample.get("body_excerpt") or ""),
                "text/html",
            )
            detail_callback = request.callback or spider.parse
            try:
                outputs = _outputs(detail_callback(response))
            except Exception as exc:
                outputs = []
                callback_errors.append(
                    f"{detail_callback.__name__}:{type(exc).__name__}:"
                    f"{str(exc)[:500]}"
                )
            items = [
                output
                for output in outputs
                if isinstance(output, (scrapy.Item, dict))
            ]
            if len(items) != 1:
                errors.append(f"detail_item_count:{sample_url}:{len(items)}")
                continue
            item, error = _validate_item(
                items[0],
                min_content_chars=int(
                    fixture.get("min_content_chars") or 40
                ),
            )
            if error:
                errors.append(f"detail_item:{sample_url}:{error}")
            elif item:
                parsed_items.append(item)

        attributes = fixture.get("expected_attributes") or {}
        mismatches = [
            name
            for name, expected in attributes.items()
            if getattr(spider, name, None) != expected
        ]
        if mismatches:
            errors.append(f"class_attribute_mismatch:{mismatches}")
        return {
            "passed": not errors and not callback_errors,
            "start_request_count": len(start_requests),
            "detail_request_count": len(detail_requests),
            "parsed_item_count": len(parsed_items),
            "errors": errors,
            "callback_errors": callback_errors,
            "sample_titles": [
                str(item.get("title") or "") for item in parsed_items[:3]
            ],
            "sample_content_chars": [
                len(str(item.get("content") or "")) for item in parsed_items[:3]
            ],
        }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        bundle = json.loads(sys.stdin.read())
        result = validate_bundle(bundle)
    except Exception as exc:
        result = {
            "passed": False,
            "start_request_count": 0,
            "detail_request_count": 0,
            "parsed_item_count": 0,
            "errors": [
                f"fixture_runner:{type(exc).__name__}:{str(exc)[:500]}"
            ],
            "callback_errors": [],
        }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
