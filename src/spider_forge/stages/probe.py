"""請求正規化與入口探測。"""

from __future__ import annotations

import copy
import re
from urllib.parse import urlparse

from ..shared import evidence as evidence_tools
from ..shared.prompts import DEFAULT_TARGET_SCHEMA
from ..shared.topic import normalize_config
from ..state import SpiderForgeState


def _safe_prefix(host: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_")
    return (value or "generated_source")[:40]


def prepare_request(state: SpiderForgeState) -> dict:
    """把一般使用者會給的最小資訊正規化；不要求 selector、HAR 或 API 路徑。"""
    site_url = str(state.get("site_url") or "").strip()
    parsed = urlparse(site_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("site_url 必須是含 http/https 的有效 URL")

    host = parsed.hostname.lower()
    explicit_prefix = bool(str(state.get("source_prefix") or "").strip())
    prefix = str(state.get("source_prefix") or "").strip() or _safe_prefix(host)
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", prefix):
        raise ValueError("source_prefix 必須符合 [a-z][a-z0-9_]{0,39}")

    schema = copy.deepcopy(DEFAULT_TARGET_SCHEMA)
    supplied_schema = state.get("target_schema") or {}
    schema.update({k: v for k, v in supplied_schema.items() if k != "fields"})
    schema["fields"].update(supplied_schema.get("fields") or {})

    validation = dict(state.get("validation") or {})
    validation.setdefault("allowed_domains", [host])
    validation.setdefault("min_content_chars", 40)
    content_contract = schema.get("fields", {}).get("content", {})
    if schema.get("source_type") == "media" and content_contract.get("max_chars"):
        validation.setdefault("max_content_chars", content_contract["max_chars"])
    validation.setdefault("max_age_days", 30)
    validation.setdefault("min_valid_items", 5)
    validation.setdefault("min_unique_ratio", 0.8)
    topic_gate = normalize_config(
        state.get("topic_gate"),
        min_valid_items=int(validation["min_valid_items"]),
    )

    sample_urls = []
    for value in state.get("sample_urls") or []:
        sample = str(value).strip()
        p = urlparse(sample)
        if p.scheme in {"http", "https"} and p.hostname and sample not in sample_urls:
            sample_urls.append(sample)

    access_mode = state.get("access_mode") or "public"
    if access_mode not in {"public", "browser_session"}:
        raise ValueError("access_mode 僅允許 public 或 browser_session")
    if access_mode == "browser_session" and not state.get("access_context_ref"):
        raise ValueError("browser_session 必須提供 access_context_ref")

    requested_retries = int(state.get("max_retries", 2))
    return {
        "site_url": site_url,
        "site_name": str(state.get("site_name") or host),
        "source_prefix": prefix,
        "source_prefix_explicit": explicit_prefix,
        "target_schema": schema,
        "target_schema_explicit": bool(supplied_schema),
        "sample_urls": sample_urls[:5],
        "access_mode": access_mode,
        "constraints": {
            "max_pages": 2,
            "validation_probe_items": 20,
            **(state.get("constraints") or {}),
        },
        "validation": validation,
        "validation_explicit": bool(state.get("validation")),
        "topic_gate": topic_gate,
        "max_retries": max(0, min(2, requested_retries)),
        "retry_count": 0,
        "error_signature_history": [],
        "kimi_used": False,
        "status": "request_ready",
    }


def recon(state: SpiderForgeState) -> dict:
    from ..clients.browser import probe

    site_url = state["site_url"]
    http_sample = evidence_tools._fetch_sample(
        site_url,
        max_chars=6000,
        max_links=250,
        include_raw_body=True,
    )
    raw_http_body = http_sample.pop("_raw_body", "")
    try:
        report = probe(
            site_url,
            max_links=200,
            storage_state_path=(
                state.get("access_context_ref")
                if state.get("access_mode") == "browser_session"
                else None
            ),
        )
    except Exception as exc:
        error_message = str(exc)
        access_ref = str(state.get("access_context_ref") or "")
        if access_ref:
            error_message = error_message.replace(access_ref, "<access_context_ref>")
        report = {
            "url": site_url,
            "http_status": None,
            "title": None,
            "aria_snapshot": "",
            "api_candidates": [],
            "recon_error": error_message[:500],
        }
    original_browser_status = report.get("http_status")
    original_browser_blocked = (
        original_browser_status in {401, 403, 429}
        or report.get("soft_block_detected") is True
    )
    if (
        original_browser_blocked
        and http_sample.get("status") == 200
        and "html" in str(http_sample.get("content_type") or "").lower()
        and raw_http_body
        and state.get("access_mode", "public") == "public"
    ):
        original_report = report
        try:
            hydrated = probe(
                site_url,
                max_links=200,
                document_html=raw_http_body,
            )
            report = hydrated
            report["original_browser_status"] = original_browser_status
            report["original_document_chain"] = (
                original_report.get("document_chain") or []
            )[:10]
            report["browser_hydration_used"] = True
        except Exception as exc:  # keep the original 403 evidence
            report["browser_hydration_error"] = str(exc)[:500]
    report["http_entry_sample"] = http_sample
    report["feed_candidates"] = evidence_tools._discover_feed_candidates(http_sample)
    report["final_url"] = (
        report.get("final_url")
        or http_sample.get("final_url")
        or site_url
    )
    report["canonical_url"] = (
        report.get("canonical_url")
        or http_sample.get("canonical_url")
        or report["final_url"]
    )
    browser_status = report.get(
        "original_browser_status", report.get("http_status")
    )
    plain_status = http_sample.get("status")
    browser_ok = bool(browser_status and browser_status < 400)
    plain_blocked = plain_status in {401, 403, 429}
    browser_blocked = (
        browser_status in {401, 403, 429}
        or report.get("soft_block_detected") is True
    )
    if browser_blocked and plain_status == 200:
        access_assessment = "browser_blocked_http_ok"
    elif browser_ok and plain_blocked:
        access_assessment = "browser_required_http_blocked"
    elif browser_blocked and state.get("access_mode") == "browser_session":
        access_assessment = "browser_session_invalid_or_blocked"
    elif browser_blocked:
        access_assessment = "browser_session_required"
    elif browser_ok:
        access_assessment = "browser_public_ok"
    elif plain_status == 200:
        access_assessment = "http_public_ok"
    else:
        access_assessment = "unresolved"
    if access_assessment == "browser_blocked_http_ok":
        report["final_url"] = http_sample.get("final_url") or report["final_url"]
        report["canonical_url"] = (
            http_sample.get("canonical_url") or report["final_url"]
        )
    report["access_assessment"] = access_assessment
    return {"recon_report": report, "status": "reconning"}


# ════════════════════════ 可行性分流（spec v2 §3.2/§4，D1） ════════════════════════
