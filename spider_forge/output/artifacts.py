"""Candidate staging + atomic promotion（remediation P0-2）。

核心不變式：**任何候選失敗都不得改變 active spider 的 hash**。
做法：候選一律寫進隔離的 _candidates/<run_id>/ 跑；只有通過品質閘門（validate pass →
persist_spider）才用 os.replace 原子覆蓋 active。promotion 保存舊實體檔與 prev/new hash，
rollback 只在 active 未被後續版本改動時原子還原。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..config import (
    ACTIVE_DIR,
    CANDIDATE_DIR,
    PROMOTIONS_LOG,
    VERSION_DIR,
)

_ACTIVE_DIR = ACTIVE_DIR
_CANDIDATE_DIR = CANDIDATE_DIR
_PROMO_LOG = PROMOTIONS_LOG
_VERSION_DIR = VERSION_DIR


def file_hash(path: str | Path) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def active_path(source_prefix: str) -> Path:
    _ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    return _ACTIVE_DIR / f"{source_prefix}_spider.py"


def candidate_path(run_id: str, source_prefix: str) -> Path:
    return _CANDIDATE_DIR / run_id / f"{source_prefix}_spider.py"


def write_candidate(run_id: str, source_prefix: str, code: str) -> Path:
    """把候選碼寫進隔離區（**不碰 active**）。"""
    p = candidate_path(run_id, source_prefix)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code, encoding="utf-8")
    return p


def _append_promotion_event(rec: dict) -> None:
    _PROMO_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_PROMO_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _archive_active(active: Path, source_prefix: str, digest: str | None) -> Path | None:
    """保存可執行的舊版本；同 hash 只存一份。"""
    if not digest or not active.exists():
        return None
    archived = _VERSION_DIR / source_prefix / f"{digest}.py"
    if archived.exists():
        if file_hash(archived) != digest:
            raise RuntimeError(f"rollback archive hash mismatch: {archived}")
        return archived
    archived.parent.mkdir(parents=True, exist_ok=True)
    tmp = archived.with_name(f".{archived.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(active, tmp)
        if file_hash(tmp) != digest:
            raise RuntimeError(f"failed to archive active spider: {active}")
        os.replace(tmp, archived)
    finally:
        if tmp.exists():
            tmp.unlink()
    return archived


def promote(candidate: str | Path, source_prefix: str) -> dict:
    """把通過驗證的候選原子覆蓋 active，記 prev/new hash。

    先 copy 到 active 同目錄的暫存檔，再 os.replace（同檔案系統原子）——
    即使 promotion 中途被 kill，active 也只會是「舊完整檔」或「新完整檔」，不會半截。
    """
    candidate = Path(candidate)
    if not candidate.is_file():
        raise FileNotFoundError(f"candidate not found: {candidate}")
    active = active_path(source_prefix)
    active.parent.mkdir(parents=True, exist_ok=True)
    prev = file_hash(active)
    previous_artifact = _archive_active(active, source_prefix, prev)

    tmp = active.with_name(f".{active.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(candidate, tmp)
        os.replace(tmp, active)  # atomic on same filesystem
    finally:
        if tmp.exists():
            tmp.unlink()
    new = file_hash(active)
    if new != file_hash(candidate):
        raise RuntimeError(f"promotion hash mismatch: {candidate} -> {active}")

    rec = {
        "event": "promote",
        "ts": datetime.now(UTC).isoformat(),
        "source_prefix": source_prefix,
        "prev_hash": prev,
        "new_hash": new,
        "previous_artifact": str(previous_artifact) if previous_artifact else None,
        "candidate": str(candidate),
        "active": str(active),
    }
    _append_promotion_event(rec)
    return rec


def rollback(promotion: dict) -> dict:
    """將 active 原子還原為 promotion 前保存的實體版本。

    為避免覆蓋後續發布，只有 active 仍是該次 promotion 的 new_hash 時才允許回滾。
    """
    active = Path(promotion["active"])
    expected_current = promotion.get("new_hash")
    current = file_hash(active)
    if current != expected_current:
        raise RuntimeError(
            f"rollback refused: active changed after promotion ({current} != {expected_current})"
        )
    previous_artifact = promotion.get("previous_artifact")
    expected_previous = promotion.get("prev_hash")
    if not previous_artifact or not expected_previous:
        raise RuntimeError("rollback unavailable: promotion has no previous active artifact")
    archived = Path(previous_artifact)
    if file_hash(archived) != expected_previous:
        raise RuntimeError(f"rollback artifact missing or corrupt: {archived}")

    tmp = active.with_name(f".{active.name}.{uuid.uuid4().hex}.rollback.tmp")
    try:
        shutil.copyfile(archived, tmp)
        os.replace(tmp, active)
    finally:
        if tmp.exists():
            tmp.unlink()
    restored = file_hash(active)
    if restored != expected_previous:
        raise RuntimeError(f"rollback verification failed: {restored} != {expected_previous}")

    rec = {
        "event": "rollback",
        "ts": datetime.now(UTC).isoformat(),
        "source_prefix": promotion["source_prefix"],
        "from_hash": expected_current,
        "restored_hash": restored,
        "artifact": str(archived),
        "active": str(active),
    }
    _append_promotion_event(rec)
    return rec
