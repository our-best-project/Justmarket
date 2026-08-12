"""共用資料品質驗證規則。

全部是確定性規則，不叫 LLM。回傳四布林漏斗 + valid unique 計數 + 每種拒絕原因計數，
供 quality gate 與 quarantine 用。設計目標：讓已知假成功（UDN 股價指數頁、
3 筆相同垃圾、not-a-url、空 content、無時區日期、1785 年、None 欄位）**確定被拒**。

驗證設定（每站，來自 site_queue.yaml 的 validation 區塊，缺就用寬鬆預設）：
  allowed_domains        允許的 host（含子網域）
  article_url_patterns   文章頁 URL 必須命中其一（regex）；缺則跳過此檢查
  excluded_url_patterns  命中即拒（列表頁/查詢頁/指數頁）
  canonical_query_params 構成文章身分、正規化時必須保留的 query key
  identity_field         無單篇 permalink 時，以此欄位判定資料列唯一性
  min_content_chars      content 最短長度（預設 30）
  max_content_chars      content 最長長度；媒體忠實摘錄預設由 graph 設為 6000
  max_age_days           新鮮度窗（None 不限）
  min_valid_items        站要過關的最少 valid unique 筆數（預設 5）
  min_unique_ratio       unique / valid 下限（預設 0.8）
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

_MIN_CONTENT_DEFAULT = 30
_MIN_VALID_DEFAULT = 5
_YEAR_MIN = 2000
_VALID_RATE_FLOOR = 0.6
_UNIQUE_RATE_FLOOR = 0.8
_NAV_TITLES = {"首頁", "home", "登入", "註冊", "會員中心", "more", "看更多", "loading"}
_SOFT_BLOCK_PATTERNS = (
    r"\baccess denied\b",
    r"\brequest (?:was )?blocked\b",
    r"\battention required\b.*\bcloudflare\b",
    r"\bverify (?:that )?you are human\b",
    r"\benable (?:java\s*script|javascript|cookies?)\b",
    r"拒絕存取",
    r"請確認您不是機器人",
)


def canonical_url(url: str, cfg: dict | None = None) -> str:
    """正規化 URL；只保留站點明確宣告具有文章身分語意的 query 參數。"""
    try:
        s = urlsplit(str(url).strip())
        host = (s.hostname or "").lower()
        port = s.port
        if port and not ((s.scheme.lower() == "https" and port == 443) or
                         (s.scheme.lower() == "http" and port == 80)):
            host = f"{host}:{port}"
        keep = {str(k).lower() for k in (cfg or {}).get("canonical_query_params", [])}
        query = urlencode(
            sorted((k.lower(), v) for k, v in parse_qsl(s.query, keep_blank_values=True)
                   if k.lower() in keep)
        )
        return urlunsplit((s.scheme.lower(), host, s.path.rstrip("/"), query, ""))
    except Exception:
        return str(url).strip()


def _host_allowed(host: str, domains: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _url_ok(url: Any, cfg: dict) -> tuple[bool, str]:
    if not url or not isinstance(url, str):
        return False, "url_missing"
    try:
        p = urlparse(url)
    except Exception:
        return False, "url_unparseable"
    if p.scheme not in ("http", "https"):
        return False, "url_not_http"
    if not p.hostname:
        return False, "url_no_host"
    domains = cfg.get("allowed_domains")
    if domains and not _host_allowed(p.hostname.lower(), [d.lower() for d in domains]):
        return False, "url_foreign_domain"
    path_and_query = p.path + (f"?{p.query}" if p.query else "")
    for x in cfg.get("excluded_url_patterns") or []:
        if re.search(x, path_and_query, flags=re.IGNORECASE):
            return False, "url_excluded_pattern"
    pats = cfg.get("article_url_patterns")
    if pats and not any(re.search(x, p.path, flags=re.IGNORECASE) for x in pats):
        return False, "url_not_article"
    return True, ""


def _title_ok(title: Any) -> tuple[bool, str]:
    if not title or not isinstance(title, str):
        return False, "title_missing"
    t = title.strip()
    if not t:
        return False, "title_empty"
    if t.lower() in _NAV_TITLES:
        return False, "title_nav_label"
    if len(t) < 4:
        return False, "title_too_short"
    return True, ""


def _content_ok(content: Any, cfg: dict) -> tuple[bool, str]:
    txt = (content or "")
    if not isinstance(txt, str):
        return False, "content_missing"
    txt = txt.strip()
    if not txt:
        return False, "content_empty"
    sample = txt[:5000]
    if any(re.search(p, sample, flags=re.IGNORECASE | re.DOTALL) for p in _SOFT_BLOCK_PATTERNS):
        return False, "content_soft_block"
    if len(txt) < cfg.get("min_content_chars", _MIN_CONTENT_DEFAULT):
        return False, "content_too_short"
    max_chars = cfg.get("max_content_chars")
    if max_chars is not None and len(txt) > int(max_chars):
        return False, "content_too_long"
    return True, ""


def _date_ok(value: Any, cfg: dict) -> tuple[bool, str]:
    if not value or not isinstance(value, str):
        return False, "date_missing"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return False, "date_unparseable"
    if dt.tzinfo is None:
        return False, "date_naive_no_tz"
    now = datetime.now(UTC)
    if not (_YEAR_MIN <= dt.year <= now.year + 1):
        return False, "date_year_out_of_range"
    max_age = cfg.get("max_age_days")
    if max_age is not None:
        age_days = (now - dt).days
        if age_days > max_age:
            return False, "date_too_old"
        if age_days < -2:
            return False, "date_future"
    return True, ""


def validate_items(items: list[dict], cfg: dict | None = None) -> dict:
    """對抓到的 items 做品質驗證。回傳 pass + 四布林 flags + 計數 + 拒絕原因。"""
    cfg = cfg or {}
    per_item = []
    valid_identities = []
    rejected_samples = []

    for index, it in enumerate(items):
        reasons = []
        for ok, reason in (
            _url_ok(it.get("url"), cfg),
            _title_ok(it.get("title")),
            _content_ok(it.get("content"), cfg),
            _date_ok(it.get("published_at"), cfg),
        ):
            if not ok:
                reasons.append(reason)
        identity_field = cfg.get("identity_field")
        if identity_field:
            identity = it.get(identity_field)
            if not isinstance(identity, str) or not identity.strip():
                reasons.append("identity_missing")
                identity = ""
            else:
                identity = identity.strip()
        else:
            identity = canonical_url(it.get("url"), cfg)

        valid = not reasons
        per_item.append({"valid": valid, "reasons": reasons})
        if valid:
            valid_identities.append(identity)
        elif len(rejected_samples) < 3:
            rejected_samples.append({
                "index": index,
                "reasons": reasons,
                "item": {
                    key: (value[:500] if isinstance(value, str) else value)
                    for key, value in it.items()
                    if key in {"title", "url", "content", "published_at", "source_record_id"}
                },
            })

    n = len(items)
    n_valid = len(valid_identities)
    n_unique = len(set(valid_identities))
    min_valid = cfg.get("min_valid_items", _MIN_VALID_DEFAULT)
    valid_rate = (n_valid / n) if n else 0.0
    unique_rate = (n_unique / n_valid) if n_valid else 0.0
    min_unique_rate = cfg.get("min_unique_ratio", _UNIQUE_RATE_FLOOR)

    discovery_ok = n > 0
    retrieval_ok = any(_content_ok(it.get("content"), cfg)[0] for it in items)
    extraction_ok = n_valid > 0
    quality_ok = (
        n_unique >= min_valid
        and valid_rate >= cfg.get("min_valid_ratio", _VALID_RATE_FLOOR)
        and unique_rate >= min_unique_rate
    )
    passed = discovery_ok and retrieval_ok and extraction_ok and quality_ok

    return {
        "pass": passed,
        "flags": {
            "discovery_ok": discovery_ok,
            "retrieval_ok": retrieval_ok,
            "extraction_ok": extraction_ok,
            "quality_ok": quality_ok,
        },
        "item_count": n,
        "valid_count": n_valid,
        "valid_item_indices": [
            index for index, result in enumerate(per_item) if result["valid"]
        ],
        "unique_valid_count": n_unique,
        "valid_rate": round(valid_rate, 3),
        "unique_ratio": round(unique_rate, 3),
        "min_valid_items": min_valid,
        "min_unique_ratio": min_unique_rate,
        "reject_reasons": dict(Counter(r for p in per_item for r in p["reasons"])),
        "rejected_samples": rejected_samples,
    }
