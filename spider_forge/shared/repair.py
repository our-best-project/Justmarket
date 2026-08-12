"""診斷失敗並執行有界修復。"""

from __future__ import annotations

import json
import re

from ..config import FINAL_REPAIR_PROVIDER, REPAIR_PROVIDER
from ..state import SpiderForgeState, normalize_failure_class
from .generation import _contract, _safe_generate
from .materials import compile_generation_materials
from .prompts import _DIAGNOSE_SCHEMA

_PROVIDER_RETRY_MAX = 2
# 付費牆狀態碼在 crawl log 的確定性訊號；單純 401/403 是灰色登入牆（D2 照樣試），
# 不在此列——只有 402 這種「要付費才給看」才在 diagnose 階段判 policy_kill。
_LIVE_PAYWALL_MARKERS = (
    re.compile(r"\(402\)"),
    re.compile(r"402 payment required", re.IGNORECASE),
)


def diagnose_failure(state: SpiderForgeState) -> dict:
    from ..clients.judge import judge

    # provider_failure（spec v2 §4）：生成/修復的 LLM 呼叫本身逾時或 5xx，不是爬蟲程式
    # 的錯——不叫診斷模型、不消耗 retry_count 修復額度，只有界地重試供應商。
    generation_error = state.get("generation_error")
    if generation_error:
        history = list(state.get("error_signature_history", []))
        history.append("provider_failure")
        return {
            "diagnosis": {
                "failure_type": "provider_failure",
                "failure_class": "provider_failure",
                "evidence": str(generation_error)[:500],
                "suggested_fix": "重試生成/修復供應商；此為供應商層問題，非爬蟲程式碼問題。",
                "error_signature": "provider_failure",
            },
            "failure_class": "provider_failure",
            "provider_retry_count": state.get("provider_retry_count", 0) + 1,
            "error_signature_history": history,
            # 刻意不回傳 retry_count：保留原值，供應商問題不吃修復額度。
            "status": "repairing",
        }

    preflight = state.get("generation_preflight") or {}
    if preflight and not preflight.get("passed"):
        errors = list(preflight.get("errors") or [])
        history = list(state.get("error_signature_history", []))
        history.append("generation_preflight_failed")
        return {
            "diagnosis": {
                "failure_type": "generation_contract",
                "failure_class": "code_error",
                "evidence": errors,
                "suggested_fix": (
                    "逐項修正產碼契約錯誤；保留已驗證策略與 selector，"
                    "不要以移除必要欄位或改寫驗證規則規避。"
                ),
                "error_signature": "generation_preflight_failed",
            },
            "failure_class": "code_error",
            "retry_count": state.get("retry_count", 0) + 1,
            "error_signature_history": history,
            "status": "repairing",
        }

    fixture = state.get("fixture_result") or {}
    if fixture and not fixture.get("passed"):
        errors = list(fixture.get("errors") or [])
        callback_errors = list(
            fixture.get("callback_errors") or []
        )
        history = list(state.get("error_signature_history", []))
        history.append("fixture_gate_failed")
        return {
            "diagnosis": {
                "failure_type": "fixture_gate",
                "failure_class": "selector_schema",
                "evidence": {
                    "errors": errors,
                    "callback_errors": callback_errors,
                    "fixture_source": fixture.get("fixture_source"),
                },
                "suggested_fix": (
                    "只依編譯後材料修正列表、明細 selector、欄位或 request meta；"
                    "不得猜新端點，也不得放寬 fixture 驗證。"
                ),
                "error_signature": "fixture_gate_failed",
            },
            "failure_class": "selector_schema",
            "retry_count": state.get("retry_count", 0) + 1,
            "error_signature_history": history,
            "status": "repairing",
        }

    # block_page_200（spec v2 §3.3/§4）：content_block_gate 判為挑戰/錯誤頁。可修復類
    # （換傳輸/補瀏覽器 headers 重取一次），照 retry_count 迴圈；修不好耗盡額度就死信。
    if state.get("block_page_detected"):
        detection = state.get("block_detection") or {}
        history = list(state.get("error_signature_history", []))
        history.append("block_page_200")
        return {
            "diagnosis": {
                "failure_type": "block_page_200",
                "failure_class": "block_page_200",
                "evidence": f"content_block_gate 判定 block（{detection.get('method')}）：{detection}",
                "suggested_fix": "此頁疑為挑戰/錯誤頁；沿用 replay_headers 補瀏覽器等級 headers 重取，"
                "不要把 block 文字當內文抽出。",
                "error_signature": "block_page_200",
            },
            "failure_class": "block_page_200",
            "retry_count": state.get("retry_count", 0) + 1,
            "error_signature_history": history,
            "status": "repairing",
        }

    test = state.get("test_result") or {}
    validation = state.get("validation_result") or {}
    topic = state.get("topic_result") or {}
    error_text = "\n".join(
        [
            str(test.get("error") or ""),
            str(test.get("stderr_tail") or ""),
            str(test.get("stdout_tail") or ""),
        ]
    )

    # policy_kill（spec v2 §4）：live 階段才浮現的付費牆（402）。確定性短路，不叫診斷
    # 模型，直接死信——付費牆是硬線，不繞（D2）。
    if any(pattern.search(error_text) for pattern in _LIVE_PAYWALL_MARKERS):
        history = list(state.get("error_signature_history", []))
        history.append("http_402_paywall")
        return {
            "diagnosis": {
                "failure_type": "policy_kill",
                "failure_class": "policy_kill",
                "evidence": "Crawl log 出現 402 Payment Required——付費牆，不繞。",
                "suggested_fix": "付費牆為硬線；轉死信，不生成/不修復。",
                "error_signature": "http_402_paywall",
            },
            "failure_class": "policy_kill",
            "retry_count": state.get("retry_count", 0) + 1,
            "error_signature_history": history,
            "status": "repairing",
        }

    known_failures = (
        (
            "SelectorList' object has no attribute 'first",
            {
                "failure_type": "scrapy_api_misuse",
                "failure_class": "code_error",
                "evidence": "Scrapy SelectorList does not implement .first().",
                "suggested_fix": "Replace .first() with .get(), getall(), or [0] after a truth check.",
                "error_signature": "scrapy_selectorlist_first_invalid",
            },
        ),
        (
            "SyntaxError:",
            {
                "failure_type": "syntax_error",
                "failure_class": "code_error",
                "evidence": "Python raised SyntaxError before the spider could run.",
                "suggested_fix": "Return one complete syntactically valid Python spider file.",
                "error_signature": "syntax_error",
            },
        ),
        (
            "ModuleNotFoundError:",
            {
                "failure_type": "dependency_error",
                "failure_class": "code_error",
                "evidence": "Candidate imported a module unavailable in the sandbox.",
                "suggested_fix": "Use only Scrapy, Python stdlib, and ArticleItem.",
                "error_signature": "module_not_found",
            },
        ),
    )
    result = next(
        (diagnosis for marker, diagnosis in known_failures if marker in error_text),
        None,
    )
    system = (
        "你是爬蟲失敗診斷器，只輸出 JSON。error_signature 必須描述根因，"
        "例如 http_403、json_path_wrong、content_too_short、published_at_no_tz、syntax_error。"
    )
    user = (
        f"退出碼：{test.get('exit_code')}；item_count：{test.get('item_count')}\n"
        f"validation：{json.dumps(validation, ensure_ascii=False)[:6000]}\n"
        f"topic_gate：{json.dumps(topic, ensure_ascii=False)[:6000]}\n"
        f"stderr：{test.get('stderr_tail', '')[-1800:]}\n"
        f"stdout：{test.get('stdout_tail', '')[-800:]}"
    )
    if result is None:
        result = dict(judge(system=system, user=user, schema=_DIAGNOSE_SCHEMA))
        # 診斷模型不吐 failure_class；由確定性訊號補上路由用的可修復類別。
        result.setdefault("failure_class", _repairable_failure_class(error_text, result))
    else:
        result = dict(result)

    # intent-match（spec v2 §3.3）：結構欄位都過、只有主題整批漂走（relevant_ratio 很低），
    # 歸 wrong_section——不是抽取壞了，是抓到不相關分類的列表。沿用主題閘門既有的
    # Gemini 分類輸出判定，不另花一次 Gemini 呼叫。wrong_section 為可修復類（換列表區塊）。
    flags = validation.get("flags") or {}
    if (
        flags.get("quality_ok") is True
        and topic.get("status") == "scored"
        and topic.get("gate_pass") is False
        and float(topic.get("relevant_ratio") or 0.0) < 0.5
    ):
        result["failure_class"] = "wrong_section"

    history = list(state.get("error_signature_history", []))
    history.append(result.get("error_signature", "unknown"))
    return {
        "diagnosis": result,
        "failure_class": result.get("failure_class", "selector_schema"),
        "retry_count": state.get("retry_count", 0) + 1,
        "error_signature_history": history,
        "status": "repairing",
    }


def _repairable_failure_class(error_text: str, diagnosis: dict) -> str:
    """把非 KILL、非 provider 的失敗歸成可修復子類（僅供死信記錄與人工判讀，
    路由上三者一視同仁走 retry_count 修復迴圈）。"""
    signature = str(diagnosis.get("error_signature") or "")
    if "403" in error_text or signature in {"http_403", "transport_blocked"}:
        return "transport_blocked"
    return "selector_schema"


# 品質漏斗四階段（validators 的四布林），依序第一個 False 就是失敗落點。
_FUNNEL_STAGES = ("discovery_ok", "retrieval_ok", "extraction_ok", "quality_ok")
_STAGE_HINT = {
    "crawl_did_not_finish": "crawl 沒有正常結束（傳輸/語法層）：先讓程式能跑完再談抽取。"
    "browser 403 但 plain HTTP=200 就用 replay_headers + Scrapy HTTP，別硬切 playwright。",
    "discovery": "crawl 跑完但 0 item：列表 selector / JSON path 沒對上 recon 觀察到的結構——"
    "回去對 api_sample / discovered_detail_urls 的真實形狀，不要另猜路徑。",
    "retrieval": "有抓到列表但內文取回失敗（content 空/被擋）：明細頁請求或內文 selector 錯，"
    "對 detail_samples 的真實 DOM 修。",
    "extraction": "有 item 但無一通過欄位驗證：看 reject_reasons——url 不符 pattern 多半是"
    "permalink 拼接錯或抓到列表頁；date_* 多半是沒照 published_at_probe 補時區。",
    "quality": "欄位大致對但唯一有效筆數不足/重複率過高：翻頁沒生效或抓到重複列，對 pagination 修。",
    "topic": "結構過了但主題不符（列表抓到不相關分類）：換更精準的列表來源/區塊，別放寬驗證。",
    "block_page": "抓到的其實是挑戰/錯誤頁（block_page_200）：沿用 replay_headers 補瀏覽器等級 "
    "headers 重取；不要把 block 文字當內文抽出。修不好就是硬擋，別打軍備競賽。",
    "wrong_section": "抓到的是不相關分類的列表（wrong_section）：改抓目標主題（台股財經/政策）"
    "的列表 URL 或區塊，不要放寬主題驗證來硬過。",
    "fixture": "保存 response 的離線 callback 驗證未通過：依 errors 與 callback_errors "
    "修正列表、明細 selector、request meta 或欄位契約；不要改抓新來源。",
}


def _repair_feedback(state: SpiderForgeState) -> dict:
    """確定性失敗定位：階段落點 + recon-vs-run 差異 + 前幾大拒絕原因（spec v2 §3.5）。
    目的是讓修復模型「定位而非猜」——把它該看哪裡、哪一階段壞了講白。"""
    test = state.get("test_result") or {}
    validation = state.get("validation_result") or {}
    recon = state.get("recon_report") or {}
    pack = state.get("evidence_pack") or {}
    flags = validation.get("flags") or {}
    topic = state.get("topic_result") or {}
    failure_class = normalize_failure_class(
        state.get("failure_class") or (state.get("diagnosis") or {}).get("failure_class")
    )

    fixture = state.get("fixture_result") or {}
    preflight = state.get("generation_preflight") or {}
    if (fixture and not fixture.get("passed")) or (preflight and not preflight.get("passed")):
        stage = "fixture"
    elif state.get("block_page_detected") or failure_class == "block_page_200":
        stage = "block_page"
    elif failure_class == "wrong_section":
        stage = "wrong_section"
    elif not test.get("passed"):
        stage = "crawl_did_not_finish"
    else:
        stage = next(
            (s[:-3] for s in _FUNNEL_STAGES if flags.get(s) is False),
            "topic" if str(topic.get("status") or "").startswith(("scored", "no_valid")) else "quality",
        )

    recon_entry_status = (recon.get("http_entry_sample") or {}).get("status")
    recon_links = len(
        (pack.get("entry_observation", {}) or {}).get("link_samples")
        or recon.get("link_samples")
        or []
    )
    reject_reasons = validation.get("reject_reasons") or {}
    top_rejects = sorted(reject_reasons.items(), key=lambda kv: kv[1], reverse=True)[:5]

    recon_vs_run = {
        "recon_access_assessment": recon.get("access_assessment"),
        "recon_browser_status": recon.get("http_status"),
        "recon_entry_http_status": recon_entry_status,
        "recon_article_links_seen": recon_links,
        "recon_replayable_api": bool((pack.get("api_sample") or {}).get("body_excerpt")),
        "run_finished": bool(test.get("passed")),
        "run_exit_code": test.get("exit_code"),
        "run_timed_out": test.get("timed_out"),
        "run_item_count": test.get("item_count"),
        "run_valid_count": validation.get("valid_count"),
        "run_unique_valid_count": validation.get("unique_valid_count"),
    }
    diff_notes = []
    if stage == "crawl_did_not_finish" and recon_entry_status == 200:
        diff_notes.append(
            "recon 用 plain HTTP 拿到 200，但實際 crawl 沒跑完 → 傳輸層在 spider 內被改壞，沿用 replay_headers。"
        )
    if stage == "discovery" and (recon_links or recon_vs_run["recon_replayable_api"]):
        diff_notes.append(
            f"recon 找到 {recon_links} 個文章連結且 replayable_api={recon_vs_run['recon_replayable_api']}，"
            "但 run 產出 0 item → 抽取落點沒對上 recon 結構。"
        )
    if stage == "extraction" and top_rejects:
        diff_notes.append(f"最大拒絕原因：{top_rejects[0][0]}×{top_rejects[0][1]}，優先修這個。")

    return {
        "failure_stage": stage,
        "stage_hint": _STAGE_HINT.get(stage, ""),
        "recon_vs_run": recon_vs_run,
        "top_reject_reasons": dict(top_rejects),
        "rejected_samples": (validation.get("rejected_samples") or [])[:2],
        "diff_notes": diff_notes,
    }


def _repair_prompt(state: SpiderForgeState) -> str:
    materials = compile_generation_materials(
        state.get("evidence_pack") or {}
    )
    return (
        "目前 spider 未通過 live gate。依確切證據完整重寫整支檔案；不要只給 diff，"
        "不要更換目標網站，不要用假資料繞過 validator。先讀【失敗定位】確定哪一階段壞了、"
        "改那一處，不要整體換策略。"
        "遇到 301 使用 EvidencePack 的 final/canonical URL；遇到 browser 403 但 "
        "plain HTTP=200，使用 safe_request_headers 與 Scrapy HTTP。"
        "不得另猜 API endpoint 或 JSON path。\n\n"
        f"【失敗定位】\n{json.dumps(_repair_feedback(state), ensure_ascii=False)[:5000]}\n\n"
        f"【編譯後材料】\n{json.dumps(materials, ensure_ascii=False)}\n\n"
        f"【產碼預檢】\n{json.dumps(state.get('generation_preflight'), ensure_ascii=False)[:3000]}\n\n"
        f"【離線樣本驗證】\n{json.dumps(state.get('fixture_result'), ensure_ascii=False)[:5000]}\n\n"
        f"【診斷】\n{json.dumps(state.get('diagnosis'), ensure_ascii=False)}\n\n"
        f"【Live test】\n{json.dumps(state.get('test_result'), ensure_ascii=False)[:4000]}\n\n"
        f"【Quality gate】\n{json.dumps(state.get('validation_result'), ensure_ascii=False)[:6000]}\n\n"
        f"【Topic gate】\n{json.dumps(state.get('topic_result'), ensure_ascii=False)[:6000]}\n\n"
        f"【目前程式碼】\n```python\n{state.get('spider_code', '')}\n```\n\n"
        f"{_contract(state)}"
    )


def repair_code(state: SpiderForgeState) -> dict:
    code, error = _safe_generate(_repair_prompt(state), REPAIR_PROVIDER)
    return {
        "spider_code": code or state.get("spider_code", ""),
        "generation_error": error,
        "status": "testing",
    }


def repair_code_kimi(state: SpiderForgeState) -> dict:
    code, error = _safe_generate(_repair_prompt(state), FINAL_REPAIR_PROVIDER)
    return {
        "spider_code": code or state.get("spider_code", ""),
        "generation_error": error,
        "kimi_used": True,
        "status": "testing",
    }


# ════════════════════════ 執行、驗證、升版 ════════════════════════
