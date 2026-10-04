"""Shared data and ranking contracts; no executable ranking policy."""
from dataclasses import dataclass
from typing import Any, TypedDict


class DecisionInput(TypedDict):
    candidates: Any
    X: list[list[float | None]]
    criterion_definitions: list[dict]
    weights: dict[str, float]
    missing_mask: list[list[bool]]
    comparison_groups: dict[str, str | None]
    provenance: list[dict]
    dataset_version: str | None
    feature_rule_version: str | None
    decision_status: str
    decision_reason: str
    eligible_indices: list[int]
    backup_indices: list[int]
    coverage_summary: dict
    selected_run_ids: list[dict[str, str | None]]
    selection_rules: dict


@dataclass
class RankingResult:
    status: str
    ranked: Any
    backups: Any
    reason: str
    dataset_version: str | None
    feature_rule_version: str | None
    algorithm_version: str
    active_criteria: list[str]
