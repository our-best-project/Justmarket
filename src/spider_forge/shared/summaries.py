"""跨節點與輸出層共用的去敏摘要。"""


def evidence_summary(report: dict) -> dict:
    """只保留可行性判斷與死信歸檔需要的欄位。"""
    http_entry = report.get("http_entry_sample") or {}
    return {
        "http_status": report.get("http_status"),
        "http_entry_status": http_entry.get("status"),
        "access_assessment": report.get("access_assessment"),
        "soft_block_detected": report.get("soft_block_detected"),
        "navigation_error": report.get("navigation_error"),
        "recon_error": report.get("recon_error"),
        "api_candidate_count": len(report.get("api_candidates") or []),
        "feed_candidate_count": len(report.get("feed_candidates") or []),
        "link_sample_count": len(report.get("link_samples") or []),
        "http_entry_link_sample_count": len(http_entry.get("link_samples") or []),
        "title": report.get("title"),
    }
