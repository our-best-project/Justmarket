"""將完整 EvidencePack 編譯成有限、去重、可生成程式碼的材料。

完整證據仍保存在 runtime；本模組只建立送給 coder 的唯讀投影。它不選策略、
不呼叫模型，也不修改 EvidencePack。
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any

_KEPT_ATTRIBUTES = {
    "class",
    "content",
    "datetime",
    "href",
    "id",
    "itemprop",
    "name",
    "property",
    "rel",
    "role",
    "type",
    "aria-label",
}
_DOM_MARKERS = (
    "post-content",
    "article-content",
    "article-body",
    "entry-content",
    "story-body",
    "rss-mr-content",
    "post-body",
    "<article",
    "<main",
)
_IGNORED_TAGS = {
    "iframe",
    "noscript",
    "script",
    "style",
    "svg",
    "template",
}


class _CompactHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if self.ignored_depth:
            self.ignored_depth += 1
            return
        if tag in _IGNORED_TAGS:
            self.ignored_depth = 1
            return
        kept = [
            (str(name).lower(), str(value))
            for name, value in attrs
            if str(name).lower() in _KEPT_ATTRIBUTES and value is not None
        ]
        serialized = "".join(
            f' {name}="{value.replace(chr(34), "&quot;")}"'
            for name, value in kept
        )
        self.parts.append(f"<{tag}{serialized}>")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if self.ignored_depth or tag.lower() in _IGNORED_TAGS:
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self.ignored_depth:
            self.ignored_depth -= 1
            return
        self.parts.append(f"</{tag.lower()}>")

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


def compact_dom_html(html: str, *, max_chars: int = 7000) -> str:
    """保留 selector 所需屬性與文字順序，移除生成無用的展示屬性。"""
    parser = _CompactHTMLParser()
    try:
        parser.feed(str(html or ""))
    except Exception:
        return str(html or "")[:max_chars]
    compact = "".join(parser.parts)
    if len(compact) <= max_chars:
        return compact
    lower = compact.lower()
    positions = [
        lower.find(marker)
        for marker in _DOM_MARKERS
        if lower.find(marker) >= 0
    ]
    anchor = min(positions) if positions else 0
    start = max(0, anchor - 1600)
    return compact[start : start + max_chars]


def _compact_replay(pack: dict[str, Any]) -> dict[str, Any]:
    exchange = pack.get("replay_exchange") or {}
    response = dict(exchange.get("response") or {})
    api_sample = pack.get("api_sample") or {}
    structured_format = str(api_sample.get("structured_format") or "")
    body_limit = 4500 if structured_format == "rss_or_atom" else 8000
    response["body_excerpt"] = str(response.get("body_excerpt") or "")[
        :body_limit
    ]
    return {
        "request": exchange.get("request") or {},
        "response": response,
    }


def compile_generation_materials(
    evidence_pack: dict[str, Any],
) -> dict[str, Any]:
    """建立單一、無重複的 coder 輸入；不帶未選 feed 與完整偵察雜訊。"""
    entry = evidence_pack.get("entry_observation") or {}
    api_sample = evidence_pack.get("api_sample") or {}
    dom_samples = [
        {
            "requested_url": sample.get("requested_url"),
            "final_url": sample.get("final_url"),
            "status": sample.get("status"),
            "capture_source": sample.get("capture_source"),
            "body_excerpt": compact_dom_html(
                str(sample.get("body_excerpt") or "")
            ),
        }
        for sample in (evidence_pack.get("dom_samples") or [])[:2]
    ]
    structured_source = {
        key: api_sample.get(key)
        for key in (
            "method",
            "url",
            "requested_url",
            "final_url",
            "status",
            "content_type",
            "structured_format",
            "json_shape",
            "article_record_count",
            "record_detection",
            "capture_source",
        )
        if api_sample.get(key) is not None
    }
    if api_sample.get("feed_items"):
        structured_source["feed_items"] = (
            api_sample.get("feed_items") or []
        )[:12]
    materials = {
        "request": evidence_pack.get("request") or {},
        "strategy": evidence_pack.get("strategy") or {},
        "replay_exchange": _compact_replay(evidence_pack),
        "structured_source": structured_source,
        "entry": {
            "requested_url": entry.get("requested_url"),
            "final_url": entry.get("final_url"),
            "canonical_url": entry.get("canonical_url"),
            "browser_status": entry.get("browser_status"),
            "http_status": entry.get("http_status"),
            "access_assessment": entry.get("access_assessment"),
            "safe_request_headers": entry.get("safe_request_headers"),
            "html_excerpt": compact_dom_html(
                str(entry.get("html_excerpt") or ""),
                max_chars=2200,
            ),
            "link_samples": (entry.get("link_samples") or [])[:20],
        },
        "discovered_detail_urls": (
            evidence_pack.get("discovered_detail_urls") or []
        )[:10],
        "dom_samples": dom_samples,
        "pagination": evidence_pack.get("pagination") or {},
        "published_at_probe": (
            evidence_pack.get("published_at_probe") or {}
        ),
        "replay_headers": evidence_pack.get("replay_headers") or {},
        "requirements": evidence_pack.get("requirements") or [],
        "unresolved": evidence_pack.get("unresolved") or [],
    }
    serialized = json.dumps(materials, ensure_ascii=False)
    materials["material_budget"] = {
        "serialized_chars": len(serialized),
        "dom_sample_count": len(dom_samples),
        "detail_url_count": len(materials["discovered_detail_urls"]),
        "unselected_feed_candidates_included": 0,
    }
    return materials
