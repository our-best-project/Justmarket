"""在模型呼叫前執行確定性可行性分流。"""

from __future__ import annotations

from ..shared.evidence import (
    _discover_detail_urls,
    _is_replayable_article_api,
    _matches_validation_url,
)
from ..shared.summaries import evidence_summary
from ..state import SpiderForgeState


def feasibility_triage(state: SpiderForgeState) -> dict:
    """recon 後、生成前的確定性可行性分流（D1：KILL 立即轉死信、不生成）。

    保守原則：只有明確確定性訊號才判 KILL；其餘一律 FEASIBLE，讓 strategy_decision
    / 生成迴圈照樣嘗試——寧可漏殺可做站，不可誤殺（誤殺會直接壓低成功率）。
    """
    report = state.get("recon_report") or {}
    structured = [
        *(report.get("api_candidates") or []),
        *(report.get("feed_candidates") or []),
    ]
    replayable = [c for c in structured if _is_replayable_article_api(c)]
    has_sample_urls = bool(state.get("sample_urls"))
    browser_transport_required = (
        report.get("access_assessment") == "browser_required_http_blocked"
    )

    def _kill(cls: str, reason: str) -> dict:
        return {
            "feasibility": {
                "class": cls,
                "reason": reason,
                "evidence_summary": evidence_summary(report),
            },
            "failure_class": cls,
            "status": "triaging",
        }

    # KILL_policy_kill：browser_probe 已確定的挑戰頁字樣，或明確付費牆狀態碼。
    # 單純 401/403（灰色登入牆，D2 規定要照樣試）不算——soft_block_detected 是
    # browser_probe.py 對 title+body_text 掃 access denied/forbidden/captcha/
    # verify you are human/cloudflare 的結果，比裸狀態碼更確定。
    http_entry_status = (report.get("http_entry_sample") or {}).get("status")
    if report.get("soft_block_detected") is True or 402 in {
        report.get("http_status"),
        http_entry_status,
    }:
        return _kill(
            "KILL_policy_kill",
            f"soft_block_detected={report.get('soft_block_detected')} "
            f"http_status={report.get('http_status')} http_entry_status={http_entry_status}",
        )

    # recon 本身失敗（探測出錯）是不確定訊號，以下三類一律不判定，保守放行。
    recon_incomplete = bool(report.get("recon_error") or report.get("navigation_error"))
    if not recon_incomplete:
        html_links = _discover_detail_urls(state, report, limit=1)

        # KILL_signature_required：唯一結構化路徑是需簽章/nonce 的 POST
        # （request_post_data 含 "<redacted>" 是 browser_probe._redact_payload
        # 對 token/nonce/csrf 等 key 的遮蔽產物），且沒有可重播 GET 候選、
        # 沒有 HTML 明細連結、使用者也沒給已知樣本頁。
        signature_locked = [
            c
            for c in structured
            if str(c.get("method") or "GET").upper() != "GET"
            and "<redacted>" in str(c.get("request_post_data") or "")
        ]
        if (
            signature_locked
            and not replayable
            and not html_links
            and not has_sample_urls
        ):
            return _kill(
                "KILL_signature_required",
                f"signature_like_post_candidates={[c.get('url') for c in signature_locked][:3]} "
                "無可重播 GET 候選、無 HTML 明細連結、無 sample_urls",
            )

        # KILL_js_required：瀏覽器渲染後找得到文章連結，但原始 HTTP（未執行 JS）
        # 完全沒有——這個「有 vs 沒有」的對比才是確定性訊號，不是單純猜「這站用了 JS」。
        browser_links = [
            row
            for row in (report.get("link_samples") or [])
            if _matches_validation_url(str(row.get("url") or ""), state)
        ]
        raw_links = [
            row
            for row in (report.get("http_entry_sample") or {}).get("link_samples") or []
            if _matches_validation_url(str(row.get("url") or ""), state)
        ]
        if (
            browser_links
            and not raw_links
            and not replayable
            and not (report.get("feed_candidates"))
            and not has_sample_urls
            and not browser_transport_required
        ):
            return _kill(
                "KILL_js_required",
                f"browser_rendered_links={len(browser_links)} raw_http_links=0 "
                "無底層 XHR/feed 可重播、無 sample_urls",
            )

        # KILL_discovery_empty：結構化證據與 HTML 連結（含 sample_urls／browser／
        # raw http／feed）皆無。
        if not replayable and not html_links:
            return _kill(
                "KILL_discovery_empty",
                "無 article-like API/feed 候選，也找不到任何 HTML 明細連結",
            )

    feasibility_class = (
        "FEASIBLE_API"
        if replayable
        else "FEASIBLE_BROWSER"
        if browser_transport_required
        else "FEASIBLE_HTML"
    )
    return {
        "feasibility": {
            "class": feasibility_class,
            "reason": (
                "存在可重播結構化候選"
                if replayable
                else "plain HTTP 被擋，但公開瀏覽器已取得符合規則的文章連結"
                if browser_transport_required
                else "無可重播 API，但證據不足以確定性判 KILL，保守放行嘗試 HTML/軸樹策略"
            ),
            "evidence_summary": evidence_summary(report),
        },
        "status": "triaging",
    }


# ════════════════════════ 策略、生成、診斷 ════════════════════════
