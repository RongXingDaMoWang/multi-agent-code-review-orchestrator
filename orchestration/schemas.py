"""
Dataclasses passed between Leader, Worker, Scheduler, and Reviewer.

All structures are plain dataclasses so they can be JSON-serialized for
trajectories and easily extended in later phases.
"""
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

WorkerRole = Literal["security", "performance", "architecture", "style"]
Severity = Literal["critical", "high", "medium", "low", "info"]
Confidence = Literal["high", "medium", "low"]


@dataclass
class SubTask:
    """A unit of work assigned to a single Worker."""

    id: str
    role: WorkerRole
    files: list[dict]           # subset of get_pull_request_files() rows
    context: dict               # owner/repo/pull_number + PR meta
    priority: int = 50          # 0-100, higher = scheduled first

    def short(self) -> str:
        return f"{self.role}/{self.id[:8]}"


@dataclass
class Finding:
    """A single issue surfaced by a Worker."""

    file: str
    line: Optional[int]
    severity: Severity
    category: str               # role-scoped tag (e.g. "sql_injection")
    description: str
    confidence: Confidence = "medium"


@dataclass
class WorkerResult:
    """Output of a single Worker run."""

    subtask_id: str
    role: WorkerRole
    findings: list[Finding] = field(default_factory=list)
    confidence: Confidence = "medium"
    token_usage: dict = field(default_factory=dict)   # input/output tokens
    raw_text: str = ""
    session_id: str = ""
    iterations: int = 0
    duration_ms: int = 0
    status: Literal["ok", "failed", "timeout"] = "ok"
    error: Optional[str] = None


@dataclass
class ValidatedResults:
    """ReviewerAgent verdict over the WorkerResult batch."""

    accepted: list[WorkerResult] = field(default_factory=list)
    rejected: list[WorkerResult] = field(default_factory=list)
    retry: list[SubTask] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)


@dataclass
class FinalReview:
    """LeaderAgent synthesis of all accepted Worker results."""

    summary: str
    findings: list[Finding] = field(default_factory=list)
    severity_counts: dict[str, int] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    decision: str = ""           # APPROVE / REQUEST_CHANGES / COMMENT
    per_role: dict[str, Any] = field(default_factory=dict)
