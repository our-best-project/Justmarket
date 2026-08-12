"""成功認證與死信輸出管理。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC
from pathlib import Path

from ..config import DEAD_LETTER_DIR
from ..shared.summaries import evidence_summary
from ..state import KILL_FAILURE_CLASSES, SpiderForgeState, normalize_failure_class


def persist_spider(state: SpiderForgeState) -> dict:
    from . import artifacts

    candidate = state.get("candidate_path")
    name = state["source_prefix"]
    if state.get("validation_result", {}).get("pass") is not True:
        raise RuntimeError(f"promotion refused: validation did not pass for {name}")
    if not candidate or not Path(candidate).exists():
        raise RuntimeError(f"promotion refused: candidate missing for {name}: {candidate!r}")
    record = artifacts.promote(candidate, name)
    return {"spider_path": record["active"], "promotion": record, "status": "success"}


def escalate_human(state: SpiderForgeState) -> dict:
    """死信歸檔（spec v2 D4/§3.6/§7）：不再 interrupt 阻塞（沒人 resume 會永遠卡）；
    寫完整證據到 runtime/records/dead_letter/<run_id>.json 後正常 END(status=escalated)，
    供日後有人力時撈起接手。可能從兩條路徑進來：feasibility_triage 的 KILL_*
    （state 帶 feasibility），或兩輪修復仍未過 gate／主題閘門不可用（state 不帶
    feasibility，用 topic_result/diagnosis 組出等價摘要）。
    """
    from datetime import datetime

    feasibility = state.get("feasibility") or {}
    topic = state.get("topic_result") or {}
    topic_unavailable = str(topic.get("status") or "").endswith("_unavailable")

    failure_class = state.get("failure_class") or feasibility.get("class") or (
        "topic_provider_unavailable" if topic_unavailable else "repair_exhausted"
    )
    triage_reason = feasibility.get("reason") or (
        "主題閘門服務不可用；未浪費 code repair 額度"
        if topic_unavailable
        else "兩輪定向修復仍未通過 live gate"
    )
    recon_evidence = feasibility.get("evidence_summary") or evidence_summary(
        state.get("recon_report") or {}
    )

    tried = {
        "strategy": state.get("strategy"),
        "retry_count": state.get("retry_count", 0),
        "kimi_used": state.get("kimi_used", False),
        "error_signature_history": state.get("error_signature_history", []),
        "diagnosis": state.get("diagnosis"),
        "topic_result": topic,
    }
    if state.get("generation_error"):
        tried["generation_error"] = state["generation_error"]

    run_id = str(state.get("run_id") or uuid.uuid4().hex[:8])
    dead_letter_dir = DEAD_LETTER_DIR
    dead_letter_dir.mkdir(parents=True, exist_ok=True)
    dead_letter_path = dead_letter_dir / f"{run_id}.json"
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "site_url": state.get("site_url"),
        "source_prefix": state.get("source_prefix"),
        "failure_class": failure_class,
        "triage_reason": triage_reason,
        "recon_evidence": recon_evidence,
        "tried": tried,
        "suggested_action": (
            "確定性 KILL：不建議自動重排，待人工評估是否值得客製化處理"
            if normalize_failure_class(failure_class) in KILL_FAILURE_CLASSES
            else "曾嘗試生成/修復兩輪仍未過 gate；待人工檢視診斷與修復歷史"
        ),
        "status": "dead_letter",
    }
    dead_letter_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "dead_letter_path": str(dead_letter_path),
        "failure_class": failure_class,
        "status": "escalated",
    }
