"""證據抓取、解析與 EvidencePack 組裝的共用服務。

包含 ``collect_evidence`` 使用的抓取／解析 helper
（``_fetch_sample``、feed／detail 探索等）。

測試若要替換網路抓取，直接 monkeypatch 本模組的 ``_fetch_sample``；實作與
測試共享同一個明確邊界，不再繞過 graph 套件轉接。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse

from ..state import SpiderForgeState


def _prompt_safe_request(state: SpiderForgeState) -> dict:
    """刻意排除 access_context_ref；prompt 與 sandbox 永遠拿不到 session secret。"""
    return {
        "site_url": state["site_url"],
        "site_name": state["site_name"],
        "source_prefix": state["source_prefix"],
        "target_schema": state["target_schema"],
        "sample_urls": state.get("sample_urls", []),
        "constraints": state.get("constraints", {}),
        "validation": state.get("validation", {}),
    }


# ════════════════════════ 抓取／解析 helper ════════════════════════


class _LinkEvidenceParser(HTMLParser):
    def __init__(self, base_url: str, max_links: int):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.max_links = max_links
        self.links: list[dict[str, str]] = []
        self.declared_feed_links: list[dict[str, str]] = []
        self.canonical_url: str | None = None
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(key).lower(): value for key, value in attrs}
        if tag.lower() == "link":
            rel = str(values.get("rel") or "").lower()
            media_type = str(values.get("type") or "").lower()
            href = values.get("href")
            if href and "canonical" in rel:
                self.canonical_url = urljoin(self.base_url, href)
            if (
                href
                and "alternate" in rel
                and media_type
                in {
                    "application/rss+xml",
                    "application/atom+xml",
                }
            ):
                self.declared_feed_links.append(
                    {
                        "url": urljoin(self.base_url, href),
                        "text": str(values.get("title") or "")[:300],
                    }
                )
        if tag.lower() == "a" and len(self.links) < self.max_links:
            href = values.get("href")
            if href:
                self._href = urljoin(self.base_url, href)
                self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        text = " ".join(" ".join(self._text).split())
        if text:
            self.links.append({"url": self._href, "text": text[:300]})
        self._href = None
        self._text = []


def _html_evidence(
    html: str, base_url: str, *, max_links: int = 40
) -> tuple[str | None, list[dict[str, str]], list[dict[str, str]]]:
    parser = _LinkEvidenceParser(base_url, max_links)
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.canonical_url, parser.links, parser.declared_feed_links


def _feed_evidence(xml: str) -> list[dict[str, str]]:
    from xml.etree import ElementTree

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []

    items: list[dict[str, str]] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() not in {"item", "entry"}:
            continue
        item: dict[str, str] = {}
        for child in list(node):
            name = child.tag.rsplit("}", 1)[-1].lower()
            text = " ".join("".join(child.itertext()).split())
            if name == "title" and text:
                item["title"] = text[:500]
            elif name == "link":
                link = child.attrib.get("href") or text
                if link:
                    item["url"] = link[:2000]
            elif name in {"pubdate", "published", "updated", "date"} and text:
                item["published_at"] = text[:200]
            elif name in {"description", "summary", "content"} and text:
                item["description"] = text[:1000]
            elif name in {"guid", "id"} and text:
                item["source_record_id"] = text[:500]
        if item.get("title") and item.get("url"):
            items.append(item)
        if len(items) >= 20:
            break
    return items


def _fetch_sample(
    url: str,
    *,
    max_chars: int = 6000,
    max_links: int = 0,
    include_raw_body: bool = False,
) -> dict[str, Any]:
    import requests

    from .request_identity import browser_request_headers

    # spec v2 §3.1 校準：recon 以真實瀏覽器身分抓取，http_sample 才貼近瀏覽器所見，
    # 減少假 403 誤殺；這組 headers 也會存進 pack.replay_headers 供生成/沙盒逐字重播。
    request_headers = browser_request_headers()
    try:
        response = requests.get(
            url,
            timeout=20,
            headers=request_headers,
        )
        content_type = response.headers.get("content-type", "")
        canonical_url = None
        links: list[dict[str, str]] = []
        declared_feed_links: list[dict[str, str]] = []
        if "html" in content_type.lower() and max_links:
            canonical_url, links, declared_feed_links = _html_evidence(
                response.text, response.url, max_links=max_links
            )
        feed_items = (
            _feed_evidence(response.text)
            if (
                "xml" in content_type.lower()
                or response.text.lstrip().lower().startswith(("<rss", "<feed"))
            )
            else []
        )
        result = {
            "requested_url": url,
            "final_url": response.url,
            "canonical_url": canonical_url or response.url,
            "redirect_chain": [
                {
                    "url": hop.url,
                    "status": hop.status_code,
                    "location": hop.headers.get("location"),
                }
                for hop in response.history
            ],
            "status": response.status_code,
            "content_type": content_type,
            "safe_request_headers": request_headers,
            "safe_response_headers": {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower()
                in {
                    "content-type",
                    "content-length",
                    "location",
                    "cache-control",
                    "etag",
                    "last-modified",
                }
            },
            "body_excerpt": response.text[:max_chars],
            "truncated": len(response.text) > max_chars,
        }
        if links:
            result["link_samples"] = links
        if declared_feed_links:
            result["declared_feed_links"] = declared_feed_links
        if feed_items:
            result["feed_items"] = feed_items
            result["article_record_count"] = len(feed_items)
            result["structured_format"] = "rss_or_atom"
        if include_raw_body:
            result["_raw_body"] = response.text
        return result
    except Exception as exc:
        return {"requested_url": url, "fetch_error": str(exc)[:500]}


def _fetch_browser_sample(url: str) -> dict[str, Any]:
    """用公開瀏覽器取得 plain HTTP 無法取得的明細 DOM 樣本。"""
    from ..clients.browser import probe

    report = probe(
        url,
        settle_ms=3000,
        max_network=60,
        max_api_bodies=10,
        max_links=30,
        snapshot_chars=6000,
    )
    return {
        "requested_url": url,
        "final_url": report.get("final_url") or url,
        "canonical_url": report.get("canonical_url")
        or report.get("final_url")
        or url,
        "status": report.get("http_status"),
        "content_type": "text/html",
        "capture_source": "public_browser",
        "body_excerpt": report.get("dom_excerpt") or "",
        "body_truncated": report.get("dom_truncated"),
        "text_excerpt": report.get("main_text_excerpt") or "",
        "text_truncated": report.get("main_text_truncated"),
        "aria_snapshot": report.get("aria_snapshot") or "",
        "dom_capture_error": report.get("dom_capture_error"),
        "navigation_error": report.get("navigation_error"),
        "soft_block_detected": report.get("soft_block_detected"),
    }


def _usable_detail_sample(sample: dict[str, Any]) -> bool:
    status = sample.get("status")
    return bool(
        status
        and status < 400
        and sample.get("body_excerpt")
        and sample.get("soft_block_detected") is not True
    )


def _is_feed_link(row: dict[str, str]) -> bool:
    url = str(row.get("url") or "")
    text = str(row.get("text") or "")
    path = urlparse(url).path.lower()
    return bool(
        re.search(r"(?:^|/)(?:rss|feeds?|atom)(?:/|$|\.)", path)
        or path.endswith(".xml")
        or re.search(r"\b(?:rss|atom) feeds?\b|\brss\b", text, re.IGNORECASE)
    )


def _feed_priority(url: str) -> tuple[int, int]:
    path = urlparse(url).path.lower()
    relevant = any(
        marker in path
        for marker in (
            "news",
            "press",
            "policy",
            "econom",
            "financ",
            "market",
            "publication",
        )
    )
    return (0 if relevant else 1, len(path))


def _discover_feed_candidates(
    entry_sample: dict[str, Any], *, limit: int = 3, max_fetches: int = 6
) -> list[dict[str, Any]]:
    entry_url = str(entry_sample.get("final_url") or "")
    host = urlparse(entry_url).hostname
    entry_link_urls = {
        str(row.get("url") or "").split("#", 1)[0].rstrip("/")
        for row in entry_sample.get("link_samples") or []
        if row.get("url")
    }
    declared = sorted(
        {
            str(row.get("url"))
            for row in entry_sample.get("declared_feed_links") or []
            if _is_feed_link(row)
            and urlparse(str(row.get("url") or "")).hostname == host
        },
        key=_feed_priority,
    )
    anchored = sorted(
        {
            str(row.get("url"))
            for row in entry_sample.get("link_samples") or []
            if _is_feed_link(row)
            and urlparse(str(row.get("url") or "")).hostname == host
            and str(row.get("url")) not in declared
        },
        key=_feed_priority,
    )
    queue = [
        *((url, "document_alternate") for url in declared),
        *((url, "anchor") for url in anchored),
    ]
    seen: set[str] = set()
    feeds: list[dict[str, Any]] = []
    fetches = 0
    while queue and fetches < max_fetches:
        url, discovery_source = queue.pop(0)
        if not url or url in seen:
            continue
        seen.add(url)
        fetches += 1
        sample = _fetch_sample(url, max_chars=6000, max_links=500)
        if sample.get("feed_items"):
            overlap_count = sum(
                1
                for item in sample.get("feed_items") or []
                if str(item.get("url") or "").split("#", 1)[0].rstrip("/")
                in entry_link_urls
            )
            feeds.append(
                {
                    **sample,
                    "method": "GET",
                    "url": sample.get("final_url") or url,
                    "json_shape": "rss_or_atom",
                    "discovery_source": discovery_source,
                    "entry_link_overlap_count": overlap_count,
                }
            )
            continue
        nested = [
            str(row.get("url"))
            for row in sample.get("link_samples") or []
            if _is_feed_link(row)
            and urlparse(str(row.get("url") or "")).hostname == host
            and str(row.get("url")) not in seen
        ]
        queued_urls = {queued_url for queued_url, _ in queue}
        queue.extend(
            (nested_url, "feed_index")
            for nested_url in sorted(set(nested), key=_feed_priority)
            if nested_url not in queued_urls
        )
    feeds.sort(
        key=lambda feed: (
            int(feed.get("entry_link_overlap_count") or 0),
            feed.get("discovery_source") == "document_alternate",
            int(feed.get("article_record_count") or 0),
        ),
        reverse=True,
    )
    return feeds[:limit]


def _matches_validation_url(url: str, state: SpiderForgeState) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    validation = state.get("validation") or {}
    domains = {
        str(domain).lower().lstrip(".")
        for domain in validation.get("allowed_domains") or []
    }
    host = parsed.hostname.lower()
    if domains and not any(host == domain or host.endswith("." + domain) for domain in domains):
        return False
    if any(
        re.search(pattern, url, re.IGNORECASE)
        for pattern in validation.get("excluded_url_patterns") or []
    ):
        return False
    patterns = validation.get("article_url_patterns") or []
    return not patterns or any(
        re.search(pattern, url, re.IGNORECASE) for pattern in patterns
    )


def _discover_detail_urls(
    state: SpiderForgeState, recon_report: dict, *, limit: int = 2
) -> list[str]:
    urls: list[str] = []
    chosen_api = str((state.get("strategy_detail") or {}).get("chosen_api") or "")
    structured_candidates = [
        *(recon_report.get("api_candidates") or []),
        *(recon_report.get("feed_candidates") or []),
    ]
    selected_candidates = (
        [
            candidate
            for candidate in structured_candidates
            if str(candidate.get("url") or "") == chosen_api
        ]
        if chosen_api
        else structured_candidates
    )
    rows = [
        *[
            {"url": item.get("url"), "text": item.get("title")}
            for candidate in selected_candidates
            for item in candidate.get("feed_items") or []
        ],
        *(recon_report.get("link_samples") or []),
        *(recon_report.get("http_entry_sample", {}).get("link_samples") or []),
    ]
    for supplied in state.get("sample_urls") or []:
        if supplied not in urls:
            urls.append(supplied)
    entry_urls = {
        state.get("site_url"),
        recon_report.get("final_url"),
        recon_report.get("canonical_url"),
    }
    for row in rows:
        url = str(row.get("url") or "")
        if (
            url
            and url not in entry_urls
            and url not in urls
            and _matches_validation_url(url, state)
        ):
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls[:limit]


# ════════════════════════ EvidencePack 三缺口探測（spec v2 §2）════════════════════════

# 翻頁 query 參數名（小寫）：命中即判 query_param 型翻頁。刻意收斂到高辨識度的名字，
# 不收 from/index 這種易誤判的通用字。
_PAGINATION_PARAMS = (
    "page", "pg", "pageno", "page_no", "page_num", "pageindex",
    "offset", "start", "cursor", "after", "before", "skip", "pn", "p",
)
# JSON 回應裡代表「還有下一頁」的鍵名片段（小寫子字串比對）。
_CURSOR_MARKERS = (
    "next_cursor", "nextcursor", "next_page", "nextpage", "next_url", "nexturl",
    "has_more", "hasmore", "has_next", "hasnext", '"next"', "load_more",
    "total_pages", "totalpages", "page_count", "pagecount",
)
_NEXT_LINK_RE = re.compile(r'<link[^>]+rel=["\']?next["\']?[^>]*>', re.IGNORECASE)
_NEXT_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def _detect_pagination(
    *, chosen_api: str, entry_url: str, api_body: str, entry_html: str
) -> dict:
    """確定性偵測翻頁機制（spec v2 §2 缺口1）。偵測不到就明說 none_detected，不臆造。"""
    for source_url in (chosen_api, entry_url):
        params = {
            key.lower()
            for key, _ in parse_qsl(urlparse(str(source_url or "")).query, keep_blank_values=True)
        }
        hit = next((name for name in _PAGINATION_PARAMS if name in params), None)
        if hit:
            return {
                "type": "query_param",
                "param": hit,
                "example_url": str(source_url),
                "note": f"以 {hit} 遞增翻頁；遵守 constraints.max_pages 上限。",
            }
    body_lower = str(api_body or "").lower()
    cursor_hit = next((marker for marker in _CURSOR_MARKERS if marker in body_lower), None)
    if cursor_hit:
        return {
            "type": "cursor",
            "marker": cursor_hit.strip('"'),
            "note": f"回應含 {cursor_hit.strip(chr(34))} 之類的游標/總頁鍵；沿用其值請下一頁。",
        }
    link_tag = _NEXT_LINK_RE.search(str(entry_html or ""))
    if link_tag:
        href = _NEXT_HREF_RE.search(link_tag.group(0))
        return {
            "type": "next_link",
            "example_url": urljoin(str(entry_url or ""), href.group(1)) if href else None,
            "note": "HTML 有 <link rel=next>；沿用其 href 當下一頁。",
        }
    return {"type": "none_detected", "note": "未偵測到確定性翻頁訊號；預設只抓第 1 頁。"}


_META_DATE_RES = (
    re.compile(r'article:published_time["\']?\s+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.IGNORECASE),
    re.compile(r'name=["\']pubdate["\']\s+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.IGNORECASE),
)
_ISO_TZ_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}.*?(Z|[+-]\d{2}:?\d{2})$")
_ISO_NAIVE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?$")
_DATE_ONLY_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")
_ROC_RE = re.compile(r"^(民國\s*)?1[0-4]\d[/-]\d{1,2}[/-]\d{1,2}")
_RFC822_RE = re.compile(r"^\w{3},\s*\d{1,2}\s+\w{3}\s+\d{4}", re.IGNORECASE)
_RELATIVE_RE = re.compile(r"(前|ago|小時|分鐘|剛剛|hours?|minutes?|days?)", re.IGNORECASE)


def _classify_datetime(raw: Any) -> tuple[str, bool | None]:
    """把一個 published_at 原始值歸類成格式 + 是否帶時區（spec v2 §2 缺口2）。"""
    if raw is None:
        return "missing", None
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.strip().isdigit()):
        digits = str(raw).strip()
        if len(digits) >= 13:
            return "epoch_millis", True
        if len(digits) >= 9:
            return "epoch_seconds", True
        return "unknown", None
    text = str(raw).strip()
    if not text:
        return "missing", None
    if _ISO_TZ_RE.search(text):
        return "iso8601_tz", True
    if _ISO_NAIVE_RE.match(text):
        return "iso8601_naive", False
    if _ROC_RE.match(text):
        return "roc_year", False
    if _RFC822_RE.match(text):
        return "rfc822", True
    if _RELATIVE_RE.search(text):
        return "relative", False
    if _DATE_ONLY_RE.match(text):
        return "date_only", False
    return "unknown", None


def _probe_published_at(
    *,
    feed_candidates: list[dict],
    detail_samples: list[dict],
    api_body: str,
    source_timezone: str = "",
) -> dict:
    """蒐集 published_at 原始樣本並判定格式/時區，讓生成模型照原樣寫解析、補時區。"""
    raw_values: list[str] = []
    for feed in feed_candidates or []:
        for item in feed.get("feed_items") or []:
            if item.get("published_at"):
                raw_values.append(str(item["published_at"]))
    for sample in detail_samples or []:
        body = str(sample.get("body_excerpt") or "")
        for pattern in _META_DATE_RES:
            match = pattern.search(body)
            if match:
                raw_values.append(match.group(1).strip())
                break
    # api JSON 裡的日期樣本：抓常見鍵的字串/數字值
    for match in re.finditer(
        r'"(?:published_at|publishAt|publishedAt|pubDate|date|datetime|created_at)"\s*:\s*'
        r'("(?:[^"\\]|\\.)*"|\d{9,13})',
        str(api_body or ""),
    ):
        raw_values.append(match.group(1).strip('"'))
        if len(raw_values) >= 12:
            break

    seen: list[str] = []
    for value in raw_values:
        if value and value not in seen:
            seen.append(value)
    samples = seen[:5]
    classified = [
        {"raw": value, "format": fmt, "has_timezone": tz}
        for value in samples
        for fmt, tz in (_classify_datetime(value),)
    ]
    formats = {row["format"] for row in classified}
    dominant = classified[0]["format"] if classified else "no_sample"
    any_naive = any(row["has_timezone"] is False for row in classified)
    timezone_name = str(source_timezone or "").strip()
    return {
        "raw_samples": classified,
        "dominant_format": dominant,
        "formats_seen": sorted(formats),
        "needs_timezone_completion": any_naive,
        "source_timezone": timezone_name or None,
        "note": (
            f"原始值沒有時區；必須用來源時區 {timezone_name} 解析，再輸出含時區 ISO8601。"
            if any_naive and timezone_name
            else "原始值沒有時區，但請求未提供 source_timezone；不得猜測時區。"
            if any_naive
            else "原始值多半已帶時區/為 epoch；照原樣正確解析即可。"
            if classified
            else "未取得 published_at 原始樣本；到明細頁抓 meta/JSON-LD 日期，禁止用現在時間偽造。"
        ),
    }


# ════════════════════════ 完整 live recon pack ════════════════════════


def collect_evidence(state: SpiderForgeState) -> dict:
    """將 live recon 壓成 coding model 可直接使用的內部 EvidencePack。"""
    recon_report = state.get("recon_report") or {}
    chosen_api = (state.get("strategy_detail") or {}).get("chosen_api") or ""
    structured_candidates = [
        *(recon_report.get("api_candidates") or []),
        *(recon_report.get("feed_candidates") or []),
    ]
    chosen_candidate = next(
        (row for row in structured_candidates if row.get("url") == chosen_api),
        {},
    )
    is_public = state.get("access_mode") == "public"
    api_sample: dict[str, Any] = {}
    if chosen_api:
        if (
            chosen_candidate.get("body_excerpt")
            and not chosen_candidate.get("truncated")
            and not chosen_candidate.get("body_truncated")
        ):
            api_sample = {
                **chosen_candidate,
                "capture_source": "browser_network",
            }
        elif chosen_candidate.get("method", "GET").upper() == "GET" and is_public:
            api_sample = {
                **_fetch_sample(chosen_api, max_chars=20000),
                "method": "GET",
                "url": chosen_api,
                "json_shape": chosen_candidate.get("json_shape"),
                "capture_source": "public_http_refetch",
            }
        else:
            api_sample = {
                "requested_url": chosen_api,
                "method": chosen_candidate.get("method"),
                "fetch_error": "POST/auth response body must come from browser evidence",
            }
    chosen_path = urlparse(chosen_api).path.split("/") if chosen_api else []

    def path_similarity(candidate: dict[str, Any]) -> int:
        parts = urlparse(str(candidate.get("url") or "")).path.split("/")
        return next(
            (
                index
                for index, (left, right) in enumerate(zip(chosen_path, parts))
                if left != right
            ),
            min(len(chosen_path), len(parts)),
        )

    api_context_samples = []
    if chosen_api:
        related = sorted(
            (
                row
                for row in structured_candidates
                if row.get("url") != chosen_api and row.get("body_excerpt")
            ),
            key=path_similarity,
            reverse=True,
        )
        api_context_samples = [
            {
                **{
                    key: row.get(key)
                    for key in (
                        "method",
                        "url",
                        "status",
                        "json_shape",
                        "article_record_count",
                        "record_detection",
                    )
                },
                "body_excerpt": str(row.get("body_excerpt") or "")[:1200],
            }
            for row in related[:4]
        ]

    discovered_detail_urls = _discover_detail_urls(state, recon_report)
    browser_transport_required = (
        recon_report.get("access_assessment")
        == "browser_required_http_blocked"
    )
    browser_detail_available = recon_report.get("access_assessment") in {
        "browser_public_ok",
        "browser_required_http_blocked",
    }
    if is_public and browser_detail_available:
        detail_samples = [
            _fetch_browser_sample(url) for url in discovered_detail_urls
        ]
    elif is_public:
        detail_samples = [
            _fetch_sample(url, max_chars=20000)
            for url in discovered_detail_urls
        ]
    else:
        detail_samples = []
    usable_detail_samples = [
        sample for sample in detail_samples if _usable_detail_sample(sample)
    ]
    entry_http = recon_report.get("http_entry_sample") or {}
    entry_browser_html = str(
        recon_report.get("dom_excerpt") or ""
    )
    entry_observation = {
        "requested_url": state.get("site_url"),
        "browser_status": recon_report.get("http_status"),
        "original_browser_status": recon_report.get("original_browser_status"),
        "http_status": entry_http.get("status"),
        "final_url": recon_report.get("final_url"),
        "canonical_url": recon_report.get("canonical_url"),
        "access_assessment": recon_report.get("access_assessment"),
        "browser_session_used": recon_report.get("browser_session_used"),
        "browser_hydration_used": recon_report.get("browser_hydration_used"),
        "browser_hydration_error": recon_report.get("browser_hydration_error"),
        "navigation_error": recon_report.get("navigation_error"),
        "recon_error": recon_report.get("recon_error"),
        "document_chain": (recon_report.get("document_chain") or [])[:10],
        "http_redirect_chain": (entry_http.get("redirect_chain") or [])[:10],
        "safe_request_headers": (
            None
            if browser_transport_required
            else entry_http.get("safe_request_headers")
        ),
        "safe_response_headers": entry_http.get("safe_response_headers"),
        "title": recon_report.get("title"),
        "aria_snapshot": (recon_report.get("aria_snapshot") or "")[:3000],
        "html_excerpt": (
            entry_browser_html
            if browser_transport_required and entry_browser_html
            else str(entry_http.get("body_excerpt") or "")
        )[:2500],
        "link_samples": (
            (recon_report.get("link_samples") or [])
            + (entry_http.get("link_samples") or [])
        )[:30],
    }
    network_candidates = [
        {
            key: value
            for key, value in row.items()
            if key
            in {
                "method",
                "url",
                "status",
                "content_type",
                "request_headers",
                "request_post_data",
                "json_shape",
                "article_record_count",
                "record_detection",
                "body_error",
            }
        }
        for row in (recon_report.get("api_candidates") or [])[:20]
    ]
    # spec v2 §2 三缺口：翻頁機制、published_at 原始值/格式/時區、可重播 headers。
    api_body = str(
        api_sample.get("body_excerpt") or chosen_candidate.get("body_excerpt") or ""
    )
    entry_html = str(entry_http.get("body_excerpt") or "")
    pagination = _detect_pagination(
        chosen_api=chosen_api,
        entry_url=recon_report.get("final_url") or state.get("site_url") or "",
        api_body=api_body,
        entry_html=entry_html,
    )
    published_at_probe = _probe_published_at(
        feed_candidates=recon_report.get("feed_candidates") or [],
        detail_samples=usable_detail_samples,
        api_body=api_body,
        source_timezone=str(
            (state.get("constraints") or {}).get("source_timezone") or ""
        ),
    )
    replay_headers = {
        "entry": (
            None
            if browser_transport_required
            else entry_http.get("safe_request_headers")
        ),
        "api": (
            api_sample.get("request_headers")
            or api_sample.get("safe_request_headers")
        ),
        "note": (
            "plain HTTP 被擋；必須使用 EvidencePack 已證實的公開瀏覽器傳輸。"
            if browser_transport_required
            else "recon 取得 200 用的請求 headers，逐字沿用（含 Referer/X-Requested-With 若有）。"
        ),
    }
    replay_exchange = (
        {
            "request": {
                "method": api_sample.get("method")
                or chosen_candidate.get("method")
                or "GET",
                "url": api_sample.get("url")
                or api_sample.get("requested_url")
                or chosen_api,
                "headers": replay_headers["api"],
                "body": api_sample.get("request_post_data")
                or chosen_candidate.get("request_post_data"),
            },
            "response": {
                "status": api_sample.get("status"),
                "headers": api_sample.get("response_headers")
                or api_sample.get("safe_response_headers"),
                "body_excerpt": api_sample.get("body_excerpt"),
                "body_truncated": api_sample.get("body_truncated"),
            },
        }
        if chosen_api
        else {}
    )
    pack = {
        "version": 2,
        "origin": "live_recon",
        "request": _prompt_safe_request(state),
        "entry_observation": entry_observation,
        "strategy": state.get("strategy_detail"),
        "api_sample": api_sample,
        "api_context_samples": api_context_samples,
        "discovered_detail_urls": discovered_detail_urls,
        "dom_samples": [
            {
                key: sample.get(key)
                for key in (
                    "requested_url",
                    "final_url",
                    "canonical_url",
                    "status",
                    "capture_source",
                    "body_excerpt",
                    "body_truncated",
                    "text_excerpt",
                    "aria_snapshot",
                )
            }
            for sample in usable_detail_samples
        ],
        "detail_samples": detail_samples,
        "pagination": pagination,
        "published_at_probe": published_at_probe,
        "replay_headers": replay_headers,
        "replay_exchange": replay_exchange,
        "network_candidates": network_candidates,
        "feed_candidates": (recon_report.get("feed_candidates") or [])[:3],
        "requirements": [
            requirement
            for requirement, required in (
                (
                    "browser_transport",
                    recon_report.get("access_assessment")
                    in {
                        "browser_required_http_blocked",
                        "browser_session_required",
                    },
                ),
            )
            if required
        ],
        "unresolved": [
            issue
            for issue, present in (
                ("entry_recon_failed", bool(recon_report.get("recon_error"))),
                ("no_api_body_sample", bool(chosen_api and not api_sample.get("body_excerpt"))),
                ("no_detail_example", not bool(usable_detail_samples)),
                (
                    "browser_session_invalid_or_blocked",
                    recon_report.get("access_assessment")
                    == "browser_session_invalid_or_blocked",
                ),
            )
            if present
        ],
    }
    return {"evidence_pack": pack, "status": "evidence_ready"}


def _is_replayable_article_api(candidate: dict[str, Any]) -> bool:
    """API strategy requires captured article data and a request we can reproduce."""
    method = str(candidate.get("method") or "GET").upper()
    post_data = str(candidate.get("request_post_data") or "")
    request_replayable = method == "GET" or (
        bool(post_data) and "<redacted>" not in post_data
    )
    return (
        bool(candidate.get("body_excerpt"))
        and int(candidate.get("article_record_count") or 0) > 0
        and request_replayable
    )
