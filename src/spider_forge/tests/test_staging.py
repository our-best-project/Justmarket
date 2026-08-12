"""P0-B 驗收：candidate staging + atomic promotion。

核心不變式：任何候選失敗都不得改變 active spider 的 hash。
跑法（從 /）：
    python -m spider_forge.tests.test_staging
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime

from spider_forge.output import artifacts
from spider_forge.output.artifacts import (
    active_path,
    file_hash,
    promote,
    rollback,
    write_candidate,
)
from spider_forge.output.manager import persist_spider
from spider_forge.shared.sandbox import run_candidate
from spider_forge.stages.validate import validate_output

PREFIX = "_stagingtest"


def _cleanup():
    ap = active_path(PREFIX)
    for p in (ap, *ap.parent.glob(f".{ap.name}.*.tmp")):
        if p.exists():
            p.unlink()
    for d in artifacts._CANDIDATE_DIR.glob("_kt_*"):
        shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(artifacts._VERSION_DIR / PREFIX, ignore_errors=True)


def t_write_candidate_does_not_touch_active():
    _cleanup()
    ap = active_path(PREFIX)
    ap.write_text("# active v1\n", encoding="utf-8")
    before = file_hash(ap)
    cand = write_candidate("_kt_r1", PREFIX, "# candidate code\n")
    after = file_hash(ap)
    isolated = (
        cand.exists()
        and artifacts._CANDIDATE_DIR in cand.parents
    )
    ok = isolated and before == after
    _cleanup()
    return ok, f"active {before}->{after}, cand isolated={isolated}"


def t_promote_atomic_and_logged():
    _cleanup()
    ap = active_path(PREFIX)
    ap.write_text("# active v1\n", encoding="utf-8")
    prev = file_hash(ap)
    cand = write_candidate("_kt_r2", PREFIX, "# promoted v2 content\n")
    rec = promote(cand, PREFIX)
    now = file_hash(ap)
    ok = (ap.read_text(encoding="utf-8") == "# promoted v2 content\n"
          and rec["prev_hash"] == prev and rec["new_hash"] == now
          and rec["prev_hash"] != rec["new_hash"])
    _cleanup()
    return ok, f"prev={rec['prev_hash']} new={rec['new_hash']}"


def t_syntax_error_candidate_leaves_active_unchanged():
    _cleanup()
    ap = active_path(PREFIX)
    ap.write_text("# active good\n", encoding="utf-8")
    before = file_hash(ap)
    cand = write_candidate("_kt_r3", PREFIX, "def (: broken syntax here\n")
    outp = os.path.join(tempfile.gettempdir(), "_kt_out.json")
    res = run_candidate(str(cand), out_json=outp, item_limit=1, timeout_s=60)
    after = file_hash(ap)  # promote 從未被呼叫 → active 不變
    ok = res.exit_code != 0 and before == after
    _cleanup()
    return ok, f"candidate exit={res.exit_code} active {before}->{after}"


def t_rollback_restores_previous_artifact():
    _cleanup()
    ap = active_path(PREFIX)
    ap.write_text("# v1\n", encoding="utf-8")
    v1_hash = file_hash(ap)
    cand = write_candidate("_kt_r4", PREFIX, "# v2\n")
    rec = promote(cand, PREFIX)
    rolled = rollback(rec)
    ok = (
        ap.read_text(encoding="utf-8") == "# v1\n"
        and file_hash(ap) == v1_hash
        and rolled["restored_hash"] == v1_hash
        and rec.get("previous_artifact")
    )
    _cleanup()
    return ok, f"promoted={rec.get('new_hash')} restored={rolled.get('restored_hash')}"


def t_rollback_refuses_to_overwrite_newer_active():
    _cleanup()
    ap = active_path(PREFIX)
    ap.write_text("# v1\n", encoding="utf-8")
    rec = promote(write_candidate("_kt_r5", PREFIX, "# v2\n"), PREFIX)
    ap.write_text("# v3 newer release\n", encoding="utf-8")
    newer_hash = file_hash(ap)
    try:
        rollback(rec)
        raised = False
    except RuntimeError:
        raised = True
    after = file_hash(ap)
    _cleanup()
    return raised and after == newer_hash, f"raised={raised} active {newer_hash}->{after}"


def t_missing_candidate_fails_closed():
    _cleanup()
    ap = active_path(PREFIX)
    ap.write_text("# active must survive\n", encoding="utf-8")
    before = file_hash(ap)
    try:
        persist_spider({
            "source_prefix": PREFIX,
            "candidate_path": "_does_not_exist_.py",
            "validation_result": {"pass": True},
        })
        raised = False
    except RuntimeError:
        raised = True
    after = file_hash(ap)
    _cleanup()
    return raised and before == after, f"raised={raised} active {before}->{after}"


def t_failed_validation_cannot_promote():
    _cleanup()
    ap = active_path(PREFIX)
    ap.write_text("# trusted active\n", encoding="utf-8")
    before = file_hash(ap)
    candidate = write_candidate("_kt_rejected", PREFIX, "# rejected candidate\n")
    try:
        persist_spider({
            "source_prefix": PREFIX,
            "candidate_path": str(candidate),
            "validation_result": {"pass": False},
        })
        raised = False
    except RuntimeError:
        raised = True
    after = file_hash(ap)
    _cleanup()
    return raised and before == after, f"raised={raised} active {before}->{after}"


def t_validate_then_promote_integration():
    _cleanup()
    ap = active_path(PREFIX)
    ap.write_text("# old active\n", encoding="utf-8")
    candidate = write_candidate("_kt_integration", PREFIX, "# validated active\n")
    output = os.path.join(tempfile.gettempdir(), "_kt_validate_promote.json")
    now = datetime.now(UTC).isoformat()
    items = [
        {"title": f"整合測試第{i}則有效新聞", "url": f"https://example.com/article/{i}",
         "content": "整合測試有效內容" * 10, "published_at": now}
        for i in range(5)
    ]
    with open(output, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    state = {
        "test_result": {"passed": True, "output_path": output},
        "validation": {
            "allowed_domains": ["example.com"],
            "article_url_patterns": [r"/article/\d+"],
            "min_content_chars": 40,
            "min_valid_items": 5,
        },
    }
    validation = validate_output(state)["validation_result"]
    promoted = persist_spider({
        "source_prefix": PREFIX,
        "candidate_path": str(candidate),
        "validation_result": validation,
    })
    ok = (
        validation["pass"] is True
        and promoted["status"] == "success"
        and ap.read_text(encoding="utf-8") == "# validated active\n"
    )
    rollback(promoted["promotion"])
    if os.path.exists(output):
        os.unlink(output)
    _cleanup()
    return ok, f"validated={validation['pass']} status={promoted['status']}"


TESTS = [
    t_write_candidate_does_not_touch_active,
    t_promote_atomic_and_logged,
    t_syntax_error_candidate_leaves_active_unchanged,
    t_rollback_restores_previous_artifact,
    t_rollback_refuses_to_overwrite_newer_active,
    t_missing_candidate_fails_closed,
    t_failed_validation_cannot_promote,
    t_validate_then_promote_integration,
]


def main() -> int:
    failed = 0
    for t in TESTS:
        try:
            ok, detail = t()
        except Exception as e:
            ok, detail = False, f"EXCEPTION {e}"
        print(f"[{'PASS' if ok else 'FAIL'}] {t.__name__}: {detail}")
        if not ok:
            failed += 1
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
