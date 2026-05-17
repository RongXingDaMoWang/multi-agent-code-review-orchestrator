"""
Unit tests for the multi-agent orchestration layer.

Covers: LeaderAgent.decompose, TaskScheduler concurrency/timeout/failure,
ReviewerAgent.validate conflict detection, SkillRegistry matching.

All tests use mocks — no real API keys or network calls needed.

Run:
    python -m pytest tests/test_orchestration.py -v
"""
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure package importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# Stub external deps before importing project code
_openai = types.ModuleType("openai")
_openai.OpenAI = MagicMock
sys.modules.setdefault("openai", _openai)
_gh = types.ModuleType("github")
_gh.Github = MagicMock
_gh.GithubException = Exception
sys.modules.setdefault("github", _gh)

from code_review_agent.orchestration import (
    LeaderAgent,
    ReviewerAgent,
    TaskScheduler,
)
from code_review_agent.orchestration.schemas import (
    Finding,
    SubTask,
    ValidatedResults,
    WorkerResult,
)
from code_review_agent.orchestration.skill_registry import SkillRegistry


# ─── Helpers ──────────────────────────────────────────────────────────────────

class StubLeader(LeaderAgent):
    """LeaderAgent without __init__ deps (no LLM/ToolRouter needed)."""
    def __init__(self):
        pass


def _make_pr_info(files: list) -> dict:
    return {"owner": "o", "repo": "r", "pull_number": 1, "files": files}


class FakeWorker:
    """Simulates a Worker that sleeps then returns a result."""
    def __init__(self, role: str, sleep_s: float = 0.0, fail: bool = False):
        self.role = role
        self._sleep = sleep_s
        self._fail = fail

    def run(self, subtask: SubTask) -> WorkerResult:
        time.sleep(self._sleep)
        if self._fail:
            raise RuntimeError("simulated failure")
        return WorkerResult(
            subtask_id=subtask.id, role=self.role, status="ok",
            findings=[], duration_ms=int(self._sleep * 1000),
            token_usage={"input": 100, "output": 50},
            raw_text="done", iterations=3,
        )


# ─── LeaderAgent.decompose tests ─────────────────────────────────────────────

class TestLeaderDecompose:
    def setup_method(self):
        self.leader = StubLeader()

    def test_pure_python_pr(self):
        """Small Python-only PR -> security + style."""
        files = [{"filename": "app.py", "additions": 10, "deletions": 2}]
        subtasks = self.leader.decompose(_make_pr_info(files))
        roles = [s.role for s in subtasks]
        assert "security" in roles
        assert "style" in roles
        assert len(roles) == 2

    def test_large_mixed_pr(self):
        """Large mixed PR (>5 files, >200 lines) -> all 4 roles."""
        files = [
            {"filename": f"src/mod{i}.py", "additions": 50, "deletions": 5}
            for i in range(8)
        ]
        subtasks = self.leader.decompose(_make_pr_info(files))
        roles = sorted(s.role for s in subtasks)
        assert roles == ["architecture", "performance", "security", "style"]

    def test_docs_only_pr(self):
        """Docs-only PR -> style only."""
        files = [
            {"filename": "README.md", "additions": 5, "deletions": 0},
            {"filename": "docs/guide.rst", "additions": 20, "deletions": 3},
        ]
        subtasks = self.leader.decompose(_make_pr_info(files))
        assert len(subtasks) == 1
        assert subtasks[0].role == "style"

    def test_config_only_pr(self):
        """Config files (no code ext) -> style fallback."""
        files = [{"filename": ".env.example", "additions": 3, "deletions": 0}]
        subtasks = self.leader.decompose(_make_pr_info(files))
        assert len(subtasks) == 1
        assert subtasks[0].role == "style"

    def test_frontend_backend_mix(self):
        """Mix of .ts and .py files -> at least security + style."""
        files = [
            {"filename": "api/handler.py", "additions": 30, "deletions": 5},
            {"filename": "web/App.tsx", "additions": 40, "deletions": 10},
        ]
        subtasks = self.leader.decompose(_make_pr_info(files))
        roles = [s.role for s in subtasks]
        assert "security" in roles
        assert "style" in roles

    def test_priority_ordering(self):
        """Security subtask has highest priority."""
        files = [{"filename": f"f{i}.py", "additions": 50, "deletions": 0}
                 for i in range(10)]
        subtasks = self.leader.decompose(_make_pr_info(files))
        sec = next(s for s in subtasks if s.role == "security")
        for s in subtasks:
            assert sec.priority >= s.priority


# ─── TaskScheduler tests ─────────────────────────────────────────────────────

class TestTaskScheduler:
    def test_concurrency_limit(self):
        """6 workers @ 1s each with sem=3 should take ~2s."""
        sched = TaskScheduler(max_concurrency=3, per_task_timeout_s=5.0)
        assigns = {
            f"r{i}": (
                FakeWorker(f"r{i}", sleep_s=1.0),
                SubTask(id=f"s{i}", role="security", files=[], context={}),
            )
            for i in range(6)
        }
        t0 = time.time()
        results = sched.run_all_sync(assigns)
        elapsed = time.time() - t0
        assert 1.8 <= elapsed <= 2.8
        assert all(r.status == "ok" for r in results)

    def test_timeout_returns_promptly(self):
        """Worker exceeding timeout returns status=timeout quickly."""
        sched = TaskScheduler(max_concurrency=1, per_task_timeout_s=0.5)
        assigns = {
            "slow": (
                FakeWorker("security", sleep_s=5.0),
                SubTask(id="s1", role="security", files=[], context={}),
            )
        }
        t0 = time.time()
        results = sched.run_all_sync(assigns)
        elapsed = time.time() - t0
        assert results[0].status == "timeout"
        assert elapsed < 1.5

    def test_failure_isolation(self):
        """One failing worker does not block others."""
        sched = TaskScheduler(max_concurrency=3, per_task_timeout_s=5.0)
        assigns = {
            "boom": (
                FakeWorker("security", fail=True),
                SubTask(id="b", role="security", files=[], context={}),
            ),
            "ok": (
                FakeWorker("style", sleep_s=0.1),
                SubTask(id="o", role="style", files=[], context={}),
            ),
        }
        results = sched.run_all_sync(assigns)
        statuses = sorted(r.status for r in results)
        assert statuses == ["failed", "ok"]


# ─── ReviewerAgent.validate tests ─────────────────────────────────────────────

class TestReviewerValidate:
    def setup_method(self):
        self.reviewer = ReviewerAgent()

    def test_all_ok_accepted(self):
        """All healthy workers are accepted."""
        r1 = WorkerResult(subtask_id="s1", role="security", status="ok",
                          findings=[Finding(file="a.py", line=1, severity="high",
                                            category="x", description="y")],
                          raw_text="ok", iterations=3,
                          token_usage={"output": 300})
        r2 = WorkerResult(subtask_id="s2", role="style", status="ok",
                          findings=[], raw_text="ok", iterations=2,
                          token_usage={"output": 200})
        subtasks = [
            SubTask(id="s1", role="security", files=[], context={}),
            SubTask(id="s2", role="style", files=[], context={}),
        ]
        v = self.reviewer.validate([r1, r2], subtasks)
        assert len(v.accepted) == 2
        assert len(v.rejected) == 0
        assert len(v.retry) == 0

    def test_failed_worker_goes_to_retry(self):
        """Failed/timeout workers are rejected and added to retry."""
        r_fail = WorkerResult(subtask_id="s1", role="security",
                              status="timeout", error="exceeded 60s")
        subtasks = [SubTask(id="s1", role="security", files=[], context={})]
        v = self.reviewer.validate([r_fail], subtasks)
        assert len(v.rejected) == 1
        assert len(v.retry) == 1
        assert v.retry[0].id == "s1"

    def test_conflict_detection(self):
        """Same file+line with critical vs low severity is a conflict."""
        r1 = WorkerResult(
            subtask_id="s1", role="security", status="ok",
            findings=[Finding(file="x.py", line=10, severity="critical",
                              category="sql", description="injection")],
            raw_text="ok", iterations=3, token_usage={"output": 300},
        )
        r2 = WorkerResult(
            subtask_id="s2", role="style", status="ok",
            findings=[Finding(file="x.py", line=10, severity="low",
                              category="naming", description="bad name")],
            raw_text="ok", iterations=2, token_usage={"output": 200},
        )
        subtasks = [
            SubTask(id="s1", role="security", files=[], context={}),
            SubTask(id="s2", role="style", files=[], context={}),
        ]
        v = self.reviewer.validate([r1, r2], subtasks)
        assert len(v.conflicts) == 1
        assert v.conflicts[0]["file"] == "x.py"
        assert v.conflicts[0]["line"] == 10

    def test_no_conflict_same_severity(self):
        """Same file+line with same blocking severity is reinforcement, not conflict."""
        r1 = WorkerResult(
            subtask_id="s1", role="security", status="ok",
            findings=[Finding(file="x.py", line=5, severity="critical",
                              category="a", description="d1")],
            raw_text="ok", iterations=3, token_usage={"output": 300},
        )
        r2 = WorkerResult(
            subtask_id="s2", role="architecture", status="ok",
            findings=[Finding(file="x.py", line=5, severity="high",
                              category="b", description="d2")],
            raw_text="ok", iterations=3, token_usage={"output": 300},
        )
        subtasks = [
            SubTask(id="s1", role="security", files=[], context={}),
            SubTask(id="s2", role="architecture", files=[], context={}),
        ]
        v = self.reviewer.validate([r1, r2], subtasks)
        # Both are blocking severities — no conflict
        assert len(v.conflicts) == 0


# ─── SkillRegistry tests ─────────────────────────────────────────────────────

class TestSkillRegistry:
    def setup_method(self):
        self.registry = SkillRegistry()

    def test_discover_skills_finds_all(self):
        """Should find at least 5 skills in the project."""
        skills = self.registry.discover_skills()
        assert len(skills) >= 5
        names = [s.name for s in skills]
        assert "code-review" in names
        assert "code-review-analyze" in names

    def test_skills_have_tags(self):
        """All discovered skills should have non-empty tags."""
        skills = self.registry.discover_skills()
        for s in skills:
            assert len(s.tags) > 0, f"{s.name} has no tags"

    def test_discover_mcp_tools(self):
        """Should find 20 MCP tools across 3 servers."""
        tools = self.registry.discover_mcp_tools()
        assert len(tools) == 20
        servers = set(t.server for t in tools)
        assert servers == {"git", "memory", "code_analysis"}

    def test_match_skills_security(self):
        """Security role matches analyze skill (tagged 'security')."""
        matched = self.registry.match_skills_for_role("security")
        assert "code-review-analyze" in matched

    def test_match_skills_style(self):
        """Style role matches analyze skill (tagged 'style')."""
        matched = self.registry.match_skills_for_role("style")
        assert "code-review-analyze" in matched

    def test_match_tools_security(self):
        """Security role gets detect_security_issues tool."""
        matched = self.registry.match_tools_for_role("security")
        assert "detect_security_issues" in matched
        assert "get_pull_request_files" in matched

    def test_match_tools_performance(self):
        """Performance role gets analyze_complexity tool."""
        matched = self.registry.match_tools_for_role("performance")
        assert "analyze_complexity" in matched

    def test_cache_invalidation(self):
        """Calling discover with refresh=True re-scans."""
        skills1 = self.registry.discover_skills()
        skills2 = self.registry.discover_skills(refresh=True)
        assert len(skills1) == len(skills2)
