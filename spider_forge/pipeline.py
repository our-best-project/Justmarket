"""Spider Forge 的唯一流程組裝入口。"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .output.manager import escalate_human, persist_spider
from .stages.evidence import collect_evidence
from .stages.fixture import fixture_test
from .stages.generate import (
    generate_spider,
    preflight_generated_code,
    strategy_decision,
)
from .stages.probe import prepare_request, recon
from .stages.repair import (
    diagnose_failure,
    repair_code,
    repair_code_kimi,
)
from .stages.sandbox import sandbox_test
from .stages.triage import feasibility_triage
from .stages.validate import (
    apply_topic_gate,
    content_block_gate,
    validate_output,
)
from .state import (
    KILL_FAILURE_CLASSES,
    SpiderForgeState,
    normalize_failure_class,
)

_PROVIDER_RETRY_MAX = 2


def route_after_triage(state: SpiderForgeState) -> str:
    feasibility_class = str((state.get("feasibility") or {}).get("class") or "")
    return "escalate_human" if feasibility_class.startswith("KILL_") else "strategy_decision"


def route_after_block_gate(state: SpiderForgeState) -> str:
    return "diagnose_failure" if state.get("block_page_detected") else "validate_output"


def route_after_preflight(state: SpiderForgeState) -> str:
    return (
        "fixture_test"
        if (state.get("generation_preflight") or {}).get("passed")
        else "diagnose_failure"
    )


def route_after_fixture(state: SpiderForgeState) -> str:
    return (
        "sandbox_test"
        if (state.get("fixture_result") or {}).get("passed")
        else "diagnose_failure"
    )


def route_after_validate(state: SpiderForgeState) -> str:
    if state.get("validation_result", {}).get("pass"):
        return "persist_spider"
    topic = state.get("topic_result") or {}
    if (
        topic.get("mode") == "enforce"
        and str(topic.get("status") or "").endswith("_unavailable")
    ):
        return "escalate_human"
    return "diagnose_failure"


def route_after_diagnose(state: SpiderForgeState) -> str:
    failure_class = normalize_failure_class(
        (state.get("diagnosis") or {}).get("failure_class")
        or state.get("failure_class")
    )
    if failure_class in KILL_FAILURE_CLASSES:
        return "escalate_human"
    if failure_class == "provider_failure":
        if state.get("provider_retry_count", 0) > _PROVIDER_RETRY_MAX:
            return "escalate_human"
        return "repair_code_kimi" if state.get("kimi_used") else "repair_code"

    retry = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)
    if retry > max_retries:
        return "escalate_human"
    if retry >= 2:
        return "repair_code_kimi"
    return "repair_code"


def build_pipeline(checkpointer=None):
    """建立 Spider Forge 流程；節點順序與所有分支集中在本函式。"""

    builder = StateGraph(SpiderForgeState)
    for name, function in [
        ("prepare_request", prepare_request),
        ("recon", recon),
        ("feasibility_triage", feasibility_triage),
        ("strategy_decision", strategy_decision),
        ("collect_evidence", collect_evidence),
        ("generate_spider", generate_spider),
        ("generation_preflight", preflight_generated_code),
        ("fixture_test", fixture_test),
        ("sandbox_test", sandbox_test),
        ("content_block_gate", content_block_gate),
        ("validate_output", validate_output),
        ("topic_gate", apply_topic_gate),
        ("diagnose_failure", diagnose_failure),
        ("repair_code", repair_code),
        ("repair_code_kimi", repair_code_kimi),
        ("persist_spider", persist_spider),
        ("escalate_human", escalate_human),
    ]:
        builder.add_node(name, function)

    builder.add_edge(START, "prepare_request")
    builder.add_edge("prepare_request", "recon")
    builder.add_edge("recon", "feasibility_triage")
    builder.add_conditional_edges(
        "feasibility_triage",
        route_after_triage,
        ["strategy_decision", "escalate_human"],
    )
    builder.add_edge("strategy_decision", "collect_evidence")
    builder.add_edge("collect_evidence", "generate_spider")
    builder.add_edge("generate_spider", "generation_preflight")
    builder.add_conditional_edges(
        "generation_preflight",
        route_after_preflight,
        ["fixture_test", "diagnose_failure"],
    )
    builder.add_conditional_edges(
        "fixture_test",
        route_after_fixture,
        ["sandbox_test", "diagnose_failure"],
    )
    builder.add_edge("sandbox_test", "content_block_gate")
    builder.add_conditional_edges(
        "content_block_gate",
        route_after_block_gate,
        ["validate_output", "diagnose_failure"],
    )
    builder.add_edge("validate_output", "topic_gate")
    builder.add_conditional_edges(
        "topic_gate",
        route_after_validate,
        [
            "persist_spider",
            "diagnose_failure",
            "escalate_human",
        ],
    )
    builder.add_conditional_edges(
        "diagnose_failure",
        route_after_diagnose,
        ["repair_code", "repair_code_kimi", "escalate_human"],
    )
    builder.add_edge("repair_code", "generation_preflight")
    builder.add_edge("repair_code_kimi", "generation_preflight")
    builder.add_edge("persist_spider", END)
    builder.add_edge("escalate_human", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())


def forge_spider(
    url: str,
    *,
    max_retries: int = 2,
    run_id: str | None = None,
    **request: Any,
) -> dict[str, Any]:
    """執行單一網址並回傳最終狀態，不負責批次紀錄或終端輸出。"""

    thread_id = run_id or f"forge-{uuid.uuid4().hex[:8]}"
    initial_state = {
        **request,
        "site_url": url,
        "run_id": thread_id,
        "max_retries": max_retries,
        "retry_count": 0,
        "error_signature_history": [],
        "kimi_used": False,
    }
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}
    return dict(build_pipeline().invoke(initial_state, config=config))
