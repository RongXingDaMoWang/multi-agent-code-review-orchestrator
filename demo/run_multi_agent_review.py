"""
Multi-Agent Code Review Demo.

Demonstrates the Leader-Worker-Reviewer orchestration pipeline in dry-run
mode using mock PR data. No real GitHub API calls or LLM invocations needed.

Usage:
    python demo/run_multi_agent_review.py --dry-run
    python demo/run_multi_agent_review.py --pr owner/repo#42  # real mode (needs keys)
"""
import argparse
import json
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# ─── Mock data for dry-run ────────────────────────────────────────────────────

MOCK_PR_META = {
    "number": 42,
    "title": "Add user authentication middleware",
    "body": "Implements JWT-based auth with refresh tokens",
    "state": "open",
    "draft": False,
    "author": "dev-alice",
    "base_branch": "main",
    "head_branch": "feature/auth",
    "head_sha": "abc123def456",
    "additions": 245,
    "deletions": 12,
    "changed_files": 6,
    "merged": False,
    "html_url": "https://github.com/demo/repo/pull/42",
}

MOCK_FILES = [
    {"filename": "src/auth/middleware.py", "status": "added",
     "additions": 120, "deletions": 0, "changes": 120,
     "patch": "@@ +1,120 @@\n+import jwt\n+from flask import request\n+"
              "SECRET_KEY = 'hardcoded-secret-key'\n+\n+def verify_token(token):\n+"
              "    return jwt.decode(token, SECRET_KEY, algorithms=['HS256'])\n"},
    {"filename": "src/auth/models.py", "status": "added",
     "additions": 45, "deletions": 0, "changes": 45,
     "patch": "@@ +1,45 @@\n+class User:\n+    def __init__(self, id, email):\n+"
              "        self.id = id\n+        self.email = email\n"},
    {"filename": "src/auth/routes.py", "status": "added",
     "additions": 60, "deletions": 0, "changes": 60,
     "patch": "@@ +1,60 @@\n+from flask import Flask\n+import sqlite3\n+\n+"
              "def login(username, password):\n+"
              "    query = f\"SELECT * FROM users WHERE name='{username}'\"\n+"
              "    conn = sqlite3.connect('db.sqlite')\n+"
              "    return conn.execute(query).fetchone()\n"},
    {"filename": "src/config.py", "status": "modified",
     "additions": 8, "deletions": 2, "changes": 10,
     "patch": "@@ -1,5 +1,11 @@\n AUTH_ENABLED = True\n+TOKEN_EXPIRY = 3600\n"},
    {"filename": "tests/test_auth.py", "status": "added",
     "additions": 30, "deletions": 0, "changes": 30,
     "patch": "@@ +1,30 @@\n+import pytest\n+def test_login():\n+    pass\n"},
    {"filename": "README.md", "status": "modified",
     "additions": 12, "deletions": 10, "changes": 22,
     "patch": "@@ -1,3 +1,5 @@\n # Demo Repo\n+## Auth\n+JWT-based auth\n"},
]


def run_dry_run_demo():
    """Execute the full multi-agent pipeline with mocked dependencies."""
    print("=" * 60)
    print("  Multi-Agent Code Review Demo (dry-run)")
    print("=" * 60)

    # Stub external deps
    _ensure_stubs()

    from code_review_agent.orchestration import (
        LeaderAgent, ReviewerAgent, TaskScheduler,
    )
    from code_review_agent.orchestration.metrics import OrchestrationMetrics
    from code_review_agent.orchestration.schemas import (
        Finding, SubTask, WorkerResult,
    )

    # ── Phase 1: Leader decomposes ────────────────────────────────────────
    print("\n[Phase 1] Leader: decomposing PR...")

    class _MockLeader(LeaderAgent):
        def __init__(self): pass

    leader = _MockLeader()
    pr_info = {
        "owner": "demo", "repo": "repo", "pull_number": 42,
        "meta": MOCK_PR_META, "files": MOCK_FILES,
    }
    subtasks = leader.decompose(pr_info)
    print(f"  Subtasks generated: {len(subtasks)}")
    for st in subtasks:
        print(f"    - {st.role:15s} priority={st.priority} files={len(st.files)}")

    # ── Phase 2: Simulate Worker execution ────────────────────────────────
    print("\n[Phase 2] Workers: executing in parallel (simulated)...")
    metrics = OrchestrationMetrics(task_id="demo-001")
    results: list[WorkerResult] = []

    mock_findings = {
        "security": [
            Finding(file="src/auth/middleware.py", line=3, severity="critical",
                    category="hardcoded_secret",
                    description="SECRET_KEY is hardcoded — use env variable"),
            Finding(file="src/auth/routes.py", line=5, severity="critical",
                    category="sql_injection",
                    description="f-string SQL query is vulnerable to injection"),
        ],
        "style": [
            Finding(file="src/auth/models.py", line=1, severity="low",
                    category="missing_docstring",
                    description="Class User lacks a docstring"),
        ],
        "performance": [
            Finding(file="src/auth/routes.py", line=6, severity="medium",
                    category="connection_leak",
                    description="sqlite3 connection opened but never closed"),
        ],
        "architecture": [
            Finding(file="src/auth/middleware.py", line=1, severity="medium",
                    category="tight_coupling",
                    description="Auth middleware imports SECRET_KEY directly — "
                                "inject via config"),
        ],
    }

    for st in subtasks:
        time.sleep(0.3)  # simulate work
        findings = mock_findings.get(st.role, [])
        r = WorkerResult(
            subtask_id=st.id, role=st.role, status="ok",
            findings=findings, confidence="high",
            token_usage={"input": 800, "output": 400},
            duration_ms=1200, iterations=4, session_id=f"w-{st.role[:3]}",
            raw_text=f"[{st.role}] found {len(findings)} issues",
        )
        results.append(r)
        metrics.record_dispatch(st.role)
        metrics.record_worker(r)
        print(f"    [{st.role:15s}] done — {len(findings)} findings, "
              f"confidence={r.confidence}")

    # ── Phase 3: Reviewer validates ───────────────────────────────────────
    print("\n[Phase 3] Reviewer: validating results...")
    reviewer = ReviewerAgent()
    validated = reviewer.validate(results, subtasks)
    metrics.record_verdict(validated)
    print(f"  Accepted: {len(validated.accepted)}")
    print(f"  Rejected: {len(validated.rejected)}")
    print(f"  Retry:    {len(validated.retry)}")
    print(f"  Conflicts: {len(validated.conflicts)}")
    if validated.conflicts:
        for c in validated.conflicts:
            print(f"    conflict @ {c['file']}:{c['line']}")

    # ── Phase 4: Leader synthesizes ───────────────────────────────────────
    print("\n[Phase 4] Leader: synthesizing final review...")
    final = leader.synthesize(validated.accepted)
    metrics.finalize(final.severity_counts, final.decision)

    print(f"\n{'─' * 60}")
    print(f"  FINAL DECISION: {final.decision}")
    print(f"  Total findings: {len(final.findings)}")
    print(f"  Severity: {final.severity_counts}")
    print(f"{'─' * 60}")
    print("\n  Findings:")
    for f in final.findings:
        print(f"    [{f.severity:8s}] {f.file}:{f.line} — {f.description[:60]}")
    print("\n  Recommendations:")
    for r in final.recommendations:
        print(f"    - {r}")

    # ── Metrics summary ───────────────────────────────────────────────────
    print(f"\n{metrics.print_summary()}")
    print("\nDemo complete.")


def _ensure_stubs():
    """Install module stubs if real deps are not available."""
    try:
        import openai  # noqa: F401
    except ImportError:
        m = types.ModuleType("openai")
        m.OpenAI = MagicMock
        sys.modules["openai"] = m
    try:
        import github  # noqa: F401
    except ImportError:
        m = types.ModuleType("github")
        m.Github = MagicMock
        m.GithubException = Exception
        sys.modules["github"] = m


def main():
    parser = argparse.ArgumentParser(description="Multi-Agent Review Demo")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Use mock data (default)")
    parser.add_argument("--pr", default="", help="Real PR to review (needs API keys)")
    args = parser.parse_args()

    if args.pr and not args.dry_run:
        print("Real-mode review requires GITHUB_TOKEN and ANTHROPIC_API_KEY.")
        print("Use: python cli.py review --mode multi " + args.pr)
        return 1

    run_dry_run_demo()
    return 0


if __name__ == "__main__":
    sys.exit(main())
