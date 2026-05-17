"""
OrchestrationMetrics: live aggregator for a multi-agent review run.

Mutated incrementally by pipeline.orchestrator.multi_agent_review() as
each phase completes. `print_summary()` returns a human-readable, single
string suitable for CLI output.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .schemas import WorkerResult, WorkerRole, ValidatedResults


@dataclass
class OrchestrationMetrics:
    """Per-run metrics. One instance per orchestrator invocation."""

    task_id: str = ""
    total_tasks: int = 0                                    # initial subtask count
    retry_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    conflicts_count: int = 0

    # role → list of durations in milliseconds (so callers can compute avg/max)
    _durations_ms: dict[str, list[float]] = field(default_factory=dict)

    # role → {"input": tokens, "output": tokens}
    token_budget: dict[str, dict[str, int]] = field(default_factory=dict)

    # role → status count: {"ok": N, "failed": N, "timeout": N}
    status_counts: dict[str, dict[str, int]] = field(default_factory=dict)

    # severity counts merged across accepted workers
    severity_counts: dict[str, int] = field(default_factory=dict)

    decision: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None

    # ── Mutators ─────────────────────────────────────────────────────────

    def record_dispatch(self, role: WorkerRole, count: int = 1) -> None:
        """Tally a SubTask being assigned. Bumps total_tasks."""
        self.total_tasks += count

    def record_worker(self, r: WorkerResult) -> None:
        """Record one Worker's completion (success, failure, or timeout)."""
        self._durations_ms.setdefault(r.role, []).append(float(r.duration_ms))

        bucket = self.token_budget.setdefault(r.role, {"input": 0, "output": 0})
        bucket["input"] += int(r.token_usage.get("input", 0))
        bucket["output"] += int(r.token_usage.get("output", 0))

        scount = self.status_counts.setdefault(r.role, {})
        scount[r.status] = scount.get(r.status, 0) + 1

    def record_verdict(self, v: ValidatedResults) -> None:
        """Record the Reviewer's verdict over the Worker batch."""
        self.accepted_count = len(v.accepted)
        self.rejected_count = len(v.rejected)
        self.retry_count += len(v.retry)
        self.conflicts_count = len(v.conflicts)

    def finalize(self, severity_counts: dict[str, int], decision: str) -> None:
        """Capture end-of-run totals."""
        self.severity_counts = dict(severity_counts)
        self.decision = decision
        self.ended_at = time.time()

    # ── Derived views ────────────────────────────────────────────────────

    @property
    def worker_durations(self) -> dict[str, float]:
        """role → average duration in ms (the field the spec calls out)."""
        return {
            role: (sum(durs) / len(durs)) if durs else 0.0
            for role, durs in self._durations_ms.items()
        }

    @property
    def retry_rate(self) -> float:
        if self.total_tasks <= 0:
            return 0.0
        return self.retry_count / self.total_tasks

    @property
    def total_duration_ms(self) -> int:
        end = self.ended_at if self.ended_at is not None else time.time()
        return int((end - self.started_at) * 1000)

    @property
    def total_tokens(self) -> dict[str, int]:
        total_in = sum(b.get("input", 0) for b in self.token_budget.values())
        total_out = sum(b.get("output", 0) for b in self.token_budget.values())
        return {"input": total_in, "output": total_out}

    # ── Output ───────────────────────────────────────────────────────────

    def print_summary(self) -> str:
        """Return a multi-line, human-readable summary."""
        lines: list[str] = ["═" * 56, "Orchestration Metrics", "═" * 56]
        lines.append(f"task_id          : {self.task_id or '(unset)'}")
        lines.append(f"decision         : {self.decision or '(unset)'}")
        lines.append(f"total subtasks   : {self.total_tasks}")
        lines.append(f"accepted/rejected: {self.accepted_count} / {self.rejected_count}")
        lines.append(f"retry count      : {self.retry_count}  (rate {self.retry_rate:.0%})")
        lines.append(f"conflicts        : {self.conflicts_count}")
        lines.append(f"total duration   : {self.total_duration_ms} ms")

        toks = self.total_tokens
        lines.append(f"tokens (in/out)  : {toks['input']} / {toks['output']}")

        if self._durations_ms:
            lines.append("")
            lines.append("per-role:")
            lines.append(f"  {'role':<14} {'avg_ms':>8} {'runs':>5} "
                         f"{'tok_in':>7} {'tok_out':>8} status")
            for role in sorted(self._durations_ms):
                durs = self._durations_ms[role]
                avg = sum(durs) / len(durs)
                tb = self.token_budget.get(role, {"input": 0, "output": 0})
                sc = self.status_counts.get(role, {})
                status_str = " ".join(f"{k}={v}" for k, v in sorted(sc.items()))
                lines.append(
                    f"  {role:<14} {avg:>8.0f} {len(durs):>5} "
                    f"{tb['input']:>7} {tb['output']:>8} {status_str}"
                )

        if self.severity_counts:
            sev = " ".join(
                f"{k}={v}" for k, v in
                sorted(self.severity_counts.items(),
                       key=lambda kv: ["critical", "high", "medium", "low", "info"]
                       .index(kv[0]) if kv[0] in
                       ["critical", "high", "medium", "low", "info"] else 99)
            )
            lines.append("")
            lines.append(f"findings by severity: {sev}")
        lines.append("═" * 56)
        return "\n".join(lines)
