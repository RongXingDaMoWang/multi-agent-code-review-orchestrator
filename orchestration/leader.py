"""
LeaderAgent: top-level coordinator for multi-agent reviews.

Responsibilities:
1. decompose(pr_info) — pick which roles to spin up based on the diff
2. assign(subtasks)  — pair each SubTask with the right WorkerAgent
3. synthesize(results) — merge accepted WorkerResults into a FinalReview

The Leader itself does not call the LLM in Phase 2; decompose/synthesize
are deterministic so the orchestration boundary is easy to reason about.
"""
import uuid
from pathlib import Path
from typing import Optional

from ..agent.llm_client import LLMClient
from ..agent.skill_loader import SkillLoader
from ..agent.tool_router import ToolRouter
from .schemas import (
    Finding,
    FinalReview,
    SubTask,
    WorkerResult,
    WorkerRole,
)
from .worker import WorkerAgent

# Files we consider "code" for the purpose of role selection.
_CODE_EXTS: tuple[str, ...] = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rs",
    ".rb", ".cpp", ".cc", ".c", ".h", ".hpp", ".kt", ".scala", ".php",
)
_DOC_EXTS: tuple[str, ...] = (".md", ".rst", ".txt", ".adoc")

# Thresholds for promoting performance/architecture roles.
_LARGE_CHANGE_FILES = 5
_LARGE_CHANGE_LINES = 200


class LeaderAgent:
    """Coordinator that decomposes PRs, assigns Workers, and synthesizes results."""

    def __init__(
        self,
        llm_client: LLMClient,
        tool_router: ToolRouter,
        skill_loader: SkillLoader,
        trajectories_dir: Path,
    ):
        self._llm_client = llm_client
        self._tool_router = tool_router
        self._skill_loader = skill_loader
        self._trajectories_dir = trajectories_dir
        # Workers are lazily instantiated per role; same instance is reusable
        # across retry rounds because each .run() call is stateless.
        self._workers: dict[WorkerRole, WorkerAgent] = {}

    # ── Phase A: decompose ────────────────────────────────────────────────

    def decompose(self, pr_info: dict) -> list[SubTask]:
        """Produce role-specific SubTasks based on the PR's file mix."""
        files: list[dict] = pr_info.get("files", []) or []
        owner = pr_info.get("owner", "")
        repo = pr_info.get("repo", "")
        pull_number = pr_info.get("pull_number", 0)

        code_files = [f for f in files if str(f.get("filename", "")).endswith(_CODE_EXTS)]
        doc_files = [f for f in files if str(f.get("filename", "")).endswith(_DOC_EXTS)]
        total_changed = sum(
            int(f.get("additions", 0)) + int(f.get("deletions", 0)) for f in files
        )
        is_large = (
            len(files) > _LARGE_CHANGE_FILES or total_changed > _LARGE_CHANGE_LINES
        )

        roles: list[WorkerRole] = []
        if code_files:
            roles.extend(["security", "style"])
            if is_large:
                roles.extend(["architecture", "performance"])
        elif doc_files:
            roles.append("style")
        else:
            # Unknown files — fall back to a minimal style pass
            roles.append("style")

        # Stable priority: security > architecture > performance > style
        priority = {"security": 90, "architecture": 70, "performance": 60, "style": 40}
        context = {"owner": owner, "repo": repo, "pull_number": pull_number,
                   "total_files": len(files), "total_changed_lines": total_changed}

        return [
            SubTask(
                id=f"st-{uuid.uuid4().hex[:8]}",
                role=role,
                files=code_files if role != "style" else (code_files or doc_files or files),
                context=context,
                priority=priority[role],
            )
            for role in roles
        ]

    # ── Phase B: assign ───────────────────────────────────────────────────

    def assign(
        self,
        subtasks: list[SubTask],
    ) -> dict[WorkerRole, tuple[WorkerAgent, SubTask]]:
        """Pair each SubTask with the corresponding (cached) WorkerAgent."""
        return {
            st.role: (self._get_worker(st.role), st)
            for st in subtasks
        }

    def _get_worker(self, role: WorkerRole) -> WorkerAgent:
        if role not in self._workers:
            self._workers[role] = WorkerAgent(
                role=role,
                llm_client=self._llm_client,
                tool_router=self._tool_router,
                skill_loader=self._skill_loader,
                trajectories_dir=self._trajectories_dir,
            )
        return self._workers[role]

    # ── Phase C: synthesize ───────────────────────────────────────────────

    def synthesize(self, results: list[WorkerResult]) -> FinalReview:
        """Merge accepted WorkerResults into a single FinalReview."""
        all_findings: list[Finding] = []
        per_role: dict[str, dict] = {}
        for r in results:
            per_role[r.role] = {
                "status": r.status,
                "confidence": r.confidence,
                "finding_count": len(r.findings),
                "tokens": r.token_usage,
                "duration_ms": r.duration_ms,
                "session_id": r.session_id,
            }
            if r.status == "ok":
                all_findings.extend(r.findings)

        severity_counts: dict[str, int] = {}
        for f in all_findings:
            severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

        decision = _decide(severity_counts)
        summary = _build_summary(results, severity_counts, decision)
        recommendations = _build_recommendations(all_findings, severity_counts)

        return FinalReview(
            summary=summary,
            findings=all_findings,
            severity_counts=severity_counts,
            recommendations=recommendations,
            decision=decision,
            per_role=per_role,
        )


# ─── Synthesis helpers ────────────────────────────────────────────────────────

def _decide(severity_counts: dict[str, int]) -> str:
    if severity_counts.get("critical", 0) > 0 or severity_counts.get("high", 0) > 0:
        return "REQUEST_CHANGES"
    if severity_counts.get("medium", 0) > 0 or severity_counts.get("low", 0) > 0:
        return "COMMENT"
    return "APPROVE"


def _build_summary(
    results: list[WorkerResult],
    severity_counts: dict[str, int],
    decision: str,
) -> str:
    ok = [r for r in results if r.status == "ok"]
    bad = [r for r in results if r.status != "ok"]
    lines = [
        f"Multi-agent review: **{decision}**",
        f"- Workers OK: {len(ok)} / {len(results)}",
    ]
    if bad:
        lines.append(
            "- Workers failed: " + ", ".join(f"{r.role}({r.status})" for r in bad)
        )
    if severity_counts:
        sev = " ".join(f"{k}={v}" for k, v in severity_counts.items())
        lines.append(f"- Findings: {sev}")
    else:
        lines.append("- Findings: none")
    return "\n".join(lines)


def _build_recommendations(
    findings: list[Finding],
    severity_counts: dict[str, int],
) -> list[str]:
    recs: list[str] = []
    if severity_counts.get("critical", 0):
        recs.append("立即修复 critical 级问题后再合并。")
    if severity_counts.get("high", 0):
        recs.append("修复 high 级问题，或在 PR 描述中说明缓解措施。")
    if not recs and findings:
        recs.append("逐条评估 medium/low 建议；可合并但建议在跟进 PR 处理。")
    if not findings:
        recs.append("未发现明显问题，可批准合并。")
    return recs
