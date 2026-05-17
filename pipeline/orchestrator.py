"""
Pipeline orchestrator: coordinates the 4-phase code review pipeline.

Phase 1: Triage   — classify PR, decide auto vs human
Phase 2: Analyze  — collect PR data (diff, commits, memory rules)
Phase 3: Review   — LLM analysis of code quality
Phase 4: Act      — submit GitHub review + optional fix PR
"""
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..agent.runner import AgentRunner
from ..agent.llm_client import LLMClient
from ..agent.tool_router import ToolRouter
from ..agent.skill_loader import SkillLoader
from ..orchestration import (
    FinalReview,
    LeaderAgent,
    ReviewerAgent,
    TaskScheduler,
    ValidatedResults,
    WorkerResult,
)
from ..orchestration.metrics import OrchestrationMetrics
from ..tools.github_tools import GitHubTools
from ..tools.memory_tools import get_all_summary, get_repo_patterns
from ..trajectory.logger import TrajectoryLogger
from ..trajectory.schemas import AgentResult


@dataclass
class ReviewResult:
    pr: str
    decision: str = ""
    issues_found: int = 0
    fix_pr_number: Optional[int] = None
    human_required: bool = False
    skip_reason: str = ""
    session_id: str = ""
    stats: dict = field(default_factory=dict)
    error: Optional[str] = None
    # Populated only by multi_agent_review(); None for single-agent runs.
    multi_agent: Optional[dict] = None


def parse_pr_identifier(pr_input: str) -> tuple[str, str, int]:
    """
    Parse PR identifier into (owner, repo, pr_number).

    Supports:
    - "owner/repo #42"
    - "owner/repo PR 42"
    - "github.com/owner/repo/pull/42"
    - "https://github.com/owner/repo/pull/42"
    """
    # URL format
    url_match = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_input)
    if url_match:
        return url_match.group(1), url_match.group(2), int(url_match.group(3))

    # "owner/repo #42" or "owner/repo PR 42"
    short_match = re.search(r"([^/\s]+)/([^/\s#]+)\s*(?:#|PR\s+)(\d+)", pr_input, re.IGNORECASE)
    if short_match:
        return short_match.group(1), short_match.group(2), int(short_match.group(3))

    raise ValueError(
        f"Cannot parse PR identifier: {pr_input!r}\n"
        "Expected format: owner/repo #42 or https://github.com/owner/repo/pull/42"
    )


class ReviewOrchestrator:
    """
    Drives the full code review pipeline using the AgentRunner.
    The LLM (via AgentRunner) handles all phases guided by the skill system prompt.
    """

    def __init__(
        self,
        github_token: str = "",
        anthropic_api_key: str = "",
        model: str = "",
        base_url: str = "",
        trajectories_dir: Optional[Path] = None,
        dry_run: bool = False,
        enable_thinking: bool = True,
    ):
        self.trajectories_dir = trajectories_dir or Path("./trajectories")
        self.trajectories_dir.mkdir(parents=True, exist_ok=True)

        # Build components
        llm_client = LLMClient(
            api_key=anthropic_api_key,
            model=model or None,
            base_url=base_url or None,
            enable_thinking=enable_thinking,
        )
        tool_router = ToolRouter(github_token=github_token, dry_run=dry_run)
        skill_loader = SkillLoader()

        # Retained for multi_agent_review() prefetch and for spawning Workers.
        self._llm_client = llm_client
        self._tool_router = tool_router
        self._skill_loader = skill_loader
        self._github_tools = GitHubTools(token=github_token)
        self._dry_run = dry_run

        self.runner = AgentRunner(
            llm_client=llm_client,
            tool_router=tool_router,
            skill_loader=skill_loader,
            trajectories_dir=self.trajectories_dir,
            skill_name="code-review",
        )

    def review(self, pr_input: str) -> ReviewResult:
        """
        Run a full code review for the given PR.

        Args:
            pr_input: PR identifier (e.g. "owner/repo #42" or GitHub URL)
        """
        try:
            owner, repo, pr_number = parse_pr_identifier(pr_input)
        except ValueError as e:
            return ReviewResult(pr=pr_input, error=str(e))

        pr_label = f"{owner}/{repo}#{pr_number}"
        task = f"review {owner}/{repo} #{pr_number}"

        result: AgentResult = self.runner.run(task=task, pr=pr_label)

        return ReviewResult(
            pr=pr_label,
            decision=result.stats.get("decision", ""),
            issues_found=result.stats.get("issues_found", 0),
            session_id=result.session_id,
            stats=result.stats,
        )

    # ── Mode-explicit aliases ────────────────────────────────────────────

    def single_agent_review(self, pr_input: str) -> ReviewResult:
        """Run the original single-Agent loop. Identical to review()."""
        return self.review(pr_input)

    def multi_agent_review(self, pr_input: str) -> ReviewResult:
        """Run the Leader-Worker-Reviewer multi-agent pipeline.

        Flow: parse PR → prefetch files → Leader.decompose → Scheduler
        dispatch (parallel, semaphore=3, 60s/task) → Reviewer.validate →
        optional 1-round retry → Leader.synthesize. Phase 3 additions:
        emits orchestration_* events to a `orch_<task_id>.jsonl` trace
        and tallies an OrchestrationMetrics in parallel.
        """
        try:
            owner, repo, pr_number = parse_pr_identifier(pr_input)
        except ValueError as e:
            return ReviewResult(pr=pr_input, error=str(e))

        pr_label = f"{owner}/{repo}#{pr_number}"

        try:
            pr_meta = self._github_tools.get_pull_request(owner, repo, pr_number)
            files = self._github_tools.get_pull_request_files(owner, repo, pr_number)
        except Exception as e:
            return ReviewResult(pr=pr_label, error=f"prefetch failed: {e}")

        pr_info = {
            "owner": owner, "repo": repo, "pull_number": pr_number,
            "meta": pr_meta, "files": files,
        }

        leader = LeaderAgent(
            llm_client=self._llm_client,
            tool_router=self._tool_router,
            skill_loader=self._skill_loader,
            trajectories_dir=self.trajectories_dir,
        )
        reviewer = ReviewerAgent()
        scheduler = TaskScheduler(max_concurrency=3, per_task_timeout_s=60.0)

        subtasks = leader.decompose(pr_info)
        if not subtasks:
            return ReviewResult(pr=pr_label, decision="APPROVE",
                                skip_reason="no reviewable files",
                                multi_agent={"workers": 0})

        # ── Phase 3: open orchestration trace + metrics ─────────────
        task_id = uuid.uuid4().hex[:12]
        metrics = OrchestrationMetrics(task_id=task_id)
        logger = TrajectoryLogger(
            output_dir=self.trajectories_dir,
            session_id=task_id, pr=pr_label, prefix="orch",
        )
        retried: list[WorkerResult] = []

        try:
            logger.log_orchestration_start(
                task_id=task_id, pr_url=pr_label, mode="multi"
            )

            # Round 1
            assignments = leader.assign(subtasks)
            for st in subtasks:
                metrics.record_dispatch(st.role)
                logger.log_subtask_dispatched(
                    task_id=task_id, subtask_id=st.id, role=st.role,
                    worker_id=f"worker-{st.role}",
                    priority=st.priority, file_count=len(st.files),
                )
            results = scheduler.run_all_sync(assignments)
            for r in results:
                metrics.record_worker(r)
                logger.log_worker_completed(
                    task_id=task_id, subtask_id=r.subtask_id, role=r.role,
                    duration_ms=r.duration_ms, token_usage=r.token_usage,
                    findings_count=len(r.findings), status=r.status,
                    confidence=r.confidence, session_id=r.session_id,
                )
            validated = reviewer.validate(results, subtasks)
            metrics.record_verdict(validated)
            logger.log_reviewer_verdict(
                task_id=task_id,
                accepted_count=len(validated.accepted),
                rejected_count=len(validated.rejected),
                retry_count=len(validated.retry),
                conflicts_count=len(validated.conflicts),
            )

            # Round 2: at most one retry pass for failed/timeout workers
            if validated.retry:
                retry_assignments = leader.assign(validated.retry)
                for st in validated.retry:
                    logger.log_subtask_dispatched(
                        task_id=task_id, subtask_id=st.id, role=st.role,
                        worker_id=f"worker-{st.role}-retry",
                        priority=st.priority, file_count=len(st.files),
                    )
                retried = scheduler.run_all_sync(retry_assignments)
                for r in retried:
                    metrics.record_worker(r)
                    logger.log_worker_completed(
                        task_id=task_id, subtask_id=r.subtask_id, role=r.role,
                        duration_ms=r.duration_ms, token_usage=r.token_usage,
                        findings_count=len(r.findings), status=r.status,
                        confidence=r.confidence, session_id=r.session_id,
                    )
                results = _merge_retries(results, retried)
                validated = reviewer.validate(results, subtasks)
                metrics.record_verdict(validated)
                logger.log_reviewer_verdict(
                    task_id=task_id,
                    accepted_count=len(validated.accepted),
                    rejected_count=len(validated.rejected),
                    retry_count=len(validated.retry),
                    conflicts_count=len(validated.conflicts),
                )

            final: FinalReview = leader.synthesize(validated.accepted)
            metrics.finalize(final.severity_counts, final.decision)
            logger.log_orchestration_end(
                task_id=task_id,
                total_duration_ms=metrics.total_duration_ms,
                total_tokens=metrics.total_tokens,
                final_severity_counts=final.severity_counts,
                decision=final.decision,
            )
        finally:
            logger.close(extra_stats={"task_id": task_id, "mode": "multi"})

        return ReviewResult(
            pr=pr_label,
            decision=final.decision,
            issues_found=len(final.findings),
            session_id=task_id,
            stats={
                "decision": final.decision,
                "issues_found": len(final.findings),
                "severity_counts": final.severity_counts,
                "workers_total": len(results),
                "workers_accepted": len(validated.accepted),
                "workers_rejected": len(validated.rejected),
                "retry_count": len(retried),
                "conflicts": len(validated.conflicts),
                "total_duration_ms": metrics.total_duration_ms,
                "total_tokens": metrics.total_tokens,
            },
            multi_agent={
                "task_id": task_id,
                "trace_file": str(logger.output_path),
                "summary": final.summary,
                "metrics_summary": metrics.print_summary(),
                "recommendations": final.recommendations,
                "per_role": final.per_role,
                "conflicts": validated.conflicts,
                "findings": [f.__dict__ for f in final.findings],
            },
        )

    def batch_review(
        self,
        pr_list: list[str],
        max_workers: int = 1,
    ) -> list[ReviewResult]:
        """
        Review multiple PRs. Sequential by default (max_workers=1).
        Set max_workers > 1 for concurrent execution.
        """
        if max_workers <= 1:
            return [self.review(pr) for pr in pr_list]

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self.review, pr) for pr in pr_list]
            return [f.result() for f in futures]

    def generate_health_report(self, repo: str) -> str:
        """
        Generate a markdown health report for a repository based on
        accumulated cross-PR pattern statistics in memory.

        Args:
            repo: Repository in "owner/repo" format.

        Returns:
            Markdown-formatted health report string.
        """
        from datetime import datetime, timezone

        patterns = get_repo_patterns(repo)

        if not patterns:
            return (
                f"# Code Health Report: {repo}\n\n"
                f"*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*\n\n"
                "No pattern data available yet. Run some PR reviews first to accumulate data.\n"
            )

        # Sort patterns by occurrence_count descending
        patterns_sorted = sorted(
            patterns,
            key=lambda r: r["content"].get("occurrence_count", 0),
            reverse=True,
        )

        # Group by severity
        by_severity: dict[str, list] = {"critical": [], "high": [], "medium": [], "low": []}
        for p in patterns_sorted:
            sev = p["content"].get("severity", "low")
            if sev in by_severity:
                by_severity[sev].append(p)
            else:
                by_severity["low"].append(p)

        total_issues = sum(p["content"].get("occurrence_count", 0) for p in patterns)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            f"# Code Health Report: {repo}",
            f"",
            f"*Generated: {now_str}*",
            f"",
            f"## Summary",
            f"",
            f"- **Total issue occurrences tracked**: {total_issues}",
            f"- **Distinct patterns**: {len(patterns)}",
            f"- **Critical patterns**: {len(by_severity['critical'])}",
            f"- **High severity patterns**: {len(by_severity['high'])}",
            f"",
        ]

        # Severity sections
        severity_labels = {
            "critical": "🔴 Critical",
            "high": "🟠 High",
            "medium": "🟡 Medium",
            "low": "🟢 Low",
        }

        for sev, label in severity_labels.items():
            items = by_severity[sev]
            if not items:
                continue
            lines.append(f"## {label} Severity Patterns")
            lines.append("")
            lines.append("| Pattern | Category | Occurrences | Trend | Last Seen |")
            lines.append("|---------|----------|-------------|-------|-----------|")
            for p in items:
                c = p["content"]
                pattern_name = c.get("pattern_name", "unknown")
                category = c.get("category", "unknown")
                count = c.get("occurrence_count", 0)
                trend = c.get("trend", "stable")
                last_seen = c.get("last_seen", "")[:10] if c.get("last_seen") else "unknown"
                trend_icon = "📈" if trend == "increasing" else ("🆕" if trend == "new" else "➡️")
                lines.append(f"| `{pattern_name}` | {category} | {count} | {trend_icon} {trend} | {last_seen} |")
            lines.append("")

        # Top affected files
        all_files: dict[str, int] = {}
        for p in patterns:
            for f in p["content"].get("affected_files", []):
                all_files[f] = all_files.get(f, 0) + p["content"].get("occurrence_count", 0)

        if all_files:
            top_files = sorted(all_files.items(), key=lambda x: x[1], reverse=True)[:10]
            lines.append("## Top Affected Files")
            lines.append("")
            lines.append("| File | Issue Count |")
            lines.append("|------|-------------|")
            for file_path, count in top_files:
                lines.append(f"| `{file_path}` | {count} |")
            lines.append("")

        # Recommendations
        lines.append("## Recommendations")
        lines.append("")
        if by_severity["critical"]:
            lines.append("1. 🚨 **Immediate action required**: Address all critical security patterns")
        if by_severity["high"]:
            top_high = by_severity["high"][0]["content"]
            lines.append(
                f"2. 🔧 **High priority**: `{top_high.get('pattern_name')}` "
                f"has occurred {top_high.get('occurrence_count')} times — consider adding a linter rule"
            )
        lines.append("3. 📋 Consider adding pre-commit hooks to catch recurring patterns automatically")
        lines.append("4. 📚 Schedule a team knowledge-sharing session on the top issue categories")
        lines.append("")
        lines.append("---")
        lines.append(f"*Report generated by Code Review Agent*")

        return "\n".join(lines)


def _merge_retries(
    original: list[WorkerResult],
    retried: list[WorkerResult],
) -> list[WorkerResult]:
    """Replace failed/timeout WorkerResults with their retried counterparts.

    Match by subtask_id; retried results with status=="ok" win, otherwise
    keep whichever had findings, otherwise the latest attempt.
    """
    retry_by_id = {r.subtask_id: r for r in retried}
    merged: list[WorkerResult] = []
    for r in original:
        rep = retry_by_id.get(r.subtask_id)
        if rep is None:
            merged.append(r)
            continue
        if r.status != "ok" or (rep.status == "ok" and len(rep.findings) >= len(r.findings)):
            merged.append(rep)
        else:
            merged.append(r)
    return merged
