"""訓練／校準 Spider Forge topic gate 的純 JSON artifact。

輸入 JSONL 每列：
{"id":"...", "split":"train|calibration|test", "source":"...",
 "title":"...", "content":"...", "labels":["finance"|"public_policy"]}

測試集只評估 production_ready，不參與模型、校準器或門檻擬合。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

from ..shared.topic import (
    ARTIFACT_SCHEMA,
    FEATURE_VERSION,
    LABELS,
    MODEL_DIR,
    TAXONOMY_VERSION,
    _embedding_model,
    feature_text,
    finalize_artifact,
)


def load_gold(path: Path) -> list[dict]:
    records = []
    seen = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        record_id = str(row.get("id") or "").strip()
        split = row.get("split")
        labels = row.get("labels") or []
        if not record_id or record_id in seen:
            raise ValueError(f"第 {line_no} 列 id 缺漏或重複")
        if split not in {"train", "calibration", "test"}:
            raise ValueError(f"第 {line_no} 列 split 不合法")
        if not isinstance(labels, list) or any(label not in LABELS for label in labels):
            raise ValueError(f"第 {line_no} 列 labels 不合法")
        if not str(row.get("title") or "").strip():
            raise ValueError(f"第 {line_no} 列 title 缺漏")
        seen.add(record_id)
        records.append(row)
    for split in ("train", "calibration", "test"):
        if not any(row["split"] == split for row in records):
            raise ValueError(f"gold 缺少 {split} split")
    return records


def _wilson_lower(correct: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = correct / total
    denominator = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    spread = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return (centre - spread) / denominator


def _accept_threshold(
    scores: np.ndarray, truth: np.ndarray, *, min_precision: float, min_count: int
) -> float:
    candidates = sorted({float(x) for x in scores}, reverse=True)
    valid = []
    for threshold in candidates:
        mask = scores >= threshold
        count = int(mask.sum())
        if count < min_count:
            continue
        precision = float(truth[mask].mean())
        if precision >= min_precision:
            valid.append((count, threshold))
    if not valid:
        raise ValueError("calibration 無 accept threshold 能滿足 precision/count")
    return max(valid)[1]  # 最大 coverage；同 coverage 時取較高門檻


def _reject_threshold(
    scores: np.ndarray,
    truth: np.ndarray,
    *,
    accept_threshold: float,
    min_npv: float,
    min_count: int,
) -> float:
    candidates = sorted({float(x) for x in scores})
    valid = []
    for threshold in candidates:
        if threshold >= accept_threshold:
            continue
        mask = scores <= threshold
        count = int(mask.sum())
        if count < min_count:
            continue
        npv = float((1 - truth[mask]).mean())
        if npv >= min_npv:
            valid.append((count, threshold))
    if not valid:
        raise ValueError("calibration 無 reject threshold 能滿足 NPV/count")
    return max(valid)[1]


def _calibrated_scores(
    vectors: np.ndarray, base: LogisticRegression, calibrator: LogisticRegression
) -> np.ndarray:
    raw = base.decision_function(vectors).reshape(-1, 1)
    return calibrator.predict_proba(raw)[:, 1]


def _label_metrics(
    scores: np.ndarray,
    truth: np.ndarray,
    *,
    accept_threshold: float,
    reject_threshold: float,
) -> dict:
    accepted = scores >= accept_threshold
    rejected = scores <= reject_threshold
    accept_count = int(accepted.sum())
    accept_correct = int(truth[accepted].sum())
    positives = int(truth.sum())
    return {
        "count": len(scores),
        "positives": positives,
        "accept_count": accept_count,
        "accept_precision": round(
            accept_correct / accept_count if accept_count else 0.0, 6
        ),
        "accept_precision_wilson95_lower": round(
            _wilson_lower(accept_correct, accept_count), 6
        ),
        "positive_recall": round(
            accept_correct / positives if positives else 0.0, 6
        ),
        "reject_count": int(rejected.sum()),
        "review_count": int((~accepted & ~rejected).sum()),
    }


def train_artifact(
    records: list[dict],
    *,
    embedding_model: str,
    min_precision: float,
    min_npv: float,
    min_calibration_count: int,
    min_test_accept_count: int,
) -> dict:
    texts = [feature_text(row) for row in records]
    model = _embedding_model(embedding_model)
    vectors = np.asarray(
        model.encode(
            texts,
            batch_size=16,
            normalize_embeddings=True,
            show_progress_bar=True,
            convert_to_numpy=True,
        ),
        dtype=np.float64,
    )
    split_indices = {
        split: np.asarray(
            [i for i, row in enumerate(records) if row["split"] == split],
            dtype=int,
        )
        for split in ("train", "calibration", "test")
    }
    classifiers = {}
    metrics = {}
    production_ready = True

    for label in LABELS:
        truth = np.asarray([int(label in row["labels"]) for row in records], dtype=int)
        for split, indices in split_indices.items():
            if len(set(truth[indices].tolist())) < 2:
                raise ValueError(f"{label} 的 {split} split 必須同時有正負例")

        base = LogisticRegression(
            class_weight="balanced",
            max_iter=2000,
            random_state=0,
            solver="liblinear",
        )
        base.fit(vectors[split_indices["train"]], truth[split_indices["train"]])

        calibration_raw = base.decision_function(
            vectors[split_indices["calibration"]]
        ).reshape(-1, 1)
        calibrator = LogisticRegression(
            max_iter=1000, random_state=0, solver="liblinear"
        )
        calibrator.fit(calibration_raw, truth[split_indices["calibration"]])
        calibration_scores = calibrator.predict_proba(calibration_raw)[:, 1]
        calibration_truth = truth[split_indices["calibration"]]
        accept_threshold = _accept_threshold(
            calibration_scores,
            calibration_truth,
            min_precision=min_precision,
            min_count=min_calibration_count,
        )
        reject_threshold = _reject_threshold(
            calibration_scores,
            calibration_truth,
            accept_threshold=accept_threshold,
            min_npv=min_npv,
            min_count=min_calibration_count,
        )

        classifiers[label] = {
            "coef": [round(float(x), 10) for x in base.coef_[0]],
            "intercept": round(float(base.intercept_[0]), 10),
            "calibration_coef": round(float(calibrator.coef_[0][0]), 10),
            "calibration_intercept": round(float(calibrator.intercept_[0]), 10),
            "accept_threshold": round(float(accept_threshold), 10),
            "reject_threshold": round(float(reject_threshold), 10),
        }
        test_indices = split_indices["test"]
        test_scores = _calibrated_scores(
            vectors[test_indices], base, calibrator
        )
        label_metrics = _label_metrics(
            test_scores,
            truth[test_indices],
            accept_threshold=accept_threshold,
            reject_threshold=reject_threshold,
        )
        metrics[label] = label_metrics
        if (
            label_metrics["accept_count"] < min_test_accept_count
            or label_metrics["accept_precision_wilson95_lower"] < min_precision
        ):
            production_ready = False

    artifact = {
        "schema_version": ARTIFACT_SCHEMA,
        "taxonomy_version": TAXONOMY_VERSION,
        "feature_version": FEATURE_VERSION,
        "embedding_model": embedding_model,
        "embedding_dimension": int(vectors.shape[1]),
        "labels": list(LABELS),
        "classifiers": classifiers,
        "production_ready": production_ready,
        "slo": {
            "min_precision": min_precision,
            "min_npv": min_npv,
            "min_calibration_count": min_calibration_count,
            "min_test_accept_count": min_test_accept_count,
        },
        "split_counts": {
            split: len(indices) for split, indices in split_indices.items()
        },
        "test_metrics": metrics,
    }
    return finalize_artifact(artifact)


def train_topic_artifact(
    gold_jsonl: Path,
    *,
    output: str = "candidate.json",
    model: str = "BAAI/bge-m3",
    min_precision: float = 0.95,
    min_npv: float = 0.95,
    min_calibration_count: int = 10,
    min_test_accept_count: int = 10,
) -> dict:
    """訓練並保存候選 artifact，回傳可供 CLI 顯示的摘要。"""
    if not re_fullmatch_safe_name(output):
        raise ValueError("--output 只能是 topic_models 內的安全 JSON 檔名")
    records = load_gold(gold_jsonl)
    artifact = train_artifact(
        records,
        embedding_model=model,
        min_precision=min_precision,
        min_npv=min_npv,
        min_calibration_count=min_calibration_count,
        min_test_accept_count=min_test_accept_count,
    )
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = MODEL_DIR / output
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "output": str(path),
        "artifact_digest": artifact["artifact_digest"],
        "production_ready": artifact["production_ready"],
        "test_metrics": artifact["test_metrics"],
    }


def re_fullmatch_safe_name(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", value))
