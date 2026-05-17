"""
ReviewerAgent: quality control over the WorkerResult batch.

Runs deterministic checks across all WorkerResults:
- Worker status: ok / failed / timeout
- Conflict detection: same file+line surfaced by two roles with
  incompatible severities
- Confidence assessment per Worker
- Retry decision: failed/timeout Workers get added to the retry list
  (Leader applies a max-1-round retry policy upstream)
"""
from typing import Iterable, Optional

from .schemas import (
    Confidence,
    Finding,
    SubTask,
    ValidatedResults,
    WorkerResult,
)


# Severities considered "blocking" — disagreeing here counts as a conflict.
_BLOCKING_SEVERITIES = {"critical", "high"}


class ReviewerAgent:
    """Quality-control gate between Workers and Leader.synthesize()."""

    def __init__(self, min_confidence: Confidence = "low"):
        self._min_confidence = min_confidence

    def validate(
        self,
        results: list[WorkerResult],
        subtasks: list[SubTask],
    ) -> ValidatedResults:
        """Partition results into accepted/rejected and emit a retry list."""
        subtask_by_id = {s.id: s for s in subtasks}

        accepted: list[WorkerResult] = []
        rejected: list[WorkerResult] = []
        retry: list[SubTask] = []

        for r in results:
            # Re-evaluate confidence in case Worker default was conservative
            r.confidence = _grade_confidence(r)

            if r.status != "ok":
                rejected.append(r)
                st = subtask_by_id.get(r.subtask_id)
                if st is not None:
                    retry.append(st)
                continue

            if _below_threshold(r.confidence, self._min_confidence):
                rejected.append(r)
                continue

            accepted.append(r)

        conflicts = _detect_conflicts(accepted)
        return ValidatedResults(
            accepted=accepted,
            rejected=rejected,
            retry=retry,
            conflicts=conflicts,
        )


# ─── Helpers ──────────────────────────────────────────────────────────────────

_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def _below_threshold(actual: Confidence, minimum: Confidence) -> bool:
    return _CONFIDENCE_ORDER[actual] < _CONFIDENCE_ORDER[minimum]


def _grade_confidence(r: WorkerResult) -> Confidence:
    """Confidence heuristic: failures are low, sparse outputs are low,
    Workers that used tools and surfaced findings are high."""
    if r.status != "ok":
        return "low"
    if r.iterations == 0 or not r.raw_text:
        return "low"
    if r.findings and r.token_usage.get("output", 0) > 200:
        return "high"
    if r.findings:
        return "medium"
    # No findings but Worker ran cleanly → medium (an "all clear" signal)
    return "medium"


def _detect_conflicts(accepted: list[WorkerResult]) -> list[dict]:
    """Flag the same (file, line) flagged by two roles with disagreeing
    severities. Two ‘critical’ from different roles is NOT a conflict
    (it's reinforcement); a critical vs low IS a conflict."""
    by_location: dict[tuple[str, Optional[int]], list[tuple[str, Finding]]] = {}
    for r in accepted:
        for f in r.findings:
            by_location.setdefault((f.file, f.line), []).append((r.role, f))

    conflicts: list[dict] = []
    for (file, line), entries in by_location.items():
        if len(entries) < 2:
            continue
        severities = {f.severity for _, f in entries}
        # Conflict when at least one is blocking and at least one is not.
        if severities & _BLOCKING_SEVERITIES and (severities - _BLOCKING_SEVERITIES):
            conflicts.append({
                "file": file,
                "line": line,
                "entries": [
                    {"role": role, "severity": f.severity,
                     "category": f.category, "description": f.description[:120]}
                    for role, f in entries
                ],
            })
    return conflicts
