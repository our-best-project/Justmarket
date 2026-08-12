"""Crawler Runtime 的設定與網域白名單測試。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import scrapy
from scrapy.exceptions import IgnoreRequest

_RUNTIME_DIR = Path(__file__).resolve().parents[1]
if str(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_DIR))


def t_scrapy_settings_use_runtime_identity_and_disable_db():
    from news_crawler import policy, settings

    ok = (
        settings.USER_AGENT == policy.BROWSER_USER_AGENT
        and settings.DEFAULT_REQUEST_HEADERS.get("X-Purpose")
        == "academic course exercise, non-commercial"
        and settings.ITEM_PIPELINES == {}
    )
    return ok, (
        f"ua_is_browser={settings.USER_AGENT == policy.BROWSER_USER_AGENT} "
        f"pipelines={settings.ITEM_PIPELINES}"
    )


def t_runtime_enforces_control_plane_domain_allowlist():
    from news_crawler.middlewares import AllowedDomainsMiddleware

    previous = os.environ.get("SPIDERFORGE_ALLOWED_DOMAINS")
    os.environ["SPIDERFORGE_ALLOWED_DOMAINS"] = "example.com"
    try:
        middleware = AllowedDomainsMiddleware()
        allowed = middleware.process_request(scrapy.Request("https://news.example.com/a"))
        try:
            middleware.process_request(scrapy.Request("https://evil.invalid/a"))
            blocked = False
        except IgnoreRequest:
            blocked = True
    finally:
        if previous is None:
            os.environ.pop("SPIDERFORGE_ALLOWED_DOMAINS", None)
        else:
            os.environ["SPIDERFORGE_ALLOWED_DOMAINS"] = previous
    return allowed is None and blocked, f"allowed={allowed is None} blocked={blocked}"


TESTS = [
    t_scrapy_settings_use_runtime_identity_and_disable_db,
    t_runtime_enforces_control_plane_domain_allowlist,
]
