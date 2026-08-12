"""SpiderForgeState — LangGraph 流程的共享狀態（docs/20_spec_v1_spider-forge-graph.md）。

total=False：節點逐步填欄位，不必一開始湊齊。
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

# 失敗分類法（spec v2 §4）中，一經判定就不再嘗試生成/修復、直接死信的類別。
# 這些在 feasibility_triage 以 ``KILL_`` 前綴出現，在 diagnose_failure 以裸名出現，
# 兩處共用同一集合，routers 用 normalize_failure_class() 去前綴後比對。
KILL_FAILURE_CLASSES = frozenset(
    {
        "policy_kill",
        "signature_required",
        "discovery_empty",
        "js_required",
    }
)


def normalize_failure_class(value: Any) -> str:
    """去掉 triage 的 ``KILL_`` 前綴，讓 diagnose 的裸名與 triage 的前綴名可統一比對。"""
    text = str(value or "")
    return text[len("KILL_") :] if text.startswith("KILL_") else text


class SpiderForgeState(TypedDict, total=False):
    # ── 最小輸入：URL + 輸出契約 + 必要存取方式 ──
    site_url: str
    site_name: str              # 可省略；prepare_request 由 hostname 推導
    source_prefix: str          # 可省略；prepare_request 產安全 slug
    source_prefix_explicit: bool
    target_schema: dict[str, Any]
    target_schema_explicit: bool
    sample_urls: list[str]      # 選填；2–5 個已知明細 URL，不要求使用者準備 selector/HAR
    access_mode: Literal["public", "browser_session"]
    access_context_ref: str     # 選填；只交 recon，不得進 prompt / sandbox
    constraints: dict[str, Any]
    validation: dict[str, Any]  # 品質閘門設定（allowed_domains/article_url_patterns/…，來自 site_queue）
    validation_explicit: bool
    topic_gate: dict[str, Any]  # off/shadow/enforce；Gemini 預設、artifact 可回退
    max_retries: int            # prepare_request 強制 0..2，預設 2

    # ── 偵查 / 內部 EvidencePack ──
    recon_report: dict[str, Any]
    feasibility: dict[str, Any]  # feasibility_triage 輸出：class/reason/evidence_summary（spec v2 §3.2）
    evidence_pack: dict[str, Any]
    strategy: Literal["api", "dom", "hybrid"]
    strategy_detail: dict[str, Any]   # judge 完整輸出（含 confidence/reason/chosen_api）

    # ── 生成 / 測試 / 驗證 ──
    run_id: str                 # 每站一個；集中式 runtime/runs 與 candidates 共用
    spider_code: str
    generation_error: str
    generation_materials: dict[str, Any]  # 送入 coder 的材料量與裁切摘要
    generation_preflight: dict[str, Any]  # 確定性產碼契約檢查與安全修正紀錄
    fixture_result: dict[str, Any]  # 保存 response 的離線 callback 驗證結果
    candidate_path: str         # 隔離區候選檔（sandbox 跑這個，不碰 active）
    spider_path: str            # promote 後的 active 檔
    promotion: dict[str, Any]   # promote 紀錄（prev/new hash，可回滾）
    test_result: dict[str, Any]
    block_page_detected: bool   # content_block_gate 判定：200 但實為挑戰/錯誤頁（spec v2 §3.3）
    block_detection: dict[str, Any]  # 判定方法與證據（deterministic / gemini / fail-open）
    block_gate: dict[str, Any]  # 每站設定：是否啟用 Gemini 內容真偽確認（預設純確定性，省額度）
    validation_result: dict[str, Any]
    topic_result: dict[str, Any]

    # ── 修復迴圈（防死循環的關鍵：簽章歷史，不只數次數）──
    retry_count: int
    provider_retry_count: int   # 供應商逾時/5xx（provider_failure）的累計重試；不計入 retry_count 修復額度（spec v2 §4）
    diagnosis: dict[str, Any]
    error_signature_history: list[str]
    kimi_used: bool             # 第二輪修復是否已用 Kimi（再失敗 → 轉人工）

    # ── 死信歸檔（spec v2 D4/§3.6/§7：escalate_human 非阻塞死信，不再 interrupt）──
    failure_class: str          # KILL_* 或 repair_exhausted/topic_provider_unavailable
    dead_letter_path: str       # runtime/records/dead_letter/<run_id>.json

    # ── 狀態機 ──
    status: Literal[
        "pending", "request_ready", "reconning", "triaging",
        "evidence_ready", "generating", "testing",
        "validating", "topic_validating", "repairing", "success", "escalated",
    ]
