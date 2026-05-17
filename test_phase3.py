"""Phase 3 sanity tests: SkillRegistry, Metrics, Exporter, Logger."""
import sys
import types
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Stubs for deps not installed
openai_mod = types.ModuleType("openai")
openai_mod.OpenAI = lambda *a, **k: None
sys.modules["openai"] = openai_mod
gh_mod = types.ModuleType("github")
gh_mod.Github = lambda *a, **k: None
gh_mod.GithubException = Exception
sys.modules["github"] = gh_mod

PASS = 0
FAIL = 0


def ok(name):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}")


def fail(name, err):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name}")
    print(f"         {err}")


print("=" * 60)
print("  Phase 3 Sanity Tests")
print("=" * 60)

# ─── 1. SkillRegistry: discover_skills ────────────────────────────────────
print("\n[1] SkillRegistry.discover_skills()")
try:
    from code_review_agent.orchestration.skill_registry import SkillRegistry
    reg = SkillRegistry()
    skills = reg.discover_skills()
    print(f"   Found {len(skills)} skills:")
    for s in skills:
        print(f"     {s.name:30s} tags={s.tags}")
    assert len(skills) >= 5, f"Expected >=5 skills, got {len(skills)}"
    analyze = next((s for s in skills if s.name == "code-review-analyze"), None)
    assert analyze is not None
    assert "security" in analyze.tags, f"Expected security in tags, got {analyze.tags}"
    ok("discover_skills")
except Exception as e:
    fail("discover_skills", str(e))

# ─── 2. SkillRegistry: discover_mcp_tools ─────────────────────────────────
print("\n[2] SkillRegistry.discover_mcp_tools()")
try:
    tools = reg.discover_mcp_tools()
    print(f"   Found {len(tools)} MCP tools:")
    for t in tools:
        print(f"     {t.server:15s} {t.name}")
    assert len(tools) >= 20, f"Expected >=20 tools, got {len(tools)}"
    ca_tools = [t for t in tools if t.server == "code_analysis"]
    assert len(ca_tools) == 3, f"Expected 3 code_analysis tools, got {len(ca_tools)}"
    ok("discover_mcp_tools")
except Exception as e:
    fail("discover_mcp_tools", str(e))

# ─── 3. SkillRegistry: match_skills_for_role ──────────────────────────────
print("\n[3] SkillRegistry.match_skills_for_role()")
try:
    for role in ("security", "performance", "architecture", "style"):
        matched = reg.match_skills_for_role(role)
        print(f"   {role:15s} -> {matched}")
        assert len(matched) >= 1, f"No skills matched for {role}"
    ok("match_skills_for_role")
except Exception as e:
    fail("match_skills_for_role", str(e))

# ─── 4. SkillRegistry: match_tools_for_role ───────────────────────────────
print("\n[4] SkillRegistry.match_tools_for_role()")
try:
    for role in ("security", "performance", "architecture", "style"):
        matched = reg.match_tools_for_role(role)
        print(f"   {role:15s} -> {matched}")
        assert len(matched) >= 2, f"Too few tools for {role}"
    ok("match_tools_for_role")
except Exception as e:
    fail("match_tools_for_role", str(e))

# ─── 5. OrchestrationMetrics ─────────────────────────────────────────────
print("\n[5] OrchestrationMetrics")
try:
    from code_review_agent.orchestration.metrics import OrchestrationMetrics
    from code_review_agent.orchestration.schemas import (
        WorkerResult, Finding, ValidatedResults,
    )
    m = OrchestrationMetrics(task_id="test-123")
    m.record_dispatch("security", 1)
    m.record_dispatch("style", 1)
    r1 = WorkerResult(
        subtask_id="s1", role="security", status="ok",
        findings=[Finding(file="a.py", line=1, severity="high",
                          category="sql", description="x")],
        duration_ms=1200, token_usage={"input": 500, "output": 300},
    )
    r2 = WorkerResult(
        subtask_id="s2", role="style", status="ok",
        findings=[], duration_ms=800, token_usage={"input": 200, "output": 100},
    )
    m.record_worker(r1)
    m.record_worker(r2)
    v = ValidatedResults(accepted=[r1, r2], rejected=[], retry=[], conflicts=[])
    m.record_verdict(v)
    m.finalize({"high": 1}, "REQUEST_CHANGES")
    summary = m.print_summary()
    print(summary[:400])
    assert "REQUEST_CHANGES" in summary
    assert m.retry_rate == 0.0
    assert m.total_tokens == {"input": 700, "output": 400}
    assert m.worker_durations["security"] == 1200.0
    ok("OrchestrationMetrics")
except Exception as e:
    fail("OrchestrationMetrics", str(e))

# ─── 6. export_orchestration_trace ────────────────────────────────────────
print("\n[6] export_orchestration_trace()")
try:
    from code_review_agent.trajectory.exporter import export_orchestration_trace
    tmpdir = Path(tempfile.mkdtemp())
    events = [
        {"type": "session_start", "session_id": "t1", "pr": "o/r#1",
         "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "orchestration_start", "task_id": "t1", "pr_url": "o/r#1",
         "mode": "multi", "timestamp": "2026-01-01T00:00:01Z"},
        {"type": "subtask_dispatched", "task_id": "t1", "subtask_id": "s1",
         "role": "security", "worker_id": "w1",
         "timestamp": "2026-01-01T00:00:02Z"},
        {"type": "worker_completed", "task_id": "t1", "subtask_id": "s1",
         "role": "security", "duration_ms": 1000,
         "token_usage": {"input": 100, "output": 50},
         "findings_count": 2, "status": "ok",
         "timestamp": "2026-01-01T00:00:03Z"},
        {"type": "reviewer_verdict", "task_id": "t1", "accepted_count": 1,
         "rejected_count": 0, "retry_count": 0,
         "timestamp": "2026-01-01T00:00:04Z"},
        {"type": "orchestration_end", "task_id": "t1",
         "total_duration_ms": 2000,
         "total_tokens": {"input": 100, "output": 50},
         "final_severity_counts": {"high": 2},
         "decision": "REQUEST_CHANGES",
         "timestamp": "2026-01-01T00:00:05Z"},
        {"type": "session_end", "session_id": "t1", "stats": {},
         "timestamp": "2026-01-01T00:00:06Z"},
    ]
    with open(tmpdir / "orch_t1.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    result = export_orchestration_trace("t1", trajectories_dir=tmpdir)
    assert result["session_id"] == "t1"
    assert result["start"]["mode"] == "multi"
    assert result["end"]["decision"] == "REQUEST_CHANGES"
    assert len(result["subtasks"]) == 1
    assert result["subtasks"][0]["role"] == "security"
    assert result["subtasks"][0]["completed"]["findings_count"] == 2
    assert len(result["verdicts"]) == 1
    print(f"   source_file: {result['source_file']}")
    print(f"   subtasks: {len(result['subtasks'])}, verdicts: {len(result['verdicts'])}")
    print(f"   raw_events: {len(result['raw_events'])}")
    ok("export_orchestration_trace")
except Exception as e:
    fail("export_orchestration_trace", str(e))

# ─── 7. TrajectoryLogger prefix + orchestration methods ───────────────────
print("\n[7] TrajectoryLogger orchestration methods")
try:
    from code_review_agent.trajectory.logger import TrajectoryLogger
    tmpdir2 = Path(tempfile.mkdtemp())
    logger = TrajectoryLogger(
        output_dir=tmpdir2, session_id="abc", pr="o/r#1", prefix="orch"
    )
    assert "orch_abc.jsonl" in str(logger.output_path)
    logger.log_orchestration_start(task_id="abc", pr_url="o/r#1")
    logger.log_subtask_dispatched(
        task_id="abc", subtask_id="s1", role="security", worker_id="w1"
    )
    logger.log_worker_completed(
        task_id="abc", subtask_id="s1", role="security",
        duration_ms=500, token_usage={"input": 10, "output": 5},
        findings_count=1, status="ok",
    )
    logger.log_reviewer_verdict(
        task_id="abc", accepted_count=1, rejected_count=0, retry_count=0
    )
    logger.log_orchestration_end(
        task_id="abc", total_duration_ms=600,
        total_tokens={"input": 10, "output": 5},
        final_severity_counts={"high": 1}, decision="COMMENT",
    )
    logger.close()
    lines = [json.loads(l) for l in open(logger.output_path, encoding="utf-8")
             if l.strip()]
    types_found = [l["type"] for l in lines]
    assert "orchestration_start" in types_found
    assert "subtask_dispatched" in types_found
    assert "worker_completed" in types_found
    assert "reviewer_verdict" in types_found
    assert "orchestration_end" in types_found
    assert "session_start" in types_found
    assert "session_end" in types_found
    print(f"   Wrote {len(lines)} records to {logger.output_path.name}")
    print(f"   Types: {types_found}")
    ok("TrajectoryLogger orchestration methods")
except Exception as e:
    fail("TrajectoryLogger orchestration methods", str(e))

# ─── Summary ──────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"  Results: {PASS} passed, {FAIL} failed")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
