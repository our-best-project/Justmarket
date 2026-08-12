"""依真實證據選擇策略並產生候選 spider。"""

from __future__ import annotations

import ast
import json
import re

from ..config import GENERATION_PROVIDER
from ..state import SpiderForgeState
from .evidence import _discover_detail_urls, _is_replayable_article_api
from .materials import compile_generation_materials
from .prompts import _SPIDER_CONTRACT, _STRATEGY_SCHEMA, CODE_SYSTEM

_FORBIDDEN_PAGECOUNT_OVERRIDE = re.compile(
    r"""(?m)^[ \t]*["']CLOSESPIDER_PAGECOUNT["'][ \t]*:[^\r\n]*(?:\r?\n|$)"""
)

def strategy_decision(state: SpiderForgeState) -> dict:
    report = state.get("recon_report") or {}
    structured_evidence = [
        *(report.get("api_candidates") or []),
        *(report.get("feed_candidates") or []),
    ]
    structured_evidence.sort(
        key=lambda candidate: (
            _is_replayable_article_api(candidate),
            int(candidate.get("entry_link_overlap_count") or 0),
            candidate.get("discovery_source") == "document_alternate",
            int(candidate.get("article_record_count") or 0),
        ),
        reverse=True,
    )
    viable_candidates = [
        candidate
        for candidate in structured_evidence
        if _is_replayable_article_api(candidate)
    ]
    api_evidence = [
        {
            "format": candidate.get("structured_format") or "json",
            "method": candidate.get("method"),
            "url": candidate.get("url"),
            "status": candidate.get("status"),
            "json_shape": candidate.get("json_shape"),
            "article_record_count": candidate.get("article_record_count", 0),
            "discovery_source": candidate.get("discovery_source"),
            "entry_link_overlap_count": candidate.get(
                "entry_link_overlap_count", 0
            ),
            "record_detection": candidate.get("record_detection"),
            "request_post_data": candidate.get("request_post_data"),
            "body_excerpt": (candidate.get("body_excerpt") or "")[:800],
        }
        for candidate in structured_evidence[:15]
    ]
    entry = {
        "requested_url": state["site_url"],
        "final_url": report.get("final_url"),
        "canonical_url": report.get("canonical_url"),
        "browser_status": report.get("http_status"),
        "http_status": report.get("http_entry_sample", {}).get("status"),
        "access_assessment": report.get("access_assessment"),
        "document_chain": (report.get("document_chain") or [])[:10],
        "http_redirect_chain": (
            report.get("http_entry_sample", {}).get("redirect_chain") or []
        )[:10],
    }
    discovered_article_links = _discover_detail_urls(state, report, limit=10)
    if discovered_article_links and not viable_candidates:
        result = {
            "strategy": "dom",
            "chosen_api": "",
            "confidence": 1.0,
            "reason": (
                "確定性證據：沒有可重播結構化來源，且已觀察到符合驗證規則的"
                "真實文章連結；使用 HTML／瀏覽器明細路徑。"
            ),
            "evidence_enforced": True,
            "decision_method": "deterministic",
        }
        return {
            "strategy": "dom",
            "strategy_detail": result,
            "status": "generating",
        }

    from ..clients.judge import judge

    system = (
        "你是新聞/政策來源的抓取策略決策器，只輸出 JSON。"
        "只有已捕獲 response body、article_record_count>0、且 GET 或有 request_post_data "
        "的 JSON/RSS/Atom 才算可重播結構化來源；否則禁止因 URL 看似 API 就選 api。"
        "列表 API 加 HTML 明細選 hybrid；HTML 有真實文章 links 選 dom。"
        "browser_blocked_http_ok 表示應用 plain HTTP HTML，不應強迫 Playwright。"
        "chosen_api 必須逐字等於候選 URL。"
    )
    user = (
        f"目標契約：{json.dumps(state['target_schema'], ensure_ascii=False)}\n"
        f"入口證據：{json.dumps(entry, ensure_ascii=False)}\n"
        f"Recon error：{report.get('recon_error')}\n"
        f"API evidence：{json.dumps(api_evidence, ensure_ascii=False)[:7000]}\n"
        f"Validated article links："
        f"{json.dumps(discovered_article_links, ensure_ascii=False)[:3500]}\n"
        f"ARIA：{(report.get('aria_snapshot') or '')[:1500]}"
    )
    result = judge(system=system, user=user, schema=_STRATEGY_SCHEMA)
    if viable_candidates:
        strongest = viable_candidates[0]
        content_contract = (
            (state.get("target_schema") or {}).get("fields") or {}
        ).get("content") or {}
        requires_full_content = bool(
            content_contract.get("required")
            and (
                content_contract.get("mode") == "full"
                or (state.get("target_schema") or {}).get("content_scope")
                == "full"
            )
        )
        enforce_hybrid = (
            result.get("strategy") == "dom"
            or (
                strongest.get("structured_format") == "rss_or_atom"
                and requires_full_content
            )
        )
        if (
            str(result.get("chosen_api") or "") != str(strongest.get("url") or "")
            or enforce_hybrid
        ):
            result = {
                **result,
                "strategy": "hybrid" if enforce_hybrid else result.get("strategy"),
                "chosen_api": strongest["url"],
                "confidence": (
                    0.9
                    if result.get("strategy") == "dom"
                    else result.get("confidence")
                ),
                "reason": (
                    "Evidence enforcement: use the replayable candidate with the "
                    "strongest overlap with entry-page article links. "
                    + (
                        "RSS supplies discovery metadata; HTML detail pages are "
                        "required by the full-content contract."
                        if enforce_hybrid
                        else ""
                    )
                ).strip(),
                "evidence_enforced": True,
            }
    chosen_api = str(result.get("chosen_api") or "")
    chosen_candidate = next(
        (
            candidate
            for candidate in [
                *(report.get("api_candidates") or []),
                *(report.get("feed_candidates") or []),
            ]
            if candidate.get("url") == chosen_api
        ),
        {},
    )
    if result.get("strategy") in {"api", "hybrid"} and not (
        chosen_candidate and _is_replayable_article_api(chosen_candidate)
    ):
        result = {
            **result,
            "strategy": "dom",
            "chosen_api": "",
            "confidence": min(float(result.get("confidence") or 0), 0.65),
            "reason": (
                f"{result.get('reason', '')} "
                "[Evidence enforcement: no replayable captured article API; use HTML.]"
            ).strip(),
            "evidence_enforced": True,
        }
    if result.get("strategy") == "dom":
        result["chosen_api"] = ""
    try:
        confidence = float(result.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if 1 < confidence <= 100:
        confidence /= 100
    result["confidence"] = max(0.0, min(confidence, 1.0))
    return {"strategy": result["strategy"], "strategy_detail": result, "status": "generating"}


def _contract(state: SpiderForgeState) -> str:
    schema = state["target_schema"]
    return _SPIDER_CONTRACT.format(
        source_prefix=state["source_prefix"],
        site_name=state["site_name"],
        source_type=schema.get("source_type", "media"),
        content_scope=schema.get("content_scope", "summary_only"),
        max_content_chars=schema.get("fields", {})
        .get("content", {})
        .get("max_chars", 6000),
    )


def _safe_generate(prompt: str, provider: str) -> tuple[str | None, str | None]:
    from ..clients.coder import complete, extract_code

    try:
        return extract_code(complete(prompt, system=CODE_SYSTEM, provider=provider)), None
    except Exception as exc:
        return None, str(exc)


def _generation_prompt(state: SpiderForgeState) -> tuple[str, dict]:
    materials = compile_generation_materials(state["evidence_pack"])
    prompt = (
        "依下列「確定性材料編譯器」輸出寫一支可直接執行的 Scrapy spider。"
        "材料已去重且只保留已選來源；不得回頭猜未列出的 feed、API 或 selector。\n"
        "RSS／Atom 若有 XML namespace，必須依 replay_exchange 的原文處理；"
        "列表已提供 published_at 時要經 request.meta 傳給明細，不得以 HTML 日期覆蓋精確時間。\n"
        "DOM selector 只能使用 dom_samples 中逐字存在的 class／id；同站樣本版型不同時，"
        "使用少量、可證明的 fallback，不得選整個 main 或所有通用 content 容器。\n"
        "翻頁照 pagination；type=none_detected 就只抓第 1 頁。"
        "請求沿用 replay_headers，不要重複定義 headers。\n\n"
        "【編譯後材料】\n"
        f"{json.dumps(materials, ensure_ascii=False)}\n\n"
        f"{_contract(state)}"
    )
    return prompt, materials


def generate_spider(state: SpiderForgeState) -> dict:
    prompt, materials = _generation_prompt(state)
    code, error = _safe_generate(prompt, GENERATION_PROVIDER)
    return {
        "spider_code": code or "",
        "generation_error": error,
        "generation_materials": materials.get("material_budget") or {},
        "status": "testing",
    }


def _literal_class_assignments(class_node: ast.ClassDef) -> dict[str, object]:
    assignments: dict[str, object] = {}
    for node in class_node.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value_node = node.value
        if value_node is None:
            continue
        try:
            value = ast.literal_eval(value_node)
        except (ValueError, TypeError):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                assignments[target.id] = value
    return assignments


def _request_uses_playwright(call: ast.Call) -> bool:
    function_name = (
        call.func.attr
        if isinstance(call.func, ast.Attribute)
        else call.func.id
        if isinstance(call.func, ast.Name)
        else ""
    )
    if function_name != "Request":
        return False
    meta = next((kw.value for kw in call.keywords if kw.arg == "meta"), None)
    if not isinstance(meta, ast.Dict):
        return False
    return any(
        isinstance(key, ast.Constant)
        and key.value == "playwright"
        and isinstance(value, ast.Constant)
        and value.value is True
        for key, value in zip(meta.keys, meta.values)
    )


def preflight_generated_code(state: SpiderForgeState) -> dict:
    """人工／pipeline 可共用的確定性產碼預檢；只做可證明安全的機械修正。"""
    original_code = str(state.get("spider_code") or "")
    code, pagecount_removed = _FORBIDDEN_PAGECOUNT_OVERRIDE.subn(
        "", original_code
    )
    fixes = []
    if pagecount_removed:
        fixes.append("移除會把文章明細計入上限的 CLOSESPIDER_PAGECOUNT")
    errors: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        errors.append(f"syntax_error:{exc.msg}:line_{exc.lineno}")
        tree = None

    spider_node: ast.ClassDef | None = None
    if tree is not None:
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if any(
                (
                    isinstance(base, ast.Attribute)
                    and base.attr == "Spider"
                )
                or (
                    isinstance(base, ast.Name)
                    and base.id == "Spider"
                )
                for base in node.bases
            ):
                spider_node = node
                break
    if spider_node is None:
        errors.append("missing_scrapy_spider_class")
        assignments: dict[str, object] = {}
    else:
        assignments = _literal_class_assignments(spider_node)

    schema = state.get("target_schema") or {}
    expected = {
        "name": state.get("source_prefix"),
        "source_prefix": state.get("source_prefix"),
        "source": state.get("site_name"),
        "source_type": schema.get("source_type", "media"),
        "content_scope": schema.get("content_scope", "summary_only"),
    }
    if (
        spider_node is not None
        and "source_prefix" not in assignments
        and assignments.get("name") == expected["source_prefix"]
    ):
        name_assignment = next(
            (
                node
                for node in spider_node.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == "name"
                    for target in (
                        node.targets
                        if isinstance(node, ast.Assign)
                        else [node.target]
                    )
                )
            ),
            None,
        )
        if name_assignment is not None:
            lines = code.splitlines(keepends=True)
            source_line = lines[name_assignment.lineno - 1]
            indent = re.match(r"[ \t]*", source_line).group(0)
            newline = "\r\n" if source_line.endswith("\r\n") else "\n"
            lines.insert(
                name_assignment.end_lineno,
                f"{indent}source_prefix = {expected['source_prefix']!r}{newline}",
            )
            code = "".join(lines)
            fixes.append("依已驗證的 spider name 補上相同 source_prefix")
            tree = ast.parse(code)
            spider_node = next(
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef)
                and any(
                    (
                        isinstance(base, ast.Attribute)
                        and base.attr == "Spider"
                    )
                    or (
                        isinstance(base, ast.Name)
                        and base.id == "Spider"
                    )
                    for base in node.bases
                )
            )
            assignments = _literal_class_assignments(spider_node)
    for field, value in expected.items():
        if assignments.get(field) != value:
            errors.append(f"class_attribute_mismatch:{field}")

    if "CLOSESPIDER_PAGECOUNT" in code:
        errors.append("forbidden_setting:CLOSESPIDER_PAGECOUNT")
    if "playwright_include_page" in code:
        errors.append("forbidden_playwright_page_handle")

    if tree is not None:
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] in {
                        "backend",
                        "news_crawler",
                        "spider_forge",
                    }:
                        errors.append(f"forbidden_project_import:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.split(".", 1)[0] in {
                    "backend",
                    "news_crawler",
                    "spider_forge",
                }:
                    errors.append(f"forbidden_project_import:{module}")

    requirements = set((state.get("evidence_pack") or {}).get("requirements") or [])
    if "browser_transport" in requirements:
        if "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler" not in code:
            errors.append("missing_browser_setting:DOWNLOAD_HANDLERS")
        if "twisted.internet.asyncioreactor.AsyncioSelectorReactor" not in code:
            errors.append("missing_browser_setting:TWISTED_REACTOR")
        playwright_requests = (
            sum(
                _request_uses_playwright(node)
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
            )
            if tree is not None
            else 0
        )
        if playwright_requests < 2:
            errors.append("browser_transport_not_applied_to_entry_and_detail")

    published_probe = (state.get("evidence_pack") or {}).get(
        "published_at_probe"
    ) or {}
    if published_probe.get("needs_timezone_completion"):
        if "zoneinfo" not in code or "ZoneInfo" not in code:
            errors.append("missing_stdlib_source_timezone")
        if re.search(r"(?m)^\s*(?:from\s+pytz|import\s+pytz)\b", code):
            errors.append("forbidden_dependency:pytz")

    unique_errors = list(dict.fromkeys(errors))
    return {
        "spider_code": code,
        "generation_preflight": {
            "passed": not unique_errors,
            "errors": unique_errors,
            "deterministic_fixes": fixes,
        },
        "status": "testing",
    }


# 生成/修復模型逾時或 5xx（provider_failure）累計重試上限：超過就死信，避免供應商
# 一直抖動時無限空轉。這條路徑不消耗 retry_count 修復額度（spec v2 §4）。
