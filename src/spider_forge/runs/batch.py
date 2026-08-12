"""批次執行 site_queue：每站一個 graph thread，結果寫入集中式 runtime。

由套件 CLI 的 ``batch`` 子命令呼叫。站間序列執行，站內由 Scrapy
AutoThrottle 控速。
persist 只寫 spider 檔、**不寫 DB**（沙盒測試也停用 Neon pipeline）——先產可用 spider。
"""

from __future__ import annotations

import json
import time
import traceback
import uuid

import yaml

from ..clients.coder import drain_usage
from ..config import SITE_QUEUE_PATH, run_dir
from ..pipeline import build_pipeline
from .ledger import append_run, summarize


def load_sites(only: list[str] | None = None) -> list[dict]:
    sites = yaml.safe_load(
        SITE_QUEUE_PATH.read_text(encoding="utf-8")
    )["sites"]
    if only:
        sites = [s for s in sites if s["source_prefix"] in only]
    return sites


def _save_evidence(run_id: str, evidence_pack: dict) -> str | None:
    """保存已遮密的 EvidencePack，供重播、除錯與前後比較。"""
    if not evidence_pack:
        return None
    path = run_dir(run_id) / "evidence.json"
    path.write_text(
        json.dumps(evidence_pack, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(path)


def run_site(
    graph,
    site: dict,
    max_retries: int = 2,
    *,
    run_id: str | None = None,
) -> dict:
    drain_usage()  # usage 生命週期以單站為界；先清除任何舊進程殘留
    init = {
        **site,
        "max_retries": max_retries,
        "retry_count": 0,
        "error_signature_history": [],
        "kimi_used": False,
    }
    tid = run_id or f"{site.get('source_prefix', 'site')}-{uuid.uuid4().hex[:8]}"
    init["run_id"] = tid  # 候選隔離區用同一 run_id
    config = {"configurable": {"thread_id": tid}, "recursion_limit": 60}

    t0 = time.time()
    for _event in graph.stream(init, config=config, stream_mode="updates"):
        pass

    v = graph.get_state(config).values
    usage = drain_usage()
    validation = v.get("validation_result", {})
    topic = v.get("topic_result", {})
    test = v.get("test_result", {})
    # escalate_human 已改非阻塞死信（spec v2 D4）：不再靠 __interrupt__ 事件判斷，
    # 節點本身就把 status 寫成 "escalated" 並正常 END，state 是唯一真相來源。
    status = v.get("status") if v.get("status") in {"success", "escalated"} else "incomplete"
    repair_count = v.get("retry_count", 0)
    evidence_path = _save_evidence(tid, v.get("evidence_pack") or {})
    record = {
        "run_id": tid,
        "site_name": v.get("site_name") or site.get("site_name") or site["site_url"],
        "source_prefix": v.get("source_prefix") or site.get("source_prefix"),
        "site_url": v.get("site_url") or site["site_url"],
        "strategy": v.get("strategy"),
        "evidence_origin": (v.get("evidence_pack") or {}).get("origin"),
        "evidence_path": evidence_path,
        "status": status,
        "retry_count": repair_count,
        "repair_count": repair_count,
        "first_pass_success": status == "success" and repair_count == 0,
        "llm_calls": len(usage),
        "coder_tokens": sum(u.get("prompt_tokens", 0) + u.get("completion_tokens", 0) for u in usage),
        "item_count": validation.get("item_count"),
        "valid_count": validation.get("valid_count"),
        "unique_valid_count": validation.get("unique_valid_count"),
        "valid_rate": validation.get("valid_rate"),
        "unique_ratio": validation.get("unique_ratio"),
        "validation_flags": validation.get("flags"),
        "reject_reasons": validation.get("reject_reasons"),
        "validation_issues": validation.get("issues"),
        "topic_status": topic.get("status"),
        "topic_model": topic.get("model"),
        "topic_relevant_count": topic.get("relevant_count"),
        "topic_relevant_ratio": topic.get("relevant_ratio"),
        "topic_api_calls": topic.get("api_calls"),
        "signatures": v.get("error_signature_history", []),
        "spider_path": v.get("spider_path"),
        "items_path": test.get("output_path"),
        "sandbox_stdout_log": test.get("stdout_log"),
        "sandbox_stderr_log": test.get("stderr_log"),
        "pipeline_log": str(run_dir(tid) / "pipeline.log"),
        "duration_s": round(time.time() - t0, 1),
    }
    return record


def _save_result(run_id: str, record: dict) -> None:
    path = run_dir(run_id) / "result.json"
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_one(
    graph,
    site: dict,
    *,
    max_retries: int = 2,
    run_id: str | None = None,
) -> dict:
    """跑單站並保證成功、死信或例外都留下 ledger 與 result.json。"""
    tid = run_id or f"{site.get('source_prefix', 'site')}-{uuid.uuid4().hex[:8]}"
    site_t0 = time.time()
    try:
        record = run_site(
            graph,
            site,
            max_retries=max_retries,
            run_id=tid,
        )
    except Exception as exc:
        traceback.print_exc()
        usage = drain_usage()
        record = {
            "run_id": tid,
            "site_name": site.get("site_name") or site.get("site_url"),
            "source_prefix": site.get("source_prefix"),
            "site_url": site.get("site_url"),
            "status": "error",
            "error": str(exc)[:1000],
            "retry_count": None,
            "repair_count": None,
            "first_pass_success": False,
            "llm_calls": len(usage),
            "coder_tokens": sum(
                row.get("prompt_tokens", 0) + row.get("completion_tokens", 0)
                for row in usage
            ),
            "pipeline_log": str(run_dir(tid) / "pipeline.log"),
            "duration_s": round(time.time() - site_t0, 1),
        }
    append_run(record)
    _save_result(tid, record)
    return record


def run_batch(
    only: list[str] | None = None,
    *,
    max_retries: int = 2,
) -> list[dict]:
    sites = load_sites(only)
    graph = build_pipeline()
    results = []
    for site in sites:
        print(f"\n===== {site['site_name']} ({site['source_prefix']}) =====", flush=True)
        rec = run_one(graph, site, max_retries=max_retries)
        print(
            f"  -> {rec.get('status')} | strategy={rec.get('strategy')} "
            f"retry={rec.get('retry_count')} items={rec.get('item_count')} "
            f"tokens={rec.get('coder_tokens')} {rec.get('duration_s')}s",
            flush=True,
        )
        results.append(rec)

    print("\n===== 實況彙總 =====", flush=True)
    s = summarize()
    for k, val in s.items():
        print(f"  {k}: {val}", flush=True)
    return results
