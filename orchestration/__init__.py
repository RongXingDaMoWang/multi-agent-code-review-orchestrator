"""
Multi-agent orchestration layer.

Layered on top of agent/runner.py without modifying it. The pipeline-level
multi_agent_review() entrypoint uses these classes to fan out a PR review
to specialised Workers, validate the batch, and synthesize a FinalReview.
"""
from .leader import LeaderAgent
from .reviewer import ReviewerAgent
from .scheduler import TaskScheduler
from .schemas import (
    Finding,
    FinalReview,
    SubTask,
    ValidatedResults,
    WorkerResult,
    WorkerRole,
)
from .worker import ROLE_FOCUS, ROLE_TOOLS, ScopedToolRouter, WorkerAgent

__all__ = [
    "LeaderAgent",
    "ReviewerAgent",
    "TaskScheduler",
    "WorkerAgent",
    "ScopedToolRouter",
    "ROLE_FOCUS",
    "ROLE_TOOLS",
    "SubTask",
    "WorkerResult",
    "ValidatedResults",
    "FinalReview",
    "Finding",
    "WorkerRole",
]
