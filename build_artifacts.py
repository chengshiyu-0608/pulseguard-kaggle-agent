from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_PANEL = Path(os.getenv("PULSEGUARD_PANEL", "data/risk_panel_reproduced.pkl"))

FEATURES = [
    "log_submissions_current",
    "log_active_days_current",
    "log_active_competitions_current",
    "recent_activity_momentum_lag1",
    "log_hist_avg_submissions_lag1",
    "log_n_comp_sources_lag1",
    "effort_momentum_mismatch_lag1",
    "log_cum_months_lag1",
    "log_cum_submissions_lag1",
]

FEATURE_LABELS = {
    "log_submissions_current": "当月提交集中度",
    "log_active_days_current": "当月活跃天数",
    "log_active_competitions_current": "当月参与竞赛数",
    "recent_activity_momentum_lag1": "近期活跃动量",
    "log_hist_avg_submissions_lag1": "历史月均提交",
    "log_n_comp_sources_lag1": "竞赛资源连接",
    "effort_momentum_mismatch_lag1": "投入与动量错配",
    "log_cum_months_lag1": "历史参与时长",
    "log_cum_submissions_lag1": "历史累计提交",
}


def sigmoid(value: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(value, -30, 30)))


def fit_logistic(x: np.ndarray, y: np.ndarray, l2: float = 0.1) -> np.ndarray:
    beta = np.zeros(x.shape[1], dtype=float)
    penalty = np.full(len(beta), l2, dtype=float)
    penalty[0] = 0.0
    for _ in range(120):
        probability = sigmoid(x @ beta)
        weight = np.maximum(probability * (1.0 - probability), 1e-6)
        gradient = x.T @ (probability - y) + penalty * beta
        hessian = (x.T * weight) @ x + np.diag(penalty)
        step = np.linalg.solve(hessian, gradient)
        beta -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def roc_auc(y: np.ndarray, score: np.ndarray) -> float:
    order = np.argsort(score, kind="mergesort")
    sorted_score = score[order]
    ranks = np.empty(len(score), dtype=float)
    start = 0
    while start < len(score):
        end = start + 1
        while end < len(score) and sorted_score[end] == sorted_score[start]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2.0
        start = end
    positives = y == 1
    n_pos = int(positives.sum())
    n_neg = int(len(y) - n_pos)
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def classification_metrics(y: np.ndarray, score: np.ndarray, threshold: float = 0.5) -> dict:
    prediction = score >= threshold
    tp = int(((prediction == 1) & (y == 1)).sum())
    tn = int(((prediction == 0) & (y == 0)).sum())
    fp = int(((prediction == 1) & (y == 0)).sum())
    fn = int(((prediction == 0) & (y == 1)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "rows": int(len(y)),
        "positive_rate": float(y.mean()),
        "roc_auc": roc_auc(y, score),
        "accuracy": float((prediction == y).mean()),
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "brier_score": float(np.mean((score - y) ** 2)),
        "confusion_matrix": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
    }


def anonymize(user_id: int) -> str:
    digest = hashlib.sha256(f"pulseguard-{int(user_id)}".encode("ascii")).hexdigest()
    return f"U-{digest[:6].upper()}"


def prepare_panel(panel_path: Path) -> pd.DataFrame:
    panel = pd.read_pickle(panel_path).sort_values(["SubmittedUserId", "month"]).copy()
    activity_columns = ["submissions", "active_days", "active_competitions"]
    panel["is_active"] = (panel[activity_columns].sum(axis=1) > 0).astype(int)
    grouped = panel.groupby("SubmittedUserId", sort=False)
    for offset in (1, 2, 3):
        panel[f"inactive_future_{offset}"] = grouped["is_active"].shift(-offset).eq(0)
    panel["has_future_window"] = grouped["month"].shift(-3).notna()
    panel["future_silent_3m"] = (
        panel["inactive_future_1"]
        & panel["inactive_future_2"]
        & panel["inactive_future_3"]
    ).astype(int)

    panel["log_submissions_current"] = np.log1p(panel["submissions"])
    panel["log_active_days_current"] = np.log1p(panel["active_days"])
    panel["log_active_competitions_current"] = np.log1p(panel["active_competitions"])
    panel["log_hist_avg_submissions_lag1"] = np.log1p(panel["hist_avg_submissions_lag1"])
    panel["log_cum_months_lag1"] = np.log1p(panel["cum_months_lag1"])
    panel["log_cum_submissions_lag1"] = np.log1p(panel["cum_submissions_lag1"])
    panel["anonymous_user_id"] = panel["SubmittedUserId"].map(anonymize)
    return panel


def matrix_with_preprocessor(
    frame: pd.DataFrame,
    medians: np.ndarray,
    means: np.ndarray,
    scales: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    raw = frame[FEATURES].to_numpy(float)
    missing = np.isnan(raw).astype(float)
    imputed = np.where(np.isnan(raw), medians, raw)
    standardized = (imputed - means) / scales
    return np.column_stack([np.ones(len(frame)), standardized, missing]), standardized


def risk_tier(score: float) -> str:
    if score >= 0.70:
        return "高风险"
    if score >= 0.45:
        return "中风险"
    return "低风险"


def serialize_number(value: float | int | np.number | None) -> float | int | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, np.integer)):
        return int(value)
    return round(float(value), 6)


def build(panel_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    panel = prepare_panel(panel_path)
    modeling = panel[(panel["is_active"] == 1) & panel["has_future_window"]].copy()
    train = modeling[modeling["month"] <= "2023-12-01"].copy()
    test = modeling[modeling["month"] > "2023-12-01"].copy()

    train_raw = train[FEATURES].to_numpy(float)
    medians = np.nanmedian(train_raw, axis=0)
    train_imputed = np.where(np.isnan(train_raw), medians, train_raw)
    means = train_imputed.mean(axis=0)
    scales = train_imputed.std(axis=0)
    scales[scales < 1e-8] = 1.0

    x_train, _ = matrix_with_preprocessor(train, medians, means, scales)
    x_test, _ = matrix_with_preprocessor(test, medians, means, scales)
    y_train = train["future_silent_3m"].to_numpy(float)
    y_test = test["future_silent_3m"].to_numpy(float)
    coefficients = fit_logistic(x_train, y_train, l2=0.1)
    train_scores = sigmoid(x_train @ coefficients)
    test_scores = sigmoid(x_test @ coefficients)

    baseline_score = np.full(len(y_test), y_train.mean())
    evaluation = {
        "label_definition": "当前月有活动，随后连续3个自然月无提交、无活跃天数且无参赛记录",
        "training_period": f"{train['month'].min():%Y-%m} 至 {train['month'].max():%Y-%m}",
        "test_period": f"{test['month'].min():%Y-%m} 至 {test['month'].max():%Y-%m}",
        "train": classification_metrics(y_train, train_scores),
        "test": classification_metrics(y_test, test_scores),
        "baseline": classification_metrics(y_test, baseline_score),
        "limitations": [
            "Meta Kaggle为公开行为数据，无法观察用户主观动机。",
            "时间留出测试中可能包含训练期已出现过的同一用户。",
            "风险分数用于运营排序和解释，不代表因果概率或真实业务上线效果。",
        ],
    }

    model = {
        "version": "1.0.0",
        "algorithm": "L2 regularized logistic regression (NumPy IRLS)",
        "features": FEATURES,
        "feature_labels": FEATURE_LABELS,
        "medians": medians.tolist(),
        "means": means.tolist(),
        "scales": scales.tolist(),
        "coefficients": coefficients.tolist(),
        "risk_thresholds": {"high": 0.70, "medium": 0.45},
        "train_cutoff": "2023-12-01",
    }

    latest_active = (
        panel[panel["is_active"] == 1]
        .sort_values(["SubmittedUserId", "month"])
        .groupby("SubmittedUserId", as_index=False)
        .tail(1)
        .copy()
    )
    x_latest, standardized_latest = matrix_with_preprocessor(latest_active, medians, means, scales)
    raw_latest = latest_active[FEATURES].to_numpy(float)
    latest_active["risk_score"] = sigmoid(x_latest @ coefficients)
    latest_active["risk_tier"] = latest_active["risk_score"].map(risk_tier)

    feature_coefficients = coefficients[1 : 1 + len(FEATURES)]
    missing_coefficients = coefficients[1 + len(FEATURES) :]
    user_rows = []
    for row_position, (_, row) in enumerate(latest_active.iterrows()):
        contributions = standardized_latest[row_position] * feature_coefficients
        factor_candidates = []
        for index, feature in enumerate(FEATURES):
            if np.isnan(raw_latest[row_position, index]):
                contribution = missing_coefficients[index]
                factor_candidates.append(
                    {
                        "feature": f"missing_{feature}",
                        "label": FEATURE_LABELS[feature] + "信息不足",
                        "contribution": round(float(contribution), 4),
                        "direction": "提高风险" if contribution > 0 else "降低风险",
                    }
                )
            else:
                contribution = contributions[index]
                factor_candidates.append(
                    {
                        "feature": feature,
                        "label": FEATURE_LABELS[feature],
                        "contribution": round(float(contribution), 4),
                        "direction": "提高风险" if contribution > 0 else "降低风险",
                    }
                )
        factors = sorted(
            factor_candidates,
            key=lambda item: abs(item["contribution"]),
            reverse=True,
        )[:4]
        user_rows.append(
            {
                "user_id": row["anonymous_user_id"],
                "source_user_id": int(row["SubmittedUserId"]),
                "month": row["month"].strftime("%Y-%m"),
                "risk_score": round(float(row["risk_score"]), 4),
                "risk_tier": row["risk_tier"],
                "submissions": serialize_number(row["submissions"]),
                "active_days": serialize_number(row["active_days"]),
                "active_competitions": serialize_number(row["active_competitions"]),
                "recent_activity_momentum": serialize_number(row["recent_activity_momentum_lag1"]),
                "historical_average_submissions": serialize_number(row["hist_avg_submissions_lag1"]),
                "competition_sources": serialize_number(row["n_comp_sources_lag1"]),
                "cumulative_months": serialize_number(row["cum_months_lag1"]),
                "cumulative_submissions": serialize_number(row["cum_submissions_lag1"]),
                "factors": factors,
            }
        )
    user_rows.sort(key=lambda item: item["risk_score"], reverse=True)

    overview = {
        "source": "Meta Kaggle public activity data",
        "panel_rows": int(len(panel)),
        "panel_users": int(panel["SubmittedUserId"].nunique()),
        "modeling_rows": int(len(modeling)),
        "modeling_users": int(modeling["SubmittedUserId"].nunique()),
        "latest_users": len(user_rows),
        "risk_distribution": {
            tier: sum(1 for row in user_rows if row["risk_tier"] == tier)
            for tier in ("高风险", "中风险", "低风险")
        },
        "test_auc": evaluation["test"]["roc_auc"],
        "test_accuracy": evaluation["test"]["accuracy"],
    }

    (output_dir / "model.json").write_text(
        json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "evaluation.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "users.json").write_text(
        json.dumps(user_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "overview.json").write_text(
        json.dumps(overview, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    with (output_dir / "evaluation_cases.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["user_id", "month", "actual_future_silence", "risk_score", "prediction"])
        for (_, row), score in zip(test.iterrows(), test_scores):
            writer.writerow(
                [
                    anonymize(int(row["SubmittedUserId"])),
                    row["month"].strftime("%Y-%m"),
                    int(row["future_silent_3m"]),
                    round(float(score), 6),
                    int(score >= 0.5),
                ]
            )

    print(json.dumps({"overview": overview, "evaluation": evaluation["test"]}, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build PulseGuard model and demo artifacts.")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "artifacts")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    build(arguments.panel, arguments.output_dir)
