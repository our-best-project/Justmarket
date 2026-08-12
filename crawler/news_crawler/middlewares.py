"""由控制面注入的網域 allowlist；不信任 candidate 自己宣告的 allowed_domains。"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from scrapy.exceptions import IgnoreRequest


class AllowedDomainsMiddleware:
    def __init__(self) -> None:
        raw = os.environ.get("SPIDERFORGE_ALLOWED_DOMAINS", "")
        self.allowed = {
            domain.strip().lower().rstrip(".")
            for domain in raw.split(",")
            if domain.strip()
        }

    def process_request(self, request):
        if not self.allowed:
            return None
        host = (urlsplit(request.url).hostname or "").lower().rstrip(".")
        if any(host == domain or host.endswith("." + domain) for domain in self.allowed):
            return None
        raise IgnoreRequest(f"runtime_domain_not_allowed:{host}")
