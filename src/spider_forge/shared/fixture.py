"""以保存證據驗證候選爬蟲，不連正式網站。

本模組是控制層服務：只把 EvidencePack 編成 fixture 規格並啟動 crawler runtime。
未知候選碼不會在本程序 import，crawler runtime 也不依賴 Spider Forge。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from ..output import artifacts
from ..state import SpiderForgeState
from .sandbox import run_fixture_candidate


def _listing_fixture(state: SpiderForgeState) -> dict[str, str]:
    pack = state.get("evidence_pack") or {}
    exchange = pack.get("replay_exchange") or {}
    request = exchange.get("request") or {}
    response = exchange.get("response") or {}
    replay_body = str(response.get("body_excerpt") or "")
    if replay_body:
        api = pack.get("api_sample") or {}
        return {
            "url": str(
                response.get("final_url")
                or request.get("url")
                or api.get("url")
                or state.get("site_url")
                or ""
            ),
            "body": replay_body,
            "content_type": str(
                response.get("content_type")
                or api.get("content_type")
                or "application/json"
            ),
            "capture_source": "replay_exchange",
        }

    recon = state.get("recon_report") or {}
    entry = pack.get("entry_observation") or {}
    body = str(
        recon.get("dom_excerpt")
        or entry.get("html_excerpt")
        or (recon.get("http_entry_sample") or {}).get("body_excerpt")
        or ""
    )
    return {
        "url": str(
            recon.get("final_url")
            or entry.get("final_url")
            or state.get("site_url")
            or ""
        ),
        "body": body,
        "content_type": "text/html",
        "capture_source": (
            "browser_dom"
            if recon.get("dom_excerpt")
            else "entry_observation"
        ),
    }


def build_fixture_spec(state: SpiderForgeState) -> dict[str, Any]:
    """把已保存的列表與明細 response 整理成 runtime 中立的 JSON 規格。"""
    pack = state.get("evidence_pack") or {}
    listing = _listing_fixture(state)
    strategy = str(
        state.get("strategy")
        or (pack.get("strategy") or {}).get("strategy")
        or ""
    )
    needs_detail_callbacks = (
        strategy != "api"
        or listing["capture_source"] != "replay_exchange"
    )
    samples = (
        list(
            pack.get("dom_samples")
            or pack.get("detail_samples")
            or []
        )[:2]
        if needs_detail_callbacks
        else []
    )
    observed = len(samples)
    if not observed:
        observed = int(
            (pack.get("api_sample") or {}).get("article_record_count")
            or len(pack.get("discovered_detail_urls") or [])
            or len(
                (pack.get("entry_observation") or {}).get(
                    "link_samples"
                )
                or []
            )
            or 1
        )
    validation = state.get("validation") or {}
    minimum = min(
        max(1, int(validation.get("min_valid_items") or 1)),
        max(1, observed),
    )
    schema = state.get("target_schema") or {}
    return {
        "listing": listing,
        "detail_samples": samples,
        "browser_required": (
            "browser_transport" in set(pack.get("requirements") or [])
        ),
        "min_listing_outputs": minimum,
        "min_content_chars": int(
            validation.get("min_content_chars") or 40
        ),
        "expected_attributes": {
            "name": state.get("source_prefix"),
            "source_prefix": state.get("source_prefix"),
            "source": state.get("site_name"),
            "source_type": schema.get("source_type", "media"),
            "content_scope": schema.get(
                "content_scope", "summary_only"
            ),
        },
    }


def fixture_test(state: SpiderForgeState) -> dict[str, Any]:
    """在獨立 crawler runtime 以保存 response 執行候選 callback。"""
    run_id = state.get("run_id") or uuid.uuid4().hex[:8]
    candidate = artifacts.write_candidate(
        run_id,
        state["source_prefix"],
        state.get("spider_code", ""),
    )
    fixture = build_fixture_spec(state)
    if not fixture["listing"]["body"]:
        result = {
            "passed": False,
            "errors": ["fixture_missing_listing_body"],
            "callback_errors": [],
            "fixture_source": fixture["listing"]["capture_source"],
        }
    else:
        execution = run_fixture_candidate(
            str(candidate),
            fixture,
            allowed_domains=list(
                (state.get("validation") or {}).get(
                    "allowed_domains"
                )
                or []
            ),
        )
        try:
            result = json.loads(execution.stdout)
        except (TypeError, json.JSONDecodeError):
            result = {
                "passed": False,
                "errors": [
                    "fixture_runner_invalid_output",
                    str(execution.stderr or "")[-1000:],
                ],
                "callback_errors": [],
            }
        result["exit_code"] = execution.exit_code
        result["timed_out"] = execution.timed_out
        result["fixture_source"] = fixture["listing"][
            "capture_source"
        ]
    return {
        "fixture_result": result,
        "candidate_path": str(candidate),
        "run_id": run_id,
        "status": "testing" if result.get("passed") else "validating",
    }
